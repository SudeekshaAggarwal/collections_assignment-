CREATE OR REPLACE VIEW golden_payments AS
SELECT payment_id, account_id, borrower_id, event_at, amount, payment_method, provider_id
FROM read_csv_auto('../data/payments.csv', header=true, types={'event_at':'TIMESTAMP'})
WHERE payment_status = 'SUCCESS'
QUALIFY ROW_NUMBER() OVER (PARTITION BY payment_id ORDER BY event_at DESC) = 1;

CREATE OR REPLACE VIEW first_target AS
SELECT target_id, account_id, campaign_id, target_date, recommended_channel, month
FROM (
    SELECT *, strftime(target_date, '%Y-%m') AS month,
           ROW_NUMBER() OVER (PARTITION BY account_id, strftime(target_date, '%Y-%m') ORDER BY target_date, target_id) AS rn
    FROM read_csv_auto('../data/daily_targeting.csv', header=true, types={'target_date':'DATE'})
)
WHERE rn = 1;

CREATE OR REPLACE VIEW payment_attribution AS
SELECT payment_id, account_id, event_at, amount, target_id, days_after_target
FROM (
    SELECT p.payment_id, p.account_id, p.event_at, p.amount, t.target_id,
           date_diff('day', t.target_date, CAST(p.event_at AS DATE)) AS days_after_target,
           ROW_NUMBER() OVER (PARTITION BY p.payment_id ORDER BY t.target_date DESC, t.target_id DESC) AS rn
    FROM golden_payments p
    JOIN first_target t
      ON p.account_id = t.account_id
     AND p.event_at >= t.target_date
     AND p.event_at < t.target_date + INTERVAL 30 DAY
)
WHERE rn = 1;

CREATE OR REPLACE VIEW golden_collection_episode AS
SELECT t.target_id, t.account_id, t.campaign_id, t.target_date, t.recommended_channel, t.month,
       SUM(CASE WHEN a.days_after_target BETWEEN 0 AND 3 THEN a.amount ELSE 0 END) AS recovery_3d,
       SUM(CASE WHEN a.days_after_target BETWEEN 0 AND 7 THEN a.amount ELSE 0 END) AS recovery_7d,
       SUM(CASE WHEN a.days_after_target BETWEEN 0 AND 14 THEN a.amount ELSE 0 END) AS recovery_14d,
       SUM(CASE WHEN a.days_after_target BETWEEN 0 AND 30 THEN a.amount ELSE 0 END) AS recovery_30d
FROM first_target t
LEFT JOIN payment_attribution a ON t.target_id = a.target_id
GROUP BY ALL;

SELECT strftime(event_at, '%Y-%m') AS month,
       SUM(amount) AS recovery,
       COUNT(DISTINCT payment_id) AS successful_payments,
       COUNT(DISTINCT account_id) AS paid_accounts,
       100.0 * (SUM(amount) / LAG(SUM(amount)) OVER (ORDER BY strftime(event_at, '%Y-%m')) - 1) AS mom_pct
FROM golden_payments
GROUP BY 1
ORDER BY 1;

SELECT recommended_channel,
       COUNT(DISTINCT account_id) AS targeted_accounts,
       COUNT(DISTINCT CASE WHEN recovery_7d > 0 THEN account_id END) AS paid_accounts_7d,
       SUM(recovery_7d) AS attributed_recovery_7d,
       100.0 * COUNT(DISTINCT CASE WHEN recovery_7d > 0 THEN account_id END) / COUNT(DISTINCT account_id) AS conversion_7d_pct
FROM golden_collection_episode
GROUP BY 1
ORDER BY attributed_recovery_7d DESC;

SELECT window_days,
       SUM(CASE WHEN days_after_target <= window_days THEN amount ELSE 0 END) AS attributed_recovery
FROM payment_attribution
CROSS JOIN (VALUES (3), (7), (14), (30)) AS w(window_days)
GROUP BY window_days
ORDER BY window_days;

SELECT strftime(target_date, '%Y-%m') AS month,
       COUNT(DISTINCT account_id) AS targeted_accounts,
       SUM(recovery_7d) AS recovery_7d,
       SUM(recovery_30d) AS recovery_30d,
       SUM(recovery_7d) / COUNT(DISTINCT account_id) AS recovery_7d_per_target
FROM golden_collection_episode
GROUP BY 1
ORDER BY 1;

SELECT payment_id, COUNT(*) AS rows_per_payment_id, SUM(amount) AS raw_amount
FROM read_csv_auto('../data/payments.csv', header=true)
WHERE payment_status = 'SUCCESS'
GROUP BY payment_id
HAVING COUNT(*) > 1
ORDER BY raw_amount DESC;

SELECT payment_reference, COUNT(*) AS rows, COUNT(DISTINCT account_id) AS accounts
FROM read_csv_auto('../data/payments.csv', header=true)
GROUP BY payment_reference
HAVING COUNT(DISTINCT account_id) > 1
ORDER BY accounts DESC, rows DESC;

SELECT COUNT(*) AS calls_with_borrower_mismatch
FROM read_csv_auto('../data/calls.csv', header=true) c
JOIN read_csv_auto('../data/accounts.csv', header=true) a USING (account_id)
WHERE c.borrower_id <> a.borrower_id;

SELECT campaign_name,
       COUNT(DISTINCT strategy_version) AS strategy_versions,
       COUNT(DISTINCT target_definition) AS target_definitions,
       COUNT(DISTINCT campaign_id) AS campaign_ids
FROM read_csv_auto('../data/campaigns.csv', header=true)
GROUP BY campaign_name
HAVING COUNT(DISTINCT strategy_version) > 1
    OR COUNT(DISTINCT target_definition) > 1
    OR COUNT(DISTINCT campaign_id) > 1
ORDER BY campaign_name;
