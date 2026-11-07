from __future__ import annotations

from pathlib import Path

from astra.agents import AgentRegistry, discover as discover_agents
from astra.commands import CommandRegistry, discover as discover_commands, expand_file_mentions
from astra.config import Config
from astra.skills import SkillRegistry, discover as discover_skills


def _skill(path: Path, name: str, description: str, body: str = "# Steps\n\nDo it.") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nallowed-tools: Read, Grep\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_skill_discovery_and_precedence(cfg: Config):
    _skill(cfg.cwd / ".claude" / "skills" / "review", "review", "project review")
    _skill(cfg.home / ".claude" / "skills" / "review", "review", "user review")
    _skill(cfg.cwd / ".codex" / "skills" / "deploy", "deploy", "deploy app")
    _skill(cfg.home / ".claude" / "plugins" / "acme" / "skills" / "pdf", "pdf", "read pdfs")

    skills = discover_skills(cfg.cwd, cfg.home)
    by_name = {skill.name: skill for skill in skills}
    assert by_name["review"].source == "project"
    assert by_name["review"].description == "project review"
    assert by_name["deploy"].source == "project"
    assert by_name["pdf"].source == "plugin"
    assert by_name["review"].allowed_tools == ["Read", "Grep"]


def test_skill_expand_arguments(cfg: Config):
    _skill(
        cfg.cwd / ".astra" / "skills" / "release",
        "release",
        "release notes",
        "Write notes for $ARGUMENTS in ${ASTRA_SKILL_DIR}",
    )
    registry = SkillRegistry(discover_skills(cfg.cwd, cfg.home))
    skill = registry.get("release")
    assert skill is not None
    expanded = skill.expand("v1.2")
    assert "v1.2" in expanded
    assert "skills" in expanded.lower()


def test_flat_skill_file(cfg: Config):
    root = cfg.cwd / ".astra" / "skills"
    root.mkdir(parents=True)
    (root / "quick.md").write_text("---\ndescription: quick one\n---\n\nbody", encoding="utf-8")
    skills = discover_skills(cfg.cwd, cfg.home)
    assert any(skill.name == "quick" for skill in skills)


def test_agent_discovery_and_project_override(cfg: Config):
    agents_dir = cfg.cwd / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "explore.md").write_text(
        "---\nname: explore\ndescription: custom explorer\ntools: read_file, grep\nmodel: astra-reasoner\n---\n\nRead only.\n",
        encoding="utf-8",
    )
    registry = AgentRegistry(discover_agents(cfg.cwd, cfg.home))
    explore = registry.get("explore")
    assert explore is not None
    assert explore.source == "project"
    assert explore.description == "custom explorer"
    assert explore.tools == ["read_file", "grep"]
    assert explore.model == "astra-reasoner"
    assert registry.get("general") is not None


def test_command_discovery_and_expansion(cfg: Config):
    commands = cfg.cwd / ".claude" / "commands" / "db"
    commands.mkdir(parents=True)
    (commands / "migrate.md").write_text(
        "---\ndescription: create migration\nargument-hint: <name>\n---\n\nCreate migration for $1. Args: $ARGUMENTS\n",
        encoding="utf-8",
    )
    prompts = cfg.cwd / ".codex" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "review.md").write_text("Review the diff.", encoding="utf-8")

    registry = CommandRegistry(discover_commands(cfg.cwd, cfg.home))
    migrate = registry.get("db:migrate")
    assert migrate is not None
    assert migrate.description == "create migration"
    expanded = migrate.expand("users")
    assert "Create migration for users" in expanded
    assert "Args: users" in expanded
    assert registry.get("review") is not None


def test_expand_file_mentions(cfg: Config):
    (cfg.cwd / "a.py").write_text("print('a')\n", encoding="utf-8")
    text, used = expand_file_mentions("look at @a.py please", cfg.cwd)
    assert used == ["a.py"]
    assert "print('a')" in text
    assert "<attached_files>" in text


def test_expand_file_mentions_ignores_missing_and_outside(cfg: Config):
    outside = cfg.cwd.parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    text, used = expand_file_mentions("check @missing.py and @../secret.txt", cfg.cwd)
    assert used == []
    assert text == "check @missing.py and @../secret.txt"
