r"""Make one PDF letter per CSV row from a LaTeX template.

The template is an ordinary csvsimple mail-merge file, for example:

    \csvreader[head to column names]{mailmerge_database.csv}{}{
        ... \name ... \coursetitle ... \clearpage
    }

build_letters() puts the current list where the template expects its CSV
file, runs pdflatex, and cuts the PDF into one letter per row.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Union

from .core import MergeError, _decode, parse_table

File = Tuple[str, bytes]
MAX_ROWS_ONE_BY_ONE = 400
_SPECIAL = re.compile(r"(?<!\\)([&%#$_])")


def find_csv_names(tex: str) -> List[str]:
    """CSV file names used by \\csvreader / \\csvloop / \\csvautotabular."""
    lines = [ln.split("%")[0] if not ln.lstrip().startswith("%") else ""
             for ln in tex.splitlines()]
    names = re.findall(r"\\csvreader\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}", "\n".join(lines))
    out: List[str] = []
    for n in names:
        n = n.strip()
        if n and n not in out:
            out.append(n)
    return out


def images_needed(tex: Union[bytes, str]) -> List[str]:
    """File names used by \\includegraphics in a template (comments ignored)."""
    text = _decode(tex) if isinstance(tex, bytes) else tex
    lines = [ln.split("%")[0] if not ln.lstrip().startswith("%") else ""
             for ln in text.splitlines()]
    found = re.findall(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}", "\n".join(lines))
    out: List[str] = []
    for name in found:
        name = name.strip()
        if name and name not in out:
            out.append(name)
    return out


def missing_images(tex: Union[bytes, str], available: Iterable[str]) -> List[str]:
    """Images the template needs that are not among the available file names."""
    names = list(available)
    stems = {n.rsplit(".", 1)[0] for n in names}
    return [n for n in images_needed(tex) if n not in names and n not in stems]


def image_role(name: str) -> str:
    """A friendly label for an image a template needs."""
    low = name.lower()
    if "sign" in low:
        return "Signature"
    if any(k in low for k in ("background", "letterhead", "letter_head", "bg", "header")):
        return "Letterhead"
    if "logo" in low:
        return "Logo"
    return "Image"


def fit_image(expected_name: str, data: bytes) -> bytes:
    """Make an uploaded picture usable under the name the template expects.

    The person may upload 'sign.jpg' where the template wants
    'signature.png'. LaTeX goes by the file extension, so the picture is
    converted to that format when the two differ.
    """
    kinds = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"%PDF": "pdf"}
    actual = next((k for magic, k in kinds.items() if data.startswith(magic)), None)
    wanted = expected_name.rsplit(".", 1)[-1].lower() if "." in expected_name else actual
    wanted = "jpg" if wanted == "jpeg" else wanted
    if actual is None:
        raise MergeError(f"{expected_name}: the uploaded file is not a PNG or JPG picture.")
    if actual == wanted:
        return data
    if "pdf" in (actual, wanted):
        raise MergeError(f"{expected_name}: please upload a {wanted.upper()} file.")
    try:
        from PIL import Image
    except ImportError:
        raise MergeError(f"{expected_name}: please upload a {wanted.upper()} file.") from None
    img = Image.open(io.BytesIO(data))
    out = io.BytesIO()
    if wanted == "jpg":
        if img.mode in ("RGBA", "LA", "P"):          # JPG has no transparency
            rgba = img.convert("RGBA")
            white = Image.new("RGB", rgba.size, "white")
            white.paste(rgba, mask=rgba.split()[-1])
            img = white
        img.convert("RGB").save(out, "JPEG", quality=92)
    else:
        img.save(out, "PNG")
    return out.getvalue()


def _tex_field(value: str) -> str:
    value = _SPECIAL.sub(r"\\\1", value)          # & % # $ _  ->  \& \% ...
    return "{" + value + "}" if "," in value else value   # protect commas


def tex_csv(data: Union[bytes, str], only_row: Optional[int] = None) -> Tuple[bytes, int]:
    """The list as a clean CSV for LaTeX. Returns (csv bytes, number of rows).

    No BOM, no blank lines, commas inside a value protected by braces,
    and & % # $ _ escaped, so ordinary data cannot break the letter.
    """
    headers, rows = parse_table(data)
    chosen = rows if only_row is None else [rows[only_row]]
    lines = [",".join(headers)] + [",".join(_tex_field(v) for v in r) for r in chosen]
    return ("\n".join(lines) + "\n").encode("utf-8"), len(rows)


def _safe_name(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    if not base or base in (".", ".."):
        raise MergeError(f"Bad file name: {name!r}")
    return base


def _latex_error(log: str, tex_name: str) -> str:
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("! "):
            where = next((m.group(1) for ln in lines[i:i + 12]
                          if (m := re.match(r"l\.(\d+)", ln))), None)
            if "Undefined control sequence" in line:
                macros = re.findall(r"\\([A-Za-z_]+)\s*$", "\n".join(lines[i + 1:i + 3]), re.M)
                if macros:
                    return (f"{tex_name}: this letter needs a column named "
                            f"'{macros[0]}', but the list does not have it. "
                            "(LaTeX error: Undefined control sequence"
                            + (f", line {where})" if where else ")"))
            return (f"{tex_name}: LaTeX error: {line[2:].strip()}"
                    + (f" (line {where})" if where else ""))
    return f"{tex_name}: LaTeX could not make the PDF."


def _compile(folder: Path, tex_name: str, timeout: int) -> bytes:
    exe = shutil.which("pdflatex")
    if not exe:
        raise MergeError("LaTeX (pdflatex) is not installed on this computer, "
                         "so letters cannot be made here.")
    env = dict(os.environ, openin_any="p", openout_any="p")   # stay inside the folder
    cmd = [exe, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "letter.tex"]
    for _ in range(2):
        try:
            run = subprocess.run(cmd, cwd=folder, env=env, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise MergeError(f"{tex_name}: LaTeX took too long and was stopped.") from None
        log_path = folder / "letter.log"
        log = log_path.read_text(errors="replace") if log_path.exists() else ""
        pdf = folder / "letter.pdf"
        if run.returncode != 0 or not pdf.exists() or pdf.stat().st_size == 0:
            raise MergeError(_latex_error(log, tex_name))
        if "Rerun to get" not in log:
            break
    return pdf.read_bytes()


def _split(pdf: bytes) -> List[bytes]:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        raise MergeError("The 'pypdf' package is needed: pip install pypdf") from None
    pages = []
    for page in PdfReader(io.BytesIO(pdf)).pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        pages.append(buf.getvalue())
    return pages


def odd_letters(pdfs: List[bytes]) -> List[int]:
    """Row numbers whose letter has fewer images than most of the others.

    This catches a letter that came out without its letterhead or signature
    (a typical mail-merge template mistake hits only the first or last row).
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    counts = []
    for pdf in pdfs:
        try:
            counts.append(sum(len(page.images) for page in PdfReader(io.BytesIO(pdf)).pages))
        except Exception:
            counts.append(-1)
    if len(counts) < 2:
        return []
    usual = max(set(counts), key=counts.count)
    return [i for i, c in enumerate(counts, start=1) if c < usual]


