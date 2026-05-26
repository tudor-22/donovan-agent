from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

from prompt_toolkit.application import get_app, in_terminal
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import clear
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from donovanagent.activity import ActivityRenderer, ActivityService
from donovanagent.agent import DonovanAgent
from donovanagent.config.manager import ConfigManager
from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.config.wizard import configure_model, run_setup_wizard
from donovanagent.mcp.mentions import resolve_mentions, format_attachments
from donovanagent.mcp.ui import (
    mcp_status_panel,
    mcp_tool_panel,
    mcp_resource_panel,
    mcp_prompt_panel,
    mcp_log_panel,
    mcp_trust_prompt,
)
from donovanagent.memory import MemoryDatabase
from donovanagent.memory.manager import MemoryManager
from donovanagent.memory.project_context import detect_project_context
from donovanagent.providers import build_provider
from donovanagent.providers.base import LLMProvider
from donovanagent.product import ProductManager, ProductResult
from donovanagent.skills import SkillManager
from donovanagent.subagents import ROLE_PRESETS, SubagentRole
from donovanagent.tools.approval import ApprovalManager
from donovanagent.tools.base import ToolExecutionContext
from donovanagent.tools.registry import ToolRegistry, build_default_registry
from donovanagent.tools.user_tools import load_user_tools
from donovanagent.tools.web import TavilySearchProvider
from donovanagent.ui.prompt import create_prompt_session
from donovanagent.ui.render import (
    assistant_panel,
    config_table,
    error_panel,
    info_panel,
    print_startup,
    sessions_table,
    tools_used_panel,
    tools_table,
)
from donovanagent.ui.status import ActivityIndicator, status_table
from donovanagent.utils.errors import DonovanAgentError, MaxIterationsReached, ProviderError
from donovanagent.utils.logging import configure_logging
from donovanagent.utils.platform import get_platform_info
from donovanagent.utils.shell import resolve_shell


PROMPT_TOP_PADDING_LINES = 1
FOOTER_TOP_GAP_LINES = 2
STATUS_TOP_PADDING_LINES = 1
TURN_TOP_PADDING_LINES = 0
TURN_BOTTOM_PADDING_LINES = 1

BROWSER_COMPANION_WORK_TOOLS = {
    "browser_companion_active_tab",
    "browser_companion_snapshot",
    "browser_companion_list_tabs",
    "browser_companion_use_tab",
    "browser_companion_click",
    "browser_companion_type",
    "browser_companion_screenshot",
}
BROWSER_PLAYWRIGHT_WORK_TOOLS = {
    "browser_open",
    "browser_connect_existing",
    "browser_list_tabs",
    "browser_use_tab",
    "browser_screenshot",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_text",
    "browser_html",
    "browser_links",
    "browser_back",
    "browser_forward",
    "browser_reload",
    "browser_evaluate",
    "browser_wait_for_selector",
    "browser_wait",
}


def set_terminal_title(title: str = "Donovan Agent") -> None:
    """Set terminal tab title across all supported platforms."""
    import platform
    try:
        if platform.system() == "Windows":
            os.system(f"title {title}")
        else:
            sys.stdout.write(f"\033]0;{title}\007")
            sys.stdout.flush()
    except OSError:
        pass  # best-effort, non-critical


