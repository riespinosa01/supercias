"""Parse Supercias estados financieros text produced by ``pdftotext -layout``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Label, then the account code, then the amount at the end of the line.
# Amounts may be integers (``38280``), decimals (``3655446.7``), or negative.
_LINE_RE = re.compile(
    r"^(?P<label>.*?)\s+(?P<code>\d+)\s+(?P<amount>-?\d+(?:\.\d+)?)\s*$"
)
_LETTER_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")
_META_RE = {
    "razon_social": re.compile(r"RAZ[ÓO]N\s+SOCIAL\s+(.+)$", re.IGNORECASE),
    "expediente": re.compile(r"EXPEDIENTE\s+(\d+)", re.IGNORECASE),
    "ruc": re.compile(r"\bRUC\s+(\d+)", re.IGNORECASE),
}
_YEAR_LINE_RE = re.compile(r"(?:AÑO|ANO)\s+(\d{4})", re.IGNORECASE)


@dataclass(frozen=True)
class AccountLine:
    code: str
    label: str
    amount: float
    raw_amount: str


@dataclass
class Statement:
    """One balance sheet or income statement."""

    kind: str
    path: Path | None
    razon_social: str
    expediente: int | None
    ruc: str
    year: int | None
    lines: list[AccountLine] = field(default_factory=list)
    by_code: dict[str, AccountLine] = field(default_factory=dict)

    def amount(self, code: str) -> float | None:
        line = self.by_code.get(str(code))
        if line is None:
            return None
        return line.amount

    def label(self, code: str) -> str | None:
        line = self.by_code.get(str(code))
        if line is None:
            return None
        return line.label


def parse_statement(text: str, *, path: Path | None = None, kind: str | None = None) -> Statement:
    """Extract metadata and ``(code, label, amount)`` rows from one ``pdftotext -layout`` file."""
    normalized = text.replace("\x0c", "\n")
    meta = _metadata(normalized)
    detected = kind or _detect_kind(normalized, path)
    statement = Statement(
        kind=detected,
        path=path,
        razon_social=meta["razon_social"],
        expediente=meta["expediente"],
        ruc=meta["ruc"],
        year=meta["year"],
    )
    for raw in normalized.splitlines():
        candidate = raw.replace("\x0c", "").strip()
        parsed = _parse_account_line(candidate)
        if parsed is None:
            continue
        if parsed.code in statement.by_code:
            continue
        statement.lines.append(parsed)
        statement.by_code[parsed.code] = parsed
    if statement.kind == "unknown":
        statement.kind = _detect_kind_from_codes(statement)
    return statement


def parse_statement_file(path: Path, *, kind: str | None = None) -> Statement:
    text = path.read_text(encoding="utf-8", errors="replace")
    guessed = kind or _kind_from_name(path.name)
    return parse_statement(text, path=path, kind=guessed)


def frontier_sum(amounts: dict[str, float], codes: list[str] | set[str]) -> float:
    """Sum ``codes`` without double-counting a parent and its children.

    A code is dropped when another selected code is a longer code that starts
    with it. ``50102`` is omitted when ``5010201`` is also selected; the parent
    is kept when the statement only publishes the rollup.
    """
    selected = [str(code) for code in codes if str(code) in amounts]
    kept: list[str] = []
    for code in selected:
        if any(other != code and other.startswith(code) for other in selected):
            continue
        kept.append(code)
    return sum(amounts[code] for code in kept)


def codes_under(amounts: dict[str, float], roots: list[str]) -> list[str]:
    """Codes equal to a root or beginning with a root (children)."""
    found: list[str] = []
    for code in amounts:
        if any(code == root or code.startswith(root) for root in roots):
            found.append(code)
    return found


def _parse_account_line(candidate: str) -> AccountLine | None:
    match = _LINE_RE.match(candidate)
    if match is None:
        return None
    label = " ".join(match.group("label").split())
    code = match.group("code")
    raw_amount = match.group("amount")
    if not label or not _LETTER_RE.search(label):
        return None
    if len(code) > 16:
        return None
    return AccountLine(code=code, label=label, amount=float(raw_amount), raw_amount=raw_amount)


def _metadata(text: str) -> dict[str, str | int | None]:
    razon = ""
    expediente: int | None = None
    ruc = ""
    year: int | None = None
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not razon:
            match = _META_RE["razon_social"].search(line)
            if match:
                razon = match.group(1).strip()
        if expediente is None:
            match = _META_RE["expediente"].search(line)
            if match:
                expediente = int(match.group(1))
        if not ruc:
            match = _META_RE["ruc"].search(line)
            if match:
                ruc = match.group(1)
        if year is None:
            match = _YEAR_LINE_RE.search(line)
            if match:
                year = int(match.group(1))
    return {
        "razon_social": razon,
        "expediente": expediente,
        "ruc": ruc,
        "year": year,
    }


def _detect_kind(text: str, path: Path | None) -> str:
    folded = text.upper()
    if "SITUACIÓN FINANCIERA" in folded or "SITUACION FINANCIERA" in folded:
        return "balance"
    if "RESULTADO INTEGRAL" in folded or "ESTADO DE RESULTADO" in folded:
        return "income"
    if path is not None:
        named = _kind_from_name(path.name)
        if named:
            return named
    return "unknown"


def _detect_kind_from_codes(statement: Statement) -> str:
    if "1" in statement.by_code and "401" not in statement.by_code:
        return "balance"
    if "401" in statement.by_code or "707" in statement.by_code:
        return "income"
    return "unknown"


def _kind_from_name(name: str) -> str | None:
    lowered = name.casefold()
    if "balance" in lowered or "situacion" in lowered or "situación" in lowered:
        return "balance"
    if "income" in lowered or "resultado" in lowered:
        return "income"
    return None
