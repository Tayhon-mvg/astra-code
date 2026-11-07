"""Command-line entry point for Astra Code."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __app__, __version__, auth, catalog, context, doctor
from .agent import Agent
from .agents import AgentRegistry, discover as discover_agents
from .commands import CommandRegistry, discover as discover_commands, expand_file_mentions
from .config import Config, ConfigError, shorten_path
from .providers import ProviderError, SetupError
from .router import Router
from .sessions import Session, find_session, latest_session
from .skills import SkillRegistry, discover as discover_skills
from .ui import render
from .ui.app import AstraApp
from .ui.demo import run_demo
from .ui.sink import PlainSink
from .ui.theme import make_console, palette


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    if os.name == "nt":
        os.system("")


# --------------------------------------------------------------------------- parser


def build_parser(*, subcommands: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astra",
        description=f"{__app__} {__version__} — бесплатный терминальный coding-агент.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  astra                         интерактивный режим\n"
            '  astra "исправь тест"          one-shot запрос\n'
            '  astra -p "объясни код"        вывод только текста (pipe-friendly)\n'
            "  astra demo                    демо без ключей\n"
            "  astra auth set openrouter     добавить бесплатный ключ\n"
            "  astra doctor                  диагностика\n"
        ),
    )
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true", help="только текст в stdout")
    parser.add_argument("--model", help="модель: astra-coder | astra-reasoner | provider:model")
    parser.add_argument("--provider", help="провайдер по умолчанию")
    parser.add_argument("--cwd", help="рабочая папка проекта")
    parser.add_argument("--yes", action="store_true", help="авто-подтверждение изменений и команд")
    parser.add_argument("--ask-commands", action="store_true", help="спрашивать перед каждой shell-командой")
    parser.add_argument("--read-only", action="store_true", help="запретить изменения и команды")
    parser.add_argument("--continue", dest="continue_", action="store_true", help="продолжить последнюю сессию")
    parser.add_argument("--resume", nargs="?", const="", default=None, help="продолжить сессию по id")
    parser.add_argument("--theme", choices=["aurora", "mono"], help="цветовая тема")
    parser.add_argument("--no-color", action="store_true", help="без ANSI-цветов")
    parser.add_argument("--debug", action="store_true", help="показывать traceback")
    parser.add_argument("--version", action="version", version=f"{__app__} {__version__}")

    # A free-form prompt and subparsers cannot share a positional in argparse:
    # `auth list` would put "auth" into prompt and "list" into the command.
    if not subcommands:
        parser.add_argument("prompt", nargs="*", help="запрос для one-shot режима")
        return parser

    sub = parser.add_subparsers(dest="command")

    def add_cwd(sub_parser: argparse.ArgumentParser) -> None:
        sub_parser.add_argument("--cwd", default=argparse.SUPPRESS, help="рабочая папка проекта")

    auth_p = sub.add_parser("auth", help="ключи и провайдеры")
    add_cwd(auth_p)
    auth_sub = auth_p.add_subparsers(dest="auth_command")
    auth_sub.add_parser("list", help="список провайдеров и ключей")
    set_p = auth_sub.add_parser("set", help="добавить ключ")
    set_p.add_argument("provider", nargs="?")
    set_p.add_argument("--key", help="ключ (неинтерактивно)")
    set_p.add_argument("--account-id", help="Cloudflare Account ID")
    set_p.add_argument("--base-url", help="свой base_url для провайдера/endpoint")
    set_p.add_argument(
        "--from-omniroute",
        action="store_true",
        help="импортировать ключ из локальной ~/.omniroute/storage.sqlite",
    )
    test_p = auth_sub.add_parser("test", help="проверить ключ")
    test_p.add_argument("provider", nargs="?")
    remove_p = auth_sub.add_parser("remove", help="удалить ключ")
    remove_p.add_argument("provider")

    models_p = sub.add_parser("models", help="бесплатные модели")
    add_cwd(models_p)
    models_p.add_argument("--refresh", action="store_true")
    models_p.add_argument("--all", action="store_true")
    models_p.add_argument("--provider", dest="provider_filter")
    models_p.add_argument("--limit", type=int, default=None, help="сколько строк показать (0 — все)")
    models_p.add_argument("--verified", action="store_true", help="только проверенные вручную модели")

    skills_p = sub.add_parser("skills", help="список скиллов")
    add_cwd(skills_p)
    agents_p = sub.add_parser("agents", help="список субагентов")
    add_cwd(agents_p)
    commands_p = sub.add_parser("commands", help="кастомные слэш-команды")
    add_cwd(commands_p)

    doctor_p = sub.add_parser("doctor", help="диагностика окружения")
    add_cwd(doctor_p)
    doctor_p.add_argument("--network", action="store_true")

    init_p = sub.add_parser("init", help="создать ASTRA.md")
    add_cwd(init_p)
    init_p.add_argument("--force", action="store_true")

    demo_p = sub.add_parser("demo", help="демо без ключей")
    add_cwd(demo_p)
    demo_p.add_argument("--no-color", action="store_true")

    sub.add_parser("version", help="версия")
    return parser


# ---------------------------------------------------------------------------- setup


def _load_config(args: argparse.Namespace) -> tuple[Config, Path]:
    cwd = Path(args.cwd).expanduser().resolve() if getattr(args, "cwd", None) else Path.cwd().resolve()
    try:
        cfg = Config.load(cwd)
    except ConfigError as exc:
        print(f"astra: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if getattr(args, "model", None):
        cfg.set("model", args.model)
    if getattr(args, "provider", None):
        cfg.set("provider", args.provider)
    if getattr(args, "theme", None):
        cfg.set("theme", args.theme)
    if getattr(args, "yes", False):
        cfg.set("approval", "auto")
    if getattr(args, "ask_commands", False):
        cfg.set("auto_commands", False)
    if getattr(args, "read_only", False):
        cfg.set("approval", "readonly")
    if getattr(args, "debug", False):
        cfg.set("debug", True)
    return cfg, cwd


def _console(cfg: Config, *, no_color: bool = False, stderr: bool = False) -> Console:
    theme = str(cfg.get("theme") or "aurora")
    return make_console(theme, no_color=no_color or theme == "mono", stderr=stderr)


def _pick_session(cfg: Config, cwd: Path, args: argparse.Namespace) -> Session:
    if args.resume is not None:
        if args.resume:
            session = find_session(cfg.home, args.resume)
            if session is None:
                print(f"astra: сессия {args.resume!r} не найдена", file=sys.stderr)
                raise SystemExit(1)
            return session
        session = latest_session(cfg.home, cwd) or latest_session(cfg.home)
        if session is None:
            print("astra: сохранённых сессий нет", file=sys.stderr)
            raise SystemExit(1)
        return session
    if args.continue_:
        session = latest_session(cfg.home, cwd) or latest_session(cfg.home)
        if session is None:
            print("astra: сохранённых сессий нет", file=sys.stderr)
            raise SystemExit(1)
        return session
    return Session.new(cwd, cfg.provider, cfg.model)


# --------------------------------------------------------------------------- run


def _run_repl(cfg: Config, cwd: Path, args: argparse.Namespace) -> int:
    console = _console(cfg, no_color=args.no_color)
    session = None
    if args.resume is not None or args.continue_:
        session = _pick_session(cfg, cwd, args)
    app = AstraApp(
        cfg,
        cwd,
        session,
        console=console,
        auto_approve=cfg.approval == "auto",
        theme=str(cfg.get("theme") or "aurora"),
        live_enabled=console.is_terminal,
    )
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        return 130
    return 0


def _print_oneshot_header(console: Console, cfg: Config, router: Router) -> None:
    theme = str(cfg.get("theme") or "aurora")
    label, _ = router.describe(cfg.model)
    console.print(
        Text.assemble(
            ("✦ ", f"bold {palette(theme)['primary']}"),
            ("Astra", f"bold {palette(theme)['primary']}"),
            ("  " + label, palette(theme)["muted"]),
        )
    )


def _run_pipe(cfg: Config, cwd: Path, text: str, session: Session, args: argparse.Namespace) -> int:
    theme = str(cfg.get("theme") or "aurora")
    err_console = make_console(theme, no_color=args.no_color or theme == "mono", stderr=True)
    router = Router(cfg)
    skills = SkillRegistry(discover_skills(cwd, cfg.home))
    agents = AgentRegistry(discover_agents(cwd, cfg.home))
    commands = CommandRegistry(discover_commands(cwd, cfg.home))
    sink = PlainSink(err_console, auto_approve=cfg.approval == "auto", quiet=False)
    agent = Agent(cfg, router, sink, session, cwd=cwd, skills=skills, agents=agents, commands=commands)
    expanded, _files = expand_file_mentions(text, cwd)

    async def go() -> int:
        try:
            await agent.run(expanded)
        except SetupError as exc:
            print(f"astra: {exc}", file=sys.stderr)
            return 1
        except ProviderError as exc:
            print(f"astra: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
        except Exception as exc:  # noqa: BLE001
            if cfg.get("debug"):
                raise
            print(f"astra: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        finally:
            session.save(cfg.home)
        return 0

    return asyncio.run(go())


def _run_oneshot(cfg: Config, cwd: Path, text: str, args: argparse.Namespace) -> int:
    session = _pick_session(cfg, cwd, args)
    use_plain = args.print_mode or not sys.stdin.isatty() or not sys.stdout.isatty()
    if use_plain:
        return _run_pipe(cfg, cwd, text, session, args)
    console = _console(cfg, no_color=args.no_color)
    router = Router(cfg)
    app = AstraApp(
        cfg,
        cwd,
        session,
        console=console,
        auto_approve=cfg.approval == "auto",
        theme=str(cfg.get("theme") or "aurora"),
        live_enabled=console.is_terminal,
    )
    _print_oneshot_header(console, cfg, router)
    console.print()
    stats = asyncio.run(app.ask(text, show_user=True))
    return 0 if stats is not None else 1


# ------------------------------------------------------------------ subcommands


def _cmd_auth(cfg: Config, args: argparse.Namespace) -> int:
    console = _console(cfg)
    theme = str(cfg.get("theme") or "aurora")
    command = getattr(args, "auth_command", None) or "list"
    if command == "list":
        router = Router(cfg)
        render.keys_table(console, cfg, router.states(), theme=theme)
        console.print(
            Text(
                "  добавить: astra auth set <provider> · проверить: astra auth test",
                style=palette(theme)["muted"],
            )
        )
        return 0
    if command == "remove":
        provider_id = args.provider.strip().lower()
        if auth.remove_key(cfg, provider_id):
            print(f"astra: ключ {provider_id} удалён")
            return 0
        print(f"astra: ключа {provider_id} нет", file=sys.stderr)
        return 1
    if command == "test":
        provider_ids = [args.provider] if getattr(args, "provider", None) else [
            state.provider.id for state in Router(cfg).states() if state.available
        ]
        if not provider_ids:
            print("astra: нет настроенных провайдеров", file=sys.stderr)
            return 1
        failed = 0
        for provider_id in provider_ids:
            ok, message, count = auth.validate(cfg, provider_id)
            mark = "✓" if ok else "✗"
            print(f"{mark} {provider_id}: {message}" + (f" · моделей {count}" if count else ""))
            failed += 0 if ok else 1
        return 0 if failed == 0 else 1
    if command == "set":
        return _cmd_auth_set(cfg, args, console)
    print(f"astra: неизвестная команда auth {command}", file=sys.stderr)
    return 2


def _cmd_auth_set(cfg: Config, args: argparse.Namespace, console: Console) -> int:
    provider_id = (args.provider or "").strip().lower() or None
    if provider_id and provider_id not in catalog.PROVIDERS:
        print(f"astra: неизвестный провайдер {provider_id}", file=sys.stderr)
        return 2
    if args.base_url:
        target = provider_id or "custom"
        cfg.data.setdefault("providers", {}).setdefault(target, {})["base_url"] = args.base_url.strip()
        cfg.save()
    if args.account_id:
        cfg.set_option(provider_id or "cloudflare", "account_id", args.account_id.strip())
    if args.from_omniroute:
        if provider_id not in {None, "omniroute"}:
            print("astra: --from-omniroute работает только для omniroute", file=sys.stderr)
            return 2
        provider_id = "omniroute"
        key = auth.read_omniroute_key()
        if not key:
            print(
                "astra: не нашёл активный ключ в ~/.omniroute/storage.sqlite. "
                "Запустите OmniRoute и создайте ключ в dashboard.",
                file=sys.stderr,
            )
            return 1
        previous = cfg.api_key(provider_id, catalog.PROVIDERS[provider_id].env_key)
        cfg.set_key(provider_id, key)
        ok, message, count = auth.validate(cfg, provider_id)
        if not ok and "401" in message:
            if previous:
                cfg.set_key(provider_id, previous)
            else:
                cfg.delete_key(provider_id)
            print(f"astra: ключ OmniRoute не принят — {message}", file=sys.stderr)
            return 1
        print(f"{'✓' if ok else '⚠'} omniroute: {message}" + (f" · моделей {count}" if count else ""))
        _refresh_models_quiet(cfg, provider_id)
        return 0
    if args.key:
        if not provider_id:
            print("astra: укажите провайдера: astra auth set <provider> --key ...", file=sys.stderr)
            return 2
        previous = cfg.api_key(provider_id, catalog.PROVIDERS[provider_id].env_key)
        cfg.set_key(provider_id, args.key)
        ok, message, count = auth.validate(cfg, provider_id)
        if not ok and "401" in message:
            if previous:
                cfg.set_key(provider_id, previous)
            else:
                cfg.delete_key(provider_id)
            print(f"astra: ключ не принят — {message}", file=sys.stderr)
            return 1
        print(f"{'✓' if ok else '⚠'} {provider_id}: {message}" + (f" · моделей {count}" if count else ""))
        _refresh_models_quiet(cfg, provider_id)
        return 0
    ok = auth.setup_interactive(
        cfg,
        provider_id,
        console=console,
        theme=str(cfg.get("theme") or "aurora"),
    )
    if ok:
        _refresh_models_quiet(cfg, provider_id or cfg.provider)
        return 0
    return 1


def _refresh_models_quiet(cfg: Config, provider_id: str) -> None:
    try:
        asyncio.run(Router(cfg).refresh(provider_id))
    except (ProviderError, SetupError, Exception):  # noqa: BLE001
        pass


def _cmd_models(cfg: Config, args: argparse.Namespace) -> int:
    console = _console(cfg)
    theme = str(cfg.get("theme") or "aurora")
    router = Router(cfg)
    provider_id = args.provider_filter
    if not provider_id:
        try:
            provider_id = router.resolve(cfg.model)[0].provider.id
        except SetupError:
            provider_id = cfg.provider
    if args.refresh:
        targets = [provider_id] if not args.all else None
        report = asyncio.run(router.refresh_all(targets))
        for pid, (count, message) in report.items():
            if count is None:
                console.print(Text(f"  ✗ {pid}: {message}", style=palette(theme)["warn"]))
            else:
                console.print(Text(f"  ✓ {pid}: {count} моделей", style=palette(theme)["ok"]))
    provider_ids = [state.provider.id for state in router.states() if state.available] if args.all else [provider_id]
    if not provider_ids:
        provider_ids = [provider_id]
        console.print(
            Text(
                "  ключ не настроен — показан встроенный каталог бесплатных моделей",
                style=palette(theme)["warn"],
            )
        )
    for pid in provider_ids:
        models = router.ranked_models(pid, "coder")
        if args.verified:
            models = [model for model in models if "проверено" in str(model.get("note") or "")]
        provider = catalog.PROVIDERS.get(pid)
        label = "проверенных" if args.verified else "бесплатных"
        console.print(
            Text(
                f"\n  {provider.short if provider else pid} · {len(models)} {label} моделей",
                style=f"bold {palette(theme)['primary']}",
            )
        )
        if models:
            default_limit = 30 if pid == "omniroute" else 12
            limit = args.limit if args.limit is not None else default_limit
            render.models_table(console, pid, models, theme=theme, limit=limit)
        else:
            console.print(Text("  список пуст — добавьте: --refresh", style=palette(theme)["muted"]))
    return 0


def _cmd_skills(cfg: Config, cwd: Path) -> int:
    console = _console(cfg)
    theme = str(cfg.get("theme") or "aurora")
    skills = discover_skills(cwd, cfg.home)
    if not skills:
        console.print(Text("Скиллов нет. Формат: .claude/skills/<name>/SKILL.md", style=palette(theme)["muted"]))
        return 0
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {palette(theme)['primary']}", expand=True)
    table.add_column("скилл", no_wrap=True)
    table.add_column("источник", width=10)
    table.add_column("описание", overflow="fold", style=palette(theme)["muted"])
    for skill in skills:
        table.add_row(Text(skill.name, style=palette(theme)["text"]), skill.source, skill.description)
    console.print(table)
    return 0


def _cmd_agents(cfg: Config, cwd: Path) -> int:
    console = _console(cfg)
    theme = str(cfg.get("theme") or "aurora")
    agents = discover_agents(cwd, cfg.home)
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {palette(theme)['primary']}", expand=True)
    table.add_column("агент", no_wrap=True)
    table.add_column("источник", width=10)
    table.add_column("инструменты", overflow="fold", style=palette(theme)["muted"])
    table.add_column("описание", overflow="fold", style=palette(theme)["muted"])
    for agent in agents:
        table.add_row(
            Text(agent.name, style=palette(theme)["text"]),
            agent.source,
            ", ".join(agent.tools) if agent.tools else "все",
            agent.description,
        )
    console.print(table)
    return 0


def _cmd_commands(cfg: Config, cwd: Path) -> int:
    console = _console(cfg)
    theme = str(cfg.get("theme") or "aurora")
    commands = discover_commands(cwd, cfg.home)
    if not commands:
        console.print(
            Text(
                "Кастомных команд нет. Формат: .claude/commands/<name>.md или .codex/prompts/<name>.md",
                style=palette(theme)["muted"],
            )
        )
        return 0
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {palette(theme)['primary']}", expand=True)
    table.add_column("команда", no_wrap=True)
    table.add_column("источник", width=10)
    table.add_column("аргументы", width=14)
    table.add_column("описание", overflow="fold", style=palette(theme)["muted"])
    for command in commands:
        table.add_row(
            "/" + command.name,
            command.source,
            command.arg_hint or "—",
            command.description,
        )
    console.print(table)
    return 0


def _cmd_doctor(cfg: Config, cwd: Path, args: argparse.Namespace) -> int:
    console = _console(cfg)
    theme = str(cfg.get("theme") or "aurora")
    router = Router(cfg)
    skills = SkillRegistry(discover_skills(cwd, cfg.home))
    agents = AgentRegistry(discover_agents(cwd, cfg.home))
    commands = CommandRegistry(discover_commands(cwd, cfg.home))
    checks = doctor.run_checks(
        cfg, router, cwd=cwd, skills=skills, agents=agents, commands=commands, network=args.network
    )
    doctor.render(console, checks, theme=theme)
    return 0 if all(check.ok for check in checks) else 1


def _cmd_init(cfg: Config, cwd: Path, args: argparse.Namespace) -> int:
    console = _console(cfg)
    theme = str(cfg.get("theme") or "aurora")
    path = cwd / "ASTRA.md"
    if path.exists() and not args.force:
        console.print(Text("ASTRA.md уже существует. Перезаписать: astra init --force", style=palette(theme)["warn"]))
        return 0
    try:
        context.init_memory(cwd)
    except OSError as exc:
        print(f"astra: {exc}", file=sys.stderr)
        return 1
    console.print(Text(f"✓ создан {shorten_path(path)}", style=palette(theme)["ok"]))
    return 0


def _cmd_demo(cfg: Config, args: argparse.Namespace) -> int:
    theme = str(cfg.get("theme") or "aurora")
    no_color = args.no_color or theme == "mono"
    console = make_console(theme, no_color=no_color)
    try:
        asyncio.run(run_demo(console, theme=theme, live_enabled=console.is_terminal))
    except KeyboardInterrupt:
        return 130
    return 0


# ---------------------------------------------------------------------------- main


COMMANDS = {"auth", "models", "skills", "agents", "commands", "doctor", "init", "demo", "version"}
VALUE_OPTIONS = {"--model", "--provider", "--cwd", "--theme", "--resume"}


def _detect_command(argv: list[str]) -> bool:
    """Decide whether argv is a subcommand invocation or a free-form prompt."""
    if any(token in {"-h", "--help"} for token in argv):
        return True
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"-p", "--print", "--"}:
            return False
        if token in COMMANDS:
            return True
        if token in VALUE_OPTIONS:
            if token == "--resume" and index + 1 < len(argv) and argv[index + 1] in COMMANDS:
                return True
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return False
    return False


def main(argv: list[str] | None = None) -> int:
    _utf8()
    raw = list(sys.argv[1:] if argv is None else argv)
    dispatch = _detect_command(raw)
    parser = build_parser(subcommands=dispatch)
    try:
        args = parser.parse_args(raw)
    except SystemExit as exc:
        return int(exc.code or 0)
    if not dispatch:
        args.command = None

    if args.command == "version":
        print(f"{__app__} {__version__}")
        return 0

    try:
        cfg, cwd = _load_config(args)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.command == "auth":
        return _cmd_auth(cfg, args)
    if args.command == "models":
        return _cmd_models(cfg, args)
    if args.command == "skills":
        return _cmd_skills(cfg, cwd)
    if args.command == "agents":
        return _cmd_agents(cfg, cwd)
    if args.command == "commands":
        return _cmd_commands(cfg, cwd)
    if args.command == "doctor":
        return _cmd_doctor(cfg, cwd, args)
    if args.command == "init":
        return _cmd_init(cfg, cwd, args)
    if args.command == "demo":
        return _cmd_demo(cfg, args)

    text = " ".join(getattr(args, "prompt", [])).strip()
    if not text:
        if not sys.stdin.isatty():
            text = sys.stdin.read().strip()
            if text:
                return _run_pipe(cfg, cwd, text, _pick_session(cfg, cwd, args), args)
            print("astra: пустой stdin. Используйте astra -p \"...\" или astra demo", file=sys.stderr)
            return 2
        if args.print_mode:
            print("astra: пустой запрос", file=sys.stderr)
            return 2
        return _run_repl(cfg, cwd, args)
    return _run_oneshot(cfg, cwd, text, args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
