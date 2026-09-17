import datetime as dt
import json

import pytest

from cfpwatch.models import Call, Delta
from cfpwatch.store import Store, expire_all, merge, read_last_delta, record_error, write_last_delta

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


def test_merge_fuzzy_near_duplicate_via_token_sort_ratio_is_still_distinct_call():
    s = Store()
    merge(s, "os", [_found("Digital Strategy")], TODAY)
    d = merge(s, "os", [_found("Digital Strategy in Emerging Markets")], TODAY)
    assert len(s.calls) == 2
    assert len(d.new) == 1


def test_merge_reopens_expired_call_seen_again_with_later_deadline():
    s = Store()
    merge(s, "os", [_found("Old Call", "2026-09-01")], TODAY - dt.timedelta(days=60))
    merge(s, "os", [], TODAY)
    c = next(iter(s.calls.values()))
    assert c.status == "expired"
    d = merge(s, "os", [_found("Old Call", "2027-06-01")], TODAY)
    c = next(iter(s.calls.values()))
    assert c.status == "open"
    assert c.id in {x.id for x in d.new}
    assert c.id in {x.id for x in s.open_calls(TODAY)}


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


def test_open_calls_excludes_past_deadline_even_if_status_still_open():
    s = Store()
    merge(s, "os", [_found("Soon", "2026-09-25")], TODAY)
    later = TODAY + dt.timedelta(days=10)
    assert next(iter(s.calls.values())).status == "open"
    assert s.open_calls(later) == []


def test_expire_all_marks_open_past_deadline_calls_across_journals():
    s = Store()
    merge(s, "os", [_found("OS Call", "2026-09-25")], TODAY)
    merge(s, "smj", [_found("SMJ Call", "2026-09-25", journal="smj")], TODAY)
    later = TODAY + dt.timedelta(days=10)
    expired = expire_all(s, later)
    assert {c.title for c in expired} == {"OS Call", "SMJ Call"}
    assert all(c.status == "expired" for c in s.calls.values())


def test_expire_all_does_not_touch_already_expired_or_future_calls():
    s = Store()
    merge(s, "os", [_found("Future", "2027-01-01")], TODAY)
    merge(s, "os", [_found("Old Call", "2026-09-01")], TODAY - dt.timedelta(days=60))
    merge(s, "os", [_found("Future")], TODAY)  # expires "Old Call"
    expired = expire_all(s, TODAY)
    assert expired == []


def test_journal_recovers_after_error():
    s = Store()
    record_error(s, "os", "boom", TODAY)
    merge(s, "os", [_found("Digital Strategy")], TODAY + dt.timedelta(days=7))
    st = s.journals["os"]
    assert st.last_error is None
    assert st.last_ok == (TODAY + dt.timedelta(days=7)).isoformat()


def test_load_corrupt_file_raises_value_error(tmp_path):
    p = tmp_path / "calls.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt store"):
        Store.load(p)


def test_write_and_read_last_delta_roundtrip(tmp_path):
    s = Store()
    merge(s, "os", [_found("Digital Strategy", "2027-01-01")], TODAY)
    merge(s, "smj", [_found("Changed Deadline", "2027-02-01", journal="smj")], TODAY)
    merge(s, "smj", [_found("Old Expiring", "2026-09-01", journal="smj")], TODAY)
    new_call = next(c for c in s.calls.values() if c.title == "Digital Strategy")
    changed_call = next(c for c in s.calls.values() if c.title == "Changed Deadline")
    expired_call = next(c for c in s.calls.values() if c.title == "Old Expiring")
    delta = Delta(new=[new_call], changed=[(changed_call, "2027-01-15")], expired=[expired_call],
                  errors={"jms": "ingest: boom"})
    p = tmp_path / "last_delta.json"
    write_last_delta(p, delta, TODAY)

    data = json.loads(p.read_text())
    assert data["date"] == TODAY.isoformat()
    assert data["new"] == [new_call.id]
    assert data["changed"] == [[changed_call.id, "2027-01-15"]]
    assert data["expired"] == [expired_call.id]
    assert data["errors"] == {"jms": "ingest: boom"}

    result = read_last_delta(p, s)
    assert result is not None
    read_delta, read_date = result
    assert read_date == TODAY
    assert [c.id for c in read_delta.new] == [new_call.id]
    assert [(c.id, old) for c, old in read_delta.changed] == [(changed_call.id, "2027-01-15")]
    assert [c.id for c in read_delta.expired] == [expired_call.id]
    assert read_delta.errors == {"jms": "ingest: boom"}


def test_read_last_delta_missing_file_returns_none(tmp_path):
    assert read_last_delta(tmp_path / "nope.json", Store()) is None


def test_read_last_delta_skips_unknown_ids(tmp_path):
    s = Store()
    merge(s, "os", [_found("Digital Strategy")], TODAY)
    known = next(iter(s.calls.values()))
    p = tmp_path / "last_delta.json"
    p.write_text(json.dumps({
        "date": TODAY.isoformat(),
        "new": [known.id, "os:deadbeef"],
        "changed": [["smj:ffffffff", "2027-01-01"]],
        "expired": ["amj:00000000"],
        "errors": {},
    }), encoding="utf-8")
    read_delta, read_date = read_last_delta(p, s)
    assert [c.id for c in read_delta.new] == [known.id]
    assert read_delta.changed == []
    assert read_delta.expired == []
    assert read_date == TODAY


def test_write_and_read_last_delta_changed_entry_with_none_old_deadline(tmp_path):
    s = Store()
    merge(s, "os", [_found("Digital Strategy", None)], TODAY)
    call = next(iter(s.calls.values()))
    delta = Delta(new=[], changed=[(call, None)], expired=[], errors={})
    p = tmp_path / "last_delta.json"
    write_last_delta(p, delta, TODAY)

    data = json.loads(p.read_text())
    assert data["changed"] == [[call.id, None]]

    read_delta, _ = read_last_delta(p, s)
    assert read_delta.changed == [(call, None)]
