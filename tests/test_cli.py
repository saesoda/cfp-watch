import datetime as dt
import json
from pathlib import Path

import pytest

from cfpwatch.cli import Paths, RunOptions, ingest_once, notify_last, run_once
from cfpwatch.models import Call, Delta
from cfpwatch.search import SearchResult
from cfpwatch.store import Store, write_last_delta

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


def test_run_once_raises_when_email_due_without_credentials(tmp_path):
    paths = _paths(tmp_path)
    searcher = _searcher({"os": SearchResult(calls=[_call("os", "Digital Strategy")])})

    def must_not_send(*a, **k):
        raise AssertionError("should not send")

    with pytest.raises(RuntimeError, match="GMAIL_USER"):
        run_once(RunOptions(journals=["os"]), paths, client=object(), today=TODAY,
                 sender=must_not_send, fetcher=lambda url: None, searcher=searcher, env={})
    assert paths.store.exists()
    assert paths.dashboard.exists()


def test_run_once_prints_token_totals(tmp_path, capsys):
    paths = _paths(tmp_path)
    searcher = _searcher({
        "os": SearchResult(calls=[_call("os", "Digital Strategy")], input_tokens=100, output_tokens=20),
        "smj": SearchResult(input_tokens=50, output_tokens=5),
    })
    run_once(RunOptions(journals=["os", "smj"], no_email=True), paths, client=object(), today=TODAY,
             fetcher=lambda url: None, searcher=searcher, env={})
    out = capsys.readouterr().out
    assert "tokens total: 150/25" in out


def test_run_once_warns_on_unknown_journal_keys(tmp_path, capsys):
    paths = _paths(tmp_path)
    run_once(RunOptions(journals=["os", "nope"], no_email=True), paths, client=object(), today=TODAY,
             fetcher=lambda url: None, searcher=_searcher({}), env={})
    assert "warning: unknown journal keys: ['nope']" in capsys.readouterr().out


def test_main_render_creates_dashboard_via_cfpwatch_root_env(tmp_path, monkeypatch):
    from cfpwatch import cli

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "journals.yaml").write_text((ROOT / "config" / "journals.yaml").read_text())
    monkeypatch.setenv("CFPWATCH_ROOT", str(tmp_path))
    assert cli.main(["render"]) == 0
    assert (tmp_path / "docs" / "index.html").exists()


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


def test_run_once_writes_last_delta(tmp_path):
    paths = _paths(tmp_path)
    searcher = _searcher({"os": SearchResult(calls=[_call("os", "Digital Strategy")])})
    run_once(RunOptions(journals=["os"], no_email=True), paths, client=object(), today=TODAY,
             fetcher=lambda url: None, searcher=searcher, env={})
    data = json.loads(paths.last_delta.read_text())
    assert data["date"] == TODAY.isoformat()
    assert len(data["new"]) == 1


def _found_call_json(title="Digital Strategy", deadline="2027-01-01", confidence="high"):
    return {
        "title": title,
        "guest_editors": [],
        "opens": None,
        "deadline": deadline,
        "deadline_text": None,
        "url": "https://example.org/call",
        "source_url": "https://example.org/call",
        "summary": "A special issue.",
        "confidence": confidence,
    }


def _search_output_json(calls, notes=""):
    return json.dumps({"calls": calls, "notes": notes})


def test_ingest_once_happy_path_writes_store_dashboard_last_delta_and_emails(tmp_path):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    (source / "os.json").write_text(_search_output_json([_found_call_json()]), encoding="utf-8")
    sent = []
    delta = ingest_once(RunOptions(), paths, source, TODAY,
                        sender=lambda subject, html, user, pw, to=None: sent.append((subject, html, user, to)),
                        env={"GMAIL_USER": "me@gmail.com", "GMAIL_APP_PASSWORD": "pw"})
    assert [c.title for c in delta.new] == ["Digital Strategy"]
    data = json.loads(paths.store.read_text())
    assert len(data["calls"]) == 1
    assert "Digital Strategy" in paths.dashboard.read_text()
    assert paths.last_delta.exists()
    assert sent and sent[0][0].startswith("cfp-watch: 1 new call") and sent[0][2] == "me@gmail.com"
    # Every other configured journal had no file this run, so each is
    # recorded as an error rather than silently looking "checked, clean."
    assert "os" not in delta.errors
    assert "smj" in delta.errors
    assert delta.errors["smj"] == "ingest: no results file"
    assert "journal error" in sent[0][0]


