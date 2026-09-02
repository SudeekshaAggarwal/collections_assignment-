from pathlib import Path
import argparse
import pandas as pd
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument('--data-dir', default='../data')
parser.add_argument('--output-dir', default='.')
parser.add_argument('--reports-dir', default='../reports')
args = parser.parse_args()

base = Path(args.data_dir)
out = Path(args.output_dir)
reports = Path(args.reports_dir)
out.mkdir(parents=True, exist_ok=True)
reports.mkdir(parents=True, exist_ok=True)


def read_csv(name, **kwargs):
    return pd.read_csv(base / f'{name}.csv', **kwargs)

accounts = read_csv('accounts')
target = read_csv('daily_targeting', parse_dates=['target_date'])
payments = read_csv('payments', parse_dates=['event_at'])
calls = read_csv('calls', parse_dates=['event_at'])
call_attempts = read_csv('call_attempts', parse_dates=['event_at'])
call_dispositions = read_csv('call_dispositions', parse_dates=['event_at'])
promises = read_csv('promises_to_pay', parse_dates=['event_at', 'promised_date'])
borrowers = read_csv('borrowers', parse_dates=['created_at', 'updated_at'])
campaigns = read_csv('campaigns', parse_dates=['start_at', 'end_at'])
agents = read_csv('agents', parse_dates=['joined_at', 'updated_at'])
vendor = read_csv('vendor_telephony')
whatsapp = read_csv('whatsapp_events', parse_dates=['event_at'])

successful_raw = payments.loc[payments.payment_status.eq('SUCCESS')].copy()
successful = successful_raw.sort_values(['payment_id', 'event_at']).drop_duplicates('payment_id', keep='last').copy()
successful['month'] = successful.event_at.dt.to_period('M').astype(str)

raw_target_rows = len(target)
target['month'] = target.target_date.dt.to_period('M').astype(str)
first_target = target.sort_values(['account_id', 'month', 'target_date', 'target_id']).drop_duplicates(['account_id', 'month'], keep='first').copy()
first_target['target_rank_in_account_month'] = 1

account_master = accounts.sort_values(['account_id']).drop_duplicates('account_id', keep='last').copy()
latest_borrower = borrowers.sort_values(['borrower_id', 'updated_at']).drop_duplicates('borrower_id', keep='last')
account_master = account_master.merge(latest_borrower[['borrower_id', 'city', 'state']], on='borrower_id', how='left')
account_master = account_master[['account_id', 'borrower_id', 'dpd', 'risk_segment', 'loan_type', 'outstanding_amount', 'status', 'timezone', 'city', 'state']]

episodes = first_target.merge(account_master, on='account_id', how='left', suffixes=('', '_account'))
episodes = episodes.merge(campaigns[['campaign_id', 'strategy_version', 'campaign_name', 'target_definition', 'channel']], on='campaign_id', how='left', suffixes=('', '_campaign'))

call_dispositions['month'] = call_dispositions.event_at.dt.to_period('M').astype(str)
rpc_codes = {'PROMISE_TO_PAY', 'PTP', 'CALLBACK', 'DISPUTE', 'REFUSED', 'PAID', 'PTP_BROKEN'}
ptp_codes = {'PROMISE_TO_PAY', 'PTP', 'PTP_BROKEN'}
call_dispositions['rpc_flag'] = call_dispositions.disposition_code.isin(rpc_codes)
call_dispositions['ptp_disposition_flag'] = call_dispositions.disposition_code.isin(ptp_codes)
flags = call_dispositions.groupby(['account_id', 'month']).agg(rpc_flag=('rpc_flag', 'max'), ptp_disposition_flag=('ptp_disposition_flag', 'max')).reset_index()
episodes = episodes.merge(flags, on=['account_id', 'month'], how='left')

calls['month'] = calls.event_at.dt.to_period('M').astype(str)
contact = calls.groupby(['account_id', 'month']).call_status.apply(lambda s: s.eq('ANSWERED').any()).reset_index(name='contact_flag')
episodes = episodes.merge(contact, on=['account_id', 'month'], how='left')

