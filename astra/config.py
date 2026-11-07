"""Configuration and credential storage for Astra Code.

Layering (highest priority first):
  1. environment variables           (e.g. OPENROUTER_API_KEY)
  2. ``$ASTRA_HOME/auth.toml``       (written by ``astra auth set``)
  3. ``$ASTRA_HOME/config.toml``     (provider overrides / inline keys)
  4. built-in defaults
"""

from __future__ import annotations

import json
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__

APP_NAME = "Astra Code"
DEFAULT_CONFIG: dict[str, Any] = {
    "provider": "openrouter",
    "model": "astra-coder",
    "theme": "aurora",
    "language": "ru",
    "approval": "ask",          # ask | auto | readonly
    "max_steps": 40,
    "max_tokens": 8192,
    "temperature": 0.2,
    "auto_compact": True,
    "show_reasoning": False,
    "allow_outside": False,
    "allow_paid": False,
    "stream": True,
    "providers": {},
    "custom": {},
}

_CONFIG_TEMPLATE = """# Astra Code configuration
# Docs: README.md · credentials live in auth.toml (chmod 600)

provider = "openrouter"
model = "astra-coder"        # или "groq", "gemini", "openrouter:model:free"
theme = "aurora"             # aurora | mono
language = "ru"
approval = "ask"             # ask | auto | readonly
max_steps = 40
max_tokens = 8192
temperature = 0.2
auto_compact = true
show_reasoning = false
allow_outside = false
allow_paid = false

# Example: pin a provider model or override the chain of a custom model.
# [providers.groq]
# model = "openai/gpt-oss-120b"
#
# [custom.astra-coder]
# chain = ["groq", "openrouter", "gemini"]
"""


