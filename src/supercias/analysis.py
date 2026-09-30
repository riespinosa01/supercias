"""Payroll, EBITDA proxy, and ranking cross-checks from parsed EEFF lines."""

from __future__ import annotations

from typing import Any

from supercias.eeff import Statement, codes_under, frontier_sum

# Selling and admin payroll block from the Supercias chart.
# 5020105 / 5020205 are honorarios, comisiones y dietas. They sit inside the
# numeric range 5020101–5020106 but are not nómina. Both totals are reported.
SELLING_PAYROLL = [f"502010{digit}" for digit in range(1, 7)]
ADMIN_PAYROLL = [f"502020{digit}" for digit in range(1, 7)]
HONORARIOS = ["5020105", "5020205"]
NOMINA_SELLING = ["5020101", "5020102", "5020103", "5020104", "5020106"]
NOMINA_ADMIN = ["5020201", "5020202", "5020203", "5020204", "5020206"]

# Depreciation and amortization only. Deterioro and other 50104 costs are not D&A.
DA_ROOTS = ["5010401", "5020120", "5020121", "5020221", "5020222"]

# A ranking/EEFF difference is material when it is at least USD 1,000 and
# at least 0.5% of the ranking figure.
MATERIAL_ABS = 1_000.0
MATERIAL_REL = 0.005

ASSUMPTIONS = (
    "Operating income proxy = ganancia bruta (402) - gastos (502) + gastos financieros (50203) "
    "+ otros ingresos (403). Adding 50203 back removes interest from opex. "
    "EBITDA proxy = that operating income + depreciation and amortization found on "
    "5010401, 5020120/5020121 (selling) and 5020221/5020222 (admin), using child lines "
    "when the statement publishes them so a parent is not added twice. "
    "Deterioro is not treated as D&A. Missing D&A codes are not filled with zero. "
    "Payroll cash uses 50102*, 50103*, and 5020101–5020106 / 5020201–5020206. "
    "Nómina excluding honorarios drops 5020105 and 5020205 (honorarios, comisiones y dietas)."
)


def amount_map(statement: Statement | None) -> dict[str, float]:
    if statement is None:
        return {}
    return {line.code: line.amount for line in statement.lines}


def payroll_rollup(income: Statement | None) -> dict[str, Any]:
    amounts = amount_map(income)
    direct = _family(amounts, ["50102"])
    indirect = _family(amounts, ["50103"])
    selling = _family(amounts, SELLING_PAYROLL)
    admin = _family(amounts, ADMIN_PAYROLL)
    honorarios = _family(amounts, HONORARIOS)
    nomina_selling = _family(amounts, NOMINA_SELLING)
    nomina_admin = _family(amounts, NOMINA_ADMIN)
    cash = direct + indirect + selling + admin
    nomina = direct + indirect + nomina_selling + nomina_admin
    components = []
    for code in [
        *codes_under(amounts, ["50102", "50103"]),
        *codes_under(amounts, SELLING_PAYROLL + ADMIN_PAYROLL),
    ]:
        if len(code) < 6:
            continue
        line = income.by_code[code] if income is not None else None
        components.append(
            {
                "code": code,
                "label": None if line is None else line.label,
                "amount": _round(amounts[code]),
            }
        )
    components.sort(key=lambda item: item["code"])
    return {
        "direct_labor": _round(direct),
        "indirect_labor": _round(indirect),
        "selling": _round(selling),
        "admin": _round(admin),
        "honorarios": _round(honorarios),
        "total": _round(cash),
        "nomina_ex_honorarios": _round(nomina),
        "components": [item for item in components if abs(item["amount"]) > 0],
    }


def ebitda_proxy(income: Statement | None) -> dict[str, Any]:
    amounts = amount_map(income)
    gross = amounts.get("402")
    opex = amounts.get("502")
    financial = amounts.get("50203")
    other_income = amounts.get("403")
    pieces = {
        "402": gross,
        "502": opex,
        "50203": financial,
        "403": other_income,
    }
    operating = None
    if gross is not None and opex is not None:
        operating = gross - opex + (financial or 0.0) + (other_income or 0.0)
    da_codes = codes_under(amounts, DA_ROOTS)
    da_missing = not da_codes
    used = _frontier_codes(da_codes)
    depreciation = None if da_missing else sum(amounts[code] for code in used)
    proxy = None
    if operating is not None:
        proxy = operating if depreciation is None else operating + depreciation
    found = []
    if income is not None:
        for code in used:
            found.append(
                {
                    "code": code,
                    "label": income.by_code[code].label,
                    "amount": _round(amounts[code]),
                }
            )
    return {
        "gross_profit": None if gross is None else _round(gross),
        "opex_502": None if opex is None else _round(opex),
        "financial_expenses_50203": None if financial is None else _round(financial),
        "other_income_403": None if other_income is None else _round(other_income),
        "operating_income": None if operating is None else _round(operating),
        "depreciation_amortization": None if depreciation is None else _round(depreciation),
        "da_missing": da_missing,
        "da_lines": [item for item in found if abs(item["amount"]) > 0 or item["code"] in DA_ROOTS],
        "proxy": None if proxy is None else _round(proxy),
        "inputs_present": {key: value is not None for key, value in pieces.items()},
        "assumptions": ASSUMPTIONS,
    }


