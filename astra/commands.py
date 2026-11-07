"""Custom slash commands for Claude Code and Codex, plus built-in command metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter

MAX_COMMANDS = 120
MAX_ATTACH_BYTES = 80_000

BUILTIN_COMMANDS: list[tuple[str, str, str]] = [
    ("/help", "", "все команды и горячие клавиши"),
    ("/model", "[имя]", "выбрать модель (Astra Coder/Reasoner, provider:model)"),
    ("/models", "[--refresh]", "список бесплатных моделей"),
    ("/provider", "[id]", "провайдер и статус ключа"),
    ("/keys", "", "ключи, лимиты и ссылки на бесплатные тарифы"),
    ("/setup", "[provider]", "мастер добавления ключа"),
    ("/skills", "[name]", "скиллы Claude Code/Codex; /skills name — загрузить"),
    ("/agents", "", "доступные субагенты"),
    ("/commands", "", "кастомные команды проекта"),
    ("/init", "", "создать ASTRA.md с контекстом проекта"),
    ("/compact", "", "сжать историю и освободить контекст"),
    ("/clear", "", "очистить текущую сессию"),
    ("/cost", "", "токены и время текущей сессии"),
    ("/resume", "[id]", "продолжить сессию"),
    ("/sessions", "", "список сессий"),
    ("/theme", "[aurora|mono]", "переключить тему"),
    ("/reasoning", "", "показывать/скрыть reasoning модели"),
    ("/approval", "[ask|auto|readonly]", "режим подтверждений"),
    ("/status", "", "модель, папка, контекст, память"),
    ("/doctor", "", "диагностика окружения"),
    ("/quit", "", "выйти"),
]


@dataclass
class CustomCommand:
    name: str
    description: str
    body: str
    source: str = "project"
    arg_hint: str = ""
    allowed_tools: list[str] | None = None
    directory: str = ""

    def expand(self, arguments: str = "") -> str:
        return frontmatter.expand_arguments(self.body, arguments, self.directory)


def _scan_markdown_root(root: Path, source: str, found: dict[str, CustomCommand], prefix: str = "") -> None:
    if not root.is_dir():
        return
    try:
        files = sorted(root.rglob("*.md"))
    except OSError:
        return
    for path in files[:MAX_COMMANDS]:
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = frontmatter.parse(text)
        if not body.strip():
            continue
        relative = path.relative_to(root).with_suffix("")
        name = ":".join(relative.parts)
        if prefix:
            name = f"{prefix}:{name}"
        name = name.replace(" ", "-").lower()
        if name in found:
            continue
        found[name] = _command(name, meta, body, source, str(path.parent))


def _command(
    name: str,
    meta: dict[str, str],
    body: str,
    source: str,
    directory: str = "",
) -> CustomCommand:
    command = CustomCommand(
        name=name,
        description=meta.get("description", "") or frontmatter.first_paragraph(body),
        body=body.strip(),
        source=source,
        arg_hint=meta.get("argument-hint", ""),
        allowed_tools=frontmatter.as_list(meta.get("allowed-tools")) or None,
        directory=directory,
    )
    return command


def discover(cwd: Path, home: Path) -> list[CustomCommand]:
    found: dict[str, CustomCommand] = {}
    for root in (cwd / ".astra" / "commands", cwd / ".claude" / "commands"):
        _scan_markdown_root(root, "project", found)
    for root in (home / ".astra" / "commands", home / ".claude" / "commands"):
        _scan_markdown_root(root, "user", found)
    for root, source in ((cwd / ".codex" / "prompts", "project"), (home / ".codex" / "prompts", "user")):
        if root.is_dir():
            try:
                for path in sorted(root.glob("*.md"))[:MAX_COMMANDS]:
                    try:
                        meta, body = frontmatter.parse(path.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        continue
                    name = path.stem.lower()
                    if body.strip() and name not in found:
                        found[name] = _command(name, meta, body, source, str(path.parent))
            except OSError:
                pass
    return sorted(found.values(), key=lambda c: c.name)


class CommandRegistry:
    def __init__(self, commands: list[CustomCommand] | None = None) -> None:
        self._commands = {command.name: command for command in (commands or [])}

    def all(self) -> list[CustomCommand]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def get(self, name: str) -> CustomCommand | None:
        return self._commands.get(name.strip().lstrip("/").lower())

    def __len__(self) -> int:
        return len(self._commands)

    def reload(self, cwd: Path, home: Path) -> "CommandRegistry":
        self._commands = {command.name: command for command in discover(cwd, home)}
        return self


_MENTION_RE = re.compile(r"(?<![\w@])@([\w./\\-]+)")


def expand_file_mentions(text: str, cwd: Path) -> tuple[str, list[str]]:
    """Attach files referenced as @path (max 8 files, 80 KB total)."""
    mentions = list(dict.fromkeys(_MENTION_RE.findall(text)))
    if not mentions:
        return text, []
    blocks: list[str] = []
    used: list[str] = []
    total = 0
    for raw in mentions[:8]:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            candidate = candidate.resolve()
            if not candidate.is_file():
                continue
            if not candidate.is_relative_to(cwd):
                continue
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        if total + len(content) > MAX_ATTACH_BYTES:
            content = content[: max(0, MAX_ATTACH_BYTES - total)]
            content += "\n… (файл обрезан)"
        total += len(content)
        rel = candidate.relative_to(cwd).as_posix()
        blocks.append(f'<file path="{rel}">\n{content}\n</file>')
        used.append(rel)
        if total >= MAX_ATTACH_BYTES:
            break
    if not blocks:
        return text, []
    attachment = "\n\n<attached_files>\n" + "\n".join(blocks) + "\n</attached_files>"
    return text + attachment, used