def astra_home() -> Path:
    raw = os.environ.get("ASTRA_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".astra").resolve()


def secure_dir(path: Path) -> None:
    """Best-effort 0700 on the private Astra directory (no-op limits on Windows)."""
    try:
        os.chmod(path, stat.S_IRWXU)
    except OSError:
        pass


def secure_file(path: Path) -> None:
    """Best-effort 0600 for auth/config/session/history files."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"cannot serialise {type(value)!r} to TOML")


def dumps_toml(data: dict[str, Any]) -> str:
    """Small dependency-free TOML serialiser for the flat-ish config we use."""
    out: list[str] = []

    def emit(table: dict[str, Any], prefix: str) -> None:
        scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
        tables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
        if prefix:
            out.append(f"[{prefix}]")
        for key, value in scalars:
            out.append(f"{key} = {_toml_value(value)}")
        if prefix and (scalars or tables):
            out.append("")
        for key, value in tables:
            name = f"{prefix}.{key}" if prefix else key
            emit(value, name)

    emit(data, "")
    return "\n".join(out).rstrip() + "\n"


def load_dotenv() -> None:
    """Read .env files without overwriting real environment variables."""
    candidates = [Path.cwd() / ".env", astra_home() / ".env"]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


@dataclass
class Config:
    data: dict[str, Any]
    path: Path
    auth: dict[str, Any]
    env: dict[str, str]
    cwd: Path
    home_dir: Path | None = None

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, cwd: str | Path | None = None, *, dotenv: bool = True) -> "Config":
        if dotenv:
            load_dotenv()
        home = astra_home()
        home.mkdir(parents=True, exist_ok=True)
        secure_dir(home)
        path = home / "config.toml"
        data = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
        if path.is_file():
            try:
                with path.open("rb") as fh:
                    loaded = tomllib.load(fh)
                data = _merge(data, loaded)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ConfigError(f"не удалось прочитать {path}: {exc}") from exc
        auth: dict[str, Any] = {}
        auth_path = home / "auth.toml"
        if auth_path.is_file():
            try:
                with auth_path.open("rb") as fh:
                    auth = tomllib.load(fh)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ConfigError(f"не удалось прочитать {auth_path}: {exc}") from exc
        return cls(data, path, auth, dict(os.environ), Path(cwd or os.getcwd()).resolve())

    # --------------------------------------------------------------- access
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    @property
    def model(self) -> str:
        return str(self.data.get("model") or "astra-coder")

    @property
    def provider(self) -> str:
        return str(self.data.get("provider") or "openrouter")

    @property
    def approval(self) -> str:
        return str(self.data.get("approval") or "ask")

    @property
    def home(self) -> Path:
        return self.home_dir or astra_home()

    def provider_cfg(self, provider_id: str) -> dict[str, Any]:
        value = self.data.get("providers", {}).get(provider_id, {})
        return value if isinstance(value, dict) else {}

    def custom_cfg(self, model_id: str) -> dict[str, Any]:
        value = self.data.get("custom", {}).get(model_id, {})
        return value if isinstance(value, dict) else {}

    def api_key(self, provider_id: str, env_key: str | None) -> str:
        if env_key:
            value = self.env.get(env_key) or os.environ.get(env_key)
            if value and value.strip():
                return value.strip()
        keys = self.auth.get("keys", {})
        if isinstance(keys, dict):
            value = keys.get(provider_id)
            if isinstance(value, str) and value.strip():
                return value.strip()
        inline = self.provider_cfg(provider_id).get("api_key")
        return inline.strip() if isinstance(inline, str) and inline.strip() else ""

    def option(self, provider_id: str, name: str, env_key: str | None = None) -> str:
        if env_key and self.env.get(env_key):
            return str(self.env[env_key]).strip()
        options = self.auth.get("options", {})
        if isinstance(options, dict):
            section = options.get(provider_id, {})
            if isinstance(section, dict) and section.get(name):
                return str(section[name]).strip()
        value = self.provider_cfg(provider_id).get(name)
        return str(value).strip() if value else ""

    # ----------------------------------------------------------------- save
    def save(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        secure_dir(self.home)
        text = dumps_toml(self.data)
        tmp = self.path.with_suffix(".toml.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)
        secure_file(self.path)
        return self.path

    def save_auth(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        secure_dir(self.home)
        path = self.home / "auth.toml"
        tmp = path.with_suffix(".toml.tmp")
        text = "# Astra Code credentials — do not commit.\n" + dumps_toml(self.auth)
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        secure_file(path)
        return path

    def set_key(self, provider_id: str, key: str) -> None:
        keys = self.auth.setdefault("keys", {})
        if not isinstance(keys, dict):
            keys = self.auth["keys"] = {}
        keys[provider_id] = key.strip()
        self.save_auth()

    def delete_key(self, provider_id: str) -> bool:
        keys = self.auth.get("keys", {})
        if isinstance(keys, dict) and provider_id in keys:
            del keys[provider_id]
            self.save_auth()
            return True
        return False

    def set_option(self, provider_id: str, name: str, value: str) -> None:
        options = self.auth.setdefault("options", {})
        if not isinstance(options, dict):
            options = self.auth["options"] = {}
        section = options.setdefault(provider_id, {})
        if not isinstance(section, dict):
            section = options[provider_id] = {}
        section[name] = value.strip()
        self.save_auth()

    def ensure_config_file(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        secure_dir(self.home)
        if not self.path.exists():
            self.path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
            secure_file(self.path)
        return self.path

    def describe(self) -> dict[str, str]:
        return {
            "home": str(self.home),
            "config": str(self.path),
            "version": __version__,
        }


class ConfigError(RuntimeError):
    pass


def mask(secret: str, keep: int = 4) -> str:
    if not secret:
        return "—"
    if len(secret) <= keep * 2:
        return "•" * len(secret)
    return f"{secret[:keep]}…{secret[-keep:]}"


def shorten_path(path: str | Path, width: int = 46) -> str:
    text = str(path)
    home = str(Path.home())
    if text.startswith(home):
        text = "~" + text[len(home):]
    if len(text) <= width:
        return text
    parts = Path(text).parts
    if len(parts) >= 3:
        return str(Path(parts[0]) / "…" / parts[-1])[:width]
    return "…" + text[-(width - 1):]
