"""`astra demo` — a complete agent run with no API keys and no network."""

from __future__ import annotations

import tempfile
from pathlib import Path

from rich.console import Console
from rich.text import Text

from ..config import Config, shorten_path
from .app import AstraApp
from . import render
from .theme import palette


def _make_demo_project(root: Path) -> None:
    (root / ".claude" / "skills" / "check-output").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "commands").mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(
        "# Demo project\n\n"
        "This folder is created by `astra demo`. It shows how Astra discovers "
        "Claude Code / Codex project files.\n\n"
        "## Rules\n\n- Keep examples tiny and runnable.\n- Verify output by running the script.\n",
        encoding="utf-8",
    )
    (root / ".claude" / "skills" / "check-output" / "SKILL.md").write_text(
        "---\n"
        "name: check-output\n"
        "description: Run a script and verify its stdout before reporting success.\n"
        "allowed-tools: run_command, read_file\n"
        "---\n\n"
        "# Check output\n\n"
        "1. Run the script with the project's Python.\n"
        "2. Compare stdout with the expected text.\n"
        "3. Report the exact output, not a paraphrase.\n",
        encoding="utf-8",
    )
    (root / ".claude" / "commands" / "review.md").write_text(
        "---\n"
        "description: Review the current changes\n"
        "argument-hint: <path>\n"
        "---\n\n"
        "Review $ARGUMENTS for bugs, missing tests and risky assumptions. "
        "Report findings with path:line references.",
        encoding="utf-8",
    )


async def run_demo(console: Console, *, theme: str = "aurora", live_enabled: bool | None = None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="astra-demo-")).resolve()
    _make_demo_project(root)

    cfg = Config.load(root)
    cfg.home_dir = root / ".astra"
    cfg.cwd = root
    cfg.data.update(
        {
            "model": "mock",
            "approval": "auto",
            "theme": theme,
            "show_reasoning": False,
            "allow_paid": False,
        }
    )
    app = AstraApp(
        cfg,
        root,
        console=console,
        auto_approve=True,
        theme=theme,
        live_enabled=live_enabled,
    )
    render.render_banner(
        console,
        cfg,
        app.router,
        skills=len(app.skills),
        agents=len(app.agents),
        commands=len(app.commands),
        theme=theme,
        demo=True,
    )
    console.print(
        Text(
            "  В демо-проекте: AGENTS.md · .claude/skills/check-output · .claude/commands/review.md",
            style=palette(theme)["muted"],
        )
    )
    console.print()

    await app.ask(
        "Создай hello.py с функцией greet() и проверь, что он запускается.",
        show_user=True,
    )

    console.print()
    console.print(
        Text.assemble(
            ("  ✓ демо завершено. Проект: ", palette(theme)["ok"]),
            (shorten_path(root, 70), palette(theme)["text"]),
        )
    )
    console.print(
        Text(
            "  Дальше: astra auth set openrouter  →  astra  →  опишите задачу",
            style=palette(theme)["muted"],
        )
    )
    return root
