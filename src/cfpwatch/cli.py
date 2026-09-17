from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import anthropic

from .config import load_journals
from .fetch import fetch_text
from .models import Delta
from .notify import send_email
from .render import RunMeta, render_dashboard, render_email
from .search import DEFAULT_MODEL, SearchOutput, find_calls, to_call
from .store import Store, expire_all, merge, read_last_delta, record_error, write_last_delta

DEFAULT_DASHBOARD_URL = "https://saesoda.github.io/cfp-watch/"


@dataclass
class RunOptions:
    journals: list[str] | None = None
    dry_run: bool = False
    no_email: bool = False
    force_email: bool = False
    model: str = DEFAULT_MODEL
    max_searches: int = 5


@dataclass
class Paths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config" / "journals.yaml"

    @property
    def store(self) -> Path:
        return self.root / "data" / "calls.json"

    @property
    def dashboard(self) -> Path:
        return self.root / "docs" / "index.html"

    @property
    def last_delta(self) -> Path:
        return self.root / "data" / "last_delta.json"


def run_once(
    opts: RunOptions,
    paths: Paths,
    client,
    today: dt.date,
    sender=send_email,
    fetcher=fetch_text,
    searcher=find_calls,
    env=os.environ,
) -> Delta:
    journals = load_journals(paths.config)
    if opts.journals:
        wanted = set(opts.journals)
        unknown = wanted - {j.key for j in journals}
        if unknown:
            print(f"warning: unknown journal keys: {sorted(unknown)}")
        journals = [j for j in journals if j.key in wanted]
    store = Store.load(paths.store)
    total = Delta(new=[], changed=[], expired=[], errors={})
    in_total = out_total = 0

    for j in journals:
        evidence = [t for t in (fetcher(u) for u in j.direct_urls) if t]
        print(f"[{j.key}] searching (evidence pages: {len(evidence)})", flush=True)
        res = searcher(client, j, today, evidence, opts.model, opts.max_searches)
        in_total += res.input_tokens
        out_total += res.output_tokens
        if res.error:
            print(f"[{j.key}] ERROR {res.error}", flush=True)
            record_error(store, j.key, res.error, today)
            total.errors[j.key] = res.error
            continue
        d = merge(store, j.key, res.calls, today)
        print(f"[{j.key}] found {len(res.calls)}: new {len(d.new)}, changed {len(d.changed)}, "
              f"expired {len(d.expired)}; tokens {res.input_tokens}/{res.output_tokens}", flush=True)
        if res.notes:
            print(f"[{j.key}] notes: {res.notes}", flush=True)
        total.new += d.new
        total.changed += d.changed
        total.expired += d.expired

    print(f"tokens total: {in_total}/{out_total}")
    return finish_run(store, total, opts, paths, today, sender, env)


def finish_run(
    store: Store,
    total: Delta,
    opts: RunOptions,
    paths: Paths,
    today: dt.date,
    sender,
    env,
) -> Delta:
    total.expired += expire_all(store, today)

    meta = RunMeta(today=today, model=opts.model,
                   dashboard_url=env.get("CFPWATCH_DASHBOARD_URL", DEFAULT_DASHBOARD_URL),
                   journals=load_journals(paths.config))
    subject, html = render_email(store, total, meta)

    if opts.dry_run:
        print(f"[dry-run] would write {paths.store} and {paths.dashboard}; email subject: {subject}")
        return total

    store.save(paths.store)
    write_last_delta(paths.last_delta, total, today)
    paths.dashboard.parent.mkdir(parents=True, exist_ok=True)
    paths.dashboard.write_text(render_dashboard(store, total, meta), encoding="utf-8")

    if opts.no_email or not (total.should_email or opts.force_email):
        print(f"no email ({subject})")
        return total
    user, pw = env.get("GMAIL_USER"), env.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        raise RuntimeError("email due but GMAIL_USER / GMAIL_APP_PASSWORD not set")
    sender(subject, html, user, pw, env.get("DIGEST_TO") or None)
    print(f"email sent: {subject}")
    return total


