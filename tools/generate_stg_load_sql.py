"""
Regenerate sql/02_stg_load_sample_data.sql from data/*.csv.

The CSVs are the single source of truth for the demo dataset; this keeps the
SQL in step with them. Run after editing any CSV:

    python tools/generate_stg_load_sql.py
"""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "sql", "02_stg_load_sample_data.sql")

TABLES = [
    ("STG_CUSTOMER", "customers.csv",
     ["CUSTOMER_ID", "CUSTOMER_NAME", "EMAIL", "PHONE_NUMBER", "CITY", "LAST_UPDATED_TS"]),
    ("STG_POLICY", "policies.csv",
     ["POLICY_ID", "CUSTOMER_ID", "POLICY_NUMBER", "POLICY_TYPE", "POLICY_STATUS",
      "PREMIUM_AMOUNT", "SUM_INSURED", "ISSUE_DATE", "EXPIRY_DATE", "LAST_UPDATED_TS"]),
    ("STG_CLAIM", "claims.csv",
     ["CLAIM_ID", "POLICY_ID", "CUSTOMER_ID", "CLAIM_DATE", "CLAIM_AMOUNT",
      "APPROVED_AMOUNT", "CLAIM_STATUS", "INCIDENT_TYPE", "LAST_UPDATED_TS"]),
]

NUMERIC = {"PREMIUM_AMOUNT", "SUM_INSURED", "CLAIM_AMOUNT", "APPROVED_AMOUNT"}


def lit(column: str, value: str) -> str:
    if value is None or value == "":
        return "NULL"
    if column in NUMERIC:
        return value
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    parts = [
        "-- =====================================================================",
        "-- 02_stg_load_sample_data.sql   (GENERATED — do not edit by hand)",
        "-- Source: data/*.csv   Regenerate: python tools/generate_stg_load_sql.py",
        "--",
        "-- Loads the demo source rows into staging. Idempotent: the batch's own",
        "-- rows are deleted first, so re-running the loader cannot create the",
        "-- duplicates that the duplicate-check test would then (correctly) flag.",
        "-- The batch is supplied as :batch_id, so --batch-id actually works.",
        "-- =====================================================================",
        "",
    ]
    for table, _csv, _cols in TABLES:
        parts.append(f"DELETE FROM {table} WHERE BATCH_ID = :batch_id;")
    parts.append("")

    for table, csv_name, columns in TABLES:
        with open(os.path.join(DATA, csv_name)) as f:
            rows = list(csv.DictReader(f))
        parts.append(f"-- ---------- {table} ({len(rows)} rows) ----------")
        collist = ", ".join(columns)
        parts.append(f"INSERT INTO {table} ({collist}, BATCH_ID)")
        parts.append("VALUES")
        values = [
            "    (" + ", ".join(lit(c, r[c]) for c in columns) + ", :batch_id)"
            for r in rows
        ]
        parts.append(",\n".join(values) + ";")
        parts.append("")

    parts.append("-- Record the batch so file-to-stage reconciliation has expected counts.")
    parts.append("DELETE FROM DQ_BATCH_METADATA WHERE BATCH_ID = :batch_id;")
    counts = {}
    for table, csv_name, _ in TABLES:
        with open(os.path.join(DATA, csv_name)) as f:
            counts[table] = sum(1 for _ in csv.DictReader(f))
    total = sum(counts.values())
    parts.append(
        "INSERT INTO DQ_BATCH_METADATA "
        "(BATCH_ID, SOURCE_SYSTEM, SOURCE_FILE, SOURCE_ROWS, LOADED_ROWS, REJECT_ROWS, LOAD_STATUS)"
    )
    parts.append(
        f"VALUES (:batch_id, 'CSV_FEED', 'data/*.csv', {total}, {total}, 0, 'LOADED');"
    )
    parts.append("")

    with open(OUT, "w") as f:
        f.write("\n".join(parts))
    print(f"Wrote {OUT}")
    for t, n in counts.items():
        print(f"  {t}: {n} rows")


if __name__ == "__main__":
    main()
