from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from astra.catalog import Provider
from astra.providers import (
    AuthError,
    OpenAICompatClient,
    RateLimitError,
    _ToolMarkupFilter,
    strip_tool_markup,
)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    auth_seen: str = ""
    last_payload: dict[str, Any] = {}

    def log_message(self, fmt, *args):  # noqa: ANN001
        pass

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/models"):
            self._json(
                200,
                {
                    "data": [
                        {
                            "id": "free/model",
                            "context_length": 64000,
                            "pricing": {"prompt": "0", "completion": "0"},
                            "owned_by": "kiro",
                            "type": "text",
                            "capabilities": {"tool_calling": True},
                            "max_output_tokens": 4096,
                        },
                        {
                            "id": "paid/model",
                            "context_length": 128000,
                            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                        },
                    ]
                },
            )
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        Handler.last_payload = payload
        auth = self.headers.get("Authorization", "")
        Handler.auth_seen = auth
        if auth == "Bearer bad":
            self._json(401, {"error": {"message": "invalid api key"}})
            return
        if payload.get("model") == "ratelimit":
            body = json.dumps({"error": {"message": "slow down"}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "2")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not payload.get("stream"):
            self._json(
                200,
                {
                    "choices": [{"message": {"content": "nonstream answer"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                },
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        events = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Привет, "}}]},
            {"choices": [{"index": 0, "delta": {"content": "мир"}}]},
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "read_file", "arguments": '{"path": "a'},
                                }
                            ]
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '.py"}'}}
                            ]
                        },
                    }
                ]
            },
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
        ]
        for event in events:
            self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()
    httpd.server_close()


def client(base: str, key: str = "test-key") -> OpenAICompatClient:
    provider = Provider(
        id="test",
        name="Test Provider",
        base_url=base,
        env_key="TEST_KEY",
        signup_url="",
        docs_url="",
        free_note="free",
    )
    return OpenAICompatClient(provider, base, key)


def test_streaming_content_and_tool_calls(server):
    seen: list[str] = []
    result = asyncio.run(
        client(server).chat(
            model="free/model",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
            on_delta=seen.append,
        )
    )
    assert result.content == "Привет, мир"
    assert "".join(seen) == result.content
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "read_file"
    assert json.loads(result.tool_calls[0].arguments) == {"path": "a.py"}
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 5
    assert Handler.auth_seen == "Bearer test-key"
    assert Handler.last_payload["stream"] is True


def test_non_stream(server):
    result = asyncio.run(
        client(server).chat(
            model="free/model",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
        )
    )
    assert result.content == "nonstream answer"
    assert result.total_tokens == 10


def test_models_free_flag(server):
    models = asyncio.run(client(server).list_models())
    by_id = {model["id"]: model for model in models}
    assert by_id["free/model"]["free"] is True
    assert by_id["paid/model"]["free"] is False
    assert by_id["free/model"]["ctx"] == 64000
    assert by_id["free/model"]["owned_by"] == "kiro"
    assert by_id["free/model"]["capabilities"]["tool_calling"] is True
    assert by_id["free/model"]["max_output_tokens"] == 4096


def test_auth_error(server):
    with pytest.raises(AuthError):
        asyncio.run(
            client(server, "bad").chat(
                model="free/model",
                messages=[{"role": "user", "content": "hi"}],
            )
        )


def test_rate_limit(server):
    with pytest.raises(RateLimitError) as exc:
        asyncio.run(
            client(server).chat(
                model="ratelimit",
                messages=[{"role": "user", "content": "hi"}],
            )
        )
    assert exc.value.retry_after == 2.0
    assert exc.value.retryable is True


def test_strip_tool_markup():
    assert strip_tool_markup("hello\n<｜DSML｜function_calls") == "hello"
    assert strip_tool_markup("plain answer") == "plain answer"
    assert strip_tool_markup("ok<|tool_call|>{}") == "ok"


def test_markup_filter_across_chunks():
    markup = _ToolMarkupFilter()
    out = markup.feed("hello ") + markup.feed("<｜DSML｜function_calls") + markup.flush()
    assert out.strip() == "hello"
    assert "DSML" not in out
