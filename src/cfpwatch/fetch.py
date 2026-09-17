from __future__ import annotations

import html as htmllib
import re
import urllib.error
import urllib.request
from urllib.request import urlopen  # module-level so tests can monkeypatch

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

_BLOCK_MARKERS = (
    "just a moment",
    "performing security verification",
    "are you a robot",
    "client challenge",
    "access denied",
    "verify you are human",
    "enable javascript and cookies to continue",
)

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


def looks_blocked(text: str) -> bool:
    head = text[:20000].lower()
    return any(m in head for m in _BLOCK_MARKERS)


def html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE.sub(" ", html)
    text = _TAGS.sub(" ", text)
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_text(url: str, timeout: float = 20.0, max_chars: int = 30000) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            raw = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    body = raw.decode("utf-8", errors="ignore")
    if looks_blocked(body):
        return None
    return html_to_text(body)[:max_chars]
