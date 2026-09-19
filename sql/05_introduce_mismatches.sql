-- =====================================================================
-- 05_introduce_mismatches.sql
-- DEMO STEP: deliberately corrupts three curated records so the
-- source-to-target validations have real defects to catch. Run AFTER
-- 04_curated_merge_load.sql and BEFORE the validation suite.
--
--   P1005   PREMIUM_AMOUNT   expected 12000.00        actual 10000.00
--   CL1007  CLAIM_STATUS     expected Approved        actual Pending
--   CL1010  POLICY_ID        expected P1002           actual P9999 (orphan)
-- =====================================================================

UPDATE POLICY_MASTER SET PREMIUM_AMOUNT = 10000.00 WHERE POLICY_ID = 'P1005';

UPDATE CLAIM_MASTER  SET CLAIM_STATUS   = 'Pending' WHERE CLAIM_ID  = 'CL1007';

UPDATE CLAIM_MASTER  SET POLICY_ID      = 'P9999'   WHERE CLAIM_ID  = 'CL1010';
