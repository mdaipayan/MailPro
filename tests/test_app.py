"""End-to-end tests of the Streamlit app with a fake mail server."""

import smtplib
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from mailmerge import MessageTemplate, check_rows, parse_database  # noqa: E402

APP = str(Path(__file__).parent.parent / "app.py")


class FakeSMTP:
    sent = []
    files = []
    refuse = set()

    def __init__(self, host, port, timeout=None):
        pass

    def noop(self):
        return (250, b"ok")

    def login(self, user, password):
        pass

    def send_message(self, msg, to_addrs=None):
        if to_addrs[0] in FakeSMTP.refuse:
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (550, b"no such user")})
        FakeSMTP.sent.append((msg["Subject"], list(to_addrs),
                              msg.get_body(preferencelist=("plain",)).get_content()))
        FakeSMTP.files.append([a.get_filename() for a in msg.iter_attachments()])

    def quit(self):
        pass


LIB_TEX = r"""\documentclass{article}
\usepackage{csvsimple}
\begin{document}
\csvreader[head to column names]{whatever.csv}{}{\clearpage
Result of \name: \marks\ in \course. FIELD}
\end{document}
"""


def make_library(root):
    """A small letter library that fits the app's sample data."""
    good = root / "result_letters"
    (good / "files").mkdir(parents=True)
    (good / "Result_Letter.tex").write_text(LIB_TEX.replace("FIELD", ""))
    (good / "email.txt").write_text("Subject: Result of {{ name }}\n\nDear {{ name }},\nSee the letter.\n")
    (good / "files" / "Structural Analysis.pdf").write_bytes(b"%PDF-1.4 syllabus")
    bad = root / "wrong_list"
    bad.mkdir()
    (bad / "Letter.tex").write_text(LIB_TEX.replace("FIELD", r"\rollnumber"))


needs_latex = pytest.mark.skipif(__import__("shutil").which("pdflatex") is None,
                                 reason="no pdflatex")


@pytest.fixture
def app(monkeypatch, tmp_path):
    make_library(tmp_path / "lib")
    monkeypatch.setenv("MAILMERGE_TEMPLATES", str(tmp_path / "lib"))
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.delenv("MAILMERGE_APP_PASSWORD", raising=False)
    FakeSMTP.sent, FakeSMTP.files, FakeSMTP.refuse = [], [], set()
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.selectbox(key="provider").select("Other").run()
    at.text_input(key="host").input("localhost")
    at.selectbox(key="security").select("none")
    at.text_input(key="from_addr").input("cell@kits.edu")
    at.number_input(key="pause").set_value(0.0)
    at.run()
    at.button(key="sample_btn").click().run()
    assert not at.exception
    return at


def test_send_is_locked_until_check_and_test(app):
    at = app
    assert at.button(key="send_btn").disabled
    assert at.button(key="test_btn").disabled

    at.button(key="check_btn").click().run()
    assert any("All 2 rows are OK" in s.value for s in at.success)
    assert at.button(key="send_btn").disabled          # test email still missing

    at.button(key="test_btn").click().run()
    assert FakeSMTP.sent[0][0] == "[TEST] Marks for Structural Analysis"
    assert FakeSMTP.sent[0][1] == ["cell@kits.edu"]    # only to me, not the student
    assert at.button(key="send_btn").disabled          # confirm box not ticked

    at.checkbox(key="confirm").check().run()
    assert not at.button(key="send_btn").disabled


def test_full_send_and_conditional_text(app):
    at = app
    at.button(key="check_btn").click().run()
    at.button(key="test_btn").click().run()
    at.checkbox(key="confirm").check().run()
    at.button(key="send_btn").click().run()
    assert not at.exception

    real = FakeSMTP.sent[1:]
    assert [r for _, r, _ in real] == [["student1@example.com"], ["student2@example.com"]]
    assert "re-test" not in real[0][2] and "re-test" in real[1][2]
    assert any("all 2 emails were sent by MailPro" in s.value and "IST" in s.value
               for s in at.success)
    assert any("MailPro v" in c.value and "Developed by **Daipayan Mandal**" in c.value
               and "IST" in c.value for c in at.caption)
    log = at.session_state["results"]["rows"]
    assert all(r["app"].startswith("MailPro v") and r["time"].endswith("IST") for r in log.values())
    assert not any(b.key == "send_btn" for b in at.button)   # cannot send twice