promises['month'] = promises.event_at.dt.to_period('M').astype(str)
ptp_flags = promises.assign(ptp_flag=True).groupby(['account_id', 'month']).ptp_flag.max().reset_index()
episodes = episodes.merge(ptp_flags, on=['account_id', 'month'], how='left')

payment_targets = first_target[['target_id', 'account_id', 'target_date']].sort_values(['target_date', 'account_id', 'target_id'])
payment_source = successful[['payment_id', 'account_id', 'event_at', 'amount']].sort_values(['event_at', 'account_id', 'payment_id'])
attribution_rows = []
for days in [3, 7, 14, 30]:
    matched = pd.merge_asof(payment_source, payment_targets, left_on='event_at', right_on='target_date', by='account_id', direction='backward', tolerance=pd.Timedelta(days=days), allow_exact_matches=True)
    matched = matched.dropna(subset=['target_id']).copy()
    matched['window_days'] = days
    attribution_rows.append(matched)
    sums = matched.groupby('target_id').amount.sum().rename(f'recovery_{days}d')
    episodes = episodes.merge(sums, left_on='target_id', right_index=True, how='left')

for column in ['rpc_flag', 'ptp_disposition_flag', 'contact_flag', 'ptp_flag']:
    episodes[column] = episodes[column].fillna(False).astype(bool)
for column in ['recovery_3d', 'recovery_7d', 'recovery_14d', 'recovery_30d']:
    episodes[column] = episodes[column].fillna(0.0)
episodes['paid_3d_flag'] = episodes.recovery_3d.gt(0)
episodes['paid_7d_flag'] = episodes.recovery_7d.gt(0)
episodes['paid_14d_flag'] = episodes.recovery_14d.gt(0)
episodes['paid_30d_flag'] = episodes.recovery_30d.gt(0)
episodes['dpd_band'] = pd.cut(eps_dpd := episodes.dpd, bins=[-1, 7, 30, 60, 90, 10**9], labels=['0-7', '8-30', '31-60', '61-90', '90+'])

successful.to_csv(out / 'golden_payments.csv', index=False)
episodes.to_csv(out / 'golden_collection_episode.csv', index=False)

raw_recovery = successful_raw.amount.sum()
golden_recovery = successful.amount.sum()
duplicate_amount = raw_recovery - golden_recovery

monthly_index = pd.period_range('2026-01', '2026-08', freq='M').astype(str)
monthly = successful.groupby('month').agg(recovery=('amount', 'sum'), successful_payments=('payment_id', 'nunique'), paid_accounts=('account_id', 'nunique')).reindex(monthly_index, fill_value=0).reset_index().rename(columns={'index': 'month'})
monthly['mom_pct'] = monthly.recovery.pct_change().mul(100)
monthly['month_complete'] = monthly.month.ne('2026-08')
monthly['recovery_cr'] = monthly.recovery / 1e7
monthly['recovery_per_paid_account'] = monthly.recovery.div(monthly.paid_accounts.replace(0, np.nan))
monthly.to_csv(reports / 'monthly_recovery.csv', index=False)

monthly_perf = episodes.groupby('month').agg(targeted_accounts=('account_id', 'nunique'), targeted_outstanding=('outstanding_amount', 'sum'), recovery_7d=('recovery_7d', 'sum'), recovery_30d=('recovery_30d', 'sum')).reset_index()
monthly_perf = monthly_perf.merge(monthly[['month', 'recovery', 'successful_payments', 'paid_accounts', 'mom_pct', 'month_complete']], on='month', how='left')
monthly_perf['recovery_7d_per_target'] = monthly_perf.recovery_7d.div(monthly_perf.targeted_accounts.replace(0, np.nan))
monthly_perf['recovery_30d_per_target'] = monthly_perf.recovery_30d.div(monthly_perf.targeted_accounts.replace(0, np.nan))
monthly_perf['provisional_recovery_rate_pct'] = monthly_perf.recovery.div(monthly_perf.targeted_outstanding.replace(0, np.nan)).mul(100)
monthly_perf.to_csv(reports / 'monthly_performance.csv', index=False)

