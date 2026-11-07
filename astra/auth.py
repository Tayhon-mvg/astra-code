"""Interactive free-provider setup and key validation."""

from __future__ import annotations

import getpass
import sqlite3
import webbrowser
from pathlib import Path
from typing import Callable

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from . import catalog
from .config import Config, mask
from .ui.theme import palette

SETUP_ORDER = [
    "openrouter",
    "gemini",
    "groq",
    "sambanova",
    "zai",
    "cloudflare",
    "mistral",
    "ollama-cloud",
    "omniroute",
    "ollama",
    "lmstudio",
    "custom",
]


def read_omniroute_key() -> str | None:
    """Read the active local OmniRoute API key without printing it.

    OmniRoute stores the key in plaintext in its local SQLite DB (the hash is a
    separate column). This is a local-only, opt-in import used by
    ``astra auth set omniroute --from-omniroute``.
    """
    db = Path.home() / ".omniroute" / "storage.sqlite"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute(
                "select key from api_keys where is_active=1 "
                "order by coalesce(last_used_at,'') desc limit 1"
            ).fetchone()
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return None
    if not row or not isinstance(row[0], str):
        return None
    key = row[0].strip()
    return key if key.startswith("sk-") and len(key) >= 20 else None


def _colors(theme: str = "aurora") -> dict[str, str]:
    return palette(theme)


def validate(cfg: Config, provider_id: str, timeout: float = 18.0) -> tuple[bool, str, int]:
    """Return (ok, message, model_count). Never raises."""
    provider = catalog.PROVIDERS.get(provider_id)
    if provider is None:
        return False, f"неизвестный провайдер {provider_id}", 0
    base_url = catalog.resolve_base_url(provider, cfg)
    if not base_url:
        return False, "не задан base_url", 0
    if provider.requires_account_id and "{account_id}" in base_url:
        return False, "не задан account_id", 0
    headers = {"Accept": "application/json", "User-Agent": "AstraCode/0.1"}
    key = cfg.api_key(provider_id, provider.env_key)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    for extra_key, extra_value in provider.headers:
        headers[extra_key] = extra_value
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(f"{base_url}/models", headers=headers)
    except httpx.HTTPError as exc:
        return False, f"сеть недоступна: {exc.__class__.__name__}", 0
    if response.status_code in (401, 403):
        return False, "ключ не принят (401/403)", 0
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}: {response.text[:160]}", 0
    try:
        data = response.json()
        rows = data.get("data") if isinstance(data, dict) else data
        count = len(rows) if isinstance(rows, list) else 0
    except ValueError:
        count = 0
    return True, "соединение работает", count


def _choose_provider(console: Console, theme: str) -> str | None:
    colors = _colors(theme)
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {colors['primary']}", expand=True)
    table.add_column("#", width=3, justify="right", style=colors["muted"])
    table.add_column("провайдер", no_wrap=True)
    table.add_column("бесплатно", overflow="fold", style=colors["muted"])
    for index, provider_id in enumerate(SETUP_ORDER, 1):
        provider = catalog.PROVIDERS[provider_id]
        table.add_row(str(index), Text(provider.name, style=colors["text"]), provider.free_note)
    console.print(table)
    raw = input("  Номер провайдера (Enter — отмена): ").strip()
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(SETUP_ORDER):
        return SETUP_ORDER[int(raw) - 1]
    if raw in catalog.PROVIDERS:
        return raw
    console.print(Text("  Не понял номер.", style=colors["err"]))
    return None