def income_snapshot(income: Statement | None) -> dict[str, float | None]:
    amounts = amount_map(income)
    return {
        "401": _maybe(amounts.get("401")),
        "501": _maybe(amounts.get("501")),
        "402": _maybe(amounts.get("402")),
        "502": _maybe(amounts.get("502")),
        "50201": _maybe(amounts.get("50201")),
        "50202": _maybe(amounts.get("50202")),
        "50203": _maybe(amounts.get("50203")),
        "707": _maybe(amounts.get("707")),
    }


def balance_snapshot(balance: Statement | None) -> dict[str, float | None]:
    amounts = amount_map(balance)
    activo = amounts.get("1")
    pasivo = amounts.get("2")
    patrimonio = amounts.get("3")
    gap = None
    if activo is not None and pasivo is not None and patrimonio is not None:
        gap = _round(activo - pasivo - patrimonio)
    return {
        "1": _maybe(activo),
        "2": _maybe(pasivo),
        "3": _maybe(patrimonio),
        "identity_gap": gap,
    }


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def cross_check(
    *,
    ranking_ingresos: float | None,
    ranking_utilidad: float | None,
    eeff_ingresos: float | None,
    eeff_utilidad: float | None,
) -> list[dict[str, Any]]:
    return [
        _gap("ingresos_totales", "401", ranking_ingresos, eeff_ingresos),
        _gap("utilidad_neta", "707", ranking_utilidad, eeff_utilidad),
    ]


def red_flags(brief: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    ranking = brief.get("ranking")
    if not ranking:
        flags.append("No ranking row for this RUC and year.")
    if brief.get("income") is None:
        flags.append("No income statement loaded for this year.")
    if brief.get("balance") is None:
        flags.append("No balance sheet loaded for this year.")
    balance = brief.get("balance_totals") or {}
    gap = balance.get("identity_gap")
    if gap is not None and abs(gap) > 1:
        flags.append(
            f"Balance sheet does not add up: activo - pasivo - patrimonio = {gap:,.2f}."
        )
    for item in brief.get("cross_check") or []:
        if item.get("material"):
            flags.append(item["message"])
    payroll = brief.get("payroll") or {}
    ingresos = (brief.get("income_totals") or {}).get("401")
    nomina = payroll.get("nomina_ex_honorarios")
    share = ratio(nomina, ingresos)
    if share is not None and share >= 0.35:
        flags.append(
            f"Nómina excluding honorarios is {share:.1%} of ingresos (401)."
        )
    honorarios = payroll.get("honorarios")
    if honorarios is not None and ingresos not in (None, 0) and honorarios / ingresos >= 0.15:
        flags.append(
            f"Honorarios (5020105/5020205) are {honorarios / ingresos:.1%} of ingresos (401)."
        )
    yoy = payroll.get("yoy")
    if yoy and yoy.get("nomina_growth") is not None and yoy.get("ingresos_growth") is not None:
        if yoy["nomina_growth"] >= 0.15 and yoy["ingresos_growth"] < 0.05:
            flags.append(
                "Nómina grew "
                f"{yoy['nomina_growth']:.1%} while ingresos (401) grew {yoy['ingresos_growth']:.1%} "
                "versus the prior EEFF year."
            )
    ebitda = brief.get("ebitda") or {}
    if brief.get("income") is not None and ebitda.get("da_missing"):
        flags.append("Depreciation and amortization lines were not in the income statement.")
    return flags


def _family(amounts: dict[str, float], roots: list[str]) -> float:
    return frontier_sum(amounts, codes_under(amounts, roots))


def _gap(ranking_name: str, eeff_code: str, ranking_value: float | None, eeff_value: float | None) -> dict[str, Any]:
    if ranking_value is None or eeff_value is None:
        return {
            "ranking_field": ranking_name,
            "eeff_code": eeff_code,
            "ranking": None if ranking_value is None else _round(ranking_value),
            "eeff": None if eeff_value is None else _round(eeff_value),
            "difference": None,
            "relative": None,
            "material": False,
            "message": f"{ranking_name} vs {eeff_code}: one side is missing.",
        }
    difference = eeff_value - ranking_value
    base = abs(ranking_value) if ranking_value else abs(eeff_value)
    relative = abs(difference) / base if base else 0.0
    material = abs(difference) >= MATERIAL_ABS and relative >= MATERIAL_REL
    message = (
        f"{ranking_name} vs {eeff_code}: ranking {_round(ranking_value):,.2f} vs "
        f"EEFF {_round(eeff_value):,.2f} (difference {_round(difference):,.2f}, {relative:.2%})."
    )
    if material:
        message = "Material gap: " + message
    return {
        "ranking_field": ranking_name,
        "eeff_code": eeff_code,
        "ranking": _round(ranking_value),
        "eeff": _round(eeff_value),
        "difference": _round(difference),
        "relative": relative,
        "material": material,
        "message": message,
    }


def _frontier_codes(codes: list[str]) -> list[str]:
    kept: list[str] = []
    for code in codes:
        if any(other != code and other.startswith(code) for other in codes):
            continue
        kept.append(code)
    return sorted(kept)


def _round(value: float) -> float:
    rounded = round(float(value) + 0.0, 2)
    if rounded == 0:
        return 0.0
    return rounded


def _maybe(value: float | None) -> float | None:
    if value is None:
        return None
    return _round(value)
