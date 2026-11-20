"""MailPro: mail merge app for the Examination Section.

Run:  streamlit run app.py

The page has five tabs, used from left to right:
  1 · List           upload the CSV
  2 · Email          write on the left, live preview on the right
  3 · Attachments    common files, letters made from LaTeX, named and personal files
  4 · Check & test   check all rows, then send a test email to yourself
  5 · Send           send to everyone, download the log

Sending stays locked until tab 4 passes for the current list, email
and attachments.
"""

from __future__ import annotations

import csv
import base64
import hashlib
import hmac
import io
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import formataddr
from pathlib import Path

import streamlit as st

try:
    import mailmerge  # noqa: F401  (installed with pip)
except ImportError:  # running straight from the repo, e.g. Streamlit Cloud
    sys.path.insert(0, str(Path(__file__).parent / "src"))

from mailmerge.attachments import (assign_files, check_size, columns_naming_files,
                                   expand_uploads, pick_files)
from mailmerge.letters import (LetterSet, build_letters, fit_image, image_role,
                               load_library, missing_images, odd_letters)
from mailmerge.core import (ADDRESS_RE, MergeError, MessageTemplate, check_rows,
                            look_alike_report, parse_database)
from mailmerge.sender import Sender, ServerConfig
from mailmerge import __version__

PRESETS = {
    "Gmail": ("smtp.gmail.com", 587, "starttls"),
    "Outlook / Office 365": ("smtp.office365.com", 587, "starttls"),
    "Other": None,
}

SAMPLE_CSV = (
    b"email,name,course,marks\n"
    b"student1@example.com,Asha Patil,Structural Analysis,78\n"
    b"student2@example.com,Rohit Meshram,Structural Analysis,35\n"
)
SAMPLE_SUBJECT = "Marks for {{ course }}"
SAMPLE_BODY = """\
Dear {{ name }},

Your marks in {{ course }} are {{ marks }} out of 100.
{% if marks|int < 40 %}
Please meet your course teacher for the re-test schedule.
{% endif %}

Regards,
Exam Cell
"""
# The letter library: one sub-folder per letter set (see its README.txt).
LIBRARY_DIR = Path(os.environ.get("MAILMERGE_TEMPLATES")
                   or Path(__file__).parent / "letter_templates")

DEFAULT_BODY = "Dear {{ name }},\n\nWrite your message here.\n\nRegards,\n"

APP_NAME = "MailPro"
DEVELOPER = "Daipayan Mandal"
APP_LABEL = f"{APP_NAME} v{__version__}"
IST = timezone(timedelta(hours=5, minutes=30), "IST")   # the server clock is UTC


def now_ist() -> datetime:
    return datetime.now(IST)


def stamp(moment: datetime = None) -> str:
    """'15 Nov 2026, 06:16:05 IST'"""
    return (moment or now_ist()).strftime("%d %b %Y, %H:%M:%S IST")


st.set_page_config(page_title=APP_NAME, page_icon="✉️", layout="wide")
ss = st.session_state


# ------------------------------------------------------------ helpers ----
def secret(name: str, default=None):
    """Read one value from Streamlit secrets; never fails if there are none."""
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def on_cloud() -> bool:
    """True on Streamlit Community Cloud (or when a password is made compulsory)."""
    return (str(Path(__file__).resolve()).startswith("/mount/src")
            or os.environ.get("MAILMERGE_REQUIRE_PASSWORD") == "1")


def gate() -> None:
    """App password. Set MAILMERGE_APP_PASSWORD, or app_password in Streamlit secrets.

    On a PC the password is optional. On the cloud the link is public, so the
    app refuses to open at all until a password has been set.
    """
    need = os.environ.get("MAILMERGE_APP_PASSWORD") or secret("app_password")
    if need is not None and not isinstance(need, str) and hasattr(need, "keys"):
        # Secrets written as a group:  [app_password]  app_password = "..."
        values = [need[k] for k in need.keys()]
        need = need.get("app_password") or next((v for v in values if isinstance(v, str)), None)
    need = str(need).strip() if need else None
    if not need:
        if on_cloud():
            st.error("This app is locked: no app password has been set. "
                     "The owner must add `app_password` in the app's Secrets.")
            st.stop()
        return
    if ss.get("unlocked"):
        return
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.subheader(f"✉️ {APP_NAME}")
        st.caption("For the Examination Section only.")
        st.caption(f"{APP_LABEL} · Developed by {DEVELOPER}")
        pw = st.text_input("App password", type="password", key="gate_password")
        if st.button("Open", type="primary", key="gate_btn"):
            if hmac.compare_digest(pw.strip().encode("utf-8"), need.encode("utf-8")):
                ss["unlocked"] = True
                st.rerun()
            time.sleep(2)                 # slows down password guessing
            st.error("Wrong password.")
    st.stop()


