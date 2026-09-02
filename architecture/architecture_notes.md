# Production analytics design

## Flow
Raw sources feed staging tables with schema and freshness checks. Clean tables enforce primary keys, data types, timestamp policy and deduplication. Golden tables hold trusted payment transactions and first-target account-month episodes. Feature tables hold recovery, contact, RPC and PTP features. Metrics feed the CEO dashboard.

## Data contracts
Every source should publish a stable schema, unique key, event timestamp, source timestamp where available, allowed status values and a freshness SLA. Schema changes should fail the staging contract rather than silently changing downstream metrics.

## Primary keys
payments: payment_id after deduplication. daily_targeting: target_id. calls: call_id after deduplication. call_attempts: attempt_id. call_dispositions: disposition_id. whatsapp_events: whatsapp_event_id. sms_events: sms_event_id. field_visits: visit_id. promises_to_pay: ptp_id. accounts: account_id. borrowers: borrower_id with temporal snapshots. agents: agent_id plus effective timestamps for identity history.

## Metric lineage
Monthly recovery comes from golden_payments. Attribution metrics come from first-target episodes plus golden_payments. Contact, RPC and PTP metrics come from cleaned call and disposition events. Channel conversion is explicitly observational.

## Incremental processing
Process new records by event timestamp with a configurable lookback window. Re-read recent partitions so late-arriving events can update attribution and monthly metrics.

## Late-arriving events and backfills
Keep both event_at and ingestion or recorded_at timestamps where available. Late records within the lookback are merged by primary key. Historical backfills rerun affected account-month partitions and downstream metrics.

## Data-quality checks
Monitor duplicate primary keys, null key rates, foreign-key coverage, account-borrower consistency, status-code drift, timestamp validity, vendor timezone changes, campaign-definition changes and recovery reconciliation from raw to golden.

## Anomaly detection
Alert on large changes in payment duplication rate, attribution coverage, account denominator, answer rate, RPC rate, PTP kept rate and monthly recovery. Use rolling baselines and absolute business thresholds.
