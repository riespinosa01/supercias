# Supercias company export

Command-line export of Ecuador's Superintendencia de Compañías, Valores y Seguros (Supercias) company directory and financial ranking. Each run downloads the public files, keeps active companies in the latest balance cohort, joins them to the ranking, and writes a CSV.

There is no login and no local `Downloads` path. The ranking frame is always parsed from the HTTP body of `bi_ranking.csv`. It is never replaced with a local file such as `df1.csv`.

## Install

From this directory, with Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate`. The install pulls in pandas, openpyxl, and requests, and installs this package. Then either of these works:

```bash
python -m supercias
supercias
```

## Refresh the CSV

```bash
python -m supercias -o data/supercias.csv
```

The default output path is `data/supercias.csv` in the current working directory. The command downloads both sources, applies the default columns and the default utilidad screen, and prints the CSV path when it finishes.

The ranking file is about 370 MB and the directory workbook is about 37 MB. A run needs a few GB of free RAM and a connection that can reach the Supercias hosts. The read timeout is 300 seconds between chunks; raise it on a slow link:

```bash
python -m supercias --timeout 600 -o data/supercias.csv
```

## What the export keeps

1. **Directory.** The workbook is read from the first row that contains `EXPEDIENTE` (the published file puts titles on the rows above that). Header text is stripped. The published sheet declares its dimension as `A1`, which would hide every column after the first; the reader widens that dimension before parsing.
2. **Active companies only.** `SITUACIÓN LEGAL == ACTIVA`.
3. **Latest balance cohort.** `ÚLTIMO BALANCE` equal to the latest year that covers at least 1% of active companies. The year is not hardcoded. On the 26 September 2026 file the literal maximum is 2026, but that year is three companies that filed an opening balance (`PRESENTÓ BALANCE INICIAL = SI`) in calendar 2026. The reporting cohort is 2025 (about 149,000 active companies). Pass `--balance-year 2026` to force another year.
4. **Ranking.** `bi_ranking.csv` is downloaded with a browser User-Agent and loaded with pandas through `StringIO`. Rows with an empty `expediente` are dropped and the id is cast to an integer. The same cleanup is applied to `EXPEDIENTE`.
5. **Left join** of the directory onto the ranking, on `EXPEDIENTE` = `expediente`. A company can have one row per ranking year.
6. **Complete financials.** Rows missing any of `anio`, `ingresos_totales`, `utilidad_neta`, `deuda_total`, `deuda_total_c_plazo`, `n_empleados`, or `empresa_nueva` are dropped, after those fields are coerced to numbers. Zero is kept; only missing values are dropped.
7. **Utilidad screen.** A RUC is kept when the mean of its remaining `utilidad_neta` values is between `--floor` and `--ceiling`, inclusive. Defaults are 50000 and 5000000.
8. **`margen_neto`.** `utilidad_neta / ingresos_totales`. The cell is blank when ingresos are 0.

## Columns

```bash
python -m supercias --list-columns
```

Default export, in this order, plus `margen_neto`:

`EXPEDIENTE`, `RUC`, `NOMBRE`, `FECHA_CONSTITUCION`, `TIPO`, `PAÍS`, `PROVINCIA`, `CIUDAD`, `ÚLTIMO BALANCE`, `CIIU NIVEL 1`, `CIIU NIVEL 6`, `anio`, `ingresos_totales`, `costos_ventas_prod`, `gastos_admin_ventas`, `gastos_financieros`, `utilidad_neta`, `deuda_total`, `deuda_total_c_plazo`, `n_empleados`, `empresa_nueva`

Names are matched without depending on accents or letter case, and the CSV uses the names above. `--list-columns` also prints the other directory and ranking fields you can request.

`empresa_nueva` is part of the default export. The ranking dictionary published by Supercias currently ends at `total_gastos` and does not include this field. When it is absent, the CLI sets it from the directory column `PRESENTÓ BALANCE INICIAL`: `SI` is 1, and `NO` / `NO APLICA` are 0. If a future ranking file includes `empresa_nueva`, those published values are used instead.

Choose a different export list:

```bash
python -m supercias --columns "EXPEDIENTE,RUC,NOMBRE,anio,utilidad_neta,ingresos_totales"
```

Repeat `--columns` if you prefer several arguments. `margen_neto` is still appended when both `utilidad_neta` and `ingresos_totales` are in the list.

## Other flags

```bash
python -m supercias --floor 100000 --ceiling 1000000 -o data/mid.csv
python -m supercias --skip-filter -o data/all_financials.csv
python -m supercias --balance-year 2024 -o data/balance_2024.csv
```

`--skip-filter` skips the mean `utilidad_neta` floor and ceiling. Rows still have to contain the required financial fields, so companies with no ranking match are not written.

## Schedule it

The command is safe to rerun. It only reads public URLs and writes the CSV you name.

```cron
0 6 * * * cd /path/to/this/repo && .venv/bin/python -m supercias -o data/supercias.csv
```

## Sources

- Directory workbook: <https://mercadodevalores.supercias.gob.ec/reportes/excel/directorio_companias.xlsx>
- Ranking CSV: <https://appscvsmovil.supercias.gob.ec/ranking/recursos/bi_ranking.csv>
- Ranking column dictionary: <https://appscvsmovil.supercias.gob.ec/ranking/reporte.html>

## If a download fails

Supercias sometimes stalls or blocks a network. The CLI does not fall back to a previously saved local extract. Run the same command from a machine that can open those URLs. `--timeout` covers a slow transfer; the downloader retries transient HTTP 429/5xx responses.

## Query a company

The export CSV is one input. Parsed estados financieros (EEFF) are the other. A query joins them for one company: ranking fields, balance-sheet totals, the income statement, a payroll rollup, an EBITDA proxy, and cross-check flags.

```bash
python -m supercias query --name ESCOLLANOS
python -m supercias query --ruc 1792319900001 --json
python -m supercias query --expediente 139571 --year 2025
```

From other Python code:

```python
from supercias.engine import CompanyEngine

