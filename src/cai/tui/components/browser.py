"""BrowserPreview widget — inline screenshot display for the CAI TUI."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


# ---------------------------------------------------------------------------
# Browser preview widget
# ---------------------------------------------------------------------------
class BrowserPreview(Widget):
    """Displays the most recent browser screenshot path and optional VLM sitrep.

    Visibility is driven via a reactive `panel_visible` property which keeps the UI
    and styles in sync. `screenshot_path` and `interactive_map_data` remain
    reactive and trigger content updates. Watcher is `watch_panel_visible`.
    """

    panel_visible: reactive[bool] = reactive(False)
    screenshot_path: reactive[str] = reactive("")
    interactive_map_data: reactive[list] = reactive([])

    # The Textual runtime sets `app` at mount time; expose for static analysis.
    app: Any

    def compose(self) -> ComposeResult:
        yield Static("", id="browser-preview-content")

    def watch_screenshot_path(self, path: str) -> None:
        """React to a new screenshot path: update content and show the panel.

        The visibility flag is updated so the CSS class is managed in
        :py:meth:`watch_visible`.
        """
        # Use the reactive boolean to drive class changes in watch_panel_visible
        self.panel_visible = bool(path)
        if not path:
            return

        try:
            self.query_one("#browser-preview-content", Static)
        except Exception:
            return

    def watch_panel_visible(self, panel_visible: bool) -> None:
        """Toggle the display class with a smooth fade-in / fade-out animation."""
        # Ensure fallback variables are defined for all branches
        path = getattr(self, "screenshot_path", "")
        content_widget = None
        try:
            if panel_visible:
                self.add_class("-visible")
                # Fade in: start fully transparent then animate to opaque
                self.styles.opacity = 0.0
                self.animate("styles.opacity", value=1.0, duration=0.35, easing="out_cubic")
            else:
                if self.has_class("-visible"):
                    # Fade out, then remove the class once animation completes
                    def _hide_after_fade() -> None:
                        try:
                            self.remove_class("-visible")
                            self.styles.opacity = 1.0
                        except Exception:
                            pass

                    self.animate(
                        "styles.opacity",
                        value=0.0,
                        duration=0.25,
                        easing="in_cubic",
                        on_complete=_hide_after_fade,
                    )
                else:
                    self.remove_class("-visible")
        except Exception:
            # Fallback: plain class toggle with no animation
            try:
                if panel_visible:
                    self.add_class("-visible")
                else:
                    self.remove_class("-visible")
            except Exception:
                pass

        # Try textual-image first for rich inline display
        try:
            from textual_image.widget import Image as TerminalImage  # type: ignore

            # Replace the Static placeholder with a TerminalImage on first use;
            # subsequent calls update its path attribute.
            # Snapshot the current screenshot path for use in both branches.
            path = self.screenshot_path
            existing = None
            try:
                existing = self.query_one("TerminalImage")
            except Exception:
                pass

            if existing is None:
                # Ensure we have the last screenshot path and a placeholder
                path = self.screenshot_path
                try:
                    content_widget = self.query_one("#browser-preview-content", Static)
                except Exception:
                    content_widget = None

                # If Pillow is available and we have map data, composite an overlay
                use_path = path
                try:
                    if self.interactive_map_data:
                        from PIL import Image, ImageDraw, ImageFont  # type: ignore

                        orig = Image.open(path).convert("RGBA")
                        overlay_img = Image.new("RGBA", orig.size, (255, 255, 255, 0))
                        draw = cast(Any, ImageDraw.Draw(overlay_img))
                        try:
                            font = ImageFont.load_default()
                        except Exception:
                            font = None

                        def _measure_label(text: str) -> tuple[int, int]:
                            if not font:
                                return int(len(text) * 6), 10
                            if hasattr(draw, "textbbox"):
                                left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
                                return int(max(0, right - left)), int(max(0, bottom - top))
                            if hasattr(draw, "textsize"):
                                w, h = draw.textsize(text, font=font)
                                return int(w), int(h)
                            return int(len(text) * 6), 10

                        # Playwright viewport assumed; scale rects to image size if needed
                        vp_w, vp_h = 1280, 800
                        sx = orig.width / vp_w if vp_w else 1.0
                        sy = orig.height / vp_h if vp_h else 1.0

                        for node in (self.interactive_map_data or [])[:80]:
                            try:
                                rect = node.get("rect") or {}
                                if not rect:
                                    continue
                                x = int(rect.get("x") or 0)
                                y = int(rect.get("y") or 0)
                                w = int(rect.get("w") or 0)
                                h = int(rect.get("h") or 0)
                                rx = int(x * sx)
                                ry = int(y * sy)
                                rw = max(2, int(w * sx))
                                rh = max(2, int(h * sy))

                                outline_w = max(1, orig.width // 300)
                                draw.rectangle(
                                    [rx, ry, rx + rw, ry + rh],
                                    outline=(255, 77, 255, 200),
                                    width=outline_w,
                                )

                                idx = (
                                    node.get("map_index")
                                    if node.get("map_index") is not None
                                    else node.get("index")
                                )
                                label = str(idx) if idx is not None else ""
                                if label:
                                    tx, ty = rx + 4, ry + 4
                                    # text background
                                    tw, th = _measure_label(label)
                                    draw.rectangle(
                                        [tx - 2, ty - 2, tx + tw + 2, ty + th + 2],
                                        fill=(0, 0, 0, 160),
                                    )
                                    draw.text((tx, ty), label, fill=(255, 255, 255, 255), font=font)
                            except Exception:
                                continue

                        out = Image.alpha_composite(orig, overlay_img)
                        ts = int(time.time())
                        p = Path(path)
                        overlay_path = str(p.with_name(f"{p.stem}_overlay_{ts}.png"))
                        try:
                            out.save(overlay_path)
                            use_path = overlay_path
                        except Exception:
                            use_path = path
                except Exception:
                    use_path = path

                if content_widget is not None:
                    content_widget.display = False
                self.mount(TerminalImage(use_path))
            else:
                try:
                    # If map data available, attempt to composite overlay and update path
                    use_path = path
                    try:
                        if self.interactive_map_data:
                            from PIL import Image, ImageDraw, ImageFont  # type: ignore

                            orig = Image.open(path).convert("RGBA")
                            overlay_img = Image.new("RGBA", orig.size, (255, 255, 255, 0))
                            draw = cast(Any, ImageDraw.Draw(overlay_img))
                            try:
                                font = ImageFont.load_default()
                            except Exception:
                                font = None

                            def _measure_label(text: str) -> tuple[int, int]:
                                if not font:
                                    return int(len(text) * 6), 10
                                if hasattr(draw, "textbbox"):
                                    left, top, right, bottom = draw.textbbox(
                                        (0, 0), text, font=font
                                    )
                                    return int(max(0, right - left)), int(max(0, bottom - top))
                                if hasattr(draw, "textsize"):
                                    w, h = draw.textsize(text, font=font)
                                    return int(w), int(h)
                                return int(len(text) * 6), 10

                            vp_w, vp_h = 1280, 800
                            sx = orig.width / vp_w if vp_w else 1.0
                            sy = orig.height / vp_h if vp_h else 1.0
                            for node in (self.interactive_map_data or [])[:80]:
                                try:
                                    rect = node.get("rect") or {}
                                    if not rect:
                                        continue
                                    x = int(rect.get("x") or 0)
                                    y = int(rect.get("y") or 0)
                                    w = int(rect.get("w") or 0)
                                    h = int(rect.get("h") or 0)
                                    rx = int(x * sx)
                                    ry = int(y * sy)
                                    rw = max(2, int(w * sx))
                                    rh = max(2, int(h * sy))
                                    outline_w = max(1, orig.width // 300)
                                    draw.rectangle(
                                        [rx, ry, rx + rw, ry + rh],
                                        outline=(255, 77, 255, 200),
                                        width=outline_w,
                                    )
                                    idx = (
                                        node.get("map_index")
                                        if node.get("map_index") is not None
                                        else node.get("index")
                                    )
                                    label = str(idx) if idx is not None else ""
                                    if label:
                                        tx, ty = rx + 4, ry + 4
                                        tw, th = _measure_label(label)
                                        draw.rectangle(
                                            [tx - 2, ty - 2, tx + tw + 2, ty + th + 2],
                                            fill=(0, 0, 0, 160),
                                        )
                                        draw.text(
                                            (tx, ty), label, fill=(255, 255, 255, 255), font=font
                                        )
                                except Exception:
                                    continue
                            out = Image.alpha_composite(orig, overlay_img)
                            ts = int(time.time())
                            p = Path(path)
                            overlay_path = str(p.with_name(f"{p.stem}_overlay_{ts}.png"))
                            try:
                                out.save(overlay_path)
                                use_path = overlay_path
                            except Exception:
                                use_path = path
                    except Exception:
                        use_path = path
                    existing.path = use_path  # type: ignore[attr-defined]
                except Exception:
                    pass

            # Render a simple legend overlay (below the image) showing map indices
            try:
                overlay = self.query_one("#browser-preview-overlay", Static)
            except Exception:
                overlay = None

            legend_lines = []
            try:
                for node in (self.interactive_map_data or [])[:40]:
                    idx = node.get("map_index") or node.get("index") or ""
                    name = (node.get("name") or "").strip()
                    selector = node.get("selector") or node.get("id") or ""
                    rect = node.get("rect") or {}
                    coords = (
                        f"({rect.get('x')},{rect.get('y')} {rect.get('w')}x{rect.get('h')})"
                        if rect
                        else ""
                    )
                    legend_lines.append(f"[{idx}] {name} {selector} {coords}")
            except Exception:
                legend_lines = []

            legend_text = "\n".join(legend_lines) or ""
            if overlay is None:
                try:
                    self.mount(Static(legend_text, id="browser-preview-overlay"))
                except Exception:
                    pass
            else:
                try:
                    overlay.update(legend_text)
                except Exception:
                    pass

            return
        except ImportError:
            pass

        # Fallback: show the path + instruction in a styled Static
        fname = path.split("/")[-1] if "/" in path else path
        # Compose fallback text content and include the interactive map legend
        legend_lines = []
        try:
            for node in (self.interactive_map_data or [])[:40]:
                idx = node.get("map_index") or node.get("index") or ""
                name = (node.get("name") or "").strip()
                selector = node.get("selector") or node.get("id") or ""
                legend_lines.append(f"[{idx}] {name} {selector}")
        except Exception:
            legend_lines = []

            legend_text = "\n".join(legend_lines) or ""
            fallback_text = (
                f"[bold magenta]Browser Screenshot[/bold magenta]\n"
                f"[#ff77ff]{fname}[/#ff77ff]\n"
                f"[dim #aa55aa]{path}[/dim #aa55aa]\n\n"
                "[dim]Install textual-image for inline preview (pip install textual-image)[/dim]\n\n"
                f"{legend_text}"
            )
            if content_widget is not None:
                try:
                    content_widget.update(fallback_text)
                except Exception:
                    pass
            else:
                try:
                    self.mount(Static(fallback_text))
                except Exception:
                    pass

    def set_screenshot(self, path: str, interactive_map: Any | None = None) -> None:
        """Thread-safe entry point called by the screenshot writer closure.

        Accepts an optional ``interactive_map`` list produced by the browser tool.
        """
        self.app.call_from_thread(self._set_screenshot_path, path, interactive_map)

    def _set_screenshot_path(self, path: str, interactive_map: Any | None = None) -> None:
        self.screenshot_path = path
        try:
            self.interactive_map_data = interactive_map or []
        except Exception:
            self.interactive_map_data = []
