-- =====================================================================
-- 01_ddl_create_all_tables.sql
-- The insurance demo pipeline: Staging -> Transformation -> Curated.
--
-- Objects are created in whatever database/schema the connection points
-- at, so SNOWFLAKE_DATABASE / SNOWFLAKE_SCHEMA in your .env decide where
-- they land. (An earlier version issued USE DATABASE here, which meant
-- the tables could be built somewhere other than where the app queried.)
--
-- The DQ control tables live in 00_metadata_repository_ddl.sql.
-- Idempotent — safe to rerun.
-- =====================================================================

-- =====================================================================
-- STAGING LAYER (the source: a landing copy of the incoming feed)
-- =====================================================================
CREATE TABLE IF NOT EXISTS STG_CUSTOMER (
    CUSTOMER_ID       VARCHAR(20)      NOT NULL,
    CUSTOMER_NAME     VARCHAR(200),
    EMAIL             VARCHAR(200),
    PHONE_NUMBER      VARCHAR(20),
    CITY              VARCHAR(100),
    LAST_UPDATED_TS   TIMESTAMP_NTZ,
    BATCH_ID          VARCHAR(50)      NOT NULL,
    LOAD_TS           TIMESTAMP_NTZ    DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS STG_POLICY (
    POLICY_ID         VARCHAR(20)      NOT NULL,
    CUSTOMER_ID       VARCHAR(20)      NOT NULL,
    POLICY_NUMBER     VARCHAR(30),
    POLICY_TYPE       VARCHAR(20),
    POLICY_STATUS     VARCHAR(20),
    PREMIUM_AMOUNT    NUMBER(12,2),
    SUM_INSURED       NUMBER(15,2),
    ISSUE_DATE        DATE,
    EXPIRY_DATE       DATE,
    LAST_UPDATED_TS   TIMESTAMP_NTZ,
    BATCH_ID          VARCHAR(50)      NOT NULL,
    LOAD_TS           TIMESTAMP_NTZ    DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS STG_CLAIM (
    CLAIM_ID          VARCHAR(20)      NOT NULL,
    POLICY_ID         VARCHAR(20)      NOT NULL,
    CUSTOMER_ID       VARCHAR(20)      NOT NULL,
    CLAIM_DATE        DATE,
    CLAIM_AMOUNT      NUMBER(12,2),
    APPROVED_AMOUNT   NUMBER(12,2),
    CLAIM_STATUS      VARCHAR(20),
    INCIDENT_TYPE     VARCHAR(100),
    LAST_UPDATED_TS   TIMESTAMP_NTZ,
    BATCH_ID          VARCHAR(50)      NOT NULL,
    LOAD_TS           TIMESTAMP_NTZ    DEFAULT CURRENT_TIMESTAMP()
);

-- =====================================================================
-- TRANSFORMATION LAYER (cleaned / decoded / calculated)
-- =====================================================================
CREATE TABLE IF NOT EXISTS TRN_CUSTOMER (
    CUSTOMER_ID          VARCHAR(20)   NOT NULL,
    CUSTOMER_NAME        VARCHAR(200),
    EMAIL                VARCHAR(200),
    LOCATION_DESC        VARCHAR(100),
    BATCH_ID             VARCHAR(50)   NOT NULL,
    RECORD_EFFECTIVE_TS  TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS TRN_POLICY (
    POLICY_ID            VARCHAR(20)   NOT NULL,
    CUSTOMER_ID          VARCHAR(20)   NOT NULL,
    POLICY_TYPE_DESC     VARCHAR(100),
    POLICY_STATUS_DESC   VARCHAR(50),
    PREMIUM_AMOUNT       NUMBER(12,2),
    POLICY_TERM_DAYS     NUMBER(10,0),
    ISSUE_DATE           DATE,
    EXPIRY_DATE          DATE,
    RECORD_EFFECTIVE_TS  TIMESTAMP_NTZ,
    BATCH_ID             VARCHAR(50)   NOT NULL
);

CREATE TABLE IF NOT EXISTS TRN_CLAIM (
    CLAIM_ID             VARCHAR(20)   NOT NULL,
    POLICY_ID            VARCHAR(20)   NOT NULL,
    CLAIM_STATUS_DESC    VARCHAR(50),
    CLAIM_RATIO          NUMBER(10,4),
    OUTSTANDING_AMOUNT   NUMBER(12,2),
    CLAIM_YEAR_MONTH     VARCHAR(7),
    CLAIM_DATE           DATE,
    CLAIM_AMOUNT         NUMBER(12,2),
    APPROVED_AMOUNT      NUMBER(12,2),
    RECORD_EFFECTIVE_TS  TIMESTAMP_NTZ,
    BATCH_ID             VARCHAR(50)   NOT NULL
);

-- =====================================================================
-- CURATED LAYER (the target of the MERGE loads)
-- =====================================================================
CREATE TABLE IF NOT EXISTS CUSTOMER_360 (
    CUSTOMER_ID          VARCHAR(20)   NOT NULL PRIMARY KEY,
    CUSTOMER_NAME        VARCHAR(200),
    EMAIL                VARCHAR(200),
    TOTAL_PREMIUM        NUMBER(15,2),
    TOTAL_CLAIMS         NUMBER(10,0),
    TOTAL_CLAIM_AMOUNT   NUMBER(15,2),
    LAST_UPDATED_TS      TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS POLICY_MASTER (
    POLICY_ID          VARCHAR(20)     NOT NULL PRIMARY KEY,
    CUSTOMER_ID        VARCHAR(20)     NOT NULL,
    POLICY_TYPE        VARCHAR(100),
    POLICY_STATUS      VARCHAR(50),
    PREMIUM_AMOUNT     NUMBER(12,2),
    ISSUE_DATE         DATE,
    EXPIRY_DATE        DATE,
    LAST_UPDATED_TS    TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CLAIM_MASTER (
    CLAIM_ID          VARCHAR(20)      NOT NULL PRIMARY KEY,
    POLICY_ID         VARCHAR(20)      NOT NULL,
    CLAIM_DATE        DATE,
    CLAIM_AMOUNT      NUMBER(12,2),
    APPROVED_AMOUNT   NUMBER(12,2),
    CLAIM_STATUS      VARCHAR(50),
    CLAIM_RATIO       NUMBER(10,4),
    LAST_UPDATED_TS   TIMESTAMP_NTZ
);