class DonovanAgentApp:
    def __init__(
        self,
        manager: ConfigManager | None = None,
        console: Console | None = None,
        *,
        assume_yes: bool = False,
    ) -> None:
        self.manager = manager or ConfigManager()
        self.paths = self.manager.paths
        self.console = console or Console()
        self.assume_yes = assume_yes
        self.config = self.manager.load(create=True)
        configure_logging(self.paths, self.config.logging.level, self.config.logging.file_logging)
        self.db = MemoryDatabase(self.config.memory.database_path)
        self.db.initialize()
        self.registry = build_default_registry(self.config)
        _load_user_tools(self.registry, self.paths.config_dir, self.config.app.default_workspace)
        self.approval = ApprovalManager(self.console, assume_yes=assume_yes)
        self.provider: LLMProvider | None = None
        self.agent: DonovanAgent | None = None
        self.product = ProductManager(self.paths.data_dir, self.config.app.default_workspace)
        self.session_id: str | None = None
        self._activity_enabled = True
        self._context_tokens = 0
        self._context_window = self.config.provider.context_window
        self._status_word = "Thinking"
        self._turn_started_at = 0.0
        self._turn_busy = threading.Event()
        self._turn_queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._turn_results: queue.Queue[str | None] = queue.Queue()

    def refresh(self) -> None:
        self.config = self.manager.load(create=True)
        configure_logging(self.paths, self.config.logging.level, self.config.logging.file_logging)
        self.db = MemoryDatabase(self.config.memory.database_path)
        self.db.initialize()
        self.registry = build_default_registry(self.config)
        _load_user_tools(self.registry, self.paths.config_dir, self.config.app.default_workspace)
        self.provider = None
        self.agent = None
        self.product.set_workspace(self.config.app.default_workspace)

    def ensure_provider(self) -> LLMProvider:
        if self.provider is None:
            self.provider = build_provider(self.config)
        return self.provider

    def ensure_agent(self) -> DonovanAgent:
        if self.agent is None:
            self.agent = DonovanAgent(
                self.config, self.db, self.ensure_provider(),
                self.registry, self.console, self.approval,
                config_dir=self.paths.config_dir,
            )
        return self.agent

    def start_session(self) -> str:
        self.session_id = self.db.create_session(
            self.config.app.default_workspace,
            self.config.provider.active,
            self.config.provider.model,
        )
        return self.session_id

    def _check_workspace_trust(self) -> None:
        from pathlib import Path as _Path
        from rich.prompt import Confirm as _Confirm
        cwd = str(_Path.cwd().resolve())
        approved = {str(_Path(p).resolve()) for p in self.config.security.approved_paths}
        if cwd not in approved:
            self.console.print(
                Panel(
                    f"[bold]{cwd}[/bold]\n\nDonovanAgent will be able to read and write files in this folder.",
                    title="Trust this folder?",
                    border_style="white",
                    box=box.ROUNDED,
                )
            )
            trust = _Confirm.ask("Do you trust this folder?", default=False)
            if trust:
                self.config.security.approved_paths.append(cwd)
                self.manager.save(self.config)
                self.refresh()
            else:
                self.console.print("[dim]Folder not trusted. File operations will be restricted.[/dim]")

    def _asyncio_cleanup(self) -> None:
        """Suppress stray RuntimeError from Playwright asyncio callbacks."""
        try:
            asyncio.get_running_loop()
            asyncio._set_running_loop(None)
        except RuntimeError:
            pass
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
        except RuntimeError:
            pass

    def _run_turn_thread(self, session_id: str, text: str) -> None:
        """Run an agent turn in a background thread. Results are displayed
        directly from this thread using patch_stdout-compatible output."""
        loop = None
        try:
            self._status_word = "Thinking"
            self._turn_started_at = time.monotonic()
            self.product.record_timeline(
                "turn_started",
                text[:300],
                session_id=session_id,
                metadata={"retry_prompt": text},
            )
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(lambda loop, ctx: None)

            agent = self.ensure_agent()
            # Resolve @ MCP resource mentions
            resolved_text, attachments = resolve_mentions(text, agent.mcp_manager)
            if attachments:
                self.console.print(info_panel(
                    f"Attached {len(attachments)} MCP resource(s)",
                    title="MCP Resources",
                ))
            full_text = resolved_text
            if attachments:
                full_text = resolved_text + format_attachments(attachments)

            def _set_status(word: str) -> None:
                self._status_word = word
                indicator.set_word(word)

            with ActivityIndicator(self.console, workspace=self.config.app.default_workspace) as indicator:
                agent.state_callback = _set_status
                agent.indicator = indicator
                answer = agent.run_turn(session_id, full_text)
                self._context_tokens = agent.last_context_tokens
                self._context_window = agent.config.provider.context_window
            tools_panel = tools_used_panel(agent.last_tool_names)
            used_companion_browser = any(name in BROWSER_COMPANION_WORK_TOOLS for name in agent.last_tool_names)
            used_playwright_browser = any(name in BROWSER_PLAYWRIGHT_WORK_TOOLS for name in agent.last_tool_names)
            if used_companion_browser:
                try:
                    agent.browser_companion.minimize()
                    self.product.record_timeline(
                        "browser_minimized",
                        "Browser Companion window minimized after browser work completed.",
                        session_id=session_id,
                    )
                except Exception as exc:
                    self.product.record_timeline(
                        "browser_minimize_failed",
                        str(exc),
                        session_id=session_id,
                    )
            if used_playwright_browser:
                try:
                    agent.browser_service.minimize()
                    self.product.record_timeline(
                        "browser_minimized",
                        "Browser minimized after browser work completed.",
                        session_id=session_id,
                    )
                except Exception as exc:
                    self.product.record_timeline(
                        "browser_minimize_failed",
                        str(exc),
                        session_id=session_id,
                    )
            self.product.record_timeline(
                "turn_completed",
                answer[:300],
                session_id=session_id,
                metadata={"tools": agent.last_tool_names},
            )
            self._show_turn_result("ok", answer, tools_panel)
        except MaxIterationsReached:
            self._show_turn_result("error", "[dim]Reached the tool iteration limit. "
                                   "Try rephrasing or breaking your request into smaller steps.[/dim]", None)
        except KeyboardInterrupt:
            self._show_turn_result("error", "[yellow]Generation interrupted.[/yellow]", None)
        except DonovanAgentError as exc:
            message = str(exc).strip()
            self.product.record_timeline(
                "error",
                message or type(exc).__name__,
                session_id=session_id,
                metadata={"retry_prompt": text},
            )
            if message:
                self._show_turn_result("error", message, None)
        except Exception as exc:
            self.product.record_timeline(
                "error",
                f"{type(exc).__name__}: {exc}",
                session_id=session_id,
                metadata={"retry_prompt": text},
            )
            self._show_turn_result("error", f"{type(exc).__name__}: {exc}", None)
        finally:
            if loop is not None:
                try:
                    loop.close()
                except Exception:
                    pass
            self._turn_busy.clear()
            self._schedule_next_turn()

    def _show_turn_result(self, kind: str, content: str, extra: Any) -> None:
        """Display a turn result above the prompt without requiring user input.
        Uses prompt_toolkit's in_terminal to suspend the prompt, display output,
        and re-render the prompt below the result."""
        async def _display() -> None:
            try:
                async with in_terminal():
                    for _ in range(TURN_TOP_PADDING_LINES):
                        self.console.print()
                    if kind == "ok":
                        if extra:
                            self.console.print(extra)
                        self.console.print(assistant_panel(content))
                    else:
                        self.console.print(error_panel(content))
                    for _ in range(TURN_BOTTOM_PADDING_LINES):
                        self.console.print()
            except Exception:
                pass

        try:
            app = get_app()
            if app is not None and app.loop is not None and app._is_running:
                asyncio.run_coroutine_threadsafe(_display(), app.loop)
                return
        except Exception:
            pass

        # Fallback: no app running, display directly
        for _ in range(TURN_TOP_PADDING_LINES):
            self.console.print()
        if kind == "ok":
            if extra:
                self.console.print(extra)
            self.console.print(assistant_panel(content))
        else:
            self.console.print(error_panel(content))
        for _ in range(TURN_BOTTOM_PADDING_LINES):
            self.console.print()

    def _schedule_next_turn(self) -> None:
        """Start the next queued turn if any."""
        try:
            item = self._turn_queue.get_nowait()
            if item is None:
                return
            sid, msg = item
            self._turn_busy.set()
            self._run_turn_thread(sid, msg)
        except queue.Empty:
            pass

    def run_interactive(self) -> None:
        set_terminal_title("Donovan Agent")
        if not self.config.app.first_run_complete:
            run_setup_wizard(self.manager, self.console, launch_note=True)
            self.refresh()
        self._check_workspace_trust()
        print_startup(self.console, self.config)
        self.start_session()
        session = create_prompt_session(self.paths.history_file)
        workspace = self.config.app.default_workspace

        def _toolbar() -> str:
            mode = self.config.app.permission_mode
            backend = (self.agent.backend_manager.active_name
                       if self.agent is not None else "local")
            cw = max(self._context_window, 1)
            pct = min(100, round((self._context_tokens / cw) * 100, 1))
            left = f"  {workspace}  | Mode: {mode}  Backend: {backend}"
            right = f"  Context: {pct}%  "
            pad = max(1, shutil.get_terminal_size().columns - len(left) - len(right))
            return f"{left}{' ' * pad}{right}"

        def _padded_toolbar() -> list[tuple[str, str]]:
            return [
                ("", "\n" * FOOTER_TOP_GAP_LINES),
                ("", _toolbar()),
            ]

        # Set a global asyncio exception handler to suppress Playwright tracebacks
        try:
            main_loop = asyncio.get_running_loop()
            main_loop.set_exception_handler(lambda loop, ctx: None)
        except RuntimeError:
            pass

        while True:
            try:
                self._asyncio_cleanup()
                raw = session.prompt(
                    HTML("\n" * PROMPT_TOP_PADDING_LINES + "<prompt>&gt; </prompt>"),
                    bottom_toolbar=_padded_toolbar,
                    rprompt="  ",
                    refresh_interval=0.2,
                )
                text = raw.strip()
                if len(text) > 1 and text[0] == text[-1] and text[0] in {"'", '"'}:
                    text = text[1:-1].strip()
            except KeyboardInterrupt:
                self.console.print("[dim]Interrupted. Type /exit to quit.[/dim]")
                continue
            except EOFError:
                self.console.print()
                return
            if not text:
                continue
            if text.startswith("/"):
                keep_going = self.handle_slash_command(text)
                if not keep_going:
                    return
                continue
            auto_configured = self.product.auto_configure(text)
            if auto_configured is not None:
                self._print_product_result(auto_configured)
                continue
            if self._turn_busy.is_set():
                self.console.print("[yellow]Queueing message — agent is busy processing, will handle it next.[/yellow]")
                assert self.session_id is not None
                self._turn_queue.put((self.session_id, text))
                continue
            try:
                assert self.session_id is not None
                for _ in range(STATUS_TOP_PADDING_LINES):
                    self.console.print()
                self._status_word = "Thinking"
                self._turn_started_at = time.monotonic()
                self._turn_busy.set()
                self._run_turn_thread(self.session_id, text)
            except DonovanAgentError as exc:
                self._turn_busy.clear()
                self.console.print(error_panel(str(exc)))

    def one_shot(self, prompt: str) -> str:
        set_terminal_title("Donovan Agent")
        if not self.config.app.first_run_complete:
            raise ProviderError("DonovanAgent is not configured yet. Run `DonovanAgent setup` first.")
        session_id = self.start_session()
        agent = self.ensure_agent()
        # Resolve @ MCP resource mentions
        resolved_text, attachments = resolve_mentions(prompt, agent.mcp_manager)
        full_text = resolved_text
        if attachments:
            full_text = resolved_text + format_attachments(attachments)
        return agent.run_turn(session_id, full_text)

    def handle_slash_command(self, command: str) -> bool:
        parts = command.split(maxsplit=2)
        name = parts[0].lower()
        rest = command[len(parts[0]):].strip()
        if name in {"/exit", "/quit"}:
            return False

        # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Existing commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬
        if name == "/help":
            self.console.print(HELP_PANEL)
        elif name == "/model":
            if rest == "set":
                configure_model(self.manager, self.console, self.config)
                self.manager.save(self.config)
                self.refresh()
            else:
                self.console.print(
                    info_panel(f"{self.config.provider.active}: {self.config.provider.model}")
                )
        elif name == "/tools":
            self.console.print(tools_table(self.registry.rows()))
        elif name == "/search":
            self.run_direct_search(rest)
        elif name == "/workspace":
            self.handle_workspace(rest)
        elif name == "/mode":
            self.handle_mode(rest)
        elif name == "/new":
            self.start_session()
            self.console.print(info_panel("Started a new session."))
        elif name == "/sessions":
            self.console.print(sessions_table(self.db.list_sessions()))
        elif name == "/skills":
            self.handle_skill_list()
        elif name == "/skill_add":
            self.handle_skill_add(rest)
        elif name == "/skill_list":
            self.handle_skill_list()
        elif name == "/skill_remove":
            self.handle_skill_remove(rest)
        elif name == "/resume":
            self.handle_resume()
        elif name == "/history":
            assert self.session_id is not None
            rows = self.db.recent_messages(self.session_id, limit=12)
            self.console.print(
                Panel(
                    "\n\n".join(f"{r['role']}: {r['content']}" for r in rows)
                )
            )
        elif name == "/clear":
            clear()
        elif name == "/doctor":
            if rest == "ai":
                self._handle_product_command("doctor-ai", "")
            else:
                run_doctor(self.manager, self.console)
        elif name == "/config":
            self.console.print(config_table(self.manager.sanitized(self.config)))
        elif name in ("/context",):
            self._handle_context_cmd(rest)

        # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ New feature commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬
        elif name in ("/activity",):
            self._handle_activity(rest)
        elif name in ("/think",):
            self._handle_think(rest)
        elif name in ("/plan",):
            self._handle_plan(rest)
        elif name in ("/memory",):
            self._handle_memory(rest)
        elif name in ("/recall",):
            self._handle_recall(rest)
        elif name in ("/context",):
            self._handle_context(rest)
        elif name in ("/backend",):
            self._handle_backend(rest)
        elif name in ("/browser",):
            self._handle_browser(rest)
        elif name in ("/checkpoint",):
            self._handle_checkpoint(rest)
        elif name in ("/schedule",):
            self._handle_schedule(rest)
        elif name in ("/mcp",):
            self._handle_mcp_cmd(rest)
        elif name in ("/followup",):
            self._handle_followup(rest)
        elif name in ("/subagent", "/subagents"):
            self._handle_subagent(rest)
        elif name == "/skill":
            self._handle_skill(rest)
        elif name in ("/skill_drafts", "/skill drafts"):
            self._handle_skill_drafts()
        elif name in ("/skill_approve",):
            self._handle_skill_approve(rest)
        elif name in ("/skill_reject",):
            self._handle_skill_reject(rest)
        elif name in ("/skill_show",):
            self._handle_skill_show(rest)
        elif name in ("/skill_use",):
            self._handle_skill_use(rest)
        elif name in ("/skill_disable",):
            self._handle_skill_disable(rest)
        elif name in ("/skill_enable",):
            self._handle_skill_disable(rest, enable=True)
        elif name in ("/skill_delete",):
            self._handle_skill_delete(rest)
        elif name == "/skill_learn":
            self._handle_skill_learn()
        elif name in {
            "/timeline", "/replay", "/recipe", "/sandbox", "/profile",
            "/contract", "/eval", "/graph", "/impact", "/pr", "/watch",
            "/inbox", "/marketplace", "/memory-citations", "/recover",
            "/router", "/stats", "/handoff", "/doctor-ai",
            "/workspace-profile", "/agent-test",
        }:
            self._handle_product_command(name[1:], rest)
        else:
            self.console.print(error_panel(f"Unknown slash command: {name}"))
        return True

    # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ New slash command handlers ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

    def _print_product_result(self, result: ProductResult) -> None:
        self.console.print(info_panel(result.body, title=result.title))
        if result.prompt:
            self._run_product_prompt(result.prompt)

    def _run_product_prompt(self, prompt: str) -> None:
        if not prompt.strip():
            return
        if self.session_id is None:
            self.start_session()
        assert self.session_id is not None
        if self._turn_busy.is_set():
            self._turn_queue.put((self.session_id, prompt))
            self.console.print(info_panel("Task queued."))
            return
        self._status_word = "Thinking"
        self._turn_started_at = time.monotonic()
        self._turn_busy.set()
        self._run_turn_thread(self.session_id, prompt)

    def _handle_product_command(self, command: str, rest: str) -> None:
        self.product.set_workspace(self.config.app.default_workspace)
        cmd = rest.strip()
        if command == "timeline":
            result = self.product.timeline()
        elif command == "replay":
            result = self.product.replay(self.session_id if cmd in {"", "last"} else cmd)
        elif command == "recipe":
            parts = cmd.split(maxsplit=2)
            if parts and parts[0] == "create" and len(parts) >= 3:
                result = self.product.create_recipe(parts[1], parts[2])
            elif parts and parts[0] == "run" and len(parts) >= 2:
                prompt = self.product.get_recipe_prompt(parts[1])
                result = ProductResult("Recipe", f"Running recipe: {parts[1]}", prompt=prompt) if prompt else ProductResult("Recipe", f"Recipe not found: {parts[1]}")
            else:
                result = self.product.list_recipes()
        elif command == "sandbox":
            if cmd.startswith("start"):
                result = self.product.start_sandbox(cmd[5:].strip() or "sandbox")
            elif cmd.startswith("run "):
                result = self.product.sandbox_run(cmd[4:].strip())
            elif cmd == "diff":
                result = self.product.sandbox_diff()
            elif cmd == "promote":
                result = self.product.close_sandboxes(promote=True)
            elif cmd == "discard":
                result = self.product.close_sandboxes(promote=False)
            else:
                result = self.product.sandbox_status()
        elif command == "profile":
            result = self.product.profile(cmd)
        elif command == "contract":
            result = self.product.create_contract(cmd) if cmd else self.product.list_contracts()
        elif command == "eval":
            result = self.product.evals(cmd)
        elif command == "graph":
            result = self.product.build_graph() if cmd == "build" else self.product.graph_query(cmd or "")
        elif command == "impact":
            ctx = detect_project_context(self.config.app.default_workspace)
            result = self.product.impact(cmd, ctx.get("test_commands", []))
        elif command == "pr":
            result = self.product.pr_draft(cmd) if cmd else ProductResult("PR Draft", "Usage: /pr <goal>")
        elif command == "watch":
            result = self.product.watch(cmd)
        elif command == "inbox":
            result = self.product.inbox(cmd)
        elif command == "marketplace":
            result = self.product.marketplace(cmd, _skill_dir(self.config.app.default_workspace))
        elif command == "memory-citations":
            result = self.product.memory_citations(cmd)
        elif command == "recover":
            result = self.product.recover(cmd)
        elif command == "router":
            result = self.product.router(cmd)
        elif command == "stats":
            result = self.product.stats()
        elif command == "handoff":
            result = self.product.handoff(self.session_id)
        elif command == "doctor-ai":
            result = self.product.doctor_ai()
        elif command == "workspace-profile":
            result = self.product.workspace_profile(cmd)
        elif command == "agent-test":
            result = self.product.agent_test(cmd)
        else:
            result = ProductResult("Product Command", f"Unknown command: {command}")
        self._print_product_result(result)

    def _handle_activity(self, rest: str) -> None:
        cmd = rest.strip().lower()
        if cmd == "on":
            self.config.activity_stream.enabled = True
            self._activity_enabled = True
            self.console.print(info_panel("Activity stream enabled."))
        elif cmd == "off":
            self.config.activity_stream.enabled = False
            self._activity_enabled = False
            self.console.print(info_panel("Activity stream disabled."))
        elif cmd == "compact":
            self.config.activity_stream.compact = True
            self.console.print(info_panel("Activity stream set to compact mode."))
        elif cmd == "verbose":
            self.config.activity_stream.compact = False
            self.console.print(info_panel("Activity stream set to verbose mode."))
        else:
            status = "on" if self.config.activity_stream.enabled else "off"
            mode = "compact" if self.config.activity_stream.compact else "verbose"
            self.console.print(info_panel(
                f"Activity stream: {status}\nMode: {mode}\n"
                f"Show timers: {self.config.activity_stream.show_timers}\n"
                f"Show results: {self.config.activity_stream.show_result_summaries}"
            ))

    def _handle_think(self, rest: str) -> None:
        cmd = rest.strip().lower()
        if cmd == "on":
            self.config.thinking.enabled = True
            if self.agent:
                self.agent.thinking.enabled = True
            self.console.print(info_panel("Thinking summaries enabled."))
        elif cmd == "off":
            self.config.thinking.enabled = False
            if self.agent:
                self.agent.thinking.enabled = False
            self.console.print(info_panel("Thinking summaries disabled."))
        elif cmd == "status":
            self.console.print(info_panel(
                f"Thinking: {'on' if self.config.thinking.enabled else 'off'}\n"
                f"Safe summaries: {self.config.thinking.show_safe_summaries}\n"
                f"Provider reasoning: {self.config.thinking.show_provider_reasoning_if_available}"
            ))
        else:
            self.console.print(error_panel("Usage: /think on | off | status"))

    def _handle_plan(self, rest: str) -> None:
        cmd = rest.strip()
        if not cmd:
            # Toggle plan mode display
            self.console.print(info_panel(
                "Plan mode:\n"
                "  /plan <task> - Create a plan\n"
                "  /plan on     - Enable plan mode\n"
                "  /plan off    - Disable plan mode\n"
                "  /plan show   - Show current plan\n"
                "  /plan approve - Approve the plan\n"
                "  /plan cancel  - Cancel the plan\n"
                "  /plan edit    - Edit the plan (coming soon)"
            ))
            return
        if cmd == "on":
            self.config.plan.default_for_complex_tasks = True
            self.console.print(info_panel("Plan mode enabled for complex tasks."))
        elif cmd == "off":
            self.config.plan.default_for_complex_tasks = False
            self.console.print(info_panel("Plan mode disabled."))
        elif cmd == "show":
            if self.agent and self.agent.plan_manager.current_plan:
                plan = self.agent.plan_manager.current_plan
                self.console.print(info_panel(
                    f"Task: {plan.task}\n"
                    f"Status: {plan.status}\n"
                    f"Items:\n" +
                    "\n".join(
                        f"  [{item.status}] {item.title}"
                        for item in sorted(plan.items, key=lambda x: x.item_order)
                    )
                ))
            else:
                self.console.print(info_panel("No active plan."))
        elif cmd == "approve" or cmd == "y":
            if self.agent and self.agent.plan_manager.current_plan:
                self.agent.plan_manager.approve()
                self.console.print(info_panel("Plan approved! Executing now..."))
        elif cmd == "cancel":
            if self.agent and self.agent.plan_manager.current_plan:
                self.agent.plan_manager.cancel()
                self.console.print(info_panel("Plan cancelled."))
        else:
            # Create new plan
            if self.agent:
                plan = self.agent.plan_manager.create_plan(
                    task=cmd,
                    items=[{"title": cmd}],
                    session_id=self.session_id,
                )
                self.console.print(info_panel(
                    f"Plan created for: {cmd}\n"
                    "Use /plan approve to execute or /plan cancel to cancel."
                ))

    def _handle_memory(self, rest: str) -> None:
        cmd = rest.strip()
        if not cmd:
            cfg = self.config.memory
            self.console.print(info_panel(
                f"Memory enabled: {cfg.enabled}\n"
                f"Auto recall: {cfg.auto_recall}\n"
                f"Auto summarize: {cfg.auto_summarize_sessions}\n"
                f"Project context: {cfg.project_context_enabled}\n"
                f"Max recall items: {cfg.max_recall_items}"
            ))
            return
        if cmd.startswith("search "):
            query = cmd[7:].strip()
            if self.agent:
                results = self.agent.memory_manager.search(query)
                if results:
                    for r in results:
                        self.console.print(info_panel(
                            f"[{r.get('memory_type', 'memory')}] {r.get('title', '')}\n"
                            f"{r.get('summary', '')[:300]}",
                            title=f"Memory #{r.get('id', '')}"
                        ))
                else:
                    self.console.print(info_panel("No memories found."))
        elif cmd.startswith("add "):
            text = cmd[4:].strip()
            if self.agent:
                self.agent.memory_manager.add_memory(
                    memory_type="user_preference",
                    title="Manual entry",
                    content=text,
                    source="user",
                )
                self.console.print(info_panel("Memory saved."))
        elif cmd.startswith("forget "):
            try:
                mem_id = int(cmd[7:].strip())
                if self.agent:
                    self.agent.memory_manager.delete(mem_id)
                    self.console.print(info_panel(f"Memory #{mem_id} deleted."))
            except ValueError:
                self.console.print(error_panel("Usage: /memory forget <id>"))
        elif cmd == "summarize" or cmd == "summary":
            if self.session_id and self.agent:
                from donovanagent.memory.summaries import generate_session_summary
                msgs = self.db.recent_messages(self.session_id, limit=24)
                summary = generate_session_summary(self.db, self.session_id, msgs)
                self.console.print(info_panel(summary, title="Session Summary"))
        else:
            self.console.print(error_panel(
                "Usage:\n  /memory\n  /memory search <query>\n"
                "  /memory add <text>\n  /memory forget <id>\n"
                "  /memory summarize"
            ))

    def _handle_recall(self, rest: str) -> None:
        query = rest.strip()
        if not query:
            self.console.print(error_panel("Usage: /recall <query>"))
            return
        if self.agent:
            results = self.agent.memory_manager.search(query)
            if results:
                self.console.print(info_panel(
                    "\n".join(
                        f"- [{r.get('memory_type', 'memory')}] {r.get('title', '')}: "
                        f"{r.get('summary', '')[:150]}"
                        for r in results
                    ),
                    title=f"Recall: {query}"
                ))
            else:
                self.console.print(info_panel(f"No memories found for: {query}"))

    def _handle_context(self, rest: str) -> None:
        cmd = rest.strip()
        if cmd in ("project",):
            ws = self.config.app.default_workspace
            ctx = detect_project_context(ws)
            if any(ctx.values()):
                lines = [
                    f"Language: {ctx.get('language', 'unknown')}",
                    f"Package manager: {ctx.get('package_manager', 'unknown')}",
                    f"Test commands: {', '.join(ctx.get('test_commands', []))}",
                    f"Build commands: {', '.join(ctx.get('build_commands', []))}",
                ]
                self.console.print(info_panel("\n".join(lines), title=f"Project Context"))
            else:
                self.console.print(info_panel("No project context detected."))
        elif cmd == "refresh":
            ws = self.config.app.default_workspace
            if self.agent:
                self.agent.memory_manager.generate_project_context(ws)
            self.console.print(info_panel("Project context refreshed."))
        else:
            self.console.print(error_panel("Usage: /context project | /context refresh"))

    def _setup_ssh_interactive(self) -> None:
        """Prompt user for SSH connection details and save to config."""
        from rich.panel import Panel
        from rich.prompt import IntPrompt, Prompt

        self.console.print()
        self.console.print(Panel(
            "Enter the SSH connection details for the remote host.\n"
            "Press Enter to accept defaults shown in brackets.",
            title="SSH Setup", border_style="cyan"
        ))
        host = Prompt.ask("Host", default="")
        if not host:
            self.console.print(error_panel("SSH host is required. Cancelled."))
            return
        port = IntPrompt.ask("Port", default=22)
        username = Prompt.ask("Username", default="")
        key_path = Prompt.ask("SSH key path (leave blank for password auth)", default="")
        remote_workspace = Prompt.ask("Remote workspace", default="/tmp/donovan")

        self.config.execution.ssh.host = host
        self.config.execution.ssh.port = port
        self.config.execution.ssh.username = username or None
        self.config.execution.ssh.key_path = key_path or None
        self.config.execution.ssh.remote_workspace = remote_workspace
        self.config.save()
        self.console.print(info_panel(f"SSH configuration saved: {username}@{host}:{port}"))

    def _handle_backend(self, rest: str) -> None:
        cmd = rest.strip().lower()
        if not cmd:
            if self.agent:
                self.console.print(info_panel(
                    f"Active backend: {self.agent.backend_manager.active_name}\n"
                    "Available: local, docker, ssh\n"
                    "Switch: /backend local|docker|ssh"
                ))
            return
        if cmd == "ssh":
            ssh_cfg = self.config.execution.ssh
            if not ssh_cfg.host:
                self._setup_ssh_interactive()
                if not self.config.execution.ssh.host:
                    return
        if cmd in ("local", "docker", "ssh"):
            if self.agent:
                try:
                    name = self.agent.backend_manager.switch(cmd)
                    self.console.print(info_panel(f"Switched to backend: {name}"))
                except Exception as exc:
                    self.console.print(error_panel(str(exc)))
            else:
                self.config.execution.backend = cmd  # type: ignore[assignment]
                self.console.print(info_panel(f"Backend set to {cmd} (will take effect next session)"))
        else:
            self.console.print(error_panel("Usage: /backend [local|docker|ssh]"))

    def _handle_browser(self, rest: str) -> None:
        cmd = rest.strip()
        if not cmd:
            self.console.print(error_panel(
                "Browser commands:\n"
                "  /browser open <url>\n"
                "  /browser companion setup [chrome|edge|brave|vivaldi|opera|arc|chromium|firefox]\n"
                "  /browser companion start|status|active|snapshot|tabs|use|click|type|screenshot\n"
                "  /browser connect [cdp_endpoint] [tab]\n"
                "  /browser tabs\n"
                "  /browser use <tab-index|title|url>\n"
                "  /browser close\n"
                "  /browser minimize\n"
                "  /browser screenshot\n"
                "  /browser text\n"
                "  /browser url\n"
                "  /browser back\n"
                "  /browser reload"
            ))
            return
        if cmd.startswith("open "):
            url = cmd[5:].strip()
            if self.agent:
                try:
                    self.agent.browser_service.open(url)
                    self.console.print(info_panel(f"Opened: {url}"))
                except Exception as exc:
                    self.console.print(error_panel(str(exc)))
            else:
                self.console.print(error_panel("Agent not initialized."))
        elif cmd == "companion" or cmd.startswith("companion "):
            self._handle_browser_companion(cmd[len("companion"):].strip())
        elif cmd == "connect" or cmd.startswith("connect "):
            if self.agent:
                parts = cmd.split(maxsplit=2)
                endpoint = parts[1] if len(parts) >= 2 and parts[1].startswith("http") else None
                tab = parts[2] if endpoint and len(parts) >= 3 else parts[1] if len(parts) >= 2 and not endpoint else None
                try:
                    self.agent.browser_service.connect_existing(cdp_endpoint=endpoint, tab=tab)
                    self.console.print(info_panel(
                        f"Connected to existing tab:\n{self.agent.browser_service.current_url()}"
                    ))
                except Exception as exc:
                    self.console.print(error_panel(str(exc)))
            else:
                self.console.print(error_panel("Agent not initialized."))
        elif cmd == "tabs":
            if self.agent:
                try:
                    tabs = self.agent.browser_service.list_tabs()
                    if tabs:
                        self.console.print(info_panel(
                            "\n".join(f"{tab['index']}. {tab['title']} -> {tab['url']}" for tab in tabs),
                            title="Browser Tabs",
                        ))
                    else:
                        self.console.print(info_panel("No browser tabs exposed."))
                except Exception as exc:
                    self.console.print(error_panel(str(exc)))
            else:
                self.console.print(error_panel("Agent not initialized."))
        elif cmd.startswith("use "):
            if self.agent:
                tab_text = cmd[4:].strip()
                tab = int(tab_text) if tab_text.isdigit() else tab_text
                try:
                    self.agent.browser_service.use_tab(tab)
                    self.console.print(info_panel(
                        f"Using tab:\n{self.agent.browser_service.current_url()}"
                    ))
                except Exception as exc:
                    self.console.print(error_panel(str(exc)))
            else:
                self.console.print(error_panel("Agent not initialized."))
        elif cmd == "close":
            if self.agent:
                self.agent.browser_service.close()
                self.console.print(info_panel("Browser closed."))
        elif cmd == "minimize":
            if self.agent:
                self.agent.browser_service.minimize()
                self.console.print(info_panel("Browser minimized."))
        elif cmd == "screenshot":
            if self.agent:
                try:
                    path = self.agent.browser_service.screenshot()
                    self.console.print(info_panel(f"Screenshot saved to: {path}"))
                except Exception as exc:
                    self.console.print(error_panel(str(exc)))
        elif cmd == "text":
            if self.agent and self.agent.browser_service.is_open:
                self.console.print(self.agent.browser_service.get_text()[:2000])
            else:
                self.console.print(error_panel("Browser is not open."))
        elif cmd == "url":
            if self.agent and self.agent.browser_service.is_open:
                self.console.print(info_panel(self.agent.browser_service.current_url()))
            else:
                self.console.print(error_panel("Browser is not open."))
        elif cmd == "back":
            if self.agent and self.agent.browser_service.is_open:
                self.agent.browser_service.back()
                self.console.print(info_panel("Navigated back."))
        elif cmd == "reload":
            if self.agent and self.agent.browser_service.is_open:
                self.agent.browser_service.reload()
                self.console.print(info_panel("Page reloaded."))
        else:
            self.console.print(error_panel(f"Unknown browser command: {cmd}"))

    def _handle_browser_companion(self, rest: str) -> None:
        if not self.agent:
            self.console.print(error_panel("Agent not initialized."))
            return
        companion = self.agent.browser_companion
        parts = rest.split(maxsplit=2)
        cmd = parts[0] if parts else "status"
        try:
            if cmd == "setup":
                browser = parts[1] if len(parts) >= 2 else None
                self.console.print(info_panel(companion.setup_instructions(browser), title="Browser Companion Setup"))
            elif cmd == "start":
                companion.start()
                self.console.print(info_panel("Browser companion server started.", title="Browser Companion"))
            elif cmd == "status":
                status = companion.status()
                self.console.print(info_panel("\n".join(f"{k}: {v}" for k, v in status.items()), title="Browser Companion"))
            elif cmd == "active":
                result = companion.command("active_tab")
                self.console.print(info_panel(json.dumps(result, indent=2), title="Active Tab"))
            elif cmd == "snapshot":
                result = companion.command("snapshot")
                self.console.print(info_panel(str(result.get("text") or result.get("error") or "")[:4000], title="Active Tab Snapshot"))
            elif cmd == "tabs":
                result = companion.command("list_tabs")
                tabs = result.get("tabs") or []
                self.console.print(info_panel(
                    "\n".join(f"{tab.get('index')}. {tab.get('title')} -> {tab.get('url')}" for tab in tabs) or str(result.get("error", "No tabs.")),
                    title="Browser Companion Tabs",
                ))
            elif cmd == "use" and len(parts) >= 2:
                result = companion.command("use_tab", tab=parts[1])
                self.console.print(info_panel(json.dumps(result, indent=2), title="Browser Companion"))
            elif cmd == "click" and len(parts) >= 2:
                result = companion.command("click", selector=parts[1])
                self.console.print(info_panel(json.dumps(result, indent=2), title="Browser Companion"))
            elif cmd == "type" and len(parts) >= 3:
                result = companion.command("type", selector=parts[1], text=parts[2])
                self.console.print(info_panel(json.dumps(result, indent=2), title="Browser Companion"))
            elif cmd == "screenshot":
                result = companion.command("screenshot")
                self.console.print(info_panel("Captured." if result.get("success") else str(result.get("error")), title="Browser Companion"))
            else:
                self.console.print(error_panel("Usage: /browser companion setup [browser]|start|status|active|snapshot|tabs|use|click|type|screenshot"))
        except Exception as exc:
            self.console.print(error_panel(str(exc)))

    def _handle_checkpoint(self, rest: str) -> None:
        cmd = rest.strip()
        if not cmd:
            self.console.print(error_panel(
                "Checkpoint commands:\n"
                "  /checkpoint list\n"
                "  /checkpoint show <id>\n"
                "  /checkpoint diff <id>\n"
                "  /checkpoint restore <id>\n"
                "  /checkpoint delete <id>"
            ))
            return
        if not self.agent:
            self.console.print(error_panel("Agent not initialized. Send a message or configure a model with /model set first."))
            return
        if cmd == "list":
            cps = self.agent.checkpoints.list()
            if cps:
                for cp in cps[:10]:
                    self.console.print(info_panel(
                        f"ID: {cp.id}\n"
                        f"Reason: {cp.reason}\n"
                        f"Files: {len(cp.affected_paths)}\n"
                        f"Created: {cp.created_at}",
                        title="Checkpoint"
                    ))
            else:
                self.console.print(info_panel("No checkpoints found."))
        elif cmd.startswith("show "):
            cp_id = cmd[5:].strip()
            cp = self.agent.checkpoints.get(cp_id)
            if cp:
                self.console.print(info_panel(
                    f"ID: {cp.id}\n"
                    f"Reason: {cp.reason}\n"
                    f"Tool: {cp.tool_name}\n"
                    f"Affected: {', '.join(cp.affected_paths[:5])}\n"
                    f"Created: {cp.created_at}\n"
                    f"Restored: {cp.restored_at or 'never'}",
                    title="Checkpoint Details"
                ))
            else:
                self.console.print(error_panel(f"Checkpoint not found: {cp_id}"))
        elif cmd.startswith("diff "):
            cp_id = cmd[5:].strip()
            diff = self.agent.checkpoints.diff(cp_id)
            if diff:
                self.console.print(info_panel(diff[:2000], title="Git Diff Before"))
            else:
                self.console.print(info_panel("No diff available."))
        elif cmd.startswith("restore "):
            cp_id = cmd[8:].strip()
            pre = self.agent.checkpoints.restore(cp_id)
            if pre:
                self.console.print(info_panel(
                    f"Restored checkpoint {cp_id}.\n"
                    f"A pre-restore checkpoint was created: {pre.id}"
                ))
            else:
                self.console.print(error_panel(f"Failed to restore: {cp_id}"))
        elif cmd.startswith("delete "):
            cp_id = cmd[7:].strip()
            self.agent.checkpoints.delete(cp_id)
            self.console.print(info_panel(f"Checkpoint {cp_id} deleted."))

    def _handle_schedule(self, rest: str) -> None:
        cmd = rest.strip()
        if not cmd:
            if not self.agent:
                self.console.print(error_panel("Agent not initialized. Send a message or configure a model with /model set first."))
                return
            tasks = self.agent.scheduler.list_tasks()
            if tasks:
                for t in tasks:
                    self.console.print(info_panel(
                        f"Name: {t.name}\n"
                        f"Type: {t.schedule_type}\n"
                        f"Enabled: {t.enabled}\n"
                        f"Next run: {t.next_run_at}\n"
                        f"Last status: {t.last_status}",
                        title=f"Scheduled: {t.id}"
                    ))
            else:
                self.console.print(info_panel("No scheduled tasks."))
            return
        if not self.agent:
            self.console.print(error_panel("Agent not initialized. Send a message or configure a model with /model set first."))
            return
        if cmd == "list":
            self._handle_schedule("")
        elif cmd.startswith("remove "):
            task_id = cmd[7:].strip()
            self.agent.scheduler.remove_task(task_id)
            self.console.print(info_panel(f"Task {task_id} removed."))
        elif cmd.startswith("pause "):
            task_id = cmd[6:].strip()
            self.agent.scheduler.pause_task(task_id)
            self.console.print(info_panel(f"Task {task_id} paused."))
        elif cmd.startswith("resume "):
            task_id = cmd[7:].strip()
            self.agent.scheduler.resume_task(task_id)
            self.console.print(info_panel(f"Task {task_id} resumed."))
        elif cmd.startswith("run "):
            task_id = cmd[4:].strip()
            result = self.agent.scheduler.run_now(task_id)
            if result:
                self.console.print(info_panel(result[:500], title="Scheduled Run"))
            else:
                self.console.print(error_panel(f"Task not found: {task_id}"))
        else:
            self.console.print(error_panel(
                "Schedule commands:\n"
                "  /schedule list\n"
                "  /schedule remove <id>\n"
                "  /schedule pause <id>\n"
                "  /schedule resume <id>\n"
                "  /schedule run <id>"
            ))

    def _handle_mcp_cmd(self, rest: str) -> None:
        """Handle /mcp slash command and subcommands."""
        if not self.agent:
            self.console.print(error_panel("Agent not initialized. Start a session first."))
            return

        manager = self.agent.mcp_manager
        parts = rest.strip().split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "" or cmd == "list" or cmd == "status":
            statuses = manager.list_statuses()
            self.console.print(mcp_status_panel(statuses))

        elif cmd == "connect":
            if not arg:
                self.console.print(error_panel("Usage: /mcp connect <name>"))
                return
            config_model, scope = manager.config_store.load_server(arg)
            if config_model and config_model.trust == "ask":
                if not manager.trust_store.is_trusted(arg, config_model, scope):
                    from rich.prompt import Confirm as ConfirmAsk
                    env_keys = list(config_model.env.keys()) if config_model.env else None
                    headers_keys = list(config_model.headers.keys()) if config_model.headers else None
                    self.console.print(mcp_trust_prompt(
                        server_name=arg,
                        server_type=config_model.type,
                        command=config_model.command,
                        url=config_model.url,
                        args=config_model.args,
                        env_keys=env_keys,
                        headers_keys=headers_keys,
                        scope=scope,
                    ))
                    if not ConfirmAsk.ask("Trust this server?", default=False):
                        self.console.print(info_panel(f"MCP server '{arg}' not trusted."))
                        return
                    config_hash = config_model.trust_hash()
                    manager.trust_store.set_trust(arg, "trusted", scope, config_hash)
                    config_model.trust = "trusted"
                    manager.config_store.save_server(arg, config_model, scope)
            msg = manager.connect_server(arg)
            self.console.print(info_panel(msg))

        elif cmd == "disconnect":
            if not arg:
                self.console.print(error_panel("Usage: /mcp disconnect <name>"))
                return
            msg = manager.disconnect_server(arg)
            self.console.print(info_panel(msg))

        elif cmd == "restart":
            if not arg:
                self.console.print(error_panel("Usage: /mcp restart <name>"))
                return
            msg = manager.restart_server(arg)
            self.console.print(info_panel(msg))

        elif cmd == "trust":
            if not arg:
                self.console.print(error_panel("Usage: /mcp trust <name>"))
                return
            config_model, scope = manager.config_store.load_server(arg)
            if config_model:
                config_hash = config_model.trust_hash()
                manager.trust_store.set_trust(arg, "trusted", scope, config_hash)
                config_model.trust = "trusted"
                manager.config_store.save_server(arg, config_model, scope)
                self.console.print(info_panel(f"MCP server '{arg}' trusted."))
            else:
                self.console.print(error_panel(f"MCP server '{arg}' not found."))

        elif cmd == "block":
            if not arg:
                self.console.print(error_panel("Usage: /mcp block <name>"))
                return
            config_model, scope = manager.config_store.load_server(arg)
            if config_model:
                config_hash = config_model.trust_hash()
                manager.trust_store.set_trust(arg, "blocked", scope, config_hash)
                config_model.trust = "blocked"
                manager.config_store.save_server(arg, config_model, scope)
                manager.disconnect_server(arg)
                self.console.print(info_panel(f"MCP server '{arg}' blocked."))
            else:
                self.console.print(error_panel(f"MCP server '{arg}' not found."))

        elif cmd == "tools":
            if not arg:
                self.console.print(error_panel("Usage: /mcp tools <name>"))
                return
            tools = manager.tool_registry.get_server_tools(arg)
            if not tools:
                self.console.print(info_panel(f"No tools for server '{arg}' or server not found."))
            else:
                self.console.print(mcp_tool_panel(tools))

        elif cmd == "resources":
            if not arg:
                self.console.print(error_panel("Usage: /mcp resources <name>"))
                return
            resources = manager.resource_registry.get_server_resources(arg)
            if not resources:
                self.console.print(info_panel(f"No resources for server '{arg}' or server not found."))
            else:
                self.console.print(mcp_resource_panel(resources))

        elif cmd == "prompts":
            if not arg:
                self.console.print(error_panel("Usage: /mcp prompts <name>"))
                return
            prompts = manager.prompt_registry.get_server_prompts(arg)
            if not prompts:
                self.console.print(info_panel(f"No prompts for server '{arg}' or server not found."))
            else:
                self.console.print(mcp_prompt_panel(prompts))

        elif cmd == "logs":
            if not arg:
                self.console.print(error_panel("Usage: /mcp logs <name>"))
                return
            status = manager.get_server_status(arg)
            if status:
                self.console.print(mcp_log_panel(status.stderr_log))
            else:
                self.console.print(info_panel(f"No logs for server '{arg}'."))

        elif cmd == "auth":
            if not arg:
                self.console.print(error_panel("Usage: /mcp auth <name>"))
                return
            config_model, _ = manager.config_store.load_server(arg)
            if config_model and config_model.oauth:
                self.console.print(info_panel(
                    f"MCP server '{arg}' has OAuth configured but the OAuth flow "
                    f"is not yet fully implemented. For now, use header/API-key based auth."
                ))
            elif config_model:
                auth_info = "Authentication: "
                if config_model.headers:
                    masked = config_model.get_display_headers()
                    auth_info += f"Headers: {masked}"
                elif config_model.env:
                    masked = config_model.get_display_env()
                    auth_info += f"Env: {masked}"
                else:
                    auth_info += "No authentication configured"
                self.console.print(info_panel(auth_info))
            else:
                self.console.print(error_panel(f"Server '{arg}' not found."))

        elif cmd == "refresh":
            msgs = manager.connect_all()
            for msg in msgs:
                self.console.print(info_panel(msg))
            if not msgs:
                self.console.print(info_panel("No MCP servers to refresh."))

        else:
            self.console.print(info_panel(
                "MCP commands:\n"
                "  /mcp              - Show server status table\n"
                "  /mcp list         - List configured servers\n"
                "  /mcp connect <n>  - Connect to a server\n"
                "  /mcp disconnect <n> - Disconnect a server\n"
                "  /mcp restart <n>  - Restart a server\n"
                "  /mcp trust <n>    - Trust a server\n"
                "  /mcp block <n>    - Block a server\n"
                "  /mcp tools <n>    - List server tools\n"
                "  /mcp resources <n> - List server resources\n"
                "  /mcp prompts <n>  - List server prompts\n"
                "  /mcp logs <n>     - Show server logs\n"
                "  /mcp auth <n>     - Show auth info\n"
                "  /mcp refresh      - Reconnect all servers"
            ))

    def _handle_followup(self, message: str) -> None:
        """Send a follow-up message to the agent while it's busy or subagents run."""
        if not message:
            self.console.print(info_panel(
                "Usage: /followup <message>\n"
                "Sends a message to the agent while subagents continue working in background.\n"
                "Use /subagents list to check subagent status."
            ))
            return
        if not self.session_id:
            self.console.print(error_panel("No active session. Send a message first."))
            return
        running = []
        if self.agent:
            running = self.agent.subagent_manager.list_active()
        ctx = ""
        if running:
            ctx = "[Active subagents: " + ", ".join(f"{s.name} ({s.id})" for s in running) + "]\n"
            self.console.print(info_panel(
                f"[bold]{len(running)} subagent(s) running:[/bold]\n" +
                "\n".join(f"  {s.name} ({s.id})" for s in running) +
                "\nFollow-up queued — result will appear when processed."
            ))
        prefixed = ctx + message if ctx else message
        self._turn_queue.put((self.session_id, prefixed))
        if not self._turn_busy.is_set():
            self._schedule_next_turn()

    def _handle_subagent(self, rest: str) -> None:
        cmd = rest.strip()
        if not cmd or cmd == "list":
            if not self.agent:
                self.console.print(error_panel("Agent not initialized. Send a message or configure a model with /model set first."))
                return
            subs = self.agent.subagent_manager.list()
            if not subs:
                self.console.print(info_panel("No subagents."))
                return
            table = Table(title="Subagents", box=box.SIMPLE_HEAVY)
            table.add_column("ID", style="bold")
            table.add_column("Role")
            table.add_column("Status")
            table.add_column("Goal")
            for s in subs:
                status_display = {
                    "pending": "[dim]Pending[/dim]",
                    "running": "[cyan]Running[/cyan]",
                    "completed": "[green]Completed[/green]",
                    "failed": "[red]Failed[/red]",
                }.get(s.status, s.status)
                goal_short = s.goal[:60] + "..." if len(s.goal) > 60 else s.goal
                table.add_row(s.id, s.name, status_display, goal_short)
            self.console.print(table)
            return

        if cmd == "on":
            self.config.subagents.enabled = True
            self.config.tools.subagents.enabled = True
            self.console.print(info_panel("Subagents enabled."))
            return
        if cmd == "off":
            self.config.subagents.enabled = False
            self.config.tools.subagents.enabled = False
            self.console.print(info_panel("Subagents disabled."))
            return

        parts = cmd.split(maxsplit=2)
        action = parts[0].lower()

        if action == "create" and len(parts) >= 3:
            role_name = parts[1].lower()
            goal = parts[2].strip("\"'")
            if role_name not in ROLE_PRESETS:
                valid = ", ".join(sorted(ROLE_PRESETS.keys()))
                self.console.print(error_panel(
                    f"Unknown role: '{role_name}'.\nValid roles: {valid}"
                ))
                return
            if not self.agent:
                self.console.print(error_panel("Agent not initialized. Send a message or configure a model with /model set first."))
                return
            if not self.agent.subagent_manager.can_spawn:
                self.console.print(error_panel(
                    "Maximum parallel subagents reached. Wait for one to complete or kill it."
                ))
                return
            sub = self.agent.subagent_manager.create_and_start(role_name, goal)
            ws_status = "Enabled" if sub.web_search_enabled else "Not configured"
            self.console.print(info_panel(
                f"Started subagent '{sub.name}' ({sub.id})\n"
                f"Role: {role_name}\n"
                f"Goal: {goal}\n"
                f"Web search: {ws_status}",
                title="Subagent Spawned"
            ))
            return

        if action == "kill" and len(parts) >= 2:
            sub_id = parts[1]
            if not self.agent:
                self.console.print(error_panel("Agent not initialized."))
                return
            killed = self.agent.subagent_manager.kill(sub_id)
            if killed:
                self.console.print(info_panel(f"Subagent {sub_id} terminated."))
            else:
                self.console.print(error_panel(f"Subagent not found: {sub_id}"))
            return

        if action == "result" and len(parts) >= 2:
            sub_id = parts[1]
            if not self.agent:
                self.console.print(error_panel("Agent not initialized."))
                return
            sub = self.agent.subagent_manager.get(sub_id)
            if not sub:
                self.console.print(error_panel(f"Subagent not found: {sub_id}"))
                return
            if sub.status == "completed":
                self.console.print(info_panel(
                    sub.result_summary or "(empty result)",
                    title=f"Result: {sub.name} ({sub.id})"
                ))
            elif sub.status == "failed":
                self.console.print(error_panel(sub.error or "Unknown error"))
            else:
                self.console.print(info_panel(f"Subagent is {sub.status}."))
            return

        self.console.print(error_panel(
            "Subagent commands:\n"
            "  /subagents list                    List all subagents\n"
            "  /subagents create <role> \"<goal>\"  Spawn a subagent\n"
            "  /subagents kill <ID>               Terminate a subagent\n"
            "  /subagents result <ID>             Show a subagent's result\n"
            "  /subagents on                      Enable subagents\n"
            "  /subagents off                     Disable subagents\n"
            "\nValid roles: " + ", ".join(sorted(ROLE_PRESETS.keys()))
        ))

    def _handle_skill(self, rest: str) -> None:
        cmd = rest.strip()
        if not cmd:
            if not self.agent:
                self.console.print(error_panel("Agent not initialized. Send a message or configure a model with /model set first."))
                return
            self._handle_skill_list()
            return
        parts = cmd.split(maxsplit=1)
        sub = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if sub == "search" and arg:
            self._handle_skill_search(arg)
        elif sub == "show" and arg:
            self._handle_skill_show(arg)
        elif sub == "use" and arg:
            self._handle_skill_use(arg)
        elif sub == "disable" and arg:
            self._handle_skill_disable(arg)
        elif sub == "enable" and arg:
            self._handle_skill_disable(arg, enable=True)
        elif sub == "delete" and arg:
            self._handle_skill_delete(arg)
        elif sub == "drafts":
            self._handle_skill_drafts()
        elif sub == "approve" and arg:
            self._handle_skill_approve(arg)
        elif sub == "reject" and arg:
            self._handle_skill_reject(arg)
        elif sub == "learn":
            self._handle_skill_learn()
        elif sub == "add" and arg:
            self.handle_skill_add(arg)
        elif sub == "list":
            self._handle_skill_list()
        elif sub == "remove" and arg:
            self.handle_skill_remove(arg)
        else:
            self.console.print(error_panel(
                "Usage: /skill search <q> | show <name> | use <name> |\n"
                "       disable <name> | enable <name> | delete <name> |\n"
                "       drafts | approve <name> | reject <name> | learn |\n"
                "       add <name> | list | remove <name>"
            ))

    def _handle_skill_search(self, query: str) -> None:
        if self.agent:
            results = self.agent.skill_manager.search(query)
            if results:
                self.console.print(info_panel(
                    "\n".join(
                        f"- {s.name} ({s.skill_type.value}, confidence: {s.confidence})"
                        for s in results
                    ),
                    title=f"Skills matching: {query}"
                ))
            else:
                self.console.print(info_panel("No matching skills."))

    def _handle_skill_show(self, name: str) -> None:
        if self.agent:
            skills = self.agent.skill_manager.load_all()
            for s in skills:
                if s.name == name:
                    self.console.print(info_panel(
                        f"{s.content[:2000]}\n\n"
                        f"Type: {s.skill_type.value}\n"
                        f"Confidence: {s.confidence}\n"
                        f"Uses: {s.usage_count}\n"
                        f"Triggers: {', '.join(s.triggers[:5])}",
                        title=s.name
                    ))
                    return
            self.console.print(error_panel(f"Skill not found: {name}"))

    def _handle_skill_use(self, name: str) -> None:
        if self.agent:
            self.console.print(info_panel(
                f"Include '{name}' in your next prompt to the agent.\n"
                "The agent automatically loads relevant skills."
            ))

    def _handle_skill_disable(self, name: str, enable: bool = False) -> None:
        action = "enabled" if enable else "disabled"
        # Skills can't be disabled in filesystem - this is a note
        self.console.print(info_panel(
            f"Skill '{name}' is managed via files. "
            f"Delete or rename the .md file to disable it."
        ))

    def _handle_skill_delete(self, name: str) -> None:
        if self.agent:
            if self.agent.skill_manager.delete_skill(name):
                self.console.print(info_panel(f"Skill '{name}' deleted."))
            else:
                self.console.print(error_panel(f"Skill not found: {name}"))

    def _handle_skill_drafts(self) -> None:
        if self.agent:
            drafts = self.agent.skill_manager.list_drafts()
            if drafts:
                self.console.print(info_panel(
                    "\n".join(
                        f"- {s.name} (confidence: {s.confidence})"
                        for s in drafts
                    ),
                    title="Skill Drafts"
                ))
            else:
                self.console.print(info_panel("No draft skills."))

    def _handle_skill_approve(self, name: str) -> None:
        if self.agent:
            if self.agent.skill_manager.promote_draft(name):
                self.console.print(info_panel(f"Skill '{name}' promoted from draft to learned."))
            else:
                self.console.print(error_panel(f"Draft not found: {name}"))

    def _handle_skill_reject(self, name: str) -> None:
        if self.agent:
            if self.agent.skill_manager.delete_skill(name):
                self.console.print(info_panel(f"Draft '{name}' rejected and deleted."))
            else:
                self.console.print(error_panel(f"Draft not found: {name}"))

    def _handle_skill_learn(self) -> None:
        self.console.print(info_panel(
            "Skill learning is automatic. The agent learns from your interactions.\n"
            "To manually add a skill: /skill_add <name>\n"
            "To review drafts: /skill drafts"
        ))

    def _handle_context_cmd(self, rest: str) -> None:
        parts = rest.strip().split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        if cmd == "set" and len(parts) > 1:
            try:
                new_window = int(parts[1])
                if new_window <= 0:
                    raise ValueError
                self.config.provider.context_window = new_window
                self._context_window = new_window
                self.manager.save(self.config)
                self.console.print(info_panel(f"Context window set to {new_window:,} tokens."))
            except ValueError:
                self.console.print(error_panel("Usage: /context set <positive_integer>"))
        else:
            cw = max(self._context_window, 1)
            pct = min(100, round((self._context_tokens / cw) * 100, 1))
            compacts = self.db.get_conversation_compacts(self.session_id or "")
            compact_info = f"\nCompactions: {len(compacts)}" if compacts else ""
            self.console.print(info_panel(
                f"Context window: {self._context_window:,} tokens\n"
                f"Current usage: {self._context_tokens:,} tokens ({pct}% used)"
                f"{compact_info}\n\n"
                "To change: /context set <token_count>\n"
                f"Default: 256,000 tokens\n"
                f"Auto-compaction: {'on' if self.config.memory.compaction_enabled else 'off'}"
            ))

    # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Original workspace, mode, resume, skill handlers ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬

    def handle_workspace(self, rest: str) -> None:
        if not rest:
            self.console.print(info_panel(
                "\n".join(self.config.security.approved_paths),
                "Approved workspaces"
            ))
            return
        if rest.startswith("add "):
            path = str(Path(rest[4:].strip()).expanduser().resolve(strict=False))
            if path not in self.config.security.approved_paths:
                self.config.security.approved_paths.append(path)
            self.manager.save(self.config)
            self.refresh()
            self.console.print(info_panel(f"Added workspace: {path}"))
        elif rest.startswith("remove "):
            path = str(Path(rest[7:].strip()).expanduser().resolve(strict=False))
            self.config.security.approved_paths = [
                item for item in self.config.security.approved_paths
                if str(Path(item).resolve(strict=False)) != path
            ]
            self.manager.save(self.config)
            self.refresh()
            self.console.print(info_panel(f"Removed workspace: {path}"))
        else:
            self.console.print(error_panel("Use /workspace add PATH or /workspace remove PATH"))

    def handle_mode(self, rest: str) -> None:
        mode = rest.strip()
        _aliases = {"full autonomy": "full_autonomy"}
        mode = _aliases.get(mode, mode)
        _display = {"full_autonomy": "full autonomy"}
        if mode not in {"readonly", "review", "workspace", "full_autonomy"}:
            self.console.print(
                error_panel("Use /mode readonly, /mode review, /mode workspace, or /mode full_autonomy")
            )
            return
        self.config.app.permission_mode = mode  # type: ignore[assignment]
        self.manager.save(self.config)
        self.refresh()
        self.console.print(info_panel(f"Permission mode is now {_display.get(mode, mode)}"))

    def handle_resume(self) -> None:
        rows = self.db.list_sessions()
        if not rows:
            self.console.print(info_panel("No sessions found."))
            return
        table = sessions_table(rows[:20])
        self.console.print(table)
        from prompt_toolkit import prompt as pt_prompt
        try:
            choice = pt_prompt("Resume session number (or ID): ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if not choice:
            return
        selected = None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(rows):
                selected = rows[idx]
        if selected is None:
            for row in rows:
                if str(row.get("id", "")).startswith(choice):
                    selected = row
                    break
        if selected is None:
            self.console.print(error_panel(f"No session matching: {choice}"))
            return
        self.session_id = str(selected["id"])
        self.agent = None
        title = selected.get("title") or selected.get("id")
        self.console.print(info_panel(f"Resumed: {title}"))

    def run_direct_search(self, query: str) -> None:
        if not query:
            self.console.print(error_panel("Usage: /search query"))
            return
        if not self.config.search.enabled or self.config.search.provider != "tavily":
            self.console.print(
                error_panel("Tavily search is not configured. Run DonovanAgent setup.")
            )
            return
        try:
            bundle = TavilySearchProvider(self.config.search).search(query, self.config.search.max_results)
        except DonovanAgentError as exc:
            self.console.print(error_panel(str(exc)))
            return
        self.console.print(search_results_table(bundle.to_dict()))

    def handle_skill_add(self, name: str) -> None:
        if not name:
            self.console.print(error_panel("Usage: /skill_add <skill_name>"))
            return
        name = name.strip().replace(" ", "_")
        skill_path = _skill_dir(self.config.app.default_workspace) / f"{name}.md"
        if skill_path.exists():
            from rich.prompt import Confirm as ConfirmAsk
            overwrite = ConfirmAsk.ask(f"Skill '{name}' already exists. Overwrite?", default=False)
            if not overwrite:
                return
        self.console.print(
            info_panel(f"Paste the instructions for skill '{name}' below and press Enter twice when done.")
        )
        lines: list[str] = []
        try:
            from prompt_toolkit import prompt as pt_prompt
            while True:
                line = pt_prompt("  ")
                if not line and (not lines or not lines[-1]):
                    break
                lines.append(line)
        except (KeyboardInterrupt, EOFError):
            self.console.print("[dim]Cancelled.[/dim]")
            return
        content = "\n".join(lines).strip()
        if not content:
            self.console.print(error_panel("No content provided. Skill not saved."))
            return
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content + "\n", encoding="utf-8")
        self.console.print(info_panel(f"Skill '{name}' saved ({len(content)} chars)"))

    def handle_skill_list(self) -> None:
        if self.agent:
            skills = self.agent.skill_manager.list_all()
            if skills:
                table = Table(title="Skills", box=box.SIMPLE_HEAVY)
                table.add_column("Name", style="bold")
                table.add_column("Type")
                table.add_column("Confidence")
                table.add_column("Uses")
                for s in skills:
                    table.add_row(
                        s.name, s.skill_type.value,
                        f"{s.confidence:.2f}", str(s.usage_count),
                    )
                self.console.print(table)
            else:
                self.console.print(info_panel("No skills found. Use /skill_add to create one."))

    def handle_skill_remove(self, name: str) -> None:
        if not name:
            self.console.print(error_panel("Usage: /skill_remove <skill_name>"))
            return
        if self.agent and self.agent.skill_manager.delete_skill(name):
            self.console.print(info_panel(f"Skill '{name}' removed."))
        else:
            self.console.print(error_panel(f"Skill '{name}' not found."))


