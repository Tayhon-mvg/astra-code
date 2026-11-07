from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


from astra.config import Config
from astra.tools import ToolContext, ToolRegistry, _is_sensitive, _safe_env, redact_secrets


def registry(cfg: Config, approver=None) -> ToolRegistry:
    return ToolRegistry(ToolContext(cwd=cfg.cwd, cfg=cfg, approver=approver))


def run(reg: ToolRegistry, name: str, args: dict):
    return asyncio.run(reg.execute(name, args))


def test_write_read_edit(cfg: Config):
    reg = registry(cfg)
    out = run(reg, "write_file", {"path": "src/app.py", "content": "one\ntwo\n"})
    assert out.startswith("OK")
    assert (cfg.cwd / "src" / "app.py").read_text(encoding="utf-8") == "one\ntwo\n"

    read = run(reg, "read_file", {"path": "src/app.py"})
    assert "one" in read and "two" in read
    assert "строки 1-2" in read

    edited = run(
        reg,
        "edit_file",
        {"path": "src/app.py", "old_string": "two", "new_string": "three"},
    )
    assert "OK" in edited
    assert (cfg.cwd / "src" / "app.py").read_text(encoding="utf-8") == "one\nthree\n"


def test_edit_requires_unique_match(cfg: Config):
    reg = registry(cfg)
    run(reg, "write_file", {"path": "x.txt", "content": "a\na\n"})
    out = run(reg, "edit_file", {"path": "x.txt", "old_string": "a", "new_string": "b"})
    assert out.startswith("ERROR")
    assert "2 раз" in out
    out = run(reg, "edit_file", {"path": "x.txt", "old_string": "a", "new_string": "b", "replace_all": True})
    assert out.startswith("OK")
    assert (cfg.cwd / "x.txt").read_text(encoding="utf-8") == "b\nb\n"


def test_edit_preserves_crlf(cfg: Config):
    path = cfg.cwd / "win.txt"
    path.write_bytes(b"a\r\nb\r\n")
    reg = registry(cfg)
    out = run(reg, "edit_file", {"path": "win.txt", "old_string": "b", "new_string": "c"})
    assert out.startswith("OK")
    assert path.read_bytes() == b"a\r\nc\r\n"


def test_edit_missing_string(cfg: Config):
    reg = registry(cfg)
    run(reg, "write_file", {"path": "x.txt", "content": "hello\n"})
    out = run(reg, "edit_file", {"path": "x.txt", "old_string": "absent", "new_string": "x"})
    assert out.startswith("ERROR")
    assert "не найден" in out


def test_glob_and_grep(cfg: Config):
    (cfg.cwd / "src").mkdir()
    (cfg.cwd / "src" / "a.py").write_text("needle = 1\n", encoding="utf-8")
    (cfg.cwd / "src" / "b.txt").write_text("no match\n", encoding="utf-8")
    reg = registry(cfg)
    found = run(reg, "glob", {"pattern": "**/*.py"})
    assert "a.py" in found
    assert "b.txt" not in found
    hits = run(reg, "grep", {"pattern": "needle", "include": "*.py"})
    assert "a.py:1:" in hits
    assert "b.txt" not in hits


