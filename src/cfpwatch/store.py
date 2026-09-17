from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from .models import Call, Delta, JournalStatus, normalize_title, parse_date

STALE_AFTER = 3
FUZZY_THRESHOLD = 90


@dataclass
class Store:
    calls: dict[str, Call] = field(default_factory=dict)
    journals: dict[str, JournalStatus] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Store":
        if not path.exists():
            return cls()
        raw = path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"corrupt store {path}: {e}") from e
        calls = {c["id"]: Call.from_dict(c) for c in data.get("calls", [])}
        journals = {k: JournalStatus(**v) for k, v in data.get("journals", {}).items()}
        return cls(calls=calls, journals=journals)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "calls": [c.to_dict() for c in sorted(self.calls.values(), key=lambda c: c.id)],
            "journals": {k: asdict(v) for k, v in sorted(self.journals.items())},
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def open_calls(self, today: dt.date) -> list[Call]:
        def key(c: Call):
            d = parse_date(c.deadline)
            return (d is None, d or dt.date.max, c.journal, c.title)

        def is_open(c: Call) -> bool:
            if c.status != "open":
                return False
            d = parse_date(c.deadline)
            return d is None or d >= today

        return sorted((c for c in self.calls.values() if is_open(c)), key=key)


def _match_existing(store: Store, journal_key: str, found: Call) -> Call | None:
    if found.id in store.calls:
        return store.calls[found.id]
    norm = normalize_title(found.title)
    best, best_score = None, 0
    for c in store.calls.values():
        if c.journal != journal_key:
            continue
        score = fuzz.token_sort_ratio(norm, normalize_title(c.title))
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= FUZZY_THRESHOLD else None


def merge(store: Store, journal_key: str, found: list[Call], today: dt.date) -> Delta:
    delta = Delta(new=[], changed=[], expired=[], errors={})
    seen_ids: set[str] = set()
    ts = today.isoformat()

    for f in found:
        existing = _match_existing(store, journal_key, f)
        if existing is None:
            f.journal = journal_key
            f.first_seen = ts
            f.last_seen = ts
            f.status = "open"
            f.misses = 0
            store.calls[f.id] = f
            delta.new.append(f)
            seen_ids.add(f.id)
            continue
        old_deadline = existing.deadline
        old_status = existing.status
        if f.deadline and f.deadline != existing.deadline:
            existing.deadline = f.deadline
            delta.changed.append((existing, old_deadline))
        for attr in ("guest_editors", "opens", "deadline_text", "url", "source_url", "summary", "confidence"):
            val = getattr(f, attr)
            if val:
                setattr(existing, attr, val)
        existing.last_seen = ts
        existing.misses = 0
        d = parse_date(existing.deadline)
        if d is None or d >= today:
            existing.status = "open"
        if old_status == "expired" and existing.status == "open":
            delta.new.append(existing)
        seen_ids.add(existing.id)

    for c in store.calls.values():
        if c.journal != journal_key or c.status != "open":
            continue
        if c.id not in seen_ids:
            c.misses += 1
        d = parse_date(c.deadline)
        if d is not None and d < today:
            c.status = "expired"
            delta.expired.append(c)
        elif d is None and c.misses >= STALE_AFTER:
            c.status = "stale"

    st = store.journals.setdefault(journal_key, JournalStatus())
    st.last_run = ts
    st.last_ok = ts
    st.last_error = None
    return delta


def record_error(store: Store, journal_key: str, error: str, today: dt.date) -> None:
    st = store.journals.setdefault(journal_key, JournalStatus())
    st.last_run = today.isoformat()
    st.last_error = error[:500]


def write_last_delta(path: Path, delta: Delta, today: dt.date) -> None:
    data = {
        "date": today.isoformat(),
        "new": [c.id for c in delta.new],
        "changed": [[c.id, old] for c, old in delta.changed],
        "expired": [c.id for c in delta.expired],
        "errors": dict(delta.errors),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_last_delta(path: Path, store: Store) -> tuple[Delta, dt.date] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    new = [store.calls[i] for i in data.get("new", []) if i in store.calls]
    changed = [(store.calls[i], old) for i, old in data.get("changed", []) if i in store.calls]
    expired = [store.calls[i] for i in data.get("expired", []) if i in store.calls]
    errors = dict(data.get("errors", {}))
    delta = Delta(new=new, changed=changed, expired=expired, errors=errors)
    return delta, dt.date.fromisoformat(data["date"])


def expire_all(store: Store, today: dt.date) -> list[Call]:
    expired = []
    for c in store.calls.values():
        if c.status != "open":
            continue
        d = parse_date(c.deadline)
        if d is not None and d < today:
            c.status = "expired"
            expired.append(c)
    return expired
