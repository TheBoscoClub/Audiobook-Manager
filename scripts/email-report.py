#!/usr/bin/env python3
# /test:wiring-exception: standalone CLI tool, not service-graph wired. Operator runs manually as needed.
"""Email a translation verification report.

Usage:
    python scripts/email-report.py --to you@example.com \
        --report $AUDIOBOOKS_VAR_DIR/db/translation-verification.json
"""

import argparse
import json
import os
import pwd
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


def _require_audiobooks_user() -> None:
    """Abort if not running as the audiobooks user (mirrors lib/audiobook-config.sh)."""
    if os.environ.get("AUDIOBOOKS_SKIP_USER_GATE") == "1":
        return
    current = pwd.getpwuid(os.getuid()).pw_name
    if current == "audiobooks":
        return
    script_name = os.path.basename(sys.argv[0]) if sys.argv else "<script>"
    rest_args = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    print(
        f"error: {script_name} must run as the audiobooks user.\n"
        f"  current:  {current} (uid {os.getuid()})\n"
        f"  required: audiobooks\n"
        f"\n"
        f"Re-invoke with: sudo -u audiobooks {script_name} {rest_args}",
        file=sys.stderr,
    )
    sys.exit(1)


def load_smtp_config() -> dict:
    """Load SMTP config from audiobooks.conf."""
    conf = {}
    conf_path = Path("/etc/audiobooks/audiobooks.conf")
    if conf_path.exists():
        for line in conf_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                conf[key.strip()] = val.strip().strip('"').strip("'")
    return conf


def build_report_body(report_path: Path) -> str:
    """Build a human-readable email body from the JSON report."""
    if not report_path.exists():
        return "Verification report file not found. Check the server logs."

    data = json.loads(report_path.read_text())

    lines = [
        "AUDIOBOOK TRANSLATION VERIFICATION REPORT",
        "=" * 50,
        "",
        f"Timestamp:        {data.get('timestamp', 'unknown')}",
        f"Total books:      {data.get('total_books', 0)}",
        f"Verified:         {data.get('verified', 0)}",
        f"  PASS:           {data.get('pass', 0)}",
        f"  WARN:           {data.get('warn', 0)}",
        f"  FAIL:           {data.get('fail', 0)}",
        "",
        "SUBTITLE COUNTS",
        "-" * 30,
        f"  English chapters:  {data.get('en_chapters', 0)} (across {data.get('en_books', 0)} books)",
        f"  zh-Hans chapters:  {data.get('zh_chapters', 0)} (across {data.get('zh_books', 0)} books)",
        f"  Coverage:          {data.get('coverage_pct', 0)}%",
        "",
        "QUEUE STATE",
        "-" * 30,
    ]

    queue = data.get("queue", {})
    for state, count in sorted(queue.items()):
        lines.append(f"  {state}: {count}")

    # Failed book details
    details = data.get("details", [])
    failures = [d for d in details if d.get("status") == "FAIL"]
    if failures:
        lines.extend(["", "FAILURES", "-" * 30])
        for f in failures[:20]:
            issues = "; ".join(f.get("issues", [])[:3])
            lines.append(f"  [{f['book_id']}] {f['title'][:60]} — {issues}")
        if len(failures) > 20:
            lines.append(f"  ... and {len(failures) - 20} more")

    lines.extend(
        [
            "",
            "=" * 50,
            f"Full JSON report: {report_path}",
            "",
            "— Audiobook Manager Translation Pipeline",
        ]
    )

    return "\n".join(lines)


def send_email(to: str, subject: str, body: str, conf: dict) -> None:
    host = conf.get("SMTP_HOST", "smtp.resend.com")
    port = int(conf.get("SMTP_PORT", "587"))
    user = conf.get("SMTP_USER", "resend")
    password = conf.get("SMTP_PASS", "")
    from_addr = conf.get("SMTP_FROM", "audiobooks@localhost")

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(from_addr, to, msg.as_string())

    print(f"Email sent to {to}")


def main():
    _require_audiobooks_user()

    parser = argparse.ArgumentParser(description="Email translation report")
    parser.add_argument("--to", required=True, help="Recipient email")
    parser.add_argument("--report", required=True, help="Path to JSON report")
    args = parser.parse_args()

    conf = load_smtp_config()
    report_path = Path(args.report)
    body = build_report_body(report_path)

    # Determine subject based on results
    if report_path.exists():
        data = json.loads(report_path.read_text())
        fail_count = data.get("fail", 0)
        total = data.get("verified", 0)
        coverage = data.get("coverage_pct", 0)
        if fail_count == 0:
            subject = f"Translation Complete — {total} books verified, {coverage}% coverage"
        else:
            subject = f"Translation Report — {fail_count} failures, {total} verified"
    else:
        subject = "Translation Verification Report"

    send_email(args.to, subject, body, conf)


if __name__ == "__main__":
    main()