sensitivity = []
for days, matched in zip([3, 7, 14, 30], attribution_rows):
    total = matched.amount.sum()
    sensitivity.append({'window_days': days, 'attributed_recovery_inr': total, 'matched_successful_payments': matched.payment_id.nunique(), 'matched_accounts': matched.account_id.nunique(), 'share_of_golden_recovery_pct': total / golden_recovery * 100})
pd.DataFrame(sensitivity).to_csv(reports / 'payment_attribution_sensitivity.csv', index=False)

channel = episodes.groupby('recommended_channel', dropna=False).agg(targeted_accounts=('account_id', 'nunique'), target_events=('target_id', 'nunique'), paid_accounts_7d=('paid_7d_flag', 'sum'), attributed_recovery_7d=('recovery_7d', 'sum')).reset_index()
channel['conversion_7d_pct'] = channel.paid_accounts_7d.div(channel.targeted_accounts.replace(0, np.nan)).mul(100)
channel['observational_only'] = True
channel.to_csv(reports / 'channel_conversion_7d.csv', index=False)

for group, name in [('dpd_band', 'dpd_band_analysis'), ('risk_segment', 'risk_segment_analysis'), ('loan_type', 'loan_type_analysis'), ('state', 'geography_state_analysis'), ('city', 'geography_city_analysis')]:
    frame = episodes.groupby(group, dropna=False).agg(targeted_accounts=('account_id', 'nunique'), recovery_7d=('recovery_7d', 'sum'), recovery_30d=('recovery_30d', 'sum')).reset_index()
    frame['recovery_7d_per_target'] = frame.recovery_7d.div(frame.targeted_accounts.replace(0, np.nan))
    frame['recovery_30d_per_target'] = frame.recovery_30d.div(frame.targeted_accounts.replace(0, np.nan))
    frame.to_csv(reports / f'{name}.csv', index=False)

mix = episodes.groupby(['month', 'dpd_band'], observed=False).agg(targets=('account_id', 'nunique'), recovery=('recovery_7d', 'sum')).reset_index()
base_mix = mix[mix.month.eq('2026-01')].set_index('dpd_band')
base_weights = base_mix.targets / base_mix.targets.sum()
mix_rows = []
for month, frame in mix.groupby('month'):
    rates = frame.set_index('dpd_band').recovery.div(frame.set_index('dpd_band').targets.replace(0, np.nan))
    adjusted = sum(rates.get(band, 0) * base_weights.get(band, 0) for band in base_weights.index)
    raw = frame.recovery.sum() / frame.targets.sum() if frame.targets.sum() else np.nan
    mix_rows.append({'month': month, 'raw_recovery_7d_per_target': raw, 'dpd_mix_adjusted_recovery_7d_per_target': adjusted})
pd.DataFrame(mix_rows).to_csv(reports / 'dpd_mix_adjustment.csv', index=False)

attempts = call_attempts.copy()
attempts['month'] = attempts.event_at.dt.to_period('M').astype(str)
account_attempts = attempts.groupby('account_id').size().rename('attempts').reset_index()
account_attempts['attempt_band'] = pd.cut(account_attempts.attempts, bins=[0, 1, 2, 3, 5, 10, 100000], labels=['1', '2', '3', '4-5', '6-10', '11+'])
attempt_analysis = episodes[['account_id', 'month', 'recovery_7d']].drop_duplicates(['account_id', 'month']).merge(account_attempts, on='account_id', how='left')
attempt_report = attempt_analysis.groupby('attempt_band', observed=False).agg(accounts=('account_id', 'nunique'), recovery_7d=('recovery_7d', 'sum')).reset_index()
attempt_report['recovery_7d_per_account'] = attempt_report.recovery_7d.div(attempt_report.accounts.replace(0, np.nan))
attempt_report.to_csv(reports / 'attempt_frequency.csv', index=False)

