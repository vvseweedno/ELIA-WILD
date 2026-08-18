from __future__ import annotations

import atexit
from hashlib import sha256
import importlib.util
from pathlib import Path
from urllib.parse import urlparse
from typing import Any
from uuid import uuid4

from .net import assert_http_url, is_safe_browser_subresource
from .types import BodyCapability, BodyResult


class BrowserBody:
    """Playwright-backed browser organ with an isolated ephemeral BrowserContext.

    Application-layer URL validation cannot fully defeat DNS rebinding, WebSockets or
    browser network-stack races. Enabling this organ therefore requires an explicit
    network-isolation attestation from the deployment layer (container/firewall policy
    denying loopback, RFC1918, link-local and metadata ranges unless intentionally
    permitted). Interactive actions additionally require an origin allow-list.

    Genesis 1.7 treats the *destination* of an interaction as a separate authority
    boundary. During a click/fill operation, top-level document requests are allowed
    only to configured trusted interaction origins; a trusted page cannot use a button,
    form or redirect to smuggle an interaction to another origin.
    """

    def __init__(self, workspace: Path, config: dict[str, Any] | None = None):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config = dict(config or {})
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._interaction_active = False
        atexit.register(self.close)

    @property
    def installed(self) -> bool:
        return importlib.util.find_spec("playwright") is not None

    @property
    def network_isolation_confirmed(self) -> bool:
        return bool(self.config.get("network_isolation_confirmed", False))

    @property
    def enabled(self) -> bool:
        return (
            bool(self.config.get("enabled", False))
            and self.installed
            and self.network_isolation_confirmed
        )

    def _trusted_interaction_origins(self) -> set[str]:
        raw = self.config.get("trusted_interaction_origins") or []
        if not isinstance(raw, list):
            return set()
        return {str(item).strip().lower().rstrip("/") for item in raw if str(item).strip()}

    @property
    def interaction_enabled(self) -> bool:
        return (
            self.enabled
            and bool(self.config.get("interaction_enabled", False))
            and bool(self._trusted_interaction_origins())
        )

    def capabilities(self) -> list[BodyCapability]:
        if not bool(self.config.get("enabled", False)):
            readiness = "disabled"
        elif not self.installed:
            readiness = "playwright_not_installed"
        elif not self.network_isolation_confirmed:
            readiness = "network_isolation_required"
        else:
            readiness = "ready"
        interaction_readiness = readiness
        if readiness == "ready" and bool(self.config.get("interaction_enabled", False)):
            if not self._trusted_interaction_origins():
                interaction_readiness = "trusted_interaction_origins_required"
        elif readiness == "ready":
            interaction_readiness = "interaction_disabled"
        return [
            BodyCapability(
                "browser_navigate",
                "Navigate the isolated browser to one HTTP/HTTPS page and return a structured snapshot.",
                "{url: str}",
                "configured_browser_read",
                "network navigation and page resource requests",
                "deployment_isolated_public_http_https_or_explicit_private_scope",
                "network",
                self.enabled,
                readiness,
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
                readiness,
            ),
            BodyCapability(
                "browser_click",
                "Click one locator only on a configured trusted interaction origin.",
                "{locator: {kind: role|text|css, role?: str, name?: str, text?: str, selector?: str}}",
                "configured_browser_interaction",
                "may cause remote state changes only through an allow-listed interaction origin",
                "current_and_resulting_trusted_origin",
                "network",
                self.interaction_enabled,
                interaction_readiness,
            ),
            BodyCapability(
                "browser_fill",
                "Fill one form control only on a configured trusted interaction origin without submitting it.",
                "{locator: {...}, value: str}",
                "configured_browser_interaction",
                "changes current browser form state; navigation caused by page scripts remains origin-gated",
                "current_and_resulting_trusted_origin",
                "network",
                self.interaction_enabled,
                interaction_readiness,
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
                readiness,
            ),
        ]

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(str(url))
        if parsed.scheme in {"about", "data", "blob"}:
            return str(url).lower().rstrip("/")
        if not parsed.scheme or not parsed.hostname:
            return ""
        port = parsed.port
        default = (parsed.scheme == "https" and port == 443) or (
            parsed.scheme == "http" and port == 80
        )
        suffix = "" if port is None or default else f":{port}"
        return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{suffix}"

    def _interaction_destination_allowed(self, url: str) -> bool:
        return self._origin(url) in self._trusted_interaction_origins()

    def _ensure_started(self) -> None:
        if not self.enabled:
            if not self.installed:
                reason = "Playwright dependency is missing"
            elif not self.network_isolation_confirmed:
                reason = "browser network isolation has not been confirmed"
            else:
                reason = "browser body is disabled"
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
            viewport={
                "width": int(self.config.get("viewport_width", 1280)),
                "height": int(self.config.get("viewport_height", 720)),
            },
            accept_downloads=False,
            service_workers="block",
        )
        allow_private = bool(self.config.get("allow_private", False))

        def guard(route: Any) -> None:
            request = route.request
            if (
                self._interaction_active
                and request.resource_type == "document"
                and not self._interaction_destination_allowed(request.url)
            ):
                route.abort("blockedbyclient")
                return
            if is_safe_browser_subresource(request.url, allow_private=allow_private):
                route.continue_()
            else:
                route.abort("blockedbyclient")

        self._context.route("**/*", guard)
        self._page = self._context.new_page()

    def close(self) -> None:
        page, context, browser, playwright = (
            self._page,
            self._context,
            self._browser,
            self._playwright,
        )
        self._page = self._context = self._browser = self._playwright = None
        self._interaction_active = False
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

    def _assert_interaction_origin(self) -> None:
        self._ensure_started()
        origin = self._origin(self._page.url)
        if origin not in self._trusted_interaction_origins():
            raise PermissionError(
                f"browser interaction origin is not allow-listed: {origin or self._page.url!r}"
            )

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
        max_text = max(
            1000, min(int(self.config.get("max_text_chars", 50_000)), 200_000)
        )
        body = self._page.locator("body")
        text = (
            body.inner_text(timeout=int(self.config.get("timeout_ms", 20_000)))
            if body.count()
            else ""
        )
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
            return BodyResult(
                False,
                "browser_navigate",
                error="browser body is disabled/unavailable",
            )
        assert_http_url(
            url,
            allow_private=bool(self.config.get("allow_private", False)),
        )
        self._ensure_started()
        response = self._page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=int(self.config.get("timeout_ms", 20_000)),
        )
        final_url = self._page.url
        assert_http_url(
            final_url,
            allow_private=bool(self.config.get("allow_private", False)),
        )
        data = self._snapshot()
        data["status_code"] = response.status if response is not None else None
        return BodyResult(True, "browser_navigate", data)

    def snapshot(self) -> BodyResult:
        try:
            return BodyResult(True, "browser_snapshot", self._snapshot())
        except Exception as exc:
            return BodyResult(
                False,
                "browser_snapshot",
                error=f"{type(exc).__name__}: {exc}",
            )

    def click(self, locator: dict[str, Any]) -> BodyResult:
        if not self.interaction_enabled:
            return BodyResult(
                False,
                "browser_click",
                error="browser interaction is disabled/unavailable",
            )
        try:
            self._assert_interaction_origin()
            target = self._locator(locator)
            self._interaction_active = True
            try:
                target.click(timeout=int(self.config.get("timeout_ms", 20_000)))
            finally:
                self._interaction_active = False
            self._assert_interaction_origin()
            return BodyResult(True, "browser_click", self._snapshot())
        except Exception as exc:
            return BodyResult(
                False,
                "browser_click",
                error=f"{type(exc).__name__}: {exc}",
            )

    def fill(self, locator: dict[str, Any], value: str) -> BodyResult:
        if not self.interaction_enabled:
            return BodyResult(
                False,
                "browser_fill",
                error="browser interaction is disabled/unavailable",
            )
        if len(str(value)) > 32_000:
            return BodyResult(
                False,
                "browser_fill",
                error="fill value exceeds 32k characters",
            )
        try:
            self._assert_interaction_origin()
            target = self._locator(locator)
            self._interaction_active = True
            try:
                target.fill(
                    str(value),
                    timeout=int(self.config.get("timeout_ms", 20_000)),
                )
            finally:
                self._interaction_active = False
            self._assert_interaction_origin()
            return BodyResult(True, "browser_fill", self._snapshot())
        except Exception as exc:
            return BodyResult(
                False,
                "browser_fill",
                error=f"{type(exc).__name__}: {exc}",
            )

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
