# Calculation performance

The largest delay was caused by work that the current view did not need.
Forecast refreshed its hidden sensitivity chart by running approximately twenty
additional forecasts. Goals repeatedly loaded the same ledger and calculated
the same balances for its funding alternatives. Dashboard repeated those reads
and inspected duplicate-posting candidates one group at a time.

Forecast now calculates sensitivity or backtesting when its analysis is opened,
or when the user requests the underlying numbers. Scenario calculations reuse
the selected spending weeks and fitted recurring schedules. Percentile bands
are calculated together, and Progress resampling uses NumPy batches while
retaining the seeded samples. Transaction review indicators and bill histories
are also calculated in batches. Sub-tab links select their destination before
refreshing, so opening Progress does not first calculate Projection.

## Reusing results

Each Store owns an in-memory cache, limited to 32 entries and approximately
64 MiB of estimated result data. Results are copied before being returned, so
editing a DataFrame, array or nested result cannot change another page's input.
The cache does not store the full Monte Carlo path matrices.

The cache expires after database writes, commits from another connection, schema
changes or the date changing. Reopening a connection, including Undo and Redo,
creates a fresh cache. Reads inside an open transaction bypass caching; this
also prevents reuse of temporary values after a SAVEPOINT rollback. Unseeded
forecasts continue to draw fresh random samples.

Arguments form part of each cache key, including account selections, history
windows, scenarios, events, seeds, path counts and forecast horizons. A changed
setting therefore cannot reuse an answer for a different scenario. Changes to
stored settings also invalidate the cache.

## Measured comparison

Measured on Windows 11 with Python 3.13.16 and Qt's offscreen platform on
2026-10-16, comparing commit `6f32390` with this implementation. Both versions
ran sequentially using the same benchmark: 9,266 fictional transactions over
72 months, four cash accounts, one loan and one investment account. No personal
database was used.

These timings record the performance pass before the Goals-to-Forecast funding
integration. Re-run the benchmark to measure the current linked-plan workflow.

| Operation | Before | After |
|---|---:|---:|
| Forecast setting change | 1,713 ms | 58 ms |
| Forecast page | 1,752 ms | 41 ms |
| Dashboard | 652 ms | 39 ms |
| Goals | 463 ms | 53 ms |
| Progress | 213 ms | 56 ms |
| Plan | 276 ms | 11 ms |
| Transactions | 109 ms | 56 ms |
| Accounts | 233 ms | 25 ms |
| Calendar | 125 ms | 38 ms |

Page values are medians of three visits, including the first visit and later
returns with reusable results. The setting-change value covers six distinct
income adjustments, rather than repeatedly requesting the same result. One
startup measurement decreased from 1,043 ms to 542 ms. These measurements include
synchronous page work and processing pending Qt events; they are not physical
display-latency measurements. Larger datasets, longer horizons and higher path
counts can take longer.

Eight forecast cases covering scenario changes, loans, investments, repeating
events, different horizons and selected-history refitting matched the original
calculation across 105,592 numerical values, using absolute tolerance $1e-9 and
relative tolerance 1e-13. Four seeded Progress comparisons retained their
p-values and confidence intervals within floating-point tolerance. The full
Linux and Windows regression suites also passed.

Run the benchmark from the repository with an activated virtual environment:

```bash
python scripts/benchmark.py --rounds 3 --json timings.json
```

Use `--months`, `--cash-accounts` and `--rounds` to vary the fictional workload.
Use identical arguments for comparisons. Add `--profile timing.prof` when
investigating a bottleneck, and measure elapsed times separately without that
instrumentation.
