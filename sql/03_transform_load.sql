-- =====================================================================
-- 03_transform_load.sql
-- Staging -> Transformation. Applies the mapping rules
-- (CUS-001..004, POL-001..006, CLM-001..006).
-- Idempotent: the batch's rows are cleared before reloading.
-- =====================================================================

DELETE FROM TRN_CUSTOMER WHERE BATCH_ID = :batch_id;
DELETE FROM TRN_POLICY   WHERE BATCH_ID = :batch_id;
DELETE FROM TRN_CLAIM    WHERE BATCH_ID = :batch_id;

-- ---------- TRN_CUSTOMER ----------
-- CUS-001 direct map | CUS-002 UPPER(TRIM(name)) | CUS-003 LOWER(TRIM(email))
-- CUS-004 INITCAP(TRIM(city))
INSERT INTO TRN_CUSTOMER (CUSTOMER_ID, CUSTOMER_NAME, EMAIL, LOCATION_DESC, BATCH_ID, RECORD_EFFECTIVE_TS)
SELECT
    TRIM(CUSTOMER_ID),
    COALESCE(NULLIF(UPPER(TRIM(CUSTOMER_NAME)), ''), 'UNKNOWN'),
    LOWER(TRIM(EMAIL)),
    COALESCE(NULLIF(INITCAP(TRIM(CITY)), ''), 'UNKNOWN'),
    BATCH_ID,
    LAST_UPDATED_TS
FROM STG_CUSTOMER
WHERE BATCH_ID = :batch_id
  AND CUSTOMER_ID IS NOT NULL;

-- ---------- TRN_POLICY ----------
-- POL-001 type decode | POL-002 status decode | POL-003 round premium
-- POL-004 term in days | POL-005 carry coverage dates | POL-006 carry watermark
INSERT INTO TRN_POLICY (POLICY_ID, CUSTOMER_ID, POLICY_TYPE_DESC, POLICY_STATUS_DESC, PREMIUM_AMOUNT,
                        POLICY_TERM_DAYS, ISSUE_DATE, EXPIRY_DATE, RECORD_EFFECTIVE_TS, BATCH_ID)
SELECT
    TRIM(POLICY_ID),
    TRIM(CUSTOMER_ID),
    CASE UPPER(TRIM(POLICY_TYPE))
        WHEN 'AUTO'   THEN 'Automobile'
        WHEN 'HOME'   THEN 'Home Insurance'
        WHEN 'HEALTH' THEN 'Health Insurance'
        WHEN 'LIFE'   THEN 'Life Insurance'
        ELSE 'Unknown'
    END,
    CASE UPPER(TRIM(POLICY_STATUS))
        WHEN 'A' THEN 'Active'
        WHEN 'I' THEN 'Inactive'
        WHEN 'C' THEN 'Cancelled'
        WHEN 'L' THEN 'Lapsed'
        ELSE 'Unknown'
    END,
    ROUND(PREMIUM_AMOUNT, 2),
    DATEDIFF(day, ISSUE_DATE, EXPIRY_DATE),
    ISSUE_DATE,
    EXPIRY_DATE,
    LAST_UPDATED_TS,
    BATCH_ID
FROM STG_POLICY
WHERE BATCH_ID = :batch_id
  AND POLICY_ID IS NOT NULL;

-- ---------- TRN_CLAIM ----------
-- CLM-001 status decode | CLM-002 claim ratio | CLM-003 outstanding amount
-- CLM-004 claim year-month | CLM-005 carry claim date | CLM-006 keep only linked claims
INSERT INTO TRN_CLAIM (CLAIM_ID, POLICY_ID, CLAIM_STATUS_DESC, CLAIM_RATIO, OUTSTANDING_AMOUNT,
                       CLAIM_YEAR_MONTH, CLAIM_DATE, CLAIM_AMOUNT, APPROVED_AMOUNT,
                       RECORD_EFFECTIVE_TS, BATCH_ID)
SELECT
    S.CLAIM_ID,
    S.POLICY_ID,
    CASE UPPER(TRIM(S.CLAIM_STATUS))
        WHEN 'P' THEN 'Pending'
        WHEN 'A' THEN 'Approved'
        WHEN 'R' THEN 'Rejected'
        WHEN 'S' THEN 'Settled'
        ELSE 'Unknown'
    END,
    ROUND(COALESCE(S.APPROVED_AMOUNT / NULLIF(S.CLAIM_AMOUNT, 0), 0), 4),
    S.CLAIM_AMOUNT - COALESCE(S.APPROVED_AMOUNT, 0),
    TO_CHAR(S.CLAIM_DATE, 'YYYY-MM'),
    S.CLAIM_DATE,
    S.CLAIM_AMOUNT,
    COALESCE(S.APPROVED_AMOUNT, 0),
    S.LAST_UPDATED_TS,
    S.BATCH_ID
FROM STG_CLAIM S
WHERE S.BATCH_ID = :batch_id
  AND S.CLAIM_ID IS NOT NULL
  AND EXISTS (SELECT 1 FROM STG_POLICY P
              WHERE P.POLICY_ID = S.POLICY_ID AND P.BATCH_ID = S.BATCH_ID);