def _load_user_tools(registry: ToolRegistry, config_dir: Path, workspace: str) -> None:
    for tool in load_user_tools(config_dir, workspace):
        registry.register(tool)


def _skill_dir(workspace: str) -> Path:
    return Path(workspace) / ".DonovanAgent" / "skills"


HELP_PANEL = Panel(
    "\n".join([
        "/help", "/model", "/model set", "/tools", "/search query",
        "/workspace", "/workspace add PATH", "/workspace remove PATH",
        "/mode readonly|review|workspace|full_autonomy",
        "/new", "/sessions", "/skills",
        "/skill_add NAME", "/skill_list", "/skill_remove NAME",
        "/skill search|show|use|disable|enable|delete|drafts|approve|reject|learn",
        "/memory", "/memory search <query>", "/memory add <text>",
        "/recall <query>", "/context", "/context set <tokens>",
        "/plan <task>", "/plan approve", "/plan cancel",
        "/think on|off|status",
        "/activity on|off|compact|verbose",
        "/backend [local|docker|ssh]",
        "/browser open|connect|tabs|use|close|minimize|screenshot|text|url|back|reload",
        "/checkpoint list|show|diff|restore|delete",
        "/schedule list|remove|pause|resume|run",
        "/subagents create <role> \"<goal>\"  Spawn a subagent",
        "/subagents list              List all subagents",
        "/subagents kill <ID>         Terminate a subagent",
        "/subagents result <ID>       Show subagent result",
        "/timeline | /replay [last|session_id]",
        "/recipe create NAME PROMPT | /recipe run NAME",
        "/sandbox start|run|diff|promote|discard",
        "/profile create|use|lock NAME",
        "/contract <goal> | /impact <query> | /graph build|QUERY",
        "/eval create NAME PROMPT | /eval run NAME",
        "/pr <goal> | /watch add TARGET | /watch check | /inbox add TASK | /inbox run",
        "/marketplace install NAME | /recover [retry] | /router auto|manual",
        "/stats | /handoff | /doctor ai | /workspace-profile | /agent-test",
        "/history", "/resume", "/clear", "/doctor", "/config", "/exit",
    ]),
    title="Slash commands",
    border_style="white",
)


