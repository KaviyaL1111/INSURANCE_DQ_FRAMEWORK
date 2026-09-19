-- =====================================================================
-- 06_fix_mismatches.sql
-- DEMO STEP: corrects the three defects injected by 05. Values are taken
-- from the transformation layer rather than hardcoded, so the fix is a
-- real reconciliation rather than a lucky constant.
-- Afterwards, rerun the saved tests from the Regression folder — every
-- one of them should report PASS.
-- =====================================================================

UPDATE POLICY_MASTER T
SET PREMIUM_AMOUNT = S.PREMIUM_AMOUNT,
    LAST_UPDATED_TS = CURRENT_TIMESTAMP()
FROM TRN_POLICY S
WHERE S.POLICY_ID = T.POLICY_ID
  AND T.POLICY_ID = 'P1005';

UPDATE CLAIM_MASTER T
SET CLAIM_STATUS = S.CLAIM_STATUS_DESC,
    LAST_UPDATED_TS = CURRENT_TIMESTAMP()
FROM TRN_CLAIM S
WHERE S.CLAIM_ID = T.CLAIM_ID
  AND T.CLAIM_ID = 'CL1007';

UPDATE CLAIM_MASTER T
SET POLICY_ID = S.POLICY_ID,
    LAST_UPDATED_TS = CURRENT_TIMESTAMP()
FROM TRN_CLAIM S
WHERE S.CLAIM_ID = T.CLAIM_ID
  AND T.CLAIM_ID = 'CL1010';
