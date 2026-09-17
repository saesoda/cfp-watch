import datetime as dt

from cfpwatch.models import Call, Delta, call_id, normalize_title, parse_date


def test_normalize_title_strips_prefix_punctuation_case():
    assert normalize_title("Special Issue on: Digital  Strategy!") == "digital strategy"
    assert normalize_title("Special Issue: Digital Strategy") == "digital strategy"
    assert normalize_title("Call for Papers - Digital Strategy") == "digital strategy"


def test_call_id_is_journal_plus_8_hex():
    cid = call_id("os", "Special Issue on Digital Strategy")
    assert cid.startswith("os:")
    assert len(cid) == 3 + 8
    assert cid == call_id("os", "digital strategy")


def test_parse_date_handles_iso_none_and_garbage():
    assert parse_date("2027-01-15") == dt.date(2027, 1, 15)
    assert parse_date(None) is None
    assert parse_date("Spring 2027") is None


def _call(**kw):
    base = dict(
        journal="os", title="Digital Strategy", guest_editors=["A"], opens=None,
        deadline="2027-01-15", deadline_text=None, url="https://x", source_url="https://x",
        summary="s", confidence="high", first_seen="2026-09-22", last_seen="2026-09-22",
    )
    base.update(kw)
    return Call(**base)


def test_call_roundtrip_dict():
    c = _call()
    d = c.to_dict()
    assert d["id"] == c.id
    assert Call.from_dict(d) == c


def test_delta_should_email():
    assert not Delta([], [], [], {}).should_email
    assert Delta([_call()], [], [], {}).should_email
    assert Delta([], [(_call(), "2026-12-01")], [], {}).should_email
    assert Delta([], [], [_call()], {}).should_email is False
    assert Delta([], [], [], {"os": "boom"}).should_email
