# RoleFinder — Design Document

## 1. Overview

RoleFinder is an agentic job-discovery system. Once per day it autonomously crawls multiple job boards using an AI agent, deduplicates results, stores them in a local database, and emails a formatted report to a configured recipient. A web dashboard allows the user to edit the search criteria and trigger on-demand crawls.

---

## 2. Goals

| Goal | Description |
|------|-------------|
| Automated discovery | Find relevant job listings daily without manual searching |
| Configurable criteria | User can update what the agent searches for at any time |
| Email delivery | Receive a digest at a fixed email address each morning |
| On-demand crawl | Trigger a crawl immediately from the dashboard |
| Live feedback | See crawl progress in real time from the browser |
| Self-hosted | Runs on a single cheap VM with no external managed services |

**Out of scope (v1)**
- Authentication / multi-user support
- Deduplication across days (same job appearing on multiple days)
- Applying to jobs from the dashboard
- Push notifications / Slack integration

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        GCP e2-micro VM                          │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │   FastAPI    │    │  APScheduler │    │  OpenAI Agent    │  │
│  │  Dashboard   │───▶│  (05:00 UTC) │───▶│  web_search_     │  │
│  │  :8080       │    │              │    │  preview × 8     │  │
│  └──────┬───────┘    └──────────────┘    └────────┬─────────┘  │
│         │                                          │            │
│         │            ┌──────────────┐              │            │
│         └───────────▶│  SQLite DB   │◀─────────────┘            │
│                      │  rolefinder  │                           │
│                      │  .db         │                           │
│                      └──────────────┘                           │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  systemd service (rolefinder.service) — always running   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                   Gmail SMTP │ (port 587 + TLS)
                              ▼
                   edward-goh@outlook.com
```

---

## 4. File Structure

```
RoleFinder/
├── run.py                      # Entry point — loads .env, starts uvicorn
├── requirements.txt
├── Dockerfile                  # For future Cloud Run deployment
├── .env                        # Secrets — never committed
├── .env.example                # Template for .env
├── .gitignore
│
└── app/
    ├── __init__.py
    ├── main.py                 # FastAPI app, routes, lifespan
    ├── crawler.py              # OpenAI agent crawl logic
    ├── emailer.py              # HTML email builder + SMTP sender
    ├── scheduler.py            # APScheduler daily job
    ├── database.py             # SQLite schema + all DB operations
    └── templates/
        └── dashboard.html      # Single-page dashboard (Tailwind CDN)
```

---

## 5. Component Design

### 5.1 Crawler (`app/crawler.py`)

The crawler is the core of the system. It uses the **OpenAI Responses API** with the built-in `web_search_preview` tool, which gives GPT-4o live access to web search results.

**Crawl flow:**

```
run_crawl(run_id)
    │
    ├── Read search_prompt from DB config
    │
    ├── For each board in BOARDS (batches of 2, parallel):
    │       _search_one_board(client, prompt, board, run_id)
    │           │
    │           ├── Build targeted search prompt for this board
    │           ├── Call OpenAI Responses API (gpt-4o + web_search_preview)
    │           ├── Extract output_text from response
    │           ├── Regex-extract JSON array from response text
    │           └── Return list of validated job dicts
    │
    ├── Flatten all results → all_jobs[]
    ├── _deduplicate() by (title.lower, company.lower)
    ├── insert_jobs(run_id, unique_jobs)
    ├── finish_crawl_run(run_id, "completed")
    └── Return unique_jobs
