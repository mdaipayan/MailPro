import smtplib
from pathlib import Path

import pytest

from mailmerge import MergeError, load_template, read_database, render_message
from mailmerge import cli


def write(tmp_path: Path, name: str, text: str, encoding="utf-8") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return p


TEMPLATE = """\
From: Exam Cell <cell@kits.edu>
To: {{ name }} <{{ email }}>
Cc: hod@kits.edu
Bcc: archive@kits.edu
Subject: Marks for {{ course }}

Dear {{ name }}, you scored {{ marks }}.
"""


# ------------------------------------------------------------ CSV ----
def test_csv_bom_semicolon_and_blank_lines(tmp_path):
    p = write(tmp_path, "d.csv",
              "﻿email ; name\n a@x.com ; Asha \n\n b@x.com;Rohit\n")
    rows = list(read_database(p))
    assert rows == [{"email": "a@x.com", "name": "Asha"},
                    {"email": "b@x.com", "name": "Rohit"}]


def test_csv_quoted_comma_and_cp1252(tmp_path):
    p = tmp_path / "d.csv"
    p.write_bytes('email,name\na@x.com,"Patil, Asha – CE"\n'.encode("cp1252"))
    rows = list(read_database(p))
    assert rows[0]["name"] == "Patil, Asha – CE"


def test_csv_missing(tmp_path):
    with pytest.raises(MergeError):
        list(read_database(tmp_path / "nope.csv"))


# ------------------------------------------------------- Template ----
def test_render_headers_recipients_and_bcc_hidden(tmp_path):
    t = load_template(write(tmp_path, "t.txt", TEMPLATE))
    msg, rcpts = render_message(
        t, {"name": "Asha", "email": "a@x.com", "course": "SA", "marks": "78"})
    assert msg["Subject"] == "Marks for SA"
    assert msg["To"] == "Asha <a@x.com>"
    assert msg["Bcc"] is None
    assert rcpts == ["a@x.com", "hod@kits.edu", "archive@kits.edu"]
    assert "you scored 78" in msg.get_content()


def test_missing_column_is_an_error(tmp_path):
    t = load_template(write(tmp_path, "t.txt", TEMPLATE))
    with pytest.raises(MergeError, match="marks"):
        render_message(t, {"name": "A", "email": "a@x.com", "course": "SA"})


def test_bad_address(tmp_path):
    t = load_template(write(tmp_path, "t.txt", TEMPLATE))
    with pytest.raises(MergeError, match="Bad email"):
        render_message(t, {"name": "A", "email": "not-an-email",
                           "course": "SA", "marks": "1"})


def test_no_blank_line_between_headers_and_body(tmp_path):
    t = load_template(write(tmp_path, "t.txt", "From: a@x.com\nTo: b@x.com\nHi"))
    with pytest.raises(MergeError, match="blank line"):
        render_message(t, {})


def test_html_and_attachment(tmp_path):
    write(tmp_path, "card_A1.pdf", "%PDF-fake")
    t = load_template(write(tmp_path, "t.txt",
        "From: a@x.com\nTo: {{ email }}\nSubject: Hi\n"
        "Content-Type: text/html\nAttachment: card_{{ roll }}.pdf\n\n"
        "<p>Hello <b>{{ name }}</b></p>\n"))
    msg, _ = render_message(t, {"email": "b@x.com", "roll": "A1", "name": "Asha"},
                            tmp_path)
    html = msg.get_body(preferencelist=("html",)).get_content()
    plain = msg.get_body(preferencelist=("plain",)).get_content()
    assert "<b>Asha</b>" in html and "Hello Asha" in plain
    atts = list(msg.iter_attachments())
    assert [a.get_filename() for a in atts] == ["card_A1.pdf"]
    assert atts[0].get_content_type() == "application/pdf"


def test_missing_attachment(tmp_path):
    t = load_template(write(tmp_path, "t.txt",
        "From: a@x.com\nTo: b@x.com\nAttachment: none.pdf\n\nHi\n"))
    with pytest.raises(MergeError, match="Attachment not found"):
        render_message(t, {}, tmp_path)


def test_jinja_logic(tmp_path):
    t = load_template(write(tmp_path, "t.txt",
        "From: a@x.com\nTo: b@x.com\n\n"
        "{% if marks|int < 40 %}RETEST{% else %}PASS{% endif %}\n"))
    assert "RETEST" in render_message(t, {"marks": "35"})[0].get_content()
    assert "PASS" in render_message(t, {"marks": "78"})[0].get_content()


