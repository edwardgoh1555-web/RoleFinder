"""
Multi-board job crawler powered by the OpenAI Responses API with web_search_preview.

Strategy:
  1. Parse the user's prompt and generate targeted search queries.
  2. Run one search per job board (parallelised where possible).
  3. Deduplicate by (title, company) and return the unique list.
"""

import asyncio
import json
import logging
import re
from openai import AsyncOpenAI

from app.database import append_crawl_log, finish_crawl_run, get_config, insert_jobs

logger = logging.getLogger(__name__)

# ── Job boards to search ───────────────────────────────────────────────────────
BOARDS = [
    "LinkedIn Jobs",
    "Indeed",
    "Glassdoor",
    "Wellfound (AngelList)",
    "Remotive",
    "Otta",
    "Hired",
    "Stack Overflow Jobs",
]

# ── Prompts ────────────────────────────────────────────────────────────────────
SYSTEM_INSTRUCTIONS = """\
You are a specialised job-search agent. Your ONLY task is to search the web for \
real, currently-live job listings that match the user's criteria and return them \
as a JSON array.

Rules:
- Use web search to find actual job postings. Do not invent or fabricate listings.
- Return ONLY a valid JSON array — no markdown fences, no explanation, no prose.
- Each element must be a JSON object with EXACTLY these fields (use null for any \
  field that is genuinely unavailable):
  {
    "title":       "Exact job title from the listing",
    "company":     "Company name",
    "location":    "City / Country, or 'Remote', or 'Hybrid – London'",
    "salary":      "e.g. '£80k–£110k' or '$150k–$200k' or null",
    "job_type":    "Full-time | Part-time | Contract | Freelance",
    "date_posted": "ISO date or relative string e.g. '2025-05-28' or '3 days ago'",
    "apply_url":   "Direct URL to the job listing or application page",
    "description": "One or two sentences summarising the role and key requirements",
    "source":      "Name of the job board or site where found"
  }
- Include up to 10 listings per search. Prefer listings posted within the last 14 days.
- Only include roles that closely match the criteria. Do not pad with tangential results.\
"""


def _build_search_prompt(user_prompt: str, board: str) -> str:
    return (
        f"Search {board} for job listings that match all of the following criteria:\n\n"
        f"{user_prompt.strip()}\n\n"
        "Return up to 10 matching, currently-live job listings as a JSON array following "
        "the schema in your instructions. Use web search to verify the listings exist."
    )


def _parse_jobs(text: str, board: str) -> list[dict]:
    """Extract a JSON array from model output, tolerating minor formatting issues."""
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return []
    try:
        items = json.loads(match.group())
    except json.JSONDecodeError:
        return []

    valid = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not item.get("title") or not item.get("company"):
            continue
        # Ensure all expected fields exist
        item.setdefault("location", None)
        item.setdefault("salary", None)
        item.setdefault("job_type", None)
        item.setdefault("date_posted", None)
        item.setdefault("apply_url", None)
        item.setdefault("description", None)
        item.setdefault("source", board)
        valid.append(item)
    return valid


def _deduplicate(jobs: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    unique = []
    for job in jobs:
        key = (
            (job.get("title") or "").lower().strip(),
            (job.get("company") or "").lower().strip(),
        )
        if key not in seen and key != ("", ""):
            seen.add(key)
            unique.append(job)
    return unique


async def _search_one_board(
    client: AsyncOpenAI,
    user_prompt: str,
    board: str,
    run_id: int,
) -> list[dict]:
    try:
        response = await client.responses.create(
            model="gpt-4o",
            instructions=SYSTEM_INSTRUCTIONS,
            input=_build_search_prompt(user_prompt, board),
            tools=[{"type": "web_search_preview", "search_context_size": "high"}],
        )

        text = ""
        for item in response.output:
            if getattr(item, "type", None) == "message":
                for part in getattr(item, "content", []):
                    if getattr(part, "type", None) == "output_text":
                        text += part.text

        jobs = _parse_jobs(text, board)
        append_crawl_log(run_id, f"  {board}: {len(jobs)} listing(s) found")
        return jobs

    except Exception as exc:
        append_crawl_log(run_id, f"  {board}: search failed — {exc}")
        logger.warning("Board search failed for %s: %s", board, exc)
        return []


async def run_crawl(run_id: int) -> list[dict]:
    """Orchestrates the full crawl. Writes progress to the DB log."""
    import os

    user_prompt = get_config("search_prompt") or "Find software engineering jobs"
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    append_crawl_log(run_id, f"Crawl started — searching {len(BOARDS)} job boards")

    # Run boards in pairs to keep throughput reasonable without hammering the API
    all_jobs: list[dict] = []
    batch_size = 2

    for i in range(0, len(BOARDS), batch_size):
        batch = BOARDS[i : i + batch_size]
        append_crawl_log(run_id, f"Searching: {', '.join(batch)}")

        results = await asyncio.gather(
            *[_search_one_board(client, user_prompt, board, run_id) for board in batch],
            return_exceptions=False,
        )
        for job_list in results:
            all_jobs.extend(job_list)

        # Brief pause between batches to respect rate limits
        if i + batch_size < len(BOARDS):
            await asyncio.sleep(3)

    unique_jobs = _deduplicate(all_jobs)
    append_crawl_log(
        run_id,
        f"Deduplication complete: {len(all_jobs)} raw → {len(unique_jobs)} unique listings",
    )

    if unique_jobs:
        insert_jobs(run_id, unique_jobs)

    finish_crawl_run(run_id, "completed", jobs_found=len(unique_jobs))
    append_crawl_log(run_id, f"Done. {len(unique_jobs)} unique job(s) saved.")

    return unique_jobs
