"""Claude Code / Codex skill discovery.

Supported layouts (project first, then user, then plugins):
  .claude/skills/<name>/SKILL.md      ~/.claude/skills/<name>/SKILL.md
  .astra/skills/<name>/SKILL.md       ~/.astra/skills/<name>/SKILL.md
  .codex/skills/<name>/SKILL.md       ~/.codex/skills/<name>/SKILL.md
  .claude/plugins/**/skills/**/SKILL.md
  .astra/skills/<name>.md             (flat variant)

Frontmatter: name, description, allowed-tools, argument-hint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter

MAX_SKILLS = 300
_NAME_RE = re.compile(r"[^a-z0-9_-]+")


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    source: str = "project"
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: str = ""

    @property
    def directory(self) -> str:
        return str(self.path.parent)

    def expand(self, arguments: str = "") -> str:
        return frontmatter.expand_arguments(self.body, arguments, self.directory)

    def summary(self) -> str:
        return f"{self.name} — {self.description}" if self.description else self.name


def _normalise_name(raw: str, fallback: str) -> str:
    candidate = (raw or fallback).strip().lower().replace(" ", "-")
    candidate = _NAME_RE.sub("-", candidate).strip("-")
    return candidate or "skill"


def _read_skill(path: Path, source: str, fallback_name: str) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, body = frontmatter.parse(text)
    if not body.strip():
        return None
    name = _normalise_name(meta.get("name", ""), fallback_name)
    description = meta.get("description", "") or frontmatter.first_paragraph(body)
    return Skill(
        name=name,
        description=description.strip(),
        body=body.strip(),
        path=path,
        source=source,
        allowed_tools=frontmatter.as_list(meta.get("allowed-tools") or meta.get("allowed_tools")),
        argument_hint=meta.get("argument-hint", ""),
    )


def _scan_root(root: Path, source: str, found: dict[str, Skill]) -> None:
    if not root.is_dir():
        return
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if len(found) >= MAX_SKILLS:
            return
        try:
            if entry.is_dir():
                skill_file = entry / "SKILL.md"
                if not skill_file.is_file():
                    skill_file = entry / "skill.md"
                if skill_file.is_file():
                    skill = _read_skill(skill_file, source, entry.name)
                    if skill and skill.name not in found:
                        found[skill.name] = skill
            elif entry.suffix.lower() == ".md" and entry.name.lower() != "readme.md":
                skill = _read_skill(entry, source, entry.stem)
                if skill and skill.name not in found:
                    found[skill.name] = skill
        except OSError:
            continue


def _scan_plugins(root: Path, found: dict[str, Skill]) -> None:
    if not root.is_dir():
        return
    count = 0
    try:
        for path in root.rglob("SKILL.md"):
            if len(found) >= MAX_SKILLS or count >= MAX_SKILLS:
                return
            count += 1
            parts = {part.lower() for part in path.parts}
            if "node_modules" in parts or ".git" in parts:
                continue
            skill = _read_skill(path, "plugin", path.parent.name)
            if skill and skill.name not in found:
                found[skill.name] = skill
    except OSError:
        return


def discover(cwd: Path, home: Path) -> list[Skill]:
    found: dict[str, Skill] = {}
    for root in (
        cwd / ".astra" / "skills",
        cwd / ".claude" / "skills",
        cwd / ".codex" / "skills",
    ):
        _scan_root(root, "project", found)
    for root in (
        home / ".astra" / "skills",
        home / ".claude" / "skills",
        home / ".codex" / "skills",
    ):
        _scan_root(root, "user", found)
    for root in (cwd / ".claude" / "plugins", home / ".claude" / "plugins"):
        _scan_plugins(root, found)
    return sorted(found.values(), key=lambda s: (s.source == "user", s.name))


class SkillRegistry:
    def __init__(self, skills: list[Skill] | None = None) -> None:
        self._skills = {skill.name: skill for skill in (skills or [])}

    def all(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill | None:
        if not name:
            return None
        return self._skills.get(name.strip().lower())

    def __len__(self) -> int:
        return len(self._skills)

    def catalog_text(self, limit: int = 1500) -> str:
        lines = []
        for skill in self.all():
            line = f"- {skill.name}: {skill.description}"
            if f"({skill.source})" not in line:
                line += f"  [{skill.source}]"
            lines.append(line)
        text = "\n".join(lines)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def reload(self, cwd: Path, home: Path) -> "SkillRegistry":
        self._skills = {skill.name: skill for skill in discover(cwd, home)}
        return self