```

**Job boards searched (8 total):**
1. LinkedIn Jobs
2. Indeed
3. Glassdoor
4. Wellfound (AngelList)
5. Remotive
6. Otta
7. Hired
8. Stack Overflow Jobs

**Parallelism:** Boards are searched in pairs (`batch_size=2`) with a 3-second pause between batches. This balances throughput against OpenAI rate limits.

**Estimated runtime:** 5–20 minutes depending on OpenAI API response times.

**Output schema per job:**

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Exact job title from the listing |
| `company` | string | Company name |
| `location` | string | City/country, "Remote", or "Hybrid – City" |
| `salary` | string \| null | e.g. "£80k–£110k" |
| `job_type` | string | Full-time / Contract / Part-time |
| `date_posted` | string \| null | ISO date or relative e.g. "3 days ago" |
| `apply_url` | string \| null | Direct link to the job posting |
| `description` | string \| null | 1–2 sentence summary |
| `source` | string | Job board name |

---

### 5.2 Scheduler (`app/scheduler.py`)

Uses **APScheduler** (`AsyncIOScheduler`) with a `CronTrigger`. Configured via environment variables:

```
CRAWL_HOUR=5        # 05:00 server local time
CRAWL_MINUTE=0
```

The scheduler starts inside FastAPI's `lifespan` context, so it runs in the same process as the web server. This means:
- No separate worker process needed
- The VM must stay running (handled by systemd `Restart=always`)
- If the process crashes, systemd restarts it within 10 seconds

**Daily job sequence:**
1. `create_crawl_run()` → get `run_id`
2. `await run_crawl(run_id)` → crawl + store jobs
3. `send_job_report(jobs)` → send email
4. Log result

---

### 5.3 Email (`app/emailer.py`)

Sends a **single HTML email** via SMTP with STARTTLS. Compatible with Gmail, Outlook SMTP, and SendGrid SMTP relay.

**Email structure:**
```
┌────────────────────────────────────────────────┐
│ HEADER: "RoleFinder Daily Report"              │
│         Monday, 01 June 2026 — 12 roles found  │
├────────────────────────────────────────────────┤
│ SUMMARY BAR: "Found 12 matching roles..."      │
├────────────────────────────────────────────────┤
│ TABLE:                                         │
│  Role | Company | Location | Salary | Type |   │
│  Posted | Source                               │
│  ─────────────────────────────────────────     │
│  [linked job title rows...]                    │
├────────────────────────────────────────────────┤
│ FOOTER: "Sent by RoleFinder"                   │
└────────────────────────────────────────────────┘
```

Role titles are hyperlinked to `apply_url` if available. All user-supplied content is HTML-escaped to prevent injection.

---

### 5.4 Dashboard (`app/templates/dashboard.html`)

Single-page, no framework — vanilla JS + Tailwind CSS (CDN). No build step required.

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ HEADER: RoleFinder    Schedule: 05:00   [Run Trawl Now] │
├──────────────────┬──────────────────────────────────────┤
│ Search Criteria  │  Job Results                         │
│ ─────────────── │  ───────────────────────────────────  │
│ [textarea]       │  [Run Trawl Now btn]  Run #N  status  │
│ [Save]           │                                      │
│                  │  [Live log — shown during crawl]     │
│ Crawl History    │                                      │
│ ─────────────── │  Role | Company | Location | Salary   │
│ #3  12 jobs ✓   │  Type | Posted | Source               │
│ #2   8 jobs ✓   │  [job rows...]                        │
│ #1   0 jobs ✗   │                                      │
└──────────────────┴──────────────────────────────────────┘
```

**Interactions:**

| Action | Mechanism |
|--------|-----------|
| Save criteria | `POST /api/config` |
| Run Trawl Now | `POST /api/crawl/trigger` → returns `run_id` |
| Live log | Polls `GET /api/crawl/runs/{id}/log` every 2 seconds |
| Load historical run | `GET /api/crawl/runs/{id}/jobs` |
| Auto-refresh history | Called after crawl completes |

Both "Run Trawl Now" buttons (header + panel) share state via `setTriggerState()` so they disable/re-enable together.

---

### 5.5 Database (`app/database.py`)

**SQLite** with WAL journal mode for concurrent reads during crawls.

**Schema:**

```sql
CREATE TABLE config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE crawl_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    status       TEXT DEFAULT 'running',   -- running | completed | failed
    jobs_found   INTEGER DEFAULT 0,
    log          TEXT DEFAULT '',          -- append-only progress log
    error        TEXT                      -- set on failure
);

CREATE TABLE jobs (
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
```

**Config keys:**

| Key | Default | Description |
|-----|---------|-------------|
| `search_prompt` | (see default in database.py) | The criteria prompt sent to the AI agent |

---

## 6. API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Renders the dashboard HTML |
| `GET` | `/api/config` | Returns `{ search_prompt }` |
| `POST` | `/api/config` | Updates `search_prompt`. Body: `{ "prompt": "..." }` |
| `POST` | `/api/crawl/trigger` | Starts a background crawl. Returns `{ run_id, already_running }` |
| `GET` | `/api/crawl/runs` | Returns last 20 crawl run records |
| `GET` | `/api/crawl/runs/{id}/jobs` | Returns all jobs for a run |
| `GET` | `/api/crawl/runs/{id}/log` | Returns `{ log, status }` — polled for live updates |

---

