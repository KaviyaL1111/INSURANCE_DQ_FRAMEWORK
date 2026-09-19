# Using MSSQL as a second data source

The framework talks to Snowflake and SQL Server through the same
`Connector` interface, and every saved test case stores its SQL with
neutral `:named` placeholders. Each connector translates those into its own
driver's parameter style, so **one saved test case can point its source at
Snowflake and its target at MSSQL** — the two result sets are compared in
Python, so no linked server, external table or federation is required.

## Enabling it

1. **Install a driver.** `pip install pymssql` is the easy path — it is a
   single wheel with no system dependencies. `pyodbc` also works if you
   already have the Microsoft ODBC driver installed; the connector prefers
   `pymssql` and falls back to `pyodbc`.

2. **Create the tables.** Run `01_ddl_mssql.sql` against your instance.

3. **Configure `.env`:**

   ```
   MSSQL_HOST=localhost
   MSSQL_PORT=1433
   MSSQL_USER=sa
   MSSQL_PASSWORD=your_password
   MSSQL_DATABASE=INSURANCE_AUTOMATION
   ```

4. **Check it:** `python -m src.cli doctor --connection mssql`

5. **Use it:** in the test-case editor, a `SOURCE_TARGET_COMPARE` test shows
   a *Source connection* and a *Target connection* dropdown. Set the target
   to `mssql`.

## Running SQL Server locally

```bash
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=YourStrong!Passw0rd" \
    -p 1433:1433 -d --name mssql mcr.microsoft.com/mssql/server:2022-latest
```

On Apple Silicon use `mcr.microsoft.com/azure-sql-edge` instead, or enable
Rosetta emulation in Docker Desktop.

## What differs between the two dialects

Test-case SQL is written by you against whichever database it targets, so
dialect differences are yours to manage. Two the comparison engine already
handles for you:

- **Numeric types.** Snowflake returns `Decimal`, SQL Server may return
  `float`. The comparator normalises both before comparing, so
  `Decimal('12000.00')` and `12000.0` are equal, not a false mismatch.
- **String padding.** `CHAR` columns come back space-padded from SQL Server;
  values are trimmed before comparison.

Use the **Numeric tolerance** field on a test case when the two systems
genuinely round differently.

## Note on the repository

The repository itself (projects, folders, saved test cases, execution
history) lives in whichever database `DEFAULT_CONNECTION` names — Snowflake
in the standard setup. MSSQL is supported as a *validation source/target*,
which is what the brief calls for.
