"""Astra colour themes and Rich console factory."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

PALETTES: dict[str, dict[str, str]] = {
    "aurora": {
        "primary": "#A78BFA",
        "accent": "#22D3EE",
        "ok": "#34D399",
        "warn": "#FBBF24",
        "err": "#FB7185",
        "muted": "#64748B",
        "text": "#E2E8F0",
        "tool": "#C4B5FD",
        "agent": "#F472B6",
        "border": "#4C1D95",
        "code": "#0F172A",
    },
    "mono": {
        "primary": "white",
        "accent": "white",
        "ok": "green",
        "warn": "yellow",
        "err": "red",
        "muted": "bright_black",
        "text": "white",
        "tool": "white",
        "agent": "bright_black",
        "border": "bright_black",
        "code": "black",
    },
}


def palette(theme: str = "aurora") -> dict[str, str]:
    return PALETTES.get(theme, PALETTES["aurora"])


def build_theme(theme: str = "aurora") -> Theme:
    colors = palette(theme)
    return Theme(
        {
            "astra.primary": f"bold {colors['primary']}",
            "astra.accent": colors["accent"],
            "astra.ok": colors["ok"],
            "astra.warn": colors["warn"],
            "astra.err": colors["err"],
            "astra.muted": colors["muted"],
            "astra.text": colors["text"],
            "astra.tool": colors["tool"],
            "astra.agent": colors["agent"],
            "astra.border": colors["border"],
            "astra.logo": f"bold {colors['primary']}",
            "astra.logo2": f"bold {colors['accent']}",
        }
    )


def make_console(theme: str = "aurora", *, no_color: bool = False, stderr: bool = False, width: int | None = None) -> Console:
    return Console(
        theme=build_theme(theme),
        no_color=no_color,
        stderr=stderr,
        width=width,
        highlight=False,
        soft_wrap=False,
        emoji=False,
    )
