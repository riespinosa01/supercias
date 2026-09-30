"""Download the live Supercias files and write the export CSV."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pandas as pd

from supercias.columns import (
    DEFAULT_COLUMNS,
    MARGEN_NETO,
    REQUIRED_FINANCIAL,
    fold,
)
from supercias.directorio import read_directorio_xlsx
from supercias.http import DIRECTORIO_URL, RANKING_URL, download_file, download_text
from supercias.ranking import ranking_frame_from_text
from supercias.transform import build_dataset, prepare_directorio

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("data") / "supercias.csv"


def export(
    output: Path = DEFAULT_OUTPUT,
    *,
    floor: float = 50_000,
    ceiling: float = 5_000_000,
    columns: list[str] | None = None,
    skip_filter: bool = False,
    balance_year: int | None = None,
    directorio_url: str = DIRECTORIO_URL,
    ranking_url: str = RANKING_URL,
    timeout: tuple[float, float] = (30, 300),
) -> Path:
    """Pull both public files, merge them, and write ``output``."""
    with tempfile.TemporaryDirectory(prefix="supercias-") as temporary:
        workbook = Path(temporary) / "directorio_companias.xlsx"
        download_file(directorio_url, workbook, timeout)
        directory = read_directorio_xlsx(workbook)

    export_columns = list(columns) if columns else list(DEFAULT_COLUMNS)
    cohort = prepare_directorio(directory, balance_year=balance_year)
    ranking_text = download_text(ranking_url, timeout)
    ranking = ranking_frame_from_text(
        ranking_text,
        needed=_ranking_columns(directory.columns, export_columns),
        expedientes=set(cohort["EXPEDIENTE"].astype(int)),
    )
    del ranking_text

    frame = build_dataset(
        directory,
        ranking,
        columns=export_columns,
        floor=floor,
        ceiling=ceiling,
        skip_filter=skip_filter,
        balance_year=balance_year,
    )
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False, encoding="utf-8", lineterminator="\n")
    if frame.empty:
        logger.warning(
            "Export has no rows. Check --balance-year, --floor, and --ceiling."
        )
    logger.info("Wrote %s (%s rows, %s columns)", destination, len(frame), len(frame.columns))
    return destination


def _ranking_columns(directory_columns: pd.Index, export_columns: list[str]) -> set[str]:
    """Folded ranking fields required for the join, the screen, and the export."""
    in_directory = {fold(column) for column in directory_columns}
    needed = {fold("expediente")}
    needed.update(fold(column) for column in REQUIRED_FINANCIAL)
    needed.add(fold("anio"))
    needed.add(fold("ingresos_totales"))
    needed.add(fold("utilidad_neta"))
    for name in export_columns:
        if name == MARGEN_NETO:
            continue
        if fold(name) not in in_directory:
            needed.add(fold(name))
    return needed
