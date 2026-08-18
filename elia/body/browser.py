from __future__ import annotations

import atexit
from hashlib import sha256
import importlib.util
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from .net import assert_http_url, is_safe_browser_subresource
from .types import BodyCapability, BodyResult


class BrowserBody:
    """Playwright-backed browser organ with an isolated ephemeral BrowserContext."""

    def __init__(self, workspace: Path, config: dict[str, Any] | None = None):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config = dict(config or {})
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        atexit.register(self.close)

    @property
    def installed(self) -> bool:
        return importlib.util.find_spec("playwright") is not None

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False)) and self.installed

    @property
    def interaction_enabled(self) -> bool:
        return self.enabled and bool(self.config.get("interaction_enabled", False))

    def capabilities(self) -> list[BodyCapability]:
        readiness = "ready" if self.installed else "playwright_not_installed"
        return [
            BodyCapability(
                "browser_navigate",
                "Navigate the isolated browser to one HTTP/HTTPS page and return a structured snapshot.",
                "{url: str}",
                "configured_browser_read",
                "network navigation and page resource requests",
                "public_http_https_or_explicit_private_scope",
                "network",
                self.enabled,
                readiness if self.config.get("enabled", False) else "disabled",
            ),
            BodyCapability(
                "browser_snapshot",
                "Read title, URL, visible body text, links, controls and forms from the current page.",
                "{}",
                "browser_read",
                "none beyond already loaded page",
                "none",
                "low",
                self.enabled,
                readiness if self.config.get("enabled", False) else "disabled",
            ),
            BodyCapability(
                "browser_click",
                "Click one locator in the current page using role/text/css targeting.",
                "{locator: {kind: role|text|css, role?: str, name?: str, text?: str, selector?: str}}",
                "configured_browser_interaction",
                "may cause remote state changes through the web application",
                "current_page",
                "network",
                self.interaction_enabled,
                "ready" if self.interaction_enabled else "interaction_disabled",
            ),
            BodyCapability(
                "browser_fill",
                "Fill one form control in the current page without submitting it.",
                "{locator: {...}, value: str}",
                "configured_browser_interaction",
                "changes current browser form state; may trigger application events",
                "current_page",
                "network",
                self.interaction_enabled,
                "ready" if self.interaction_enabled else "interaction_disabled",
            ),
            BodyCapability(
                "browser_screenshot",
                "Capture the current page to ELIA-owned workspace and return path plus SHA-256.",
                "{full_page?: bool}",
                "browser_read_workspace_write",
                "writes a PNG artifact inside workspace/browser-artifacts",
                "none",
                "low",
                self.enabled,
                readiness if self.config.get("enabled", False) else "disabled",
            ),
        ]

    def _ensure_started(self) -> None:
        if not self.enabled:
            reason = "Playwright dependency is missing" if not self.installed else "browser body is disabled"
            raise RuntimeError(reason)
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        browser_name = str(self.config.get("browser", "chromium")).strip().lower()
        browser_type = getattr(self._playwright, browser_name, None)
        if browser_type is None or browser_name not in {"chromium", "firefox", "webkit"}:
            raise ValueError(f"unsupported Playwright browser: {browser_name}")
        self._browser = browser_type.launch(headless=bool(self.config.get("headless", True)))
        self._context = self._browser.new_context(
            viewport={"width": int(self.config.get("viewport_width", 1280)), "height": int(self.config.get("viewport_height", 720))},
            accept_downloads=False,
        )
        allow_private = bool(self.config.get("allow_private", False))

        def guard(route: Any) -> None:
            if is_safe_browser_subresource(route.request.url, allow_private=allow_private):
                route.continue_()
            else:
                route.abort("blockedbyclient")

        self._context.route("**/*", guard)
        self._page = self._context.new_page()

    def close(self) -> None:
        page, context, browser, playwright = self._page, self._context, self._browser, self._playwright
        self._page = self._context = self._browser = self._playwright = None
        for item in (page, context, browser):
            if item is not None:
                try:
                    item.close()
                except Exception:
                    pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def _locator(self, spec: dict[str, Any]) -> Any:
        self._ensure_started()
        if not isinstance(spec, dict):
            raise ValueError("locator must be an object")
        kind = str(spec.get("kind", "")).strip().lower()
        if kind == "role":
            role = str(spec.get("role", "")).strip()
            name = str(spec.get("name", "")).strip() or None
            if not role:
                raise ValueError("role locator requires role")
            return self._page.get_by_role(role, name=name)
        if kind == "text":
            text = str(spec.get("text", "")).strip()
            if not text:
                raise ValueError("text locator requires text")
            return self._page.get_by_text(text, exact=bool(spec.get("exact", True)))
        if kind == "css":
            selector = str(spec.get("selector", "")).strip()
            if not selector or len(selector) > 2000:
                raise ValueError("css locator requires a bounded selector")
            return self._page.locator(selector)
        raise ValueError("locator kind must be role, text, or css")

    def _snapshot(self) -> dict[str, Any]:
        self._ensure_started()
        max_text = max(1000, min(int(self.config.get("max_text_chars", 50_000)), 200_000))
        text = self._page.locator("body").inner_text(timeout=int(self.config.get("timeout_ms", 20_000))) if self._page.locator("body").count() else ""
        links = self._page.locator("a").evaluate_all(
            "els => els.slice(0,100).map(e => ({text:(e.innerText||'').trim().slice(0,500), href:e.href}))"
        )
        controls = self._page.locator("button,input,textarea,select").evaluate_all(
            "els => els.slice(0,100).map(e => ({tag:e.tagName.toLowerCase(), type:e.type||null, name:e.name||null, aria:e.getAttribute('aria-label'), placeholder:e.placeholder||null, text:(e.innerText||'').trim().slice(0,300)}))"
        )
        return {
            "url": self._page.url,
            "title": self._page.title(),
            "text": text[:max_text],
            "text_truncated": len(text) > max_text,
            "links": links,
            "controls": controls,
        }

    def navigate(self, url: str) -> BodyResult:
        if not self.enabled:
            return BodyResult(False, "browser_navigate", error="browser body is disabled or Playwright is unavailable")
        assert_http_url(url, allow_private=bool(self.config.get("allow_private", False)))
        self._ensure_started()
        response = self._page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=int(self.config.get("timeout_ms", 20_000)),
        )
        data = self._snapshot()
        data["status_code"] = response.status if response is not None else None
        return BodyResult(True, "browser_navigate", data)

    def snapshot(self) -> BodyResult:
        try:
            return BodyResult(True, "browser_snapshot", self._snapshot())
        except Exception as exc:
            return BodyResult(False, "browser_snapshot", error=f"{type(exc).__name__}: {exc}")

    def click(self, locator: dict[str, Any]) -> BodyResult:
        if not self.interaction_enabled:
            return BodyResult(False, "browser_click", error="browser interaction is disabled")
        target = self._locator(locator)
        target.click(timeout=int(self.config.get("timeout_ms", 20_000)))
        return BodyResult(True, "browser_click", self._snapshot())

    def fill(self, locator: dict[str, Any], value: str) -> BodyResult:
        if not self.interaction_enabled:
            return BodyResult(False, "browser_fill", error="browser interaction is disabled")
        if len(str(value)) > 32_000:
            return BodyResult(False, "browser_fill", error="fill value exceeds 32k characters")
        target = self._locator(locator)
        target.fill(str(value), timeout=int(self.config.get("timeout_ms", 20_000)))
        return BodyResult(True, "browser_fill", self._snapshot())

    def screenshot(self, full_page: bool = False) -> BodyResult:
        self._ensure_started()
        artifact_dir = (self.workspace / "browser-artifacts").resolve()
        if not artifact_dir.is_relative_to(self.workspace):
            raise RuntimeError("browser artifact directory escaped workspace")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"page-{uuid4().hex}.png"
        self._page.screenshot(path=str(path), full_page=bool(full_page))
        raw = path.read_bytes()
        return BodyResult(
            True,
            "browser_screenshot",
            {
                "path": str(path.relative_to(self.workspace)),
                "bytes": len(raw),
                "sha256": sha256(raw).hexdigest(),
                "url": self._page.url,
            },
        )

    def _set_content_for_test(self, html: str) -> None:
        """Exercise the real Playwright context in tests without external network."""
        self._ensure_started()
        self._page.set_content(str(html))