def build_letters(
    tex_name: str,
    tex_source: Union[bytes, str],
    csv_data: Union[bytes, str],
    support: Iterable[File] = (),
    timeout: int = 300,
    progress: Optional[Callable[[int, int], None]] = None,
) -> List[bytes]:
    """Return one PDF (bytes) per row of the list, in row order.

    Fast way: one LaTeX run for the whole list, then one page per row.
    If the template does not give exactly one page per row (for example a
    two-page letter), each row is compiled on its own instead. That is
    slower but always keeps the right letter with the right row.
    """
    tex = _decode(tex_source) if isinstance(tex_source, bytes) else tex_source
    csv_names = find_csv_names(tex)
    if not csv_names:
        raise MergeError(f"{tex_name}: no \\csvreader{{file.csv}} line was found, "
                         "so the letter cannot read the list.")
    full_csv, n_rows = tex_csv(csv_data)
    if n_rows == 0:
        raise MergeError("The list has no rows.")

    with tempfile.TemporaryDirectory(prefix="mailmerge_tex_") as tmp:
        folder = Path(tmp)
        for name, data in support:
            (folder / _safe_name(name)).write_bytes(data)
        (folder / "letter.tex").write_text(tex, encoding="utf-8")

        def put_csv(content: bytes) -> None:
            for name in csv_names:
                (folder / _safe_name(name)).write_bytes(content)

        put_csv(full_csv)
        pages = _split(_compile(folder, tex_name, timeout))
        if len(pages) == n_rows:
            return pages

        if n_rows > MAX_ROWS_ONE_BY_ONE:
            raise MergeError(
                f"{tex_name}: made {len(pages)} pages for {n_rows} rows. Make the "
                "letter exactly one page per row, or send the list in smaller parts.")
        letters = []
        for i in range(n_rows):
            put_csv(tex_csv(csv_data, only_row=i)[0])
            letters.append(_compile(folder, f"{tex_name}, row {i + 1}", timeout))
            if progress:
                progress(i + 1, n_rows)
        return letters