def saved_senders() -> dict:
    """Sender addresses whose App Password is stored in Streamlit secrets.

    Secrets format (private, never in GitHub):
        [sender_passwords]
        "coe@kits.edu" = "abcd efgh ijkl mnop"
    Returns {address: password}. The passwords are never shown on the page.
    """
    out = {}
    try:
        table = st.secrets.get("sender_passwords", {})
        for addr in table:
            value = str(table[addr]).strip()
            if "@" in str(addr) and value:
                out[str(addr).strip().lower()] = value
    except Exception:
        pass
    return out


OTHER_SENDER = "Another address…"


def secret_images() -> dict:
    """Letterhead and signature kept in Streamlit secrets, never in GitHub.

    Secrets format:   [letter_images]
                      "signature.png" = "<base64 text>"
    """
    out = {}
    try:
        table = st.secrets.get("letter_images", {})
        for name in table:
            out[str(name)] = base64.b64decode(str(table[name]))
    except Exception:
        pass
    return out


def check_login(host, port, security, username, password) -> None:
    try:
        with Sender(ServerConfig(host, port, security, username or None),
                    password, allow_prompt=False) as sender:
            used = sender.config.port
        ss["login_note"] = ("success", "Login is OK. The app can send email from here"
                            + (f" (through port {used})." if used != port else "."))
    except Exception as exc:
        ss["login_note"] = ("error", str(exc))


def use_sample() -> None:
    ss["use_sample"] = True
    ss["to"] = "{{ name }} <{{ email }}>"
    ss["subject"] = SAMPLE_SUBJECT
    ss["body"] = SAMPLE_BODY


def choose_mail_type(library) -> None:
    """Mail type chosen at the top: pick its letters and its email text."""
    chosen = next((x for x in library if x.title == ss.get("mail_type")), None)
    ss["letter_set"] = chosen.title if chosen else "(none)"
    if chosen and chosen.email:
        ss["subject"], ss["body"] = chosen.email


def use_email_text(subject: str, body: str) -> None:
    ss["subject"], ss["body"] = subject, body


def insert_field(column: str) -> None:
    key = "subject" if ss.get("insert_into") == "Subject" else "body"
    ss[key] = ss.get(key, "") + "{{ " + column + " }}"


def step_preview(delta: int, last: int) -> None:
    ss["preview_row"] = min(max(ss.get("preview_row", 1) + delta, 1), last)


def signature(data: bytes, headers, body: str, extra=()) -> str:
    h = hashlib.sha1(data)
    h.update(repr(headers).encode("utf-8"))
    h.update(body.encode("utf-8"))
    h.update(repr(extra).encode("utf-8"))
    return h.hexdigest()


def fingerprint(files) -> list:
    """Name + content hash of each file, so a changed file is noticed."""
    return sorted((n, hashlib.sha1(d).hexdigest()) for n, d in files)


