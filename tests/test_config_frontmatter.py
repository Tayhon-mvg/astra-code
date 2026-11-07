from __future__ import annotations

import tomllib

from astra.config import Config, dumps_toml, mask
from astra.frontmatter import as_list, expand_arguments, parse


def test_frontmatter_parse():
    text = """---
name: my-skill
description: "Do the thing, carefully"
allowed-tools: Read, Write
---
# Body

Hello $ARGUMENTS.
"""
    meta, body = parse(text)
    assert meta["name"] == "my-skill"
    assert meta["description"] == "Do the thing, carefully"
    assert as_list(meta["allowed-tools"]) == ["Read", "Write"]
    assert body.startswith("# Body")
    assert "$ARGUMENTS" in body


def test_frontmatter_without_header():
    meta, body = parse("plain text")
    assert meta == {}
    assert body == "plain text"


def test_expand_arguments():
    text = "Review $1 and $2. All: $ARGUMENTS. Dir: ${CLAUDE_SKILL_DIR}"
    out = expand_arguments(text, "a.py b.py", "/skills/review")
    assert "Review a.py and b.py" in out
    assert "All: a.py b.py" in out
    assert "/skills/review" in out


def test_toml_roundtrip(tmp_path):
    data = {
        "model": "astra-coder",
        "approval": "ask",
        "max_steps": 40,
        "temperature": 0.2,
        "show_reasoning": False,
        "providers": {"groq": {"model": "openai/gpt-oss-120b"}},
        "custom": {"astra-coder": {"chain": ["groq", "gemini"]}},
    }
    text = dumps_toml(data)
    parsed = tomllib.loads(text)
    assert parsed["model"] == "astra-coder"
    assert parsed["providers"]["groq"]["model"] == "openai/gpt-oss-120b"
    assert parsed["custom"]["astra-coder"]["chain"] == ["groq", "gemini"]


def test_config_auth_precedence(cfg: Config):
    cfg.auth.setdefault("keys", {})["groq"] = "from-file"
    cfg.env.pop("GROQ_API_KEY", None)
    assert cfg.api_key("groq", "GROQ_API_KEY") == "from-file"
    cfg.env["GROQ_API_KEY"] = "from-env"
    assert cfg.api_key("groq", "GROQ_API_KEY") == "from-env"


def test_set_key_writes_auth(cfg: Config):
    cfg.set_key("openrouter", "sk-or-secret")
    text = (cfg.home / "auth.toml").read_text(encoding="utf-8")
    assert "sk-or-secret" in text
    assert cfg.api_key("openrouter", "OPENROUTER_API_KEY") == "sk-or-secret"
    assert cfg.delete_key("openrouter") is True


def test_mask():
    assert mask("") == "—"
    assert mask("abcdefghij") == "abcd…ghij"
