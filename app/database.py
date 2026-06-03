import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "rolefinder.db")))

# ── Default prompts & config ───────────────────────────────────────────────────

_DEFAULT_ROLE_SEARCH_PROMPT = """\
You are RoleFinder, a selective job discovery agent.

Find roles matching this archetype:

AI Workflow Transformation / AI Adoption role:
A business-facing role where the person helps organisations redesign real work around AI. \
The role should involve process mapping, AI use-case discovery, stakeholder workshops, pilots, \
lightweight AI prototyping or configuration, user training, adoption, change management and \
measurable business impact.

The candidate is a UK-based innovation consultant at Accenture with around two years at level. \
They are strong in consulting, workshops, stakeholder engagement, GenAI adoption, workflow thinking, \
research, writing and early-stage AI product building. They are technically curious and can use AI \
tools, automation tools, APIs and agent builders, but they are not a production software engineer, \
ML engineer, data scientist, BI engineer or enterprise architect.

Prioritise UK, London, UK remote, UK hybrid and South East England roles. Include overseas roles \
only if they explicitly support UK remote work, visa sponsorship or relocation for UK candidates.

Bullseye titles include: AI Advisor, AI Adoption Specialist, AI Enablement Specialist, \
GenAI Adoption Consultant, AI Workflow Transformation Consultant, AI Transformation Consultant, \
AI Change & Adoption Manager, AI Training and Adoption Specialist, AI Capability Lead, \
AI Implementation Consultant, AI Delivery Consultant, Applied AI Consultant, AI Product Specialist, \
Enterprise AI Product Specialist, AI Automation Consultant, AI Business Transformation Consultant, \
AI Business Analyst, AI Programme Associate, Digital Transformation Consultant (only where AI is \
central), Innovation Consultant (only where AI deployment/adoption is central), \
Copilot Adoption Consultant, Microsoft Copilot Enablement Specialist, \
AI Literacy / AI Enablement Consultant, AI Experience Designer (if focused on workflows and \
adoption rather than pure UX).

The role must primarily involve helping people, teams or organisations use AI better. It should \
not primarily involve building production software, training models, managing engineers or owning \
infrastructure.

Reject roles that are primarily: software engineering, AI engineering, AI agent engineering, \
ML engineering, data science, data analysis, BI engineering, engineering management, platform \
engineering, cloud architecture, enterprise architecture, quota-carrying sales, account executive / \
business development, or generic SaaS implementation with no AI transformation element.

Return only real job postings with direct URLs.\
"""

_DEFAULT_EVALUATOR_PROMPT = """\
You are the RoleFinder evaluator.

Your job is to decide whether a discovered job should be shown to the candidate.

The candidate wants roles matching this archetype:

AI Workflow Transformation / AI Adoption role: A business-facing role where the person helps \
organisations redesign real work around AI. The role involves process mapping, AI use-case \
discovery, stakeholder workshops, pilots, lightweight AI prototyping or configuration, user \
training, adoption, change management and measurable business impact.

The candidate is suitable for: AI adoption, AI workflow transformation, GenAI enablement, \
business process redesign, stakeholder workshops, AI use-case discovery, lightweight AI \
prototyping/configuration, training and coaching users, product/adoption roles for AI tools, \
applied AI consulting where business change is central.

The candidate is NOT suitable for: senior AI engineering, AI agent engineering, ML engineering, \
data science, BI engineering, platform engineering, engineering management, deep solutions \
architecture, quota-carrying sales, generic SaaS implementation with no AI transformation element.

Evaluate the job using the full title, company, description, requirements and source URL.

First answer these gates:
1. Is this role primarily about AI adoption, workflow transformation, enablement or business-facing AI implementation?
2. Is it realistic for a strong innovation consultant with hands-on AI building experience but without a formal software engineering background?
3. Is it not primarily engineering, data, BI, sales or senior leadership?
4. Is it UK-based, UK-remote, or visa/relocation-friendly?

If any answer is clearly no, reject the role.

Hard rejection rules — reject if the title contains any of these unless the description clearly proves it is actually adoption/workflow-focused:
Software Engineer, Senior Software Engineer, AI Engineer, AI Agent Engineer, ML Engineer, \
Machine Learning Engineer, Data Scientist, Data Analyst, BI Engineer, Engineering Manager, \
Platform Engineer, Infrastructure Engineer, Cloud Architect, Enterprise Architect, \
Sales Engineer, Account Executive, Business Development, Revenue Lead.

Be especially sceptical of: Forward Deployed Engineer, Solutions Engineer, Senior Solutions Consultant, \
Professional Services Consultant. These can be relevant only if the job is mostly workflow discovery, \
adoption, implementation, training and business transformation, not coding, sales or enterprise \
software configuration.

Score:
- Archetype fit: 35 points
- Workflow/adoption/change-management substance: 25 points
- Seniority realism: 15 points
- Technical appropriateness: 10 points
- Geography: 10 points
- Salary/upside: 5 points

Only accept roles scoring 70 or higher.

Classify:
- 90-100: Bullseye
- 80-89: Strong fit
- 70-79: Stretch or Backup
- Below 70: Reject

Return valid JSON only:
{
  "decision": "accept" or "reject",
  "score": 0-100,
  "category": "Bullseye" or "Strong fit" or "Stretch" or "Backup" or "Reject",
  "reason": "one concise explanation",
  "main_risk": "one concise risk",
  "application_angle": "one sentence if accepted, otherwise null",
  "rejection_type": "too_technical | too_sales_led | too_senior | wrong_function | not_ai_specific | wrong_location | generic_saas | not_rejected"
}

Be harsh. Reject roles that merely contain AI keywords. Reject roles that a normal recruiter would \
expect a software engineer, ML engineer, data analyst, BI engineer, sales engineer or senior \
enterprise architect to fill.\
"""

