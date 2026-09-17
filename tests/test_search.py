import datetime as dt
from types import SimpleNamespace

import anthropic

from cfpwatch.models import Journal
from cfpwatch.search import (
    DEFAULT_MODEL,
    FoundCall,
    SearchOutput,
    build_prompt,
    find_calls,
)

TODAY = dt.date(2026, 9, 22)
J = Journal(key="os", name="Organization Science", aliases=["OrgSci"], publisher="INFORMS",
            direct_urls=[], search_hints=["site:pubsonline.informs.org orsc"])


def test_build_prompt_mentions_date_name_aliases_hints_and_evidence():
    system, user = build_prompt(J, TODAY, ["EVIDENCE TEXT"])
    assert "2026-09-22" in system
    assert "Organization Science" in user and "OrgSci" in system
    assert "site:pubsonline.informs.org orsc" in system
    assert "EVIDENCE TEXT" in user
    assert "conference" in system.lower()  # exclusion rule present


def test_build_prompt_without_evidence_has_no_evidence_section():
    _, user = build_prompt(J, TODAY, [])
    assert "Evidence" not in user


class _FakeParse:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _client(*responses):
    parse = _FakeParse(responses)
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    return client, parse


def _resp(calls, stop_reason="end_turn", parsed=True):
    out = SearchOutput(calls=calls, notes="n") if parsed else None
    return SimpleNamespace(
        parsed_output=out, stop_reason=stop_reason, content=[SimpleNamespace(type="text", text="{}")],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


def test_find_calls_maps_output_to_calls_and_request_shape():
    fc = FoundCall(title="Digital Strategy", guest_editors=["A"], opens=None, deadline="2027-01-15",
                   deadline_text=None, url="https://u", source_url="https://s", summary="sum", confidence="high")
    client, parse = _client(_resp([fc]))
    res = find_calls(client, J, TODAY, [], model=DEFAULT_MODEL)
    assert res.error is None
    assert len(res.calls) == 1 and res.calls[0].journal == "os" and res.calls[0].deadline == "2027-01-15"
    assert res.calls[0].first_seen == "2026-09-22"
    assert res.input_tokens == 100 and res.output_tokens == 20
    kw = parse.calls[0]
    assert kw["model"] == "claude-sonnet-5"
    assert kw["output_format"] is SearchOutput
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in kw["betas"]
    types = {t["type"] for t in kw["tools"]}
    assert "web_search_20260209" in types and "web_fetch_20260209" in types
    assert next(t for t in kw["tools"] if t["type"] == "web_search_20260209")["max_uses"] == 5


def test_find_calls_drops_calls_with_past_deadline_or_empty_title():
    ok = FoundCall(title="Future", guest_editors=[], opens=None, deadline="2027-01-01", deadline_text=None,
                   url="https://u", source_url="https://s", summary="", confidence="medium")
    past = FoundCall(title="Past", guest_editors=[], opens=None, deadline="2026-01-01", deadline_text=None,
                     url="https://u", source_url="https://s", summary="", confidence="high")
    empty = FoundCall(title="  ", guest_editors=[], opens=None, deadline=None, deadline_text=None,
                      url="https://u", source_url="https://s", summary="", confidence="low")
    client, _ = _client(_resp([ok, past, empty]))
    res = find_calls(client, J, TODAY, [], model=DEFAULT_MODEL)
    assert [c.title for c in res.calls] == ["Future"]


def test_find_calls_continues_after_pause_turn():
    fc = FoundCall(title="X", guest_editors=[], opens=None, deadline=None, deadline_text=None,
                   url="https://u", source_url="https://s", summary="", confidence="low")
    client, parse = _client(_resp([], stop_reason="pause_turn", parsed=False), _resp([fc]))
    res = find_calls(client, J, TODAY, [], model=DEFAULT_MODEL)
    assert res.error is None and len(res.calls) == 1
    assert len(parse.calls) == 2
    assert parse.calls[1]["messages"][1]["role"] == "assistant"


def test_find_calls_reports_refusal_as_error():
    client, _ = _client(_resp([], stop_reason="refusal", parsed=False))
    res = find_calls(client, J, TODAY, [], model=DEFAULT_MODEL)
    assert res.error and "refusal" in res.error and res.calls == []


def test_find_calls_reports_sdk_errors_without_raising():
    client, _ = _client(anthropic.APIConnectionError(request=None))
    res = find_calls(client, J, TODAY, [], model=DEFAULT_MODEL)
    assert res.error and res.calls == []
