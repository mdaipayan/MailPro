"""Core logic: read the CSV, render the template, build the email.

No network code here, so everything in this module is easy to unit-test.
Both the command-line tool and the Streamlit app use these functions.
"""

from __future__ import annotations

import csv
import io
import mimetypes
import re
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union

import jinja2
import jinja2.meta


class MergeError(Exception):
    """Any problem with the template, the CSV, or one row of data."""


ADDRESS_HEADERS = ("To", "Cc", "Bcc")
ADDRESS_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")

Row = Dict[str, str]
Rendered = Tuple[EmailMessage, List[str]]


# ---------------------------------------------------------------- CSV ----
def safe_name(name: str) -> str:
    """Turn a column name into a valid field name: 'Roll No.' -> 'Roll_No'."""
    name = re.sub(r"\W+", "_", (name or "").strip()).strip("_")
    if name[:1].isdigit():
        name = "_" + name
    return name


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise MergeError("Cannot read the file; save it as CSV UTF-8.")


def parse_table(data: Union[bytes, str]) -> Tuple[List[str], List[List[str]]]:
    """Parse CSV content into (original column names, rows as lists).

    - Handles a UTF-8 BOM (Excel "CSV UTF-8") and Windows-1252 files.
    - Detects comma, semicolon or tab delimiters.
    - Strips spaces around names and values; skips blank lines.
    """
    text = _decode(data) if isinstance(data, bytes) else data.lstrip("\ufeff")
    if not text.strip():
        raise MergeError("The CSV file is empty.")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel  # plain comma CSV

    reader = csv.reader(io.StringIO(text, newline=""), dialect=dialect)
    try:
        headers = [(h or "").strip() for h in next(reader)]
    except StopIteration:
        raise MergeError("The CSV file has no header row.") from None
    rows = []
    for raw in reader:
        values = [(v or "").strip() for v in raw[:len(headers)]]
        values += [""] * (len(headers) - len(values))
        if any(values):
            rows.append(values)
    return headers, rows


def parse_database(data: Union[bytes, str]) -> List[Row]:
    """Parse CSV content (bytes or text) into a list of row dicts.

    Column names become valid field names ('Roll No.' -> 'Roll_No').
    See parse_table for the reading rules.
    """
    headers, table = parse_table(data)
    names = [safe_name(h) for h in headers]
    return [{k: v for k, v in zip(names, values) if k} for values in table]


def read_database(path: Union[str, Path]) -> Iterator[Row]:
    """Yield each row of a CSV file as a dict."""
    path = Path(path)
    if not path.exists():
        raise MergeError(f"CSV file not found: {path}")
    yield from parse_database(path.read_bytes())


# ----------------------------------------------------------- Template ----
def _env() -> jinja2.Environment:
    return jinja2.Environment(
        undefined=jinja2.StrictUndefined,  # unknown {{ field }} is an error
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )


def _compile(env: jinja2.Environment, text: str, where: str) -> jinja2.Template:
    try:
        return env.from_string(text)
    except jinja2.TemplateSyntaxError as exc:
        raise MergeError(f"{where}: template error, line {exc.lineno}: {exc.message}") from exc


def _render(template: jinja2.Template, row: Row, where: str = "") -> str:
    prefix = f"{where}: " if where else ""
    try:
        return template.render(**row)
    except jinja2.UndefinedError as exc:
        raise MergeError(f"{prefix}{exc.message} (check the CSV column names)") from exc
    except jinja2.TemplateError as exc:
        raise MergeError(f"{prefix}{exc}") from exc


def load_template(path: Union[str, Path]) -> jinja2.Template:
    """Load a template file (headers, blank line, body)."""
    path = Path(path)
    if not path.exists():
        raise MergeError(f"Template file not found: {path}")
    return _compile(_env(), _decode(path.read_bytes()), path.name)


def _split_headers(text: str) -> Tuple[List[Tuple[str, str]], str]:
    """Split 'Header: value' lines from the body at the first blank line."""
    text = text.lstrip("\n")
    head, sep, body = text.partition("\n\n")
    if not sep:
        raise MergeError("Template needs header lines, then a blank line, then the body.")

    headers: List[Tuple[str, str]] = []
    for line in head.splitlines():
        if line[:1] in (" ", "\t") and headers:  # folded header line
            name, value = headers[-1]
            headers[-1] = (name, value + " " + line.strip())
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*)\s*:\s*(.*)$", line)
        if not m:
            raise MergeError(f"Bad header line in template: {line!r}")
        headers.append((_title(m.group(1)), m.group(2).strip()))
    return headers, body


def _title(name: str) -> str:
    return "-".join(p.capitalize() for p in name.split("-"))


def render_message(
    template: jinja2.Template, row: Row, template_dir: Union[str, Path] = "."
) -> Rendered:
    """Fill a template file with one CSV row. Returns (message, recipients)."""
    headers, body = _split_headers(_render(template, row))
    return build_message(headers, body, template_dir)


