# cfp-watch: special-issue call-for-papers monitor — design

Date: 2026-09-17
Status: approved in conversation; spec for implementation planning

## 1. Goal

A weekly, unattended tool that finds open special-issue calls for papers (CFPs)
for 12 management journals, keeps a running list, publishes a dashboard, and
emails Cheonmok only when something new appears.

Journals: ASQ, SMJ, SEJ, Organization Science (OS), AMJ, Management Science
(MS), Journal of Management (JOM), Journal of Management Studies (JMS),
Strategy Science, Organization Studies, JIBS, Research Policy.

## 2. Constraints discovered during design

Direct HTTP fetching of the publisher CFP pages does not work from scripts.
SAGE, INFORMS, Wiley, AOM (all Atypon-hosted), SMS, and Springer serve a
bot-challenge page to curl, to a rendering proxy (r.jina.ai), and even to
headless Chrome on a residential IP. Only ScienceDirect (Research Policy),
egos.org (Organization Studies), business.cornell.edu/insights/asq (ASQ), and
southernmanagement.org (JOM) returned real content, and ScienceDirect only from
a residential IP.

Consequence: the tool is search-driven. Search engines index those pages; the
runner cannot load them. Direct fetch is an opportunistic bonus, never a
requirement.

## 3. Decisions (from the brainstorm)

| Decision | Choice |
|---|---|
| Delivery | Email digest + dashboard web page |
| Runtime | GitHub Actions, weekly cron + manual trigger |
| Extraction | Claude API with server-side web search, structured JSON output |
| Email | Gmail SMTP with an app password, sent to self |
| Repo | Public GitHub repo `saesoda/cfp-watch`; dashboard on GitHub Pages |
| Email policy | Send only when there are new calls, changed deadlines, or journal errors |
| Language | Python 3.12, official `anthropic` SDK |

## 4. Architecture

```
GitHub Actions (weekly)
  └─ python -m cfpwatch run
       ├─ config/journals.yaml        (12 journals: names, aliases, optional direct URLs)
       ├─ fetch.py     opportunistic direct fetch, bot-page detection
       ├─ search.py    one Claude call per journal (web_search tool) -> list[Call]
       ├─ store.py     merge into data/calls.json, classify new/changed/expired
       ├─ render.py    docs/index.html (dashboard) + email HTML
       └─ notify.py    Gmail SMTP, only if digest is non-empty
     then: git commit data/ + docs/, push; Pages serves docs/
```

Every unit has one job and a plain-data interface (dataclasses / dicts) so it
can be unit-tested without network access.

## 5. Components

### 5.1 `config/journals.yaml`

One entry per journal:

```yaml
- key: os
  name: Organization Science
  aliases: ["Org Science", "OrgSci"]
  publisher: INFORMS
  direct_urls: []                     # optional; tried but not required
  search_hints: ["site:pubsonline.informs.org orsc"]   # optional extra query terms
```

Initial `direct_urls` (verified reachable at design time): ASQ → Cornell ASQ
insights page; JOM → southernmanagement.org/journal/; Organization Studies →
egos.org. Everything else empty. Research Policy's ScienceDirect page is listed
but expected to fail from GitHub's IPs; failure is not an error.

### 5.2 `fetch.py`

