import datetime as dt
import json

from cfpwatch.models import Call
from cfpwatch.store import Store, merge, record_error

TODAY = dt.date(2026, 9, 22)


def _found(title, deadline="2027-01-15", journal="os"):
    return Call(
        journal=journal, title=title, guest_editors=["A"], opens=None, deadline=deadline,
        deadline_text=None, url="https://x/" + title[:3], source_url="https://s", summary="s",
        confidence="high", first_seen=TODAY.isoformat(), last_seen=TODAY.isoformat(),
    )


def test_load_missing_file_gives_empty_store(tmp_path):
    s = Store.load(tmp_path / "nope.json")
    assert s.calls == {} and s.journals == {}


def test_save_and_load_roundtrip(tmp_path):
    s = Store()
    merge(s, "os", [_found("Digital Strategy")], TODAY)
    p = tmp_path / "calls.json"
    s.save(p)
    data = json.loads(p.read_text())
    assert data["calls"][0]["id"].startswith("os:")
    s2 = Store.load(p)
    assert s2.calls.keys() == s.calls.keys()
    assert s2.journals["os"].last_ok == TODAY.isoformat()


def test_merge_new_call():
    s = Store()
    d = merge(s, "os", [_found("Digital Strategy")], TODAY)
    assert [c.title for c in d.new] == ["Digital Strategy"]
    assert not d.changed and not d.expired and not d.errors
    c = next(iter(s.calls.values()))
    assert c.first_seen == "2026-09-22" and c.status == "open"


def test_merge_seen_again_updates_last_seen_not_first_seen():
    s = Store()
    merge(s, "os", [_found("Digital Strategy")], TODAY)
    later = TODAY + dt.timedelta(days=7)
    d = merge(s, "os", [_found("Digital Strategy")], later)
    assert not d.new and not d.changed
    c = next(iter(s.calls.values()))
    assert c.first_seen == "2026-09-22" and c.last_seen == later.isoformat() and c.misses == 0


def test_merge_deadline_change_is_reported():
    s = Store()
    merge(s, "os", [_found("Digital Strategy", "2027-01-15")], TODAY)
    d = merge(s, "os", [_found("Digital Strategy", "2027-03-01")], TODAY)
    assert len(d.changed) == 1
    call, old = d.changed[0]
    assert old == "2027-01-15" and call.deadline == "2027-03-01"


def test_merge_null_deadline_does_not_overwrite_known_deadline():
    s = Store()
    merge(s, "os", [_found("Digital Strategy", "2027-01-15")], TODAY)
    d = merge(s, "os", [_found("Digital Strategy", None)], TODAY)
    assert not d.changed
    assert next(iter(s.calls.values())).deadline == "2027-01-15"


def test_merge_expires_past_deadline():
    s = Store()
    merge(s, "os", [_found("Old Call", "2026-09-01")], TODAY - dt.timedelta(days=60))
    d = merge(s, "os", [], TODAY)
    assert [c.title for c in d.expired] == ["Old Call"]
    assert next(iter(s.calls.values())).status == "expired"


def test_merge_marks_stale_after_three_misses_without_deadline():
    s = Store()
    merge(s, "os", [_found("Vague Call", None)], TODAY)
    for i in range(1, 4):
        merge(s, "os", [], TODAY + dt.timedelta(days=7 * i))
    c = next(iter(s.calls.values()))
    assert c.misses == 3 and c.status == "stale"


def test_merge_fuzzy_duplicate_is_same_call():
    s = Store()
    merge(s, "os", [_found("Special Issue on Digital Strategy and Organizations")], TODAY)
    d = merge(s, "os", [_found("Digital Strategy and Organizations: Special Issue")], TODAY)
    assert not d.new and len(s.calls) == 1


def test_merge_only_touches_its_own_journal():
    s = Store()
    merge(s, "smj", [_found("SMJ Call", journal="smj")], TODAY)
    d = merge(s, "os", [], TODAY)
    assert not d.expired
    assert next(iter(s.calls.values())).misses == 0


def test_record_error_sets_status_and_leaves_calls():
    s = Store()
    merge(s, "os", [_found("Digital Strategy")], TODAY)
    record_error(s, "os", "boom", TODAY + dt.timedelta(days=7))
    st = s.journals["os"]
    assert st.last_error == "boom" and st.last_ok == TODAY.isoformat()
    assert next(iter(s.calls.values())).misses == 0


def test_open_calls_sorted_deadline_then_none_last():
    s = Store()
    merge(s, "os", [_found("B", "2027-03-01"), _found("A", "2027-01-01"), _found("C", None)], TODAY)
    assert [c.title for c in s.open_calls(TODAY)] == ["A", "B", "C"]
