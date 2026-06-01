import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

logger = logging.getLogger(__name__)


def _row(job: dict) -> str:
    url = escape(job.get("apply_url") or "")
    title = escape(job.get("title") or "N/A")
    company = escape(job.get("company") or "—")
    location = escape(job.get("location") or "—")
    salary = escape(job.get("salary") or "—")
    job_type = escape(job.get("job_type") or "—")
    posted = escape(job.get("date_posted") or "—")
    source = escape(job.get("source") or "—")

    linked_title = (
        f'<a href="{url}" style="color:#1d4ed8;text-decoration:none;font-weight:600;">{title}</a>'
        if url
        else f'<strong>{title}</strong>'
    )

    return f"""\
<tr>
  <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;vertical-align:top;">{linked_title}</td>
  <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;vertical-align:top;">{company}</td>
  <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;vertical-align:top;color:#4b5563;font-size:13px;">{location}</td>
  <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;vertical-align:top;color:#4b5563;font-size:13px;white-space:nowrap;">{salary}</td>
  <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;vertical-align:top;font-size:12px;">
    <span style="background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:9999px;">{job_type}</span>
  </td>
  <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;vertical-align:top;color:#6b7280;font-size:13px;">{posted}</td>
  <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;vertical-align:top;color:#6b7280;font-size:12px;">{source}</td>
</tr>"""


def build_html(jobs: list[dict], run_date: datetime) -> str:
    date_str = run_date.strftime("%A, %d %B %Y")
    count = len(jobs)
    rows = "\n".join(_row(j) for j in jobs)

    empty_state = "" if jobs else """\
<tr>
  <td colspan="7" style="text-align:center;padding:40px;color:#9ca3af;">
    No matching roles found in today's crawl.
  </td>
</tr>"""

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:20px;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#111827;">
<div style="max-width:900px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">

  <!-- Header -->
  <div style="background:#1e3a8a;padding:28px 32px;">
    <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;">RoleFinder Daily Report</h1>
    <p style="margin:6px 0 0;color:#93c5fd;font-size:14px;">
      {date_str} &mdash; <strong style="color:#fff;">{count} role{'s' if count != 1 else ''}</strong> found
    </p>
  </div>

  <!-- Summary bar -->
  <div style="padding:16px 32px;background:#eff6ff;border-bottom:1px solid #dbeafe;">
    <p style="margin:0;font-size:14px;color:#1e40af;">
      {'Found <strong>' + str(count) + ' matching role' + ('s' if count != 1 else '') + '</strong> based on your current search criteria.' if count else 'No matching roles were found today. Your criteria or the job market may need a refresh.'}
    </p>
  </div>

  <!-- Table -->
  <div style="padding:24px 32px;overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <thead>
        <tr style="border-bottom:2px solid #e5e7eb;">
          <th style="text-align:left;padding:8px 12px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Role</th>
          <th style="text-align:left;padding:8px 12px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Company</th>
          <th style="text-align:left;padding:8px 12px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Location</th>
          <th style="text-align:left;padding:8px 12px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Salary</th>
          <th style="text-align:left;padding:8px 12px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Type</th>
          <th style="text-align:left;padding:8px 12px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Posted</th>
          <th style="text-align:left;padding:8px 12px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Source</th>
        </tr>
      </thead>
      <tbody>
        {rows}{empty_state}
      </tbody>
    </table>
  </div>

  <!-- Footer -->
  <div style="padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;">
    <p style="margin:0;font-size:12px;color:#9ca3af;">
      Sent by <strong>RoleFinder</strong> &mdash; update your search criteria at your dashboard.
    </p>
  </div>

</div>
</body>
</html>"""


def send_job_report(jobs: list[dict], run_date: datetime | None = None):
    if run_date is None:
        run_date = datetime.now()

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    email_from = os.environ.get("EMAIL_FROM", smtp_user)
    email_to = os.environ.get("EMAIL_TO", "edward-goh@outlook.com")

    count = len(jobs)
    subject = (
        f"RoleFinder: {count} new role{'s' if count != 1 else ''} — {run_date.strftime('%d %b %Y')}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(build_html(jobs, run_date), "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.sendmail(email_from, [email_to], msg.as_string())

    logger.info("Email sent to %s — %d job(s)", email_to, count)
