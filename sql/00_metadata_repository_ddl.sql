-- =====================================================================
-- 00_metadata_repository_ddl.sql
-- The validation REPOSITORY: projects, folders, saved test cases,
-- version history, execution history, failure detail, regression runs.
--
-- This is the control plane the application reads and writes. It is
-- deliberately separate from the insurance demo tables (01_ddl_*) — the
-- repository can manage test cases against ANY source/target, including
-- MSSQL, while living in Snowflake.
--
-- Objects are created in whatever database/schema the connection points
-- at (SNOWFLAKE_DATABASE / SNOWFLAKE_SCHEMA), so nothing here hardcodes
-- a database name. Idempotent — safe to rerun.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Application / project folder root
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_PROJECT (
    PROJECT_ID       VARCHAR(40)    NOT NULL PRIMARY KEY,
    PROJECT_NAME     VARCHAR(100)   NOT NULL,   -- uniqueness enforced by the app
    DESCRIPTION      VARCHAR(500),
    IS_ACTIVE        BOOLEAN        DEFAULT TRUE,
    CREATED_BY       VARCHAR(100),
    CREATED_TS       TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_TS       TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------
-- Configurable, nestable folder tree inside a project.
-- FOLDER_PATH is the materialised path ('/Regression', '/Source-to-Target/Policy')
-- so the UI can render a tree without recursive queries.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_FOLDER (
    FOLDER_ID        VARCHAR(40)    NOT NULL PRIMARY KEY,
    PROJECT_ID       VARCHAR(40)    NOT NULL,
    PARENT_FOLDER_ID VARCHAR(40),
    FOLDER_NAME      VARCHAR(100)   NOT NULL,
    FOLDER_PATH      VARCHAR(1000)  NOT NULL,
    FOLDER_TYPE      VARCHAR(30)    DEFAULT 'STANDARD',  -- STANDARD | REGRESSION
    DESCRIPTION      VARCHAR(500),
    IS_ACTIVE        BOOLEAN        DEFAULT TRUE,
    CREATED_BY       VARCHAR(100),
    CREATED_TS       TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------
-- Saved validation test cases. One row = the CURRENT configuration.
-- Every edit also appends the previous state to DQ_TEST_CASE_VERSION,
-- so "load the latest saved configuration" is always well defined.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_TEST_CASE (
    TEST_CASE_ID       VARCHAR(40)      NOT NULL PRIMARY KEY,  -- TC-00001, app-generated
    PROJECT_ID         VARCHAR(40)      NOT NULL,
    FOLDER_ID          VARCHAR(40)      NOT NULL,
    TEST_NAME          VARCHAR(200)     NOT NULL,   -- unique per folder, enforced by the app
    DESCRIPTION        VARCHAR(2000),
    TEST_TYPE          VARCHAR(40)      NOT NULL,   -- SQL_ROWCOUNT | SOURCE_TARGET_COMPARE
                                                    -- | ROW_COUNT_MATCH | STATUS_COLUMN | INFORMATIONAL
    VALIDATION_SCOPE   VARCHAR(40),                 -- SOURCE_TO_TARGET | HISTORICAL | INCREMENTAL | GENERIC
    ENTITY             VARCHAR(50),
    LAYER              VARCHAR(50),
    SEVERITY           VARCHAR(20)      DEFAULT 'Medium',
    SOURCE_CONNECTION  VARCHAR(50),                 -- profile name: 'snowflake' | 'mssql'
    TARGET_CONNECTION  VARCHAR(50),
    SOURCE_SQL         VARCHAR(16777216),           -- SOURCE_TARGET_COMPARE / ROW_COUNT_MATCH
    TARGET_SQL         VARCHAR(16777216),
    VALIDATION_SQL     VARCHAR(16777216),           -- SQL_ROWCOUNT / STATUS_COLUMN / INFORMATIONAL
    KEY_COLUMNS        VARCHAR(500),                -- comma-separated business key
    COMPARE_COLUMNS    VARCHAR(4000),               -- comma-separated; blank = all non-key columns
    PARAM_DEFAULTS     VARCHAR(8000),               -- JSON: {"batch_id": "BATCH_...", ...}
    TOLERANCE          NUMBER(18,8)     DEFAULT 0,  -- numeric comparison tolerance
    IS_REGRESSION      BOOLEAN          DEFAULT TRUE,
    IS_ACTIVE          BOOLEAN          DEFAULT TRUE,
    VERSION_NO         NUMBER(6,0)      DEFAULT 1,
    CREATED_BY         VARCHAR(100),
    CREATED_TS         TIMESTAMP_NTZ    DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_BY         VARCHAR(100),
    UPDATED_TS         TIMESTAMP_NTZ    DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------
-- Immutable edit history for every test case.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_TEST_CASE_VERSION (
    VERSION_ID         VARCHAR(40)      NOT NULL PRIMARY KEY,
    TEST_CASE_ID       VARCHAR(40)      NOT NULL,
    VERSION_NO         NUMBER(6,0)      NOT NULL,
    SNAPSHOT_JSON      VARCHAR(16777216),           -- full test-case row as JSON
    CHANGE_NOTE        VARCHAR(1000),
    CHANGED_BY         VARCHAR(100),
    CHANGED_TS         TIMESTAMP_NTZ    DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------
-- Execution history. One row per test case per run.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_EXECUTION_LOG (
    EXECUTION_ID        VARCHAR(40)     NOT NULL PRIMARY KEY,
    RUN_ID              VARCHAR(40)     NOT NULL,   -- groups tests executed together
    TEST_CASE_ID        VARCHAR(40)     NOT NULL,
    TEST_NAME           VARCHAR(200),
    PROJECT_ID          VARCHAR(40),
    FOLDER_PATH         VARCHAR(1000),
    TEST_VERSION_NO     NUMBER(6,0),                -- which saved config was executed
    BATCH_ID            VARCHAR(50),
    RUN_TS              TIMESTAMP_NTZ   DEFAULT CURRENT_TIMESTAMP(),
    STATUS              VARCHAR(10),                -- PASS | FAIL | ERROR
    SOURCE_ROW_COUNT    NUMBER(18,0),
    TARGET_ROW_COUNT    NUMBER(18,0),
    MATCHED_RECORD_COUNT NUMBER(18,0),
    FAILED_RECORD_COUNT NUMBER(18,0),
    DURATION_MS         NUMBER(18,0),
    RUN_MODE            VARCHAR(30),                -- ADHOC | FOLDER | REGRESSION_RERUN | FULL_SUITE
    PARAMS_JSON         VARCHAR(8000),
    ERROR_MESSAGE       VARCHAR(4000),
    EXECUTED_BY         VARCHAR(100)
);

-- ---------------------------------------------------------------------
-- Failed-record detail with expected vs actual.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_FAILURE_LOG (
    FAILURE_ID       VARCHAR(40)     NOT NULL PRIMARY KEY,
    EXECUTION_ID     VARCHAR(40)     NOT NULL,   -- scopes detail to ONE run
    TEST_CASE_ID     VARCHAR(40)     NOT NULL,
    BATCH_ID         VARCHAR(50),
    BUSINESS_KEY     VARCHAR(500),
    COLUMN_NAME      VARCHAR(200),
    EXPECTED_VALUE   VARCHAR(4000),
    ACTUAL_VALUE     VARCHAR(4000),
    FAILURE_TYPE     VARCHAR(50),    -- VALUE_MISMATCH | MISSING_IN_TARGET | EXTRA_IN_TARGET | RULE_VIOLATION
    FAILURE_REASON   VARCHAR(1000),
    FAILED_TS        TIMESTAMP_NTZ   DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------
-- Regression runs: who reran which saved tests, and how it turned out.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_REGRESSION_RUN (
    REGRESSION_ID       VARCHAR(40)    NOT NULL PRIMARY KEY,
    RUN_ID              VARCHAR(40)    NOT NULL,
    PROJECT_ID          VARCHAR(40),
    FOLDER_PATH         VARCHAR(1000),
    TRIGGERED_FROM      VARCHAR(50),   -- EXECUTION_HISTORY | REGRESSION_FOLDER | CLI | FULL_SUITE
    SELECTED_TEST_IDS   VARCHAR(4000),
    HISTORY_START       TIMESTAMP_NTZ, -- the date filter that produced the selection
    HISTORY_END         TIMESTAMP_NTZ,
    TOTAL_TESTS         NUMBER(10,0),
    PASSED_TESTS        NUMBER(10,0),
    FAILED_TESTS        NUMBER(10,0),
    ERROR_TESTS         NUMBER(10,0),
    STARTED_TS          TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP(),
    COMPLETED_TS        TIMESTAMP_NTZ,
    EXECUTED_BY         VARCHAR(100)
);

-- ---------------------------------------------------------------------
-- Incremental-load watermarks (kept current by the ETL loader).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_WATERMARK (
    PROCESS_NAME          VARCHAR(100) NOT NULL PRIMARY KEY,
    PREVIOUS_WATERMARK    TIMESTAMP_NTZ,
    BATCH_HIGH_WATERMARK  TIMESTAMP_NTZ,
    LAST_BATCH_ID         VARCHAR(50),
    ROWS_PROCESSED        NUMBER(18,0),
    LOAD_STATUS           VARCHAR(20),
    VALIDATION_STATUS     VARCHAR(20),
    UPDATED_TS            TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------
-- Per-batch load metadata (feeds file-to-stage reconciliation).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_BATCH_METADATA (
    BATCH_ID       VARCHAR(50)   NOT NULL PRIMARY KEY,
    SOURCE_SYSTEM  VARCHAR(100),
    SOURCE_FILE    VARCHAR(500),
    SOURCE_ROWS    NUMBER(18,0),
    LOADED_ROWS    NUMBER(18,0),
    REJECT_ROWS    NUMBER(18,0),
    LOAD_STATUS    VARCHAR(20),
    LOAD_TS        TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------
-- Convenience view: the LATEST execution per test case. The dashboard
-- reads this so corrected tests stop being counted as failures.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW VW_LATEST_EXECUTION AS
SELECT *
FROM (
    SELECT E.*,
           ROW_NUMBER() OVER (PARTITION BY E.TEST_CASE_ID ORDER BY E.RUN_TS DESC, E.EXECUTION_ID) AS RN
    FROM DQ_EXECUTION_LOG E
)
WHERE RN = 1;
