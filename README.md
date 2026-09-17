# cfp-watch

Weekly monitor for special-issue calls for papers in 12 management journals
(ASQ, SMJ, SEJ, Organization Science, AMJ, Management Science, JOM, JMS,
Strategy Science, Organization Studies, JIBS, Research Policy).

Dashboard: https://saesoda.github.io/cfp-watch/

## How it works

This runs in zero-API-cost mode: no scheduled job calls the Anthropic API.
Instead, a [Claude Code cloud routine](https://claude.ai/code/routines)
runs weekly on your own Claude subscription, following the checklist in
[`scripts/routine.md`](scripts/routine.md): it searches the web for each
journal's open special-issue calls, writes one `data/found/<key>.json` file
per journal, then runs `cfpwatch ingest data/found --no-email` (no LLM
call — this just parses, merges into `data/calls.json`, and rebuilds
`docs/index.html`) and pushes the result to `main`.

That push — specifically, a change to `data/last_delta.json` — triggers a
GitHub Actions job (`.github/workflows/notify.yml`) that only sends the
email digest, via `python -m cfpwatch notify-last`. It never touches the
Anthropic API. Note that `notify-last` doesn't mark the digest as sent,
so manually re-running or re-dispatching that workflow re-sends the same
last digest rather than being a no-op — the job's `concurrency` group
only prevents two *overlapping* runs from both emailing at once, it
doesn't make a later, separate re-run idempotent.

The old API-based `cfpwatch run` command still exists for optional local
use (see below), but nothing scheduled calls it anymore.

## Setup (once)

1. Repo secrets (Settings → Secrets and variables → Actions → New repository secret):
   - `GMAIL_USER` — the Gmail address that sends and receives the digest
   - `GMAIL_APP_PASSWORD` — https://myaccount.google.com/apppasswords (needs 2-step verification on)
   - `DIGEST_TO` — optional; another recipient address
   - `ANTHROPIC_API_KEY` is **not** needed for the scheduled workflow — it's
     only used if you run the optional local `run` command yourself.
2. GitHub Pages: Settings → Pages → Source "Deploy from a branch", branch `main`, folder `/docs`.
3. Set up the cloud routine at https://claude.ai/code/routines to run
   `scripts/routine.md` on a schedule (e.g. weekly). It needs push access
   to this repo (and `gh` access, for the fallback PR path in the script).
4. Test the email side by hand once: Actions → "cfp-watch notify" → Run
   workflow (tick "force_email" to send even with an empty delta).

## Local use

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q

# Optional, API-based search (costs money; not used by the scheduled routine):
export ANTHROPIC_API_KEY=...            # or `ant auth login`
.venv/bin/python -m cfpwatch run --journals os,smj --dry-run
.venv/bin/python -m cfpwatch run --no-email

# Zero-API-cost path — ingest hand- or routine-written search results:
.venv/bin/python -m cfpwatch ingest data/found --no-email
.venv/bin/python -m cfpwatch notify-last --force-email   # test the email side

.venv/bin/python -m cfpwatch render      # rebuild dashboard only
```

## Cost

$0 API — search runs on your Claude subscription (via the cloud routine),
not the Anthropic API. The scheduled GitHub Actions job only sends an
email over SMTP. The optional local `run` command still costs about
$1–2 per invocation with Sonnet 5 if you choose to use it.

## Adding a journal

Add an entry to `config/journals.yaml` (key, name, aliases, publisher,
optional `direct_urls` that are known to be fetchable by a script, and
`search_hints`). Nothing else changes.
