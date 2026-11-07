"""Free-only model router with composite Astra models and automatic fallback."""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from . import catalog
from .catalog import COMPOSITE_MODELS, CompositeModel, Provider
from .config import Config
from .providers import (
    ChatResult,
    MockClient,
    OpenAICompatClient,
    ProviderError,
    SetupError,
)

CACHE_TTL = 24 * 3600
Notify = Callable[[str], None] | None


@dataclass
class ModelTarget:
    provider: Provider
    model: str
    client: Any
    label: str
    ctx: int
    custom: CompositeModel | None = None
    temperature: float | None = None
    system_extra: str = ""
    free: bool = True

    @property
    def ref(self) -> str:
        return f"{self.provider.id}:{self.model}"

    @property
    def display(self) -> str:
        return f"{self.label} · {self.provider.short}"


@dataclass
class ProviderState:
    provider: Provider
    available: bool
    reason: str
    models: int = 0
    free_models: int = 0
    cached_at: float = 0.0

    @property
    def status(self) -> str:
        if self.available:
            prefix = "сервер доступен" if self.provider.local else "готово"
            if self.models:
                return f"{prefix} · {self.free_models} бесплатных моделей"
            return f"{prefix} · модели не загружены"
        return self.reason


class Router:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._cache_path = cfg.home / "models_cache.json"
        self._cache: dict[str, dict[str, Any]] = {}
        self._local_up: dict[str, bool] = {}
        self._load_cache()

    # ------------------------------------------------------------------ cache
    def _load_cache(self) -> None:
        try:
            if self._cache_path.is_file():
                data = json.loads(self._cache_path.read_text(encoding="utf-8"))
                providers = data.get("providers")
                if isinstance(providers, dict):
                    self._cache = providers
        except (OSError, ValueError):
            self._cache = {}

    def _save_cache(self) -> None:
        try:
            self.cfg.home.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "providers": self._cache}
            tmp = self._cache_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self._cache_path)
        except OSError:
            pass

    def cached_models(self, provider_id: str) -> list[dict[str, Any]]:
        record = self._cache.get(provider_id) or {}
        # Stale cache is still useful: refresh failures should not empty the UI.
        models = record.get("models")
        return models if isinstance(models, list) else []

    def cache_age(self, provider_id: str) -> float:
        record = self._cache.get(provider_id) or {}
        try:
            return max(0.0, time.time() - float(record.get("ts") or 0))
        except (TypeError, ValueError):
            return float("inf")

    # ----------------------------------------------------------------- states
    def _reachable(self, provider: Provider) -> bool:
        """Cheap TCP probe for local OpenAI-compatible servers."""
        if provider.id not in {"ollama", "lmstudio", "omniroute"}:
            return True
        if provider.id in self._local_up:
            return self._local_up[provider.id]
        base = catalog.resolve_base_url(provider, self.cfg)
        parsed = urlparse(base)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=0.35):
                ok = True
        except OSError:
            ok = False
        self._local_up[provider.id] = ok
        return ok

    def provider_available(self, provider: Provider) -> bool:
        if not catalog.provider_available(provider, self.cfg):
            return False
        return self._reachable(provider)

    def states(self) -> list[ProviderState]:
        out: list[ProviderState] = []
        ids = list(catalog.PROVIDER_ORDER)
        for provider_id in ids:
            provider = catalog.PROVIDERS[provider_id]
            configured = catalog.provider_available(provider, self.cfg)
            reachable = self.provider_available(provider)
            available = configured and reachable
            if available:
                reason = ""
            elif configured and not reachable:
                reason = f"сервер не запущен · {catalog.resolve_base_url(provider, self.cfg)}"
            else:
                reason = catalog.missing_setup(provider, self.cfg)
            models = self.all_models(provider_id)
            out.append(
                ProviderState(
                    provider=provider,
                    available=available,
                    reason=reason,
                    models=len(models),
                    free_models=sum(1 for m in models if m["free"]),
                    cached_at=self.cache_age(provider_id),
                )
            )
        return out

    def available_providers(self) -> list[Provider]:
        return [
            catalog.PROVIDERS[pid]
            for pid in catalog.PROVIDER_ORDER
            if self.provider_available(catalog.PROVIDERS[pid])
        ]

    # ----------------------------------------------------------------- models
    def all_models(self, provider_id: str) -> list[dict[str, Any]]:
        provider = catalog.PROVIDERS.get(provider_id)
        if not provider:
            return []
        merged: dict[str, dict[str, Any]] = {}
        for model in provider.models:
            free = catalog.is_free_model(provider_id, model.id)
            merged[model.id] = {
                "id": model.id,
                "label": model.label,
                "ctx": model.ctx,
                "free": free,
                "note": model.note,
                "source": "preset",
                "owner": "",
                "type": "text",
                "capabilities": {},
                "tool_calling": True,
            }
        for row in self.cached_models(provider_id):
            model_id = str(row.get("id") or "")
            if not model_id:
                continue
            free = bool(row.get("free")) or catalog.is_free_model(provider_id, model_id)
            existing = merged.get(model_id) or {}
            ctx = int(row.get("ctx") or 0) or existing.get("ctx") or 0
            capabilities = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
            model_type = str(row.get("type") or "")
            owner = str(row.get("owned_by") or "")
            note = existing.get("note", "")
            if not note:
                parts = []
                if owner:
                    parts.append(owner)
                if model_type in {"image", "video"}:
                    parts.append(model_type)
                elif capabilities and not capabilities.get("tool_calling"):
                    parts.append("без tools")
                note = " · ".join(parts)
            merged[model_id] = {
                "id": model_id,
                "label": existing.get("label") or str(row.get("name") or model_id),
                "ctx": ctx or catalog.context_window(provider_id, model_id),
                "free": free,
                "note": note,
                "source": "api",
                "owner": owner,
                "type": model_type,
                "capabilities": capabilities,
                "tool_calling": bool(capabilities.get("tool_calling", True)) if model_type not in {"image", "video"} else False,
            }
        if not self.cfg.get("allow_paid", False):
            merged = {k: v for k, v in merged.items() if v["free"]}
        return list(merged.values())

    def ranked_models(self, provider_id: str, purpose: str = "coder") -> list[dict[str, Any]]:
        models = self.all_models(provider_id)
        by_id = {m["id"]: m for m in models}
        if provider_id == "deepseek":
            order = {m.id: m.rank for m in catalog.PROVIDERS[provider_id].models}
            return sorted(models, key=lambda m: (order.get(m["id"], 99), m["id"]))
        if provider_id == "omniroute":
            # Verified working models first; then the long tail (Kiro text first).
            presets = list(catalog.PROVIDERS[provider_id].models)
            preset_ids = {preset.id for preset in presets}
            ordered: list[str] = []
            for preset in presets:
                if "проверено" in preset.note and preset.id in by_id:
                    ordered.append(preset.id)
            rest = [
                m for m in models
                if m["id"] not in ordered
                and m["id"] not in preset_ids
                and m.get("type") not in {"image", "video"}
            ]
            rest.sort(
                key=lambda m: (
                    0 if m.get("tool_calling") else 1,
                    0 if m.get("owner") == "kiro" else 1,
                    m.get("owner", ""),
                    m["id"],
                )
            )
            ordered.extend(m["id"] for m in rest)
            for preset in presets:
                if preset.id in by_id and preset.id not in ordered:
                    ordered.append(preset.id)
            ordered.extend(m["id"] for m in models if m["id"] not in ordered)
            return [by_id[mid] for mid in ordered if mid in by_id]
        ids = catalog.rank_candidates(
            provider_id,
            [m["id"] for m in models],
            purpose,
            presets=catalog.PROVIDERS[provider_id].models,
        )
        return [by_id[mid] for mid in ids if mid in by_id]

    def choose_model(self, provider_id: str, purpose: str = "coder") -> str:
        pinned = str(self.cfg.provider_cfg(provider_id).get("model") or "").strip()
        if pinned:
            free, why = catalog.free_status(provider_id, pinned)
            if not free and not self.cfg.get("allow_paid", False):
                raise SetupError(
                    f"{provider_id}: модель {pinned} — {why}. "
                    "Выберите бесплатную: astra models, либо включите allow_paid."
                )
            return pinned
        if provider_id == "omniroute":
            available = {m["id"] for m in self.all_models(provider_id)}
            preferred = (
                "kiro/qwen3-coder-next" if purpose == "coder" else "kiro/deepseek-3.2"
            )
            if preferred in available:
                return preferred
            for preset in catalog.PROVIDERS[provider_id].models:
                if "проверено" in preset.note and preset.id in available:
                    return preset.id
            if available:
                return sorted(available)[0]
        ranked = self.ranked_models(provider_id, purpose)
        if ranked:
            return str(ranked[0]["id"])
        raise SetupError(
            f"{provider_id}: нет списка моделей. Выполните: astra models --refresh"
        )

    async def refresh(self, provider_id: str) -> int:
        provider = catalog.PROVIDERS.get(provider_id)
        if not provider:
            raise SetupError(f"неизвестный провайдер: {provider_id}")
        if not self.provider_available(provider):
            raise SetupError(catalog.missing_setup(provider, self.cfg))
        base_url = catalog.resolve_base_url(provider, self.cfg)
        if not base_url:
            raise SetupError("не задан base_url")
        client = OpenAICompatClient(
            provider,
            base_url,
            self.cfg.api_key(provider.id, provider.env_key),
        )
        rows = await client.list_models()
        models: list[dict[str, Any]] = []
        for row in rows:
            model_id = str(row.get("id") or "")
            if not model_id:
                continue
            free = bool(row.get("free")) or catalog.is_free_model(provider_id, model_id)
            capabilities = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
            models.append(
                {
                    "id": model_id,
                    "name": str(row.get("name") or model_id),
                    "ctx": int(row.get("ctx") or 0),
                    "free": free,
                    "owned_by": str(row.get("owned_by") or ""),
                    "type": str(row.get("type") or ""),
                    "capabilities": capabilities,
                    "max_output_tokens": int(row.get("max_output_tokens") or 0),
                }
            )
        self._cache[provider_id] = {"ts": time.time(), "models": models}
        self._save_cache()
        return len(models)

    async def refresh_all(self, only: list[str] | None = None) -> dict[str, tuple[int | None, str]]:
        report: dict[str, tuple[int | None, str]] = {}
        for pid in only or catalog.PROVIDER_ORDER:
            provider = catalog.PROVIDERS[pid]
            if not self.provider_available(provider):
                report[pid] = (None, catalog.missing_setup(provider, self.cfg))
                continue
            try:
                count = await self.refresh(pid)
                report[pid] = (count, "ok")
            except (ProviderError, SetupError) as exc:
                report[pid] = (None, str(exc)[:220])
            except Exception as exc:  # noqa: BLE001 - report, don't crash the CLI
                report[pid] = (None, f"{exc.__class__.__name__}: {exc}"[:220])
        return report

    # ----------------------------------------------------------------- resolve
    def resolve(self, ref: str | None = None) -> list[ModelTarget]:
        raw = (ref or self.cfg.model or "astra-coder").strip()
        if raw in COMPOSITE_MODELS:
            return self._resolve_composite(COMPOSITE_MODELS[raw])
        if raw == "mock":
            return [self._mock_target()]
        if raw in catalog.PROVIDERS:
            provider_id, model_id = raw, ""
        else:
            provider_id, model_id = catalog.split_model_ref(raw, self.cfg.provider)
        if provider_id == "mock":
            return [self._mock_target()]
        provider = catalog.PROVIDERS.get(provider_id)
        if not provider:
            raise SetupError(
                f"неизвестный провайдер «{provider_id}». Доступны: "
                + ", ".join(catalog.PROVIDER_ORDER)
            )
        if not catalog.provider_available(provider, self.cfg):
            raise SetupError(
                f"{provider.short}: {catalog.missing_setup(provider, self.cfg)}\n"
                f"Быстрый старт: astra auth set {provider.id}  ·  демо без ключей: astra demo"
            )
        if not self.provider_available(provider):
            raise SetupError(
                f"{provider.short}: сервер не отвечает на {provider.base_url}. "
                f"Запустите его или выберите другого провайдера."
            )
        model = model_id.strip()
        if not model:
            model = self.choose_model(provider_id, "coder")
        free, why = catalog.free_status(provider_id, model)
        if not free and not self.cfg.get("allow_paid", False):
            raise SetupError(
                f"{model} у {provider.short}: {why}. "
                "Astra работает только с бесплатными моделями "
                "(allow_paid = true отключает защиту)."
            )
        return [self._make_target(provider, model)]

    def _resolve_composite(self, model: CompositeModel) -> list[ModelTarget]:
        override = self.cfg.custom_cfg(model.id).get("chain")
        chain = list(override) if isinstance(override, list) and override else list(model.chain)
        targets: list[ModelTarget] = []
        skipped: list[str] = []
        for entry in chain:
            entry_text = str(entry)
            if entry_text in catalog.PROVIDERS:
                provider_id, model_id = entry_text, ""
            else:
                provider_id, model_id = catalog.split_model_ref(entry_text, self.cfg.provider)
            provider = catalog.PROVIDERS.get(provider_id)
            if not provider:
                continue
            if not catalog.provider_available(provider, self.cfg):
                skipped.append(provider_id)
                continue
            if not self.provider_available(provider):
                skipped.append(provider_id)
                continue
            try:
                chosen = model_id.strip() or self.choose_model(provider_id, model.purpose)
            except SetupError:
                skipped.append(provider_id)
                continue
            free, why = catalog.free_status(provider_id, chosen)
            if not free and not self.cfg.get("allow_paid", False):
                skipped.append(provider_id)
                continue
            try:
                target = self._make_target(provider, chosen)
            except SetupError:
                skipped.append(provider_id)
                continue
            target.custom = model
            target.temperature = model.temperature
            target.system_extra = model.system
            targets.append(target)
        if not targets:
            wanted: list[str] = []
            for entry in chain:
                pid = str(entry).split(":", 1)[0]
                if pid in catalog.PROVIDERS and pid not in wanted:
                    wanted.append(pid)
            hints = []
            for pid in wanted[:4]:
                suffix = " --from-omniroute" if pid == "omniroute" else ""
                hints.append(f"  astra auth set {pid}{suffix}")
            raise SetupError(
                f"{model.name}: нет ни одного доступного провайдера.\n"
                "Добавьте ключ:\n"
                + ("\n".join(hints) if hints else "  astra auth set openrouter")
                + "\nИли посмотрите демо без ключей: astra demo"
            )
        return targets[:6]

    def _make_target(self, provider: Provider, model: str) -> ModelTarget:
        base_url = catalog.resolve_base_url(provider, self.cfg)
        if not base_url:
            raise SetupError(catalog.missing_setup(provider, self.cfg))
        if provider.requires_account_id and "{account_id}" in base_url:
            raise SetupError(catalog.missing_setup(provider, self.cfg))
        client = OpenAICompatClient(
            provider,
            base_url,
            self.cfg.api_key(provider.id, provider.env_key),
        )
        ctx = catalog.context_window(provider.id, model)
        for row in self.cached_models(provider.id):
            if str(row.get("id") or "") == model and int(row.get("ctx") or 0):
                ctx = int(row["ctx"])
                break
        return ModelTarget(
            provider=provider,
            model=model,
            client=client,
            label=catalog.model_label(provider.id, model),
            ctx=ctx,
            temperature=float(self.cfg.get("temperature", 0.2) or 0.2),
        )

    def _mock_target(self) -> ModelTarget:
        provider = MockClient.provider
        return ModelTarget(
            provider=provider,
            model="astra-demo",
            client=MockClient(),
            label="Astra Demo",
            ctx=32_768,
            temperature=0.2,
        )

    # -------------------------------------------------------------------- chat
    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = True,
        on_delta: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        on_notice: Notify = None,
        allow_fallback: bool = True,
    ) -> ChatResult:
        targets = self.resolve(model)
        if not allow_fallback:
            targets = targets[:1]
        last_error: ProviderError | SetupError | None = None
        fallbacks: list[str] = []
        for index, target in enumerate(targets):
            emitted = False

            def delta(text: str, _target: ModelTarget = target) -> None:
                nonlocal emitted
                emitted = True
                if on_delta:
                    on_delta(text)

            try:
                result = await target.client.chat(
                    model=target.model,
                    messages=messages,
                    tools=tools,
                    temperature=target.temperature if temperature is None else temperature,
                    max_tokens=int(max_tokens or self.cfg.get("max_tokens", 8192)),
                    stream=stream,
                    on_delta=delta,
                    on_reasoning=on_reasoning,
                )
                result.provider = target.provider.id
                result.model = target.model
                result.fallbacks = fallbacks
                return result
            except SetupError:
                raise
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or emitted or index == len(targets) - 1:
                    raise
                fallbacks.append(f"{target.provider.short}: {exc.short}")
                if on_notice:
                    on_notice(
                        f"{target.provider.short} недоступен ({exc.status or 'сеть'}) — "
                        f"переключаюсь на {targets[index + 1].provider.short}"
                    )
                continue
        if last_error:
            raise last_error
        raise ProviderError("не удалось получить ответ ни от одного провайдера")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1200,
        model: str | None = None,
    ) -> str:
        result = await self.chat(
            messages=messages,
            tools=None,
            model=model,
            max_tokens=max_tokens,
            stream=False,
        )
        return result.content.strip()

    # ------------------------------------------------------------------ status
    def describe(self, ref: str | None = None) -> tuple[str, str]:
        """Return (headline, detail) for the banner; never raises."""
        try:
            targets = self.resolve(ref)
        except SetupError as exc:
            return "нет активной модели", str(exc).splitlines()[0]
        target = targets[0]
        chain = ""
        if target.custom and len(targets) > 1:
            chain = " → ".join(t.provider.short for t in targets[:4])
        detail = target.model
        if chain:
            detail += f"  ·  fallback: {chain}"
        if target.provider.short.lower() in target.label.lower():
            return target.label, detail
        return f"{target.label} · {target.provider.short}", detail

    def context_limit(self, ref: str | None = None) -> int:
        try:
            return self.resolve(ref)[0].ctx
        except SetupError:
            return 128_000