engine = CompanyEngine()
brief = engine.lookup(name="ESCOLLANOS").brief()
```

`CompanyEngine` reads a ranking CSV and every `pdftotext -layout` statement it can find. Ranking resolution order:

1. `--ranking` / `CompanyEngine(ranking_path=...)`
2. The `SUPERCIAS_RANKING` environment variable
3. `data/supercias.csv` from `python -m supercias`
4. `fixtures/ranking_sample.csv` (the four bundled sample companies only)

EEFF directories, first match wins for a given RUC and year:

1. `--eeff-dir` (repeat the flag to add directories)
2. `data/eeff/{ruc}/{year}_balance.txt` and `{year}_income.txt`
3. `fixtures/eeff/` (samples committed with this repo)

Add another filing by dropping the two text files into `data/eeff/{ruc}/`. The year and RUC are also read from the statement header, so the file name only has to contain `balance` or `income` if you use a different pattern. Copy the bundled samples into `data/eeff` with:

```bash
python -m supercias load-eeff
```

The samples are ESCOLLANOS, ECUACOBRANZAS, EUROINSTRUMENTS (2023–2025), and ABRODESIVOS (2024–2025).

Payroll uses account codes, not Spanish labels. `50102*` and `50103*` are direct and indirect labor. Selling `5020101`–`5020106` and admin `5020201`–`5020206` are the cash payroll block. `5020105` and `5020205` are honorarios, so the brief shows both the full cash total and nómina with honorarios removed. When `n_empleados` is on the ranking row, nómina per employee is included. A prior EEFF year, when the folder has one, produces a year-over-year nómina check.

The EBITDA figure is a proxy, not a reported Supercias line. It is ganancia bruta (402) minus gastos (502), plus gastos financieros (50203) and otros ingresos (403), plus depreciation and amortization on `5010401`, `5020120`/`5020121`, and `5020221`/`5020222` when those codes are in the file. Child lines are used instead of a parent so the same amount is not added twice. Deterioro is not treated as D&A. If none of those D&A codes appear, the brief says so and the proxy stops at operating income.

Ranking `ingresos_totales` is compared with code 401, and `utilidad_neta` with code 707. A gap is flagged when it is at least USD 1,000 and at least 0.5% of the ranking amount.

Downloading the PDFs from the Supercias portal is not implemented. The form to automate later is <https://appscvsgen.supercias.gob.ec/consultaCompanias/societario/estadosFinancierosPorRamo.jsf>. Save each PDF with `pdftotext -layout` and place the `.txt` files as above.

## Tests

```bash
python -m unittest discover -s tests
pytest
```

Parser tests read the bundled EEFF samples. The export tests use small in-memory tables. Neither downloads Supercias. `pytest` collects the same unittest modules if it is installed.
