"""OpenAI-compatible transport used by every Astra provider.

All shipped providers expose ``/chat/completions`` and ``/models`` in the
OpenAI wire format, so one small streaming client covers OpenRouter, Groq,
Gemini (compat endpoint), SambaNova, Z.ai, Cloudflare, Mistral, Ollama Cloud
and local Ollama / LM Studio.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from .catalog import Provider

DeltaCallback = Callable[[str], None] | None

_TOOL_MARKERS = (
    "<｜DSML｜",
    "<|DSML|",
    "<|tool_call",
    "<tool_call",
    "<function_call",
)


def strip_tool_markup(text: str) -> str:
    """Some OpenAI-compatible gateways leak their native tool-call markup."""
    cut = len(text)
    for marker in _TOOL_MARKERS:
        index = text.find(marker)
        if index != -1:
            cut = min(cut, index)
    return text[:cut].rstrip()


class _ToolMarkupFilter:
    """Hold a small tail so a marker split across chunks never reaches the UI."""

    HOLD = 48

    def __init__(self) -> None:
        self.pending = ""
        self.suppressed = False

    def feed(self, text: str) -> str:
        if self.suppressed:
            return ""
        self.pending += text
        cut = len(self.pending)
        for marker in _TOOL_MARKERS:
            index = self.pending.find(marker)
            if index != -1:
                cut = min(cut, index)
        if cut < len(self.pending):
            out = self.pending[:cut]
            self.pending = ""
            self.suppressed = True
            return out
        if len(self.pending) <= self.HOLD:
            return ""
        out = self.pending[:-self.HOLD]
        self.pending = self.pending[-self.HOLD:]
        return out

    def flush(self) -> str:
        if self.suppressed:
            self.pending = ""
            return ""
        out = self.pending
        self.pending = ""
        return out


class AstraError(RuntimeError):
    """Base class for user-facing errors."""


class SetupError(AstraError):
    """Missing key / account id / base URL."""


class ProviderError(AstraError):
    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status: int = 0,
        retryable: bool = False,
        retry_after: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after

    @property
    def short(self) -> str:
        text = str(self)
        return text if len(text) <= 160 else text[:157] + "…"


class AuthError(ProviderError):
    pass


class RateLimitError(ProviderError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def as_message_part(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments or "{}"},
        }


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""
    finish_reason: str = ""
    model: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency: float = 0.0
    fallbacks: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def estimate_tokens(value: Any) -> int:
    """Cheap token estimate for mixed English/Russian text."""
    if isinstance(value, str):
        return max(1, len(value) // 3)
    if isinstance(value, dict):
        return sum(estimate_tokens(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(estimate_tokens(v) for v in value)
    return 0


def _normalise(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _tool_calls_from_message(message: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index, item in enumerate(message.get("tool_calls") or []):
        fn = item.get("function") or {}
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args or {}, ensure_ascii=False)
        calls.append(
            ToolCall(
                id=str(item.get("id") or f"call_{index}"),
                name=str(fn.get("name") or ""),
                arguments=args,
            )
        )
    return calls


class OpenAICompatClient:
    def __init__(
        self,
        provider: Provider,
        base_url: str,
        api_key: str = "",
        *,
        timeout: float = 300.0,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # ------------------------------------------------------------------ utils
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "AstraCode/0.1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for key, value in self.provider.headers:
            headers[key] = value
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _error_text(response: httpx.Response, body: bytes) -> tuple[str, float]:
        retry_after = 0.0
        try:
            retry_after = float(response.headers.get("retry-after", "0") or 0)
        except ValueError:
            pass
        message = ""
        try:
            data = json.loads(body.decode("utf-8", "replace"))
            error = data.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("type") or "")
            elif isinstance(error, str):
                message = error
            if not message:
                message = str(data.get("message") or data.get("detail") or "")
        except (ValueError, AttributeError):
            message = body.decode("utf-8", "replace")[:400]
        return (message or response.reason_phrase or "unknown error").strip(), retry_after

    def _raise_for_response(self, response: httpx.Response, body: bytes) -> None:
        if response.status_code < 400:
            return
        message, retry_after = self._error_text(response, body)
        text = f"{self.provider.short}: HTTP {response.status_code} — {message}"
        if response.status_code in (401, 403):
            hint = (
                f"проверьте ключ: astra auth set {self.provider.id}"
                if self.api_key
                else f"нужен ключ: astra auth set {self.provider.id}"
            )
            raise AuthError(f"{text}. {hint}", provider=self.provider.id, status=response.status_code)
        if response.status_code == 429:
            raise RateLimitError(
                f"{text}. Лимит free-tier исчерпан — Astra переключится на другую модель.",
                provider=self.provider.id,
                status=429,
                retryable=True,
                retry_after=retry_after,
            )
        raise ProviderError(
            text,
            provider=self.provider.id,
            status=response.status_code,
            retryable=response.status_code >= 500 or response.status_code in (408, 409),
            retry_after=retry_after,
        )

    # ----------------------------------------------------------------- models
    async def list_models(self) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
                response = await client.get(self._url("models"), headers=self._headers())
                if response.status_code >= 400:
                    self._raise_for_response(response, await response.aread())
                data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"{self.provider.short}: сеть недоступна ({exc.__class__.__name__})",
                provider=self.provider.id,
                retryable=True,
            ) from exc
        rows = data.get("data") if isinstance(data, dict) else data
        models: list[dict[str, Any]] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                model_id = row.get("id") or row.get("name")
                if not model_id:
                    continue
                pricing = row.get("pricing") or {}
                is_free = False
                if isinstance(pricing, dict) and pricing:
                    prompt_price = pricing.get("prompt")
                    completion_price = pricing.get("completion")
                    try:
                        is_free = float(prompt_price) == 0 and float(completion_price) == 0
                    except (TypeError, ValueError):
                        is_free = False
                models.append(
                    {
                        "id": str(model_id),
                        "name": str(row.get("name") or model_id),
                        "ctx": int(row.get("context_length") or row.get("context_window") or 0),
                        "free": is_free,
                        "owned_by": str(row.get("owned_by") or ""),
                        "type": str(row.get("type") or ""),
                        "capabilities": row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {},
                        "max_output_tokens": int(row.get("max_output_tokens") or 0),
                    }
                )
        return models

    # ------------------------------------------------------------------- chat
    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = 0.2,
        max_tokens: int = 8192,
        stream: bool = True,
        on_delta: DeltaCallback = None,
        on_reasoning: DeltaCallback = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": bool(stream),
        }
        if temperature is not None:
            payload["temperature"] = max(0.0, min(2.0, float(temperature)))
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        started = asyncio.get_event_loop().time()
        last_error: ProviderError | None = None
        for attempt in range(2):
            try:
                result = await self._chat_once(
                    payload,
                    stream=stream,
                    on_delta=on_delta,
                    on_reasoning=on_reasoning,
                )
                result.model = model
                result.provider = self.provider.id
                result.latency = asyncio.get_event_loop().time() - started
                return result
            except RateLimitError:
                raise
            except AuthError:
                raise
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt >= 1:
                    raise
                await asyncio.sleep(1.2 + attempt * 1.5)
            except httpx.HTTPError as exc:
                last_error = ProviderError(
                    f"{self.provider.short}: сетевая ошибка ({exc.__class__.__name__}: {exc})",
                    provider=self.provider.id,
                    retryable=True,
                )
                if attempt >= 1:
                    raise last_error from exc
                await asyncio.sleep(1.2)
        raise last_error or ProviderError("неизвестная ошибка провайдера")

    async def _chat_once(
        self,
        payload: dict[str, Any],
        *,
        stream: bool,
        on_delta: DeltaCallback,
        on_reasoning: DeltaCallback,
    ) -> ChatResult:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=20.0),
            follow_redirects=True,
        ) as client:
            async with client.stream(
                "POST",
                self._url("chat/completions"),
                headers=self._headers(),
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    self._raise_for_response(response, body)
                content_type = response.headers.get("content-type", "")
                if not stream or "application/json" in content_type:
                    raw = await response.aread()
                    try:
                        data = json.loads(raw.decode("utf-8", "replace"))
                    except ValueError as exc:
                        raise ProviderError(
                            f"{self.provider.short}: некорректный JSON ответа",
                            provider=self.provider.id,
                            retryable=True,
                        ) from exc
                    return self._parse_full(data)

                result = ChatResult()
                tool_buffer: dict[int, dict[str, Any]] = {}
                markup = _ToolMarkupFilter() if self.provider.id == "omniroute" else None
                saw_data = False
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith(":"):
                        continue
                    data_text = ""
                    if line.startswith("data:"):
                        data_text = line[5:].strip()
                    elif line.startswith("{"):
                        data_text = line.strip()
                    if not data_text:
                        continue
                    if data_text == "[DONE]":
                        break
                    saw_data = True
                    try:
                        event = json.loads(data_text)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("error"):
                        error = event["error"]
                        message = error.get("message") if isinstance(error, dict) else str(error)
                        raise ProviderError(
                            f"{self.provider.short}: {message or 'stream error'}",
                            provider=self.provider.id,
                            retryable=True,
                        )
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        result.prompt_tokens = int(usage.get("prompt_tokens") or 0)
                        result.completion_tokens = int(usage.get("completion_tokens") or 0)
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    delta = choice.get("delta") or choice.get("message") or {}
                    text = _normalise(delta.get("content"))
                    if text and markup is not None:
                        text = markup.feed(text)
                    if text:
                        result.content += text
                        if on_delta:
                            on_delta(text)
                    reasoning = _normalise(
                        delta.get("reasoning_content")
                        or delta.get("reasoning")
                        or delta.get("thinking")
                    )
                    if reasoning:
                        result.reasoning += reasoning
                        if on_reasoning:
                            on_reasoning(reasoning)
                    for item in delta.get("tool_calls") or []:
                        index = int(item.get("index") or 0)
                        slot = tool_buffer.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        if item.get("id"):
                            slot["id"] = str(item["id"])
                        fn = item.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = str(fn["name"])
                        arguments = fn.get("arguments")
                        if isinstance(arguments, str):
                            slot["arguments"] += arguments
                        elif isinstance(arguments, dict):
                            slot["arguments"] += json.dumps(arguments, ensure_ascii=False)
                    if choice.get("finish_reason"):
                        result.finish_reason = str(choice["finish_reason"])

                if markup is not None:
                    tail = markup.flush()
                    if tail:
                        result.content += tail
                        if on_delta:
                            on_delta(tail)

                if not saw_data and not result.content and not tool_buffer:
                    # A gateway that ignored stream=true returns nothing useful here;
                    # surface a clear error instead of silently returning an empty turn.
                    raise ProviderError(
                        f"{self.provider.short}: пустой stream-ответ",
                        provider=self.provider.id,
                        retryable=True,
                    )

                for index in sorted(tool_buffer):
                    slot = tool_buffer[index]
                    if slot.get("name"):
                        result.tool_calls.append(
                            ToolCall(
                                id=slot.get("id") or f"call_{index}",
                                name=slot["name"],
                                arguments=slot.get("arguments") or "{}",
                            )
                        )
                if not result.prompt_tokens:
                    result.prompt_tokens = estimate_tokens(payload.get("messages") or [])
                if not result.completion_tokens:
                    result.completion_tokens = estimate_tokens(result.content)
                return result

    def _parse_full(self, data: dict[str, Any]) -> ChatResult:
        choices = data.get("choices") or []
        message = (choices[0] or {}).get("message", {}) if choices else {}
        usage = data.get("usage") or {}
        content = _normalise(message.get("content"))
        if self.provider.id == "omniroute":
            content = strip_tool_markup(content)
        return ChatResult(
            content=content,
            tool_calls=_tool_calls_from_message(message),
            reasoning=_normalise(
                message.get("reasoning_content") or message.get("reasoning") or ""
            ),
            finish_reason=str((choices[0] or {}).get("finish_reason") or "") if choices else "",
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )


class MockClient:
    """Deterministic offline backend used by ``astra demo`` and the test suite."""

    provider = Provider(
        id="mock",
        name="Astra Demo",
        base_url="",
        env_key=None,
        signup_url="",
        docs_url="",
        free_note="офлайн-демо без ключей",
        kind="mock",
        local=True,
        keyless=True,
    )

    def __init__(self) -> None:
        self.calls = 0

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = 0.2,
        max_tokens: int = 8192,
        stream: bool = True,
        on_delta: DeltaCallback = None,
        on_reasoning: DeltaCallback = None,
    ) -> ChatResult:
        self.calls += 1
        assistant_turns = sum(1 for m in messages if m.get("role") == "assistant")
        has_tools = bool(tools)
        await asyncio.sleep(0.35)

        if tools is None:
            return ChatResult(
                content="Краткое содержание предыдущей беседы: пользователь просил демонстрацию Astra Code.",
                model=model,
                provider="mock",
            )

        if has_tools and assistant_turns == 0:
            await asyncio.sleep(0.25)
            content = (
                "Сначала соберу минимальный пример и проверю, что он запускается. "
                "Пишу файл и держу задачу в todo."
            )
            await self._emit(content, on_delta)
            calls = [
                ToolCall(
                    id="call_demo_todo",
                    name="todo_write",
                    arguments=json.dumps(
                        {
                            "todos": [
                                {"content": "Создать hello.py", "status": "in_progress"},
                                {"content": "Запустить python hello.py", "status": "pending"},
                            ]
                        },
                        ensure_ascii=False,
                    ),
                ),
                ToolCall(
                    id="call_demo_write",
                    name="write_file",
                    arguments=json.dumps(
                        {
                            "path": "hello.py",
                            "content": (
                                'def greet(name: str) -> str:\n'
                                '    return f"Привет, {name}!"\n\n\n'
                                'if __name__ == "__main__":\n'
                                '    print(greet("Astra"))\n'
                            ),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
            return ChatResult(
                content=content,
                tool_calls=calls,
                finish_reason="tool_calls",
                prompt_tokens=estimate_tokens(messages),
                completion_tokens=estimate_tokens(content),
            )

        if has_tools and assistant_turns == 1:
            await asyncio.sleep(0.2)
            content = "Файл готов. Запускаю его."
            await self._emit(content, on_delta)
            command = "py hello.py" if os.name == "nt" else "python3 hello.py"
            return ChatResult(
                content=content,
                tool_calls=[
                    ToolCall(
                        id="call_demo_run",
                        name="run_command",
                        arguments=json.dumps({"command": command}, ensure_ascii=False),
                    )
                ],
                finish_reason="tool_calls",
                prompt_tokens=estimate_tokens(messages),
                completion_tokens=estimate_tokens(content),
            )

        final = (
            "Готово. Создал `hello.py`, запустил его и получил вывод:\n\n"
            "```text\nПривет, Astra!\n```\n\n"
            "**Что сделано**\n"
            "1. `hello.py` — функция `greet()` и точка входа.\n"
            "2. Проверка запуском через `run_command`.\n\n"
            "Дальше можно попросить: *«добавь тест на greet и прогони pytest»*."
        )
        await self._emit(final, on_delta)
        return ChatResult(
            content=final,
            finish_reason="stop",
            model=model,
            provider="mock",
            prompt_tokens=estimate_tokens(messages),
            completion_tokens=estimate_tokens(final),
        )

    async def _emit(self, text: str, on_delta: DeltaCallback) -> None:
        if not on_delta:
            return
        chunk = ""
        for char in text:
            chunk += char
            if len(chunk) >= 4 or char in "\n ":
                on_delta(chunk)
                chunk = ""
                await asyncio.sleep(0.006)
        if chunk:
            on_delta(chunk)
