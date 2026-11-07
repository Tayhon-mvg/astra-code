"""Subagent definitions compatible with Claude Code's .claude/agents format."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter

MAX_AGENTS = 60

READ_ONLY_TOOLS = ["read_file", "list_dir", "glob", "grep", "fetch_url", "skill"]
VERIFY_TOOLS = READ_ONLY_TOOLS + ["run_command"]


@dataclass
class AgentDef:
    name: str
    description: str
    body: str
    source: str = "builtin"
    tools: list[str] = field(default_factory=list)
    model: str = ""
    path: Path | None = None

    def summary(self) -> str:
        return f"{self.name} — {self.description}" if self.description else self.name

    @property
    def read_only(self) -> bool:
        return bool(self.tools) and all(tool in READ_ONLY_TOOLS for tool in self.tools)


GENERAL = AgentDef(
    name="general",
    description="General-purpose agent for multi-step research or implementation subtasks.",
    body=(
        "You are a focused subagent inside Astra Code. Complete the delegated task end-to-end. "
        "Inspect files before changing them, keep edits scoped, verify your work, and return a "
        "compact report: what you did, what you found, and any remaining risk. "
        "You cannot talk to the end user; the parent agent will relay your result."
    ),
    source="builtin",
    tools=[],
)

EXPLORE = AgentDef(
    name="explore",
    description="Read-only codebase explorer: finds files, symbols, patterns and explains architecture.",
    body=(
        "You are a read-only exploration subagent. Locate relevant code and explain how it works. "
        "Use grep/glob/read_file aggressively, but never modify files or run mutating commands. "
        "Return: a short map of the relevant files, key functions with path:line references, "
        "and pitfalls. Be concise and factual."
    ),
    source="builtin",
    tools=list(READ_ONLY_TOOLS),
)

REVIEWER = AgentDef(
    name="reviewer",
    description="Code reviewer: looks for bugs, regressions, missing tests and risky assumptions.",
    body=(
        "You are a rigorous code reviewer. Inspect the diff or requested area, then report only "
        "real, actionable findings ordered by severity. For each finding give path:line, why it "
        "is wrong, and a concrete fix. Run targeted tests when useful. Do not rewrite the whole "
        "project; you are reviewing, not refactoring."
    ),
    source="builtin",
    tools=list(VERIFY_TOOLS),
)

BUILTIN_AGENTS = {agent.name: agent for agent in (GENERAL, EXPLORE, REVIEWER)}


def _scan(root: Path, source: str, found: dict[str, AgentDef]) -> None:
    if not root.is_dir():
        return
    try:
        files = sorted(root.glob("*.md"))
    except OSError:
        return
    for path in files[:MAX_AGENTS]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = frontmatter.parse(text)
        if not body.strip():
            continue
        name = (meta.get("name") or path.stem).strip().lower().replace(" ", "-")
        if not name:
            continue
        tools = frontmatter.as_list(meta.get("tools") or meta.get("allowed-tools"))
        found[name] = AgentDef(
            name=name,
            description=meta.get("description", "") or frontmatter.first_paragraph(body),
            body=body.strip(),
            source=source,
            tools=tools,
            model=(meta.get("model") or "").strip(),
            path=path,
        )


def discover(cwd: Path, home: Path) -> list[AgentDef]:
    found: dict[str, AgentDef] = dict(BUILTIN_AGENTS)
    # user first, project last: a project file overrides a user file, both override builtins
    for root in (
        home / ".astra" / "agents",
        home / ".claude" / "agents",
        home / ".codex" / "agents",
    ):
        _scan(root, "user", found)
    for root in (
        cwd / ".astra" / "agents",
        cwd / ".claude" / "agents",
        cwd / ".codex" / "agents",
    ):
        _scan(root, "project", found)
    return sorted(found.values(), key=lambda a: (a.source == "builtin", a.name))


class AgentRegistry:
    def __init__(self, agents: list[AgentDef] | None = None) -> None:
        self._agents = {agent.name: agent for agent in (agents or [])}

    def all(self) -> list[AgentDef]:
        return sorted(self._agents.values(), key=lambda a: (a.source == "builtin", a.name))

    def get(self, name: str | None) -> AgentDef | None:
        if not name:
            return self._agents.get("general")
        return self._agents.get(name.strip().lower())

    def __len__(self) -> int:
        return len(self._agents)

    def catalog_text(self, limit: int = 1200) -> str:
        lines = [f"- {agent.name}: {agent.description}" for agent in self.all()]
        text = "\n".join(lines)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def reload(self, cwd: Path, home: Path) -> "AgentRegistry":
        self._agents = {agent.name: agent for agent in discover(cwd, home)}
        return self