def test_editing_the_email_locks_send_again(app):
    at = app
    at.button(key="check_btn").click().run()
    at.button(key="test_btn").click().run()
    at.text_input(key="subject").input("New subject").run()
    assert at.button(key="test_btn").disabled
    assert at.button(key="send_btn").disabled


def test_failed_row_can_be_retried_without_resending_others(app):
    at = app
    FakeSMTP.refuse = {"student2@example.com"}
    at.button(key="check_btn").click().run()
    at.button(key="test_btn").click().run()
    at.checkbox(key="confirm").check().run()
    at.button(key="send_btn").click().run()
    assert at.button(key="send_btn").label == "Retry 1 failed or unsent row(s)"
    assert [m.value for m in at.metric] == ["1", "1"]

    FakeSMTP.refuse = set()
    at.checkbox(key="confirm").check().run()
    at.button(key="send_btn").click().run()
    to_students = [r[0] for _, r, _ in FakeSMTP.sent[1:]]
    assert to_students == ["student1@example.com", "student2@example.com"]


def test_unknown_field_blocks_sending(app):
    at = app
    at.text_area(key="body").input("Dear {{ nmae }}").run()
    at.button(key="check_btn").click().run()
    assert any("2 row(s) have a problem" in e.value for e in at.error)
    assert at.button(key="test_btn").disabled


def test_insert_field_button(app):
    at = app
    at.button(key="ins_course").click().run()
    assert at.text_area(key="body").value.endswith("{{ course }}")


# ------------------------------------------------ logic used by the app ----
def test_check_rows_problems_and_warnings():
    rows = parse_database(
        "Email ID,Student Name,marks\n"
        "a@x.com,Asha,70\n"
        "bad-address,Rohit,40\n"
        "a@x.com,,55\n"
    )
    assert list(rows[0]) == ["Email_ID", "Student_Name", "marks"]
    tpl = MessageTemplate(
        [("From", "cell@kits.edu"), ("To", "{{ Email_ID }}"), ("Subject", "Hi")],
        "Dear {{ Student_Name }}, marks {{ marks }}",
    )
    ready, problems, warnings = check_rows(tpl, rows)
    assert [n for n, *_ in ready] == [1, 3]
    assert problems[0][0] == 2 and "Bad email address" in problems[0][1]
    texts = [t for n, t in warnings if n == 3]
    assert any("Student_Name" in t for t in texts) and any("row 1" in t for t in texts)


def test_csv_value_cannot_inject_a_header():
    tpl = MessageTemplate(
        [("From", "cell@kits.edu"), ("To", "{{ email }}"), ("Subject", "Hi {{ name }}")],
        "Body")
    msg, rcpts = tpl.render({"email": "a@x.com", "name": "Asha\nBcc: thief@x.com"})
    assert rcpts == ["a@x.com"]
    assert msg["Subject"] == "Hi Asha Bcc: thief@x.com"


# ---------------------------------------------------- letter library ----
@needs_latex
def test_letters_from_the_library_need_only_the_csv(app):
    pytest.importorskip("pypdf")
    at = app
    at.selectbox(key="letter_set").select("Result letters").run()
    assert not at.exception
    assert any("2 letters made" in s.value for s in at.success)
    assert any("1 file(s) come from the letter library" in s.value for s in at.success)

    at.button(key="use_email_btn").click().run()
    assert at.text_input(key="subject").value == "Result of {{ name }}"

    at.button(key="check_btn").click().run()
    at.button(key="test_btn").click().run()
    at.checkbox(key="confirm").check().run()
    at.button(key="send_btn").click().run()
    # test email + 2 real emails: own letter + the syllabus named in the 'course' column
    assert FakeSMTP.files == [["Result_Letter.pdf", "Structural Analysis.pdf"]] * 3
    assert [s for s, _, _ in FakeSMTP.sent[1:]] == ["Result of Asha Patil",
                                                    "Result of Rohit Meshram"]


