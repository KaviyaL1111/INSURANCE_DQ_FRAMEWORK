"""
Demo ETL pipeline driver.

Runs the numbered SQL scripts against the configured database. Every script
is parameterised on :batch_id and every load step is idempotent, so the
whole pipeline can be rerun without accumulating duplicate rows.
"""
from __future__ import annotations

import os

from src.config import DEFAULT_BATCH_ID, SQL_DIR
from src.connectors import get_connector


def _script(name: str) -> str:
    with open(os.path.join(SQL_DIR, name)) as f:
        return f.read()


def run_script(name: str, batch_id: str = DEFAULT_BATCH_ID, connector=None):
    own = connector is None
    conn = connector or get_connector()
    try:
        return conn.run_script(_script(name), {"batch_id": batch_id})
    finally:
        if own:
            conn.close()


def init_schema(connector=None):
    """Repository control tables + the insurance demo tables."""
    own = connector is None
    conn = connector or get_connector()
    try:
        conn.run_script(_script("00_metadata_repository_ddl.sql"))
        conn.run_script(_script("01_ddl_create_all_tables.sql"))
    finally:
        if own:
            conn.close()


def load_staging(batch_id: str = DEFAULT_BATCH_ID, connector=None):
    return run_script("02_stg_load_sample_data.sql", batch_id, connector)


def transform_load(batch_id: str = DEFAULT_BATCH_ID, connector=None):
    return run_script("03_transform_load.sql", batch_id, connector)


def curated_merge_load(batch_id: str = DEFAULT_BATCH_ID, connector=None):
    return run_script("04_curated_merge_load.sql", batch_id, connector)


def introduce_mismatches(batch_id: str = DEFAULT_BATCH_ID, connector=None):
    return run_script("05_introduce_mismatches.sql", batch_id, connector)


def fix_mismatches(batch_id: str = DEFAULT_BATCH_ID, connector=None):
    return run_script("06_fix_mismatches.sql", batch_id, connector)


def run_full_pipeline(batch_id: str = DEFAULT_BATCH_ID, inject_mismatches: bool = True,
                      connector=None) -> dict:
    """DDL -> staging -> transformation -> curated -> (optional) demo defects."""
    own = connector is None
    conn = connector or get_connector()
    try:
        init_schema(conn)
        load_staging(batch_id, conn)
        transform_load(batch_id, conn)
        curated_merge_load(batch_id, conn)
        if inject_mismatches:
            introduce_mismatches(batch_id, conn)
        counts = row_counts(conn, batch_id)
        return {"batch_id": batch_id, "status": "LOADED",
                "mismatches_injected": inject_mismatches, "row_counts": counts}
    finally:
        if own:
            conn.close()


def row_counts(connector=None, batch_id: str = DEFAULT_BATCH_ID) -> dict:
    own = connector is None
    conn = connector or get_connector()
    try:
        out = {}
        for table, filtered in [
            ("STG_CUSTOMER", True), ("STG_POLICY", True), ("STG_CLAIM", True),
            ("TRN_CUSTOMER", True), ("TRN_POLICY", True), ("TRN_CLAIM", True),
            ("CUSTOMER_360", False), ("POLICY_MASTER", False), ("CLAIM_MASTER", False),
        ]:
            sql = f"SELECT COUNT(*) FROM {table}"
            params = {}
            if filtered:
                sql += " WHERE BATCH_ID = :batch_id"
                params = {"batch_id": batch_id}
            out[table] = int(conn.query(sql, params).rows[0][0])
        return out
    finally:
        if own:
            conn.close()