calls['local_hour_raw'] = calls.event_at.dt.hour
hour = calls.groupby('local_hour_raw').agg(calls=('call_id', 'count'), answered_calls=('call_id', lambda s: 0)).reset_index()
answer_counts = calls.groupby('local_hour_raw').call_status.apply(lambda s: s.eq('ANSWERED').sum()).rename('answered_calls').reset_index()
hour = hour.drop(columns=['answered_calls']).merge(answer_counts, on='local_hour_raw')
hour['answer_rate_pct'] = hour.answered_calls.div(hour.calls.replace(0, np.nan)).mul(100)
hour.to_csv(reports / 'hour_answer_rate.csv', index=False)

vendor_answer = calls.groupby('vendor_id').agg(calls=('call_id', 'count')).reset_index()
vendor_answer = vendor_answer.merge(calls.assign(answered=calls.call_status.eq('ANSWERED')).groupby('vendor_id').answered.sum().rename('answered_calls').reset_index(), on='vendor_id')
vendor_answer['answer_rate_pct'] = vendor_answer.answered_calls.div(vendor_answer.calls.replace(0, np.nan)).mul(100)
vendor_answer.to_csv(reports / 'vendor_answer_rate.csv', index=False)

attempted_accounts = calls.groupby(['account_id', 'month']).size().rename('call_events').reset_index()
answered_accounts = calls[calls.call_status.eq('ANSWERED')].groupby(['account_id', 'month']).size().rename('answered_events').reset_index()
contact_metrics = attempted_accounts.merge(answered_accounts, on=['account_id', 'month'], how='left').fillna({'answered_events': 0})
contact_metrics['contact_flag'] = contact_metrics.answered_events.gt(0)
contact_kpis = contact_metrics.groupby('month').agg(call_events=('call_events', 'sum'), attempted_accounts=('account_id', 'nunique'), contacted_accounts=('contact_flag', 'sum'), answered_events=('answered_events', 'sum')).reset_index()
contact_kpis['call_answer_rate_pct'] = contact_kpis.answered_events.div(contact_kpis.call_events.replace(0, np.nan)).mul(100)
contact_kpis['account_contact_rate_pct'] = contact_kpis.contacted_accounts.div(contact_kpis.attempted_accounts.replace(0, np.nan)).mul(100)
contact_kpis.to_csv(reports / 'contact_kpis.csv', index=False)

rpc_month = call_dispositions.groupby('month').agg(disposition_events=('disposition_id', 'count'), rpc_accounts=('rpc_flag', lambda s: 0)).reset_index()
rpc_counts = call_dispositions[call_dispositions.rpc_flag].groupby('month').account_id.nunique().rename('rpc_accounts').reset_index()
rpc_month = rpc_month.drop(columns=['rpc_accounts']).merge(rpc_counts, on='month', how='left').fillna({'rpc_accounts': 0})
rpc_month = rpc_month.merge(attempted_accounts.groupby('month').account_id.nunique().rename('attempted_accounts').reset_index(), on='month', how='left')
rpc_month['rpc_rate_pct'] = rpc_month.rpc_accounts.div(rpc_month.attempted_accounts.replace(0, np.nan)).mul(100)
rpc_month.to_csv(reports / 'rpc_kpis.csv', index=False)

ptp_kept_den = promises.status.isin(['KEPT', 'BROKEN'])
ptp_kpis = pd.DataFrame([{'metric': 'total_ptps', 'value': len(promises)}, {'metric': 'kept_ptps', 'value': int(promises.status.eq('KEPT').sum())}, {'metric': 'broken_ptps', 'value': int(promises.status.eq('BROKEN').sum())}, {'metric': 'open_ptps', 'value': int(promises.status.eq('OPEN').sum())}, {'metric': 'cancelled_ptps', 'value': int(promises.status.eq('CANCELLED').sum())}, {'metric': 'ptp_kept_rate_pct', 'value': promises.loc[ptp_kept_den, 'status'].eq('KEPT').mean() * 100}])
ptp_kpis.to_csv(reports / 'ptp_kpis.csv', index=False)