`fetch_text(url, timeout=20) -> str | None`. Browser-like User-Agent. Returns
`None` on non-200, on network error, or when the body matches bot-challenge
markers ("Just a moment", "Performing security verification", "Are you a
robot", "Client Challenge", "Access Denied"). Strips HTML to text, caps at
30k characters. No retries beyond the HTTP client default.

### 5.3 `search.py`

`find_calls(journal, today, evidence: list[str]) -> SearchResult`

One `client.messages.parse()` call per journal:

- Model: `claude-opus-5` by default (`CFPWATCH_MODEL` env var overrides,
  e.g. `claude-sonnet-5` to cut cost). Adaptive thinking on.
- Tools: `web_search_20260209` with `max_uses` = 5 (configurable). Optionally
  `web_fetch_20260209` with `max_uses` = 3, so the model can read an open PDF
  or society page it found. Fetch failures are expected and harmless.
- System prompt: today's date, the journal's full name and aliases, what
  counts as a special-issue CFP (special issue, special research forum,
  special section, thematic issue; not a conference CFP, not a regular
  issue), instruction to prefer publisher and society sources, to report a
  deadline only if seen in a source, and to include the source URL for every
  call.
- User message: "Find all currently open or announced special issue calls for
  papers for {journal}. Include calls whose submission window opens in the
  future. Exclude calls whose deadline has passed." plus any direct-fetch
  evidence text appended under a heading.
- Output: structured output (`output_config.format`) into this schema, so the
  parse is guaranteed:

```json
{
  "calls": [
    {
      "title": "string",
      "guest_editors": ["string"],
      "opens": "YYYY-MM-DD | null",
      "deadline": "YYYY-MM-DD | null",
      "deadline_text": "string | null",   // verbatim when a date is vague ("Spring 2027")
      "url": "string",
      "source_url": "string",
      "summary": "string (<= 40 words)",
      "confidence": "high | medium | low"
    }
  ],
  "notes": "string"     // anything the model wants to flag, e.g. blocked sources
}
```

Returns `SearchResult(calls, notes, usage, error=None)`. Any SDK error is
caught inside `find_calls` and returned as `error=str(e)`; it never propagates.
Refusal stop reason is treated as an error. `fallbacks="default"` with the
server-side fallback beta is enabled so a refusal on Opus 5 reroutes rather
than fails.

### 5.4 `store.py`

`data/calls.json` is the source of truth, committed to the repo.

Record:

```json
{
  "id": "os:2a9f3c1b",                 // journal key + 8-hex sha1 of normalized title
  "journal": "os",
  "title": "...", "guest_editors": [...], "opens": "...", "deadline": "...",
  "deadline_text": "...", "url": "...", "source_url": "...", "summary": "...",
  "confidence": "high",
  "first_seen": "2026-09-22", "last_seen": "2026-09-22",
  "status": "open"                     // open | expired | stale
}
```

Normalization for the id: lowercase, strip punctuation, collapse whitespace,
drop a leading "special issue on / special issue:" prefix. Two titles that
still differ by wording are treated as different calls; a low-cost fuzzy
match (token-set ratio ≥ 0.9 via `rapidfuzz`) merges near-duplicates within
the same journal.

`merge(store, journal, found, today) -> Delta`:

- id not in store → **new**; insert with `first_seen = today`.
- id in store, deadline differs and new value is non-null → **changed**;
  update fields, keep `first_seen`.
- id in store, seen again → update `last_seen`, refresh mutable fields.
- id in store with `deadline < today` → status `expired` (kept for history).
- id in store, not seen for 3 consecutive successful runs of that journal and
  no deadline known → status `stale` (hidden from the dashboard's open list).
- Journals whose search errored do not touch their existing records.

`Delta` = new, changed, expired lists plus per-journal `errors`.

### 5.5 `render.py`

- `render_dashboard(store, run_meta) -> str` writes `docs/index.html`: a
  single self-contained page. Sections: "New this week", then all open calls
  in one table sorted by deadline (nulls last) with journal, title, deadline,
  editors, link; then per-journal status (last successful check, last error).
  Expired calls in a collapsed section. No external assets; works on a phone.
- `render_email(delta, store, run_meta) -> (subject, html)`: new calls,
  changed deadlines, deadlines within 30 days, journal errors, link to
  dashboard. Subject like `cfp-watch: 2 new calls (SMJ, OS)`.

### 5.6 `notify.py`

`send(subject, html)` via `smtplib` SSL to smtp.gmail.com:465 using
`GMAIL_USER` / `GMAIL_APP_PASSWORD`, to `DIGEST_TO` (defaults to
`GMAIL_USER`). Called only when `delta` has new, changed, or errors. A
`--force-email` flag sends regardless (for testing).

### 5.7 CLI (`python -m cfpwatch`)

- `run [--journals os,smj] [--dry-run] [--force-email] [--no-email]`
  - `--dry-run`: search but do not write the store, dashboard, or email;
    print the delta.
- `render`: rebuild the dashboard from the store without searching.

### 5.8 GitHub Actions `.github/workflows/weekly.yml`

- `schedule: cron "0 0 * * 1"` (Monday 00:00 UTC = 08:00 HKT) and
  `workflow_dispatch`.
- Steps: checkout, Python 3.12, `pip install -e .`, `python -m cfpwatch run`,
  commit `data/calls.json` + `docs/index.html` with message
  `cfp-watch: weekly run YYYY-MM-DD`, push.
- Secrets: `ANTHROPIC_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`. Optional
  variable: `CFPWATCH_MODEL`.
- Pages: source = `main` branch, `/docs` folder.
- A crashed run (uncaught exception, e.g. missing secret) fails the workflow;
  GitHub emails the failure. This covers the "silence means broken" risk of
  the send-only-when-new policy.

## 6. Error handling summary

| Failure | Behaviour |
|---|---|
| Direct URL blocked or down | Silently skipped; search still runs |
| Claude API error for one journal | Journal marked errored in delta, records untouched, others continue, email sent listing the error |
| Refusal | Server-side fallback; if still refused, treated as error |
| Structured output parse failure | Cannot happen with `parse()`; SDK error → journal error |
| Gmail send failure | Exception propagates; workflow fails; GitHub notifies |
| All journals error | Email with errors only; workflow still succeeds |

## 7. Testing

Unit tests (`pytest`, no network):

- `test_fetch.py`: bot-page detection on saved fixture HTML from the probes
  (Cloudflare "Just a moment", Elsevier captcha, Springer client challenge);
  HTML-to-text stripping.
- `test_store.py`: id normalization; new / changed / expired / stale
  transitions; errored journal leaves records untouched; fuzzy merge.
- `test_render.py`: dashboard contains each open call once, sorted by
  deadline; email subject counts; empty delta produces no email.
- `test_search.py`: prompt builder includes date, aliases, evidence; response
  mapping from the parsed schema to `Call`; SDK exception → `error` set
  (mock the client).

Live checks (manual, need the API key): `run --journals os,smj --dry-run`,
then a full `run --force-email` to confirm SMTP and Pages.

## 8. Cost

Per run: 12 Claude calls. With `claude-opus-5`, roughly 20–40k input tokens
(search results) and ~2k output per journal → about $2–3 in tokens, plus up to
60 web searches at $10 per 1,000 → $0.60. Estimate **$3–4 per weekly run,
$12–16 per month**. Setting `CFPWATCH_MODEL=claude-sonnet-5` cuts tokens to
about $1 per run (**$5–7 per month**). The model is a config knob, not a code
change.

## 9. Out of scope (deferred)

- A local "deep check" mode using real Chrome for the bot-gated publisher
  pages. Add only if search-driven results prove to miss calls.
- Tracking calls for journals beyond the 12.
- A read-it-later or calendar integration.

## 10. Repo layout

```
cfp_watch/
  pyproject.toml
  README.md
  config/journals.yaml
  src/cfpwatch/{__init__,__main__,cli,fetch,search,store,render,notify,models}.py
  tests/{fixtures/,test_fetch,test_store,test_render,test_search}.py
  data/calls.json
  docs/index.html
  docs/superpowers/specs/2026-09-17-cfp-watch-design.md
  .github/workflows/weekly.yml
```
