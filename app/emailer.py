import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

logger = logging.getLogger(__name__)

# ── Category config ────────────────────────────────────────────────────────────

_SECTIONS = [
    ("Bullseye",       (90, 101), "#166534", "#dcfce7"),
    ("Strong Fit",     (80, 90),  "#1e40af", "#dbeafe"),
    ("Stretch/Backup", (70, 80),  "#92400e", "#fef3c7"),
]


def _score_badge(score: int | None) -> str:
    n = score or 0
    if n >= 90:
        colour = "#166534"
        bg = "#dcfce7"
    elif n >= 80:
        colour = "#1e40af"
        bg = "#dbeafe"
    elif n >= 70:
        colour = "#92400e"
        bg = "#fef3c7"
    else:
        colour = "#6b7280"
        bg = "#f3f4f6"
    return (
        f'<span style="display:inline-block;background:{bg};color:{colour};'
        f'font-weight:700;font-size:12px;padding:2px 8px;border-radius:9999px;">'
        f'{n}</span>'
    )


def _job_row(job: dict) -> str:
    url = escape(job.get("apply_url") or "")
    title = escape(job.get("title") or "N/A")
    company = escape(job.get("company") or "—")
    location = escape(job.get("location") or "—")
    salary = escape(job.get("salary") or "—")
    source = escape(job.get("source") or "—")
    posted = escape(job.get("date_posted") or "—")
    reason = escape(job.get("evaluator_reason") or "")
    risk = escape(job.get("main_risk") or "")
    angle = escape(job.get("application_angle") or "")
    score = job.get("fit_score")

    linked_title = (
        f'<a href="{url}" style="color:#1d4ed8;text-decoration:none;font-weight:600;">{title}</a>'
        if url else f"<strong>{title}</strong>"
    )

    sub_rows = ""
    if reason:
        sub_rows += f'<tr><td colspan="6" style="padding:0 12px 4px 12px;font-size:12px;color:#166534;">✓ {reason}</td></tr>'
    if risk:
        sub_rows += f'<tr><td colspan="6" style="padding:0 12px 4px 12px;font-size:12px;color:#92400e;">⚠ {risk}</td></tr>'
    if angle:
        sub_rows += f'<tr><td colspan="6" style="padding:0 12px 8px 12px;font-size:12px;color:#1e40af;">→ {angle}</td></tr>'

    return f"""
<tr style="border-bottom:1px solid #f3f4f6;">
  <td style="padding:10px 12px;vertical-align:top;">{_score_badge(score)}</td>
  <td style="padding:10px 12px;vertical-align:top;">{linked_title}<br>
      <span style="font-size:12px;color:#6b7280;">{company}</span></td>
  <td style="padding:10px 12px;vertical-align:top;font-size:13px;color:#4b5563;">{location}</td>
  <td style="padding:10px 12px;vertical-align:top;font-size:13px;color:#4b5563;white-space:nowrap;">{salary}</td>
  <td style="padding:10px 12px;vertical-align:top;font-size:12px;color:#6b7280;">{posted}</td>
  <td style="padding:10px 12px;vertical-align:top;font-size:12px;color:#9ca3af;">{source}</td>
</tr>{sub_rows}"""


def _near_miss_row(job: dict) -> str:
    title = escape(job.get("title") or "N/A")
    company = escape(job.get("company") or "—")
    score = job.get("fit_score") or 0
    rtype = escape(job.get("rejection_type") or "—").replace("_", " ")
    reason = escape(job.get("evaluator_reason") or "")
    source = escape(job.get("source") or "—")
    return f"""
<tr style="border-bottom:1px solid #f3f4f6;">
  <td style="padding:8px 12px;font-size:13px;">{title}<br>
      <span style="font-size:12px;color:#6b7280;">{company}</span></td>
  <td style="padding:8px 12px;font-size:13px;">{_score_badge(score)}</td>
  <td style="padding:8px 12px;font-size:12px;color:#92400e;">{rtype}</td>
  <td style="padding:8px 12px;font-size:12px;color:#6b7280;">{reason}</td>
  <td style="padding:8px 12px;font-size:12px;color:#9ca3af;">{source}</td>
</tr>"""