campaign_conflicts = campaigns.groupby('campaign_name').agg(rows=('campaign_id', 'size'), strategy_versions=('strategy_version', 'nunique'), target_definitions=('target_definition', 'nunique'), campaign_ids=('campaign_id', 'nunique')).reset_index()
campaign_conflicts.to_csv(reports / 'campaign_definition_conflicts.csv', index=False)

strategy_mix = episodes.groupby(['month', 'strategy_version']).size().unstack(fill_value=0)
strategy_mix = strategy_mix.div(strategy_mix.sum(axis=1), axis=0).reset_index()
strategy_mix.to_csv(reports / 'strategy_mix.csv', index=False)

reference_reuse = payments.groupby('payment_reference').agg(account_count=('account_id', 'nunique'), amount_count=('amount', 'nunique')).reset_index()
borrower_conflicts = borrowers.groupby('borrower_id').agg(rows=('borrower_id', 'size'), phones=('phone', 'nunique'), emails=('email', 'nunique'), cities=('city', 'nunique'), states=('state', 'nunique')).reset_index()
agent_conflicts = agents.groupby('agent_id').agg(rows=('agent_id', 'size'), employee_codes=('employee_code', 'nunique'), names=('agent_name', 'nunique'), joined_dates=('joined_at', 'nunique'), vendors=('vendor_id', 'nunique')).reset_index()
vendor_timezones = calls.groupby('vendor_id').timezone.nunique().reset_index(name='timezone_count')

account_master_key = accounts[['account_id', 'borrower_id']].drop_duplicates('account_id')
identity_rows = []
for frame, name in [(calls, 'calls'), (call_attempts, 'call_attempts'), (call_dispositions, 'call_dispositions'), (promises, 'promises_to_pay'), (successful, 'successful_payments')]:
    merged = frame[['account_id', 'borrower_id']].merge(account_master_key, on='account_id', how='left', suffixes=('_event', '_account'))
    mismatch = int((merged.borrower_id_event.ne(merged.borrower_id_account) & merged.borrower_id_account.notna()).sum())
    missing_account = int(merged.borrower_id_account.isna().sum())
    identity_rows.append({'dataset': name, 'rows': len(frame), 'borrower_mismatch_to_account': mismatch, 'account_not_found': missing_account, 'mismatch_rate_pct': mismatch / len(frame) * 100 if len(frame) else np.nan})
identity_integrity = pd.DataFrame(identity_rows)
identity_integrity.to_csv(reports / 'identity_integrity.csv', index=False)

first_target_accounts = first_target.account_id.nunique()
unique_account_months = target[['account_id', 'month']].drop_duplicates().shape[0]
all_target_accounts = target.account_id.nunique()
channel_account_sum = first_target.groupby('recommended_channel').account_id.nunique().sum()
denominator_audit = pd.DataFrame([{'measure': 'raw_target_rows', 'value': raw_target_rows, 'interpretation': 'All targeting events before episode collapse'}, {'measure': 'unique_target_ids', 'value': target.target_id.nunique(), 'interpretation': 'Unique targeting events'}, {'measure': 'unique_account_months', 'value': unique_account_months, 'interpretation': 'Unique account-month denominator'}, {'measure': 'unique_targeted_accounts', 'value': all_target_accounts, 'interpretation': 'Unique accounts touched at least once'}, {'measure': 'first_target_account_months', 'value': len(first_target), 'interpretation': 'Primary golden episode denominator'}, {'measure': 'sum_of_first_target_channel_account_counts', 'value': channel_account_sum, 'interpretation': 'May exceed unique accounts because channels are not mutually exclusive'}])
denominator_audit.to_csv(reports / 'denominator_audit.csv', index=False)

raw_whatsapp = read_csv('whatsapp_events')
whatsapp_duplicate_count = int(raw_whatsapp.whatsapp_event_id.duplicated().sum())

august_days = int(successful.loc[successful.month.eq('2026-08'), 'event_at'].dt.date.nunique())

