from __future__ import annotations

import asyncio
import io
import json

from rich.console import Console

from astra.agent import Agent, TurnStats
from astra.agents import AgentRegistry, discover as discover_agents
from astra.cli import _detect_command, main
from astra.commands import CommandRegistry, discover as discover_commands
from astra.config import Config
from astra.router import Router
from astra.sessions import Session
from astra.skills import SkillRegistry, discover as discover_skills
from astra.ui.app import AstraApp
from astra.ui.sink import RecordingSink


def _agent(cfg: Config, sink: RecordingSink) -> Agent:
    return Agent(
        cfg,
        Router(cfg),
        sink,
        Session.new(cfg.cwd, "mock", "mock"),
        cwd=cfg.cwd,
        skills=SkillRegistry(discover_skills(cfg.cwd, cfg.home)),
        agents=AgentRegistry(discover_agents(cfg.cwd, cfg.home)),
        commands=CommandRegistry(discover_commands(cfg.cwd, cfg.home)),
    )


def test_mock_agent_full_cycle(cfg: Config):
    sink = RecordingSink()
    agent = _agent(cfg, sink)
    stats = asyncio.run(agent.run("Создай hello.py и проверь его"))

    assert (cfg.cwd / "hello.py").is_file()
    content = (cfg.cwd / "hello.py").read_text(encoding="utf-8")
    assert "def greet" in content
    assert "Привет" in content
    assert "Готово" in sink.text
    assert stats.steps == 3
    assert stats.tools == 3
    assert stats.total_tokens > 0
    assert stats.provider == "mock"
    assert sink.stats is not None
    names = [event[1][0] for event in sink.events if event[0] == "tool_start"]
    assert names == ["todo_write", "write_file", "run_command"]
    assert any(event[0] == "tool_end" and event[1][1] is True for event in sink.events)

    roles = [message["role"] for message in agent.session.messages]
    assert roles[0] == "user"
    assert "tool" in roles
    assert agent.session.usage.steps == 3
    session_files = list((cfg.home / "sessions").glob("*.json"))
    assert session_files
    saved = json.loads(session_files[0].read_text(encoding="utf-8"))
    assert saved["messages"]


def test_agent_system_prompt_contains_skills(cfg: Config):
    skill_dir = cfg.cwd / ".claude" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo workflow for testing\n---\n\nDo demo things.",
        encoding="utf-8",
    )
    sink = RecordingSink()
    agent = _agent(cfg, sink)
    assert "demo" in agent._system
    assert "Demo workflow for testing" in agent._system
    assert "<project_context>" in agent._system
    assert "untrusted data" in agent._system


def test_agent_handles_bad_tool_arguments(cfg: Config):
    # Mock never emits bad args, so call the tool path directly.
    sink = RecordingSink()
    agent = _agent(cfg, sink)
    from astra.providers import ToolCall

    asyncio.run(
        agent._execute_tool(
            ToolCall(id="x", name="read_file", arguments="{oops"),
            TurnStats(),
        )
    )
    last = agent.session.messages[-1]
    assert last["role"] == "tool"
    assert last["content"].startswith("ERROR")


def test_app_renders_and_runs(cfg: Config):
    stream = io.StringIO()
    console = Console(file=stream, width=120, no_color=True, force_terminal=False)
    app = AstraApp(cfg, cfg.cwd, console=console, auto_approve=True, live_enabled=False)
    stats = asyncio.run(app.ask("Создай hello.py"))
    output = stream.getvalue()
    assert stats is not None
    assert "Astra" in output
    assert "готово" in output.lower()
    assert (cfg.cwd / "hello.py").exists()


def test_cli_pipe_mode(cfg: Config, monkeypatch, capsys):
    monkeypatch.setenv("ASTRA_HOME", str(cfg.home))
    code = main(
        [
            "-p",
            "Создай hello.py",
            "--model",
            "mock",
            "--yes",
            "--cwd",
            str(cfg.cwd),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Готово" in captured.out
    assert (cfg.cwd / "hello.py").exists()


def test_cli_detects_subcommands():
    assert _detect_command(["demo"])
    assert _detect_command(["--cwd", "C:/tmp", "commands"])
    assert _detect_command(["--model", "mock", "doctor"])
    assert _detect_command(["--help"])
    assert not _detect_command(["-p", "demo"])
    assert not _detect_command(["--model", "mock", "исправь тест"])
    assert not _detect_command([])


def test_cli_lists_custom_commands(cfg: Config, monkeypatch, capsys):
    monkeypatch.setenv("ASTRA_HOME", str(cfg.home))
    commands = cfg.cwd / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "deploy.md").write_text("---\ndescription: deploy it\n---\n\nDeploy.", encoding="utf-8")
    code = main(["commands", "--cwd", str(cfg.cwd)])
    captured = capsys.readouterr()
    assert code == 0
    assert "deploy" in captured.out
    assert "deploy it" in captured.out