@needs_latex
def test_letter_set_that_does_not_fit_the_list_blocks_sending(app):
    at = app
    at.selectbox(key="letter_set").select("Wrong list").run()
    assert any("needs a column named 'rollnumber'" in e.value for e in at.error)
    at.button(key="check_btn").click().run()
    assert any("2 row(s) have a problem" in e.value for e in at.error)
    assert at.button(key="test_btn").disabled


# ------------------------------------------------- cloud safety ----
def _tiny_png() -> bytes:
    import struct, zlib
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))


def test_cloud_without_password_stays_locked(monkeypatch):
    monkeypatch.delenv("MAILMERGE_APP_PASSWORD", raising=False)
    monkeypatch.setenv("MAILMERGE_REQUIRE_PASSWORD", "1")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert any("no app password has been set" in e.value for e in at.error)
    assert not at.tabs and not at.sidebar.text_input


def test_password_gate(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("MAILMERGE_REQUIRE_PASSWORD", "1")
    at = AppTest.from_file(APP, default_timeout=30)
    at.secrets["app_password"] = "a-long-exam-cell-password"
    at.run()
    assert not at.tabs
    at.text_input(key="gate_password").input("guess").run()
    at.button(key="gate_btn").click().run()
    assert any("Wrong password" in e.value for e in at.error) and not at.tabs
    at.text_input(key="gate_password").input("a-long-exam-cell-password").run()
    at.button(key="gate_btn").click().run()
    assert at.tabs and not at.exception


SIGNED_TEX = LIB_TEX.replace(r"\usepackage{csvsimple}", r"\usepackage{csvsimple}\usepackage{graphicx}") \
                    .replace("FIELD", r"\includegraphics[width=1cm]{sign.png}")


@needs_latex
def test_letter_needs_its_image_from_secrets_not_from_the_repo(monkeypatch, tmp_path):
    pytest.importorskip("pypdf")
    lib = tmp_path / "lib" / "signed_letters"
    lib.mkdir(parents=True)
    (lib / "Letter.tex").write_text(SIGNED_TEX)          # the repo has NO sign.png
    monkeypatch.setenv("MAILMERGE_TEMPLATES", str(tmp_path / "lib"))
    monkeypatch.delenv("MAILMERGE_APP_PASSWORD", raising=False)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    def start(with_secret):
        at = AppTest.from_file(APP, default_timeout=30)
        if with_secret:
            import base64
            at.secrets["letter_images"] = {"sign.png": base64.b64encode(_tiny_png()).decode()}
        at.run()
        at.selectbox(key="provider").select("Other").run()
        at.text_input(key="host").input("localhost")
        at.selectbox(key="security").select("none")
        at.text_input(key="from_addr").input("cell@kits.edu")
        at.run()
        at.button(key="sample_btn").click().run()
        at.selectbox(key="letter_set").select("Signed letters").run()
        assert not at.exception
        return at

    at = start(with_secret=False)
    assert any("needs the image sign.png" in w.value for w in at.warning)
    at.button(key="check_btn").click().run()
    assert any("2 row(s) have a problem" in e.value for e in at.error)   # sending is blocked

    at = start(with_secret=True)
    assert any("2 letters made" in s.value for s in at.success)
    assert not any("needs the image" in w.value for w in at.warning)


def test_check_login_button(app):
    at = app
    at.sidebar.button(key="login_btn").click().run()
    assert any("Login is OK" in s.value for s in at.sidebar.success)


@needs_latex
def test_letter_without_letterhead_is_caught(monkeypatch, tmp_path):
    """The classic mistake: letterhead added BEFORE \\clearpage, so the last
    row's letter has none. The app must refuse to send it."""
    pytest.importorskip("pypdf")
    from mailmerge import build_letters, odd_letters
    bad = (r"\documentclass{article}\usepackage{csvsimple,graphicx,eso-pic}\begin{document}"
           r"\csvreader[head to column names]{x.csv}{}{"
           r"\AddToShipoutPictureBG*{\put(0,0){\includegraphics[width=1cm]{bg.png}}}"
           r"\clearpage Hello \name}\end{document}")
    good = bad.replace(r"\clearpage Hello", "Hello").replace(
        r"\AddToShipoutPictureBG*", r"\clearpage\AddToShipoutPictureBG*")
    csv = "name\nAsha\nRohit\nMeena\n"
    support = [("bg.png", _tiny_png())]
    assert odd_letters(build_letters("bad.tex", bad, csv, support)) == [3]
    assert odd_letters(build_letters("good.tex", good, csv, support)) == []


def test_password_written_as_a_group_in_secrets_still_works(monkeypatch):
    """Secrets typed as   [app_password]  app_password = "..."   (a common slip)."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.delenv("MAILMERGE_APP_PASSWORD", raising=False)
    monkeypatch.setenv("MAILMERGE_REQUIRE_PASSWORD", "1")
    at = AppTest.from_file(APP, default_timeout=30)
    at.secrets["app_password"] = {"app_password": "exam-cell-2026"}
    at.run()
    assert not at.tabs and not at.error            # locked, and not "no password set"
    at.text_input(key="gate_password").input("exam-cell-2026 ").run()   # stray space is fine
    at.button(key="gate_btn").click().run()
    assert at.tabs and not at.exception


# ------------------------------------------- saved sender passwords ----
class LoginSpy(FakeSMTP):
    logins = []

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        LoginSpy.logins.append((user, password))


def test_saved_sender_password_is_used_and_never_shown(monkeypatch):
    monkeypatch.delenv("MAILMERGE_APP_PASSWORD", raising=False)
    monkeypatch.setattr(smtplib, "SMTP", LoginSpy)
    LoginSpy.logins = []
    at = AppTest.from_file(APP, default_timeout=30)
    at.secrets["sender_passwords"] = {"coe@kits.edu": "abcd efgh ijkl mnop",
                                      "deputycoe@kits.edu": "qrst uvwx yzab cdef"}
    at.run()
    box = at.sidebar.selectbox(key="sender_choice")
    assert box.options == ["coe@kits.edu", "deputycoe@kits.edu", "Another address…"]
    assert not any(t.key == "password" for t in at.sidebar.text_input)   # nothing to type

    at.sidebar.button(key="login_btn").click().run()
    assert LoginSpy.logins[-1] == ("coe@kits.edu", "abcdefghijklmnop")   # spaces removed
    box.select("deputycoe@kits.edu").run()
    at.sidebar.button(key="login_btn").click().run()
    assert LoginSpy.logins[-1] == ("deputycoe@kits.edu", "qrstuvwxyzabcdef")
    assert {u for u, _ in LoginSpy.logins} == {"coe@kits.edu", "deputycoe@kits.edu"}

    page = " ".join(str(e.value) for group in (at.main.markdown, at.sidebar.markdown,
                                                at.sidebar.caption, at.main.caption,
                                                at.sidebar.text_input)
                    for e in group)
    assert "abcd" not in page and "qrst" not in page

    # Typing a saved address by hand also finds its password.
    box.select("Another address…").run()
    at.sidebar.text_input(key="username").input("COE@kits.edu").run()
    at.sidebar.button(key="login_btn").click().run()
    assert LoginSpy.logins[-1] == ("COE@kits.edu", "abcdefghijklmnop")