_DEFAULT_QUERY_FAMILIES = json.dumps([
    '"AI Adoption Specialist" UK OR London OR remote',
    '"AI Enablement Specialist" UK OR London OR remote',
    '"AI Advisor" "workflow" UK OR London',
    '"AI Workflow Transformation" UK OR London',
    '"GenAI Adoption" consultant UK',
    '"AI Change Adoption Manager" UK',
    '"AI Transformation Consultant" "adoption" UK',
    '"AI Training and Adoption" UK',
    '"Copilot Adoption Consultant" UK',
    '"Microsoft Copilot Enablement" UK',
    '"AI Capability Lead" UK',
    '"AI Implementation Consultant" "workflow" UK',
    '"Applied AI Consultant" "workflows" UK',
    '"AI Product Specialist" "enterprise AI" UK',
    '"AI Automation Consultant" "business process" UK',
    '"AI Business Analyst" "GenAI" UK',
    '"Digital Transformation Consultant" "GenAI" UK',
    '"AI Programme Associate" UK',
    '"AI Experience Designer" "AI" "workflow" UK',
    '"AI Delivery Consultant" "adoption" UK',
    'site:greenhouse.io "AI Adoption" UK',
    'site:lever.co "AI Enablement" UK',
    'site:ashbyhq.com "GenAI Adoption" UK',
    'site:workday.com "AI Transformation" UK',
    'site:greenhouse.io "AI Advisor" "workflow"',
    'site:lever.co "AI Adoption" London',
    'site:ashbyhq.com "AI Enablement" London',
    'site:lever.co "AI Transformation" UK',
    'site:greenhouse.io "AI Adoption Specialist" UK',
    'site:greenhouse.io "AI Enablement Specialist" UK',
    'site:lever.co "AI Adoption" UK',
    'site:ashbyhq.com "AI Transformation" UK',
    'site:workday.com "AI Enablement" London',
    'site:service.gov.uk "AI" "transformation"',
    'site:gov.uk "AI" "adoption"',
    '"AI Literacy" consultant UK jobs',
], indent=None)


# ── DB context ─────────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_col(conn, table: str, col: str, definition: str):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
    except Exception:
        pass  # column already exists


