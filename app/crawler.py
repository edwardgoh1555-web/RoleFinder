"""
Two-stage job crawler.

Stage 1 — Candidate Discovery
  Run each query family against the open web via OpenAI gpt-4o + web_search_preview.
  Extract structured job listings from the results.

Stage 2 — Evaluator
  Pass every deduplicated candidate through gpt-4o-mini (json_object mode) using
  the configurable evaluator prompt. Accept only jobs scoring >= min_accept_score.

Post-processing
  Apply source-domain quota. Mark cross-day duplicates.
"""

import asyncio
import json
import logging
import re
from collections import Counter
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from openai import AsyncOpenAI

from app.database import (
    append_crawl_log,
    finish_crawl_run,
    get_all_config,
    get_previously_seen_url_hashes,
    insert_jobs,
    _make_url_hash,
    _make_job_key,
)

logger = logging.getLogger(__name__)

# ── Prompts ────────────────────────────────────────────────────────────────────

_DISCOVERY_INSTRUCTIONS = """\
You are a job listing extractor. Search the web for the given query and extract ALL real job \
listings you find in the results.

Return ONLY a valid JSON array — no markdown fences, no explanation.
Each element must be a JSON object with exactly these fields (use null if unavailable):
{
  "title":               "Exact job title",
  "company":             "Company name",
  "location":            "City / Country or Remote or null",
  "salary":              "e.g. £60k-£80k or null",
  "job_type":            "Full-time / Contract / Part-time or null",
  "date_posted":         "ISO date or relative string or null",
  "apply_url":           "Direct URL to the listing — required, no aggregator redirect if avoidable",
  "description":         "1-3 sentence factual summary of the role",
  "source":              "Domain or job board name",
  "raw_relevance_reason":"Why this listing was surfaced by this query"
}

Exclude listings that:
- Have no real job title or no company name
- Have no valid apply URL
- Are clearly expired, filled or unavailable
- Are obviously unrelated to the query topic\
"""

_JOB_EVAL_HEADER = "Evaluate this job listing:\n\n"


# ── URL utilities ──────────────────────────────────────────────────────────────

_TRACKING = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "ref", "source", "via", "from", "trk", "mc_eid",
})

_ATS_DOMAINS = {
    "greenhouse.io":  "Greenhouse",
    "lever.co":       "Lever",
    "ashbyhq.com":    "Ashby",
    "workday.com":    "Workday",
    "taleo.net":      "Taleo",
    "smartrecruiters": "SmartRecruiters",
    "linkedin.com":   "LinkedIn",
    "indeed.com":     "Indeed",
    "glassdoor.com":  "Glassdoor",
    "wellfound.com":  "Wellfound",
    "remotive.com":   "Remotive",
    "otta.com":       "Otta",
    "hired.com":      "Hired",
    "stackoverflow":  "Stack Overflow",
}


def _normalize_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
        clean_q = {k: v for k, v in parse_qs(p.query).items() if k.lower() not in _TRACKING}
        return urlunparse((
            p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"),
            "", urlencode(clean_q, doseq=True), ""
        ))
    except Exception:
        return url.strip().lower()


def _infer_source(job: dict) -> str:
    url = job.get("apply_url") or ""
    try:
        netloc = urlparse(url).netloc.lower().removeprefix("www.")
        for domain, name in _ATS_DOMAINS.items():
            if domain in netloc:
                return name
        if netloc:
            return netloc.split(".")[0].capitalize()
    except Exception:
        pass
    return job.get("source") or "Unknown"


# ── Retry wrapper ──────────────────────────────────────────────────────────────

async def _with_retry(coro_fn, max_attempts: int = 3, base_delay: float = 2.0):
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                await asyncio.sleep(base_delay * (2 ** attempt))
    raise last_exc  # type: ignore[misc]


# ── JSON extraction ────────────────────────────────────────────────────────────

def _extract_json_array(text: str) -> list:
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return []
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return []


def _validate_candidates(raw: list, query: str) -> list[dict]:
    valid = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not item.get("title") or not item.get("company"):
            continue
        url = item.get("apply_url") or ""
        if not url.startswith("http"):
            continue
        # Normalise URL and infer source
        item["apply_url"] = _normalize_url(url)
        item["source"] = _infer_source(item)
        item.setdefault("raw_relevance_reason", f"Found via query: {query[:80]}")
        for field in ("location", "salary", "job_type", "date_posted", "description"):
            item.setdefault(field, None)
        valid.append(item)
    return valid