# ------------------------------------------------------ letter library ----
@dataclass
class LetterSet:
    """One folder of the letter library, for example '1_paper_setter'."""
    key: str                                  # folder name
    title: str                                # 'Paper setter'
    letters: List[File] = field(default_factory=list)     # (file name, LaTeX source)
    support: Tuple[File, ...] = ()            # images: the set's own + _shared/
    files: Dict[str, bytes] = field(default_factory=dict)  # shared files named in the list
    email: Optional[Tuple[str, str]] = None   # (subject, body) from email.txt

    @staticmethod
    def attachment_name(tex_name: str) -> str:
        """'Acceptance_Form.tex' -> 'Acceptance_Form.pdf'"""
        stem = tex_name.rsplit(".", 1)[0].strip().replace(" ", "_")
        return stem[:1].upper() + stem[1:] + ".pdf"


_SKIP = {"email.txt", "title.txt", "readme.txt", "readme.md"}
_MAX_LIBRARY_FILE = 30 * 1024 * 1024


def _plain_files(folder: Path) -> List[File]:
    if not folder.is_dir():
        return []
    return [(p.name, p.read_bytes()) for p in sorted(folder.iterdir())
            if p.is_file() and not p.name.startswith((".", "_"))
            and p.stat().st_size <= _MAX_LIBRARY_FILE]


def parse_email_text(text: str) -> Tuple[str, str]:
    """'Subject: ...', blank line, body  ->  (subject, body)"""
    text = text.lstrip("﻿").replace("\r\n", "\n").strip("\n")
    first, _, rest = text.partition("\n")
    if first.lower().startswith("subject:"):
        return first.split(":", 1)[1].strip(), rest.lstrip("\n") + "\n"
    return "", text + "\n"


def load_library(root: Union[str, Path]) -> Tuple[List[LetterSet], Tuple[File, ...]]:
    """Read the letter library folder. Returns (letter sets, shared images).

    root/_shared/         images for all sets (letterhead, signature)
    root/<set>/*.tex      one letter per file
    root/<set>/email.txt  optional email text for the set
    root/<set>/files/     optional shared files that the list names
    """
    root = Path(root)
    if not root.is_dir():
        return [], ()
    shared = tuple(_plain_files(root / "_shared"))
    sets: List[LetterSet] = []
    for folder in sorted(p for p in root.iterdir()
                         if p.is_dir() and not p.name.startswith((".", "_"))):
        own = _plain_files(folder)
        letters = [(n, d) for n, d in own if n.lower().endswith(".tex")]
        letters.sort(key=lambda f: (not f[0].lower().startswith("appointment"), f[0].lower()))
        if not letters:
            continue
        images = {n: d for n, d in shared}
        images.update({n: d for n, d in own
                       if not n.lower().endswith(".tex") and n.lower() not in _SKIP})
        email = next((parse_email_text(_decode(d)) for n, d in own
                      if n.lower() == "email.txt"), None)
        sets.append(LetterSet(
            key=folder.name,
            title=next((_decode(d).strip().splitlines()[0].strip() for n, d in own
                        if n.lower() == "title.txt" and _decode(d).strip()),
                       re.sub(r"^\d+[_ -]*", "", folder.name).replace("_", " ")
                       .strip().capitalize() or folder.name),
            letters=letters,
            support=tuple(sorted(images.items())),
            files=dict(_plain_files(folder / "files")),
            email=email,
        ))
    return sets, shared
