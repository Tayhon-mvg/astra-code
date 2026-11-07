---
name: astra-release
description: Prepare an Astra release — check versions, run tests, update the changelog, draft notes.
allowed-tools: read_file, edit_file, grep, run_command
argument-hint: <version>
---

# Astra release checklist

Use this skill when the user asks to cut a release or bump the version to `$ARGUMENTS`.

1. Read `pyproject.toml` and `astra/__init__.py`. The versions must match.
2. Bump both to `$ARGUMENTS` if a version was supplied.
3. Run the suite with the project interpreter:
   `.venv\Scripts\python -m pytest -q` (Windows) or `.venv/bin/python -m pytest -q`.
4. Check that `README.md` still describes every shipped provider.
5. Add a short `CHANGELOG.md` entry: date, version, added / changed / fixed.
6. Report: version, test result, files changed, and the exact commands used.

Do not tag, commit, or push unless the user explicitly asks.