# ── Stage 1: Candidate discovery ───────────────────────────────────────────────

async def _search_one_query(
    client: AsyncOpenAI, query: str, run_id: int
) -> list[dict]:
    async def _call():
        response = await client.responses.create(
            model="gpt-4o",
            instructions=_DISCOVERY_INSTRUCTIONS,
            input=f"Search the open web for: {query}",
            tools=[{"type": "web_search_preview", "search_context_size": "high"}],
        )
        text = ""
        for item in response.output:
            if getattr(item, "type", None) == "message":
                for part in getattr(item, "content", []):
                    if getattr(part, "type", None) == "output_text":
                        text += part.text
        return _validate_candidates(_extract_json_array(text), query)

    try:
        jobs = await _with_retry(_call)
        append_crawl_log(run_id, f"  ✓ {len(jobs):2d} candidates — {query[:70]}")
        return jobs
    except Exception as exc:
        append_crawl_log(run_id, f"  ✗ query failed ({str(exc)[:60]}): {query[:60]}")
        return []


# ── Deduplication ─────────────────────────────────────────────────────────────

def _deduplicate(candidates: list[dict]) -> list[dict]:
    seen_hashes: set[str] = set()
    seen_keys: set[str] = set()
    unique = []
    for job in candidates:
        h = _make_url_hash(job.get("apply_url"))
        k = _make_job_key(job.get("title"), job.get("company"))
        if h and h in seen_hashes:
            continue
        if k != "|" and k in seen_keys:
            continue
        if h:
            seen_hashes.add(h)
        seen_keys.add(k)
        unique.append(job)
    return unique


# ── Stage 2: Evaluator ────────────────────────────────────────────────────────

def _format_for_eval(job: dict) -> str:
    return (
        f"Title:    {job.get('title')}\n"
        f"Company:  {job.get('company')}\n"
        f"Location: {job.get('location')}\n"
        f"Salary:   {job.get('salary')}\n"
        f"Type:     {job.get('job_type')}\n"
        f"Source:   {job.get('source')}\n"
        f"URL:      {job.get('apply_url')}\n"
        f"Description: {job.get('description')}\n"
        f"Found via:   {job.get('raw_relevance_reason')}"
    )


async def _evaluate_one(
    client: AsyncOpenAI, job: dict, evaluator_prompt: str
) -> dict:
    async def _call():
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": evaluator_prompt},
                {"role": "user",   "content": _JOB_EVAL_HEADER + _format_for_eval(job)},
            ],
            temperature=0,
            max_tokens=400,
        )
        return json.loads(resp.choices[0].message.content)

    try:
        r = await _with_retry(_call)
        decision = r.get("decision", "reject").lower()
        # Normalise: "accept" → "accepted", "reject" → "rejected"
        if "accept" in decision:
            decision = "accepted"
        else:
            decision = "rejected"
        return {
            "evaluator_decision":  decision,
            "fit_score":           int(r.get("score", 0)),
            "fit_category":        r.get("category", "Reject"),
            "evaluator_reason":    r.get("reason"),
            "main_risk":           r.get("main_risk"),
            "application_angle":   r.get("application_angle"),
            "rejection_type":      r.get("rejection_type"),
            "raw_evaluator_json":  json.dumps(r),
        }
    except Exception as exc:
        logger.warning("Evaluation failed for '%s': %s", job.get("title"), exc)
        return {
            "evaluator_decision":  "rejected",
            "fit_score":           0,
            "fit_category":        "Reject",
            "evaluator_reason":    f"Evaluation error: {exc}",
            "main_risk":           None,
            "application_angle":   None,
            "rejection_type":      "evaluation_error",
            "raw_evaluator_json":  None,
        }


# ── Source quota ───────────────────────────────────────────────────────────────

