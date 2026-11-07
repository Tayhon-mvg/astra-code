"""Project awareness: memory files, git state, stack detection, environment."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config, shorten_path

MEMORY_NAMES = ("AGENTS.md", "CLAUDE.md", "ASTRA.md")
IGNORED = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", ".next", ".cache", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".idea", ".vscode", ".tox", "coverage", "htmlcov",
}
MAX_MEMORY_TOTAL = 30_000
MAX_SNAPSHOT_ENTRIES = 90


@dataclass
class MemoryFile:
    path: Path
    scope: str  # user | project
    title: str
    content: str


@dataclass
class GitInfo:
    branch: str = ""
    status: list[str] | None = None
    last_commit: str = ""
    root: str = ""

    @property
    def dirty(self) -> int:
        return len(self.status or [])


def _run_git(cwd: Path, args: list[str], timeout: float = 3.0) -> str:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def git_info(cwd: Path) -> GitInfo:
    branch = _run_git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not branch:
        return GitInfo()
    status_text = _run_git(cwd, ["status", "--short"])
    root = _run_git(cwd, ["rev-parse", "--show-toplevel"])
    last = _run_git(cwd, ["log", "-1", "--pretty=%s"])
    return GitInfo(
        branch=branch,
        status=status_text.splitlines()[:20] if status_text else [],
        last_commit=last[:160],
        root=root,
    )


def discover_memory(cwd: Path, cfg: Config) -> list[MemoryFile]:
    found: list[MemoryFile] = []
    seen: set[Path] = set()

    def add(path: Path, scope: str) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen or not resolved.is_file():
            return
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if not text.strip():
            return
        seen.add(resolved)
        title = resolved.name
        if resolved.parent.name == ".astra" or "astra" in title.lower():
            title = f"Astra · {title}"
        found.append(MemoryFile(resolved, scope, title, text[:60_000]))

    global_memory = cfg.home / "ASTRA.md"
    add(global_memory, "user")
    add(Path.home() / ".claude" / "CLAUDE.md", "user")
    add(Path.home() / ".codex" / "AGENTS.md", "user")

    parents = [cwd, *cwd.parents]
    # Root-most first so the closest file is last (and wins in case of conflict).
    for directory in reversed(parents[:8]):
        for name in MEMORY_NAMES:
            add(directory / name, "project")
        add(directory / ".astra" / "ASTRA.md", "project")

    total = 0
    result: list[MemoryFile] = []
    for item in found:
        if total >= MAX_MEMORY_TOTAL:
            break
        remaining = MAX_MEMORY_TOTAL - total
        if len(item.content) > remaining:
            item.content = item.content[:remaining] + "\n… (обрезано)"
        total += len(item.content)
        result.append(item)
    return result


def detect_stack(cwd: Path) -> list[str]:
    checks = [
        ("pyproject.toml", "Python · pyproject"),
        ("requirements.txt", "Python · requirements"),
        ("setup.py", "Python · setuptools"),
        ("package.json", "Node.js"),
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("Cargo.toml", "Rust"),
        ("go.mod", "Go"),
        ("pom.xml", "Java · Maven"),
        ("build.gradle", "Java/Kotlin · Gradle"),
        ("Gemfile", "Ruby"),
        ("composer.json", "PHP"),
        ("Dockerfile", "Docker"),
        ("docker-compose.yml", "Docker Compose"),
        ("Makefile", "Make"),
        ("CMakeLists.txt", "C/C++ · CMake"),
    ]
    stack = [label for name, label in checks if (cwd / name).exists()]
    if not stack:
        for pattern in ("*.csproj", "*.sln", "*.xcodeproj", "*.ipynb"):
            if list(cwd.glob(pattern)):
                stack.append(pattern)
    return stack


def project_tree(cwd: Path, max_entries: int = MAX_SNAPSHOT_ENTRIES) -> list[str]:
    lines: list[str] = []
    count = 0

    def walk(directory: Path, prefix: str, level: int) -> None:
        nonlocal count
        if level > 2 or count >= max_entries:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        entries = [e for e in entries if e.name not in IGNORED and not e.name.startswith(".") or e.name in {".claude", ".astra", ".codex"}]
        for index, entry in enumerate(entries):
            if count >= max_entries:
                lines.append(prefix + "…")
                return
            count += 1
            last = index == len(entries) - 1
            branch = "└── " if last else "├── "
            if entry.is_dir():
                lines.append(prefix + branch + entry.name + "/")
                walk(entry, prefix + ("    " if last else "│   "), level + 1)
            else:
                lines.append(prefix + branch + entry.name)

    walk(cwd, "", 1)
    return lines


def environment_block(cwd: Path, cfg: Config, git: GitInfo) -> str:
    shell = "PowerShell" if os.name == "nt" else os.environ.get("SHELL", "sh")
    lines = [
        f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {sys.version.split()[0]}",
        f"Shell: {shell}",
        f"Workspace: {cwd}",
        f"Date: {time.strftime('%Y-%m-%d %H:%M')}",
        f"Approval mode: {cfg.get('approval', 'ask')}",
    ]
    if git.branch:
        lines.append(f"Git: branch {git.branch}, {git.dirty} изменённых файлов")
        if git.last_commit:
            lines.append(f"Last commit: {git.last_commit}")
    return "\n".join(lines)


def memory_block(files: list[MemoryFile]) -> str:
    if not files:
        return ""
    chunks = []
    for item in files:
        scope = "user" if item.scope == "user" else "project"
        chunks.append(f"### {item.title} ({scope})\n{item.content.strip()}")
    return "\n\n".join(chunks)


def build_project_context(cwd: Path, cfg: Config, *, include_tree: bool = True) -> str:
    git = git_info(cwd)
    parts = ["<environment>", environment_block(cwd, cfg, git), "</environment>"]
    stack = detect_stack(cwd)
    if stack:
        parts += ["", "<stack>", ", ".join(stack), "</stack>"]
    if git.status:
        parts += ["", "<git_status>", "\n".join(git.status), "</git_status>"]
    memory = discover_memory(cwd, cfg)
    block = memory_block(memory)
    if block:
        parts += ["", "<memory>", block, "</memory>"]
    if include_tree:
        tree = project_tree(cwd)
        if tree:
            parts += ["", "<files>", "\n".join(tree), "</files>"]
    return "\n".join(parts)


def init_memory(cwd: Path) -> Path:
    stack = detect_stack(cwd)
    tree = project_tree(cwd, 50)
    lines = [
        "# ASTRA.md",
        "",
        "> Контекст проекта для Astra Code. Файл читается автоматически.",
        "> Здесь же можно описать правила, команды и архитектуру.",
        "",
        "## Стек",
        "",
    ]
    if stack:
        lines += [f"- {item}" for item in stack]
    else:
        lines.append("- определите стек проекта")
    lines += [
        "",
        "## Команды",
        "",
        "```bash",
        "# установка зависимостей",
        "",
        "# тесты",
        "",
        "# линтер / форматтер",
        "```",
        "",
        "## Структура",
        "",
        "```text",
        *tree,
        "```",
        "",
        "## Правила",
        "",
        "- Читать файл перед изменением; делать минимальные диффы.",
        "- После правок запускать релевантные проверки.",
        "- Не коммитить секреты и не печатать содержимое ключей.",
        "",
    ]
    path = cwd / "ASTRA.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def short_cwd(cwd: Path) -> str:
    return shorten_path(cwd, 54)