def run_doctor(manager: ConfigManager, console: Console) -> bool:
    config = manager.load(create=True)
    db = MemoryDatabase(config.memory.database_path)
    table = status_table()
    ok = True

    def add(check: str, status: bool | None, details: str) -> None:
        nonlocal ok
        if status is False:
            ok = False
        label = "ok" if status is True else "warn" if status is None else "fail"
        style = "green" if status is True else "yellow" if status is None else "red"
        table.add_row(check, f"[{style}]{label}[/{style}]", details)

    platform = get_platform_info()
    add("Python", sys.version_info >= (3, 11), platform.python)
    add("OS", True, f"{platform.system} {platform.release} {platform.machine}")
    encoding_ok = platform.encoding.lower().replace("-", "") in {"utf8", "utf_8"}
    add("Terminal encoding", True if encoding_ok else None, platform.encoding)
    add("Config file", manager.paths.config_file.exists(), str(manager.paths.config_file))
    try:
        manager.paths.ensure()
        probe = manager.paths.data_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        add("Data directory writable", True, str(manager.paths.data_dir))
    except OSError as exc:
        add("Data directory writable", False, str(exc))
    try:
        db.initialize()
        add("SQLite", True, str(config.memory.database_path))
        add("SQLite FTS5", db.fts_available(), "full text search available" if db.fts_available() else "fallback LIKE search")
    except sqlite3.Error as exc:
        add("SQLite", False, str(exc))

    try:
        provider = build_provider(config)
        provider_ok, provider_details = provider.validate_connection()
        add("Provider", provider_ok, provider_details)
    except Exception as exc:
        add("Provider", False if config.provider.active != "none" else None, str(exc))

    if config.search.enabled and config.search.provider == "tavily":
        provider = TavilySearchProvider(config.search)
        search_ok, search_details = provider.validate_connection()
        add("Tavily", search_ok, search_details)
    else:
        add("Tavily", None, "not enabled")

    shell = resolve_shell()
    add("Shell backend", True, f"{shell.kind}: {shell.executable}")
    add("Git", shutil.which("git") is not None, shutil.which("git") or "not found")
    add("ripgrep", shutil.which("rg") is not None, shutil.which("rg") or "not found")
    add("Node", None if shutil.which("node") is None else True, shutil.which("node") or "optional, not found")
    workspace = Path(config.app.default_workspace)
    add("Workspace exists", workspace.exists(), str(workspace))
    approved = [Path(path).expanduser().resolve(strict=False) for path in config.security.approved_paths]
    add("Approved paths", bool(approved), "\n".join(str(path) for path in approved) or "none")

    # Feature checks
    add("Activity stream", True if config.activity_stream.enabled else None, "enabled" if config.activity_stream.enabled else "disabled")
    add("Streaming tools", True if config.agent.streaming_tools else None, "enabled" if config.agent.streaming_tools else "disabled")
    add("Skills", True if config.skills.enabled else None, "enabled" if config.skills.enabled else "disabled")
    add("Scheduler", None if not config.scheduler.enabled else True, "enabled" if config.scheduler.enabled else "disabled")
    add("Checkpoints", True if config.checkpoints.enabled else None, "enabled" if config.checkpoints.enabled else "disabled")

    # Browser automation
    if config.browser.enabled:
        try:
            import playwright
            add("Browser automation", True, "Playwright available")
        except ImportError:
            add("Browser automation", None, "Playwright not installed. Run: pip install playwright && playwright install")
    else:
        add("Browser automation", None, "disabled")

    # Count registered tools
    from donovanagent.tools.registry import build_default_registry
    try:
        registry = build_default_registry(config)
        tool_count = len(registry.list())
        add("Registered tools", True, f"{tool_count} tools available")
    except Exception:
        pass

    console.print(table)
    return ok


def search_results_table(bundle: dict[str, object]) -> Table:
    table = Table(title=f"Search: {bundle.get('query')}", box=box.SIMPLE_HEAVY)
    table.add_column("#")
    table.add_column("Title")
    table.add_column("URL")
    table.add_column("Snippet")
    if bundle.get("answer"):
        table.caption = str(bundle["answer"])
    for index, item in enumerate(bundle.get("results", []), start=1):  # type: ignore[arg-type]
        row = item if isinstance(item, dict) else {}
        table.add_row(str(index), str(row.get("title", "")), str(row.get("url", "")), str(row.get("content", ""))[:300])
    return table


def skills_table(rows: list[dict[str, object]]) -> Table:
    table = Table(title="Learned Skills", box=box.SIMPLE_HEAVY)
    table.add_column("Name", style="bold")
    table.add_column("Uses")
    table.add_column("Description")
    table.add_column("Updated")
    for row in rows:
        table.add_row(
            str(row.get("name", "")),
            str(row.get("uses", 0)),
            str(row.get("description", "")),
            str(row.get("updated_at", "")),
        )
    return table
