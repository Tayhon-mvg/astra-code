"""Session persistence (JSON per session, atomic writes)."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import secure_dir, secure_file
from .providers import estimate_tokens


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    steps: int = 0
    seconds: float = 0.0
    turns: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt: int, completion: int, steps: int = 0, seconds: float = 0.0) -> None:
        self.prompt_tokens += max(0, prompt)
        self.completion_tokens += max(0, completion)
        self.steps += max(0, steps)
        self.seconds += max(0.0, seconds)
        self.turns += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "steps": self.steps,
            "seconds": round(self.seconds, 2),
            "turns": self.turns,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Usage":
        def num(key: str, cast=float) -> float:
            try:
                return cast(data.get(key) or 0)
            except (TypeError, ValueError):
                return 0
        return cls(
            prompt_tokens=num("prompt_tokens", int),
            completion_tokens=num("completion_tokens", int),
            steps=num("steps", int),
            seconds=num("seconds", float),
            turns=num("turns", int),
        )


@dataclass
class Session:
    id: str
    cwd: str
    created: float
    updated: float
    title: str = "новая сессия"
    provider: str = ""
    model: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    @classmethod
    def new(cls, cwd: Path, provider: str = "", model: str = "") -> "Session":
        now = time.time()
        return cls(
            id=time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2),
            cwd=str(cwd),
            created=now,
            updated=now,
            provider=provider,
            model=model,
        )

    # ------------------------------------------------------------------ state
    @property
    def message_tokens(self) -> int:
        return estimate_tokens(self.messages)

    def add(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        self.updated = time.time()
        if self.title == "новая сессия" and message.get("role") == "user":
            text = str(message.get("content") or "").strip().replace("\n", " ")
            if text:
                self.title = text[:64] + ("…" if len(text) > 64 else "")

    def reset(self) -> None:
        self.messages.clear()
        self.usage = Usage()
        self.title = "новая сессия"
        self.updated = time.time()

    def apply_compaction(self, new_messages: list[dict[str, Any]]) -> None:
        self.messages = new_messages
        self.updated = time.time()

    # ------------------------------------------------------------------- disk
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cwd": self.cwd,
            "created": self.created,
            "updated": self.updated,
            "title": self.title,
            "provider": self.provider,
            "model": self.model,
            "messages": self.messages,
            "usage": self.usage.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        messages = data.get("messages")
        return cls(
            id=str(data.get("id") or ""),
            cwd=str(data.get("cwd") or ""),
            created=float(data.get("created") or 0),
            updated=float(data.get("updated") or 0),
            title=str(data.get("title") or "сессия"),
            provider=str(data.get("provider") or ""),
            model=str(data.get("model") or ""),
            messages=messages if isinstance(messages, list) else [],
            usage=Usage.from_dict(data.get("usage") or {}),
        )

    def save(self, home: Path) -> Path:
        directory = home / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        secure_dir(directory)
        path = directory / f"{self.id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        secure_file(path)
        return path

    @classmethod
    def load(cls, path: Path) -> "Session":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


def sessions_dir(home: Path) -> Path:
    return home / "sessions"


def list_sessions(home: Path, cwd: Path | None = None) -> list[Session]:
    directory = sessions_dir(home)
    if not directory.is_dir():
        return []
    sessions: list[Session] = []
    for path in directory.glob("*.json"):
        try:
            session = Session.load(path)
        except (OSError, ValueError, TypeError):
            continue
        if cwd is not None and Path(session.cwd).resolve() != cwd.resolve():
            continue
        sessions.append(session)
    sessions.sort(key=lambda s: s.updated, reverse=True)
    return sessions


def latest_session(home: Path, cwd: Path | None = None) -> Session | None:
    found = list_sessions(home, cwd)
    return found[0] if found else None


def find_session(home: Path, prefix: str) -> Session | None:
    for session in list_sessions(home):
        if session.id == prefix or session.id.startswith(prefix):
            return session
    return None


def relative_time(timestamp: float) -> str:
    delta = max(0, time.time() - timestamp)
    if delta < 60:
        return "только что"
    if delta < 3600:
        return f"{int(delta // 60)} мин назад"
    if delta < 86400:
        return f"{int(delta // 3600)} ч назад"
    if delta < 86400 * 7:
        return f"{int(delta // 86400)} дн назад"
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))