def ingest_once(
    opts: RunOptions,
    paths: Paths,
    source: Path,
    today: dt.date,
    sender=send_email,
    env=os.environ,
) -> Delta:
    journals = load_journals(paths.config)
    by_key = {j.key: j for j in journals}
    store = Store.load(paths.store)
    total = Delta(new=[], changed=[], expired=[], errors={})

    files = sorted(source.glob("*.json")) if source.is_dir() else [source]
    seen_keys: set[str] = set()

    for f in files:
        key = f.stem
        if key not in by_key:
            print(f"warning: unknown journal file {f.name}")
            continue
        seen_keys.add(key)
        journal = by_key[key]
        try:
            parsed = SearchOutput.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception as e:
            err = f"ingest: {e}"[:500]
            print(f"[{key}] ERROR {err}", flush=True)
            record_error(store, key, err, today)
            total.errors[key] = err
            continue
        calls = [c for c in (to_call(fc, journal, today) for fc in parsed.calls) if c]
        d = merge(store, key, calls, today)
        print(f"[{key}] ingested {len(calls)}: new {len(d.new)}, changed {len(d.changed)}, "
              f"expired {len(d.expired)}", flush=True)
        if parsed.notes:
            print(f"[{key}] notes: {parsed.notes}", flush=True)
        total.new += d.new
        total.changed += d.changed
        total.expired += d.expired

    missing = sorted(j.key for j in journals if j.key not in seen_keys)
    if missing:
        print(f"no file for: {', '.join(missing)}")

    return finish_run(store, total, opts, paths, today, sender, env)


def notify_last(
    paths: Paths,
    sender=send_email,
    env=os.environ,
    force: bool = False,
) -> bool:
    store = Store.load(paths.store)
    result = read_last_delta(paths.last_delta, store)
    if result is None:
        print("no last_delta.json; nothing to send")
        return False
    delta, last_date = result

    meta = RunMeta(today=last_date, model="routine",
                   dashboard_url=env.get("CFPWATCH_DASHBOARD_URL", DEFAULT_DASHBOARD_URL),
                   journals=load_journals(paths.config))
    subject, html = render_email(store, delta, meta)

    if not (delta.should_email or force):
        print(f"no email ({subject})")
        return False
    user, pw = env.get("GMAIL_USER"), env.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        raise RuntimeError("email due but GMAIL_USER / GMAIL_APP_PASSWORD not set")
    sender(subject, html, user, pw, env.get("DIGEST_TO") or None)
    print(f"email sent: {subject}")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cfpwatch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="search all journals, update store, dashboard, and email")
    run.add_argument("--journals", help="comma-separated journal keys, e.g. os,smj")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--no-email", action="store_true")
    run.add_argument("--force-email", action="store_true")
    run.add_argument("--max-searches", type=int, default=5)
    sub.add_parser("render", help="rebuild the dashboard from the store without searching")
    ingest = sub.add_parser("ingest", help="ingest pre-searched SearchOutput JSON files without calling the API")
    ingest.add_argument("path", help="directory of <journal_key>.json files, or a single such file")
    ingest.add_argument("--dry-run", action="store_true")
    ingest.add_argument("--no-email", action="store_true")
    ingest.add_argument("--force-email", action="store_true")
    notify = sub.add_parser("notify-last", help="email the most recently ingested delta from data/last_delta.json")
    notify.add_argument("--force-email", action="store_true")
    args = ap.parse_args(argv)

    paths = Paths(root=Path(os.environ.get("CFPWATCH_ROOT") or Path(__file__).resolve().parents[2]))
    today = dt.date.today()
    model = os.environ.get("CFPWATCH_MODEL") or DEFAULT_MODEL  # empty env var means default

    if args.cmd == "render":
        store = Store.load(paths.store)
        meta = RunMeta(today=today, model=model,
                       dashboard_url=os.environ.get("CFPWATCH_DASHBOARD_URL", DEFAULT_DASHBOARD_URL),
                       journals=load_journals(paths.config))
        paths.dashboard.parent.mkdir(parents=True, exist_ok=True)
        paths.dashboard.write_text(render_dashboard(store, Delta([], [], [], {}), meta), encoding="utf-8")
        print(f"wrote {paths.dashboard}")
        return 0

    if args.cmd == "ingest":
        opts = RunOptions(dry_run=args.dry_run, no_email=args.no_email, force_email=args.force_email, model=model)
        ingest_once(opts, paths, Path(args.path), today)
        return 0

    if args.cmd == "notify-last":
        notify_last(paths, force=args.force_email)
        return 0

    opts = RunOptions(
        journals=args.journals.split(",") if args.journals else None,
        dry_run=args.dry_run, no_email=args.no_email, force_email=args.force_email,
        model=model, max_searches=args.max_searches,
    )
    run_once(opts, paths, client=anthropic.Anthropic(), today=today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
