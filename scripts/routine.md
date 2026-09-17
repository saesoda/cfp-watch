# cfp-watch routine: search, write, ingest, push

This is the checklist a Claude Code cloud routine follows to do the weekly
search for special-issue calls for papers. It replaces the old
API-based `cfpwatch run` step: **this routine does the web searching
itself, using your own subscription. Do not call the Anthropic API**
(no `ANTHROPIC_API_KEY`, no `anthropic.Anthropic()`, no `messages.parse`).
The only thing this routine writes to the repo is plain JSON files; the
`cfpwatch` package does the parsing, merging, rendering, and (via a
separate GitHub Actions job) the emailing.

## 0. Setup

```bash
pip install -e .
rm -f data/found/*.json
```

The `rm -f` is not optional: `data/found/` must contain only files this
run writes. A leftover `<key>.json` from a previous run (e.g. a journal
you skipped last time) will otherwise get committed and re-ingested as if
it were fresh coverage for a journal you never actually searched this
run — silently hiding a skipped journal instead of surfacing it as "no
file for: `<key>`".

Read `config/journals.yaml` to get the list of journals. Each entry has a
`key` (e.g. `os`, `smj`), `name`, `aliases`, `publisher`, `direct_urls`, and
`search_hints`.

## 1. For each journal, search the web

For **every** journal in `config/journals.yaml`, run at least two different
web searches, preferring publisher- or society-run pages over aggregators
or blogs:

- One query using the journal's hints from `search_hints` (these are
  typically `site:...` queries scoped to the publisher's own domain).
- One broader query, e.g. `"<journal name>" "special issue" call for
  papers` and `"<journal name>" "call for papers" 2026 2027`.
- If the journal has `direct_urls`, fetch those pages directly and read
  them as well.

Use today's date to judge "currently open or announced" vs. "already
closed."

### What counts as a call

Copied verbatim from the criteria the package used to give the model:

> What counts as a call:
> - A special issue, special research forum, special section, thematic
>   issue, or curated collection of THIS journal that is currently
>   accepting submissions or has announced a future submission window.
> - Exclude: conference calls, calls for other journals with similar
>   names, regular-issue submission guidance, workshops, and calls whose
>   final submission deadline is before today.

### Date rules

Also copied verbatim:

> Report a date only when a source states it. Use ISO YYYY-MM-DD. If the
> source gives only a vague date ("Spring 2027"), leave `deadline` null
> and put the phrase in `deadline_text`.

Other field guidance (same as the package used to give the model):

- `url` is the best page for the call itself; `source_url` is where you
  saw the information (may be the same).
- `confidence` is `"high"` when a publisher/society page confirms the
  call, `"medium"` for a credible secondary source, `"low"` for a single
  indirect mention.
- Put anything you could not verify, and any blocked sources, in `notes`.
  Return an empty `calls` list if there are none — still write the file.

## 2. Write `data/found/<journal_key>.json`

For each journal, write one file named after its `key`
(e.g. `data/found/os.json`, `data/found/smj.json`) matching this exact
schema (the package's `SearchOutput` / `FoundCall` models):

```json
{
  "calls": [
    {
      "title": "string, required",
      "guest_editors": ["list of strings, [] if none"],
      "opens": "YYYY-MM-DD or null",
      "deadline": "YYYY-MM-DD or null",
      "deadline_text": "free text like 'Spring 2027', or null",
      "url": "https://... (best page for the call)",
      "source_url": "https://... (where you saw it)",
      "summary": "one or two sentences",
      "confidence": "high | medium | low"
    }
  ],
  "notes": "anything you could not verify, or blocked sources; empty string if none"
}
```

Write a file for every journal you searched, even if `calls` is empty —
an empty `calls` list is how the ingest step learns "checked, found
nothing." Skip a journal only if you genuinely could not run its
searches; the ingest step will report it as "no file for: <key>."

## 3. Ingest the files (no API call, no email yet)

```bash
python -m cfpwatch ingest data/found --no-email
```

This parses each `data/found/<key>.json`, merges it into
`data/calls.json`, rebuilds `docs/index.html`, and writes
`data/last_delta.json` — all without calling any LLM API. `--no-email` is
used here because the email is sent later by GitHub Actions, triggered by
the push in the next step, not by this routine.

## 4. Commit and push

```bash
git add data docs
git commit -m "cfp-watch: routine run $(date -u +%F)"
git push origin main
```

Pushing `data/last_delta.json` on `main` is what triggers the
`.github/workflows/notify.yml` job, which emails the digest using
`GMAIL_USER` / `GMAIL_APP_PASSWORD` — no Anthropic API key involved.

### If the push is rejected

If `git push origin main` is rejected (e.g. someone else pushed in the
meantime), do not force-push. Instead:

```bash
git checkout -b routine/$(date -u +%F)
git push origin routine/$(date -u +%F)
gh pr create --title "cfp-watch: routine run $(date -u +%F)" \
  --body "Automated search results from the cfp-watch cloud routine."
```

and open a PR from that branch instead of overwriting `main`.
