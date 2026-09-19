-- =====================================================================
-- show_my_connection_values.sql
-- Paste this into a Snowsight worksheet and click Run.
-- It prints the exact lines to copy into your .env file.
--
-- Run it AFTER bootstrap_snowflake.sql, so the warehouse/database/schema
-- it reports are the ones this project actually uses.
-- =====================================================================

USE WAREHOUSE DQ_WH;
USE DATABASE INSURANCE_AUTOMATION;
USE SCHEMA DQ;

SELECT 'SNOWFLAKE_ACCOUNT='   || CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME()
UNION ALL SELECT 'SNOWFLAKE_USER='      || CURRENT_USER()
UNION ALL SELECT 'SNOWFLAKE_ROLE='      || CURRENT_ROLE()
UNION ALL SELECT 'SNOWFLAKE_WAREHOUSE=' || CURRENT_WAREHOUSE()
UNION ALL SELECT 'SNOWFLAKE_DATABASE='  || CURRENT_DATABASE()
UNION ALL SELECT 'SNOWFLAKE_SCHEMA='    || CURRENT_SCHEMA()
UNION ALL SELECT 'SNOWFLAKE_PASSWORD=<the password you set when activating the trial>';
