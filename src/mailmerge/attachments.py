"""Attachments held in memory (used by the app).

- expand_uploads: uploaded files -> {file name: bytes}; ZIP files are opened.
- pick_files:     find the file(s) named in one CSV value.
- check_size:     stop emails that are too big for Gmail / Outlook.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Sequence, Tuple

from .core import MergeError

File = Tuple[str, bytes]
MAX_EMAIL_BYTES = 18 * 1024 * 1024    # ~25 MB after email encoding
MAX_UNZIPPED_BYTES = 300 * 1024 * 1024


def expand_uploads(uploads: Iterable[File]) -> Dict[str, bytes]:
    """Return {file name: content}. A .zip upload is replaced by its files."""
    files: Dict[str, bytes] = {}
    total = 0

    def add(name: str, data: bytes) -> None:
        nonlocal total
        if name.lower() in {n.lower() for n in files}:
            raise MergeError(f"Two uploaded files have the same name: {name}")
        total += len(data)
        if total > MAX_UNZIPPED_BYTES:
            raise MergeError("The uploaded files are too large in total.")
        files[name] = data

    for name, data in uploads:
        if not name.lower().endswith(".zip"):
            add(name, data)
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    base = PurePosixPath(info.filename.replace("\\", "/")).name
                    if info.is_dir() or not base or base.startswith(".") \
                            or "__MACOSX" in info.filename:
                        continue
                    if total + info.file_size > MAX_UNZIPPED_BYTES:
                        raise MergeError("The uploaded files are too large in total.")
                    add(base, zf.read(info))
        except zipfile.BadZipFile as exc:
            raise MergeError(f"Cannot open the ZIP file: {name}") from exc
    return files


def pick_files(value: str, files: Dict[str, bytes], required: bool = True) -> List[File]:
    """Find the files named in one CSV value.

    The value is a file name with or without extension ('PS_101' matches
    'PS_101.pdf'). Several names can be separated by ';'. Case is ignored.
    """
    wanted = [v.strip() for v in re.split(r"[;|]", value or "") if v.strip()]
    if not wanted:
        if required:
            raise MergeError("no file name is given for this row")
        return []

    picked: List[File] = []
    for want in wanted:
        low = want.lower()
        hits = [n for n in files if n.lower() == low]
        if not hits:
            hits = [n for n in files if n.lower().rsplit(".", 1)[0] == low]
        if not hits:
            raise MergeError(f"file not found: {want}")
        if len(hits) > 1:
            raise MergeError(f"more than one file matches {want!r}: {', '.join(sorted(hits))}")
        if hits[0] not in [n for n, _ in picked]:
            picked.append((hits[0], files[hits[0]]))
    return picked


def check_size(files: Sequence[File]) -> None:
    size = sum(len(d) for _, d in files)
    if size > MAX_EMAIL_BYTES:
        raise MergeError(
            f"attachments are {size / 1048576:.1f} MB; the limit is about "
            f"{MAX_EMAIL_BYTES // 1048576} MB per email"
        )


# ------------------------------------------------ match by identity ----
def _tokens(text: str) -> List[str]:
    """'Bill_T014-BECVE501T' -> ['bill', 't014', 'becve501t']"""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _contains(hay: List[str], needle: List[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def assign_files(
    rows: Sequence[Dict[str, str]], files: Dict[str, bytes], key_columns: Sequence[str]
) -> Tuple[List[List[str]], Dict[str, List[int]]]:
    """Give each row the files whose NAME contains the row's key values.

    Example: key_columns = ['person_id', 'course_code'] and a row with
    T014 / BECVE501T gets 'Appointment_T014_BECVE501T.pdf',
    'Bill_T014_BECVE501T.pdf' and so on.

    Whole words only: 'T01' does not match 'T014'. Case, spaces, '_' and
    '-' do not matter. A row with an empty key value gets no file.

    Returns (files per row, clashes). clashes = {file name: [row numbers]}
    for every file that matches more than one row; such a file must never
    be sent, because it is not clear whose it is.
    """
    stems = {name: _tokens(name.rsplit(".", 1)[0]) for name in files}
    assigned: List[List[str]] = []
    owners: Dict[str, List[int]] = {}
    for num, row in enumerate(rows, start=1):
        keys = [_tokens(row.get(c, "")) for c in key_columns]
        names: List[str] = []
        if keys and all(keys):
            names = sorted(n for n, toks in stems.items()
                           if all(_contains(toks, k) for k in keys))
        assigned.append(names)
        for n in names:
            owners.setdefault(n, []).append(num)
    clashes = {n: nums for n, nums in owners.items() if len(nums) > 1}
    return assigned, clashes


def columns_naming_files(rows: Sequence[Dict[str, str]], columns: Sequence[str],
                         files: Dict[str, bytes]) -> List[str]:
    """Columns whose every filled cell is the name of an available file.

    Used to find, without asking, the columns such as 'syllabus' that hold
    file names. A column with numbers or free text does not qualify.
    """
    found = []
    for col in columns:
        values = [r.get(col, "") for r in rows if r.get(col, "")]
        if not values:
            continue
        try:
            for v in set(values):
                pick_files(v, files)
        except MergeError:
            continue
        found.append(col)
    return found