dq_rows = [
    {'check': 'successful_payment_rows_before_dedup', 'value': len(successful_raw), 'impact': 'Raw successful recovery reference population', 'treatment': 'Filter payment_status=SUCCESS'},
    {'check': 'unique_successful_payment_ids', 'value': successful.payment_id.nunique(), 'impact': 'Golden transaction population', 'treatment': 'Keep latest event_at per payment_id'},
    {'check': 'duplicate_successful_payment_rows', 'value': int(successful_raw.payment_id.duplicated().sum()), 'impact': 'Can inflate recovery', 'treatment': 'Deduplicate by payment_id'},
    {'check': 'duplicate_payment_amount_removed_inr', 'value': duplicate_amount, 'impact': 'Raw recovery inflation', 'treatment': 'Remove duplicate payment_id rows'},
    {'check': 'reused_payment_references', 'value': int(payments.payment_reference.duplicated().sum()), 'impact': 'Reference is not a transaction key', 'treatment': 'Do not deduplicate by payment_reference'},
    {'check': 'payment_references_spanning_multiple_accounts', 'value': int((reference_reuse.account_count > 1).sum()), 'impact': 'Confirms reference reuse across accounts', 'treatment': 'Use payment_id as transaction key'},
    {'check': 'borrower_ids_with_conflicting_snapshots', 'value': int(((borrower_conflicts[['phones', 'emails', 'cities', 'states']] > 1).any(axis=1)).sum()), 'impact': 'Historical borrower attributes can be unstable', 'treatment': 'Use temporal identity policy and flag descriptive fields'},
    {'check': 'agent_ids_with_multiple_identity_values', 'value': int(((agent_conflicts[['employee_codes', 'names', 'joined_dates', 'vendors']] > 1).any(axis=1)).sum()), 'impact': 'Agent performance and tenure can be misattributed', 'treatment': 'Use temporal identity master before agent KPI use'},
    {'check': 'vendors_with_multiple_call_timezones', 'value': int((vendor_timezones.timezone_count > 1).sum()), 'impact': 'Calling-hour comparisons can be distorted', 'treatment': 'Use explicit event timezone mapping'},
    {'check': 'duplicate_call_ids', 'value': int(calls.call_id.duplicated().sum()), 'impact': 'Call counts can be inflated', 'treatment': 'Deduplicate call_id before event-level KPIs'},
    {'check': 'duplicate_whatsapp_event_ids', 'value': whatsapp_duplicate_count, 'impact': 'Digital event counts can be inflated', 'treatment': 'Deduplicate whatsapp_event_id'},
    {'check': 'august_observed_days', 'value': august_days, 'impact': 'August is incomplete', 'treatment': 'Exclude August from full-month MoM claim'},
    {'check': 'calls_borrower_account_mismatches', 'value': int(identity_integrity.loc[identity_integrity.dataset.eq('calls'), 'borrower_mismatch_to_account'].iloc[0]), 'impact': 'Event identity cannot be trusted blindly', 'treatment': 'Flag mismatches; do not silently rewrite borrower_id'},
    {'check': 'successful_payment_borrower_account_mismatches', 'value': int(identity_integrity.loc[identity_integrity.dataset.eq('successful_payments'), 'borrower_mismatch_to_account'].iloc[0]), 'impact': 'Payment borrower attribution risk', 'treatment': 'Use account master for account-level recovery and retain mismatch flag'}
]
pd.DataFrame(dq_rows).to_csv(reports / 'data_quality_report.csv', index=False)

annual_baseline = monthly.loc[monthly.month_complete, 'recovery'].sum() / 7 * 12
investment = 100_000_000
break_even_uplift = investment / annual_baseline
scenarios = pd.DataFrame([{'scenario': 'Downside', 'uplift_pct': 0.02}, {'scenario': 'Base', 'uplift_pct': 0.05}, {'scenario': 'Upside', 'uplift_pct': 0.10}])
scenarios['annualized_baseline_recovery_inr'] = annual_baseline
scenarios['incremental_recovery_inr'] = annual_baseline * scenarios.uplift_pct
scenarios['investment_inr'] = investment
scenarios['net_value_inr'] = scenarios.incremental_recovery_inr - investment
scenarios['roi_pct'] = scenarios.net_value_inr.div(investment).mul(100)
scenarios.to_csv(reports / 'investment_scenarios.csv', index=False)
pd.DataFrame([{'annualized_jan_jul_recovery_inr': annual_baseline, 'annualized_jan_jul_recovery_cr': annual_baseline / 1e7, 'investment_inr': investment, 'break_even_incremental_uplift_pct': break_even_uplift * 100}]).to_csv(reports / 'investment_hurdle.csv', index=False)