def setup_interactive(
    cfg: Config,
    provider_id: str | None = None,
    *,
    console: Console | None = None,
    theme: str = "aurora",
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
) -> bool:
    console = console or Console()
    colors = _colors(theme)
    if not provider_id:
        provider_id = _choose_provider(console, theme)
        if not provider_id:
            return False
    provider = catalog.PROVIDERS.get(provider_id)
    if provider is None:
        console.print(Text(f"Неизвестный провайдер: {provider_id}", style=colors["err"]))
        return False

    url, steps = catalog.SIGNUP_STEPS.get(provider_id, ("", []))
    body = Text()
    if steps:
        for index, step in enumerate(steps, 1):
            body.append(f"{index}. {step}\n", style=colors["text"])
    if provider.free_note:
        body.append("\n" + provider.free_note + "\n", style=colors["muted"])
    if url:
        body.append("\n" + url, style=f"link {colors['accent']}")
    console.print(
        Panel(
            body,
            title=f"[{colors['primary']}]✦ {provider.name}[/]",
            border_style=colors["border"],
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )

    if url:
        try:
            answer = input_fn("  Открыть страницу в браузере? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer in {"", "y", "yes", "д", "да"}:
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 - browser is a nicety
                pass

    if provider_id == "omniroute":
        local_key = read_omniroute_key()
        key = ""
        if local_key:
            console.print(
                Text(
                    f"  Найден локальный ключ OmniRoute ({mask(local_key)}).",
                    style=colors["muted"],
                )
            )
            try:
                answer = input_fn("  Использовать его? [Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            if answer in {"", "y", "yes", "д", "да"}:
                key = local_key
        if not key:
            try:
                key = secret_fn("  OMNIROUTE_API_KEY (Enter — отмена): ").strip()
            except (EOFError, KeyboardInterrupt):
                return False
        if not key:
            console.print(Text("  Пустой ключ — отмена.", style=colors["warn"]))
            return False
        previous = cfg.api_key(provider_id, provider.env_key)
        cfg.set_key(provider_id, key)
        console.print(Text("  Проверяю OmniRoute…", style=colors["muted"]))
        ok, message, count = validate(cfg, provider_id)
        if not ok and "401" in message:
            if previous:
                cfg.set_key(provider_id, previous)
            else:
                cfg.delete_key(provider_id)
            _report_validation(console, provider, False, message, count, theme)
            return False
        _report_validation(console, provider, ok, message, count, theme)
        console.print(
            Text(
                f"  Ключ сохранён в {cfg.home / 'auth.toml'} ({mask(key)}). "
                f"Дальше: astra models --provider omniroute --refresh",
                style=colors["muted"],
            )
        )
        return True

    if provider.local:
        if provider_id == "custom":
            base = input_fn("  base_url (например http://localhost:8080/v1): ").strip()
            if not base:
                return False
            cfg.data.setdefault("providers", {}).setdefault("custom", {})["base_url"] = base
            cfg.save()
            key = secret_fn("  API key (Enter — без ключа): ").strip()
            if key:
                cfg.set_key("custom", key)
        ok, message, count = validate(cfg, provider_id)
        _report_validation(console, provider, ok, message, count, theme)
        return True

    if provider.env_key is None:
        _report_validation(console, provider, False, "провайдер без ключа", 0, theme)
        return False

    try:
        key = secret_fn(f"  {provider.env_key}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    if not key:
        console.print(Text("  Пустой ключ — отмена.", style=colors["warn"]))
        return False
    if provider.requires_account_id:
        account = input_fn("  Account ID: ").strip()
        if not account:
            console.print(Text("  Нужен Account ID.", style=colors["err"]))
            return False
        cfg.set_option(provider_id, "account_id", account)

    previous = cfg.api_key(provider_id, provider.env_key)
    cfg.set_key(provider_id, key)
    console.print(Text("  Проверяю ключ…", style=colors["muted"]))
    ok, message, count = validate(cfg, provider_id)
    if not ok and "401" in message:
        if previous:
            cfg.set_key(provider_id, previous)
        else:
            cfg.delete_key(provider_id)
        _report_validation(console, provider, False, message, count, theme)
        console.print(
            Text(
                "  Ключ не сохранён. Проверьте, что скопировали его целиком и он активен.",
                style=colors["warn"],
            )
        )
        return False
    _report_validation(console, provider, ok, message, count, theme)
    console.print(
        Text(
            f"  Ключ сохранён в {cfg.home / 'auth.toml'} ({mask(key)}). "
            f"Модели: /model или astra models --refresh",
            style=colors["muted"],
        )
    )
    return True


def _report_validation(console: Console, provider: catalog.Provider, ok: bool, message: str, count: int, theme: str) -> None:
    colors = _colors(theme)
    if ok:
        console.print(
            Text(f"  ✓ {provider.short}: {message}" + (f" · моделей: {count}" if count else ""), style=colors["ok"])
        )
    else:
        console.print(Text(f"  ✗ {provider.short}: {message}", style=colors["warn"] if provider.local else colors["err"]))


def remove_key(cfg: Config, provider_id: str) -> bool:
    return cfg.delete_key(provider_id)


def key_rows(cfg: Config) -> list[tuple[str, str, str]]:
    rows = []
    for provider_id in catalog.PROVIDER_ORDER:
        provider = catalog.PROVIDERS[provider_id]
        key = cfg.api_key(provider_id, provider.env_key)
        if provider.local:
            status = "локальный"
        elif key:
            status = f"есть ({mask(key)})"
        else:
            status = "нет"
        rows.append((provider.short, status, provider.env_key or "—"))
    return rows
