from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from elia.body.browser import BrowserBody


def test_browser_requires_network_isolation_attestation(tmp_path: Path) -> None:
    body = BrowserBody(
        tmp_path,
        {"enabled": True, "browser": "chromium", "headless": True},
    )
    assert body.enabled is False
    capability = {item.name: item for item in body.capabilities()}["browser_navigate"]
    assert capability.readiness == "network_isolation_required"


def test_real_playwright_context_snapshot_fill_click_and_screenshot(tmp_path: Path) -> None:
    body = BrowserBody(
        tmp_path,
        {
            "enabled": True,
            "interaction_enabled": True,
            "network_isolation_confirmed": True,
            "trusted_interaction_origins": ["about:blank"],
            "browser": "chromium",
            "headless": True,
            "timeout_ms": 10_000,
            "max_text_chars": 20_000,
        },
    )
    try:
        body._set_content_for_test(
            """
            <!doctype html>
            <html>
              <head><title>ELIA Body Test</title></head>
              <body>
                <label for="name">Name</label>
                <input id="name" placeholder="identity">
                <button id="commit" onclick="document.querySelector('#out').textContent='hello ' + document.querySelector('#name').value">Commit</button>
                <div id="out">idle</div>
                <a href="https://example.com/docs">Docs</a>
              </body>
            </html>
            """
        )
        initial = body.snapshot()
        assert initial.ok is True
        assert "idle" in initial.data["text"]
        assert any(item["text"] == "Docs" for item in initial.data["links"])

        filled = body.fill(
            {"kind": "css", "selector": "#name"},
            "ELIA",
        )
        assert filled.ok is True

        clicked = body.click(
            {"kind": "role", "role": "button", "name": "Commit"}
        )
        assert clicked.ok is True
        assert "hello ELIA" in clicked.data["text"]

        screenshot = body.screenshot(full_page=True)
        assert screenshot.ok is True
        path = tmp_path / screenshot.data["path"]
        assert path.is_file()
        assert path.stat().st_size == screenshot.data["bytes"]
        assert len(screenshot.data["sha256"]) == 64
    finally:
        body.close()


def test_browser_interaction_is_separately_gated(tmp_path: Path) -> None:
    body = BrowserBody(
        tmp_path,
        {
            "enabled": True,
            "interaction_enabled": False,
            "network_isolation_confirmed": True,
            "browser": "chromium",
            "headless": True,
        },
    )
    try:
        body._set_content_for_test("<button>Do thing</button>")
        denied = body.click({"kind": "text", "text": "Do thing"})
        assert denied.ok is False
        assert "interaction is disabled" in (denied.error or "")
    finally:
        body.close()


def test_browser_interaction_rejects_untrusted_origin(tmp_path: Path) -> None:
    body = BrowserBody(
        tmp_path,
        {
            "enabled": True,
            "interaction_enabled": True,
            "network_isolation_confirmed": True,
            "trusted_interaction_origins": ["https://trusted.example"],
            "browser": "chromium",
            "headless": True,
        },
    )
    try:
        body._set_content_for_test("<button>Do thing</button>")
        denied = body.click({"kind": "text", "text": "Do thing"})
        assert denied.ok is False
        assert "not allow-listed" in (denied.error or "")
    finally:
        body.close()


def test_browser_trusted_page_cannot_click_navigate_to_untrusted_origin(tmp_path: Path) -> None:
    body = BrowserBody(
        tmp_path,
        {
            "enabled": True,
            "interaction_enabled": True,
            "network_isolation_confirmed": True,
            "trusted_interaction_origins": ["about:blank"],
            "browser": "chromium",
            "headless": True,
            "timeout_ms": 5_000,
        },
    )
    try:
        body._set_content_for_test(
            '<a id="escape" href="https://untrusted.invalid/submit">Leave trusted origin</a>'
        )
        denied = body.click({"kind": "css", "selector": "#escape"})
        assert denied.ok is False
        assert body.snapshot().data["url"] == "about:blank"
    finally:
        body.close()
