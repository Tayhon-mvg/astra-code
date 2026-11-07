"""UI sinks: interactive Rich console, quiet subagents, plain stdout."""

from __future__ import annotations

import sys
from typing import Any, Callable, Awaitable

from rich.console import Console
from rich.text import Text

from ..agent import Sink, TurnStats
from ..config import Config
from . import render
from .theme import palette


class ConsoleSink(Sink):
    def __init__(
        self,
        console: Console,
        cfg: Config,
        *,
        theme: str = "aurora",
        approve: Callable[[str, str, str], Awaitable[str]] | None = None,
        live_enabled: bool | None = None,
        quiet_tools: bool = False,
    ) -> None:
        self.console = console
        self.cfg = cfg
        self.theme = theme
        self._approve = approve
        self.live_enabled = live_enabled
        self.quiet_tools = quiet_tools
        self.stream = render.StreamRenderer(
            console,
            theme=theme,
            show_reasoning=bool(cfg.get("show_reasoning", False)),
            live_enabled=live_enabled,
        )
        self._open_tool: str | None = None
        self._block = False
        self._blocks = 0
        self.current_label = ""

    # ------------------------------------------------------------------ events
    def turn_start(self, label: str) -> None:
        self.current_label = label
        self._blocks = 0
        self.stream = render.StreamRenderer(
            self.console,
            label,
            theme=self.theme,
            show_reasoning=bool(self.cfg.get("show_reasoning", False)),
            live_enabled=self.live_enabled,
            show_header=True,
        )
        self.stream.begin()

    def _close_stream(self) -> None:
        printed = bool(self.stream.buffer)
        self.stream.finish()
        if printed:
            self._blocks += 1
        self.stream = render.StreamRenderer(
            self.console,
            self.current_label,
            theme=self.theme,
            show_reasoning=bool(self.cfg.get("show_reasoning", False)),
            live_enabled=self.live_enabled,
            show_header=self._blocks == 0,
        )

    def delta(self, text: str) -> None:
        self.stream.feed(text)

    def reasoning(self, text: str) -> None:
        self.stream.add_reasoning(text)

    def notice(self, text: str, level: str = "info") -> None:
        self._close_stream()
        render.notice(self.console, text, level, theme=self.theme)

    def tool_start(self, name: str, args: dict[str, Any], summary: str) -> None:
        self._close_stream()
        self._open_tool = name
        self._block = name in {"run_command", "task", "skill"}
        if self.quiet_tools or name == "todo_write":
            return
        if self._block:
            render.tool_block_start(self.console, summary or name, theme=self.theme)
        else:
            render.tool_start(self.console, name, summary, theme=self.theme)

    def tool_end(self, name: str, result: str, ok: bool, seconds: float) -> None:
        if self.quiet_tools or name == "todo_write":
            return
        if self._block:
            render.tool_block_end(self.console, result, ok, seconds, theme=self.theme)
        else:
            render.tool_end(self.console, result, ok, seconds, theme=self.theme)
        self._open_tool = None

    def todos(self, todos: list[dict[str, str]]) -> None:
        if not self.quiet_tools:
            render.todo_line(self.console, todos, theme=self.theme)

    def turn_end(self, stats: TurnStats) -> None:
        self.stream.finish()
        if self.quiet_tools:
            return
        render.footer(
            self.console,
            steps=stats.steps,
            prompt_tokens=stats.prompt_tokens,
            completion_tokens=stats.completion_tokens,
            seconds=stats.seconds,
            fallbacks=stats.fallbacks,
            tools=stats.tools,
            cancelled=stats.cancelled and stats.steps == 0,
            theme=self.theme,
        )

    async def approve(self, tool: str, summary: str, detail: str) -> str:
        if self._approve is None:
            return "deny"
        self._close_stream()
        return await self._approve(tool, summary, detail)

    def child(self, name: str) -> Sink:
        return SubagentSink(self, name)


