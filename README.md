# Data Validation & Regression Management

A Python application for creating, organising, executing and **re-running**
data validation test cases across Snowflake and MSSQL.

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
streamlit run streamlit_app/app.py
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
| Historical validation | `/Historical` folder — date-range parameters |
| Incremental load validation | `/Incremental` folder — `DQ_WATERMARK` bounds |
| Snowflake + MSSQL | `src/connectors/` — see [sql/mssql/README.md](sql/mssql/README.md) |

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

## Project layout

```
src/
  connectors/          Snowflake + MSSQL drivers behind one interface
  config.py            connection profiles from environment variables
  repository.py        projects, folders, test cases, versioning
  history.py           execution log, failure log, regression runs
  comparator.py        source-to-target row comparison
  dq_engine.py         executes a saved test case
  regression_engine.py history filtering and selective rerun
  seed.py              loads seed/demo_catalog.yaml into the repository
  cli.py               command line
streamlit_app/
  app.py               Projects & Folders
  pages/1_Test_Cases.py        browse, author, edit, run
  pages/2_Execution_History.py date filter, checkboxes, rerun
  pages/3_Dashboard.py         current state, mismatch detail, CSV export
  pages/4_Demo_Pipeline.py     drive the sample pipeline from the browser
sql/
  bootstrap_snowflake.sql        one-time: database + warehouse
  00_metadata_repository_ddl.sql the repository control tables
  01_ddl_create_all_tables.sql   the insurance demo tables
  02..06_*.sql                   load, transform, merge, corrupt, correct
  mssql/                         T-SQL mirror + how to enable MSSQL
seed/demo_catalog.yaml   17 starter test cases across 7 folders
data/*.csv               the demo dataset (source of truth for 02_*.sql)
tools/                   regenerate the load SQL from the CSVs
tests/                   107 tests, no database required
docs/SNOWFLAKE_SETUP.md  setup for a new trial account
```

---

## The demo

The brief's Notes section, automated:

```bash
python -m src.cli demo
```

It creates the schema, seeds 17 test cases across 7 folders, loads
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

107 tests run against an in-memory SQLite database through the same
`Connector` interface, so the repository, engine and full
corrupt → detect → correct → rerun cycle are all verified without needing
credentials. `tests/test_integration_snowflake.py` exercises the real
pipeline and skips automatically when Snowflake is not configured.

---

## Notes on the demo dataset

`data/*.csv` is the source of truth; `sql/02_stg_load_sample_data.sql` is
generated from it by `tools/generate_stg_load_sql.py`. The dataset is built
so a clean load passes every rule — the only failures you see are the three
that `05_introduce_mismatches.sql` deliberately creates. Every load step is
idempotent and parameterised on `:batch_id`, so the pipeline can be rerun
without accumulating duplicates.
