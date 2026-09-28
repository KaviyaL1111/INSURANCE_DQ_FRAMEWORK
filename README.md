# Data Validation & Regression Management

A Python application for creating, organising, executing and **re-running**
data validation test cases across Snowflake, MSSQL and flat files
(CSV / Excel / JSON / Parquet).

Analysts normally lose hours after a validation failure: digging out which
test cases ran, fixing the data, then working out what to re-execute. This
tool keeps every test case as a saved, versioned, editable record, keeps
full execution history, and lets you filter that history by date, tick the
tests you care about, and rerun just those — always at the test case's
latest saved configuration.

---

## Quick start

```bash
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # then fill in your Snowflake details
```

New to Snowflake? Follow **[docs/SNOWFLAKE_SETUP.md](docs/SNOWFLAKE_SETUP.md)** —
it covers finding your account identifier, the one-time bootstrap script,
MFA, and how to keep trial credits down.

```bash
python -m src.cli doctor      # confirm the connection
python -m src.cli demo        # the entire walkthrough, end to end
streamlit run streamlit_app/DQ_Workspace.py
```

---

## What it does

| Requirement | Where it lives |
|---|---|
| Create and execute validation test cases | `Test Cases` page · `DQ_TEST_CASE` · `src/dq_engine.py` |
| Save validation scripts per application/project | `DQ_PROJECT`, `DQ_FOLDER` · `src/repository.py` |
| Folder-based organisation, configurable | `Projects & Folders` page — nested folders, any structure |
| Edit existing test cases | `Test Cases → Create / Edit`, with version history and restore |
| View execution history | `Execution History` page · `DQ_EXECUTION_LOG` |
| Filter history by start and end date | date inputs on that page · `HistoryStore.get_execution_history` |
| Select one or more historical test cases | checkbox column in the history grid |
| Rerun only the selected validations | **Execute Selected Tests** · `RegressionEngine.rerun_selected` |
| Passed and failed records with mismatch detail | `DQ_FAILURE_LOG` — business key, column, expected, actual, reason |
| Maintain results for audit and regression | `DQ_EXECUTION_LOG`, `DQ_REGRESSION_RUN`, `DQ_TEST_CASE_VERSION` |
| Prevent duplicate test-case names in a folder | enforced in `Repository.create_test_case` |
| Unique test-case ID | `TC-00001`, generated per save |
| Source-to-target validation | `SOURCE_TARGET_COMPARE` test type |
| Historical validation | `/Historical` folder — date-range parameters, latest version per record |
| Incremental load validation | `/Incremental` folder — per-entity `DQ_WATERMARK` windows |
| Snowflake + MSSQL | `src/connectors/` — see [sql/mssql/README.md](sql/mssql/README.md) |
| Flat files (CSV, TSV, Excel, JSON, Parquet) | `flatfile` connection · `Flat Files` page — see [below](#flat-file-connectivity) |
| Load a flat file through to curated, then validate it | `Flat Files → Load into staging & curated` · `src/staging.py`, `src/curated.py` — see [below](#promote-to-curated) |

---

## How a test case works

A test case is a row in `DQ_TEST_CASE`, not a file. It stores its own SQL,
which database to run against, and what "passing" means. Five types:

| Type | Passes when |
|---|---|
| `SOURCE_TARGET_COMPARE` | source and target rows match on the business key, column by column |
| `SQL_ROWCOUNT` | the rule query returns **no** rows |
| `ROW_COUNT_MATCH` | the two counts agree (within tolerance) |
| `STATUS_COLUMN` | every returned row has `STATUS = 'PASS'` |
| `INFORMATIONAL` | always — for reports you still want in the audit trail |

Having more than one type matters. A reconciliation report returns rows by
design; scoring it with "any row means failure" makes it impossible to pass.

SQL is written with neutral `:named` parameters:

```sql
SELECT POLICY_ID, PREMIUM_AMOUNT
FROM TRN_POLICY
WHERE BATCH_ID = :batch_id
  AND RECORD_EFFECTIVE_TS >= :history_start
```

Each connector translates those to its own driver's style, so the same test
case runs against Snowflake or SQL Server unchanged. `:batch_id`,
`:run_date` and `:run_ts` are always supplied; anything else is prompted for
at run time or filled from the test case's saved defaults.

### Mismatch detail

`SOURCE_TARGET_COMPARE` reports four defect classes per record:

| Type | Meaning |
|---|---|
| `VALUE_MISMATCH` | key matched, a column differs — logged with expected and actual |
| `MISSING_IN_TARGET` | in source, never loaded into target |
| `EXTRA_IN_TARGET` | in target, no matching source row |
| `DUPLICATE_KEY` | the business key is not unique on one side |

Values are normalised before comparison, so a Snowflake `Decimal('12000.00')`
and an MSSQL `12000.0` are equal rather than a false positive.

For `SQL_ROWCOUNT`, name your columns `BUSINESS_KEY`, `COLUMN_NAME`,
`EXPECTED_VALUE`, `ACTUAL_VALUE` and `FAILURE_REASON` and they flow straight
into the failure log.

---

## The rerun workflow

1. Open **Execution History**.
2. Enter a start and end date; press **Load history**.
3. Every matching execution is listed with a checkbox.
4. Tick what you need — or press *Select all failing*.
5. Press **Execute Selected Tests**.

Step 5 reloads each selected test case **from the repository**, so a test
you corrected after it failed runs in its corrected form. The page tells you
when a selected test has been edited since it last ran
(`TC-00007 v1→v2`). Picking three historical runs of the same test executes
it once, not three times.

---

## Flat-file connectivity

A folder of files is exposed as a read-only connection called `flatfile`.
Every supported file becomes a table named after the file, and test cases
query it with the same `:named` SQL as any other connection:

| File | Table |
|---|---|
| `claims.csv` | `CLAIMS` |
| `motor book.xlsx` with one sheet | `MOTOR_BOOK` |
| `motor book.xlsx` with sheets *Q1*, *Q2* | `MOTOR_BOOK_Q1`, `MOTOR_BOOK_Q2` |
| `2026-feed.psv` | `T_2026_FEED` |

Supported: `.csv .tsv .txt .psv .dat` (delimiter detected per file),
`.xlsx .xls` (needs `openpyxl`), `.json .jsonl`, `.parquet` (needs `pyarrow`).
The folder defaults to `data/`; set `FLATFILE_DIR` and the other `FLATFILE_*`
options in `.env` to change it (see `.env.example`).

Set a test case's **source** (or target) connection to `flatfile` to
reconcile a landed file against the table it was loaded into; the seeded
*Claims landing file vs staging* test does exactly that for `data/claims.csv`
vs `STG_CLAIM`. The comparator treats ISO date text in a file
(`2024-10-31`) as equal to the matching `DATE`/`TIMESTAMP` value.

The **Flat Files** page lets you upload files, browse and profile each table
(nulls, distinct values, candidate keys), run ad-hoc SQL against them, and
generate a file-vs-table reconciliation test case without writing SQL.
Files are re-read automatically when they change. `python -m src.cli
--connection flatfile doctor` lists what the connection sees.

Queries against files run in SQLite, so use SQLite syntax in the flat-file
side of a test case. The flat-file connection can't hold the repository.

### Load into staging

Browsing a file never writes to the database. To get a file's rows **into**
Snowflake, use **Flat Files → Load into staging** (or
`python -m src.cli load-file my_claims.csv`). It inserts the rows into
`STG_CUSTOMER`, `STG_POLICY` or `STG_CLAIM` under a batch ID. By default it
replaces that batch's rows in that table; you can choose to append instead.

Before anything is written, the file is checked:

* **blocking**: a required column is missing, a business key is empty,
  an amount isn't a number, or a date isn't `YYYY-MM-DD`. Nothing loads.
* **warning**: duplicate keys and extra columns. The rows still load, and
  catching them is the job of the saved test cases.

A load writes every row or none: it runs in a single transaction.
`DQ_BATCH_METADATA` is updated so the row-count reconciliation test stays
accurate. [docs/FLAT_FILE_TESTING.md](docs/FLAT_FILE_TESTING.md) is a
step-by-step guide for testers.

### Promote to curated

Once a batch is staged, the same tab (or `python -m src.cli promote`, or
`load-file FILE --promote`) takes it the rest of the way:

1. **Transformation**: `03_transform_load.sql` applies the mapping rules
   (CUS-001..004, POL-001..006, CLM-001..006) to that batch.
2. **Curated**: `04_curated_merge_load.sql` merges it into `POLICY_MASTER`,
   `CLAIM_MASTER` and `CUSTOMER_360` on the business key, recomputes the
   customer totals, and moves the watermark for each entity the batch
   carried.
3. **Validation**: the `/Source-to-Target`, `/Historical` and `/Incremental`
   test cases run on the batch. The historical window defaults to the dates
   the batch covers.

Steps 1 and 2 run in a single transaction. Before anything runs:

* **blocking**: nothing is staged for the batch, or a business key appears
  twice in it. Curated keeps one row per key.
* **warning**: a claim whose policy is neither in the batch nor in
  `POLICY_MASTER`. Rule CLM-006 drops it, and the rest of the batch still
  goes through.

A batch can carry any subset of the three files. For example, a claims-only
delta file links to policies loaded earlier.

What each folder checks after a promotion:

| Folder | Checks |
|---|---|
| `/Source-to-Target` | transformation vs curated, plus **staging vs curated**: the mapping rules are re-applied to the raw staged rows, so a wrong rule is caught even when transformation and curated agree. Customer totals are checked for every customer the batch touched. |
| `/Historical` | for records effective in the window, curated holds the **latest** version any batch delivered. An older file loaded after a newer one shows up here. |
| `/Incremental` | every record in the batch is newer than the previous batch's high watermark, so already-processed data sent again fails. It is also no later than this batch's own high watermark. Policy, claim and customer each have their own watermark. |

---

## Time zone

Every timestamp is Indian Standard Time (UTC+05:30): created/updated times,
execution and failure times, run and batch ids, the `:run_date` / `:run_ts`
parameters, and the history date filter. Python code gets the time from
`src.config.now_ist()`. Snowflake sessions are opened with
`TIMEZONE = 'Asia/Kolkata'`, so `CURRENT_TIMESTAMP()` and every
`DEFAULT CURRENT_TIMESTAMP()` column are IST too. Values are stored in
`TIMESTAMP_NTZ` columns as IST wall-clock time.

---

## Project layout

```
src/
  connectors/          Snowflake, MSSQL and flat-file drivers behind one interface
  config.py            connection profiles from environment variables
  repository.py        projects, folders, test cases, versioning
  history.py           execution log, failure log, regression runs
  comparator.py        source-to-target row comparison
  dq_engine.py         executes a saved test case
  regression_engine.py history filtering and selective rerun
  seed.py              loads seed/demo_catalog.yaml into the repository
  staging.py           checks a flat file and loads it into an STG_* table
  curated.py           promotes a staged batch to curated and validates it
  cli.py               command line
streamlit_app/
  DQ_Workspace.py      Projects & Folders (home)
  pages/1_Test_Cases.py        browse, author, edit, run
  pages/2_Execution_History.py date filter, checkboxes, rerun
  pages/3_Dashboard.py         KPI tiles, charts (status by folder, failures, trend, records per layer), mismatch detail
  charts.py            the Dashboard's Altair charts and KPI tiles
  pages/4_Demo_Pipeline.py     drive the sample pipeline from the browser
  pages/5_Flat_Files.py        upload, browse, profile and query files; build file tests
sql/
  bootstrap_snowflake.sql        one-time: database + warehouse
  00_metadata_repository_ddl.sql the repository control tables
  01_ddl_create_all_tables.sql   the insurance demo tables
  02..06_*.sql                   load, transform, merge, corrupt, correct
  mssql/                         T-SQL mirror + how to enable MSSQL
seed/demo_catalog.yaml   25 starter test cases across 7 folders
data/*.csv               the demo dataset (source of truth for 02_*.sql)
tools/                   regenerate the load SQL; make awkward flat-file test samples
tests/                   200 tests, no database required
docs/SNOWFLAKE_SETUP.md  setup for a new trial account
```

---

## The demo

The brief's Notes section, automated:

```bash
python -m src.cli demo
```

It creates the schema, seeds 25 test cases across 7 folders, loads
30 customers / 40 policies / 50 claims through
`Staging → Transformation → Curated`, injects three defects, validates,
shows the failed records with mismatch detail, corrects them, reruns the
saved scripts from `/Regression`, then reruns everything that failed by
selecting it from execution history.

The three injected defects:

| Record | Column | Expected | Actual |
|---|---|---|---|
| `P1005` | `PREMIUM_AMOUNT` | 12000.00 | 10000.00 |
| `CL1007` | `CLAIM_STATUS` | Approved | Pending |
| `CL1010` | `POLICY_ID` | P1002 | P9999 (orphan) |

Step by step instead:

```bash
python -m src.cli init                    # repository + demo tables
python -m src.cli seed-catalog            # project, folders, test cases
python -m src.cli load-data               # ETL with the 3 defects injected
python -m src.cli run --all --show-failures
python -m src.cli fix                     # correct the defects
python -m src.cli run-regression          # rerun the /Regression folder
python -m src.cli history --start 2026-09-01 --end 2026-09-30
python -m src.cli rerun --failed-since 2026-09-01
python -m src.cli dashboard
```

---

## Command reference

| Command | Does |
|---|---|
| `doctor` | check the connection, print account/warehouse/database |
| `init` | create repository control tables and demo tables |
| `seed-catalog` | load the demo project, folders and test cases |
| `load-data [--clean]` | run the ETL pipeline, with or without defects |
| `load-file FILE [--table T] [--append] [--dry-run] [--promote]` | check a CSV / Excel file and load it into a staging table; `--promote` continues to curated |
| `promote [--no-validate]` | transform the staged batch into curated, then run `/Source-to-Target`, `/Historical`, `/Incremental` |
| `projects` / `folders` / `tests` | browse the repository |
| `run [--all\|--folder\|--tests]` | execute saved test cases |
| `history --start --end` | execution history for a date range |
| `rerun --tests` / `--failed-since` | rerun at the latest saved configuration |
| `run-regression` | run the `/Regression` folder |
| `dashboard` | pass/fail from each test's latest run |
| `demo` | the entire walkthrough |

Add `--show-failures` to `run`, `rerun` or `run-regression` for mismatch
detail in the terminal.

---

## Tests

```bash
pytest -q
```

179 tests run against an in-memory SQLite database through the same
`Connector` interface, so the repository, engine and full
corrupt → detect → correct → rerun cycle are all verified without needing
credentials.

`tests/test_curated.py` (21 more) runs the real Snowflake pipeline scripts
and the seeded curated validations on DuckDB. It uses a small dialect shim,
`tests/duckdb_support.py`, and skips unless DuckDB is installed:
`pip install duckdb`.

`tests/test_integration_snowflake.py` exercises the real pipeline and skips
automatically when Snowflake is not configured.

---

## Notes on the demo dataset

`data/*.csv` is the source of truth; `sql/02_stg_load_sample_data.sql` is
generated from it by `tools/generate_stg_load_sql.py`. The dataset is built
so a clean load passes every rule — the only failures you see are the three
that `05_introduce_mismatches.sql` deliberately creates. Every load step is
idempotent and parameterised on `:batch_id`, so the pipeline can be rerun
without accumulating duplicates.