## 7. Configuration Reference (`.env`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | ✅ | — | OpenAI API key |
| `SMTP_HOST` | ✅ | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | ✅ | `587` | SMTP port (STARTTLS) |
| `SMTP_USER` | ✅ | — | SMTP login username |
| `SMTP_PASSWORD` | ✅ | — | SMTP password or App Password |
| `EMAIL_FROM` | ✅ | — | Sender address |
| `EMAIL_TO` | ✅ | `edward-goh@outlook.com` | Recipient address |
| `CRAWL_HOUR` | ✅ | `5` | Hour to run daily crawl (24h, server local time) |
| `CRAWL_MINUTE` | — | `0` | Minute to run daily crawl |
| `PORT` | — | `8080` | HTTP port for the dashboard |
| `DB_PATH` | — | `./rolefinder.db` | SQLite file path (override for Docker volume) |

---

## 8. Deployment

### Current: GCP Compute Engine e2-micro

- **OS:** Debian GNU/Linux (cloud-amd64)
- **Process manager:** systemd (`rolefinder.service`)
- **Restart policy:** `Restart=always`, `RestartSec=10`
- **Port:** 8080 open via VPC firewall rule
- **Dashboard URL:** `http://34.105.162.64:8080`

**Service file location:** `/etc/systemd/system/rolefinder.service`

**Common operations:**
```bash
# View live logs
sudo journalctl -u rolefinder -f

# Restart after code update
cd ~/RoleFinder && git pull && sudo systemctl restart rolefinder

# Check status
sudo systemctl status rolefinder
```

### Alternative: Docker / Cloud Run

A `Dockerfile` is included for containerised deployment. The `DB_PATH` environment variable should point to a mounted persistent volume (`/data/rolefinder.db`) since Cloud Run instances have ephemeral local storage.

```bash
# Build and run locally
docker build -t rolefinder .
docker run -p 8080:8080 --env-file .env -v rolefinder_data:/data rolefinder
```

For Cloud Run with persistent storage, attach a Cloud Filestore NFS mount or use Cloud SQL instead of SQLite.

---

## 9. Data Flow — End to End

```
05:00 UTC daily
    │
    ▼
APScheduler fires _daily_job()
    │
    ├── create_crawl_run() → run_id = N
    │
    ├── run_crawl(N)
    │       ├── [Board 1 + 2] → OpenAI gpt-4o + web_search_preview
    │       │       └── Bing search → job board pages → JSON extraction
    │       ├── [Board 3 + 4] → same
    │       ├── [Board 5 + 6] → same
    │       ├── [Board 7 + 8] → same
    │       ├── deduplicate(all_jobs)
    │       └── insert_jobs(N, unique_jobs) → SQLite
    │
    ├── send_job_report(jobs)
    │       ├── build_html(jobs) → HTML email string
    │       └── smtplib → Gmail SMTP → edward-goh@outlook.com
    │
    └── Log: "Daily crawl complete: X jobs found and emailed"
```

---

## 10. Security Notes

| Concern | Mitigation |
|---------|------------|
| API key exposure | `.env` is gitignored; file permissions set to `600` on the VM |
| Dashboard access | Currently open to the internet on port 8080. For production, restrict the firewall rule to your IP or add HTTP Basic Auth |
| HTML injection in job data | All job fields are HTML-escaped in `emailer.py` before rendering |
| SMTP credentials | Stored only in `.env`, never logged |
| OpenAI key in chat history | Consider rotating the key at myaccount.google.com if this chat is accessible to others |

---

## 11. Known Limitations (v1)

| Limitation | Impact | Potential fix |
|------------|--------|---------------|
| No cross-day deduplication | Same job can appear in multiple daily emails | Add a `url_hash` index and skip seen URLs |
| SQLite on single VM | No redundancy; data lost if VM is deleted | Nightly `sqlite3 .backup` to Cloud Storage |
| AI job extraction accuracy | Model occasionally hallucinates or misses fields | Add a validation/scoring step post-extraction |
| No HTTPS | Dashboard traffic is unencrypted | Nginx + Certbot on the VM, or Cloud Load Balancer |
| Single-region | VM in one GCP region | Acceptable for a personal tool |
| OpenAI dependency | Crawl fails if OpenAI API is down | Add retry with exponential backoff |

---

## 12. Potential Future Enhancements

- **Cross-day deduplication** — track seen `apply_url` hashes and only email new listings
- **Relevance scoring** — use GPT to score each job 1–10 against the criteria and sort by score
- **HTTPS + domain** — Nginx reverse proxy + Let's Encrypt cert
- **Auth** — HTTP Basic Auth or a simple PIN to protect the dashboard
- **Slack / webhook delivery** — alternative to email
- **Multiple saved criteria** — e.g. "Plan A roles" vs "backup roles"
- **Apply tracking** — mark jobs as applied/interested/ignored from the dashboard
