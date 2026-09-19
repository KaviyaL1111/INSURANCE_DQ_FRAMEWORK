-- =====================================================================
-- 01_ddl_mssql.sql   —   MSSQL (T-SQL) mirror of the demo tables
-- =====================================================================
-- Optional. Use this when you want the TARGET of a source-to-target
-- validation to live in SQL Server while the SOURCE stays in Snowflake
-- (or the other way round). The framework compares the two result sets in
-- Python, so no linked server or federation is needed.
--
-- 1. Create the database and run this script.
-- 2. Fill in the MSSQL_* variables in .env.
-- 3. `pip install pymssql` (or install the ODBC driver and use pyodbc).
-- 4. `python -m src.cli doctor --connection mssql`
-- 5. In the test-case editor, set Target connection = mssql.
-- =====================================================================

IF DB_ID('INSURANCE_AUTOMATION') IS NULL
    EXEC('CREATE DATABASE INSURANCE_AUTOMATION');
GO
USE INSURANCE_AUTOMATION;
GO

IF OBJECT_ID('dbo.POLICY_MASTER', 'U') IS NULL
CREATE TABLE dbo.POLICY_MASTER (
    POLICY_ID        VARCHAR(20)   NOT NULL PRIMARY KEY,
    CUSTOMER_ID      VARCHAR(20)   NOT NULL,
    POLICY_TYPE      VARCHAR(100)  NULL,
    POLICY_STATUS    VARCHAR(50)   NULL,
    PREMIUM_AMOUNT   DECIMAL(12,2) NULL,
    ISSUE_DATE       DATE          NULL,
    EXPIRY_DATE      DATE          NULL,
    LAST_UPDATED_TS  DATETIME2     NULL
);
GO

IF OBJECT_ID('dbo.CLAIM_MASTER', 'U') IS NULL
CREATE TABLE dbo.CLAIM_MASTER (
    CLAIM_ID         VARCHAR(20)   NOT NULL PRIMARY KEY,
    POLICY_ID        VARCHAR(20)   NOT NULL,
    CLAIM_DATE       DATE          NULL,
    CLAIM_AMOUNT     DECIMAL(12,2) NULL,
    APPROVED_AMOUNT  DECIMAL(12,2) NULL,
    CLAIM_STATUS     VARCHAR(50)   NULL,
    CLAIM_RATIO      DECIMAL(10,4) NULL,
    LAST_UPDATED_TS  DATETIME2     NULL
);
GO

IF OBJECT_ID('dbo.CUSTOMER_360', 'U') IS NULL
CREATE TABLE dbo.CUSTOMER_360 (
    CUSTOMER_ID         VARCHAR(20)   NOT NULL PRIMARY KEY,
    CUSTOMER_NAME       VARCHAR(200)  NULL,
    EMAIL               VARCHAR(200)  NULL,
    TOTAL_PREMIUM       DECIMAL(15,2) NULL,
    TOTAL_CLAIMS        INT           NULL,
    TOTAL_CLAIM_AMOUNT  DECIMAL(15,2) NULL,
    LAST_UPDATED_TS     DATETIME2     NULL
);
GO
