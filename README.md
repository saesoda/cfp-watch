# cfp-watch

Weekly monitor for special-issue calls for papers in 12 management journals
(ASQ, SMJ, SEJ, Organization Science, AMJ, Management Science, JOM, JMS,
Strategy Science, Organization Studies, JIBS, Research Policy).

Dashboard: https://saesoda.github.io/cfp-watch/

## How it works

Every Monday a GitHub Actions job asks Claude (with web search) for each
journal's open special-issue calls, merges them into `data/calls.json`,
rebuilds `docs/index.html`, and emails a digest when there are new calls,
changed deadlines, or a journal that could not be checked.

## Setup (once)

1. Repo secrets (Settings → Secrets and variables → Actions → New repository secret):
   - `ANTHROPIC_API_KEY` — from https://console.anthropic.com/settings/keys
   - `GMAIL_USER` — the Gmail address that sends and receives the digest
   - `GMAIL_APP_PASSWORD` — https://myaccount.google.com/apppasswords (needs 2-step verification on)
   - `DIGEST_TO` — optional; another recipient address
2. Optional repo variable `CFPWATCH_MODEL` (default `claude-sonnet-5`; e.g. `claude-opus-5`).
3. GitHub Pages: Settings → Pages → Source "Deploy from a branch", branch `main`, folder `/docs`.
4. Run it once by hand: Actions → "weekly cfp watch" → Run workflow (tick "force_email" to test the mail).

## Local use

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
export ANTHROPIC_API_KEY=...            # or `ant auth login`
.venv/bin/python -m cfpwatch run --journals os,smj --dry-run
.venv/bin/python -m cfpwatch run --no-email
.venv/bin/python -m cfpwatch render      # rebuild dashboard only
```

## Cost

About $1–2 per weekly run with Sonnet 5 (12 journals × up to 5 web searches).

## Adding a journal

Add an entry to `config/journals.yaml` (key, name, aliases, publisher,
optional `direct_urls` that are known to be fetchable by a script, and
`search_hints`). Nothing else changes.
