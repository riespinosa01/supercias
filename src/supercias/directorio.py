"""Read the Supercias company directory workbook."""

from __future__ import annotations

import logging
import re
import warnings
import zipfile
from pathlib import Path

import pandas as pd

from supercias.columns import fold

logger = logging.getLogger(__name__)

# The published workbook sets ``<dimension ref="A1"/>``. openpyxl's read-only
# mode trusts that box and returns only column A, so the dimension is widened
# before pandas reads the sheet.
_DIMENSION_RE = re.compile(br'<dimension\s+ref="([^"]+)"\s*/>')
_CELL_REF_RE = re.compile(br'\br="([A-Z]{1,3})\d+"')
_COL_MAX_RE = re.compile(br'<col\b[^>]*\bmax="(\d+)"')
_HEADER_SCAN_ROWS = 40
_MAX_COLUMNS = 128


def read_directorio_xlsx(path: Path) -> pd.DataFrame:
    """Return the directory table with stripped headers and no title rows."""
    readable = _workbook_pandas_can_read(path)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Workbook contains no default style",
                category=UserWarning,
            )
            raw = pd.read_excel(readable, header=None, engine="openpyxl")
    finally:
        if readable != path:
            readable.unlink(missing_ok=True)
    header_idx = _header_index(raw)
    header = [
        "" if pd.isna(value) else str(value).strip()
        for value in raw.iloc[header_idx].tolist()
    ]
    body = raw.iloc[header_idx + 1 :].copy()
    body.columns = _unique_headers(header)
    body = body.dropna(how="all").reset_index(drop=True)
    logger.info(
        "Directorio rows: %s (header on Excel row %s)",
        len(body),
        header_idx + 1,
    )
    return body


def _header_index(raw: pd.DataFrame) -> int:
    limit = min(_HEADER_SCAN_ROWS, len(raw))
    for index in range(limit):
        values = {
            fold(value)
            for value in raw.iloc[index].tolist()
            if pd.notna(value) and str(value).strip()
        }
        if fold("EXPEDIENTE") in values and (fold("RUC") in values or fold("NOMBRE") in values):
            return index
    preview = raw.head(min(6, len(raw))).to_string(index=False, header=False)
    raise ValueError(
        "Could not find the directorio header row (expected a row containing "
        f"EXPEDIENTE). First rows:\n{preview}"
    )


def _unique_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    unique: list[str] = []
    for index, name in enumerate(headers):
        base = name or f"unnamed_{index}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        unique.append(base if count == 0 else f"{base}.{count}")
    return unique


def _workbook_pandas_can_read(path: Path) -> Path:
    """Return ``path``, or a temp copy whose worksheet dimension covers the grid."""
    with zipfile.ZipFile(path) as archive:
        sheets = [
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        ]
        if not sheets:
            return path
        sheet_name = sheets[0]
        with archive.open(sheet_name) as handle:
            head = handle.read(65536)
    match = _DIMENSION_RE.search(head)
    if match is None or b":" in match.group(1):
        return path

    width = _used_column_count(head)
    replacement = b'<dimension ref="A1:' + _column_letters(width) + b'2000000"/>'
    patched = path.with_name(path.stem + ".dimension.xlsx")
    logger.info(
        "Worksheet dimension is %s; widening it to column %s so every column is read",
        match.group(1).decode("ascii", errors="replace"),
        _column_letters(width).decode("ascii"),
    )
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(
        patched, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == sheet_name:
                payload = _DIMENSION_RE.sub(replacement, payload, count=1)
            target.writestr(info, payload)
    return patched


def _used_column_count(head: bytes) -> int:
    """Widest column referenced near the top of the sheet, including the header."""
    best = 1
    for match in _CELL_REF_RE.finditer(head):
        best = max(best, _column_index(match.group(1)))
    for match in _COL_MAX_RE.finditer(head):
        best = max(best, int(match.group(1)))
    return min(max(best, 1), _MAX_COLUMNS)


def _column_index(letters: bytes) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (char - 64)
    return index


def _column_letters(index: int) -> bytes:
    letters = bytearray()
    while index:
        index, remainder = divmod(index - 1, 26)
        letters.append(65 + remainder)
    return bytes(reversed(letters))