class SubagentSink(Sink):
    def __init__(self, parent: ConsoleSink, name: str, depth: int = 1) -> None:
        self.parent = parent
        self.name = name
        self.depth = depth
        self.console = parent.console
        self.theme = parent.theme
        self.prefix = "  " + "│ " * depth

    def _print(self, text: Text) -> None:
        self.console.print(text)

    def notice(self, text: str, level: str = "info") -> None:
        colors = palette(self.theme)
        line = Text(self.prefix, style=colors["agent"])
        line.append(f"{self.name} ", style=f"bold {colors['agent']}")
        line.append(text, style=colors["muted"])
        self._print(line)

    def tool_start(self, name: str, args: dict[str, Any], summary: str) -> None:
        colors = palette(self.theme)
        self.parent.stream.finish()
        line = Text(self.prefix + "⚙ ", style=colors["agent"])
        line.append(name, style=f"bold {colors['agent']}")
        if summary:
            line.append("  " + summary, style=colors["muted"])
        self._print(line)

    def tool_end(self, name: str, result: str, ok: bool, seconds: float) -> None:
        colors = palette(self.theme)
        line = Text(self.prefix + ("✓ " if ok else "✗ "), style=colors["ok"] if ok else colors["err"])
        line.append(render.result_brief(result), style=colors["muted"])
        self._print(line)

    def delta(self, text: str) -> None:
        # Subagent streaming is intentionally compact; its report is returned to the parent.
        pass

    def turn_end(self, stats: TurnStats) -> None:
        pass

    async def approve(self, tool: str, summary: str, detail: str) -> str:
        return await self.parent.approve(tool, summary, detail)

    def child(self, name: str) -> Sink:
        return SubagentSink(self.parent, name, self.depth + 1)


class PlainSink(Sink):
    """Clean stdout for `astra -p`: text to stdout, diagnostics to stderr."""

    def __init__(self, console: Console, *, auto_approve: bool = False, quiet: bool = False) -> None:
        self.console = console
        self.auto = auto_approve
        self.quiet = quiet
        self._wrote = False
        self._last_char = ""

    def _separate(self) -> None:
        if self._wrote and self._last_char != "\n":
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._last_char = "\n"

    def delta(self, text: str) -> None:
        if not text:
            return
        sys.stdout.write(text)
        sys.stdout.flush()
        self._wrote = True
        self._last_char = text[-1]

    def notice(self, text: str, level: str = "info") -> None:
        if not self.quiet:
            self.console.print(Text(f"· {text}", style="dim"))

    def tool_start(self, name: str, args: dict[str, Any], summary: str) -> None:
        self._separate()
        if not self.quiet:
            self.console.print(Text(f"⚙ {name} {summary}".rstrip(), style="dim"))

    def tool_end(self, name: str, result: str, ok: bool, seconds: float) -> None:
        if not self.quiet:
            mark = "✓" if ok else "✗"
            self.console.print(Text(f"  {mark} {render.result_brief(result)}".rstrip(), style="dim"))

    def turn_end(self, stats: TurnStats) -> None:
        self._separate()
        if not self.quiet:
            self.console.print(
                Text(
                    f"· {stats.steps} шагов · {stats.total_tokens / 1000:.1f}k токенов · "
                    f"{stats.seconds:.1f}s · $0",
                    style="dim",
                )
            )

    async def approve(self, tool: str, summary: str, detail: str) -> str:
        if self.auto:
            return "allow"
        self.console.print(Text(f"✗ {tool}: требуется подтверждение (запустите с --yes)", style="red"))
        return "deny"


class RecordingSink(Sink):
    """For tests: captures every event."""

    def __init__(self, *, auto_approve: bool = True) -> None:
        self.events: list[tuple[str, Any]] = []
        self.text = ""
        self.auto = auto_approve
        self.stats: TurnStats | None = None

    def delta(self, text: str) -> None:
        self.text += text
        self.events.append(("delta", text))

    def notice(self, text: str, level: str = "info") -> None:
        self.events.append(("notice", text))

    def tool_start(self, name: str, args: dict[str, Any], summary: str) -> None:
        self.events.append(("tool_start", (name, summary)))

    def tool_end(self, name: str, result: str, ok: bool, seconds: float) -> None:
        self.events.append(("tool_end", (name, ok, result[:200])))

    def turn_end(self, stats: TurnStats) -> None:
        self.stats = stats
        self.events.append(("turn_end", stats))

    async def approve(self, tool: str, summary: str, detail: str) -> str:
        self.events.append(("approve", (tool, summary)))
        return "allow" if self.auto else "deny"
