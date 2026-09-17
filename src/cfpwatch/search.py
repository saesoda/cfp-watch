from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

import anthropic
from pydantic import BaseModel

from .models import Call, Journal, parse_date

DEFAULT_MODEL = "claude-sonnet-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PAUSE_RESTARTS = 1

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
WEB_FETCH_TOOL = {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3}


class FoundCall(BaseModel):
    title: str
    guest_editors: list[str]
    opens: str | None
    deadline: str | None
    deadline_text: str | None
    url: str
    source_url: str
    summary: str
    confidence: Literal["high", "medium", "low"]


class SearchOutput(BaseModel):
    calls: list[FoundCall]
    notes: str


@dataclass
class SearchResult:
    calls: list[Call] = field(default_factory=list)
    notes: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


def build_prompt(journal: Journal, today: dt.date, evidence: list[str]) -> tuple[str, str]:
    aliases = ", ".join(journal.aliases) or "none"
    hints = "\n".join(f"- {h}" for h in journal.search_hints) or "- (none)"
    system = f"""You are a research assistant who tracks special-issue calls for papers (CFPs) in management journals.
Today's date is {today.isoformat()}.

Target journal: {journal.name} (publisher: {journal.publisher}). Aliases: {aliases}.

What counts as a call:
- A special issue, special research forum, special section, thematic issue, or curated collection of THIS journal that is currently accepting submissions or has announced a future submission window.
- Exclude: conference calls, calls for other journals with similar names, regular-issue submission guidance, workshops, and calls whose final submission deadline is before today.

How to work:
- Use web_search. Prefer the publisher's or sponsoring society's pages. Useful query terms:
{hints}
- Try at least two different queries (for example: "{journal.name}" "special issue" call for papers; "{journal.name}" "call for papers" 2026 2027).
- Use web_fetch only on a promising result that is a publisher, society, or PDF page; if a fetch fails or returns a bot-check page, ignore it and rely on search snippets.
- Report a date only when a source states it. Use ISO YYYY-MM-DD. If the source gives only a vague date ("Spring 2027"), leave `deadline` null and put the phrase in `deadline_text`.
- `url` is the best page for the call itself; `source_url` is where you saw the information (may be the same).
- `confidence` is high when a publisher/society page confirms the call, medium for a credible secondary source, low for a single indirect mention.
- Put anything you could not verify, and any blocked sources, in `notes`. Return an empty `calls` list if there are none."""

    user = (
        f"Find all currently open or announced special issue calls for papers for {journal.name}. "
        "Include calls whose submission window opens in the future. Exclude calls whose deadline has passed."
    )
    if evidence:
        joined = "\n\n---\n\n".join(evidence)
        user += f"\n\nEvidence fetched directly from journal-related pages (may be partial):\n\n{joined}"
    return system, user


def to_call(fc: FoundCall, journal: Journal, today: dt.date) -> Call | None:
    title = fc.title.strip()
    if not title:
        return None
    d = parse_date(fc.deadline)
    if d is not None and d < today:
        return None
    ts = today.isoformat()
    return Call(
        journal=journal.key,
        title=title,
        guest_editors=[g.strip() for g in fc.guest_editors if g.strip()],
        opens=(o.isoformat() if (o := parse_date(fc.opens)) else None),
        deadline=d.isoformat() if d else None,
        deadline_text=fc.deadline_text or fc.deadline,
        url=fc.url,
        source_url=fc.source_url,
        summary=fc.summary.strip(),
        confidence=fc.confidence,
        first_seen=ts,
        last_seen=ts,
    )


_to_call = to_call  # backward-compatible alias for the former module-private name


def find_calls(
    client,
    journal: Journal,
    today: dt.date,
    evidence: list[str],
    model: str = DEFAULT_MODEL,
    max_searches: int = 5,
) -> SearchResult:
    system, user = build_prompt(journal, today, evidence)
    messages: list[dict] = [{"role": "user", "content": user}]
    tools = [dict(WEB_SEARCH_TOOL, max_uses=max_searches), dict(WEB_FETCH_TOOL)]
    result = SearchResult()

    try:
        for _ in range(MAX_PAUSE_RESTARTS + 1):
            resp = client.beta.messages.parse(
                model=model,
                max_tokens=16000,
                system=system,
                messages=messages,
                tools=tools,
                thinking={"type": "adaptive"},
                output_format=SearchOutput,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            result.input_tokens += getattr(resp.usage, "input_tokens", 0) or 0
            result.output_tokens += getattr(resp.usage, "output_tokens", 0) or 0
            if resp.stop_reason == "pause_turn":
                messages = messages + [{"role": "assistant", "content": resp.content}]
                continue
            if resp.stop_reason == "refusal":
                result.error = "refusal: model declined the request"
                return result
            parsed: SearchOutput | None = resp.parsed_output
            if parsed is None:
                result.error = f"no structured output (stop_reason={resp.stop_reason})"
                return result
            result.notes = parsed.notes
            result.calls = [c for c in (to_call(fc, journal, today) for fc in parsed.calls) if c]
            return result
        result.error = "pause_turn: exceeded restart limit"
        return result
    except anthropic.RateLimitError as e:
        result.error = f"rate limited: {e}"
    except anthropic.APIStatusError as e:
        result.error = f"api error {e.status_code}: {getattr(e, 'message', e)}"
    except anthropic.APIConnectionError as e:
        result.error = f"connection error: {e}"
    except anthropic.APIError as e:
        result.error = f"api error: {e}"
    except Exception as e:
        result.error = f"unexpected {type(e).__name__}: {e}"
    return result