# ── Schema init & migration ────────────────────────────────────────────────────

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS crawl_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at   TEXT DEFAULT (datetime('now')),
                completed_at TEXT,
                status       TEXT DEFAULT 'running',
                jobs_found   INTEGER DEFAULT 0,
                log          TEXT DEFAULT '',
                error        TEXT
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                crawl_run_id  INTEGER REFERENCES crawl_runs(id),
                title         TEXT,
                company       TEXT,
                location      TEXT,
                salary        TEXT,
                job_type      TEXT,
                date_posted   TEXT,
                apply_url     TEXT,
                description   TEXT,
                source        TEXT,
                found_at      TEXT DEFAULT (datetime('now'))
            );
        """)

        # ── Migrate jobs table ──
        for col, defn in [
            ("evaluator_decision",  "TEXT DEFAULT 'pending'"),
            ("fit_score",           "INTEGER"),
            ("fit_category",        "TEXT"),
            ("evaluator_reason",    "TEXT"),
            ("main_risk",           "TEXT"),
            ("application_angle",   "TEXT"),
            ("rejection_type",      "TEXT"),
            ("raw_evaluator_json",  "TEXT"),
            ("url_hash",            "TEXT"),
            ("normalized_job_key",  "TEXT"),
            ("duplicate_seen_before", "INTEGER DEFAULT 0"),
            ("raw_relevance_reason", "TEXT"),
            ("deleted",             "INTEGER NOT NULL DEFAULT 0"),
            ("applied",             "INTEGER NOT NULL DEFAULT 0"),
        ]:
            _add_col(conn, "jobs", col, defn)

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_jobs_run    ON jobs(crawl_run_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_hash   ON jobs(url_hash);
            CREATE INDEX IF NOT EXISTS idx_jobs_dec    ON jobs(evaluator_decision);
            CREATE INDEX IF NOT EXISTS idx_jobs_score  ON jobs(fit_score);
        """)

        # ── Seed config defaults ──
        defaults = [
            ("role_search_prompt",          _DEFAULT_ROLE_SEARCH_PROMPT),
            ("evaluator_prompt",            _DEFAULT_EVALUATOR_PROMPT),
            ("query_families",              _DEFAULT_QUERY_FAMILIES),
            ("min_accept_score",            "70"),
            ("max_source_share_pct",        "30"),
            ("include_rejected_near_misses","true"),
        ]
        for key, val in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (key, val)
            )

        # Migrate old search_prompt → role_search_prompt
        old = conn.execute(
            "SELECT value FROM config WHERE key = 'search_prompt'"
        ).fetchone()
        has_new = conn.execute(
            "SELECT 1 FROM config WHERE key = 'role_search_prompt' AND value != ?",
            (_DEFAULT_ROLE_SEARCH_PROMPT,)
        ).fetchone()
        if old and not has_new:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES ('role_search_prompt', ?)",
                (old["value"],)
            )


# ── Config ─────────────────────────────────────────────────────────────────────

def get_config(key: str) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def get_all_config() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_config(key: str, value: str):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO config (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE
               SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value),
        )


# ── Crawl runs ─────────────────────────────────────────────────────────────────

def create_crawl_run() -> int:
    with get_db() as conn:
        return conn.execute("INSERT INTO crawl_runs (status) VALUES ('running')").lastrowid


def append_crawl_log(run_id: int, message: str):
    ts = datetime.now().strftime("%H:%M:%S")
    with get_db() as conn:
        conn.execute(
            "UPDATE crawl_runs SET log = log || ? WHERE id = ?",
            (f"[{ts}] {message}\n", run_id),
        )


def finish_crawl_run(run_id: int, status: str, jobs_found: int = 0, error: str | None = None):
    with get_db() as conn:
        conn.execute(
            """UPDATE crawl_runs
               SET completed_at=datetime('now'), status=?, jobs_found=?, error=?
               WHERE id=?""",
            (status, jobs_found, error, run_id),
        )


# ── Jobs ───────────────────────────────────────────────────────────────────────

def _make_url_hash(url: str | None) -> str:
    if not url:
        return ""
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()[:20]


def _make_job_key(title: str | None, company: str | None) -> str:
    return f"{(title or '').lower().strip()}|{(company or '').lower().strip()}"


