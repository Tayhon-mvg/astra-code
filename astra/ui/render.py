"""Rich renderers: banner, streamed markdown, tool lines, tables, panels."""

from __future__ import annotations

import time
from typing import Any, Iterable

from rich import box
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import catalog
from ..config import Config, shorten_path
from ..router import Router
from .theme import palette

LOGO_TOP = "▄▀█ █▀ ▀█▀ █▀█ ▄▀█"
LOGO_BOTTOM = "█▀█ ▄█  █  █▀▄ █▀█"


def _c(theme: str, key: str) -> str:
    return palette(theme).get(key, "white")


# --------------------------------------------------------------------------- banner


def render_banner(
    console: Console,
    cfg: Config,
    router: Router,
    *,
    skills: int = 0,
    agents: int = 0,
    commands: int = 0,
    theme: str = "aurora",
    demo: bool = False,
) -> None:
    width = min(console.width, 96)
    colors = palette(theme)
    logo = Text()
    logo.append(LOGO_TOP, style=f"bold {colors['primary']}")
    logo.append("   C O D E\n", style=f"bold {colors['accent']}")
    logo.append(LOGO_BOTTOM, style=f"bold {colors['primary']}")
    logo.append("   v0.1", style=f"dim {colors['muted']}")
    console.print(Padding(logo, (1, 0, 0, 2)))
    console.print(
        Padding(
            Text("бесплатный coding-агент · Claude Code / Codex skills", style=colors["muted"]),
            (0, 0, 1, 2),
        )
    )

    available = router.available_providers()
    total = len(catalog.PROVIDER_ORDER)
    if demo:
        console.print(
            Panel(
                Text.from_markup(
                    f"[{colors['ok']}]✓ офлайн-демо[/]  [{colors['muted']}]— реальные вызовы не выполняются[/]\n"
                    f"[{colors['text']}]Astra Demo[/] [{colors['muted']}]· mock-провайдер · "
                    f"показан полный цикл: todo → файл → запуск → ответ[/]"
                ),
                border_style=colors["ok"],
                box=box.ROUNDED,
                padding=(0, 1),
                width=width,
            )
        )
        console.print()
        return
    if not available:
        console.print(
            Panel(
                Text.from_markup(
                    f"[{colors['warn']}]⚠ ключей нет.[/]  "
                    f"[{colors['text']}]/setup[/] — добавить бесплатный ключ   "
                    f"[{colors['text']}]/keys[/] — список провайдеров\n"
                    f"[{colors['muted']}]Посмотреть интерфейс без ключей:[/] "
                    f"[{colors['accent']}]astra demo[/]"
                ),
                border_style=colors["warn"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
        console.print()
        return

    try:
        headline, detail = router.describe(cfg.model)
    except Exception:  # noqa: BLE001 - banner must never crash
        headline, detail = cfg.model, ""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style=colors["muted"], no_wrap=True)
    grid.add_column(style=colors["text"], overflow="fold")
    grid.add_row("модель", Text(headline, style=f"bold {colors['primary']}"))
    if detail:
        grid.add_row("", Text(detail, style=colors["muted"]))
    grid.add_row("папка", Text(shorten_path(cfg.cwd, 54), style=colors["text"]))
    grid.add_row(
        "окружение",
        Text(
            f"провайдеров {len(available)}/{total} · скиллов {skills} · агентов {agents} · команд {commands}",
            style=colors["muted"],
        ),
    )
    console.print(
        Panel(
            grid,
            title=f"[{colors['primary']}]✦ Astra Code[/]",
            border_style=colors["border"],
            box=box.ROUNDED,
            padding=(0, 1),
            width=width,
        )
    )
    if not any(not provider.local for provider in available):
        console.print(
            Text(
                "  ⚠ облачных ключей нет — доступны только локальные Ollama/LM Studio. "
                "Добавить бесплатный ключ: /setup",
                style=colors["warn"],
            )
        )
    console.print(
        Padding(
            Text.from_markup(
                f"[{colors['muted']}]Enter — отправить · Ctrl+J — строка · "
                f"/help · @file · !cmd[/]"
            ),
            (0, 0, 1, 2),
        )
    )


# ------------------------------------------------------------------------- streaming


class StreamRenderer:
    """Single-line typing status, then one clean Markdown answer.

    Multi-line Live previews are unreliable on Windows terminals (cursor-up
    fails near the bottom of the buffer), so Astra keeps exactly one line
    animated and renders the answer once, at the end.
    """

    def __init__(
        self,
        console: Console,
        subtitle: str = "",
        *,
        theme: str = "aurora",
        show_reasoning: bool = False,
        live_enabled: bool | None = None,
        show_header: bool = True,
    ) -> None:
        self.console = console
        self.subtitle = subtitle
        self.theme = theme
        self.show_reasoning = show_reasoning
        self.live_enabled = console.is_terminal if live_enabled is None else live_enabled
        self.show_header = show_header
        self.buffer = ""
        self.reasoning = ""
        self._status: Any = None
        self._last = 0.0

    # ------------------------------------------------------------------ state
    def begin(self) -> None:
        if not self.live_enabled or self._status is not None:
            return
        self._status = self.console.status(
            Text("✻ думаю…", style=f"dim {_c(self.theme, 'muted')}"),
            spinner="dots12",
            spinner_style=_c(self.theme, "primary"),
        )
        self._status.start()

    def feed(self, text: str) -> None:
        if not text:
            return
        self.buffer += text
        if not self.live_enabled:
            return
        if self._status is None:
            self.begin()
            if self._status is None:
                return
        now = time.monotonic()
        if now - self._last > 0.12:
            self._last = now
            chars = len(self.buffer)
            self._status.update(
                Text(
                    f"✻ Astra пишет · {chars} симв.",
                    style=f"dim {_c(self.theme, 'muted')}",
                )
            )

    def add_reasoning(self, text: str) -> None:
        self.reasoning += text

    def _header(self) -> Text:
        colors = palette(self.theme)
        header = Text.assemble(
            ("◆ ", colors["primary"]),
            ("Astra", f"bold {colors['primary']}"),
        )
        if self.subtitle:
            header.append("  " + self.subtitle, style=colors["muted"])
        return header

    def _full(self) -> RenderableType:
        body = Padding(Markdown(self.buffer, code_theme="monokai", justify="left"), (0, 0, 0, 2))
        if not self.show_header:
            return body
        return Group(self._header(), body)

    def finish(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
        if self.buffer:
            self.console.print(self._full())
        if self.reasoning and self.show_reasoning:
            preview = self.reasoning.strip()
            if len(preview) > 2400:
                preview = preview[:2400] + "…"
            self.console.print(
                Panel(
                    Text(preview, style=f"dim {palette(self.theme)['muted']}"),
                    title="✧ reasoning",
                    border_style=palette(self.theme)["border"],
                    box=box.ROUNDED,
                    padding=(0, 1),
                )
            )
        self.buffer = ""
        self.reasoning = ""


# ----------------------------------------------------------------------- tool lines


def tool_start(console: Console, name: str, summary: str, *, theme: str = "aurora") -> None:
    colors = palette(theme)
    line = Text("  ⚙ ", style=colors["tool"])
    line.append(name, style=f"bold {colors['tool']}")
    if summary:
        line.append("  " + summary, style=colors["muted"])
    console.print(line, end="")


def tool_end(console: Console, result: str, ok: bool, seconds: float, *, theme: str = "aurora") -> None:
    colors = palette(theme)
    brief = result_brief(result)
    style = colors["ok"] if ok else colors["err"]
    marker = "  ✓ " if ok else "  ✗ "
    line = Text(marker, style=style)
    if brief:
        line.append(brief, style=colors["muted"] if ok else colors["err"])
    line.append(f"  {seconds:.1f}s" if seconds >= 0.1 else "", style=f"dim {colors['muted']}")
    console.print(line)
    if not ok:
        detail = result.strip().splitlines()
        body = "\n".join(detail[:10])
        console.print(Padding(Text(body, style=colors["err"]), (0, 0, 0, 5)))


def tool_block_start(console: Console, text: str, *, theme: str = "aurora") -> None:
    colors = palette(theme)
    console.print(Text("  ▸ " + text, style=f"bold {colors['tool']}"))


def tool_block_end(console: Console, result: str, ok: bool, seconds: float, *, theme: str = "aurora") -> None:
    colors = palette(theme)
    lines = result.strip().splitlines()
    status = lines[0] if lines else ("OK" if ok else "FAILED")
    output = "\n".join(lines[1:]) if len(lines) > 1 else ""
    console.print(Text("    " + status, style=colors["ok"] if ok else colors["err"]))
    if output:
        preview = output.strip().splitlines()
        if len(preview) > 12:
            preview = preview[:12] + ["…"]
        console.print(
            Padding(
                Text("\n".join(preview), style=colors["muted"]),
                (0, 0, 0, 6),
            )
        )


def result_brief(result: str) -> str:
    lines = [line for line in result.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    first = lines[0]
    if first.startswith("OK:"):
        return first[3:].strip()[:120]
    if first.startswith("FAILED:"):
        return first[:120]
    if first.startswith("ERROR:"):
        return first[:160]
    if first.startswith("# "):
        return first[2:][:120]
    if first.startswith("Найдено") or first.startswith("Нет файлов") or first.startswith("Совпадений"):
        return first[:120]
    if first.startswith("Todo"):
        return first[:120]
    if first.startswith("URL:"):
        return first[:120]
    return first[:100]


# --------------------------------------------------------------------------- panels


def error_panel(console: Console, title: str, message: str, hint: str = "", *, theme: str = "aurora") -> None:
    colors = palette(theme)
    body = Text(message, style=colors["text"])
    if hint:
        body.append("\n\n")
        body.append(hint, style=colors["muted"])
    console.print(
        Panel(
            body,
            title=f"[{colors['err']}]✗ {title}[/]",
            border_style=colors["err"],
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def notice(console: Console, text: str, level: str = "info", *, theme: str = "aurora") -> None:
    colors = palette(theme)
    icons = {"info": ("ℹ", colors["muted"]), "ok": ("✓", colors["ok"]), "warn": ("⚠", colors["warn"]), "err": ("✗", colors["err"]), "agent": ("⇢", colors["agent"])}
    icon, color = icons.get(level, icons["info"])
    console.print(Text(f"  {icon} {text}", style=color))


def footer(console: Console, *, steps: int, prompt_tokens: int, completion_tokens: int, seconds: float, fallbacks: list[str], tools: int, cancelled: bool = False, theme: str = "aurora") -> None:
    colors = palette(theme)
    if cancelled:
        console.print(Text(f"  ⏹ остановлено · {steps} шагов", style=colors["warn"]))
        return
    line = Text("  ✓ готово", style=f"bold {colors['ok']}")
    line.append(f" · {steps} {plural(steps, 'шаг', 'шага', 'шагов')}", style=colors["muted"])
    if tools:
        line.append(
            f" · {tools} {plural(tools, 'инструмент', 'инструмента', 'инструментов')}",
            style=colors["muted"],
        )
    line.append(
        f" · {prompt_tokens / 1000:.1f}k in / {completion_tokens / 1000:.1f}k out",
        style=colors["muted"],
    )
    line.append(f" · {seconds:.1f}s · $0", style=colors["muted"])
    if fallbacks:
        line.append(f" · fallback {len(fallbacks)}", style=colors["warn"])
    console.print(line)


def plural(number: int, one: str, few: str, many: str) -> str:
    number = abs(int(number))
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def todo_line(console: Console, todos: list[dict[str, str]], *, theme: str = "aurora") -> None:
    if not todos:
        return
    colors = palette(theme)
    done = sum(1 for todo in todos if todo.get("status") == "completed")
    current = next((todo for todo in todos if todo.get("status") == "in_progress"), None)
    text = Text("  ☑ ", style=colors["accent"])
    text.append(f"todo {done}/{len(todos)}", style=colors["muted"])
    if current:
        text.append(f" · {current.get('content', '')[:90]}", style=colors["text"])
    console.print(text)


def status_panel(console: Console, rows: Iterable[tuple[str, str]], title: str = "status", *, theme: str = "aurora") -> None:
    colors = palette(theme)
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style=colors["muted"], no_wrap=True)
    grid.add_column(style=colors["text"], overflow="fold")
    for key, value in rows:
        grid.add_row(key, Text(value))
    console.print(
        Panel(
            grid,
            title=f"[{colors['primary']}]✦ {title}[/]",
            border_style=colors["border"],
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def keys_table(console: Console, cfg: Config, states: list[Any], *, theme: str = "aurora") -> None:
    colors = palette(theme)
    table = Table(
        box=box.SIMPLE_HEAD,
        border_style=colors["border"],
        header_style=f"bold {colors['primary']}",
        expand=True,
        show_lines=False,
    )
    table.add_column("провайдер", ratio=3, overflow="fold")
    table.add_column("статус", ratio=1, no_wrap=True)
    table.add_column("ключ / ссылка", ratio=4, overflow="fold")
    for state in states:
        provider = state.provider
        if state.available:
            status = Text("✓ готов", style=colors["ok"])
        elif provider.local:
            status = Text("локальный", style=colors["muted"])
        else:
            status = Text("— нет ключа", style=colors["muted"])
        provider_cell = Text(provider.short, style=f"bold {colors['text']}")
        provider_cell.append("\n" + provider.free_note, style=colors["muted"])
        key_cell = Text()
        if provider.env_key:
            key_cell.append(provider.env_key, style=colors["muted"])
        elif provider.local:
            key_cell.append("без ключа", style=colors["muted"])
        if provider.signup_url:
            if key_cell:
                key_cell.append("\n")
            key_cell.append(provider.signup_url, style=f"link {colors['accent']}")
        table.add_row(provider_cell, status, key_cell)
    console.print(table)


def models_table(console: Console, provider_id: str, models: list[dict[str, Any]], *, theme: str = "aurora", limit: int = 30) -> None:
    colors = palette(theme)
    if limit <= 0:
        limit = len(models)
    table = Table(
        box=box.SIMPLE_HEAD,
        header_style=f"bold {colors['primary']}",
        border_style=colors["border"],
        expand=True,
    )
    table.add_column("#", justify="right", style=colors["muted"], width=3)
    table.add_column("модель", ratio=4, overflow="fold")
    table.add_column("ctx", justify="right", style=colors["muted"], width=7)
    table.add_column("free", width=4)
    table.add_column("заметка", ratio=2, style=colors["muted"], overflow="fold")
    for index, model in enumerate(models[:limit], 1):
        table.add_row(
            str(index),
            Text(str(model["id"]), style=colors["text"]),
            f"{int(model.get('ctx') or 0) // 1000}k" if model.get("ctx") else "—",
            Text("✓", style=colors["ok"]) if model.get("free") else Text("$", style=colors["warn"]),
            Text(str(model.get("note") or ""), style=colors["muted"]),
        )
    console.print(table)
    if len(models) > limit:
        console.print(Text(f"  … ещё {len(models) - limit}", style=colors["muted"]))


def help_table(console: Console, custom: list[Any] | None = None, *, theme: str = "aurora") -> None:
    from ..commands import BUILTIN_COMMANDS

    colors = palette(theme)
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {colors['primary']}", expand=True)
    table.add_column("команда", no_wrap=True, style=colors["accent"])
    table.add_column("что делает", overflow="fold", style=colors["text"])
    for name, _, description in BUILTIN_COMMANDS:
        table.add_row(name, description)
    if custom:
        table.add_section()
        for command in custom:
            table.add_row(f"/{command.name}", f"{command.description}  [{command.source}]")
    console.print(table)
    console.print(
        Text.from_markup(
            f"[{colors['muted']}]Подсказки: @path — приложить файл · !cmd — shell · "
            f"Ctrl+J — новая строка · Ctrl+C — отмена · Ctrl+D — выход[/]"
        )
    )


def session_table(console: Console, sessions: list[Any], *, theme: str = "aurora") -> None:
    colors = palette(theme)
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {colors['primary']}", expand=True)
    table.add_column("#", width=3, justify="right", style=colors["muted"])
    table.add_column("когда", width=12, style=colors["muted"])
    table.add_column("сессия", no_wrap=True, style=colors["text"])
    table.add_column("сообщений", width=9, justify="right", style=colors["muted"])
    table.add_column("заголовок", overflow="fold", style=colors["muted"])
    from ..sessions import relative_time

    for index, session in enumerate(sessions[:12], 1):
        table.add_row(
            str(index),
            relative_time(session.updated),
            session.id,
            str(len(session.messages)),
            session.title,
        )
    console.print(table)


def diff_preview(console: Console, diff: str, *, theme: str = "aurora") -> None:
    colors = palette(theme)
    text = Text()
    for line in diff.splitlines()[:80]:
        if line.startswith("+") and not line.startswith("+++"):
            text.append(line + "\n", style=colors["ok"])
        elif line.startswith("-") and not line.startswith("---"):
            text.append(line + "\n", style=colors["err"])
        elif line.startswith("@@"):
            text.append(line + "\n", style=colors["accent"])
        else:
            text.append(line + "\n", style=colors["muted"])
    console.print(Padding(text, (0, 0, 0, 2)))
