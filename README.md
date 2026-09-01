# Collections Analytics Assignment

## Executive answer
The reported sustained 11% month-on-month recovery improvement is not supported. March 2026 is +11.03% versus February, but April through July do not sustain an 11% improvement. January and July full-month golden recovery are approximately equal. August is incomplete.

## Major forensic findings

- 17,534 unique successful payment IDs remain after deduplication.
- 346 duplicate successful payment rows are removed, eliminating ₹2.59 Cr of raw duplicate amount.
- payment_reference is reused across accounts and is not a valid transaction key.
- Event borrower IDs conflict with the account master at very high rates. Account-level metrics therefore use account_id and retain identity mismatches as a DQ flag.
- All observed agent IDs have multiple joined dates, so agent tenure and agent-level causal performance are not treated as reliable.
- Vendors contain multiple call timezones, so raw event hour is descriptive rather than a borrower-local calling-time KPI.
- Campaign names are reused across strategy versions and target definitions.
- August is incomplete.

## Golden dataset logic

The primary analytical grain is one first targeting event per account-month. Successful payments are deduplicated by payment_id, keeping the latest event_at. Attribution uses only the same first-target population and assigns each payment to the latest eligible first target within the selected 3, 7, 14 or 30 day window. This removes the earlier inconsistency in which later targets could receive attributed payments while being absent from the golden episode table.

## Metric definitions

- Contact rate: unique account-months with an ANSWERED call divided by unique account-months with any call event.
- RPC rate: unique account-months with an RPC-coded disposition divided by unique account-months with any call event.
- PTP rate: unique account-months with a PTP-coded disposition divided by unique account-months with an RPC-coded disposition.
- PTP kept rate: KEPT divided by KEPT plus BROKEN PTPs.
- Recovery rate: golden successful recovery divided by targeted outstanding amount. This is provisional because outstanding_amount is a current snapshot.
- Recovery per account: reported using the relevant unique targeted or paid account denominator.
- Recovery per agent-hour: not production-reliable until agent identity and session attribution are resolved.
- Cost per ₹ recovered: not estimable because the supplied schema contains no operating-cost fields.
- Channel conversion: unique first-target accounts with positive 7-day attributed recovery divided by first-target accounts in that channel. It is observational, not causal.

## ₹10 Cr decision

The Jan-Jul full-month golden recovery annualizes to ₹217.45 Cr. A ₹10 Cr investment requires 4.60% incremental annual recovery to break even.

Scenario hurdle analysis:

| Scenario | Uplift | Incremental recovery | ROI |
|---|---:|---:|---:|
| Downside | 2% | ₹4.35 Cr | -56.5% |
| Base | 5% | ₹10.87 Cr | +8.7% |
| Upside | 10% | ₹21.75 Cr | +117.5% |

These are hurdle scenarios, not causal forecasts. The recommended area is better borrower targeting, but the ₹10 Cr should be staged through a stratified randomized holdout. Scale only if the confidence interval for incremental 30-day recovery clears the 4.60% break-even hurdle and operational guardrails remain acceptable.

## Counterfactual experiment

Treatment: eligible accounts assigned to the new targeting strategy.

Control: comparable eligible accounts retained on the previous strategy.

Stratify on DPD, risk, loan type, prior recovery and geography.

Primary outcome: 30-day golden recovery per eligible account.

Identification: random assignment removes measured and unmeasured confounding in expectation.

Decision rule: scale only when the confidence interval for incremental annual recovery clears the ₹10 Cr hurdle.

## Reproducibility

Place the assignment CSV files in `data/` and run:

`python golden/build_golden_dataset.py --data-dir data --output-dir golden --reports-dir reports`

The notebook is `notebook/collections_analysis.ipynb`. The SQL is written for DuckDB and is in `sql/collections_analysis.sql`.

No R code is used in this submission. The analytical implementation is Python plus SQL.
