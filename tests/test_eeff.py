"""Parser and company-query tests on the bundled Supercias EEFF samples."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from supercias.cli import main
from supercias.eeff import frontier_sum, parse_statement_file
from supercias.engine import CompanyEngine

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "eeff"
RANKING = ROOT / "fixtures" / "ranking_sample.csv"


def _close(actual: float | None, expected: float, places: float = 0.02) -> None:
    if actual is None:
        raise AssertionError(f"missing amount, expected {expected}")
    if not math.isclose(actual, expected, abs_tol=places):
        raise AssertionError(f"{actual} != {expected}")


class ParserTests(unittest.TestCase):
    def test_escollanos_income_key_codes(self) -> None:
        statement = parse_statement_file(FIXTURES / "1792319900001" / "2025_income.txt")
        self.assertEqual(statement.kind, "income")
        self.assertEqual(statement.ruc, "1792319900001")
        self.assertEqual(statement.expediente, 139571)
        self.assertEqual(statement.year, 2025)
        self.assertIn("ESCOLLANOS", statement.razon_social)
        _close(statement.amount("401"), 4659773.88)
        _close(statement.amount("501"), 1004327.18)
        _close(statement.amount("402"), 3655446.70)
        _close(statement.amount("502"), 3248612.67)
        _close(statement.amount("707"), 110759.56)
        _close(statement.amount("5020101"), 381511.66)
        _close(statement.amount("5020103"), 41960.30)
        _close(statement.amount("5020201"), 678389.89)
        _close(statement.amount("50102"), 0.0)
        self.assertGreater(len(statement.lines), 200)

    def test_escollanos_balance_totals(self) -> None:
        statement = parse_statement_file(FIXTURES / "1792319900001" / "2025_balance.txt")
        self.assertEqual(statement.kind, "balance")
        _close(statement.amount("1"), 26958.76)
        _close(statement.amount("2"), 25128.51)
        _close(statement.amount("3"), 1830.25)
        _close(statement.amount("101"), 22183.21)
        _close(statement.amount("10101"), 7061.88)

    def test_ecuacobranzas_direct_labor_is_not_double_counted(self) -> None:
        statement = parse_statement_file(FIXTURES / "1792191858001" / "2025_income.txt")
        _close(statement.amount("401"), 1264891.81)
        _close(statement.amount("707"), 3418.71)
        _close(statement.amount("50102"), 217235.44)
        amounts = {line.code: line.amount for line in statement.lines}
        labor_codes = [code for code in amounts if code.startswith("50102")]
        rolled = frontier_sum(amounts, labor_codes)
        _close(rolled, 217235.44)
        self.assertLess(rolled, statement.amount("50102") + statement.amount("5010201"))

    def test_integer_amounts_and_long_decimals(self) -> None:
        euro = parse_statement_file(FIXTURES / "1792209366001" / "2024_income.txt")
        _close(euro.amount("5020109"), 38280.0)
        _close(euro.amount("5020208"), 266.0)
        abro = parse_statement_file(FIXTURES / "1791316347001" / "2025_income.txt")
        _close(abro.amount("401"), 18598252.8900001)
        _close(abro.amount("1") if False else abro.amount("707"), 709187.47)

    def test_negative_discount(self) -> None:
        statement = parse_statement_file(FIXTURES / "1792319900001" / "2025_income.txt")
        self.assertLess(statement.amount("40112"), 0)
        _close(statement.amount("40112"), -30864.64)


class QueryTests(unittest.TestCase):
    def test_escollanos_brief_combines_ranking_and_eeff(self) -> None:
        engine = CompanyEngine(ranking_path=RANKING, eeff_dirs=[FIXTURES])
        view = engine.lookup(name="escollanos")
        brief = view.brief()
        self.assertEqual(brief["company"]["ruc"], "1792319900001")
        self.assertEqual(brief["company"]["year"], 2025)
        self.assertEqual(brief["company"]["expediente"], 139571)
        _close(brief["ranking"]["ingresos_totales"], 4659773.88)
        _close(brief["ranking"]["utilidad_neta"], 110759.56)
        _close(brief["ranking"]["n_empleados"], 70)
        _close(brief["income_totals"]["401"], 4659773.88)
        _close(brief["income_totals"]["707"], 110759.56)
        _close(brief["balance_totals"]["1"], 26958.76)
        self.assertFalse(brief["cross_check"][0]["material"])
        self.assertFalse(brief["cross_check"][1]["material"])
        self.assertGreater(brief["payroll"]["total"], 1_000_000)
        self.assertGreater(brief["payroll"]["nomina_ex_honorarios"], 1_000_000)
        self.assertLess(
            brief["payroll"]["nomina_ex_honorarios"],
            brief["payroll"]["total"],
        )
        self.assertIsNotNone(brief["payroll"]["per_employee"])
        self.assertFalse(brief["ebitda"]["da_missing"])
        self.assertGreater(brief["ebitda"]["proxy"], brief["ebitda"]["operating_income"])
        text = view.render()
        self.assertIn("ESCOLLANOS", text)
        self.assertIn("Payroll", text)
        self.assertIn("EBITDA proxy", text)
        self.assertIn("707", text)

    def test_euroinstruments_flags_ingresos_gap(self) -> None:
        engine = CompanyEngine(ranking_path=RANKING, eeff_dirs=[FIXTURES])
        brief = engine.lookup(ruc="1792209366001", year=2025).brief()
        ingresos = next(item for item in brief["cross_check"] if item["eeff_code"] == "401")
        self.assertTrue(ingresos["material"])
        self.assertIsNotNone(brief["payroll"]["yoy"])
        self.assertEqual(brief["payroll"]["yoy"]["prior_year"], 2024)

    def test_query_cli_json(self) -> None:
        from io import StringIO
        from unittest import mock

        buffer = StringIO()
        with mock.patch("sys.stdout", buffer):
            status = main(
                [
                    "query",
                    "--name",
                    "ECUACOBRANZAS",
                    "--ranking",
                    str(RANKING),
                    "--eeff-dir",
                    str(FIXTURES),
                    "--json",
                ]
            )
        self.assertEqual(status, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["company"]["ruc"], "1792191858001")
        self.assertAlmostEqual(payload["income_totals"]["501"], 217235.44, places=2)
        self.assertTrue(any("Nómina" in flag for flag in payload["red_flags"]))

    def test_cli_renders_escollanos(self) -> None:
        from io import StringIO
        from unittest import mock

        buffer = StringIO()
        with mock.patch("sys.stdout", buffer):
            status = main(
                [
                    "query",
                    "--expediente",
                    "139571",
                    "--ranking",
                    str(RANKING),
                    "--eeff-dir",
                    str(FIXTURES),
                ]
            )
        self.assertEqual(status, 0)
        text = buffer.getvalue()
        self.assertIn("110,759.56", text)
        self.assertIn("26,958.76", text)
        payload = json.loads(
            CompanyEngine(ranking_path=RANKING, eeff_dirs=[FIXTURES])
            .lookup(expediente=139571)
            .to_json()
        )
        self.assertEqual(payload["company"]["ruc"], "1792319900001")


if __name__ == "__main__":
    unittest.main()
