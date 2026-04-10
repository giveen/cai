"""Full-screen config modal screens for the CAI TUI."""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.screen import ModalScreen
from textual import on
from textual.widgets import Static, Button, Input, ListView, ListItem, Label


# ---------------------------------------------------------------------------
# Known provider catalogue
# ---------------------------------------------------------------------------
_KNOWN_PROVIDERS: list[dict] = [
    # ── Cloud — model developers ─────────────────────────────────────────────
    {
        "key": "openai", "label": "OpenAI", "category": "cloud",
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY", "key_hint": "",
        "models": "gpt-4o, gpt-4o-mini, o1, o3-mini",
    },
    {
        "key": "anthropic", "label": "Anthropic", "category": "cloud",
        "base_url": "https://api.anthropic.com",
        "key_env": "ANTHROPIC_API_KEY", "key_hint": "",
        "models": "claude-opus-4-5, claude-sonnet-4-5, claude-haiku",
    },
    {
        "key": "google", "label": "Google Gemini", "category": "cloud",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "key_env": "GEMINI_API_KEY", "key_hint": "",
        "models": "gemini-2.5-pro, gemini-2.0-flash, gemini-1.5-pro",
    },
    {
        "key": "mistral", "label": "Mistral AI", "category": "cloud",
        "base_url": "https://api.mistral.ai/v1",
        "key_env": "MISTRAL_API_KEY", "key_hint": "",
        "models": "mistral-large-latest, mistral-medium, codestral-latest",
    },
    {
        "key": "cohere", "label": "Cohere", "category": "cloud",
        "base_url": "https://api.cohere.ai/v1",
        "key_env": "COHERE_API_KEY", "key_hint": "",
        "models": "command-r-plus, command-r, command",
    },
    {
        "key": "deepseek", "label": "DeepSeek", "category": "cloud",
        "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY", "key_hint": "",
        "models": "deepseek-chat, deepseek-coder, deepseek-reasoner",
    },
    {
        "key": "xai", "label": "xAI Grok", "category": "cloud",
        "base_url": "https://api.x.ai/v1",
        "key_env": "XAI_API_KEY", "key_hint": "",
        "models": "grok-3, grok-3-mini, grok-2",
    },
    {
        "key": "perplexity", "label": "Perplexity", "category": "cloud",
        "base_url": "https://api.perplexity.ai",
        "key_env": "PERPLEXITYAI_API_KEY", "key_hint": "",
        "models": "sonar-pro, sonar, sonar-reasoning",
    },
    {
        "key": "azure", "label": "Azure OpenAI", "category": "cloud",
        "base_url": "https://{your-resource}.openai.azure.com/",
        "key_env": "AZURE_OPENAI_KEY", "key_hint": "",
        "models": "gpt-4o, gpt-4-turbo, gpt-35-turbo",
    },
    {
        "key": "alibaba", "label": "Alibaba Qwen", "category": "cloud",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY", "key_hint": "",
        "models": "qwen-plus, qwen-turbo, qwen-long",
    },
    # ── Aggregators & open-model hosters ────────────────────────────────────
    {
        "key": "openrouter", "label": "OpenRouter", "category": "cloud",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY", "key_hint": "",
        "models": "openai/gpt-4o, anthropic/claude-opus-4-5, meta-llama/llama-3.3-70b",
    },
    {
        "key": "groq", "label": "Groq", "category": "cloud",
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY", "key_hint": "",
        "models": "llama-3.3-70b-versatile, mixtral-8x7b-32768, llama-3.1-8b-instant",
    },
    {
        "key": "huggingface", "label": "Hugging Face Inference", "category": "cloud",
        "base_url": "https://api-inference.huggingface.co/v1",
        "key_env": "HUGGINGFACE_API_KEY", "key_hint": "",
        "models": "meta-llama/Meta-Llama-3-70B, mistralai/Mixtral-8x7B",
    },
    {
        "key": "together", "label": "Together AI", "category": "cloud",
        "base_url": "https://api.together.xyz/v1",
        "key_env": "TOGETHER_API_KEY", "key_hint": "",
        "models": "meta-llama/Llama-3-70b-chat-hf, mistralai/Mixtral-8x7B-Instruct-v0.1",
    },
    {
        "key": "deepinfra", "label": "DeepInfra", "category": "cloud",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "key_env": "DEEPINFRA_API_KEY", "key_hint": "",
        "models": "meta-llama/Meta-Llama-3.1-70B-Instruct, mistralai/Mixtral-8x22B",
    },
    {
        "key": "sambanova", "label": "SambaNova", "category": "cloud",
        "base_url": "https://api.sambanova.ai/v1",
        "key_env": "SAMBANOVA_API_KEY", "key_hint": "",
        "models": "Meta-Llama-3.3-70B-Instruct, DeepSeek-R1-Distill-Llama-70B",
    },
    {
        "key": "fireworks", "label": "Fireworks AI", "category": "cloud",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "key_env": "FIREWORKS_API_KEY", "key_hint": "",
        "models": "accounts/fireworks/models/llama-v3p3-70b-instruct",
    },
    # ── Local / self-hosted providers ────────────────────────────────────────
    {
        "key": "ollama", "label": "Ollama", "category": "local",
        "base_url": "http://localhost:11434/v1",
        "key_env": "OPENAI_API_KEY", "key_hint": "ollama",
        "models": "ollama/llama3.3:70b, ollama/mistral:latest, ollama/qwen2.5:32b",
    },
    {
        "key": "lmstudio", "label": "LM Studio", "category": "local",
        "base_url": "http://localhost:1234/v1",
        "key_env": "OPENAI_API_KEY", "key_hint": "lm-studio",
        "models": "loaded model name from LM Studio UI",
    },
    {
        "key": "vllm", "label": "vLLM", "category": "local",
        "base_url": "http://localhost:8000/v1",
        "key_env": "OPENAI_API_KEY", "key_hint": "vllm",
        "models": "meta-llama/Meta-Llama-3-70B-Instruct",
    },
    {
        "key": "localai", "label": "LocalAI", "category": "local",
        "base_url": "http://localhost:8080/v1",
        "key_env": "OPENAI_API_KEY", "key_hint": "localai",
        "models": "ggml-gpt4all-j, mistral-7b-openorca",
    },
    {
        "key": "llamacpp", "label": "llama.cpp server", "category": "local",
        "base_url": "http://localhost:8080/v1",
        "key_env": "OPENAI_API_KEY", "key_hint": "llama.cpp",
        "models": "local (set --model path when starting server)",
    },
    {
        "key": "jan", "label": "Jan (Nitro)", "category": "local",
        "base_url": "http://localhost:1337/v1",
        "key_env": "OPENAI_API_KEY", "key_hint": "jan",
        "models": "loaded model name from Jan UI",
    },
    {
        "key": "oobabooga", "label": "Oobabooga / text-gen-webui", "category": "local",
        "base_url": "http://localhost:5000/v1",
        "key_env": "OPENAI_API_KEY", "key_hint": "oobabooga",
        "models": "local (enable --api flag in webui)",
    },
    {
        "key": "tabby", "label": "TabbyML", "category": "local",
        "base_url": "http://localhost:8080/v1beta",
        "key_env": "TABBY_API_KEY", "key_hint": "tabby",
        "models": "TabbyML/StarCoder-1B, TabbyML/DeepseekCoder-6.7B",
    },
]

