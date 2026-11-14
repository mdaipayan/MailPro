"""Command-line tool:  mailmerge [options]

Safe by default: without --send nothing is sent, the tool only checks
every row and prints a preview.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import __version__
from .core import MergeError, load_template, read_database, render_message
from .sender import Sender, ServerConfig

SAMPLE_TEMPLATE = """\
From: Exam Cell KITS <your.name@gmail.com>
To: {{ name }} <{{ email }}>
Subject: Marks for {{ course }}

Dear {{ name }},

Your marks in {{ course }} are {{ marks }} out of 100.
{% if marks|int < 40 %}
Please meet your course teacher for the re-test schedule.
{% endif %}

Regards,
Exam Cell
"""

SAMPLE_DATABASE = """\
email,name,course,marks
student1@example.com,Asha Patil,Structural Analysis,78
student2@example.com,Rohit Meshram,Structural Analysis,35
"""

SAMPLE_CONFIG = """\
# SMTP server settings. The password is NOT stored here.
# Set it in the MAILMERGE_PASSWORD environment variable,
# or type it when asked.

[smtp_server]
# Gmail: create an App Password (Google Account > Security > App passwords)
host = smtp.gmail.com
port = 587
security = starttls
username = your.name@gmail.com

# Outlook / Office 365:
#   host = smtp.office365.com   port = 587   security = starttls
# SSL on port 465:
#   security = ssl   port = 465
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mailmerge",
        description="Send one personalised email per CSV row.",
    )
    p.add_argument("-t", "--template", default="mailmerge_template.txt",
                   help="email template (default: %(default)s)")
    p.add_argument("-d", "--database", default="mailmerge_database.csv",
                   help="CSV file, one row per email (default: %(default)s)")
    p.add_argument("-c", "--config", default="mailmerge_server.conf",
                   help="SMTP settings (default: %(default)s)")
    p.add_argument("--send", action="store_true",
                   help="really send. Without this, only a dry run.")
    p.add_argument("--limit", type=int, default=None,
                   help="stop after N emails")
    p.add_argument("--start", type=int, default=1,
                   help="start from row N (1 = first data row); use to resume")
    p.add_argument("--sleep", type=float, default=1.0,
                   help="seconds to wait between emails (default: %(default)s)")
    p.add_argument("--log", default="mailmerge_log.csv",
                   help="CSV log of sent / failed rows (default: %(default)s)")
    p.add_argument("--save-eml", metavar="DIR",
                   help="also save each email as a .eml file in DIR")
    p.add_argument("--show", type=int, default=1, metavar="N",
                   help="print the full text of the first N emails (default: 1)")
    p.add_argument("--sample", action="store_true",
                   help="create sample template, CSV and config here, then exit")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def write_samples(folder: Path) -> int:
    files = {
        "mailmerge_template.txt": SAMPLE_TEMPLATE,
        "mailmerge_database.csv": SAMPLE_DATABASE,
        "mailmerge_server.conf": SAMPLE_CONFIG,
    }
    for name, text in files.items():
        path = folder / name
        if path.exists():
            print(f"skip   {name} (already exists)")
        else:
            path.write_text(text, encoding="utf-8")
            print(f"create {name}")
    print("\nEdit these files, then run:  mailmerge          (dry run)")
    print("                            mailmerge --send   (send)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.sample:
        return write_samples(Path.cwd())

    try:
        return run(args)
    except MergeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped by user.", file=sys.stderr)
        return 130


def run(args: argparse.Namespace) -> int:
    template_path = Path(args.template)
    template = load_template(template_path)
    rows = list(read_database(args.database))
    if not rows:
        raise MergeError("CSV has no data rows.")
    if args.start < 1 or args.start > len(rows):
        raise MergeError(f"--start must be between 1 and {len(rows)}.")

    selected = list(enumerate(rows, start=1))[args.start - 1:]
    if args.limit is not None:
        selected = selected[: max(args.limit, 0)]

    # 1) Check EVERY selected row before sending anything.
    messages = []
    problems = []
    for num, row in selected:
        try:
            messages.append((num, *render_message(template, row, template_path.parent)))
        except MergeError as exc:
            problems.append(f"  row {num}: {exc}")
    if problems:
        print("Nothing was sent. Fix these rows first:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1

    # 2) Preview.
    for num, msg, rcpts in messages[: args.show]:
        print(f"{'=' * 20} row {num} {'=' * 20}")
        print(_preview(msg))
    if args.save_eml:
        out = Path(args.save_eml)
        out.mkdir(parents=True, exist_ok=True)
        for num, msg, _ in messages:
            (out / f"row_{num:04d}.eml").write_bytes(bytes(msg))
        print(f"Saved {len(messages)} .eml files in {out}/")

    if not args.send:
        print(f"\nDry run OK: {len(messages)} email(s) ready, none sent.")
        print("Add --send to send them.")
        return 0

    # 3) Send.
    config = ServerConfig.from_file(args.config)
    sent = failed = 0
    with Sender(config) as sender, _Log(args.log) as log:
        for i, (num, msg, rcpts) in enumerate(messages):
            try:
                sender.send(msg, rcpts)
                sent += 1
                log.write(num, rcpts, "sent", "")
                print(f"sent   row {num} -> {', '.join(rcpts)}")
            except Exception as exc:  # keep going, record the failure
                failed += 1
                log.write(num, rcpts, "failed", str(exc))
                print(f"FAILED row {num} -> {', '.join(rcpts)}: {exc}", file=sys.stderr)
            if args.sleep and i < len(messages) - 1:
                time.sleep(args.sleep)

    print(f"\nDone: {sent} sent, {failed} failed. Log: {args.log}")
    if failed:
        print("Re-run with --start <row> to retry from a row.", file=sys.stderr)
    return 0 if failed == 0 else 2


def _preview(msg) -> str:
    lines = [f"{k}: {v}" for k, v in msg.items()
             if k not in ("Content-Type", "Content-Transfer-Encoding", "MIME-Version")]
    body = msg.get_body(preferencelist=("plain", "html"))
    lines.append("")
    lines.append(body.get_content() if body is not None else "")
    names = [part.get_filename() for part in msg.iter_attachments()]
    if names:
        lines.append(f"[attachments: {', '.join(names)}]")
    return "\n".join(lines)


class _Log:
    def __init__(self, path: str):
        self.path = Path(path)

    def __enter__(self) -> "_Log":
        new = not self.path.exists()
        self.fh = self.path.open("a", newline="", encoding="utf-8")
        self.writer = csv.writer(self.fh)
        if new:
            self.writer.writerow(["time", "row", "recipients", "status", "error"])
        return self

    def write(self, row: int, rcpts: List[str], status: str, error: str) -> None:
        self.writer.writerow([datetime.now().isoformat(timespec="seconds"),
                              row, ";".join(rcpts), status, error])
        self.fh.flush()

    def __exit__(self, *exc) -> None:
        self.fh.close()
