# AGENTS.md — Astra Code

Astra Code is a free, terminal-native coding agent written in Python. It speaks the
OpenAI-compatible wire format and routes every request through a free-tier provider.

## Commands

```bash
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
.venv\Scripts\astra demo
.venv\Scripts\astra doctor
```

On Unix:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/astra demo
```

## Architecture

- `astra/catalog.py` — free provider registry, free-only rules, two composite models.
- `astra/providers.py` — one streaming OpenAI-compatible client + offline mock.
- `astra/router.py` — fallback chains, model cache, free-tier enforcement.
- `astra/agent.py` — tool-calling loop, compaction, subagents.
- `astra/tools.py` — file/shell/search tools, approvals, workspace sandbox.
- `astra/skills.py`, `agents.py`, `commands.py` — Claude Code / Codex extension loaders.
- `astra/ui/` — Rich renderer, prompt_toolkit REPL, offline demo.

## Rules

- Keep the free-tier promise: never add a provider without a free tier.
- Never commit credentials; `~/.astra/auth.toml` is private.
- Every new tool needs: schema, approval policy, truncation, and a test.
- UI strings are Russian, code and tool descriptions are English.
- Run `pytest -q` before declaring a change done.