def size_text(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(n // 1024, 1)} KB"


def server_ready(host: str, username: str, password: str, from_addr: str) -> str:
    """Return an error text, or '' when the sender account is filled in."""
    if not host:
        return "Fill in the SMTP host in the left panel."
    if not from_addr:
        return "Fill in your email address in the left panel."
    if username and not password:
        return "Fill in the App Password in the left panel."
    return ""


def send_rows(tpl, rows, nums, cfg, password, pause, sig, files_for) -> None:
    """Send the given row numbers; record every result in session state."""
    if ss.get("results", {}).get("sig") != sig:
        ss["results"] = {"sig": sig, "rows": {}}
    done = ss["results"]["rows"]
    bar = st.progress(0.0, text="Connecting…")
    try:
        with Sender(cfg, password, allow_prompt=False) as sender:
            for i, num in enumerate(nums, start=1):
                rcpts = []
                try:
                    row = rows[num - 1]
                    msg, rcpts = tpl.render(row, files=files_for(row))
                    sender.send(msg, rcpts)
                    status, error = "sent", ""
                except Exception as exc:  # record it and go on with the next row
                    status, error = "failed", str(exc)
                done[num] = {
                    "row": num,
                    "recipients": "; ".join(rcpts),
                    "status": status,
                    "error": error,
                    "time": stamp(),
                    "app": APP_LABEL,
                }
                bar.progress(i / len(nums), text=f"{i} of {len(nums)}")
                if pause and i < len(nums):
                    time.sleep(pause)
    except MergeError as exc:
        ss["send_error"] = str(exc)


@st.cache_data(show_spinner="Making the letters…", max_entries=16)
def make_letters(tex_name: str, tex_bytes: bytes, csv_bytes: bytes, support: tuple) -> list:
    """One PDF per row. Cached, so the letters are made again only when the
    template, the list or a support file changes."""
    return build_letters(tex_name, tex_bytes, csv_bytes, support)


@st.cache_data(show_spinner=False, max_entries=16)
def letters_without_image(pdfs: list) -> list:
    return odd_letters(pdfs)


def letter_problem(attach_name: str, pdfs: list) -> str:
    """'' if all letters look alike, else a text naming the odd rows."""
    odd = letters_without_image(pdfs)
    if not odd:
        return ""
    return (f"{attach_name}: the letter of row {', '.join(map(str, odd[:8]))}"
            + (" …" if len(odd) > 8 else "")
            + " has fewer images than the others (letterhead or signature missing). "
              "Fix the template before sending.")


def log_csv(results: dict) -> bytes:
    """The send log. Every line carries the app name and the time (IST)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["row", "recipients", "status", "error", "time", "app"])
    for num in sorted(results):
        r = results[num]
        writer.writerow([r["row"], r["recipients"], r["status"], r["error"],
                         r["time"], r.get("app", APP_LABEL)])
    writer.writerow([])
    writer.writerow([f"Log made by {APP_LABEL} on {stamp()}",
                     f"Developed by {DEVELOPER}"])
    return buf.getvalue().encode("utf-8-sig")


# --------------------------------------------------------------- page ----
gate()

st.title(f"✉️ {APP_NAME}")
st.caption("One personal email for every row of your list. "
           "Go through the tabs from left to right. Nothing is sent until the last tab.")
st.caption(f"**{APP_LABEL}** · Developed by **{DEVELOPER}** · {stamp()}")

OTHER_MAIL = "Other mail"
library, shared_images = load_library(LIBRARY_DIR)
mail_type = None
if library:
    mail_type = st.radio(
        "**What are you sending?**", [x.title for x in library] + [OTHER_MAIL],
        index=None, horizontal=True, key="mail_type",
        on_change=choose_mail_type, args=(library,),
        help="Choosing a mail type sets its letters (tab 3) and its email text (tab 2). "
             "You can still change both.")
    if mail_type is None:
        st.caption("Choose the mail type first. Then upload the list in tab 1.")
    elif mail_type != OTHER_MAIL:
        st.caption(f"✔ Letters and email text for **{mail_type}** are set. "
                   "Upload its list in tab 1.")
mail_set = next((x for x in library if x.title == mail_type), None)
status = st.empty()                      # progress line, filled at the end

with st.sidebar:
    st.header("Sender account")
    provider = st.selectbox("Email service", list(PRESETS), key="provider")
    if PRESETS[provider]:
        host, port, security = PRESETS[provider]
    else:
        host = st.text_input("SMTP host", key="host").strip()
        port = int(st.number_input("Port", 1, 65535, 587, key="port"))
        security = st.selectbox("Security", ["starttls", "ssl", "none"], key="security")
    senders = saved_senders()
    choice = OTHER_SENDER
    if senders:
        choice = st.selectbox(
            "Send from", list(senders) + [OTHER_SENDER], key="sender_choice",
            help="The App Password of these addresses is stored in the app's private "
                 "Secrets, so you do not type it.")
    if choice != OTHER_SENDER:
        username = choice
        password = senders[choice]
        st.caption("🔑 App Password: saved for this address.")
    else:
        username = st.text_input("Your email address (login)", key="username").strip()
        saved = senders.get(username.lower(), "")
        password = st.text_input(
            "App Password", type="password", key="password",
            placeholder="saved for this address" if saved else "",
            help="Gmail: Google Account → Security → App passwords. "
                 "Your normal Gmail password will not work.",
        ) or saved or os.environ.get("MAILMERGE_PASSWORD", "")
    if PRESETS[provider]:
        from_addr = username
    else:
        from_addr = st.text_input("From address (if different from login)",
                                  key="from_addr").strip() or username
    st.caption("🔒 The password is kept only while this page is open. It is never saved.")
    if st.button("Check login", key="login_btn", disabled=not (host and (password or not username)),
                 help="Tries to connect and log in. No email is sent."):
        check_login(host, port, security, username, password)
    if ss.get("login_note"):
        kind, text = ss["login_note"]
        (st.success if kind == "success" else st.error)(text)
    st.divider()
    pause = st.number_input("Pause between emails (seconds)", 0.0, 60.0, 1.0, 0.5, key="pause")
    st.caption("Gmail allows about 500 emails per day from a normal account.")

TAB_NAMES = ["1 · List", "2 · Email", "3 · Attachments", "4 · Check & test", "5 · Send"]
tab_list, tab_email, tab_files, tab_check, tab_send = st.tabs(TAB_NAMES)


def stop_here(message: str, *later_tabs) -> None:
    """Stop the page, but leave a hint in the tabs that cannot be shown yet."""
    for tab in later_tabs:
        tab.info(message)
    st.stop()


# ---- tab 1: list ---------------------------------------------------------
with tab_list:
    if mail_set and mail_set.list_template:
        st.download_button(
            f"⬇ Download the blank list for {mail_set.title} (CSV)",
            mail_set.list_template,
            file_name=f"{mail_set.title.replace(' ', '_')}_list.csv", mime="text/csv",
            key="list_template_btn",
            help="Open it in Excel, replace the example row with your people, "
                 "and save it as CSV UTF-8. Keep the first row as it is.")
    u1, u2 = st.columns([3, 1], vertical_alignment="bottom")
    uploaded = u1.file_uploader(
        "CSV file: first row = column names, one row per person",
        type=["csv", "txt"], key="upload",
        on_change=lambda: ss.update(use_sample=False),
    )
    u2.button("Use sample data", key="sample_btn", on_click=use_sample,
              use_container_width=True)

    later = (tab_email, tab_files, tab_check, tab_send)
    if ss.get("use_sample"):
        data = SAMPLE_CSV
    elif uploaded is not None:
        data = uploaded.getvalue()
    else:
        st.info("Upload a CSV file to start. In Excel: File → Save As → CSV UTF-8.")
        stop_here("First upload the list in tab 1.", *later)

    try:
        rows = parse_database(data)
    except MergeError as exc:
        st.error(str(exc))
        stop_here("First fix the list in tab 1.", *later)
    if not rows:
        st.error("The CSV has column names but no data rows.")
        stop_here("First fix the list in tab 1.", *later)

    columns = list(rows[0])
    st.dataframe([{"row": i, **r} for i, r in enumerate(rows, start=1)], hide_index=True)
    st.caption(f"**{len(rows)} rows** · fields: " + ", ".join(f"`{c}`" for c in columns))
    if mail_set:
        missing = [c for c in mail_set.needed_columns() if c not in columns]
        if missing:
            st.error(
                f"This list does not fit **{mail_set.title}**. It has no column "
                + ", ".join(f"`{c}`" for c in missing)
                + ". Use the blank list above, or choose another mail type.")
    fixed = look_alike_report(data)
    if fixed:
        cells = sorted({(n, c) for n, c, _, _ in fixed})
        st.warning(
            f"{len(cells)} cell(s) had a letter from another alphabet or an invisible "
            "character, which looks right but breaks the letters and file names. "
            "The app has corrected them for this send. Please also correct your CSV file.")
        with st.expander("Show the corrected cells"):
            st.dataframe([{"row": n, "column": c, "value in the file": v, "correction": ch}
                          for n, c, v, ch in fixed], hide_index=True)

    email_col = next((c for c in columns if "mail" in c.lower()), None)
    name_col = next((c for c in columns if "name" in c.lower()), None)
    if not ss.get("to"):
        if email_col and name_col:
            ss["to"] = "{{ %s }} <{{ %s }}>" % (name_col, email_col)
        elif email_col:
            ss["to"] = "{{ %s }}" % email_col
    if not email_col:
        st.warning("No column with 'email' in its name was found. "
                   "Put the right field in the **To** box in tab 2.")

# ---- tab 2: email (write on the left, preview on the right) --------------
ss.setdefault("from_name", "")
ss.setdefault("subject", "")
ss.setdefault("body", DEFAULT_BODY if name_col == "name" else "Dear student,\n\n\n\nRegards,\n")

with tab_email:
    write_col, preview_col = st.columns(2, gap="large")

with write_col:
    st.markdown("##### Write")
    c1, c2 = st.columns(2)
    from_name = c1.text_input("Sender name", key="from_name", placeholder="Exam Cell, KITS Ramtek")
    to = c2.text_input("To", key="to")
    cc = c1.text_input("Cc (optional)", key="cc", placeholder="hod@college.edu")
    bcc = c2.text_input("Bcc (optional, hidden copy)", key="bcc")
    subject = st.text_input("Subject", key="subject")
    body = st.text_area("Body", key="body", height=300)

    st.radio("Insert a field at the end of:", ["Body", "Subject"], key="insert_into",
             horizontal=True)
    per_line = 4
    for start in range(0, len(columns), per_line):
        for slot, col in zip(st.columns(per_line), columns[start:start + per_line]):
            slot.button(col, key=f"ins_{col}", on_click=insert_field, args=(col,),
                        use_container_width=True)
    with st.expander("How fields and conditions work"):
        st.markdown(
            "- `{{ name }}` is replaced by the value in the **name** column of each row.\n"
            "- You can also type a field by hand anywhere in To, Cc, Subject or Body.\n"
            "- Different text for different rows:\n"
        )
        st.code("{% if marks|int < 40 %}\nPlease meet your teacher.\n{% else %}\n"
                "Well done.\n{% endif %}", language="jinja2")

headers = [
    ("From", formataddr((from_name.strip(), from_addr)) if from_addr else ""),
    ("To", to), ("Cc", cc), ("Bcc", bcc), ("Subject", subject),
]

# ---- tab 3: attachments (four kinds, added together) ---------------------
common, pool, personal = [], {}, {}
letters, letter_errors, letter_print = [], [], []   # letters = [(attachment name, [pdf per row])]
name_cols, key_cols = [], []
named_required, file_required, per_person = False, True, 0
assigned = [[] for _ in rows]      # personal file names per row (kind D)
clashes = {}                       # file name -> row numbers, if it fits several rows

with tab_files:
    st.caption("Four kinds of attachment. All are optional, and each email gets the sum of them.")
    src_same, src_letters, src_named, src_id = st.tabs([
        "A · Same for everyone", "B · Letters",
        "C · Files named in the list", "D · Personal files, by ID"])

    with src_same:
        common_up = st.file_uploader(
            "Files that every person gets, for example the guidelines",
            accept_multiple_files=True, key="common_files")
        common = [(f.name, f.getvalue()) for f in common_up or []]

    with src_letters:
        private_images = secret_images()          # from Streamlit secrets, not from GitHub
        shared_images = tuple(sorted({**dict(shared_images), **private_images}.items()))
        support = tuple(shared_images)
        chosen = None
        if library:
            b1, b2 = st.columns([1, 2], gap="large")
            with b1:
                title = st.selectbox(
                    "Letter set", ["(none)"] + [s.title for s in library], key="letter_set",
                    help="The letters are kept in the letter library folder of the app. "
                         "You need only the list from tab 1.")
                chosen = next((s for s in library if s.title == title), None)
                if chosen and chosen.email:
                    st.button("Use this set's email text in tab 2", key="use_email_btn",
                              on_click=use_email_text, args=chosen.email)
            with b2:
                if chosen is None:
                    st.info("Choose a letter set. The app then makes each person's letters "
                            "from the list, and you upload nothing here.")
                else:
                    set_images = {**dict(chosen.support), **private_images}
                    lacking = sorted({m for _, t in chosen.letters
                                      for m in missing_images(t, set_images)})
                    if lacking:
                        st.markdown("**Upload the letterhead and signature** "
                                    "(needed once each time you open the app)")
                        for slot, need in zip(st.columns(len(lacking)), lacking):
                            pic = slot.file_uploader(
                                image_role(need), type=["png", "jpg", "jpeg"],
                                key=f"img_{need}",
                                help=f"Any file name is fine. The letters use it as {need}. "
                                     "It is kept only while this page is open.")
                            if pic is not None:
                                try:
                                    set_images[need] = fit_image(need, pic.getvalue())
                                except MergeError as exc:
                                    slot.error(str(exc))
                    set_support = tuple(sorted(set_images.items()))
                    st.markdown("**Letters in this set** (untick one to leave it out)")
                    for tex_name, tex_bytes in chosen.letters:
                        attach_name = LetterSet.attachment_name(tex_name)
                        t1, t2 = st.columns([1, 2], vertical_alignment="center")
                        if not t1.checkbox(attach_name, True,
                                           key=f"lib_{chosen.key}_{tex_name}"):
                            continue
                        letter_print.append((chosen.key + "/" + tex_name,
                                             hashlib.sha1(tex_bytes).hexdigest(), attach_name))
                        still = missing_images(tex_bytes, set_images)
                        if still:
                            text = ("needs the image " + ", ".join(still) + " ("
                                    + " and ".join(image_role(n).lower() for n in still)
                                    + "). Upload it in tab 3 → B · Letters. The letter is not made without it.")
                            letter_errors.append(f"{attach_name} {text}")
                            t2.warning(f"This letter {text}")
                            continue
                        try:
                            pdfs = make_letters(tex_name, tex_bytes, data, set_support)
                            letters.append((attach_name, pdfs))
                            bad = letter_problem(attach_name, pdfs)
                            if bad:
                                letter_errors.append(bad)
                                t2.error(bad)
                            else:
                                t2.success(f"{len(pdfs)} letters made, one for each row.")
                        except MergeError as exc:
                            letter_errors.append(str(exc))
                            t2.error(str(exc))
                    support = set_support
        else:
            st.caption(f"No letter library was found in `{LIBRARY_DIR}`. "
                       "You can still upload templates below.")

        with st.expander("Use other templates (upload .tex files)"):
            st.caption(
                "Upload LaTeX mail-merge templates (the ones with `\\csvreader`). The CSV "
                "file name written inside the template does not matter. The images of "
                "the letter library (letterhead, signature) are available to them.")
            l1, l2 = st.columns(2, gap="large")
            tex_up = l1.file_uploader("Templates (.tex), one letter per row",
                                      type=["tex"], accept_multiple_files=True, key="tex_files")
            support_up = l2.file_uploader(
                "Extra files the templates need (images)",
                accept_multiple_files=True, key="tex_support")
            merged = dict(shared_images)
            merged.update({f.name: f.getvalue() for f in support_up or []})
            up_support = tuple(sorted(merged.items()))
            if tex_up:
                support = tuple(sorted({**dict(support), **merged}.items()))
            for f in tex_up or []:
                n1, n2 = st.columns([1, 2], vertical_alignment="bottom")
                attach_name = n1.text_input(
                    f"Attachment name for {f.name}", value=LetterSet.attachment_name(f.name),
                    key=f"tex_name_{f.name}").strip() or LetterSet.attachment_name(f.name)
                if not attach_name.lower().endswith(".pdf"):
                    attach_name += ".pdf"
                letter_print.append((f.name, hashlib.sha1(f.getvalue()).hexdigest(), attach_name))
                try:
                    pdfs = make_letters(f.name, f.getvalue(), data, up_support)
                    letters.append((attach_name, pdfs))
                    bad = letter_problem(attach_name, pdfs)
                    if bad:
                        letter_errors.append(bad)
                        n2.error(bad)
                    else:
                        n2.success(f"{attach_name}: {len(pdfs)} letters made, one for each row.")
                except MergeError as exc:
                    letter_errors.append(str(exc))
                    n2.error(str(exc))
        names = [n.lower() for n, _ in letters]
        if len(names) != len(set(names)):
            letter_errors.append("two letters have the same attachment name")
            st.error("Two letters have the same attachment name. Give each a different name.")

    with src_named:
        st.caption(
            "For files that several people share, for example one syllabus per course. "
            "The list holds the file name (with or without the extension).")
        pool = dict(chosen.files) if chosen else {}
        c1, c2 = st.columns(2, gap="large")
        with c1:
            if pool:
                st.success(f"{len(pool)} file(s) come from the letter library "
                           f"(folder `{chosen.key}/files`).")
            elif chosen:
                st.caption(f"Tip: put these files in `letter_templates/{chosen.key}/files` "
                           "once, and you never upload them again.")
            pool_up = st.file_uploader("Upload files (many files, or one ZIP)",
                                       accept_multiple_files=True, key="pool_files")
        if pool_up:
            try:
                pool.update(expand_uploads((f.name, f.getvalue()) for f in pool_up))
            except MergeError as exc:
                st.error(str(exc))
                stop_here("First fix the attachments in tab 3.", tab_check, tab_send)
        with c2:
            found = columns_naming_files(rows, columns, pool) if pool else []
            name_cols = st.multiselect(
                "Columns of the list that hold a file name", columns, default=found,
                key=f"name_cols_{chosen.key if chosen else ''}_{len(pool)}",
                help="The app selects the columns whose values match the available "
                     "files. For two files in one cell, separate the names with ;")
            if found:
                st.caption("Selected automatically: every value in "
                           + ", ".join(f"`{c}`" for c in found) + " matches a file.")
            named_required = st.checkbox("An empty cell is a problem", False,
                                         key="named_required")
        if name_cols and not pool:
            st.info("Now upload the files that these columns name.")

    with src_id:
        st.caption(
            "For ready-made files that belong to one person. Each file name must contain "
            "that person's ID or code from the list.")
        d1, d2 = st.columns(2, gap="large")
        personal_up = d1.file_uploader("Personal files (many files, or one ZIP)",
                                       accept_multiple_files=True, key="personal_files")
        if personal_up:
            try:
                personal = expand_uploads((f.name, f.getvalue()) for f in personal_up)
            except MergeError as exc:
                st.error(str(exc))
                stop_here("First fix the attachments in tab 3.", tab_check, tab_send)
            with d2:
                guess = [c for c in columns
                         if c.lower() == "id" or c.lower().endswith("_id")][:1]
                key_cols = st.multiselect(
                    "The file name must contain the values of ALL these columns",
                    columns, default=guess, key="key_cols",
                    help="Choose columns that together are unique for one row, for "
                         "example the person's ID and the course code.")
                a1, a2 = st.columns(2, vertical_alignment="bottom")
                per_person = int(a1.number_input(
                    "Files per person (0 = any)", 0, 20, 0, key="per_person",
                    help="Example: 3 for appointment letter + bill form + undertaking."))
                file_required = a2.checkbox("Personal file is compulsory", True,
                                            key="file_required")
            if key_cols:
                example = "_".join(rows[0].get(c, "") for c in key_cols).replace(" ", "_")
                st.caption(
                    f"{len(personal)} files uploaded. Row 1 gets every file whose name "
                    "contains "
                    + " and ".join(f"`{rows[0].get(c, '')}`" for c in key_cols)
                    + f", for example `Bill_{example}.pdf`. "
                    "Whole words only: `T01` does not match `T014`.")
                assigned, clashes = assign_files(rows, personal, key_cols)
            else:
                st.info("Choose at least one column that identifies the person.")
            if clashes:
                st.error(
                    f"{len(clashes)} file(s) fit more than one row, so it is not clear "
                    "whose they are. Add another column (for example the course code), "
                    "or rename the files: "
                    + "; ".join(f"{n} → rows {', '.join(map(str, nums))}"
                                for n, nums in sorted(clashes.items())[:5]))

row_index = {id(r): i for i, r in enumerate(rows)}


def files_for(row):
    """All attachments of one row. Raises MergeError if something is wrong."""
    i = row_index[id(row)]
    out = list(common)
    if letter_errors:
        raise MergeError("tab 3 B: " + letter_errors[0])
    out += [(name, pdfs[i]) for name, pdfs in letters]
    for col in name_cols:
        out += pick_files(row.get(col, ""), pool, required=named_required)
    if personal:
        if not key_cols:
            raise MergeError("tab 3 D: choose the column(s) that identify each person's files")
        empty = [c for c in key_cols if not row.get(c)]
        if empty:
            raise MergeError("empty value in: " + ", ".join(empty))
        names = assigned[i]
        for n in names:
            if n in clashes:
                raise MergeError(f"file {n} also fits row(s) "
                                 + ", ".join(map(str, clashes[n])))
        if per_person and len(names) != per_person:
            raise MergeError(f"expected {per_person} personal file(s), found {len(names)}"
                             + (": " + ", ".join(names) if names else ""))
        if not names and file_required:
            raise MergeError("no personal file found for "
                             + " + ".join(row[c] for c in key_cols))
        out += [(n, personal[n]) for n in names]
    seen, unique = set(), []
    for name, content in out:             # the same file twice is sent once
        if name.lower() not in seen:
            seen.add(name.lower())
            unique.append((name, content))
    check_size(unique)
    return unique


with tab_files:
    if common or letters or letter_errors or name_cols or personal:
        st.markdown("##### Who gets which file")
        table, used = [], set()
        who = name_col or email_col or columns[0]
        for i, r in enumerate(rows, start=1):
            try:
                got = [n for n, _ in files_for(r)]
                used.update(got)
                cell = ", ".join(got) or "(none)"
            except MergeError as exc:
                got, cell = [], f"⚠ {exc}"
            table.append({"row": i, who: r.get(who, ""), "files": len(got), "attachments": cell})
        unused = sorted((set(pool) | set(personal)) - used)
        if unused:
            st.warning(f"{len(unused)} uploaded file(s) are used by no row and will not "
                       "be sent: " + ", ".join(unused[:10]) + (" …" if len(unused) > 10 else ""))
        st.dataframe(table, hide_index=True, height=min(38 * (len(table) + 1) + 3, 300))

sig = signature(data, headers, body,
                (fingerprint(common), fingerprint(pool.items()), fingerprint(personal.items()),
                 letter_print, fingerprint(support), name_cols, named_required,
                 key_cols, file_required, per_person))

blocker = server_ready(host, username, password, from_addr)
if not subject.strip():
    blocker = blocker or "The Subject is empty (tab 2)."
if not body.strip():
    blocker = blocker or "The Body is empty (tab 2)."
try:
    template = MessageTemplate(headers, body)
except MergeError as exc:
    write_col.error(str(exc))
    stop_here("First fix the email in tab 2.", tab_check, tab_send)

# ---- tab 2, right side: preview (needs the attachments, so it comes here) --
with preview_col:
    st.markdown("##### Preview")
    ss["preview_row"] = min(max(int(ss.get("preview_row", 1)), 1), len(rows))
    p1, p2, p3 = st.columns([1, 1, 1])
    p1.button("◀ Previous", on_click=step_preview, args=(-1, len(rows)),
              use_container_width=True)
    p2.number_input("Row", 1, len(rows), key="preview_row", label_visibility="collapsed")
    p3.button("Next ▶", on_click=step_preview, args=(1, len(rows)), use_container_width=True)

    preview = None
    if not from_addr:
        st.info("Fill in your email address in the left panel to see the preview.")
    else:
        try:
            _row = rows[ss["preview_row"] - 1]
            preview = template.render(_row, files=files_for(_row))
        except MergeError as exc:
            st.error(f"Row {ss['preview_row']}: {exc}")
    if preview:
        msg, rcpts = preview
        with st.container(border=True):
            st.markdown(f"**From:** {msg['From']}  \n**To:** {msg['To']}"
                        + (f"  \n**Cc:** {msg['Cc']}" if msg["Cc"] else "")
                        + f"  \n**Subject:** {msg['Subject'] or '(empty)'}")
            hidden = [a for a in rcpts if a not in str(msg["To"]) + str(msg["Cc"] or "")]
            if hidden:
                st.caption("Bcc (hidden): " + ", ".join(hidden))
            st.divider()
            st.text(msg.get_body(preferencelist=("plain",)).get_content())
            parts = list(msg.iter_attachments())
            if parts:
                st.caption("Attachments of this row (click to open and read):")
            for k, part in enumerate(parts):
                content = part.get_payload(decode=True) or b""
                st.download_button(
                    f"📎 {part.get_filename()} ({size_text(len(content))})", content,
                    file_name=part.get_filename(), key=f"att_{k}", on_click="ignore")

# ---- tab 4: check (left) and test email (right) --------------------------
with tab_check:
    check_col, test_col = st.columns(2, gap="large")

with check_col:
    st.markdown("##### Check all rows")
    if blocker:
        st.warning(blocker)
    if st.button("Check all rows", key="check_btn", disabled=bool(blocker)):
        ready, problems, warnings = check_rows(template, rows, files_for=files_for)
        ss["check"] = {"sig": sig, "problems": problems, "warnings": warnings}

    check = ss.get("check") or {}
    checked = check.get("sig") == sig
    check_ok = checked and not check["problems"] and not blocker
    if not checked:
        if check:
            st.info("The list, the email or the attachments changed. Please check again.")
    elif check["problems"]:
        st.error(f"{len(check['problems'])} row(s) have a problem. Fix them and check again.")
        st.dataframe([{"row": n, "problem": t} for n, t in check["problems"]], hide_index=True)
    else:
        st.success(f"All {len(rows)} rows are OK.")
    if checked and check["warnings"]:
        with st.expander(f"{len(check['warnings'])} warning(s): please look, "
                         "but you can still send"):
            st.dataframe([{"row": n, "warning": t} for n, t in check["warnings"]],
                         hide_index=True)

with test_col:
    st.markdown("##### Send a test email to yourself")
    st.caption(f"You get the email of **row {ss['preview_row']}** (the row shown in the "
               "preview), with its attachments. It goes **only to the address below**.")
    test_to = st.text_input("Send the test to", value=from_addr, key="test_to").strip()
    if st.button("Send test email", key="test_btn", disabled=not (check_ok and preview)):
        if not ADDRESS_RE.match(test_to):
            ss["test_note"] = ("error", "Type a valid email address for the test.")
        else:
            try:
                _row = rows[ss["preview_row"] - 1]
                msg, _ = template.render(_row, files=files_for(_row))
                old = msg["Subject"]
                del msg["Subject"]
                msg["Subject"] = f"[TEST] {old}"
                with Sender(ServerConfig(host, port, security, username or None),
                            password, allow_prompt=False) as sender:
                    sender.send(msg, [test_to])
                ss["tested_sig"] = sig
                ss["test_note"] = ("success",
                                   f"Test sent to {test_to}. Open your inbox and read it.")
            except Exception as exc:
                ss["test_note"] = ("error", str(exc))
    tested = ss.get("tested_sig") == sig
    if not check_ok:
        st.caption("Locked until all rows are checked and OK.")
    if ss.get("test_note"):
        kind, text = ss["test_note"]
        if kind == "error":
            st.error(text)
        elif tested:
            st.success(text)

# ---- tab 5: send ---------------------------------------------------------
results = ss.get("results") or {}
same_run = results.get("sig") == sig
done = results.get("rows", {}) if same_run else {}
pending = [n for n in range(1, len(rows) + 1) if done.get(n, {}).get("status") != "sent"]

with tab_send:
    send_col, log_col = st.columns(2, gap="large")

with send_col:
    st.markdown("##### Send to everyone")
    st.markdown(f"{'✅' if check_ok else '⬜'} All rows checked (tab 4)  \n"
                f"{'✅' if tested else '⬜'} Test email sent and read (tab 4)")
    if ss.get("send_error"):
        st.error(ss.pop("send_error"))

    if done and not pending:
        last = max(done.values(), key=lambda r: r["row"])["time"]
        st.success(f"Finished: all {len(rows)} emails were sent by {APP_NAME}. "
                   f"Last one at {last}.")
    else:
        label = (f"Retry {len(pending)} failed or unsent row(s)" if done
                 else f"Send {len(pending)} email(s)")
        if ss.pop("reset_confirm", False):
            ss["confirm"] = False
        confirm = st.checkbox(
            f"Yes, send {len(pending)} email(s) now. I know this cannot be undone.",
            key="confirm", disabled=not (check_ok and tested))
        if st.button(label, key="send_btn", type="primary",
                     disabled=not (check_ok and tested and confirm)):
            st.warning("Sending… do not close this page or click anything until it finishes.")
            send_rows(template, rows, pending,
                      ServerConfig(host, port, security, username or None),
                      password, pause, sig, files_for)
            ss["reset_confirm"] = True
            st.rerun()

with log_col:
    st.markdown("##### Log")
    if not results.get("rows"):
        st.caption("Nothing has been sent yet.")
    else:
        shown = results["rows"]
        if not same_run:
            st.caption("Log of the previous send (the list or the email has changed since then):")
        sent = sum(1 for r in shown.values() if r["status"] == "sent")
        m1, m2 = st.columns(2)
        m1.metric("Sent", sent)
        m2.metric("Failed", len(shown) - sent)
        st.dataframe([shown[n] for n in sorted(shown)], hide_index=True)
        st.download_button("Download log (CSV)", log_csv(shown),
                           file_name=f"{APP_NAME}_log_{now_ist():%Y-%m-%d_%H%M}.csv",
                           mime="text/csv", key="log_btn")

# ---- progress line under the title ---------------------------------------
sent_now = sum(1 for r in done.values() if r["status"] == "sent")
n_files = len(common) + len(pool) + len(personal)
status.markdown(
    f"✅ List: **{len(rows)} rows** &nbsp;·&nbsp; "
    f"📎 Files: **{n_files}**, letters: **{len(letters)}** &nbsp;·&nbsp; "
    f"{'✅' if check_ok else '⬜'} Checked &nbsp;·&nbsp; "
    f"{'✅' if tested else '⬜'} Test email &nbsp;·&nbsp; "
    f"{'✅' if done and not pending else '⬜'} Sent: **{sent_now} / {len(rows)}**")