def test_ingest_once_fills_missing_optional_keys_with_defaults(tmp_path):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    minimal_call = {"title": "Digital Strategy", "url": "https://example.org/call"}
    (source / "os.json").write_text(json.dumps({"calls": [minimal_call], "notes": ""}), encoding="utf-8")
    delta = ingest_once(RunOptions(no_email=True), paths, source, TODAY, env={})
    assert [c.title for c in delta.new] == ["Digital Strategy"]
    call = delta.new[0]
    assert call.confidence == "low"
    assert call.guest_editors == []
    assert call.opens is None
    assert call.deadline is None
    assert call.deadline_text is None
    assert call.source_url == "https://example.org/call"
    assert call.summary == ""


def test_ingest_once_invalid_file_records_error_and_emails_it(tmp_path):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    (source / "smj.json").write_text(json.dumps({"calls": [{"title": "Bad"}], "notes": ""}), encoding="utf-8")
    sent = []
    delta = ingest_once(RunOptions(), paths, source, TODAY,
                        sender=lambda subject, html, user, pw, to=None: sent.append((subject, html)),
                        env={"GMAIL_USER": "u", "GMAIL_APP_PASSWORD": "p"})
    assert "smj" in delta.errors
    data = json.loads(paths.store.read_text())
    assert data["journals"]["smj"]["last_error"].startswith("ingest:")
    assert sent and "journal error" in sent[0][0]
    assert "ingest:" in sent[0][1]


def test_ingest_once_records_error_for_journals_with_no_file(tmp_path, capsys):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    (source / "os.json").write_text(_search_output_json([]), encoding="utf-8")
    delta = ingest_once(RunOptions(no_email=True), paths, source, TODAY, env={})
    assert "os" not in delta.errors
    assert delta.errors["smj"] == "ingest: no results file"
    data = json.loads(paths.store.read_text())
    assert data["journals"]["smj"]["last_error"] == "ingest: no results file"
    out = capsys.readouterr().out
    assert "no file for:" in out and "smj" in out


def test_ingest_once_skips_unknown_journal_file_with_warning(tmp_path, capsys):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    (source / "not-a-journal.json").write_text(_search_output_json([]), encoding="utf-8")
    (source / "os.json").write_text(_search_output_json([]), encoding="utf-8")
    ingest_once(RunOptions(no_email=True), paths, source, TODAY, env={})
    out = capsys.readouterr().out
    assert "warning: unknown journal file not-a-journal.json" in out


