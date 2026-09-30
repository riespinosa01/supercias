"""Filter, join, and shape the directorio and ranking tables."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd

from supercias.columns import (
    DEFAULT_COLUMNS,
    DEFAULT_MIN_YEAR_SHARE,
    DIRECTORIO_COLUMNS,
    MARGEN_NETO,
    REQUIRED_FINANCIAL,
    ColumnError,
    canonicalize,
    column_map,
    fold,
    require_column,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class YearChoice:
    selected: int
    literal_max: int
    selected_rows: int
    literal_rows: int


def build_dataset(
    directorio: pd.DataFrame,
    ranking: pd.DataFrame,
    *,
    columns: list[str] | None = None,
    floor: float = 50_000,
    ceiling: float = 5_000_000,
    skip_filter: bool = False,
    balance_year: int | None = None,
    min_year_share: float = DEFAULT_MIN_YEAR_SHARE,
) -> pd.DataFrame:
    """Return the export frame. ``directorio`` still includes every legal status."""
    directory = prepare_directorio(
        directorio,
        balance_year=balance_year,
        min_year_share=min_year_share,
    )
    finances = _prepare_ranking(ranking)
    merged = directory.merge(
        finances,
        how="left",
        left_on="EXPEDIENTE",
        right_on="expediente",
    )
    if "expediente" in merged.columns:
        merged = merged.drop(columns=["expediente"])
    merged = _ensure_empresa_nueva(merged, finances)
    logger.info("Merged rows: %s", len(merged))

    export_columns = list(columns) if columns else list(DEFAULT_COLUMNS)
    _reject_unknown(merged, export_columns)
    if MARGEN_NETO in export_columns and not {"utilidad_neta", "ingresos_totales"}.issubset(export_columns):
        raise ValueError(
            "margen_neto is utilidad_neta / ingresos_totales; include both columns in --columns"
        )
    merged = _require_financials(merged)
    logger.info("Rows with complete financials: %s", len(merged))
    if skip_filter:
        logger.info("Skipping utilidad floor/ceiling")
    else:
        merged = _filter_utilidad(merged, floor=floor, ceiling=ceiling)
        logger.info(
            "Rows after utilidad_neta mean in [%s, %s]: %s",
            floor,
            ceiling,
            len(merged),
        )

    projected = _project(merged, export_columns)
    return _with_margen(projected)


def prepare_directorio(
    directorio: pd.DataFrame,
    *,
    balance_year: int | None = None,
    min_year_share: float = DEFAULT_MIN_YEAR_SHARE,
) -> pd.DataFrame:
    frame = directorio.copy()
    frame = frame.rename(columns=canonicalize(list(frame.columns), DIRECTORIO_COLUMNS))
    status_col = str(require_column(list(frame.columns), "SITUACIÓN LEGAL"))
    year_col = str(require_column(list(frame.columns), "ÚLTIMO BALANCE"))
    expediente_col = str(require_column(list(frame.columns), "EXPEDIENTE"))

    status = frame[status_col].map(_strip)
    active = frame.loc[status == "ACTIVA"].copy()
    if active.empty:
        raise ValueError("No directorio rows with SITUACIÓN LEGAL == ACTIVA")

    choice = select_balance_year(
        active[year_col],
        min_share=min_year_share,
        override=balance_year,
    )
    years = pd.to_numeric(active[year_col], errors="coerce")
    active = active.loc[years == choice.selected].copy()
    if active.empty:
        present = sorted(
            int(year)
            for year in pd.to_numeric(frame[year_col], errors="coerce").dropna().unique()
        )
        raise ValueError(
            f"No ACTIVA companies with ÚLTIMO BALANCE == {choice.selected}. "
            f"Years present: {present}"
        )
    active[year_col] = pd.to_numeric(active[year_col], errors="coerce").astype("Int64")
    if choice.selected != choice.literal_max:
        logger.info(
            "ÚLTIMO BALANCE cohort: %s (%s active companies). "
            "The literal maximum year is %s (%s active companies); "
            "that tail is below %.0f%% of active companies, so it is not the default cohort. "
            "Pass --balance-year %s to use it.",
            choice.selected,
            choice.selected_rows,
            choice.literal_max,
            choice.literal_rows,
            min_year_share * 100,
            choice.literal_max,
        )
    else:
        logger.info(
            "ÚLTIMO BALANCE cohort: %s (%s active companies)",
            choice.selected,
            choice.selected_rows,
        )

    expediente = pd.to_numeric(active[expediente_col], errors="coerce")
    dropped = int(expediente.isna().sum())
    active = active.loc[expediente.notna()].copy()
    active[expediente_col] = expediente.loc[active.index].round().astype(int)
    if expediente_col != "EXPEDIENTE":
        active = active.rename(columns={expediente_col: "EXPEDIENTE"})
    before = len(active)
    active = active.drop_duplicates(subset=["EXPEDIENTE"], keep="first")
    duplicate_rows = before - len(active)
    if dropped or duplicate_rows:
        logger.info(
            "Dropped %s directorio rows with empty EXPEDIENTE and %s duplicate EXPEDIENTE rows",
            dropped,
            duplicate_rows,
        )
    if "RUC" in active.columns:
        active["RUC"] = _normalize_ruc(active["RUC"])
    logger.info("Directorio companies in cohort: %s", len(active))
    return active.reset_index(drop=True)


def select_balance_year(
    years: pd.Series,
    *,
    min_share: float = DEFAULT_MIN_YEAR_SHARE,
    override: int | None = None,
) -> YearChoice:
    """Pick the latest reporting year without hardcoding one.

    The greatest numeric year is remembered as ``literal_max``. The year used
    by default is the greatest year that still covers at least ``min_share``
    of the values. On the September 2026 directory, three active companies
    show 2026 because they filed an initial balance that calendar year; the
    reporting cohort is 2025.
    """
    numeric = pd.to_numeric(years, errors="coerce").dropna()
    if numeric.empty:
        raise ValueError("ÚLTIMO BALANCE has no numeric years")
    counts = numeric.astype(int).value_counts()
    literal_max = int(counts.index.max())
    literal_rows = int(counts.loc[literal_max])
    if override is not None:
        selected = int(override)
        selected_rows = int(counts.loc[selected]) if selected in counts.index else 0
        return YearChoice(selected, literal_max, selected_rows, literal_rows)

    if not 0 < min_share <= 1:
        raise ValueError("min_year_share must be between 0 and 1")
    threshold = max(1, math.ceil(len(numeric) * min_share))
    eligible = counts[counts >= threshold]
    if eligible.empty:
        selected = int(counts.idxmax())
    else:
        selected = int(eligible.index.max())
    return YearChoice(selected, literal_max, int(counts.loc[selected]), literal_rows)


def _prepare_ranking(ranking: pd.DataFrame) -> pd.DataFrame:
    frame = ranking.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    expediente_col = str(require_column(list(frame.columns), "expediente"))
    expediente = pd.to_numeric(frame[expediente_col], errors="coerce")
    dropped = int(expediente.isna().sum())
    frame = frame.loc[expediente.notna()].copy()
    frame["expediente"] = expediente.loc[frame.index].round().astype(int)
    if expediente_col != "expediente":
        frame = frame.drop(columns=[expediente_col])
    if dropped:
        logger.info("Dropped %s ranking rows with empty expediente", dropped)
    if "empresa_nueva" in column_map(list(frame.columns)):
        source = str(require_column(list(frame.columns), "empresa_nueva"))
        frame["empresa_nueva"] = pd.to_numeric(frame[source], errors="coerce")
        if source != "empresa_nueva":
            frame = frame.drop(columns=[source])
    return frame.reset_index(drop=True)


def _ensure_empresa_nueva(merged: pd.DataFrame, ranking: pd.DataFrame) -> pd.DataFrame:
    """Use the published flag, otherwise derive it.

    The current ``bi_ranking.csv`` dictionary ends at ``total_gastos`` and does
    not include ``empresa_nueva``. The directory field ``PRESENTÓ BALANCE
    INICIAL`` is the Supercias marker for a company that filed an opening
    balance: SI becomes 1, and NO / NO APLICA become 0.
    """
    if "empresa_nueva" in merged.columns and merged["empresa_nueva"].notna().any():
        merged["empresa_nueva"] = pd.to_numeric(merged["empresa_nueva"], errors="coerce")
        return merged

    initial_name = column_map(list(merged.columns)).get(fold("PRESENTÓ BALANCE INICIAL"))
    if initial_name is not None:
        merged["empresa_nueva"] = merged[initial_name].map(_initial_balance_flag)
        logger.info(
            "empresa_nueva derived from PRESENTÓ BALANCE INICIAL "
            "(the ranking CSV does not publish this column)"
        )
        return merged

    if "anio" in ranking.columns and "expediente" in ranking.columns:
        years = pd.to_numeric(ranking["anio"], errors="coerce")
        latest = years.max()
        first_year = years.groupby(ranking["expediente"]).min()
        flag = (first_year == latest).astype(int)
        merged["empresa_nueva"] = pd.to_numeric(
            merged["EXPEDIENTE"].map(flag), errors="coerce"
        )
        logger.info(
            "empresa_nueva derived from ranking history "
            "(1 when the company's first anio is the latest anio in the file)"
        )
        return merged

    merged["empresa_nueva"] = pd.NA
    logger.warning("empresa_nueva could not be derived; those rows will fail the financial screen")
    return merged


def _initial_balance_flag(value: object) -> object:
    text = fold(value)
    if not text or text == "nan":
        return pd.NA
    if text in {"si", "s", "1", "yes", "true"}:
        return 1
    if text in {"no", "no aplica", "n", "0", "false"}:
        return 0
    return pd.NA


def _require_financials(frame: pd.DataFrame) -> pd.DataFrame:
    present = [name for name in REQUIRED_FINANCIAL if name in frame.columns]
    missing = [name for name in REQUIRED_FINANCIAL if name not in frame.columns]
    if missing:
        raise ColumnError(missing, list(frame.columns))
    cleaned = frame.copy()
    for name in present:
        cleaned[name] = pd.to_numeric(cleaned[name], errors="coerce")
    return cleaned.dropna(subset=present).reset_index(drop=True)


def _filter_utilidad(frame: pd.DataFrame, *, floor: float, ceiling: float) -> pd.DataFrame:
    if "RUC" not in frame.columns or "utilidad_neta" not in frame.columns:
        raise ColumnError(
            [name for name in ("RUC", "utilidad_neta") if name not in frame.columns],
            list(frame.columns),
        )
    if floor > ceiling:
        raise ValueError(f"floor ({floor}) is greater than ceiling ({ceiling})")
    means = frame.groupby("RUC", dropna=False)["utilidad_neta"].mean()
    keep = means.index[(means >= floor) & (means <= ceiling)]
    selected = frame.loc[frame["RUC"].isin(keep)].copy()
    logger.info("RUCs inside the utilidad band: %s", int(means.index.isin(keep).sum()))
    return selected.reset_index(drop=True)


def _reject_unknown(frame: pd.DataFrame, columns: list[str]) -> None:
    available = column_map(list(frame.columns))
    # empresa_nueva may have just been created under its canonical name.
    missing = [name for name in columns if fold(name) not in available and name != MARGEN_NETO]
    if missing:
        raise ColumnError(missing, list(frame.columns))


def _project(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    available = column_map(list(frame.columns))
    selected: list[pd.Series] = []
    names: list[str] = []
    for name in columns:
        if name == MARGEN_NETO:
            continue
        source = available[fold(name)]
        series = frame[source]
        if fold(name) in {fold(item) for item in ("EXPEDIENTE", "anio", "ÚLTIMO BALANCE", "n_empleados", "empresa_nueva")}:
            series = _integral(series)
        selected.append(series.reset_index(drop=True))
        names.append(name)
    projected = pd.concat(selected, axis=1)
    projected.columns = names
    if "anio" in projected.columns:
        return projected.sort_values(
            [column for column in ("EXPEDIENTE", "anio") if column in projected.columns],
            kind="mergesort",
        ).reset_index(drop=True)
    return projected


def _with_margen(frame: pd.DataFrame) -> pd.DataFrame:
    if MARGEN_NETO in frame.columns:
        return frame
    if "utilidad_neta" not in frame.columns or "ingresos_totales" not in frame.columns:
        return frame
    utilidad = pd.to_numeric(frame["utilidad_neta"], errors="coerce")
    ingresos = pd.to_numeric(frame["ingresos_totales"], errors="coerce")
    ingresos = ingresos.mask(ingresos == 0)
    result = frame.copy()
    result[MARGEN_NETO] = utilidad / ingresos
    return result


def _integral(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.dropna()
    if valid.empty or (valid % 1 != 0).any():
        return numeric
    return numeric.round().astype("Int64")


def _normalize_ruc(series: pd.Series) -> pd.Series:
    def clean(value: object) -> str:
        if pd.isna(value):
            return ""
        text = str(value).strip()
        if text.endswith(".0") and text.replace(".", "", 1).isdigit():
            return text[:-2]
        return text

    return series.map(clean)


def _strip(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()
