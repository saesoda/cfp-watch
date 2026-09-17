from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import asdict, dataclass, field

_PREFIX = re.compile(
    r"^(call for papers|cfp|special issue|special research forum|special section|thematic issue)"
    r"\s*(on|:|-|–|—)?\s*",
    re.I,
)

_SUFFIX = re.compile(
    r"\s*(:|-|–|—)?\s*(special issue|call for papers|cfp)\s*$",
    re.I,
)


def normalize_title(title: str) -> str:
    t = title.strip().lower()
    t = _PREFIX.sub("", t)
    t = _PREFIX.sub("", t)  # handle "call for papers - special issue on X"
    t = _SUFFIX.sub("", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def call_id(journal_key: str, title: str) -> str:
    digest = hashlib.sha1(normalize_title(title).encode()).hexdigest()[:8]
    return f"{journal_key}:{digest}"


def parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


@dataclass
class Journal:
    key: str
    name: str
    aliases: list[str] = field(default_factory=list)
    publisher: str = ""
    direct_urls: list[str] = field(default_factory=list)
    search_hints: list[str] = field(default_factory=list)


@dataclass
class Call:
    journal: str
    title: str
    guest_editors: list[str]
    opens: str | None
    deadline: str | None
    deadline_text: str | None
    url: str
    source_url: str
    summary: str
    confidence: str
    first_seen: str
    last_seen: str
    status: str = "open"
    misses: int = 0

    @property
    def id(self) -> str:
        return call_id(self.journal, self.title)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Call":
        d = {k: v for k, v in d.items() if k != "id"}
        return cls(**d)


@dataclass
class JournalStatus:
    last_run: str | None = None
    last_ok: str | None = None
    last_error: str | None = None


@dataclass
class Delta:
    new: list[Call]
    changed: list[tuple[Call, str | None]]  # (call, previous deadline)
    expired: list[Call]
    errors: dict[str, str]

    @property
    def should_email(self) -> bool:
        return bool(self.new or self.changed or self.errors)