def _apply_source_quota(jobs: list[dict], max_pct: float) -> list[dict]:
    """Demote excess jobs from over-represented sources (sorted by score)."""
    if len(jobs) < 5:
        return jobs

    max_per_source = max(1, int(len(jobs) * max_pct / 100))
    source_counts: Counter = Counter()
    kept, demoted = [], []

    for job in sorted(jobs, key=lambda j: -(j.get("fit_score") or 0)):
        src = job.get("source") or "Unknown"
        if source_counts[src] < max_per_source:
            source_counts[src] += 1
            kept.append(job)
        else:
            demoted.append(job)

    for job in demoted:
        job["evaluator_decision"] = "rejected"
        job["rejection_type"] = "source_quota"

    return kept


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def run_crawl(run_id: int) -> list[dict]:
    import os

    cfg = get_all_config()
    query_families: list[str] = json.loads(cfg.get("query_families") or "[]")
    evaluator_prompt: str = cfg.get("evaluator_prompt") or ""
    min_score: int = int(cfg.get("min_accept_score") or "70")
    max_source_pct: float = float(cfg.get("max_source_share_pct") or "30")

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # ── Stage 1: Discovery ──
    append_crawl_log(run_id, f"Stage 1: Discovery — {len(query_families)} query families")
    all_raw: list[dict] = []
    batch_size = 3

    for i in range(0, len(query_families), batch_size):
        batch = query_families[i : i + batch_size]
        results = await asyncio.gather(
            *[_search_one_query(client, q, run_id) for q in batch],
            return_exceptions=False,
        )
        for result in results:
            all_raw.extend(result)
        if i + batch_size < len(query_families):
            await asyncio.sleep(2)

    append_crawl_log(run_id, f"Stage 1 done: {len(all_raw)} raw candidates")

    # ── Deduplicate ──
    unique = _deduplicate(all_raw)
    append_crawl_log(run_id, f"Dedup: {len(all_raw)} → {len(unique)} unique candidates")

    # ── Stage 2: Evaluation ──
    append_crawl_log(run_id, f"Stage 2: Evaluating {len(unique)} candidates...")
    eval_batch = 5
    evaluated: list[dict] = []

    for i in range(0, len(unique), eval_batch):
        batch = unique[i : i + eval_batch]
        eval_results = await asyncio.gather(
            *[_evaluate_one(client, job, evaluator_prompt) for job in batch]
        )
        for job, result in zip(batch, eval_results):
            evaluated.append({**job, **result})

    # Enforce min_score threshold
    for job in evaluated:
        if job.get("evaluator_decision") == "accepted":
            if (job.get("fit_score") or 0) < min_score:
                job["evaluator_decision"] = "rejected"
                job["rejection_type"] = job.get("rejection_type") or "below_threshold"

    # ── Source quota ──
    pre_quota = [j for j in evaluated if j.get("evaluator_decision") == "accepted"]
    kept = _apply_source_quota(pre_quota, max_source_pct)
    if len(kept) < len(pre_quota):
        append_crawl_log(run_id, f"Source quota: {len(pre_quota)} → {len(kept)} accepted")

    # ── Cross-day dedup ──
    prev_hashes = get_previously_seen_url_hashes(exclude_run_id=run_id)
    dupes = 0
    for job in evaluated:
        h = _make_url_hash(job.get("apply_url"))
        if h and h in prev_hashes:
            job["duplicate_seen_before"] = 1
            dupes += 1
    if dupes:
        append_crawl_log(run_id, f"Cross-day dedup: {dupes} job(s) seen before (marked, not emailed)")

    # ── Persist ──
    insert_jobs(run_id, evaluated)

    accepted_new = [
        j for j in evaluated
        if j.get("evaluator_decision") == "accepted"
        and not j.get("duplicate_seen_before")
    ]
    rejected = [j for j in evaluated if j.get("evaluator_decision") != "accepted"]

    # Rejection summary
    rejection_types = Counter(
        j.get("rejection_type") or "unknown" for j in rejected
    )
    sources = Counter(j.get("source") for j in accepted_new)

    append_crawl_log(run_id, f"Results: {len(accepted_new)} accepted (new), {len(rejected)} rejected")
    if rejection_types:
        append_crawl_log(run_id, f"Rejection reasons: {dict(rejection_types.most_common(5))}")
    if sources:
        append_crawl_log(run_id, f"Accepted sources: {dict(sources.most_common())}")

    finish_crawl_run(run_id, "completed", jobs_found=len(accepted_new))
    append_crawl_log(run_id, "Crawl complete.")

    return accepted_new