_PROVIDER_BY_KEY: dict[str, dict] = {p["key"]: p for p in _KNOWN_PROVIDERS}


# ---------------------------------------------------------------------------
# Providers screen — two-panel picker + config form
# ---------------------------------------------------------------------------
class ProvidersScreen(ModalScreen):
    """Two-panel Providers screen.

    Left pane: scrollable list of cloud and local provider buttons.
    Right pane: per-provider config form that slides in on selection.
    """

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}
        self._selected_key: str = ""
        self._show_key: bool = False  # password visibility toggle

    def compose(self) -> ComposeResult:  # noqa: PLR0912
        with Vertical(id="providers-dialog"):
            yield Static("Providers Configuration", id="modal-agent-label")
            with Horizontal(id="providers-body"):
                # ── LEFT: provider list ──────────────────────────────────
                with Vertical(id="providers-left"):
                    yield Static("☁  Cloud", classes="prov-cat-label")
                    with ScrollableContainer(id="prov-cloud-scroll"):
                        for p in _KNOWN_PROVIDERS:
                            if p["category"] == "cloud":
                                configured = bool(os.environ.get(p["key_env"]))
                                badge = " ●" if configured else ""
                                yield Button(
                                    f"{p['label']}{badge}",
                                    id=f"prov-pick-{p['key']}",
                                    classes="prov-btn",
                                )
                    yield Static("⚙  Local / Self-hosted", classes="prov-cat-label")
                    with ScrollableContainer(id="prov-local-scroll"):
                        for p in _KNOWN_PROVIDERS:
                            if p["category"] == "local":
                                active = os.environ.get("LOCAL_API_BASE", "") == p["base_url"]
                                badge = " ◆" if active else ""
                                yield Button(
                                    f"{p['label']}{badge}",
                                    id=f"prov-pick-{p['key']}",
                                    classes="prov-btn",
                                )
                # ── RIGHT: config form ───────────────────────────────────
                with Vertical(id="providers-right"):
                    yield Static(
                        "← Select a provider from the list",
                        id="prov-detail-title",
                        classes="prov-right-title",
                    )
                    yield Static("", id="prov-detail-hint", classes="prov-hint")
                    yield Static("API Base URL", classes="prov-field-label")
                    yield Input(
                        "",
                        id="prov-base-url",
                        placeholder="https://api.example.com/v1",
                    )
                    with Horizontal(id="prov-key-row"):
                        yield Static("API Key", classes="prov-field-label prov-key-label")
                        yield Button(
                            "Show",
                            id="prov-toggle-key",
                            classes="prov-inline-btn",
                        )
                    yield Input(
                        "",
                        id="prov-api-key",
                        placeholder="Enter API key…",
                        password=True,
                    )
                    yield Static("", id="prov-detail-models", classes="prov-models-hint")
                    yield Static("", id="prov-status", classes="prov-status")
                    with Horizontal(id="prov-actions"):
                        yield Button("Apply", id="prov-apply", classes="modal-btn")
                        yield Button("Close", id="providers-cancel", classes="modal-btn modal-btn--cancel")
            # Status bar at the bottom
            yield Static(
                "Tip: [bold]Apply[/bold] sets env vars for the session. "
                "● = key already configured, ◆ = active LOCAL_API_BASE.",
                id="prov-footer-hint",
                classes="prov-hint",
            )

    # ------------------------------------------------------------------
    # Provider picker
    # ------------------------------------------------------------------

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""

        if btn_id.startswith("prov-pick-"):
            key = btn_id[len("prov-pick-"):]
            provider = _PROVIDER_BY_KEY.get(key)
            if provider:
                self._selected_key = key
                self._show_key = False
                self._populate_config_panel(provider)
            return

        if btn_id == "prov-toggle-key":
            self._show_key = not self._show_key
            try:
                inp = self.query_one("#prov-api-key", Input)
                inp.password = not self._show_key
                self.query_one("#prov-toggle-key", Button).label = (
                    "Hide" if self._show_key else "Show"
                )
            except Exception:
                pass
            return

        if btn_id == "prov-apply":
            self._apply_provider()
            return

        if btn_id == "providers-cancel":
            self.dismiss(None)
            return

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _populate_config_panel(self, provider: dict) -> None:
        """Fill the right-hand form with the selected provider's defaults."""
        try:
            self.query_one("#prov-detail-title", Static).update(
                f"[bold #00ff00]{provider['label']}[/bold #00ff00]"
            )
            env_var = provider["key_env"]
            hint_parts = [f"Env var: [#00aa00]{env_var}[/#00aa00]"]
            key_hint = provider.get("key_hint", "")
            if key_hint:
                hint_parts.append(f"default key: [dim]{key_hint}[/dim]")
            is_local = provider["category"] == "local"
            if is_local:
                hint_parts.append("[dim](sets LOCAL_API_BASE)[/dim]")
            self.query_one("#prov-detail-hint", Static).update("  |  ".join(hint_parts))

            # Pre-fill base URL with current env value or the catalogue default
            current_base = (
                os.environ.get("LOCAL_API_BASE")
                if is_local
                else os.environ.get("OPENAI_API_BASE", "")
            ) or provider["base_url"]
            self.query_one("#prov-base-url", Input).value = current_base

            # Pre-fill API key from env; for local providers use the hint as default
            current_key = os.environ.get(env_var, key_hint or "")
            self.query_one("#prov-api-key", Input).value = current_key

            self.query_one("#prov-detail-models", Static).update(
                f"[dim]Suggested models:[/dim] [#008800]{provider['models']}[/#008800]"
            )
            self.query_one("#prov-status", Static).update("")
        except Exception:
            pass

    def _apply_provider(self) -> None:
        """Write env vars for the selected provider and report status."""
        if not self._selected_key:
            try:
                self.query_one("#prov-status", Static).update(
                    "[#ffaa00]Select a provider first.[/#ffaa00]"
                )
            except Exception:
                pass
            return

        provider = _PROVIDER_BY_KEY.get(self._selected_key)
        if not provider:
            return

        try:
            base_url = self.query_one("#prov-base-url", Input).value.strip()
            api_key = self.query_one("#prov-api-key", Input).value.strip()
        except Exception:
            return

        applied: list[str] = []

        # Always set the API key env var for this provider
        env_var = provider["key_env"]
        if api_key:
            os.environ[env_var] = api_key
            applied.append(env_var)

        # For local providers also set LOCAL_API_BASE and OPENAI_API_BASE
        if provider["category"] == "local":
            if base_url:
                os.environ["LOCAL_API_BASE"] = base_url
                os.environ["OPENAI_API_BASE"] = base_url
                applied.extend(["LOCAL_API_BASE", "OPENAI_API_BASE"])
        else:
            # For cloud providers set OPENAI_API_BASE only if it differs from openai default
            openai_default = "https://api.openai.com/v1"
            if base_url and base_url != openai_default:
                os.environ["OPENAI_API_BASE"] = base_url
                applied.append("OPENAI_API_BASE")

        label = provider["label"]
        applied_str = ", ".join(applied) if applied else "nothing"
        try:
            self.query_one("#prov-status", Static).update(
                f"[#00ff00]✓ Applied {label}[/#00ff00] — set: [dim]{applied_str}[/dim]"
            )
        except Exception:
            pass

        # Refresh badge on the button in the left panel
        try:
            badge_char = "◆" if provider["category"] == "local" else "●"
            self.query_one(f"#prov-pick-{self._selected_key}", Button).label = (
                f"{label} {badge_char}"
            )
        except Exception:
            pass

        self.dismiss(("applied_provider", self._selected_key, base_url, api_key))


class ModelParamsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        mp = self._config.get("model_params", {})
        with Vertical(id="modal-dialog"):
            yield Static("Model Parameters", id="modal-agent-label")
            yield Input(value=str(mp.get("temperature", "0.0")), id="model-temp")
            yield Input(value=str(mp.get("max_tokens", "1024")), id="model-max-tokens")
            yield Input(value=str(mp.get("system_prompt", "")), id="model-system")
            with Horizontal():
                yield Button("Save", id="model-save", classes="modal-btn")
                yield Button("Close", id="model-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed, "#model-save")
    def on_model_save(self, event: Button.Pressed) -> None:
        try:
            temp = float(self.query_one("#model-temp", Input).value)
        except Exception:
            temp = 0.0
        try:
            max_t = int(self.query_one("#model-max-tokens", Input).value)
        except Exception:
            max_t = 1024
        try:
            sys = self.query_one("#model-system", Input).value
        except Exception:
            sys = ""
        self.dismiss(
            (
                "save_model_params",
                {"temperature": temp, "max_tokens": max_t, "system_prompt": sys},
            )
        )

    @on(Button.Pressed, "#model-cancel")
    def on_model_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class MemoryInspectorScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Memory / RAG Inspector", id="modal-agent-label")
            yield Static("Operations:", id="memory-ops")
            with Horizontal():
                yield Button("Rebuild Index", id="memory-rebuild", classes="modal-btn")
                yield Button("Evict All", id="memory-evict", classes="modal-btn modal-btn--cancel")
                yield Button("Close", id="memory-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed, "#memory-rebuild")
    def on_memory_rebuild(self, event: Button.Pressed) -> None:
        self.dismiss(("rebuild_memory",))

    @on(Button.Pressed, "#memory-evict")
    def on_memory_evict(self, event: Button.Pressed) -> None:
        self.dismiss(("evict_memory",))

    @on(Button.Pressed, "#memory-cancel")
    def on_memory_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class ExportImportScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Export / Import Workspace", id="modal-agent-label")
            yield Input(placeholder="path to export/import", id="export-import-path")
            with Horizontal():
                yield Button("Export", id="export-do", classes="modal-btn")
                yield Button("Import", id="import-do", classes="modal-btn")
                yield Button(
                    "Close", id="export-import-cancel", classes="modal-btn modal-btn--cancel"
                )

    @on(Button.Pressed, "#export-do")
    def on_export_do(self, event: Button.Pressed) -> None:
        try:
            path = self.query_one("#export-import-path", Input).value.strip()
        except Exception:
            path = ""
        self.dismiss(("export_config", path))

    @on(Button.Pressed, "#import-do")
    def on_import_do(self, event: Button.Pressed) -> None:
        try:
            path = self.query_one("#export-import-path", Input).value.strip()
        except Exception:
            path = ""
        self.dismiss(("import_config", path))

    @on(Button.Pressed, "#export-import-cancel")
    def on_export_import_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class EnvScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Environment Variables (CAI_*)", id="modal-agent-label")
            yield ListView(id="env-list")
            yield Input(placeholder="VAR_NAME (no CAI_ prefix)", id="env-name")
            yield Input(placeholder="value", id="env-value")
            with Horizontal():
                yield Button("Set", id="env-set", classes="modal-btn")
                yield Button("Unset", id="env-unset", classes="modal-btn modal-btn--cancel")
                yield Button("Close", id="env-cancel", classes="modal-btn modal-btn--cancel")

    async def on_mount(self) -> None:
        try:
            lv = self.query_one("#env-list", ListView)
            for k in sorted([k for k in os.environ if k.startswith("CAI_")]):
                await lv.mount(ListItem(Label(f"{k}={os.environ.get(k)}")))
        except Exception:
            pass

    @on(Button.Pressed, "#env-set")
    def on_env_set(self, event: Button.Pressed) -> None:
        try:
            name = self.query_one("#env-name", Input).value.strip()
            val = self.query_one("#env-value", Input).value
        except Exception:
            name = ""
            val = ""
        if not name:
            self.dismiss(None)
            return
        self.dismiss(("set_env", f"CAI_{name}", val))

    @on(Button.Pressed, "#env-unset")
    def on_env_unset(self, event: Button.Pressed) -> None:
        try:
            name = self.query_one("#env-name", Input).value.strip()
        except Exception:
            name = ""
        if not name:
            self.dismiss(None)
            return
        self.dismiss(("unset_env", f"CAI_{name}"))

    @on(Button.Pressed, "#env-cancel")
    def on_env_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class SessionRecordingScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        cur = os.environ.get("CAI_DISABLE_SESSION_RECORDING", "").lower() == "true"
        status = "disabled" if cur else "enabled"
        with Vertical(id="modal-dialog"):
            yield Static(
                f"Session recording is currently: [bold]{status}[/bold]", id="modal-agent-label"
            )
            with Horizontal():
                yield Button("Toggle", id="session-toggle", classes="modal-btn")
                yield Button("Close", id="session-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed, "#session-toggle")
    def on_session_toggle(self, event: Button.Pressed) -> None:
        self.dismiss(("toggle_session_recording",))

    @on(Button.Pressed, "#session-cancel")
    def on_session_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class ResetDefaultsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Reset all TUI config to defaults?", id="modal-agent-label")
            yield Button("Reset", id="reset-do", classes="modal-btn modal-btn--cancel")
            yield Button("Cancel", id="reset-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed, "#reset-do")
    def on_reset_do(self, event: Button.Pressed) -> None:
        self.dismiss(("reset_defaults", True))

    @on(Button.Pressed, "#reset-cancel")
    def on_reset_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class ConfigOverviewScreen(ModalScreen):
    """Full interactive configuration overview: list of variables with Edit/Reset."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, variables: list[dict]) -> None:
        super().__init__()
        self._variables = variables

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Configuration Overview", id="modal-agent-label")
            with ScrollableContainer(id="config-overview-scroll"):
                yield ListView(id="config-overview-list")
            with Horizontal():
                yield Button(
                    "Close", id="config-overview-close", classes="modal-btn modal-btn--cancel"
                )

    async def on_mount(self) -> None:
        try:
            from cai.tui.app_impl import _load_tui_config

            lv = self.query_one("#config-overview-list", ListView)
            # Populate rows with current values
            for idx, v in enumerate(self._variables):
                # Ensure 'name' is always a string for downstream lookups
                name = str(v.get("name") or "")
                # Prefer explicit env var, then persisted config, then default
                cfg = _load_tui_config()
                val = (
                    os.environ.get(name)
                    or cfg.get("env", {}).get(name)
                    or v.get("default", "Not set")
                )
                display = f"{idx + 1:2d} | {name:<40.40} | {str(val):<15.15} | {v.get('default', ''):<12.12} | {v.get('description', '')[:60]}"
                item = ListItem(Label(display), id=f"config-item-{idx}")
                await lv.mount(item)
                await item.mount(Button("Edit", id=f"cfg-edit-{idx}", classes="agent-btn"))
                await item.mount(Button("Reset", id=f"cfg-reset-{idx}", classes="team-btn"))
        except Exception:
            pass

    @on(Button.Pressed, "#config-overview-close")
    def on_config_overview_close(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    @on(Button.Pressed, ".agent-btn")
    def on_config_edit(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if not btn_id.startswith("cfg-edit-"):
            return
        try:
            idx = int(btn_id.rsplit("-", 1)[-1])
        except Exception:
            idx = None
        self.dismiss(("edit", idx))

    @on(Button.Pressed, ".team-btn")
    def on_config_reset(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if not btn_id.startswith("cfg-reset-"):
            return
        try:
            idx = int(btn_id.rsplit("-", 1)[-1])
        except Exception:
            idx = None
        self.dismiss(("reset", idx))