def insert_jobs(run_id: int, jobs: list[dict]):
    rows = []
    for j in jobs:
        rows.append({
            "crawl_run_id":           run_id,
            "title":                  j.get("title"),
            "company":                j.get("company"),
            "location":               j.get("location"),
            "salary":                 j.get("salary"),
            "job_type":               j.get("job_type"),
            "date_posted":            j.get("date_posted"),
            "apply_url":              j.get("apply_url"),
            "description":            j.get("description"),
            "source":                 j.get("source"),
            "raw_relevance_reason":   j.get("raw_relevance_reason"),
            "evaluator_decision":     j.get("evaluator_decision", "pending"),
            "fit_score":              j.get("fit_score"),
            "fit_category":           j.get("fit_category"),
            "evaluator_reason":       j.get("evaluator_reason"),
            "main_risk":              j.get("main_risk"),
            "application_angle":      j.get("application_angle"),
            "rejection_type":         j.get("rejection_type"),
            "raw_evaluator_json":     j.get("raw_evaluator_json"),
            "url_hash":               _make_url_hash(j.get("apply_url")),
            "normalized_job_key":     _make_job_key(j.get("title"), j.get("company")),
            "duplicate_seen_before":  int(j.get("duplicate_seen_before", 0)),
        })
    with get_db() as conn:
        conn.executemany(
            """INSERT INTO jobs (
                crawl_run_id, title, company, location, salary, job_type,
                date_posted, apply_url, description, source, raw_relevance_reason,
                evaluator_decision, fit_score, fit_category, evaluator_reason,
                main_risk, application_angle, rejection_type, raw_evaluator_json,
                url_hash, normalized_job_key, duplicate_seen_before
               ) VALUES (
                :crawl_run_id, :title, :company, :location, :salary, :job_type,
                :date_posted, :apply_url, :description, :source, :raw_relevance_reason,
                :evaluator_decision, :fit_score, :fit_category, :evaluator_reason,
                :main_risk, :application_angle, :rejection_type, :raw_evaluator_json,
                :url_hash, :normalized_job_key, :duplicate_seen_before
               )""",
            rows,
        )


def get_jobs_for_run_by_decision(run_id: int, decision: str | None = None) -> list[dict]:
    with get_db() as conn:
        if decision:
            rows = conn.execute(
                """SELECT * FROM jobs WHERE crawl_run_id=? AND evaluator_decision=?
                   ORDER BY COALESCE(fit_score,0) DESC, id""",
                (run_id, decision),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM jobs WHERE crawl_run_id=?
                   ORDER BY COALESCE(fit_score,0) DESC, id""",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_jobs_for_run(run_id: int) -> list[dict]:
    return get_jobs_for_run_by_decision(run_id)


def get_previously_seen_url_hashes(exclude_run_id: int | None = None) -> set[str]:
    with get_db() as conn:
        if exclude_run_id:
            rows = conn.execute(
                """SELECT DISTINCT url_hash FROM jobs
                   WHERE evaluator_decision='accepted'
                   AND crawl_run_id != ? AND url_hash != ''""",
                (exclude_run_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT url_hash FROM jobs WHERE evaluator_decision='accepted' AND url_hash != ''"
            ).fetchall()
        return {r["url_hash"] for r in rows}


def get_recent_runs(limit: int = 10) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM crawl_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_run_log(run_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT log, status FROM crawl_runs WHERE id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None


def get_latest_completed_run_id() -> int | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM crawl_runs WHERE status='completed' ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None


def get_all_unique_jobs(decision: str) -> list[dict]:
    """All non-deleted jobs with the given decision, deduplicated across runs.

    When the same URL or title+company appears in multiple runs, the highest-
    scoring instance is kept (ties broken by most-recently inserted id).
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM jobs
               WHERE evaluator_decision = ?
                 AND (deleted IS NULL OR deleted = 0)
               ORDER BY COALESCE(fit_score, 0) DESC, id DESC""",
            (decision,),
        ).fetchall()

    seen_hashes: set[str] = set()
    seen_keys: set[str] = set()
    unique = []
    for row in [dict(r) for r in rows]:
        h = row.get("url_hash") or ""
        k = row.get("normalized_job_key") or "|"
        if h and h in seen_hashes:
            continue
        if k != "|" and k in seen_keys:
            continue
        if h:
            seen_hashes.add(h)
        if k != "|":
            seen_keys.add(k)
        unique.append(row)
    return unique


def set_job_deleted(job_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE jobs SET deleted = 1 WHERE id = ?", (job_id,))


def set_job_applied(job_id: int, applied: bool) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE jobs SET applied = ? WHERE id = ?",
            (1 if applied else 0, job_id),
        )
