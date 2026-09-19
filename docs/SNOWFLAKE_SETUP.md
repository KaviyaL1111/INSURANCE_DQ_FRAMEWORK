# Snowflake setup — start to finish

Written for a brand-new 30-day trial. Takes about 10 minutes.

---

## 1. Find your account identifier

This is the value most people get stuck on.

1. Log in at <https://app.snowflake.com>.
2. Bottom-left, click your **account name** → hover the account in the list
   → click the **copy icon** next to the account URL.
3. You get something like `https://ab12345.ap-south-1.aws.snowflakecomputing.com`.

Your `SNOWFLAKE_ACCOUNT` is the part **before** `.snowflakecomputing.com`:

```
ab12345.ap-south-1.aws
```

Alternatively run this in a worksheet:

```sql
SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS ACCOUNT_IDENTIFIER;
```

Either format works. If the connection fails with *"Incorrect username or
password"* but you are certain the password is right, the account
identifier is almost always the real culprit.

---

## 2. Create the database and warehouse

1. In Snowsight: **Projects → Worksheets → +** for a new worksheet.
2. Paste in the whole of [`sql/bootstrap_snowflake.sql`](../sql/bootstrap_snowflake.sql).
3. Click **Run All** (top right).

That creates:

| Object | Name | Why |
|---|---|---|
| Warehouse | `DQ_WH` | XSMALL, auto-suspends after 60s so it costs almost nothing |
| Database | `INSURANCE_AUTOMATION` | holds everything |
| Schema | `DQ` | staging, transformation, curated and control tables |

---

## 3. Write your `.env`

```bash
cp .env.example .env
```

Then edit it:

```ini
DEFAULT_CONNECTION=snowflake
SNOWFLAKE_USER=YOUR_USERNAME
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_ACCOUNT=ab12345.ap-south-1.aws
SNOWFLAKE_WAREHOUSE=DQ_WH
SNOWFLAKE_DATABASE=INSURANCE_AUTOMATION
SNOWFLAKE_SCHEMA=DQ
SNOWFLAKE_ROLE=ACCOUNTADMIN
```

`.env` is git-ignored. Never commit it.

**If your trial forces MFA** (Snowflake now enables this for new accounts),
password login from a script will be blocked. Leave `SNOWFLAKE_PASSWORD`
empty and add:

```ini
SNOWFLAKE_AUTHENTICATOR=externalbrowser
```

A browser window opens once per session for you to approve. For an
unattended setup, use key-pair auth instead — see
[Snowflake's key-pair guide](https://docs.snowflake.com/en/user-guide/key-pair-auth)
and set `SNOWFLAKE_PRIVATE_KEY_PATH`.

---

## 4. Verify

```bash
source venv/bin/activate
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

---

## Troubleshooting

**`250001 Could not connect to Snowflake backend`**
The account identifier is wrong. Go back to step 1.

**`Incorrect username or password was specified`**
Either the account identifier is wrong, or MFA is on. Try
`SNOWFLAKE_AUTHENTICATOR=externalbrowser`.

**`No active warehouse selected in the current session`**
`SNOWFLAKE_WAREHOUSE` is missing or misspelled in `.env`. It must match the
warehouse you created (`DQ_WH`).

**`Object 'DQ_PROJECT' does not exist`**
Run `python -m src.cli init` — the tables have not been created yet.

**`Insufficient privileges to operate on database`**
Set `SNOWFLAKE_ROLE=ACCOUNTADMIN`, or grant your role `USAGE` on the
database and `ALL` on the schema.

**`certificate verify failed` on macOS**
Run `/Applications/Python\ 3.x/Install\ Certificates.command`.

---

## Keeping trial credits

A trial gives you $400 of credits over 30 days. This project uses a tiny
fraction of that, provided you:

- keep the warehouse at **XSMALL** — the bootstrap script does this;
- keep **auto-suspend at 60 seconds** — also in the bootstrap script;
- close the Streamlit app when you finish, so nothing keeps polling.

To check what you have spent:

```sql
SELECT WAREHOUSE_NAME, SUM(CREDITS_USED) AS CREDITS
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
GROUP BY 1;
```

The whole demo — loading the data and running the suite several times —
typically costs well under one credit.

---

## Useful Snowsight queries

```sql
USE DATABASE INSURANCE_AUTOMATION; USE SCHEMA DQ;

-- What has been created
SHOW TABLES;

-- Saved test cases
SELECT TEST_CASE_ID, TEST_NAME, TEST_TYPE, VERSION_NO FROM DQ_TEST_CASE ORDER BY TEST_CASE_ID;

-- Execution history
SELECT TEST_CASE_ID, RUN_TS, STATUS, FAILED_RECORD_COUNT
FROM DQ_EXECUTION_LOG ORDER BY RUN_TS DESC LIMIT 50;

-- Mismatch detail from the most recent runs
SELECT * FROM DQ_FAILURE_LOG ORDER BY FAILED_TS DESC LIMIT 50;

-- Start over (destructive)
-- DROP DATABASE INSURANCE_AUTOMATION;
```
