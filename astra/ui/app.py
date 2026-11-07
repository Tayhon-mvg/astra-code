"""Interactive terminal application."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import auth, catalog, context, doctor
from ..agent import Agent, TurnStats
from ..agents import AgentRegistry, discover as discover_agents
from ..commands import BUILTIN_COMMANDS, CommandRegistry, expand_file_mentions, discover as discover_commands
from ..config import Config, secure_file, shorten_path
from ..providers import ProviderError, SetupError
from ..router import Router
from ..sessions import Session, find_session, list_sessions
from ..skills import SkillRegistry, discover as discover_skills
from . import render
from .sink import ConsoleSink
from .theme import make_console, palette

IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", ".cache", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
}


def _pt_style(theme: str) -> Style:
    c = palette(theme)
    return Style.from_dict(
        {
            "prompt": f"{c['primary']} bold",
            "muted": c["muted"],
            "warn": c["warn"],
            "err": c["err"],
            "bottom-toolbar": f"bg:#1e1b4b {c['text']}",
            "bottom-toolbar.text": c["text"],
            "completion-menu.completion": f"bg:#1e1b4b {c['text']}",
            "completion-menu.completion.current": "bg:#7c3aed #ffffff",
            "completion-menu.meta.completion": "bg:#1e1b4b #94a3b8",
            "completion-menu.meta.completion.current": "bg:#7c3aed #ede9fe",
            "scrollbar.background": "bg:#1e1b4b",
            "scrollbar.button": "bg:#7c3aed",
        }
    )


class AstraCompleter(Completer):
    def __init__(self, app: "AstraApp") -> None:
        self.app = app

    def get_completions(self, document, complete_event):  # noqa: ANN001
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            word = text[1:].lower()
            entries = [(name.lstrip("/"), hint, desc) for name, hint, desc in BUILTIN_COMMANDS]
            entries += [(cmd.name, cmd.arg_hint, cmd.description) for cmd in self.app.commands.all()]
            for name, hint, description in entries:
                if name.startswith(word):
                    yield Completion(
                        "/" + name,
                        start_position=-len(text),
                        display="/" + name,
                        display_meta=(description or hint)[:70],
                    )
            return

        match = re.search(r"@([^\s@]*)$", text)
        if not match:
            return
        raw = match.group(1)
        directory_part, _, prefix = raw.replace("\\", "/").rpartition("/")
        base = self.app.cwd
        if directory_part:
            candidate = Path(directory_part)
            base = candidate if candidate.is_absolute() else self.app.cwd / candidate
        try:
            entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        count = 0
        for entry in entries:
            if count >= 30:
                break
            if not entry.name.lower().startswith(prefix.lower()):
                continue
            if entry.name in IGNORED_DIRS or entry.name.startswith("."):
                if not entry.name.startswith(prefix):
                    continue
            suffix = "/" if entry.is_dir() else ""
            shown = (directory_part + "/" if directory_part else "") + entry.name + suffix
            count += 1
            yield Completion(
                "@" + shown,
                start_position=-len("@" + raw),
                display=entry.name + suffix,
                display_meta="папка" if entry.is_dir() else "файл",
            )


class AstraApp:
    def __init__(
        self,
        cfg: Config,
        cwd: Path | None = None,
        session: Session | None = None,
        *,
        console: Console | None = None,
        auto_approve: bool = False,
        theme: str | None = None,
        live_enabled: bool | None = None,
        quiet_tools: bool = False,
    ) -> None:
        self.cfg = cfg
        self.cwd = (cwd or cfg.cwd).resolve()
        self.theme = theme or str(cfg.get("theme") or "aurora")
        no_color = self.theme == "mono"
        self.console = console or make_console(self.theme, no_color=no_color)
        self.auto_approve = auto_approve
        self.quiet_tools = quiet_tools
        self.live_enabled = live_enabled
        self.router = Router(cfg)
        self.skills = SkillRegistry(discover_skills(self.cwd, cfg.home))
        self.agents = AgentRegistry(discover_agents(self.cwd, cfg.home))
        self.commands = CommandRegistry(discover_commands(self.cwd, cfg.home))
        self.session = session or Session.new(self.cwd, cfg.provider, cfg.model)
        self.sink = ConsoleSink(
            self.console,
            cfg,
            theme=self.theme,
            approve=self._approve,
            live_enabled=live_enabled,
            quiet_tools=quiet_tools,
        )
        self.agent = Agent(
            cfg,
            self.router,
            self.sink,
            self.session,
            cwd=self.cwd,
            skills=self.skills,
            agents=self.agents,
            commands=self.commands,
        )
        self._exit = False
        self._ctrlc = 0
        self._status_line = ""
        self._approve_session: PromptSession | None = None
        self._prompt: PromptSession | None = None
        self._refresh_status()

    # ------------------------------------------------------------------ prompt
    def _build_prompt(self) -> PromptSession:
        cfg_home = self.cfg.home
        cfg_home.mkdir(parents=True, exist_ok=True)
        history_path = cfg_home / "history"
        if not history_path.exists():
            history_path.touch()
        secure_file(history_path)
        history = FileHistory(str(history_path))
        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event):  # noqa: ANN001
            # Enter always sends. Tab accepts a completion from the menu.
            event.current_buffer.validate_and_handle()

        @bindings.add("c-j")
        def _newline(event):  # noqa: ANN001
            event.current_buffer.insert_text("\n")

        @bindings.add("escape", "enter")
        def _newline_alt(event):  # noqa: ANN001
            event.current_buffer.insert_text("\n")

        @bindings.add("c-l")
        def _clear(event):  # noqa: ANN001
            event.app.renderer.clear()
            event.app.invalidate()

        return PromptSession(
            history=history,
            completer=AstraCompleter(self),
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            style=_pt_style(self.theme),
            key_bindings=bindings,
            multiline=True,
            prompt_continuation=HTML("<muted>· </muted>"),
            bottom_toolbar=self._toolbar,
            placeholder=HTML("<muted>Спросите что-нибудь или /help</muted>"),
            enable_history_search=True,
        )

    @property
    def prompt(self) -> PromptSession:
        if self._prompt is None:
            self._prompt = self._build_prompt()
        return self._prompt

    def _toolbar(self):
        return [("class:bottom-toolbar", f" {self._status_line} ")]

    def _refresh_status(self) -> None:
        try:
            label, _ = self.router.describe(self.cfg.model)
        except Exception:  # noqa: BLE001
            label = "нет модели"
        try:
            used, limit = self.agent.context_usage()
            ctx_text = f"{used / 1000:.1f}k/{limit // 1000}k"
        except Exception:  # noqa: BLE001
            ctx_text = "—"
        approval = self.cfg.get("approval", "ask")
        cwd = shorten_path(self.cwd, 30)
        self._status_line = f"⚡ {label} · ctx {ctx_text} · {approval} · {cwd}"

    # ------------------------------------------------------------------- run
    async def run(self) -> None:
        self._print_banner()
        while not self._exit:
            try:
                text = await self.prompt.prompt_async(HTML("<prompt>✦</prompt> "))
            except KeyboardInterrupt:
                self._ctrlc += 1
                if self._ctrlc >= 2:
                    break
                self.console.print(Text("  Ctrl+C ещё раз — выход", style="dim"))
                continue
            except EOFError:
                break
            self._ctrlc = 0
            text = text.strip()
            if not text:
                continue
            if text.startswith("/"):
                await self._command(text)
            elif text.startswith("!"):
                await self._shell(text[1:].strip())
            else:
                await self.ask(text)
        self.session.save(self.cfg.home)
        self.console.print(Text("  до встречи ✦", style="dim"))

    def _print_banner(self) -> None:

        render.render_banner(
            self.console,
            self.cfg,
            self.router,
            skills=len(self.skills),
            agents=len(self.agents),
            commands=len(self.commands),
            theme=self.theme,
        )

    async def ask(self, text: str, *, show_user: bool = False) -> TurnStats | None:
        expanded, files = expand_file_mentions(text, self.cwd)
        if show_user:
            self.console.print(Text.assemble(("❯ ", palette(self.theme)["accent"]), (text, palette(self.theme)["text"])))
        if files:
            render.notice(self.console, "приложено: " + ", ".join(files), "info", theme=self.theme)
        try:
            stats = await self.agent.run(expanded)
            return stats
        except SetupError as exc:
            self.sink.stream.finish()
            render.error_panel(
                self.console,
                "нужна настройка",
                str(exc),
                "Быстрый старт: /setup · список: /keys · демо: astra demo",
                theme=self.theme,
            )
        except ProviderError as exc:
            self.sink.stream.finish()
            hint = ""
            if exc.status == 429:
                hint = "Free-tier лимит. Подождите или переключитесь: /model"
            elif exc.status in (401, 403):
                hint = "Ключ отклонён: /setup"
            render.error_panel(self.console, "ошибка провайдера", str(exc), hint, theme=self.theme)
        except KeyboardInterrupt:
            self.sink.stream.finish()
            render.notice(self.console, "остановлено", "warn", theme=self.theme)
        except Exception as exc:  # noqa: BLE001 - interactive shell must survive
            self.sink.stream.finish()
            render.error_panel(
                self.console,
                "внутренняя ошибка",
                f"{exc.__class__.__name__}: {exc}",
                "Запустите с --debug для traceback.",
                theme=self.theme,
            )
            if self.cfg.get("debug"):
                raise
        finally:
            self._refresh_status()
        return None

    async def _approve(self, tool: str, summary: str, detail: str) -> str:
        if self.auto_approve:
            return "allow"
        colors = palette(self.theme)
        body = Text()
        body.append(summary + "\n", style=f"bold {colors['text']}")
        if detail:
            preview = detail if len(detail) <= 2400 else detail[:2400] + "\n…"
            body.append(preview, style=colors["muted"])
        self.console.print(
            Panel(
                body,
                title=f"[{colors['warn']}]⚠ подтверждение · {tool}[/]",
                border_style=colors["warn"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
        if self._approve_session is None:
            self._approve_session = PromptSession(style=_pt_style(self.theme), multiline=False)
        try:
            answer = await self._approve_session.prompt_async(
                HTML("<warn>  Разрешить?</warn> [y] да · [n] нет · [a] всегда: ")
            )
        except (KeyboardInterrupt, EOFError):
            self.console.print()
            return "deny"
        self.console.print()
        answer = answer.strip().lower()
        if answer in {"a", "always", "в", "всегда"}:
            return "always"
        if answer in {"y", "yes", "д", "да"}:
            return "allow"
        return "deny"

    # ----------------------------------------------------------------- commands
    async def _shell(self, command: str) -> None:
        if not command:
            return
        result = await self.agent.tools.execute("run_command", {"command": command})
        self.console.print(Text(result, style="dim" if result.startswith("OK") else "red"))

    async def _command(self, raw: str) -> None:
        body = raw[1:].strip()
        if not body:
            render.help_table(self.console, self.commands.all(), theme=self.theme)
            return
        name, _, args = body.partition(" ")
        name = name.strip().lower()
        args = args.strip()
        aliases = {"exit": "quit", "q": "quit", "?": "help"}
        name = aliases.get(name, name)

        builtin = {
            "help": self._cmd_help,
            "model": self._cmd_model,
            "models": self._cmd_models,
            "provider": self._cmd_provider,
            "keys": self._cmd_keys,
            "setup": self._cmd_setup,
            "skills": self._cmd_skills,
            "agents": self._cmd_agents,
            "commands": self._cmd_commands,
            "init": self._cmd_init,
            "compact": self._cmd_compact,
            "clear": self._cmd_clear,
            "cost": self._cmd_cost,
            "resume": self._cmd_resume,
            "sessions": self._cmd_sessions,
            "theme": self._cmd_theme,
            "reasoning": self._cmd_reasoning,
            "approval": self._cmd_approval,
            "status": self._cmd_status,
            "doctor": self._cmd_doctor,
            "quit": self._cmd_quit,
        }
        handler = builtin.get(name)
        if handler is not None:
            await handler(args)
            return
        custom = self.commands.get(name)
        if custom is not None:
            render.notice(self.console, f"команда /{custom.name} · {custom.source}", "info", theme=self.theme)
            await self.ask(custom.expand(args))
            return
        render.notice(
            self.console,
            f"неизвестная команда /{name}. Введите /help",
            "warn",
            theme=self.theme,
        )

    async def _cmd_help(self, args: str) -> None:
        render.help_table(self.console, self.commands.all(), theme=self.theme)
        if self.commands:
            self.console.print(
                Text(
                    "  кастомные команды: " + ", ".join("/" + c.name for c in self.commands.all()),
                    style=palette(self.theme)["muted"],
                )
            )

    async def _cmd_model(self, args: str) -> None:
        colors = palette(self.theme)
        if args:
            await self._set_model(args)
            return
        table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {colors['primary']}", expand=True)
        table.add_column("модель", no_wrap=True)
        table.add_column("что это", overflow="fold")
        for model in catalog.COMPOSITE_MODELS.values():
            table.add_row(
                Text(model.id, style=f"bold {colors['accent']}"),
                Text(f"{model.name} · {model.tagline}\n{model.description}", style=colors["muted"]),
            )
        self.console.print(table)
        suggestions = self._model_suggestions()
        if suggestions:
            self.console.print(Text("  Быстрый выбор:", style=colors["muted"]))
            for index, (ref, label) in enumerate(suggestions, 1):
                self.console.print(Text(f"   {index}. {ref}  — {label}", style=colors["text"]))
        try:
            answer = await self.prompt.prompt_async(HTML("<muted>  модель (номер/имя, Enter — отмена): </muted>"))
        except (KeyboardInterrupt, EOFError):
            return
        answer = answer.strip()
        if not answer:
            return
        if answer.isdigit() and suggestions and 1 <= int(answer) <= len(suggestions):
            answer = suggestions[int(answer) - 1][0]
        await self._set_model(answer)

    def _model_suggestions(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = [("astra-coder", "Astra Coder"), ("astra-reasoner", "Astra Reasoner")]
        for state in self.router.states():
            if not state.available or not state.models:
                continue
            provider = state.provider
            for model in self.router.ranked_models(provider.id, "coder")[:2]:
                out.append((f"{provider.id}:{model['id']}", f"{model['label']} · {provider.short}"))
        return out[:14]

    async def _set_model(self, ref: str) -> None:
        try:
            targets = self.router.resolve(ref)
        except SetupError as exc:
            render.error_panel(self.console, "модель недоступна", str(exc), theme=self.theme)
            return
        self.cfg.set("model", ref)
        self.cfg.save()
        self.agent.refresh_context()
        self._refresh_status()
        target = targets[0]
        note = f"  fallback: {len(targets) - 1}" if len(targets) > 1 else ""
        render.notice(
            self.console,
            f"модель: {target.label} · {target.provider.short} ({target.model}){note}",
            "ok",
            theme=self.theme,
        )

    async def _cmd_models(self, args: str) -> None:
        refresh = "--refresh" in args
        try:
            target = self.router.resolve(self.cfg.model)[0]
            provider_id = target.provider.id
        except SetupError:
            provider_id = self.cfg.provider
        if refresh:
            render.notice(self.console, f"обновляю модели {provider_id}…", "info", theme=self.theme)
            try:
                count = await self.router.refresh(provider_id)
                render.notice(self.console, f"получено моделей: {count}", "ok", theme=self.theme)
            except (ProviderError, SetupError) as exc:
                render.error_panel(self.console, "не удалось обновить", str(exc), theme=self.theme)
                return
        models = self.router.ranked_models(provider_id, "coder")
        if not models:
            render.notice(
                self.console,
                f"для {provider_id} нет списка. Запустите /models --refresh",
                "warn",
                theme=self.theme,
            )
            return
        render.models_table(self.console, provider_id, models, theme=self.theme)
        self.console.print(
            Text(
                f"  активный провайдер: {provider_id} · выбрать: /model {provider_id}:<id>",
                style=palette(self.theme)["muted"],
            )
        )

    async def _cmd_provider(self, args: str) -> None:
        colors = palette(self.theme)
        if not args:
            table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {colors['primary']}", expand=True)
            table.add_column("id", no_wrap=True)
            table.add_column("провайдер", no_wrap=True)
            table.add_column("статус", overflow="fold")
            for state in self.router.states():
                table.add_row(
                    Text(state.provider.id, style=colors["accent"]),
                    Text(state.provider.short, style=colors["text"]),
                    Text(state.status, style=colors["ok"] if state.available else colors["muted"]),
                )
            self.console.print(table)
            return
        provider_id = args.strip().lower()
        provider = catalog.PROVIDERS.get(provider_id)
        if provider is None:
            render.notice(self.console, f"нет провайдера {provider_id}", "warn", theme=self.theme)
            return
        if not catalog.provider_available(provider, self.cfg):
            render.error_panel(
                self.console,
                "провайдер не настроен",
                catalog.missing_setup(provider, self.cfg),
                f"Добавить: /setup {provider_id}",
                theme=self.theme,
            )
            return
        try:
            model = self.router.choose_model(provider_id, "coder")
        except SetupError as exc:
            render.error_panel(self.console, "нет моделей", str(exc), theme=self.theme)
            return
        await self._set_model(f"{provider_id}:{model}")

    async def _cmd_keys(self, args: str) -> None:
        render.keys_table(self.console, self.cfg, self.router.states(), theme=self.theme)
        self.console.print(
            Text(
                "  Ключи хранятся в ~/.astra/auth.toml · переменные окружения имеют приоритет · "
                "все провайдеры в списке имеют бесплатный tier",
                style=palette(self.theme)["muted"],
            )
        )

    async def _cmd_setup(self, args: str) -> None:
        provider_id = args.strip().lower() or None
        if provider_id and provider_id not in catalog.PROVIDERS:
            render.notice(self.console, f"неизвестный провайдер {provider_id}", "warn", theme=self.theme)
            return
        self.console.print()
        ok = await asyncio.to_thread(
            auth.setup_interactive,
            self.cfg,
            provider_id,
            console=self.console,
            theme=self.theme,
        )
        if ok:
            try:
                await asyncio.to_thread(_refresh_sync, self.cfg, provider_id or self.cfg.provider)
            except Exception:  # noqa: BLE001
                pass
            self.agent.refresh_context()
            self._refresh_status()
            self.console.print(
                Text("  Готово. Попробуйте: /model  или просто опишите задачу.", style=palette(self.theme)["ok"])
            )

    async def _cmd_skills(self, args: str) -> None:
        colors = palette(self.theme)
        if args:
            skill = self.skills.get(args.strip())
            if skill is None:
                render.notice(self.console, f"скилл {args!r} не найден", "warn", theme=self.theme)
                return
            body = skill.expand("")
            self.console.print(
                Panel(
                    Text(body[:4000] + ("…" if len(body) > 4000 else ""), style=colors["text"]),
                    title=f"[{colors['primary']}]✦ {skill.name} · {skill.source}[/]",
                    border_style=colors["border"],
                    box=box.ROUNDED,
                    padding=(0, 1),
                )
            )
            self.session.add(
                {"role": "user", "content": f"[Загружен скилл {skill.name}]\n\n{body}"}
            )
            self.session.save(self.cfg.home)
            render.notice(self.console, f"скилл {skill.name} добавлен в контекст", "ok", theme=self.theme)
            return
        if not self.skills:
            render.notice(
                self.console,
                "скиллов нет. Создайте .claude/skills/<name>/SKILL.md или /setup",
                "info",
                theme=self.theme,
            )
            return
        table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {colors['primary']}", expand=True)
        table.add_column("скилл", no_wrap=True)
        table.add_column("источник", width=10)
        table.add_column("описание", overflow="fold", style=colors["muted"])
        for skill in self.skills.all():
            table.add_row(Text(skill.name, style=colors["text"]), skill.source, skill.description)
        self.console.print(table)
        self.console.print(
            Text("  загрузить: /skills <name> · формат: .claude/skills/<name>/SKILL.md", style=colors["muted"])
        )

    async def _cmd_agents(self, args: str) -> None:
        colors = palette(self.theme)
        table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {colors['primary']}", expand=True)
        table.add_column("агент", no_wrap=True)
        table.add_column("источник", width=10)
        table.add_column("инструменты", overflow="fold", style=colors["muted"])
        table.add_column("описание", overflow="fold", style=colors["muted"])
        for agent in self.agents.all():
            table.add_row(
                Text(agent.name, style=colors["text"]),
                agent.source,
                ", ".join(agent.tools) if agent.tools else "все",
                agent.description,
            )
        self.console.print(table)
        self.console.print(
            Text("  формат: .claude/agents/<name>.md · вызов: модель сама использует task", style=colors["muted"])
        )

    async def _cmd_commands(self, args: str) -> None:
        colors = palette(self.theme)
        if not self.commands:
            render.notice(self.console, "кастомных команд нет", "info", theme=self.theme)
            return
        table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {colors['primary']}", expand=True)
        table.add_column("команда", no_wrap=True)
        table.add_column("источник", width=10)
        table.add_column("описание", overflow="fold", style=colors["muted"])
        for command in self.commands.all():
            table.add_row("/" + command.name, command.source, command.description)
        self.console.print(table)

    async def _cmd_init(self, args: str) -> None:
        path = self.cwd / "ASTRA.md"
        if path.exists() and "--force" not in args:
            render.notice(self.console, "ASTRA.md уже есть. Перезаписать: /init --force", "info", theme=self.theme)
            return
        try:
            path = context.init_memory(self.cwd)
        except OSError as exc:
            render.error_panel(self.console, "не удалось создать ASTRA.md", str(exc), theme=self.theme)
            return
        self.agent.refresh_context()
        render.notice(self.console, f"создан {shorten_path(path)} — дополните команды и правила", "ok", theme=self.theme)

    async def _cmd_compact(self, args: str) -> None:
        ok = await self.agent.compact(force=True)
        if ok:
            self._refresh_status()
        else:
            render.notice(self.console, "сжимать нечего", "info", theme=self.theme)

    async def _cmd_clear(self, args: str) -> None:
        self.session.reset()
        self.agent.tools.ctx.todos = []
        self.session.save(self.cfg.home)
        self._refresh_status()
        render.notice(self.console, "сессия очищена", "ok", theme=self.theme)

    async def _cmd_cost(self, args: str) -> None:
        usage = self.session.usage
        render.status_panel(
            self.console,
            [
                ("сессия", self.session.id),
                ("ходов", str(usage.turns)),
                ("шагов", str(usage.steps)),
                ("токены", f"{usage.prompt_tokens / 1000:.1f}k in · {usage.completion_tokens / 1000:.1f}k out"),
                ("время", f"{usage.seconds / 60:.1f} мин"),
                ("стоимость", "$0.00 — только free-tier"),
                ("сообщений", str(len(self.session.messages))),
            ],
            title="расход",
            theme=self.theme,
        )

    async def _cmd_resume(self, args: str) -> None:
        sessions = list_sessions(self.cfg.home)
        if not sessions:
            render.notice(self.console, "сохранённых сессий нет", "info", theme=self.theme)
            return
        if args:
            session = find_session(self.cfg.home, args.strip())
            if session is None:
                render.notice(self.console, f"сессия {args!r} не найдена", "warn", theme=self.theme)
                return
        else:
            render.session_table(self.console, sessions, theme=self.theme)
            try:
                answer = await self.prompt.prompt_async(HTML("<muted>  номер/ID сессии (Enter — отмена): </muted>"))
            except (KeyboardInterrupt, EOFError):
                return
            answer = answer.strip()
            if not answer:
                return
            if answer.isdigit() and 1 <= int(answer) <= len(sessions):
                session = sessions[int(answer) - 1]
            else:
                session = find_session(self.cfg.home, answer)
            if session is None:
                render.notice(self.console, "не найдено", "warn", theme=self.theme)
                return
        self.session = session
        self.agent = Agent(
            self.cfg,
            self.router,
            self.sink,
            session,
            cwd=self.cwd,
            skills=self.skills,
            agents=self.agents,
            commands=self.commands,
        )
        self._refresh_status()
        render.notice(
            self.console,
            f"сессия {session.id} · {session.title} · сообщений {len(session.messages)}",
            "ok",
            theme=self.theme,
        )

    async def _cmd_sessions(self, args: str) -> None:
        sessions = list_sessions(self.cfg.home)
        if not sessions:
            render.notice(self.console, "сохранённых сессий нет", "info", theme=self.theme)
            return
        render.session_table(self.console, sessions, theme=self.theme)
        self.console.print(Text("  продолжить: /resume <id>", style=palette(self.theme)["muted"]))

    async def _cmd_theme(self, args: str) -> None:
        name = args.strip().lower()
        if name not in {"aurora", "mono"}:
            name = "mono" if self.theme == "aurora" else "aurora"
        self.theme = name
        self.cfg.set("theme", name)
        self.cfg.save()
        self.console.no_color = name == "mono"
        self.sink.theme = name
        render.notice(self.console, f"тема: {name}", "ok", theme=name)

    async def _cmd_reasoning(self, args: str) -> None:
        value = not bool(self.cfg.get("show_reasoning", False))
        if args.strip().lower() in {"on", "1", "true", "вкл"}:
            value = True
        elif args.strip().lower() in {"off", "0", "false", "выкл"}:
            value = False
        self.cfg.set("show_reasoning", value)
        self.cfg.save()
        render.notice(self.console, f"reasoning: {'показан' if value else 'скрыт'}", "ok", theme=self.theme)

    async def _cmd_approval(self, args: str) -> None:
        value = args.strip().lower()
        if value not in {"ask", "auto", "readonly"}:
            value = {"ask": "auto", "auto": "readonly", "readonly": "ask"}.get(
                str(self.cfg.get("approval", "ask")), "ask"
            )
        self.cfg.set("approval", value)
        self.cfg.save()
        self._refresh_status()
        hint = {
            "ask": "спрашивать перед изменениями и командами",
            "auto": "разрешать всё автоматически (осторожно)",
            "readonly": "только чтение",
        }[value]
        render.notice(self.console, f"approval: {value} — {hint}", "ok", theme=self.theme)

    async def _cmd_status(self, args: str) -> None:
        label, detail = self.router.describe(self.cfg.model)
        used, limit = self.agent.context_usage()
        memory = context.discover_memory(self.cwd, self.cfg)
        render.status_panel(
            self.console,
            [
                ("модель", label),
                ("маршрут", detail),
                ("папка", str(self.cwd)),
                ("контекст", f"{used / 1000:.1f}k / {limit // 1000}k токенов"),
                ("сессия", f"{self.session.id} · {len(self.session.messages)} сообщений"),
                ("расширения", f"скиллов {len(self.skills)} · агентов {len(self.agents)} · команд {len(self.commands)}"),
                ("память", ", ".join(m.title for m in memory) or "нет"),
                ("approval", str(self.cfg.get("approval", "ask"))),
                ("тема", self.theme),
            ],
            title="status",
            theme=self.theme,
        )

    async def _cmd_doctor(self, args: str) -> None:
        checks = doctor.run_checks(
            self.cfg,
            self.router,
            cwd=self.cwd,
            skills=self.skills,
            agents=self.agents,
            commands=self.commands,
            network="--network" in args,
        )
        doctor.render(self.console, checks, theme=self.theme)

    async def _cmd_quit(self, args: str) -> None:
        self._exit = True
        self.session.save(self.cfg.home)


def _refresh_sync(cfg: Config, provider_id: str) -> None:
    asyncio.run(Router(cfg).refresh(provider_id))
