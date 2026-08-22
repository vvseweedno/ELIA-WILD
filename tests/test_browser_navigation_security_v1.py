from __future__ import annotations

from pathlib import Path

import pytest

from elia.body import browser as browser_module
from elia.body.browser import BrowserBody


class _Response:
    status = 200


def test_explicit_navigation_closes_interacted_page_before_lifting_origin_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    body = BrowserBody(tmp_path, {"enabled": True})
    events: list[tuple[str, bool]] = []

    class OldPage:
        url = "https://trusted.example/form"

        def close(self) -> None:
            events.append(("old_page_closed", body._interaction_active))

    class NewPage:
        url = "https://destination.example/path"

        def goto(self, *args, **kwargs):
            events.append(("new_page_navigated", body._interaction_active))
            return _Response()

    class Context:
        def new_page(self):
            events.append(("new_page_created", body._interaction_active))
            return NewPage()

    monkeypatch.setattr(BrowserBody, "enabled", property(lambda self: True))
    monkeypatch.setattr(browser_module, "assert_http_url", lambda *args, **kwargs: None)
    monkeypatch.setattr(body, "_snapshot", lambda: {"url": body._page.url})
    body._context = Context()
    body._page = OldPage()
    body._interaction_active = True

    result = body.navigate("https://destination.example/path")
    assert result.ok is True
    assert events == [
        ("old_page_closed", True),
        ("new_page_created", True),
        ("new_page_navigated", False),
    ]


def test_explicit_navigation_fails_closed_on_cross_origin_redirect_signal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    body = BrowserBody(tmp_path, {"enabled": True})

    class Page:
        url = "https://destination.example/path"

        def goto(self, *args, **kwargs):
            body._interaction_denied_url = "https://redirected.example/landing"
            return _Response()

    monkeypatch.setattr(BrowserBody, "enabled", property(lambda self: True))
    monkeypatch.setattr(browser_module, "assert_http_url", lambda *args, **kwargs: None)
    body._page = Page()

    with pytest.raises(PermissionError, match="unauthorized origin"):
        body.navigate("https://destination.example/path")