def _section(label: str, score_range: tuple, colour: str, bg: str, jobs: list[dict]) -> str:
    if not jobs:
        return ""
    rows = "".join(_job_row(j) for j in jobs)
    return f"""
<div style="margin-bottom:28px;">
  <div style="background:{bg};border-left:4px solid {colour};padding:8px 16px;margin-bottom:8px;border-radius:4px;">
    <span style="font-weight:700;color:{colour};font-size:14px;">{label}</span>
    <span style="color:{colour};font-size:13px;margin-left:8px;">{len(jobs)} role{'s' if len(jobs)!=1 else ''}</span>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <thead>
      <tr style="border-bottom:2px solid #e5e7eb;">
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">Score</th>
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">Role / Company</th>
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">Location</th>
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">Salary</th>
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">Posted</th>
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">Source</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""


def build_html(
    accepted_jobs: list[dict],
    near_misses: list[dict],
    run_date: datetime,
    include_near_misses: bool = True,
) -> str:
    date_str = run_date.strftime("%A, %d %B %Y")
    count = len(accepted_jobs)

    body_parts = []

    if accepted_jobs:
        for label, score_range, colour, bg in _SECTIONS:
            group = [j for j in accepted_jobs if score_range[0] <= (j.get("fit_score") or 0) < score_range[1]]
            body_parts.append(_section(label, score_range, colour, bg, group))
    else:
        body_parts.append("""
<div style="background:#fef9c3;border-left:4px solid #ca8a04;padding:16px;border-radius:4px;margin-bottom:24px;">
  <strong>No roles passed the acceptance threshold today.</strong>
  The evaluator did not find any listings that scored 70 or above.
</div>""")

    near_miss_section = ""
    if include_near_misses and near_misses:
        nm_rows = "".join(_near_miss_row(j) for j in near_misses[:5])
        near_miss_section = f"""
<div style="margin-top:32px;border-top:1px solid #e5e7eb;padding-top:24px;">
  <h3 style="margin:0 0 12px;font-size:15px;color:#374151;">Near-misses (top 5 rejected)</h3>
  <table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead>
      <tr style="border-bottom:2px solid #e5e7eb;">
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;">Role</th>
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;">Score</th>
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;">Reason</th>
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;">Detail</th>
        <th style="text-align:left;padding:6px 12px;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;">Source</th>
      </tr>
    </thead>
    <tbody>{nm_rows}</tbody>
  </table>
</div>"""

    body_html = "".join(body_parts) + near_miss_section

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:20px;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#111827;">
<div style="max-width:860px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">
  <div style="background:#1e3a8a;padding:28px 32px;">
    <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;">RoleFinder Daily Report</h1>
    <p style="margin:6px 0 0;color:#93c5fd;font-size:14px;">
      {date_str} &mdash; <strong style="color:#fff;">{count} role{'s' if count!=1 else ''} accepted</strong>
    </p>
  </div>
  <div style="padding:24px 32px;">
    {body_html}
  </div>
  <div style="padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;">
    <p style="margin:0;font-size:12px;color:#9ca3af;">
      Sent by <strong>RoleFinder</strong> &mdash; update your criteria at your dashboard.
    </p>
  </div>
</div>
</body>
</html>"""


def send_job_report(
    accepted_jobs: list[dict],
    near_misses: list[dict] | None = None,
    run_date: datetime | None = None,
    include_near_misses: bool = True,
):
    if run_date is None:
        run_date = datetime.now()
    if near_misses is None:
        near_misses = []

    smtp_host     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port     = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user     = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    email_from    = os.environ.get("EMAIL_FROM", smtp_user)
    email_to      = os.environ.get("EMAIL_TO", "edward-goh@outlook.com")

    count = len(accepted_jobs)
    subject = (
        f"RoleFinder: {count} role{'s' if count!=1 else ''} accepted — {run_date.strftime('%d %b %Y')}"
        if count
        else f"RoleFinder: no roles today — {run_date.strftime('%d %b %Y')}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(
        build_html(accepted_jobs, near_misses, run_date, include_near_misses),
        "html", "utf-8"
    ))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.sendmail(email_from, [email_to], msg.as_string())

    logger.info("Email sent to %s — %d accepted job(s)", email_to, count)
