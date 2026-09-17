import datetime as dt

from cfpwatch.models import Call, Delta, Journal
from cfpwatch.render import RunMeta, render_dashboard, render_email
from cfpwatch.store import Store, merge, record_error

TODAY = dt.date(2026, 9, 22)
JOURNALS = [Journal(key="os", name="Organization Science"), Journal(key="smj", name="Strategic Management Journal")]
META = RunMeta(today=TODAY, model="claude-sonnet-5", dashboard_url="https://saesoda.github.io/cfp-watch/", journals=JOURNALS)


def _found(title, deadline, journal="os"):
    return Call(journal=journal, title=title, guest_editors=["Ed One"], opens=None, deadline=deadline,
                deadline_text=None, url="https://x/" + str(abs(hash(title)) % 1000), source_url="https://s", summary="Sum <b>",
                confidence="high", first_seen=TODAY.isoformat(), last_seen=TODAY.isoformat())


def _store():
    s = Store()
    d1 = merge(s, "os", [_found("Later Call", "2027-03-01"), _found("Soon Call", "2026-10-10")], TODAY)
    merge(s, "smj", [_found("Expired", "2026-01-01", "smj")], TODAY - dt.timedelta(days=300))
    merge(s, "smj", [], TODAY)
    record_error(s, "smj", "boom", TODAY)
    delta = Delta(new=d1.new, changed=[], expired=[], errors={"smj": "boom"})
    return s, delta


def test_dashboard_lists_open_calls_once_sorted_and_escapes():
    s, delta = _store()
    html = render_dashboard(s, delta, META)
    assert html.count("Soon Call") == 2  # once in "new this week", once in open table
    assert html.index("Soon Call", html.index("All open calls")) < html.index("Later Call", html.index("All open calls"))
    assert "Sum &lt;b&gt;" in html
    assert "Expired" in html  # in collapsed section
    assert "boom" in html and "Organization Science" in html
    assert "2026-09-22" in html


def test_dashboard_with_empty_store_renders():
    html = render_dashboard(Store(), Delta([], [], [], {}), META)
    assert "No open calls" in html


def test_email_subject_counts_new_and_lists_journals():
    s, delta = _store()
    subject, html = render_email(s, delta, META)
    assert subject == "cfp-watch: 2 new calls (OS); 1 journal error"
    assert "Soon Call" in html and "boom" in html and META.dashboard_url in html


def test_email_includes_deadlines_within_30_days_and_changed():
    s, _ = _store()
    call = s.calls[next(k for k in s.calls if "Soon" in s.calls[k].title)]
    delta = Delta(new=[], changed=[(call, "2026-09-30")], expired=[], errors={})
    subject, html = render_email(s, delta, META)
    assert subject == "cfp-watch: 1 deadline changed"
    assert "2026-09-30" in html and "Deadlines within 30 days" in html and "Soon Call" in html
    assert "Later Call" not in html.split("Deadlines within 30 days")[1]
