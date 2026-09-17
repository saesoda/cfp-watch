import io
import urllib.error
from pathlib import Path

import pytest

from cfpwatch import fetch
from cfpwatch.fetch import fetch_text, html_to_text, looks_blocked

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "name",
    ["cloudflare_challenge.html", "springer_client_challenge.html", "elsevier_captcha.txt"],
)
def test_bot_pages_are_detected(name):
    assert looks_blocked((FIX / name).read_text(encoding="utf-8", errors="ignore"))


def test_real_page_is_not_blocked():
    assert not looks_blocked((FIX / "egos_real_page.html").read_text(encoding="utf-8", errors="ignore"))


def test_html_to_text_strips_tags_scripts_and_collapses_whitespace():
    html = "<html><head><script>var x=1;</script><style>p{}</style></head><body><h1>Hi</h1>\n\n<p>there &amp; you</p></body></html>"
    assert html_to_text(html) == "Hi there & you"


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_text_returns_text_for_real_page(monkeypatch):
    body = (FIX / "egos_real_page.html").read_bytes()
    monkeypatch.setattr(fetch, "urlopen", lambda req, timeout: _Resp(body))
    text = fetch_text("https://www.egos.org/")
    assert text and "EGOS" in text
    assert len(text) <= 30000


def test_fetch_text_returns_none_for_bot_page(monkeypatch):
    body = (FIX / "cloudflare_challenge.html").read_bytes()
    monkeypatch.setattr(fetch, "urlopen", lambda req, timeout: _Resp(body))
    assert fetch_text("https://pubsonline.informs.org/x") is None


def test_fetch_text_returns_none_on_http_error(monkeypatch):
    def boom(req, timeout):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(fetch, "urlopen", boom)
    assert fetch_text("https://x") is None


def test_fetch_text_returns_none_on_network_error(monkeypatch):
    def boom(req, timeout):
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(fetch, "urlopen", boom)
    assert fetch_text("https://x") is None