def test_ingest_once_raises_filenotfound_for_missing_source(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(FileNotFoundError, match="ingest source not found"):
        ingest_once(RunOptions(), paths, tmp_path / "does-not-exist", TODAY, env={})
    assert not paths.store.exists() and not paths.last_delta.exists()


def test_ingest_once_exits_when_source_has_no_journal_files(tmp_path, capsys):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()  # exists, but empty
    with pytest.raises(SystemExit) as exc_info:
        ingest_once(RunOptions(), paths, source, TODAY, env={})
    assert exc_info.value.code == 2
    assert f"error: no journal files found in {source}" in capsys.readouterr().out
    assert not paths.store.exists() and not paths.dashboard.exists() and not paths.last_delta.exists()


def test_ingest_once_exits_when_source_has_only_unknown_files(tmp_path, capsys):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    (source / "not-a-journal.json").write_text(_search_output_json([]), encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        ingest_once(RunOptions(), paths, source, TODAY, env={})
    assert exc_info.value.code == 2
    out = capsys.readouterr().out
    assert "warning: unknown journal file not-a-journal.json" in out
    assert f"error: no journal files found in {source}" in out
    assert not paths.store.exists() and not paths.last_delta.exists()


def test_ingest_once_prints_journals_with_no_file(tmp_path, capsys):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    (source / "os.json").write_text(_search_output_json([]), encoding="utf-8")
    ingest_once(RunOptions(no_email=True), paths, source, TODAY, env={})
    out = capsys.readouterr().out
    assert "no file for:" in out
    assert "smj" in out


def test_ingest_once_dry_run_writes_nothing(tmp_path):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    (source / "os.json").write_text(_search_output_json([_found_call_json()]), encoding="utf-8")
    sent = []
    delta = ingest_once(RunOptions(dry_run=True, force_email=True), paths, source, TODAY,
                        sender=lambda *a, **k: sent.append(a),
                        env={"GMAIL_USER": "u", "GMAIL_APP_PASSWORD": "p"})
    assert len(delta.new) == 1
    assert not paths.store.exists() and not paths.dashboard.exists() and not paths.last_delta.exists()
    assert sent == []


def test_notify_last_sends_from_last_delta(tmp_path):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    (source / "os.json").write_text(_search_output_json([_found_call_json()]), encoding="utf-8")
    ingest_once(RunOptions(no_email=True), paths, source, TODAY, env={})

    sent = []
    ok = notify_last(paths, sender=lambda subject, html, user, pw, to=None: sent.append((subject, user)),
                     env={"GMAIL_USER": "me@gmail.com", "GMAIL_APP_PASSWORD": "pw"})
    assert ok is True
    assert sent and sent[0][0].startswith("cfp-watch: 1 new call") and sent[0][1] == "me@gmail.com"


def test_notify_last_missing_file_returns_false_and_does_not_send(tmp_path, capsys):
    paths = _paths(tmp_path)

    def must_not_send(*a, **k):
        raise AssertionError("should not send")

    ok = notify_last(paths, sender=must_not_send, env={"GMAIL_USER": "u", "GMAIL_APP_PASSWORD": "p"})
    assert ok is False
    assert "no last_delta.json; nothing to send" in capsys.readouterr().out


def test_notify_last_force_sends_even_when_delta_is_empty(tmp_path):
    paths = _paths(tmp_path)
    Store().save(paths.store)
    write_last_delta(paths.last_delta, Delta(new=[], changed=[], expired=[], errors={}), TODAY)

    sent = []
    ok = notify_last(paths, sender=lambda subject, html, user, pw, to=None: sent.append(subject),
                     env={"GMAIL_USER": "me@gmail.com", "GMAIL_APP_PASSWORD": "pw"}, force=True)
    assert ok is True
    assert sent == ["cfp-watch: weekly digest"]


def test_notify_last_raises_when_credentials_missing_and_email_due(tmp_path):
    paths = _paths(tmp_path)
    source = tmp_path / "found"
    source.mkdir()
    (source / "os.json").write_text(_search_output_json([_found_call_json()]), encoding="utf-8")
    ingest_once(RunOptions(no_email=True), paths, source, TODAY, env={})

    def must_not_send(*a, **k):
        raise AssertionError("should not send")

    with pytest.raises(RuntimeError, match="GMAIL_USER"):
        notify_last(paths, sender=must_not_send, env={})


def test_main_ingest_and_notify_last_work_via_cfpwatch_root_env(tmp_path, monkeypatch):
    from cfpwatch import cli
    from cfpwatch.config import load_journals

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "journals.yaml").write_text((ROOT / "config" / "journals.yaml").read_text())
    source = tmp_path / "found"
    source.mkdir()
    # An empty-calls file for every configured journal, so no journal is
    # "uncovered" (which would otherwise be recorded as an error): the
    # resulting delta has nothing to email, so notify-last can run to
    # completion with no GMAIL credentials and no network access.
    for j in load_journals(tmp_path / "config" / "journals.yaml"):
        (source / f"{j.key}.json").write_text(_search_output_json([]), encoding="utf-8")
    monkeypatch.setenv("CFPWATCH_ROOT", str(tmp_path))
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    def boom():
        raise AssertionError("must not construct anthropic.Anthropic()")

    monkeypatch.setattr(cli.anthropic, "Anthropic", boom)

    assert cli.main(["ingest", str(source), "--no-email"]) == 0
    assert (tmp_path / "data" / "last_delta.json").exists()
    # The dashboard footer names the model that produced it; ingest never
    # calls an API model, so it should say "routine", not claude-sonnet-5.
    assert "model routine" in (tmp_path / "docs" / "index.html").read_text()

    assert cli.main(["notify-last"]) == 0
