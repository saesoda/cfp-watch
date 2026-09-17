import datetime as dt
import json
from pathlib import Path

from cfpwatch.cli import Paths, RunOptions, run_once
from cfpwatch.models import Call
from cfpwatch.search import SearchResult

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 9, 22)


def _paths(tmp_path):
    p = Paths(root=tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "journals.yaml").write_text((ROOT / "config" / "journals.yaml").read_text())
    return p


def _call(journal, title, deadline="2027-01-01"):
    return Call(journal=journal, title=title, guest_editors=[], opens=None, deadline=deadline, deadline_text=None,
                url="https://u", source_url="https://s", summary="", confidence="high",
                first_seen=TODAY.isoformat(), last_seen=TODAY.isoformat())


def _searcher(results: dict):
    seen = []

    def search(client, journal, today, evidence, model, max_searches=5):
        seen.append((journal.key, evidence, model))
        return results.get(journal.key, SearchResult())

    search.seen = seen
    return search


def test_run_once_writes_store_dashboard_and_emails_on_new(tmp_path):
    paths = _paths(tmp_path)
    sent = []
    searcher = _searcher({"os": SearchResult(calls=[_call("os", "Digital Strategy")])})
    env = {"GMAIL_USER": "me@gmail.com", "GMAIL_APP_PASSWORD": "pw"}
    delta = run_once(RunOptions(journals=["os", "smj"]), paths, client=object(), today=TODAY,
                     sender=lambda subject, html, user, password, to=None: sent.append((subject, user, to)),
                     fetcher=lambda url: None, searcher=searcher, env=env)
    assert [c.title for c in delta.new] == ["Digital Strategy"]
    assert sorted(k for k, _, _ in searcher.seen) == ["os", "smj"]
    data = json.loads(paths.store.read_text())
    assert len(data["calls"]) == 1 and "smj" in data["journals"] and "os" in data["journals"]
    assert "Digital Strategy" in paths.dashboard.read_text()
    assert sent and sent[0][0].startswith("cfp-watch: 1 new call") and sent[0][1] == "me@gmail.com"


def test_run_once_passes_direct_fetch_evidence(tmp_path):
    paths = _paths(tmp_path)
    searcher = _searcher({})
    run_once(RunOptions(journals=["asq"], no_email=True), paths, client=object(), today=TODAY,
             fetcher=lambda url: "ASQ PAGE TEXT" if "cornell" in url else None, searcher=searcher, env={})
    assert searcher.seen[0][1] == ["ASQ PAGE TEXT"]


def test_run_once_records_error_and_emails(tmp_path):
    paths = _paths(tmp_path)
    sent = []
    searcher = _searcher({"os": SearchResult(error="api error 500")})
    run_once(RunOptions(journals=["os"]), paths, client=object(), today=TODAY,
             sender=lambda subject, html, user, password, to=None: sent.append(subject),
             fetcher=lambda url: None, searcher=searcher, env={"GMAIL_USER": "u", "GMAIL_APP_PASSWORD": "p"})
    data = json.loads(paths.store.read_text())
    assert data["journals"]["os"]["last_error"] == "api error 500"
    assert sent == ["cfp-watch: 1 journal error"]


def test_run_once_no_email_when_nothing_new(tmp_path):
    paths = _paths(tmp_path)
    sent = []
    run_once(RunOptions(journals=["os"]), paths, client=object(), today=TODAY,
             sender=lambda *a, **k: sent.append(a), fetcher=lambda url: None, searcher=_searcher({}),
             env={"GMAIL_USER": "u", "GMAIL_APP_PASSWORD": "p"})
    assert sent == []


def test_run_once_force_email_sends_even_when_empty(tmp_path):
    paths = _paths(tmp_path)
    sent = []
    run_once(RunOptions(journals=["os"], force_email=True), paths, client=object(), today=TODAY,
             sender=lambda subject, *a, **k: sent.append(subject), fetcher=lambda url: None,
             searcher=_searcher({}), env={"GMAIL_USER": "u", "GMAIL_APP_PASSWORD": "p"})
    assert sent == ["cfp-watch: weekly digest"]


def test_run_once_dry_run_writes_nothing_and_sends_nothing(tmp_path):
    paths = _paths(tmp_path)
    sent = []
    searcher = _searcher({"os": SearchResult(calls=[_call("os", "Digital Strategy")])})
    delta = run_once(RunOptions(journals=["os"], dry_run=True, force_email=True), paths, client=object(),
                     today=TODAY, sender=lambda *a, **k: sent.append(a), fetcher=lambda url: None,
                     searcher=searcher, env={"GMAIL_USER": "u", "GMAIL_APP_PASSWORD": "p"})
    assert len(delta.new) == 1
    assert not paths.store.exists() and not paths.dashboard.exists() and sent == []


def test_run_once_skips_email_without_credentials(tmp_path, capsys):
    paths = _paths(tmp_path)
    searcher = _searcher({"os": SearchResult(calls=[_call("os", "Digital Strategy")])})

    def must_not_send(*a, **k):
        raise AssertionError("should not send")

    run_once(RunOptions(journals=["os"]), paths, client=object(), today=TODAY,
             sender=must_not_send, fetcher=lambda url: None, searcher=searcher, env={})
    assert "GMAIL_USER" in capsys.readouterr().out


def test_main_treats_empty_model_env_as_default(monkeypatch):
    from cfpwatch import cli
    from cfpwatch.models import Delta

    monkeypatch.setenv("CFPWATCH_MODEL", "")
    captured = {}

    def fake_run_once(opts, paths, client, today):
        captured["model"] = opts.model
        return Delta([], [], [], {})

    monkeypatch.setattr(cli, "run_once", fake_run_once)
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda: object())
    assert cli.main(["run", "--dry-run"]) == 0
    assert captured["model"] == "claude-sonnet-5"
