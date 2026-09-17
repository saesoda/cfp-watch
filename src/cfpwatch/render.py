from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from html import escape

from .models import Call, Delta, Journal, parse_date
from .store import Store

SOON_DAYS = 30

_CSS = """
:root{color-scheme:light dark;--fg:#1a1a1a;--bg:#fafafa;--muted:#666;--line:#ddd;--new:#0a7;--warn:#c40}
@media (prefers-color-scheme:dark){:root{--fg:#eee;--bg:#111;--muted:#aaa;--line:#333}}
body{font:15px/1.45 system-ui,sans-serif;color:var(--fg);background:var(--bg);margin:0;padding:16px;max-width:1100px;margin-inline:auto}
h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.15rem;margin:1.6em 0 .5em}
table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}
th{font-weight:600;color:var(--muted);font-size:.85rem}.muted{color:var(--muted)}.badge{background:var(--new);color:#fff;border-radius:4px;padding:0 6px;font-size:.75rem}
.err{color:var(--warn)}details summary{cursor:pointer;color:var(--muted)}.wrap{overflow-x:auto}
"""


@dataclass
class RunMeta:
    today: dt.date
    model: str
    dashboard_url: str
    journals: list[Journal]


def _jname(meta: RunMeta, key: str) -> str:
    return next((j.name for j in meta.journals if j.key == key), key)


def _abbrev(meta: RunMeta, key: str) -> str:
    j = next((j for j in meta.journals if j.key == key), None)
    return (j.aliases[0] if j and j.aliases else key.upper())


def _deadline_cell(c: Call) -> str:
    if c.deadline:
        return escape(c.deadline)
    return escape(c.deadline_text or "—")


def _row(c: Call, meta: RunMeta, new_ids: set[str]) -> str:
    badge = ' <span class="badge">new</span>' if c.id in new_ids else ""
    eds = escape(", ".join(c.guest_editors)) or "—"
    return (
        f"<tr><td>{escape(_abbrev(meta, c.journal))}</td>"
        f'<td><a href="{escape(c.url, quote=True)}">{escape(c.title)}</a>{badge}'
        f'<div class="muted">{escape(c.summary)}</div></td>'
        f"<td>{_deadline_cell(c)}</td><td>{eds}</td><td>{escape(c.confidence)}</td></tr>"
    )


def _table(calls: list[Call], meta: RunMeta, new_ids: set[str]) -> str:
    if not calls:
        return '<p class="muted">No open calls.</p>'
    rows = "".join(_row(c, meta, new_ids) for c in calls)
    return (
        '<div class="wrap"><table><thead><tr><th>Journal</th><th>Call</th><th>Deadline</th>'
        f"<th>Guest editors</th><th>Conf.</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _status_table(store: Store, meta: RunMeta) -> str:
    rows = []
    for j in meta.journals:
        st = store.journals.get(j.key)
        n_open = sum(1 for c in store.calls.values() if c.journal == j.key and c.status == "open")
        ok = escape(st.last_ok) if st and st.last_ok else "never"
        err = f'<span class="err">{escape(st.last_error)}</span>' if st and st.last_error else ""
        rows.append(f"<tr><td>{escape(j.name)}</td><td>{n_open}</td><td>{ok}</td><td>{err}</td></tr>")
    return (
        '<div class="wrap"><table><thead><tr><th>Journal</th><th>Open</th><th>Last successful check</th>'
        f"<th>Last error</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_dashboard(store: Store, delta: Delta, meta: RunMeta) -> str:
    new_ids = {c.id for c in delta.new}
    open_calls = store.open_calls(meta.today)
    expired = sorted((c for c in store.calls.values() if c.status == "expired"),
                     key=lambda c: c.deadline or "", reverse=True)
    new_section = _table(delta.new, meta, new_ids) if delta.new else '<p class="muted">Nothing new this run.</p>'
    exp_rows = "".join(
        f"<tr><td>{escape(_abbrev(meta, c.journal))}</td><td>{escape(c.title)}</td><td>{_deadline_cell(c)}</td></tr>"
        for c in expired
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cfp-watch</title><style>{_CSS}</style></head>
<body>
<h1>Special-issue calls for papers</h1>
<p class="muted">Last run {escape(meta.today.isoformat())} · model {escape(meta.model)} · {len(open_calls)} open calls</p>
<h2>New this run</h2>
{new_section}
<h2>All open calls</h2>
{_table(open_calls, meta, new_ids)}
<h2>Journal status</h2>
{_status_table(store, meta)}
<details><summary>Expired calls ({len(expired)})</summary><div class="wrap"><table><tbody>{exp_rows}</tbody></table></div></details>
</body></html>
"""


def _email_list(calls: list[Call], meta: RunMeta, extra=None) -> str:
    items = []
    for c in calls:
        line = (f'<li><b>{escape(_abbrev(meta, c.journal))}</b> · <a href="{escape(c.url, quote=True)}">{escape(c.title)}</a>'
                f" · deadline {_deadline_cell(c)}")
        if extra:
            line += f" {extra(c)}"
        if c.summary:
            line += f'<br><span style="color:#666">{escape(c.summary)}</span>'
        items.append(line + "</li>")
    return "<ul>" + "".join(items) + "</ul>"


def render_email(store: Store, delta: Delta, meta: RunMeta) -> tuple[str, str]:
    parts = []
    if delta.new:
        js = sorted({_abbrev(meta, c.journal) for c in delta.new})
        parts.append(f"{len(delta.new)} new call{'s' if len(delta.new) != 1 else ''} ({', '.join(js)})")
    if delta.changed:
        parts.append(f"{len(delta.changed)} deadline{'s' if len(delta.changed) != 1 else ''} changed")
    if delta.errors:
        parts.append(f"{len(delta.errors)} journal error{'s' if len(delta.errors) != 1 else ''}")
    subject = "cfp-watch: " + "; ".join(parts) if parts else "cfp-watch: weekly digest"

    soon_cut = meta.today + dt.timedelta(days=SOON_DAYS)
    soon = [c for c in store.open_calls(meta.today)
            if (d := parse_date(c.deadline)) is not None and meta.today <= d <= soon_cut]

    body = [f"<p>Run of {escape(meta.today.isoformat())}. <a href=\"{escape(meta.dashboard_url, quote=True)}\">Open the dashboard</a>.</p>"]
    if delta.new:
        body.append("<h3>New calls</h3>" + _email_list(delta.new, meta))
    if delta.changed:
        old = {c.id: prev for c, prev in delta.changed}
        body.append("<h3>Changed deadlines</h3>" + _email_list(
            [c for c, _ in delta.changed], meta, extra=lambda c: f"(was {escape(old[c.id] or '—')})"))
    if soon:
        body.append(f"<h3>Deadlines within {SOON_DAYS} days</h3>" + _email_list(soon, meta))
    if delta.errors:
        items = "".join(f"<li><b>{escape(_jname(meta, k))}</b>: {escape(v)}</li>" for k, v in delta.errors.items())
        body.append(f"<h3>Journals not verified this run</h3><ul>{items}</ul>")
    html = f'<div style="font:15px/1.45 system-ui,sans-serif;max-width:720px">{"".join(body)}</div>'
    return subject, html
