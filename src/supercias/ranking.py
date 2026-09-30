"""Load the Supercias financial ranking CSV from an HTTP body."""

from __future__ import annotations

import io
import logging

import pandas as pd

from supercias.columns import fold

logger = logging.getLogger(__name__)


def ranking_frame_from_text(
    text: str,
    needed: set[str] | None = None,
    expedientes: set[int] | None = None,
) -> pd.DataFrame:
    """Parse ranking CSV text that was just downloaded.

    ``needed`` is a set of folded column names. ``expediente`` is always kept.
    ``expedientes`` limits the frame to those company ids. A left join only
    needs ranking rows for the directory cohort, and the full history file is
    hundreds of megabytes.

    Passing the response text through ``StringIO`` is deliberate: ``df_fin`` is
    this download, never a local file such as ``df1.csv``.
    """
    if not text.strip():
        raise ValueError("Ranking response was empty")
    separator = _separator(text)
    raw_header = list(pd.read_csv(io.StringIO(text), sep=separator, nrows=0).columns)
    stripped = [str(column).strip() for column in raw_header]
    usecols = _usecols(stripped, needed)
    original_by_name = {name: raw for name, raw in zip(stripped, raw_header)}
    reader = pd.read_csv(
        io.StringIO(text),
        sep=separator,
        usecols=[original_by_name[name] for name in usecols],
        chunksize=200_000,
        low_memory=False,
    )
    frames: list[pd.DataFrame] = []
    seen = 0
    for chunk in reader:
        chunk.columns = [str(column).strip() for column in chunk.columns]
        seen += len(chunk)
        if expedientes is not None:
            column = next(name for name in chunk.columns if fold(name) == fold("expediente"))
            ids = pd.to_numeric(chunk[column], errors="coerce")
            chunk = chunk.loc[ids.notna() & ids.round().isin(expedientes)]
        if not chunk.empty:
            frames.append(chunk)
    if frames:
        frame = pd.concat(frames, ignore_index=True)
    else:
        frame = pd.DataFrame(columns=usecols)
    logger.info(
        "Ranking rows: %s kept of %s read, columns: %s",
        len(frame),
        seen,
        len(frame.columns),
    )
    return frame


def _separator(text: str) -> str:
    first = text.splitlines()[0] if text else ""
    if first.count(";") > first.count(","):
        return ";"
    return ","


def _usecols(columns: list[str], needed: set[str] | None) -> list[str]:
    if not needed:
        return columns
    wanted = set(needed)
    wanted.add(fold("expediente"))
    selected = [column for column in columns if fold(column) in wanted]
    if not any(fold(column) == fold("expediente") for column in selected):
        raise ValueError(
            "Ranking CSV has no expediente column. Columns: " + ", ".join(columns)
        )
    return selected
