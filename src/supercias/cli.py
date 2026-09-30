"""Command line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import requests

from supercias import __version__
from supercias.columns import (
    DEFAULT_COLUMNS,
    DIRECTORIO_COLUMNS,
    MARGEN_NETO,
    RANKING_COLUMNS,
    ColumnError,
)
from supercias.pipeline import DEFAULT_OUTPUT, export

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="supercias",
        description=(
            "Download the Supercias company directory and financial ranking, "
            "keep active companies in the latest balance cohort, and write a CSV. "
            "Query one company with: python -m supercias query --help"
        ),
    )
    parser.add_argument("--version", action="version", version=f"supercias {__version__}")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Final CSV path (default: {DEFAULT_OUTPUT.as_posix()})",
    )
    parser.add_argument(
        "--floor",
        type=float,
        default=50_000,
        help="Keep RUCs whose mean utilidad_neta is at least this value (default: 50000)",
    )
    parser.add_argument(
        "--ceiling",
        type=float,
        default=5_000_000,
        help="Keep RUCs whose mean utilidad_neta is at most this value (default: 5000000)",
    )
    parser.add_argument(
        "--columns",
        action="append",
        default=None,
        metavar="LIST",
        help=(
            "Export columns, comma-separated. Repeat the flag to add more. "
            "Replaces the default list. See --list-columns."
        ),
    )
    parser.add_argument(
        "--list-columns",
        action="store_true",
        help="Print the default export columns and other names you can request, then exit",
    )
    parser.add_argument(
        "--skip-filter",
        action="store_true",
        help=(
            "Do not apply the utilidad_neta floor/ceiling. "
            "Rows still need the required financial fields."
        ),
    )
    parser.add_argument(
        "--balance-year",
        type=int,
        default=None,
        help=(
            "Use this ÚLTIMO BALANCE year instead of the latest reporting cohort. "
            "The default is the latest year that covers at least 1%% of active companies."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="Per-read timeout in seconds for each download (default: 300)",
    )
    return parser


def column_catalog() -> str:
    default = "\n".join(f"  {name}" for name in DEFAULT_COLUMNS)
    extra_directory = "\n".join(
        f"  {name}" for name in DIRECTORIO_COLUMNS if name not in DEFAULT_COLUMNS
    )
    extra_ranking = "\n".join(
        f"  {name}" for name in RANKING_COLUMNS if name not in DEFAULT_COLUMNS and name != "expediente"
    )
    return (
        "Default columns:\n"
        f"{default}\n"
        f"  {MARGEN_NETO}  (calculated: utilidad_neta / ingresos_totales; "
        "blank when ingresos are 0)\n"
        "\n"
        "Other directorio columns:\n"
        f"{extra_directory}\n"
        "\n"
        "Other ranking columns:\n"
        f"{extra_ranking}\n"
        "\n"
        "empresa_nueva is included in the default export. The published ranking CSV\n"
        "does not currently contain it; it is derived from PRESENTÓ BALANCE INICIAL\n"
        "(SI = 1, NO / NO APLICA = 0). A published empresa_nueva column is used as-is\n"
        "when Supercias adds one.\n"
    )


def parse_columns(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    names: list[str] = []
    for value in values:
        for part in value.split(","):
            name = part.strip()
            if name:
                names.append(name)
    if not names:
        raise ValueError("--columns did not include any column names")
    # Preserve order, drop repeats.
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "query":
        return query_main(args_list[1:])
    if args_list and args_list[0] == "load-eeff":
        return load_eeff_main(args_list[1:])
    parser = build_parser()
    args = parser.parse_args(args_list)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.list_columns:
        print(column_catalog())
        return 0
    if args.timeout <= 0:
        print("error: --timeout must be greater than 0", file=sys.stderr)
        return 2
    try:
        columns = parse_columns(args.columns)
        destination = export(
            args.output,
            floor=args.floor,
            ceiling=args.ceiling,
            columns=columns,
            skip_filter=args.skip_filter,
            balance_year=args.balance_year,
            timeout=(30, args.timeout),
        )
    except (requests.RequestException, TimeoutError, ColumnError, ValueError, OSError) as exc:
        logger.error("error: %s", exc)
        return 1
    print(destination)
    return 0


def query_main(argv: list[str]) -> int:
    from supercias.engine import LookupError, CompanyEngine

    parser = argparse.ArgumentParser(
        prog="supercias query",
        description=(
            "Brief one company from a ranking export plus parsed Supercias EEFF text files."
        ),
    )
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--ruc", help="Company RUC")
    identity.add_argument("--expediente", help="Supercias expediente number")
    identity.add_argument("--name", help="Substring of the company name")
    parser.add_argument("--year", type=int, default=None, help="Filing year (default: latest EEFF year)")
    parser.add_argument(
        "--ranking",
        type=Path,
        default=None,
        help="Ranking export CSV. Defaults to SUPERCIAS_RANKING, data/supercias.csv, or fixtures/ranking_sample.csv",
    )
    parser.add_argument(
        "--eeff-dir",
        type=Path,
        action="append",
        default=None,
        help="Directory of EEFF text files. Repeat to add more. Defaults to data/eeff and fixtures/eeff",
    )
    parser.add_argument("--json", action="store_true", help="Print the brief as JSON")
    args = parser.parse_args(argv)
    try:
        engine = CompanyEngine(
            ranking_path=args.ranking,
            eeff_dirs=args.eeff_dir,
        )
        view = engine.lookup(
            ruc=args.ruc,
            expediente=args.expediente,
            name=args.name,
            year=args.year,
        )
    except (LookupError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(view.to_json() if args.json else view.render())
    return 0


def load_eeff_main(argv: list[str]) -> int:
    from supercias.engine import seed_eeff

    parser = argparse.ArgumentParser(
        prog="supercias load-eeff",
        description="Copy the bundled EEFF samples into data/eeff for local demos.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("data") / "eeff",
        help="Destination directory (default: data/eeff)",
    )
    args = parser.parse_args(argv)
    try:
        copied = seed_eeff(args.dest)
    except (FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Copied {copied} statements to {args.dest}")
    return 0
