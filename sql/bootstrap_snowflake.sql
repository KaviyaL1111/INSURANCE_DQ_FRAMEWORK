-- =====================================================================
-- bootstrap_snowflake.sql   —   RUN THIS ONCE, IN THE SNOWSIGHT UI
-- =====================================================================
-- Creates the database and warehouse the framework uses. It is separate
-- from the other scripts because it needs ACCOUNTADMIN, while everything
-- afterwards runs happily as a normal role.
--
-- How to run it:
--   1. Log in to Snowsight (https://app.snowflake.com)
--   2. Projects -> Worksheets -> + (new worksheet)
--   3. Paste this whole file in, then click "Run All"
--
-- Afterwards, set these in your .env:
--   SNOWFLAKE_DATABASE=INSURANCE_AUTOMATION
--   SNOWFLAKE_SCHEMA=DQ
--   SNOWFLAKE_WAREHOUSE=DQ_WH
-- =====================================================================

USE ROLE ACCOUNTADMIN;

-- XSMALL with a 60-second auto-suspend: on a trial this costs almost
-- nothing, because the warehouse sleeps the moment you stop querying.
CREATE WAREHOUSE IF NOT EXISTS DQ_WH
    WAREHOUSE_SIZE = 'XSMALL'
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT = 'Data quality / regression automation';

CREATE DATABASE IF NOT EXISTS INSURANCE_AUTOMATION
    COMMENT = 'Insurance policy & claim DQ automation';

CREATE SCHEMA IF NOT EXISTS INSURANCE_AUTOMATION.DQ
    COMMENT = 'Staging, transformation, curated and DQ control objects';

USE WAREHOUSE DQ_WH;
USE DATABASE INSURANCE_AUTOMATION;
USE SCHEMA DQ;

SELECT CURRENT_ACCOUNT()   AS ACCOUNT,
       CURRENT_USER()      AS "USER",
       CURRENT_ROLE()      AS ROLE,
       CURRENT_WAREHOUSE() AS WAREHOUSE,
       CURRENT_DATABASE()  AS DATABASE,
       CURRENT_SCHEMA()    AS SCHEMA,
       'Bootstrap complete — now run: python -m src.cli init' AS NEXT_STEP;
