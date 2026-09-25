# Testing the flat-file features

A step-by-step guide for trying the flat-file connection and **Load into
staging** on your own machine. It takes about 20 minutes.

## What the two features do

| | Writes to the database? | What it's for |
|---|---|---|
| **Upload / browse / query a file** | No | The file is saved to the flat-file folder (`data/` by default) and read as a table: `claim.csv` → `CLAIM`. Use it as the source or target of a test case. |
| **Load into staging** | **Yes** | Inserts the file's rows into `STG_CUSTOMER`, `STG_POLICY` or `STG_CLAIM` for a batch ID, so the pipeline and test cases run on your data. |

## 0 · Set up

```bash
git pull
pip install -r requirements.txt
python -m src.cli doctor                      # Snowflake connection OK?
python -m src.cli init                        # creates the tables if they aren't there yet
python -m src.cli seed-catalog                # the demo project and test cases
streamlit run streamlit_app/DQ_Workspace.py
```

The app needs a working Snowflake (or MSSQL) connection in `.env`. See
[SNOWFLAKE_SETUP.md](SNOWFLAKE_SETUP.md).

## 1 · Awkward files — does the reader cope?

```bash
python tools/make_flatfile_samples.py
```

This writes 16 awkward files into `flatfile_samples/` and prints what each
one should turn into. On the **Flat Files** page, open **Upload files**, drop
them all in and save. Then check each file against the printed table:

- [ ] every table is listed with the expected row count
- [ ] `empty.csv` and `broken.json` are the only files listed as could not be loaded
- [ ] `WINDOWS_EXCEL_EXPORT` shows *José* / *Zoë*, not garbled characters
- [ ] `NA_IS_DATA` shows the text `NA`, and only row 3 is empty
- [ ] `book.xlsx` becomes two tables, `BOOK_MOTOR` and `BOOK_HOME`

## 2 · Your own customer / policy / claim files

Your files need these column headers (upper or lower case both work; extra
columns are ignored):

| Staging table | Columns |
|---|---|
| `STG_CUSTOMER` | CUSTOMER_ID, CUSTOMER_NAME, EMAIL, PHONE_NUMBER, CITY, LAST_UPDATED_TS |
| `STG_POLICY` | POLICY_ID, CUSTOMER_ID, POLICY_NUMBER, POLICY_TYPE, POLICY_STATUS, PREMIUM_AMOUNT, SUM_INSURED, ISSUE_DATE, EXPIRY_DATE, LAST_UPDATED_TS |
| `STG_CLAIM` | CLAIM_ID, POLICY_ID, CUSTOMER_ID, CLAIM_DATE, CLAIM_AMOUNT, APPROVED_AMOUNT, CLAIM_STATUS, INCIDENT_TYPE, LAST_UPDATED_TS |

Dates must be `YYYY-MM-DD` and timestamps `YYYY-MM-DD HH:MM:SS`.

1. Upload the three files. **Browse & profile**: check row counts, nulls and
   *Unique key?*.
2. **Load into staging**: pick a file. The staging table is guessed from
   the file name.
   - [ ] every column shows as found, or the missing ones are named
   - [ ] tick the confirmation box and press **Load into staging**
   - [ ] the message says how many rows were loaded and how many were replaced
3. Press **Run the /Staging test cases on this batch**.
4. Load the same file again with **Replace them**. The row count must stay
   the same, not double.
5. Try **Keep them (append)** once. The count should grow.

### Things that should be refused — nothing may be written

Edit a copy of `claim.csv` and try to load it with:

- [ ] a blank `CLAIM_ID` or `POLICY_ID`
- [ ] `12k` in `CLAIM_AMOUNT`
- [ ] `31/12/2024` in `CLAIM_DATE`
- [ ] a missing column (delete `INCIDENT_TYPE`)

Each one must show a red message naming the column and the row, and the
**Load** button must not appear. A **duplicate** `CLAIM_ID` is only a yellow
warning. It loads, and the *Duplicate … check* test cases should then catch it.

## 3 · Compare a file with Snowflake

**Create a reconciliation test**: source = your file, target = `STG_CLAIM`,
key = `CLAIM_ID`. Save it, then run it from **Test Cases**.

- [ ] right after loading that file, the test **passes**
- [ ] change one amount in the file (not in Snowflake) and rerun it — it
      **fails**, and shows the claim, the column, the expected value and the actual value

## Command-line alternative

```bash
python -m src.cli load-file my_claims.csv --dry-run   # checks only
python -m src.cli load-file my_claims.csv              # loads into STG_CLAIM
python -m src.cli load-file c.csv --table STG_CUSTOMER --append
python -m src.cli --connection flatfile doctor         # lists the tables it reads
```

## Reporting a problem

Please send:
- the page and the step number from this guide
- the file you used (or its first few lines)
- a screenshot of the message on screen
- the output of `python -m src.cli doctor`
