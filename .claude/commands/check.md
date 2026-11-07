---
description: Run tests and review the current change
argument-hint: <path or area>
---

Check the change in `$ARGUMENTS` (or the whole repo if empty):

1. Run `.venv\Scripts\python -m pytest -q`.
2. Inspect the touched files with `grep`/`read_file`.
3. Report: test result, risky spots, missing tests, and a one-line verdict.

Be concise. Do not commit.