# ------------------------------------------------------------ CLI ----
class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        if password != "secret":
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, msg, to_addrs=None):
        if "fail" in to_addrs[0]:
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (550, b"no")})
        FakeSMTP.sent.append((msg["Subject"], to_addrs))

    def noop(self):
        return (250, b"ok")

    def quit(self):
        pass


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--sample"]) == 0
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr("time.sleep", lambda s: None)
    FakeSMTP.sent = []
    return tmp_path


def test_dry_run_sends_nothing(project, capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Dry run OK: 2 email(s)" in out
    assert "Subject: Marks for Structural Analysis" in out
    assert FakeSMTP.sent == []


def test_send_and_log(project, monkeypatch):
    monkeypatch.setenv("MAILMERGE_PASSWORD", "secret")
    assert cli.main(["--send"]) == 0
    assert len(FakeSMTP.sent) == 2
    log = (project / "mailmerge_log.csv").read_text().splitlines()
    assert len(log) == 3 and log[1].endswith("sent,")


def test_wrong_password(project, monkeypatch, capsys):
    monkeypatch.setenv("MAILMERGE_PASSWORD", "wrong")
    assert cli.main(["--send"]) == 1
    assert "App Password" in capsys.readouterr().err
    assert FakeSMTP.sent == []


def test_start_and_limit(project, monkeypatch):
    monkeypatch.setenv("MAILMERGE_PASSWORD", "secret")
    assert cli.main(["--send", "--start", "2", "--limit", "1"]) == 0
    assert [r for _, r in FakeSMTP.sent] == [["student2@example.com"]]


def test_one_bad_row_blocks_everything(project, monkeypatch, capsys):
    (project / "mailmerge_database.csv").write_text(
        "email,name,course,marks\nok@x.com,A,SA,50\nbroken,B,SA,60\n")
    monkeypatch.setenv("MAILMERGE_PASSWORD", "secret")
    assert cli.main(["--send"]) == 1
    assert "row 2" in capsys.readouterr().err
    assert FakeSMTP.sent == []


def test_server_refusal_is_logged_and_continues(project, monkeypatch):
    (project / "mailmerge_database.csv").write_text(
        "email,name,course,marks\nfail@x.com,A,SA,50\nok@x.com,B,SA,60\n")
    monkeypatch.setenv("MAILMERGE_PASSWORD", "secret")
    assert cli.main(["--send"]) == 2
    assert len(FakeSMTP.sent) == 1
    assert "failed" in (project / "mailmerge_log.csv").read_text()


def test_save_eml(project):
    assert cli.main(["--save-eml", "out"]) == 0
    assert sorted(p.name for p in (project / "out").iterdir()) == \
        ["row_0001.eml", "row_0002.eml"]


# ---------------------------------------------------- attachments ----
def _zip(members):
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_expand_uploads_opens_zip_and_drops_folders():
    from mailmerge import expand_uploads
    z = _zip({"letters/PS_101.pdf": b"a", "letters/VAL_7.pdf": b"b",
              "__MACOSX/letters/._PS_101.pdf": b"junk"})
    files = expand_uploads([("letters.zip", z), ("rules.pdf", b"c")])
    assert sorted(files) == ["PS_101.pdf", "VAL_7.pdf", "rules.pdf"]
    with pytest.raises(MergeError, match="same name"):
        expand_uploads([("a.pdf", b"1"), ("A.PDF", b"2")])
    with pytest.raises(MergeError, match="Cannot open"):
        expand_uploads([("bad.zip", b"not a zip")])


def test_pick_files_rules():
    from mailmerge import pick_files
    files = {"PS_101.pdf": b"a", "bill_form.docx": b"b", "X.pdf": b"1", "X.docx": b"2"}
    assert [n for n, _ in pick_files("ps_101", files)] == ["PS_101.pdf"]
    assert [n for n, _ in pick_files("PS_101.pdf; bill_form", files)] == \
        ["PS_101.pdf", "bill_form.docx"]
    assert pick_files("", files, required=False) == []
    with pytest.raises(MergeError, match="no file name"):
        pick_files("", files)
    with pytest.raises(MergeError, match="not found"):
        pick_files("PS_999", files)
    with pytest.raises(MergeError, match="more than one"):
        pick_files("X", files)


def test_each_person_gets_only_their_own_file():
    from mailmerge import MessageTemplate, check_rows, check_size, pick_files
    rows = [{"email": "a@x.com", "letter": "PS_101"},
            {"email": "b@x.com", "letter": "VAL_7"},
            {"email": "c@x.com", "letter": "missing"}]
    personal = {"PS_101.pdf": b"AAA", "VAL_7.pdf": b"BBB"}
    common = [("rules.pdf", b"RULES")]

    def files_for(row):
        out = common + pick_files(row["letter"], personal)
        check_size(out)
        return out

    tpl = MessageTemplate([("From", "coe@kits.edu"), ("To", "{{ email }}"),
                           ("Subject", "Appointment")], "Please see the letter.")
    ready, problems, _ = check_rows(tpl, rows, files_for=files_for)
    got = {r[0]: [(a.get_filename(), a.get_payload(decode=True))
                  for a in m.iter_attachments()] for _, m, r in ready}
    assert got == {"a@x.com": [("rules.pdf", b"RULES"), ("PS_101.pdf", b"AAA")],
                   "b@x.com": [("rules.pdf", b"RULES"), ("VAL_7.pdf", b"BBB")]}
    assert problems == [(3, "file not found: missing")]


def test_too_large_attachment():
    from mailmerge import check_size
    from mailmerge.attachments import MAX_EMAIL_BYTES
    with pytest.raises(MergeError, match="limit"):
        check_size([("big.pdf", b"0" * (MAX_EMAIL_BYTES + 1))])


def test_assign_files_by_person_and_course():
    from mailmerge import assign_files
    rows = [{"pid": "T014", "code": "BECVE501T"},   # setter, 501
            {"pid": "T031", "code": "BECVE501T"},   # valuer, same course
            {"pid": "T014", "code": "BECVE503T"},   # same person, other course
            {"pid": "T01", "code": "BECVE501T"},    # T01 must not take T014's files
            {"pid": "", "code": "BECVE501T"}]       # empty ID gets nothing
    names = ["Appointment_T014_BECVE501T.pdf", "Bill-t014-becve501t.pdf",
             "Undertaking T014 BECVE501T.pdf", "Appointment_T031_BECVE501T.pdf",
             "Appointment_T014_BECVE503T.pdf", "Appointment_T099_BECVE501T.pdf"]
    files = {n: b"x" for n in names}

    assigned, clashes = assign_files(rows, files, ["pid", "code"])
    assert assigned[0] == ["Appointment_T014_BECVE501T.pdf", "Bill-t014-becve501t.pdf",
                           "Undertaking T014 BECVE501T.pdf"]
    assert assigned[1] == ["Appointment_T031_BECVE501T.pdf"]
    assert assigned[2] == ["Appointment_T014_BECVE503T.pdf"]
    assert assigned[3] == [] and assigned[4] == []
    assert clashes == {}

    # Course code alone cannot tell the setter from the valuer: a clash.
    _, clashes = assign_files(rows[:2], files, ["code"])
    assert clashes["Appointment_T014_BECVE501T.pdf"] == [1, 2]
    # Person ID alone mixes the two courses of T014: also a clash.
    _, clashes = assign_files(rows[:3], files, ["pid"])
    assert clashes["Appointment_T014_BECVE503T.pdf"] == [1, 3]


def test_assign_files_name_with_spaces_and_dots():
    from mailmerge import assign_files
    rows = [{"name": "A. B. Deshmukh"}, {"name": "S. R. Kale"}]
    files = {"Bill_A_B_Deshmukh.pdf": b"1", "Bill S.R. Kale.pdf": b"2"}
    assigned, clashes = assign_files(rows, files, ["name"])
    assert assigned == [["Bill_A_B_Deshmukh.pdf"], ["Bill S.R. Kale.pdf"]] and not clashes


# -------------------------------------------------------- letters ----
import shutil as _shutil

needs_latex = pytest.mark.skipif(_shutil.which("pdflatex") is None, reason="no pdflatex")

LETTER_TEX = r"""
\documentclass[a4paper,12pt]{article}
\usepackage{csvsimple}
\begin{document}
\pagenumbering{gobble}
\csvreader[head to column names]{mailmerge_database_9.csv}{}{
\clearpage
To \salutation\ \name, \institute, \place.

Course: \coursetitle\ (\coursecode). EXTRA
}
\end{document}
"""
LETTER_CSV = (
    "\ufeffsl_no,email,salutation,name,institute,place,coursetitle,coursecode\n"
    "1,a@x.com,Dr.,Asha Rao,ABC,\" Shahapur, Bhandara\",Soil & Rock,CE_501\n"
    "\n"
    "2,b@x.com,Mr.,Ravi Kale,XYZ,Nagpur,Surveying 100%,CE#2\n"
)


def _pdf_text(pdf):
    import io
    from pypdf import PdfReader
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def test_tex_csv_is_clean():
    from mailmerge.letters import find_csv_names, tex_csv
    data, n = tex_csv(LETTER_CSV)
    lines = data.decode().splitlines()
    assert n == 2 and len(lines) == 3 and not data.startswith(b"\xef\xbb\xbf")
    assert lines[1] == r"1,a@x.com,Dr.,Asha Rao,ABC,{Shahapur, Bhandara},Soil \& Rock,CE\_501"
    assert lines[2].endswith(r"Surveying 100\%,CE\#2")
    assert find_csv_names(LETTER_TEX) == ["mailmerge_database_9.csv"]
    assert find_csv_names("% \\csvreader{old.csv}\n\\csvreader{new.csv}{}{}") == ["new.csv"]


@needs_latex
def test_one_letter_per_row_with_the_right_person():
    pytest.importorskip("pypdf")
    from mailmerge.letters import build_letters
    letters = build_letters("letter.tex", LETTER_TEX, LETTER_CSV)
    assert len(letters) == 2
    one, two = _pdf_text(letters[0]), _pdf_text(letters[1])
    assert "Asha Rao" in one and "Shahapur, Bhandara" in one and "Soil & Rock" in one
    assert "Ravi Kale" in two and "Asha" not in two and "100%" in two


@needs_latex
def test_two_page_letters_still_match_their_rows():
    pytest.importorskip("pypdf")
    from mailmerge.letters import build_letters
    tex = LETTER_TEX.replace("EXTRA", r"\newpage Second page for \name.")
    seen = []
    letters = build_letters("letter.tex", tex, LETTER_CSV, progress=lambda i, n: seen.append(i))
    assert len(letters) == 2 and seen == [1, 2]
    assert _pdf_text(letters[1]).count("Ravi Kale") == 2 and "Asha" not in _pdf_text(letters[1])


@needs_latex
def test_latex_error_is_reported_with_line():
    from mailmerge.letters import build_letters
    with pytest.raises(MergeError, match=r"LaTeX error: Undefined control sequence.*line"):
        build_letters("bad.tex", LETTER_TEX.replace(r"\place", r"\nosuchcolumn"), LETTER_CSV)
    with pytest.raises(MergeError, match="csvreader"):
        build_letters("plain.tex", r"\documentclass{article}\begin{document}Hi\end{document}",
                      LETTER_CSV)


def test_name_with_comma_does_not_break_the_address():
    from mailmerge import MessageTemplate
    tpl = MessageTemplate([("From", "coe@kits.edu"), ("To", "{{ name }} <{{ email }}>"),
                           ("Cc", "{{ cc }}"), ("Subject", "Hi {{ name }}")], "Body")
    msg, rcpts = tpl.render({"name": 'Dhoble, M. "Mayu"', "email": "m@x.com",
                             "cc": "a@x.com; b@x.com"})
    assert rcpts == ["m@x.com", "a@x.com", "b@x.com"]
    assert msg["To"].addresses[0].display_name == 'Dhoble, M. "Mayu"'
    assert msg["Subject"] == 'Hi Dhoble, M. "Mayu"'      # other fields are not quoted


def test_load_library_and_auto_file_columns(tmp_path):
    from mailmerge import load_library
    from mailmerge.attachments import columns_naming_files
    (tmp_path / "_shared").mkdir()
    (tmp_path / "_shared" / "bg.png").write_bytes(b"BG")
    s = tmp_path / "paper_setter"
    (s / "files").mkdir(parents=True)
    (s / "Appointment.tex").write_text("tex")
    (s / "sign.png").write_bytes(b"S")
    (s / "email.txt").write_text("Subject: Hello {{ name }}\n\nBody line\n")
    (s / "files" / "3S_CE_SM.pdf").write_bytes(b"1")
    (s / "files" / "Guidelines.pdf").write_bytes(b"2")
    (s / "files" / "_note.txt").write_text("ignored")
    (tmp_path / "empty_folder").mkdir()

    sets, shared = load_library(tmp_path)
    assert [x.key for x in sets] == ["paper_setter"] and sets[0].title == "Paper setter"
    assert [n for n, _ in sets[0].letters] == ["Appointment.tex"]
    assert [n for n, _ in sets[0].support] == ["bg.png", "sign.png"]
    assert sorted(sets[0].files) == ["3S_CE_SM.pdf", "Guidelines.pdf"]
    assert sets[0].email == ("Hello {{ name }}", "Body line\n")
    assert sets[0].attachment_name("acceptance form.tex") == "Acceptance_form.pdf"
    assert load_library(tmp_path / "nothing") == ([], ())

    rows = [{"sl": "1", "syllabus": "3S_CE_SM", "a4": "Guidelines", "note": "x", "blank": ""},
            {"sl": "2", "syllabus": "3s_ce_sm", "a4": "Guidelines", "note": "", "blank": ""}]
    assert columns_naming_files(rows, list(rows[0]), sets[0].files) == ["syllabus", "a4"]


def test_images_needed_and_missing():
    from mailmerge import images_needed, missing_images
    tex = ("\\includegraphics[width=3cm]{sign.png}\n"
           "% \\includegraphics{old.png}\n"
           "\\includegraphics{letterhead}  % comment \\includegraphics{no.png}\n"
           "\\includegraphics[width=\\paperwidth,height=\\paperheight]{bg.png}\n")
    assert images_needed(tex) == ["sign.png", "letterhead", "bg.png"]
    assert missing_images(tex, ["sign.png", "letterhead.jpg"]) == ["bg.png"]
    assert missing_images(tex, ["sign.png", "letterhead.jpg", "bg.png"]) == []


def test_fit_image_any_name_any_format():
    PIL = pytest.importorskip("PIL")
    import io
    from PIL import Image
    from mailmerge import fit_image, image_role
    buf = io.BytesIO(); Image.new("RGBA", (4, 4), (255, 0, 0, 0)).save(buf, "PNG"); png = buf.getvalue()
    buf = io.BytesIO(); Image.new("RGB", (4, 4), "blue").save(buf, "JPEG"); jpg = buf.getvalue()

    assert fit_image("sign.png", png) is png                       # already right
    assert fit_image("sign.png", jpg).startswith(b"\x89PNG")       # JPG -> PNG
    as_jpg = fit_image("letterhead.jpg", png)                      # PNG -> JPG, white behind
    assert as_jpg.startswith(b"\xff\xd8\xff")
    assert Image.open(io.BytesIO(as_jpg)).getpixel((1, 1))[0] > 240
    with pytest.raises(MergeError, match="not a PNG or JPG"):
        fit_image("sign.png", b"hello")

    assert image_role("signAND-removebg-preview.png") == "Signature"
    assert image_role("Admit_card_background.png") == "Letterhead"
    assert image_role("photo1.png") == "Image"


# --------------------------------------------- second mail port ----
def test_gmail_falls_back_to_port_465_when_587_is_closed(monkeypatch):
    from mailmerge import Sender, ServerConfig
    calls = []

    class Closed587:
        def __init__(self, host, port, timeout=None, **kw):
            calls.append(("plain", port))
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    class Open465(FakeSMTP):
        def __init__(self, host, port, context=None, timeout=None):
            calls.append(("ssl", port))

    monkeypatch.setattr(smtplib, "SMTP", Closed587)
    monkeypatch.setattr(smtplib, "SMTP_SSL", Open465)
    with Sender(ServerConfig("smtp.gmail.com", 587, "starttls", "a@gmail.com"), "secret") as s:
        assert s.config.port == 465 and s.config.security == "ssl"
    assert calls == [("plain", 587), ("ssl", 465)]

    # Both closed: one clear message, no endless tries.
    monkeypatch.setattr(smtplib, "SMTP_SSL", Closed587)
    with pytest.raises(MergeError, match="port 587 or 465.*block outgoing email"):
        Sender(ServerConfig("smtp.gmail.com", 587, "starttls", "a@gmail.com"), "secret").__enter__()

    # Another server has no second door: the old message stays.
    with pytest.raises(MergeError, match=r"Cannot connect to mail.kits.edu:587"):
        Sender(ServerConfig("mail.kits.edu", 587, "starttls"), "x").__enter__()


def test_wrong_password_does_not_try_the_other_port(monkeypatch):
    from mailmerge import Sender, ServerConfig
    ssl_calls = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: ssl_calls.append(1))
    with pytest.raises(MergeError, match="Login failed"):
        Sender(ServerConfig("smtp.gmail.com", 587, "starttls", "a@gmail.com"), "wrong").__enter__()
    assert ssl_calls == []