agent_tenure = pd.DataFrame([{'metric': 'agent_ids', 'value': agents.agent_id.nunique(), 'note': 'Operational agent IDs'}, {'metric': 'agent_ids_with_multiple_joined_dates', 'value': int((agent_conflicts.joined_dates > 1).sum()), 'note': 'Tenure is not reliable without temporal identity resolution'}, {'metric': 'agent_ids_with_multiple_names', 'value': int((agent_conflicts.names > 1).sum()), 'note': 'Identity conflict'}])
agent_tenure.to_csv(reports / 'agent_tenure_analysis.csv', index=False)

metric_definitions = pd.DataFrame([
    {'metric': 'Contact rate', 'definition': 'Unique account-months with at least one ANSWERED call divided by unique account-months with at least one call event', 'status': 'Observed'},
    {'metric': 'RPC rate', 'definition': 'Unique account-months with an RPC-coded disposition divided by unique account-months with at least one call event', 'status': 'Observed, code-set dependent'},
    {'metric': 'PTP rate', 'definition': 'Unique account-months with a PTP-coded disposition divided by unique account-months with an RPC-coded disposition', 'status': 'Observed, code-set dependent'},
    {'metric': 'PTP kept rate', 'definition': 'KEPT PTPs divided by KEPT plus BROKEN PTPs', 'status': 'Observed'},
    {'metric': 'Recovery rate', 'definition': 'Golden successful recovery divided by targeted outstanding amount', 'status': 'Provisional because account outstanding is a current snapshot'},
    {'metric': 'Recovery per account', 'definition': 'Recovery divided by unique targeted accounts or unique paid accounts, reported separately', 'status': 'Observed'},
    {'metric': 'Recovery per agent-hour', 'definition': 'Recovery attributed to an agent divided by productive session hours', 'status': 'Not reliable until agent identity and session attribution are resolved'},
    {'metric': 'Cost per rupee recovered', 'definition': 'Incremental collection operating cost divided by incremental recovery', 'status': 'Not estimable because no cost fields are supplied'},
    {'metric': 'Channel conversion', 'definition': 'Unique first-target accounts with a positive attributed payment within 7 days divided by first-target accounts in that channel', 'status': 'Observed, not causal'}
])
metric_definitions.to_csv(reports / 'metric_definitions.csv', index=False)

counterfactual = pd.DataFrame([
    {'component': 'Treatment', 'design': 'Eligible accounts assigned to the new targeting strategy'},
    {'component': 'Control', 'design': 'Comparable eligible accounts retained on the previous targeting strategy'},
    {'component': 'Stratification', 'design': 'DPD band, risk segment, loan type, prior recovery, geography'},
    {'component': 'Primary outcome', 'design': '30-day golden recovery per eligible account'},
    {'component': 'Identification', 'design': 'Random assignment removes measured and unmeasured confounding in expectation'},
    {'component': 'Decision rule', 'design': 'Scale only if the confidence interval for incremental annual recovery clears the ₹10 Cr break-even hurdle'},
    {'component': 'Downside', 'design': 'No uplift or negative uplift; stop after predefined guardrails'},
    {'component': 'Limitation', 'design': 'Historical observational data cannot by itself identify causal targeting ROI'}
])
counterfactual.to_csv(reports / 'counterfactual_design.csv', index=False)

print(f'Golden payments: {len(successful):,}')
print(f'Golden episodes: {len(episodes):,}')
print(f'Break-even uplift: {break_even_uplift * 100:.2f}%')