def test_list_dir_ignores_build(cfg: Config):
    (cfg.cwd / "node_modules").mkdir()
    (cfg.cwd / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    (cfg.cwd / "main.py").write_text("print(1)", encoding="utf-8")
    reg = registry(cfg)
    tree = run(reg, "list_dir", {})
    assert "main.py" in tree
    assert "node_modules" not in tree


def test_outside_workspace_needs_approval(cfg: Config):
    cfg.data["approval"] = "ask"
    outside = cfg.cwd.parent / "secret.txt"
    outside.write_text("top secret", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    async def deny(tool: str, summary: str, detail: str) -> str:
        calls.append((tool, summary))
        return "deny"

    reg = registry(cfg, deny)
    out = run(reg, "read_file", {"path": str(outside)})
    assert out.startswith("ERROR")
    assert calls and calls[0][0] == "outside_workspace"
    assert "top secret" not in out


def test_auth_file_is_protected(cfg: Config):
    cfg.set_key("openrouter", "sk-or-secret")
    reg = registry(cfg)
    out = run(reg, "read_file", {"path": str(cfg.home / "auth.toml")})
    assert out.startswith("ERROR")
    assert "защищён" in out


def test_approval_always_is_remembered(cfg: Config):
    cfg.data["approval"] = "ask"
    calls = 0

    async def always(tool: str, summary: str, detail: str) -> str:
        nonlocal calls
        calls += 1
        return "always"

    reg = registry(cfg, always)
    run(reg, "write_file", {"path": "a.txt", "content": "1"})
    run(reg, "write_file", {"path": "a.txt", "content": "2"})
    assert calls == 1


def test_readonly_blocks_writes(cfg: Config):
    cfg.data["approval"] = "readonly"
    reg = registry(cfg)
    out = run(reg, "write_file", {"path": "a.txt", "content": "1"})
    assert out.startswith("ERROR")
    assert not (cfg.cwd / "a.txt").exists()


def test_auto_commands_skip_prompt(cfg: Config):
    cfg.data["approval"] = "ask"
    cfg.data["auto_commands"] = True

    async def fail(tool: str, summary: str, detail: str) -> str:
        raise AssertionError("approval must not be requested for a safe command")

    reg = registry(cfg, fail)
    out = run(reg, "run_command", {"command": "echo auto-ok"})
    assert out.startswith("OK")
    assert "auto-ok" in out


def test_dangerous_command_still_asks(cfg: Config):
    cfg.data["approval"] = "ask"
    cfg.data["auto_commands"] = True
    calls: list[str] = []

    async def deny(tool: str, summary: str, detail: str) -> str:
        calls.append(summary)
        return "deny"

    reg = registry(cfg, deny)
    out = run(reg, "run_command", {"command": "git reset --hard"})
    assert out.startswith("ERROR")
    assert calls and "git reset --hard" in calls[0]


def test_ask_commands_flag_disables_auto(cfg: Config):
    cfg.data["approval"] = "ask"
    cfg.data["auto_commands"] = False

    async def deny(tool: str, summary: str, detail: str) -> str:
        return "deny"

    reg = registry(cfg, deny)
    out = run(reg, "run_command", {"command": "echo blocked"})
    assert out.startswith("ERROR")
    assert "отклонил" in out


def test_readonly_blocks_commands(cfg: Config):
    cfg.data["approval"] = "readonly"
    cfg.data["auto_commands"] = True
    reg = registry(cfg)
    out = run(reg, "run_command", {"command": "echo nope"})
    assert out.startswith("ERROR")
    assert "readonly" in out


def test_web_search_tool(cfg: Config, monkeypatch):
    from astra.search import SearchResult

    async def fake_search(query: str, max_results: int = 5):
        assert query == "example"
        return [
            SearchResult("Example", "https://example.com", "Snippet", "bing"),
            SearchResult("Second", "https://example.org", "Other", "bing"),
        ]

    monkeypatch.setattr("astra.tools.search_web", fake_search)
    reg = registry(cfg)
    out = run(reg, "web_search", {"query": "example", "max_results": 2})
    assert "https://example.com" in out
    assert "https://example.org" in out
    assert "Snippet" in out


def test_run_command(cfg: Config):
    reg = registry(cfg)
    out = run(reg, "run_command", {"command": "echo astra-ok"})
    assert out.startswith("OK")
    assert "astra-ok" in out
    assert "exit_code=0" in out


def test_run_command_nonzero(cfg: Config):
    reg = registry(cfg)
    prefix = "& " if os.name == "nt" else ""
    command = f'{prefix}"{sys.executable}" -c "import sys; sys.exit(3)"'
    out = run(reg, "run_command", {"command": command})
    assert out.startswith("FAILED")
    assert "exit_code=3" in out


def test_todo_write(cfg: Config):
    reg = registry(cfg)
    out = run(
        reg,
        "todo_write",
        {"todos": [{"content": "one", "status": "completed"}, {"content": "two", "status": "pending"}]},
    )
    assert "1/2" in out
    assert reg.ctx.todos[0]["status"] == "completed"


def test_unknown_tool(cfg: Config):
    reg = registry(cfg)
    out = run(reg, "nope", {})
    assert out.startswith("ERROR")
    assert "неизвестный" in out


def test_fetch_url_blocks_localhost(cfg: Config):
    reg = registry(cfg)
    out = run(reg, "fetch_url", {"url": "http://127.0.0.1:9/secret"})
    assert out.startswith("ERROR")
    assert "приватные" in out or "локальные" in out


def test_system_roots_are_blocked(cfg: Config):
    system_path = "C:\\Windows" if os.name == "nt" else "/etc"
    cfg.data["allow_outside"] = True
    cfg.data["approval"] = "auto"
    reg = registry(cfg)

    for tool, args in (
        ("read_file", {"path": system_path}),
        ("write_file", {"path": system_path + "\\astra-test.txt" if os.name == "nt" else "/etc/astra-test.txt", "content": "x"}),
        ("list_dir", {"path": system_path}),
        ("glob", {"pattern": "*.txt", "path": system_path}),
        ("grep", {"pattern": "x", "path": system_path}),
    ):
        out = run(reg, tool, args)
        assert out.startswith("ERROR"), (tool, out)
        assert "системная" in out, (tool, out)


def test_system_root_command_blocked(cfg: Config):
    system_path = "C:\\Windows" if os.name == "nt" else "/etc"
    reg = registry(cfg)
    out = run(reg, "run_command", {"command": f"echo {system_path}"})
    assert out.startswith("ERROR")
    assert "системная" in out


def test_system_env_wsl_and_drive_root_blocked(cfg: Config):
    reg = registry(cfg)
    for command in ("echo %SystemRoot%", "echo $env:SystemRoot", "wsl cat /etc/passwd"):
        out = run(reg, "run_command", {"command": command})
        assert out.startswith("ERROR"), (command, out)
        assert "системная" in out
    out = run(reg, "run_command", {"command": "dir C:\\" if os.name == "nt" else "ls /"})
    assert out.startswith("ERROR")


def test_extended_windows_system_path_blocked(cfg: Config):
    if os.name != "nt":
        return
    reg = registry(cfg)
    out = run(reg, "read_file", {"path": "\\\\?\\C:\\Windows\\win.ini"})
    assert out.startswith("ERROR")
    assert "системная" in out or "device" in out


def test_safe_env_strips_secrets(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-value")
    monkeypatch.setenv("GH_TOKEN", "dummy-value")
    monkeypatch.setenv("PATH", "C:\\Windows")
    env = _safe_env()
    assert "OPENROUTER_API_KEY" not in env
    assert "GH_TOKEN" not in env
    assert env.get("PATH") == "C:\\Windows"
    assert env.get("ASTRA_AGENT") == "1"


def test_redact_secrets():
    secret = "sk-" + "or-" + "1234567890abcdefghij"
    text = f"OPENROUTER_API_KEY={secret}\nAuthorization: Bearer {secret}\nvalue={secret}"
    hidden = redact_secrets(text)
    assert secret not in hidden
    assert "sk-***" in hidden
    assert "Bearer ***" in hidden


def test_sensitive_file_names():
    assert _is_sensitive(Path(".env"))
    assert _is_sensitive(Path("id_rsa"))
    assert _is_sensitive(Path("cert.pem"))
    assert not _is_sensitive(Path("src/app.py"))


def test_sensitive_file_requires_approval_and_redacts(cfg: Config, tmp_path):
    cfg.data["approval"] = "ask"
    secret = "sk-" + "or-" + "1234567890abcdefghij"
    cfg.cwd.joinpath(".env").write_text(
        f"OPENROUTER_API_KEY={secret}\n", encoding="utf-8"
    )
    calls: list[str] = []

    async def deny(tool: str, summary: str, detail: str) -> str:
        calls.append(tool)
        return "deny"

    reg = registry(cfg, deny)
    out = run(reg, "read_file", {"path": ".env"})
    assert out.startswith("ERROR")
    assert calls == ["sensitive_read"]

    async def allow(tool: str, summary: str, detail: str) -> str:
        return "allow"

    reg = registry(cfg, allow)
    out = run(reg, "read_file", {"path": ".env"})
    assert "OPENROUTER_API_KEY=***" in out
    assert secret not in out
