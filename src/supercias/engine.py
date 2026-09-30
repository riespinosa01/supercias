"""Queryable company view over a ranking export and parsed EEFF files."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from supercias.analysis import (
    balance_snapshot,
    cross_check,
    ebitda_proxy,
    income_snapshot,
    payroll_rollup,
    ratio,
    red_flags,
)
from supercias.columns import fold
from supercias.eeff import Statement, parse_statement_file

RANKING_ENV = "SUPERCIAS_RANKING"


class LookupError(ValueError):
    """The query did not resolve to one company."""


@dataclass
class CompanyRecord:
    ruc: str
    expediente: int | None
    name: str
    ranking_name: str
    eeff_name: str
    years: list[int] = field(default_factory=list)


@dataclass
class CompanyView:
    """One company-year, ready to render."""

    ruc: str
    year: int | None
    record: CompanyRecord
    brief_data: dict[str, Any]

    def brief(self) -> dict[str, Any]:
        return self.brief_data

    def render(self) -> str:
        return render_brief(self.brief_data)

    def to_json(self) -> str:
        return json.dumps(self.brief_data, ensure_ascii=False, indent=2)


class CompanyEngine:
    """Look up a company by RUC, expediente, or name.

    Ranking rows come from a prior ``python -m supercias`` CSV. EEFF text files
    come from ``data/eeff`` and the bundled ``fixtures/eeff`` samples.
    """

    def __init__(
        self,
        ranking_path: Path | str | None = None,
        eeff_dirs: list[Path] | None = None,
    ) -> None:
        self.ranking_path = resolve_ranking_path(ranking_path)
        self.eeff_dirs = eeff_dirs if eeff_dirs is not None else default_eeff_dirs()
        self.ranking = _read_ranking(self.ranking_path)
        self.statements = _load_statements(self.eeff_dirs)
        self.records = _build_records(self.ranking, self.statements)

    def lookup(
        self,
        *,
        ruc: str | None = None,
        expediente: int | str | None = None,
        name: str | None = None,
        year: int | None = None,
    ) -> CompanyView:
        record = self._resolve(ruc=ruc, expediente=expediente, name=name)
        chosen = _choose_year(record, self.statements, year)
        return CompanyView(
            ruc=record.ruc,
            year=chosen,
            record=record,
            brief_data=self._brief(record, chosen),
        )

    def _resolve(
        self,
        *,
        ruc: str | None,
        expediente: int | str | None,
        name: str | None,
    ) -> CompanyRecord:
        if ruc:
            key = _normalize_ruc(ruc)
            found = [record for record in self.records if record.ruc == key]
            if not found:
                raise LookupError(f"No company with RUC {key}.")
            return found[0]
        if expediente is not None and str(expediente).strip():
            number = int(str(expediente).strip())
            found = [record for record in self.records if record.expediente == number]
            if not found:
                raise LookupError(f"No company with expediente {number}.")
            if len(found) > 1:
                raise LookupError(_candidate_message(found))
            return found[0]
        if name and name.strip():
            needle = fold(name)
            found = [
                record
                for record in self.records
                if needle in fold(record.name) or needle in fold(record.ranking_name) or needle in fold(record.eeff_name)
            ]
            if not found:
                raise LookupError(f"No company matching {name.strip()!r}.")
            rucs = {record.ruc for record in found}
            if len(rucs) > 1:
                raise LookupError(_candidate_message(found))
            return found[0]
        raise LookupError("Pass a RUC, expediente, or name.")

    def _brief(self, record: CompanyRecord, year: int | None) -> dict[str, Any]:
        income = _statement(self.statements, record.ruc, year, "income")
        balance = _statement(self.statements, record.ruc, year, "balance")
        prior_income = _statement(self.statements, record.ruc, None if year is None else year - 1, "income")
        ranking_row = _ranking_row(self.ranking, record.ruc, year)
        ranking = _ranking_payload(ranking_row)
        income_totals = income_snapshot(income)
        balance_totals = balance_snapshot(balance)
        payroll = payroll_rollup(income)
        ingresos = income_totals.get("401")
        if ingresos is None and ranking is not None:
            ingresos = ranking.get("ingresos_totales")
        payroll["pct_of_ingresos"] = ratio(payroll["total"], ingresos)
        payroll["nomina_pct_of_ingresos"] = ratio(payroll["nomina_ex_honorarios"], ingresos)
        employees = None if ranking is None else ranking.get("n_empleados")
        payroll["per_employee"] = ratio(payroll["nomina_ex_honorarios"], employees)
        payroll["yoy"] = _payroll_yoy(payroll, prior_income, income_totals.get("401"))
        ebitda = ebitda_proxy(income)
        checks = cross_check(
            ranking_ingresos=None if ranking is None else ranking.get("ingresos_totales"),
            ranking_utilidad=None if ranking is None else ranking.get("utilidad_neta"),
            eeff_ingresos=income_totals.get("401"),
            eeff_utilidad=income_totals.get("707"),
        )
        display_name = record.eeff_name or record.ranking_name or record.name
        payload: dict[str, Any] = {
            "company": {
                "name": display_name,
                "ruc": record.ruc,
                "expediente": record.expediente,
                "year": year,
                "eeff_years": record.years,
            },
            "ranking": ranking,
            "ranking_source": None if self.ranking_path is None else str(self.ranking_path),
            "balance": None if balance is None else balance.path.name if balance.path else "balance",
            "income": None if income is None else income.path.name if income.path else "income",
            "balance_totals": balance_totals,
            "income_totals": income_totals,
            "margins": {
                "gross": ratio(income_totals.get("402"), income_totals.get("401")),
                "net": ratio(income_totals.get("707"), income_totals.get("401")),
            },
            "payroll": payroll,
            "ebitda": ebitda,
            "cross_check": checks,
            "red_flags": [],
        }
        payload["red_flags"] = red_flags(payload)
        return payload


def default_eeff_dirs() -> list[Path]:
    """``data/eeff`` wins over the bundled samples when both contain a filing."""
    roots: list[Path] = []
    candidates = [
        Path("data") / "eeff",
        Path("fixtures") / "eeff",
        Path(__file__).resolve().parents[2] / "fixtures" / "eeff",
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not candidate.is_dir():
            continue
        seen.add(resolved)
        roots.append(candidate)
    return roots


def resolve_ranking_path(explicit: Path | str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"Ranking CSV not found: {path}")
        return path
    env = os.environ.get(RANKING_ENV, "").strip()
    if env:
        path = Path(env)
        if not path.is_file():
            raise FileNotFoundError(f"{RANKING_ENV} does not point at a file: {path}")
        return path
    for candidate in (Path("data") / "supercias.csv", Path("fixtures") / "ranking_sample.csv", Path(__file__).resolve().parents[2] / "fixtures" / "ranking_sample.csv"):
        if candidate.is_file():
            return candidate
    return None


def seed_eeff(dest: Path, source: Path | None = None) -> int:
    """Copy bundled sample statements into ``dest/{ruc}/{year}_{kind}.txt``."""
    origin = source or _bundled_fixtures()
    if origin is None or not origin.is_dir():
        raise FileNotFoundError("No fixtures/eeff directory to copy.")
    copied = 0
    for path in sorted(origin.rglob("*.txt")):
        relative = path.relative_to(origin)
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied += 1
    return copied


def render_brief(brief: dict[str, Any]) -> str:
    company = brief["company"]
    lines = [
        company["name"] or "(unknown name)",
        f"RUC {company['ruc'] or '—'}    Expediente {company['expediente'] if company['expediente'] is not None else '—'}    Year {company['year'] if company['year'] is not None else '—'}",
        "",
        "Ranking",
    ]
    ranking = brief.get("ranking")
    if not ranking:
        lines.append("  No ranking row for this year.")
    else:
        lines.extend(
            [
                f"  Year {_fmt_int(ranking.get('anio'))}",
                f"  Ingresos totales {_fmt(ranking.get('ingresos_totales'))}",
                f"  Utilidad neta {_fmt(ranking.get('utilidad_neta'))}",
                f"  Margen neto {_fmt_pct(ranking.get('margen_neto'))}",
                f"  Deuda total {_fmt(ranking.get('deuda_total'))}",
                f"  Deuda corto plazo {_fmt(ranking.get('deuda_total_c_plazo'))}",
                f"  Empleados {_fmt_int(ranking.get('n_empleados'))}",
                f"  Gastos financieros {_fmt(ranking.get('gastos_financieros'))}",
                f"  CIIU {ranking.get('ciiu_n1') or '—'} / {ranking.get('ciiu_n6') or '—'}",
                f"  Lugar {ranking.get('provincia') or '—'} / {ranking.get('ciudad') or '—'}",
            ]
        )
    totals = brief.get("balance_totals") or {}
    lines.extend(
        [
            "",
            "Balance sheet",
            f"  Activo (1) {_fmt(totals.get('1'))}",
            f"  Pasivo (2) {_fmt(totals.get('2'))}",
            f"  Patrimonio (3) {_fmt(totals.get('3'))}",
            f"  Activo - pasivo - patrimonio {_fmt(totals.get('identity_gap'))}",
        ]
    )
    income = brief.get("income_totals") or {}
    margins = brief.get("margins") or {}
    lines.extend(
        [
            "",
            "Income statement",
            f"  Ingresos (401) {_fmt(income.get('401'))}",
            f"  Costo (501) {_fmt(income.get('501'))}",
            f"  Ganancia bruta (402) {_fmt(income.get('402'))}",
            f"  Gastos (502) {_fmt(income.get('502'))}",
            f"  Gastos de venta (50201) {_fmt(income.get('50201'))}",
            f"  Gastos administrativos (50202) {_fmt(income.get('50202'))}",
            f"  Gastos financieros (50203) {_fmt(income.get('50203'))}",
            f"  Utilidad neta (707) {_fmt(income.get('707'))}",
            f"  Margen bruto {_fmt_pct(margins.get('gross'))}",
            f"  Margen neto {_fmt_pct(margins.get('net'))}",
        ]
    )
    payroll = brief.get("payroll") or {}
    lines.extend(
        [
            "",
            "Payroll",
            f"  Mano de obra directa (50102*) {_fmt(payroll.get('direct_labor'))}",
            f"  Mano de obra indirecta (50103*) {_fmt(payroll.get('indirect_labor'))}",
            f"  Ventas (5020101-5020106) {_fmt(payroll.get('selling'))}",
            f"  Administración (5020201-5020206) {_fmt(payroll.get('admin'))}",
            f"  Payroll cash total {_fmt(payroll.get('total'))}",
            f"  Honorarios inside that total (5020105/5020205) {_fmt(payroll.get('honorarios'))}",
            f"  Nómina excluding honorarios {_fmt(payroll.get('nomina_ex_honorarios'))}",
            f"  Payroll cash / ingresos {_fmt_pct(payroll.get('pct_of_ingresos'))}",
            f"  Nómina ex honorarios / ingresos {_fmt_pct(payroll.get('nomina_pct_of_ingresos'))}",
            f"  Nómina ex honorarios / empleado {_fmt(payroll.get('per_employee'))}",
        ]
    )
    yoy = payroll.get("yoy")
    if not yoy:
        lines.append("  Prior EEFF year: not loaded")
    else:
        lines.append(
            "  Vs prior year: nómina "
            f"{_fmt_pct(yoy.get('nomina_growth'))}, ingresos {_fmt_pct(yoy.get('ingresos_growth'))}"
        )
    ebitda = brief.get("ebitda") or {}
    lines.extend(
        [
            "",
            "EBITDA proxy",
            f"  Operating income (402 - 502 + 50203 + 403) {_fmt(ebitda.get('operating_income'))}",
            f"  D&A add-back {_fmt(ebitda.get('depreciation_amortization'))}",
            f"  EBITDA proxy {_fmt(ebitda.get('proxy'))}",
        ]
    )
    if ebitda.get("da_missing"):
        lines.append("  D&A lines were not in the statement. The proxy is operating income only.")
    lines.extend(["", "Cross-check"])
    for item in brief.get("cross_check") or []:
        lines.append(f"  {item['message']}")
    lines.extend(["", "Red flags"])
    flags = brief.get("red_flags") or []
    if not flags:
        lines.append("  None.")
    else:
        lines.extend(f"  - {flag}" for flag in flags)
    lines.extend(["", "Assumptions", f"  {ebitda.get('assumptions', '')}"])
    return "\n".join(lines)


def _bundled_fixtures() -> Path | None:
    for candidate in (Path("fixtures") / "eeff", Path(__file__).resolve().parents[2] / "fixtures" / "eeff"):
        if candidate.is_dir():
            return candidate
    return None


def _load_statements(roots: list[Path]) -> dict[tuple[str, int, str], Statement]:
    loaded: dict[tuple[str, int, str], Statement] = {}
    # Later roots fill gaps only. ``data/eeff`` is listed first.
    for root in reversed(roots):
        for path in sorted(root.rglob("*.txt")):
            statement = parse_statement_file(path)
            if not statement.ruc or statement.year is None or statement.kind not in {"balance", "income"}:
                continue
            loaded[(statement.ruc, statement.year, statement.kind)] = statement
    return loaded


def _build_records(ranking: pd.DataFrame | None, statements: dict[tuple[str, int, str], Statement]) -> list[CompanyRecord]:
    by_ruc: dict[str, CompanyRecord] = {}
    for (ruc, year, _kind), statement in statements.items():
        record = by_ruc.get(ruc)
        if record is None:
            record = CompanyRecord(
                ruc=ruc,
                expediente=statement.expediente,
                name=statement.razon_social,
                ranking_name="",
                eeff_name=statement.razon_social,
            )
            by_ruc[ruc] = record
        if year not in record.years:
            record.years.append(year)
        if statement.expediente is not None:
            record.expediente = statement.expediente
        if statement.razon_social:
            record.eeff_name = statement.razon_social
            record.name = statement.razon_social
    if ranking is not None and not ranking.empty and "RUC" in ranking.columns:
        for ruc, group in ranking.groupby("RUC", dropna=False):
            key = _normalize_ruc(ruc)
            if not key:
                continue
            row = group.sort_values("anio").iloc[-1] if "anio" in group.columns else group.iloc[-1]
            record = by_ruc.get(key)
            ranking_name = str(row.get("NOMBRE") or "").strip()
            expediente = _optional_int(row.get("EXPEDIENTE"))
            if record is None:
                record = CompanyRecord(
                    ruc=key,
                    expediente=expediente,
                    name=ranking_name,
                    ranking_name=ranking_name,
                    eeff_name="",
                )
                by_ruc[key] = record
            record.ranking_name = ranking_name or record.ranking_name
            if not record.name:
                record.name = ranking_name
            if record.expediente is None:
                record.expediente = expediente
    for record in by_ruc.values():
        record.years.sort()
    return sorted(by_ruc.values(), key=lambda item: fold(item.name or item.ruc))


def _read_ranking(path: Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    frame = pd.read_csv(path, dtype={"RUC": str}, low_memory=False)
    frame.columns = [str(column).strip() for column in frame.columns]
    if "RUC" in frame.columns:
        frame["RUC"] = frame["RUC"].map(_normalize_ruc)
    return frame


def _ranking_row(frame: pd.DataFrame | None, ruc: str, year: int | None) -> pd.Series | None:
    if frame is None or frame.empty or "RUC" not in frame.columns:
        return None
    rows = frame.loc[frame["RUC"] == ruc]
    if rows.empty:
        return None
    if year is not None and "anio" in rows.columns:
        matched = rows.loc[pd.to_numeric(rows["anio"], errors="coerce") == year]
        if not matched.empty:
            return matched.iloc[-1]
    if "anio" in rows.columns:
        return rows.sort_values("anio").iloc[-1]
    return rows.iloc[-1]


def _ranking_payload(row: pd.Series | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "anio": _optional_int(row.get("anio")),
        "nombre": _text(row.get("NOMBRE")),
        "ingresos_totales": _optional_float(row.get("ingresos_totales")),
        "utilidad_neta": _optional_float(row.get("utilidad_neta")),
        "margen_neto": _optional_float(row.get("margen_neto")),
        "deuda_total": _optional_float(row.get("deuda_total")),
        "deuda_total_c_plazo": _optional_float(row.get("deuda_total_c_plazo")),
        "n_empleados": _optional_float(row.get("n_empleados")),
        "gastos_financieros": _optional_float(row.get("gastos_financieros")),
        "costos_ventas_prod": _optional_float(row.get("costos_ventas_prod")),
        "gastos_admin_ventas": _optional_float(row.get("gastos_admin_ventas")),
        "ciiu_n1": _text(row.get("CIIU NIVEL 1")),
        "ciiu_n6": _text(row.get("CIIU NIVEL 6")),
        "provincia": _text(row.get("PROVINCIA")),
        "ciudad": _text(row.get("CIUDAD")),
        "tipo": _text(row.get("TIPO")),
    }


def _statement(
    statements: dict[tuple[str, int, str], Statement],
    ruc: str,
    year: int | None,
    kind: str,
) -> Statement | None:
    if year is None:
        return None
    return statements.get((ruc, year, kind))


def _choose_year(
    record: CompanyRecord,
    statements: dict[tuple[str, int, str], Statement],
    requested: int | None,
) -> int | None:
    if requested is not None:
        return requested
    if record.years:
        return record.years[-1]
    return None


def _payroll_yoy(
    current: dict[str, Any],
    prior_income: Statement | None,
    current_ingresos: float | None,
) -> dict[str, float | None] | None:
    if prior_income is None:
        return None
    prior = payroll_rollup(prior_income)
    prior_totals = income_snapshot(prior_income)
    return {
        "prior_year": prior_income.year,
        "nomina_growth": _growth(prior["nomina_ex_honorarios"], current.get("nomina_ex_honorarios")),
        "ingresos_growth": _growth(prior_totals.get("401"), current_ingresos),
    }


def _growth(previous: float | None, current: float | None) -> float | None:
    if previous in (None, 0) or current is None:
        return None
    return (current - previous) / abs(previous)


def _candidate_message(records: list[CompanyRecord]) -> str:
    lines = ["More than one company matched:"]
    for record in records[:12]:
        lines.append(f"  {record.ruc}  {record.expediente or '—'}  {record.name}")
    return "\n".join(lines)


def _normalize_ruc(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _optional_int(value: object) -> int | None:
    number = _optional_float(value)
    if number is None:
        return None
    return int(number)


def _optional_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return str(value).strip()


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_int(value: object) -> str:
    if value is None:
        return "—"
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "—"
