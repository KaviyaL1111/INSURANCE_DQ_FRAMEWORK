# Setup — running this on your machine

Everything below assumes a terminal (macOS/Linux) or PowerShell (Windows).
Total time: about 15 minutes, most of it waiting for a Snowflake trial email.

---

## 1. Prerequisites

- **Python 3.9 or newer.** Check with `python3 --version`.
  If missing, get it from [python.org/downloads](https://www.python.org/downloads/).
- **A Snowflake account.** A free 30-day trial is enough — step 3 below.

---

## 2. Install

Unzip the project, then from inside the project folder:

**macOS / Linux**
```bash
cd insurance_dq_framework
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**
```powershell
cd insurance_dq_framework
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> The zip does not include a `venv` folder on purpose — a virtual environment
> has absolute paths baked in and cannot be moved between machines. The three
> commands above build a fresh one.

You will know it worked when your prompt starts with `(venv)`. You need that
active in every new terminal you use for this project — just re-run the
`activate` line.

---

## 3. Get a Snowflake account

1. Sign up at **[signup.snowflake.com](https://signup.snowflake.com)** —
   Standard edition, AWS, whichever region is closest to you.
2. Activate via the email, and **write down the username and password you set**.
3. You land in **Snowsight**, the web UI at `app.snowflake.com`.

---

## 4. Create the database and warehouse

In Snowsight: **Projects → Worksheets → `+`** (top right) for a new worksheet.

Open `sql/bootstrap_snowflake.sql` from this project, copy the whole file into
the worksheet, and click **Run All**.

That creates:

| Object | Name | Purpose |
|---|---|---|
| Warehouse | `DQ_WH` | the compute. XSMALL, auto-suspends after 60s, so it costs almost nothing |
| Database | `INSURANCE_AUTOMATION` | storage |
| Schema | `DQ` | where all the tables go |

---

## 5. Configure your credentials

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Open `.env` in any editor. You only need to change **three** values:

```ini
SNOWFLAKE_USER=your_username        # from step 3
SNOWFLAKE_PASSWORD=your_password    # from step 3
SNOWFLAKE_ACCOUNT=...               # see below
```

Leave `DQ_WH`, `INSURANCE_AUTOMATION`, `DQ` and `ACCOUNTADMIN` alone — they
already match what step 4 created.

### Finding `SNOWFLAKE_ACCOUNT`

This is the one value people get wrong. It is **not** your username or email.

Easiest way: paste `sql/show_my_connection_values.sql` into your Snowsight
worksheet and run it. It prints the exact lines to copy into `.env`.

By hand instead: in Snowsight, click your **account name in the bottom-left
corner**, hover the account in the popup, click the **copy icon**. You get a
URL like `https://ab12345.ap-south-1.aws.snowflakecomputing.com` — your
account identifier is everything before `.snowflakecomputing.com`, so
`ab12345.ap-south-1.aws`.

`.env` is git-ignored and must never be shared or committed.

---

## 6. Check the connection

```bash
python -m src.cli doctor
```

Expected:

```
✓ Connected.
  Account    AB12345
  User       YOUR_USERNAME
  Role       ACCOUNTADMIN
  Warehouse  DQ_WH
  Database   INSURANCE_AUTOMATION
  Schema     DQ
```

If it fails, jump to Troubleshooting at the bottom.

---

## 7. Run it

**Everything at once** — creates tables, seeds 18 test cases across 7 folders,
loads the data, injects 3 defects, validates, corrects, reruns:

```bash
python -m src.cli demo
```

**The web app:**

```bash
streamlit run streamlit_app/DQ_Workspace.py
```

A browser tab opens at `http://localhost:8501`. Stop it with `Ctrl+C`.

The left sidebar has five pages:

| Page | What it does |
|---|---|
| Projects & Folders | create a project and its folder tree |
| Test Cases | write, edit and run validation test cases |
| Execution History | filter runs by date, tick checkboxes, rerun the selected ones |
| Dashboard | current pass/fail, mismatch detail, CSV export |
| Demo Pipeline | buttons to load data, inject defects and correct them |

**The tests** (no database needed — they use in-memory SQLite):

```bash
pytest -q
```

173 should pass, 5 skip.

---

## Troubleshooting

**`250001 Could not connect to Snowflake backend`**
`SNOWFLAKE_ACCOUNT` is wrong. Go back to step 5.

**`Incorrect username or password was specified`**
Usually still the account identifier. If you are sure it is right, your trial
has MFA enforced — blank out the password in `.env` and add:
```ini
SNOWFLAKE_AUTHENTICATOR=externalbrowser
```
A browser window will open once per session for you to approve.

**`No active warehouse selected in the current session`**
`SNOWFLAKE_WAREHOUSE` in `.env` does not match a warehouse that exists. It
should be `DQ_WH`, and step 4 must have run successfully.

**`Object 'DQ_PROJECT' does not exist`**
Run `python -m src.cli init`, then `python -m src.cli seed-catalog`.

**`SSL: CERTIFICATE_VERIFY_FAILED` on macOS**
`pip install --upgrade certifi`, or run
`/Applications/Python\ 3.x/Install\ Certificates.command`.

**`streamlit: command not found`**
The virtual environment is not active. Re-run the `activate` line from step 2.

**Port 8501 already in use**
`streamlit run streamlit_app/DQ_Workspace.py --server.port 8502`

---

## Cost

A trial gives $400 of credits over 30 days. This project uses a tiny fraction:
the warehouse is XSMALL and suspends itself after 60 seconds idle. Running the
whole demo several times costs well under one credit. Close the Streamlit app
when you are done so nothing keeps the warehouse awake.

Check spend in Snowsight:
```sql
SELECT WAREHOUSE_NAME, SUM(CREDITS_USED) AS CREDITS
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
GROUP BY 1;
```

---

More detail on Snowflake specifically is in `docs/SNOWFLAKE_SETUP.md`.
An overview of what the project does is in `README.md`.
