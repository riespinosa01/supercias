"""Column names published by Supercias and the default export list."""

from __future__ import annotations

import unicodedata

# Directorio workbook, sheet "Companias", after the title block.
# Confirmed against the file published on 26 Sep 2026.
DIRECTORIO_COLUMNS: tuple[str, ...] = (
    "No. FILA",
    "EXPEDIENTE",
    "RUC",
    "NOMBRE",
    "SITUACIÓN LEGAL",
    "FECHA_CONSTITUCION",
    "TIPO",
    "PAÍS",
    "REGIÓN",
    "PROVINCIA",
    "CANTÓN",
    "CIUDAD",
    "CALLE",
    "NÚMERO",
    "INTERSECCIÓN",
    "BARRIO",
    "TELÉFONO",
    "REPRESENTANTE",
    "CARGO",
    "CAPITAL SUSCRITO",
    "CIIU NIVEL 1",
    "CIIU NIVEL 6",
    "ÚLTIMO BALANCE",
    "PRESENTÓ BALANCE INICIAL",
    "FECHA PRESENTACIÓN BALANCE INICIAL",
)

# bi_ranking.csv, as documented at
# https://appscvsmovil.supercias.gob.ec/ranking/reporte.html
# The live file does not publish empresa_nueva.
RANKING_COLUMNS: tuple[str, ...] = (
    "anio",
    "expediente",
    "posicion_general",
    "cia_imvalores",
    "id_estado_financiero",
    "ingresos_ventas",
    "activos",
    "patrimonio",
    "utilidad_an_imp",
    "impuesto_renta",
    "n_empleados",
    "ingresos_totales",
    "utilidad_ejercicio",
    "utilidad_neta",
    "cod_segmento",
    "ciiu_n1",
    "ciiu_n6",
    "liquidez_corriente",
    "prueba_acida",
    "end_activo",
    "end_patrimonial",
    "end_activo_fijo",
    "end_corto_plazo",
    "end_largo_plazo",
    "cobertura_interes",
    "apalancamiento",
    "apalancamiento_financiero",
    "end_patrimonial_ct",
    "end_patrimonial_nct",
    "apalancamiento_c_l_plazo",
    "rot_cartera",
    "rot_activo_fijo",
    "rot_ventas",
    "per_med_cobranza",
    "per_med_pago",
    "impac_gasto_a_v",
    "impac_carga_finan",
    "rent_neta_activo",
    "margen_bruto",
    "margen_operacional",
    "rent_neta_ventas",
    "rent_ope_patrimonio",
    "rent_ope_activo",
    "roe",
    "roa",
    "fortaleza_patrimonial",
    "gastos_financieros",
    "gastos_admin_ventas",
    "depreciaciones",
    "amortizaciones",
    "costos_ventas_prod",
    "deuda_total",
    "deuda_total_c_plazo",
    "total_gastos",
)

DEFAULT_COLUMNS: tuple[str, ...] = (
    "EXPEDIENTE",
    "RUC",
    "NOMBRE",
    "FECHA_CONSTITUCION",
    "TIPO",
    "PAÍS",
    "PROVINCIA",
    "CIUDAD",
    "ÚLTIMO BALANCE",
    "CIIU NIVEL 1",
    "CIIU NIVEL 6",
    "anio",
    "ingresos_totales",
    "costos_ventas_prod",
    "gastos_admin_ventas",
    "gastos_financieros",
    "utilidad_neta",
    "deuda_total",
    "deuda_total_c_plazo",
    "n_empleados",
    "empresa_nueva",
)

# Rows missing any of these are dropped before the utilidad screen.
REQUIRED_FINANCIAL: tuple[str, ...] = (
    "anio",
    "ingresos_totales",
    "utilidad_neta",
    "deuda_total",
    "deuda_total_c_plazo",
    "n_empleados",
    "empresa_nueva",
)

MARGEN_NETO = "margen_neto"

# Years that are a smaller share of active companies than this are treated
# as stray filings (for example a few initial balances dated in the current
# calendar year) and do not become the default ÚLTIMO BALANCE cohort.
DEFAULT_MIN_YEAR_SHARE = 0.01


def fold(value: object) -> str:
    """Case- and accent-insensitive key for a column name."""
    text = "" if value is None else str(value).strip()
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_marks.casefold()


def column_map(columns: list[object] | tuple[object, ...]) -> dict[str, object]:
    """Map folded names to the original label. Later duplicates win."""
    mapping: dict[str, object] = {}
    for column in columns:
        mapping[fold(column)] = column
    return mapping


class ColumnError(KeyError):
    """A requested column is not on the frame."""

    def __init__(self, missing: list[str], available: list[object]) -> None:
        self.missing = missing
        self.available = [str(name) for name in available]
        super().__init__(
            "Unknown column(s): "
            + ", ".join(missing)
            + ". Available: "
            + ", ".join(self.available)
        )


def require_column(columns: list[object] | tuple[object, ...], name: str) -> object:
    found = column_map(columns).get(fold(name))
    if found is None:
        raise ColumnError([name], list(columns))
    return found


def canonicalize(frame_columns: list[object], canonical_names: tuple[str, ...] | list[str]) -> dict[object, str]:
    """Rename source labels onto the canonical names when they match folded."""
    available = column_map(frame_columns)
    rename: dict[object, str] = {}
    for name in canonical_names:
        actual = available.get(fold(name))
        if actual is not None and actual != name:
            rename[actual] = name
    return rename
