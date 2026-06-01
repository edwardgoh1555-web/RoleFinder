import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "rolefinder.db"

DEFAULT_PROMPT = """\
Find job roles that match the following criteria:

- Role: Senior Software Engineer or Staff Engineer
- Location: Remote or London, UK
- Tech stack: Python, TypeScript, or Go
- Industry: FinTech, AI/ML, or SaaS
- Seniority: 5+ years experience preferred

Search LinkedIn Jobs, Indeed, Glassdoor, Wellfound (AngelList), and Remotive.\
"""


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


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS crawl_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at   TEXT    DEFAULT (datetime('now')),
                completed_at TEXT,
                status       TEXT    DEFAULT 'running',
                jobs_found   INTEGER DEFAULT 0,
                log          TEXT    DEFAULT '',
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

        conn.execute(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
            ("search_prompt", DEFAULT_PROMPT),
        )


# ── Config ────────────────────────────────────────────────────────────────────

def get_config(key: str) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_config(key: str, value: str):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO config (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE
               SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, value),
        )


# ── Crawl runs ─────────────────────────────────────────────────────────────────

def create_crawl_run() -> int:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO crawl_runs (status) VALUES ('running')"
        )
        return cursor.lastrowid


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
               SET completed_at = datetime('now'), status = ?, jobs_found = ?, error = ?
               WHERE id = ?""",
            (status, jobs_found, error, run_id),
        )


# ── Jobs ───────────────────────────────────────────────────────────────────────

def insert_jobs(run_id: int, jobs: list[dict]):
    with get_db() as conn:
        conn.executemany(
            """INSERT INTO jobs
                   (crawl_run_id, title, company, location, salary,
                    job_type, date_posted, apply_url, description, source)
               VALUES
                   (:crawl_run_id, :title, :company, :location, :salary,
                    :job_type, :date_posted, :apply_url, :description, :source)""",
            [{"crawl_run_id": run_id, **j} for j in jobs],
        )


def get_recent_runs(limit: int = 10) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM crawl_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_jobs_for_run(run_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE crawl_run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_run_log(run_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT log, status FROM crawl_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None


def get_latest_completed_run_id() -> int | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM crawl_runs WHERE status = 'completed' ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None
