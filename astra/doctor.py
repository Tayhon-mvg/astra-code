"""`astra doctor` — environment and configuration diagnostics."""

from __future__ import annotations

import importlib.metadata
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .agents import AgentRegistry
from .commands import CommandRegistry
from .config import Config
from .router import Router
from .skills import SkillRegistry
from .ui.theme import palette


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "нет"


def run_checks(
    cfg: Config,
    router: Router,
    *,
    cwd: Path,
    skills: SkillRegistry | None = None,
    agents: AgentRegistry | None = None,
    commands: CommandRegistry | None = None,
    network: bool = False,
) -> list[Check]:
    checks: list[Check] = []
    py_ok = sys.version_info >= (3, 11)
    checks.append(Check("Python", py_ok, sys.version.split()[0], "нужен Python 3.11+"))
    encoding = (sys.stdout.encoding or "").lower()
    checks.append(
        Check(
            "Кодировка",
            "utf" in encoding,
            encoding or "неизвестно",
            "Windows: set PYTHONUTF8=1",
        )
    )
    checks.append(Check("rich", _version("rich") != "нет", _version("rich")))
    checks.append(Check("httpx", _version("httpx") != "нет", _version("httpx")))
    checks.append(Check("prompt_toolkit", _version("prompt_toolkit") != "нет", _version("prompt_toolkit")))
    checks.append(Check("git", bool(shutil.which("git")), shutil.which("git") or "не найден", "git нужен для git-контекста"))

    home = cfg.home
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check("ASTRA_HOME", True, str(home)))
    except OSError as exc:
        checks.append(Check("ASTRA_HOME", False, f"{home}: {exc}", "проверьте права на папку"))
    try:
        probe = cwd / ".astra-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check("Запись в проект", True, str(cwd)))
    except OSError as exc:
        checks.append(Check("Запись в проект", False, f"{cwd}: {exc}", "агент не сможет менять файлы"))

    states = router.states()
    available = [state for state in states if state.available]
    checks.append(
        Check(
            "Провайдеры",
            bool(available),
            f"{len(available)}/{len(states)} доступно",
            "добавьте бесплатный ключ: astra auth set openrouter",
        )
    )
    for state in available:
        provider = state.provider
        checks.append(
            Check(
                f"  {provider.short}",
                True,
                state.status,
                "",
            )
        )
    if network:
        from .auth import validate

        for state in available:
            ok, message, count = validate(cfg, state.provider.id, timeout=10)
            checks.append(Check(f"  сеть: {state.provider.short}", ok, message))

    try:
        targets = router.resolve(cfg.model)
        checks.append(
            Check(
                "Активная модель",
                True,
                f"{targets[0].label} · {targets[0].provider.short} · fallback {max(0, len(targets) - 1)}",
            )
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics should show it
        checks.append(Check("Активная модель", False, str(exc)[:180], "astra auth set <provider>"))

    try:
        skills = skills or SkillRegistry([])
        agents = agents or AgentRegistry([])
        commands = commands or CommandRegistry([])
        checks.append(
            Check(
                "Расширения",
                True,
                f"скиллов {len(skills)} · агентов {len(agents)} · команд {len(commands)}",
                "папки: .claude/skills, .claude/agents, .claude/commands",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("Расширения", False, str(exc)[:180]))

    roots = [
        cwd / ".claude",
        cwd / ".codex",
        cwd / ".astra",
        home / ".claude",
        home / ".codex",
        home / ".astra",
    ]
    existing = [str(path) for path in roots if path.is_dir()]
    checks.append(
        Check(
            "Экосистема",
            True,
            ", ".join(existing) if existing else "локальных папок Claude/Codex нет — это нормально",
        )
    )
    checks.append(
        Check(
            "Демо без ключей",
            True,
            "astra demo",
        )
    )
    return checks


def render(console: Console, checks: list[Check], *, theme: str = "aurora") -> None:
    colors = palette(theme)
    table = Table(
        box=box.SIMPLE_HEAD,
        header_style=f"bold {colors['primary']}",
        border_style=colors["border"],
        expand=True,
    )
    table.add_column("", width=3)
    table.add_column("проверка", no_wrap=True)
    table.add_column("результат", overflow="fold")
    table.add_column("подсказка", overflow="fold", style=colors["muted"])
    for check in checks:
        mark = Text("✓", style=colors["ok"]) if check.ok else Text("✗", style=colors["err"])
        table.add_row(mark, Text(check.name, style=colors["text"]), Text(check.detail), Text(check.hint))
    console.print(table)
    failed = sum(1 for check in checks if not check.ok)
    if failed:
        console.print(Text(f"  {failed} проблем(ы) — смотрите подсказки выше.", style=colors["warn"]))
    else:
        console.print(Text("  Всё готово.", style=colors["ok"]))
