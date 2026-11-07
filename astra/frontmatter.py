"""Tiny YAML-frontmatter reader (no PyYAML dependency)."""

from __future__ import annotations

import re
from typing import Any

_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")


def parse(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter, body). Frontmatter is a flat string→string map."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = -1
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            end = index
            break
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    key = ""
    buffer: list[str] = []
    for raw in lines[1:end]:
        line = raw.rstrip()
        match = _KEY_RE.match(line)
        if match:
            if key:
                meta[key] = _join(buffer)
            key = match.group(1).strip().lower()
            value = match.group(2).strip()
            buffer = [value] if value else []
        elif key and (line.startswith(" ") or line.startswith("\t")):
            buffer.append(line.strip())
        elif key and not line.strip():
            buffer.append("")
    if key:
        meta[key] = _join(buffer)
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


def _join(lines: list[str]) -> str:
    text = " ".join(part for part in lines if part).strip()
    return text.strip('"').strip("'")


def as_list(value: str | None) -> list[str]:
    if not value:
        return []
    cleaned = value.strip().strip("[]")
    parts = re.split(r"[,\n]", cleaned)
    return [part.strip().strip('"').strip("'") for part in parts if part.strip()]


def first_paragraph(body: str, limit: int = 160) -> str:
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith(("```", "|", "---")):
            return line[:limit]
    return ""


def expand_arguments(template: str, arguments: str, directory: str = "") -> str:
    text = template.replace("$ARGUMENTS", arguments)
    text = text.replace("${CLAUDE_SKILL_DIR}", directory).replace("${ASTRA_SKILL_DIR}", directory)
    if arguments:
        parts = arguments.split()
        for index in range(9, 0, -1):
            placeholder = f"${index}"
            if placeholder in text:
                text = text.replace(placeholder, parts[index - 1] if index <= len(parts) else "")
    return text


def make_meta(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value}