class MessageTemplate:
    """A template given as separate fields (used by the app).

    headers: [("From", "..."), ("To", "{{ name }} <{{ email }}>"), ...]
    Each field is rendered on its own, so data in one field can never
    spill into another header.
    """

    def __init__(self, headers: Sequence[Tuple[str, str]], body: str):
        env = _env()
        self._headers = [
            (name, _compile(env, text, name))
            for name, text in headers
            if text and text.strip()
        ]
        self._body = _compile(env, body, "Body")
        self.fields: Set[str] = set()
        for _, text in list(headers) + [("Body", body)]:
            if text:
                self.fields |= jinja2.meta.find_undeclared_variables(env.parse(text))

    def render(self, row: Row, template_dir: Union[str, Path] = ".",
               files: Sequence[Tuple[str, bytes]] = ()) -> Rendered:
        """files: extra attachments held in memory, as (file name, bytes)."""
        safe_row = {k: _address_safe(v) for k, v in row.items()}
        headers = [
            (name, " ".join(_render(                       # keep on one line
                tpl, safe_row if name in ADDRESS_HEADERS else row, name).split()))
            for name, tpl in self._headers
        ]
        return build_message(headers, _render(self._body, row, "Body"), template_dir, files)


def _address_safe(value: str) -> str:
    """Make a CSV value safe inside To / Cc / Bcc.

    A name like 'Dhoble, M.' would be read as two addresses, so it is put
    in quotes. A cell that holds one or more email addresses is left alone
    (';' between addresses becomes ',').
    """
    parts = [p.strip() for p in re.split(r"[;,]", value) if p.strip()]
    if parts and all(ADDRESS_RE.match(p) for p in parts):
        return ", ".join(parts)
    if re.search(r'[,;:<>"()\[\]\\@]', value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


# -------------------------------------------------------------- Build ----
def build_message(
    headers: Sequence[Tuple[str, str]], body: str, template_dir: Union[str, Path] = ".",
    files: Sequence[Tuple[str, bytes]] = (),
) -> Rendered:
    """Build the email from already-filled headers and body.

    Returns (message, recipients). Recipients include To, Cc and Bcc;
    the Bcc header itself is not put in the message.
    """
    attachments: List[str] = []
    content_type = "text/plain"
    recipients: List[str] = []
    visible: List[Tuple[str, str]] = []
    to_count = 0

    for name, value in headers:
        if name == "Attachment":
            if value:
                attachments.append(value)
        elif name == "Content-Type":
            content_type = value.split(";")[0].strip().lower()
        else:
            if name in ADDRESS_HEADERS:
                for _, addr in getaddresses([value]):
                    if not addr:
                        continue
                    if not ADDRESS_RE.match(addr):
                        raise MergeError(f"Bad email address in {name}: {addr!r}")
                    if addr not in recipients:
                        recipients.append(addr)
                    to_count += name == "To"
            if name != "Bcc" and value:
                visible.append((name, value))

    names = [n for n, _ in visible]
    if "From" not in names:
        raise MergeError("No sender: the 'From' address is missing.")
    if to_count == 0:
        raise MergeError("No recipient: the 'To' address is missing or empty.")

    msg = EmailMessage()
    for name, value in visible:
        try:
            msg[name] = value
        except (ValueError, IndexError) as exc:
            raise MergeError(f"Bad {name} header: {value!r}") from exc

    if content_type == "text/html":
        msg.set_content(_html_to_text(body))
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)

    base = Path(template_dir)
    for item in attachments:
        _attach(msg, base, item)
    for name, data in files:
        _attach_bytes(msg, name, data)

    msg["Date"] = formatdate(localtime=True)
    from_addrs = [a for _, a in getaddresses([str(msg["From"])]) if "@" in a]
    domain = from_addrs[0].split("@")[-1] if from_addrs else "localhost"
    msg["Message-ID"] = make_msgid(domain=domain)
    return msg, recipients


def check_rows(
    template: MessageTemplate, rows: Sequence[Row], template_dir: Union[str, Path] = ".",
    files_for: Optional[Callable[[Row], Sequence[Tuple[str, bytes]]]] = None,
) -> Tuple[List[Tuple[int, EmailMessage, List[str]]], List[Tuple[int, str]], List[Tuple[int, str]]]:
    """Render every row. Returns (ready, problems, warnings).

    problems must be fixed before sending; warnings are only hints.
    Row numbers start at 1. files_for(row) gives the in-memory attachments
    of a row and may raise MergeError (for example: file not found).
    """
    ready, problems, warnings = [], [], []
    seen: Dict[str, int] = {}
    for num, row in enumerate(rows, start=1):
        try:
            files = files_for(row) if files_for else ()
            msg, rcpts = template.render(row, template_dir, files)
        except MergeError as exc:
            problems.append((num, str(exc)))
            continue
        ready.append((num, msg, rcpts))
        empty = sorted(f for f in template.fields if f in row and not row[f])
        if empty:
            warnings.append((num, "empty value in: " + ", ".join(empty)))
        first = rcpts[0].lower()
        if first in seen:
            warnings.append((num, f"same address as row {seen[first]}: {rcpts[0]}"))
        else:
            seen[first] = num
    return ready, problems, warnings


def _attach(msg: EmailMessage, base: Path, item: str) -> None:
    path = Path(item).expanduser()
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        raise MergeError(f"Attachment not found: {path}")
    _attach_bytes(msg, path.name, path.read_bytes())


def _attach_bytes(msg: EmailMessage, filename: str, data: bytes) -> None:
    ctype, encoding = mimetypes.guess_type(filename)
    if ctype is None or encoding is not None:
        ctype = "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)


def _html_to_text(html: str) -> str:
    """Very small HTML -> plain text fallback for old mail clients."""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"
