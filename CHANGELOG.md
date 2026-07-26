# Changelog

Versioning is `MAJOR.MINOR.PATCH`. While Ledgerline is a personal tool still
gaining features it stays in the `0.x` line; `1.0.0` marks the first release
intended to hand to someone else. The running version is shown in the app
header and under **Help → About**, so a downloaded copy always identifies
itself.

## 1.11.0 — Accounts → Check my balances…

Continuing the −$1,500 investigation. Running all four of the reported CSVs
against their stated balances settles it — three reconcile, one does not:

| Account | Rows | Net flow | Balance entered | Implied opening |
|---|---|---|---|---|
| Savings | 182 | **+$1,470.16** | $0.71 | **−$1,469.45** ✗ |
| MM Savings | 78 | +$0.42 | $1.33 | $0.91 ✓ |
| CD | 24 | +$1,098.66 | $1,321.23 | $222.57 ✓ |
| Visa | 44 | −$71.52 | −$71.52 | **−$0.00** ✓ |

The card landing on exactly zero — a new card, opened empty — is the check that
proves the method. Three accounts agreeing and one missing by precisely its own
net flow points at the *balance*, not the arithmetic.

- **New: Accounts → `Check my balances…`.** A per-account reconciliation table:
  rows, money in, money out, net, the balance entered, the opening balance that
  forces, and — the actionable one — **what the balance would be today had the
  account started empty**. When that figure looks right and the entered one
  doesn't, the entered one is what to fix. Figures are shown to the cent,
  because rounding can hide a small discrepancy entirely.
- **Opening balance is now measured before the first row, not after it.** Taking
  the first point of the daily series includes that day's transactions, so a
  card whose earliest line is a $1,219 purchase was reported as having opened
  $1,219 overdrawn when it opened at zero and then bought something. It now
  computes `entered − Σflows`, which is the identity being tested, and the Visa
  above lands on −$0.00 rather than being falsely flagged.

## 1.10.2 — when the ledger can't reconcile, say so

Follow-up to the −$1,500 report, using the actual CSVs. The 1.10.1 fixes were
real but were not this case. Running the numbers:

- imported flows: **+$22,882.92 in, −$21,412.76 out, net +$1,470.16**
- entered balance today: **$0.71**
- ⟹ the account is *forced* to have opened at **−$1,469.45**

`B(start) = B(today) − ΣF` is exact arithmetic, not an estimate. There is no
reconstruction bug: the inputs are mutually inconsistent, and one of them is
wrong. In this export the same transaction is posted twice under two different
descriptions (an ACH row and a legacy row for one Purdue deposit, one Costco
deposit, and several transfers) — a bank system-change artefact that the import
hash *cannot* dedupe, because the description is part of the hash.

- **New: `history.reconcile()` and a Dashboard warning.** Any cash account whose
  ledger and entered balance imply an impossible opening balance now says so
  in plain numbers — the net flow, the balance entered, the opening it forces,
  and how many rows look like the same event posted twice. Drawing a savings
  account at −$1,469 for three years without comment reads as the program being
  broken; it isn't, and the fault is upstream where the user can fix it.
- The warning lists the usual causes in likelihood order — an export missing
  withdrawals or not reaching far enough back, a duplicated export, or a stale
  balance / as-of date — and points at **Snapshots** as the way to re-anchor
  history from a known past balance.

## 1.10.1 — two bugs in the dashboard history charts

Reported as "it puts my brother at about −$1,500 several years ago, which
doesn't sound right." It wasn't. Two independent faults, both invisible on a
ledger with one cash account and no card.

- **A credit card was being counted as negative cash instead of as debt.**
  `headline_stats` has split it correctly since 0.6.1 — a card carrying a
  balance is debt; only an overpaid card is spendable cash — but
  `networth_history`, which draws the chart, never got the same fix and folded
  the whole card into cash via `is_cashlike`. So the **Net cash line sat a full
  card balance below the Net cash stat card on the same page**, and the debt
  line ignored cards entirely. Net worth cancelled the error, which is exactly
  why it survived four months: the total was right and only the two components
  were wrong. A card with $1,500 owed and no imported transactions is flat at
  −$1,500 across the entire chart, which is the reported symptom almost
  exactly. Now split per day, the same way the stat cards do it.
- **An account's history was extrapolated back before the account had any
  data.** The chart spans the *union* of every account's history, so a savings
  account imported six months ago sits on an axis that may start years earlier.
  Across that empty stretch $F(t)=0$, and $B_0 - F(t_0)$ walks today's balance
  back through inflows that had not happened yet — $5,000 funded by $6,500 of
  recent transfers reported **−$1,500 three years ago**. The series is now held
  flat before the earlier of the account's anchor date and its first
  transaction. Flat rather than dropping the account: one with no transactions
  at all already holds its balance flat across the whole chart, so this is
  consistent, and excluding it would make net worth step up on the day its
  ledger happens to begin.
- Regression tests assert the chart and the stat cards agree on cash, debt and
  net worth — including the overpaid-card case — and that no account is
  extrapolated before its own first row.

> If you have used **Progress** already, its stored checkpoints were recorded
> against the old cash definition and will score oddly against the new one.
> **Progress → Clear checkpoints…** then **Reconstruct history…** rebuilds them
> cleanly; your transactions are untouched either way.

## 1.10.0 — credited, clearer, and a categoriser that had drifted

- **Author credit.** `Created by Dr. Caleb N. Harper` on the splash and in
  Help → About. This is heading past the family that already knows who wrote it.
- **Review uncategorized: taller rows (40px)** — merchant names and the inline
  category picker were being clipped through the middle at the default height.
- **Review uncategorized: "I labelled everything and the list didn't clear."**
  Reproduced. The table was capped at 60 merchants with nothing saying so, while
  the header counted all of them — label all 60 of 80 and 20 you never saw are
  still there. The cap is now 250, the header says *"showing the top N"* when it
  bites, and the confirmation reports what remains rather than going quiet.
- **The scoring window was described misleadingly.** It said "Scoring window
  (24 months)", which is true and reads as "two years, evenly". The half-life is
  3 months, so **the last 3 months are about half the score**, the last 6 about
  three quarters, and everything past a year about 6%. The picker now says
  *"Everything the score sees (24 months, weighted)"*, and the Insights page
  states the whole thing in a line computed from the constants — so the sentence
  cannot drift from the arithmetic.
- **Progress: `Clear checkpoints…`**. Reconstruct history is easy to press twice
  and there was no way back. This resets the page to empty so it can be rebuilt,
  and says explicitly that accounts, transactions, goals and imported CSV data
  are not touched.
- **Goals: "Getting there sooner" works on any rung, not just the top one.**
  *"Can I have the computer by July while still clearing the card first?"* is a
  question about a lower priority by definition. A target picker costs any live
  goal, the figures stay on top of what is already committed above it, and when
  a date is out of reach it says so — adding that moving the goal up the ladder
  would reach it sooner, at a cost to whatever it overtakes, rather than
  pretending priority is free.

**Categorisation audit** — the rules had drifted since they were written:

- **Three false positives fixed.** Bare `UNITED` filed *United Way* under
  **Travel**, because Travel is tested before Giving. Bare `MARKET` filed
  *Facebook Marketplace* and anything mentioning the stock market under
  **Groceries**. Both are now anchored. A miss costs one click and is
  remembered; a confident mislabel silently skews the score.
- **Credit-card payments had no rule at all** — the single commonest outflow
  the categoriser missed. When only the current account is imported there is no
  opposite side for transfer-pairing to find, so they sat in Misc forever. Now
  **Debt payment**, with pairing still winning when both sides are imported.
- **~30 merchants and utilities added** — REI, Nordstrom, TJ Maxx, Petco,
  Chewy, Wayfair, Ace Hardware, Staples, Waste Management, Con Edison and
  others. The demo profiles went from 4.4% unlabelled to **0%**.
- Recurrence detection re-checked and left alone: every pattern in the demos is
  found at the right cadence and kind, with confidence ≥ 0.95.

## 1.9.0 — a Getting started guide, in the app

- **`docs/getting-started.md`**, written for someone who has just unzipped the
  folder and does not know what a CSV export is. Covers the SmartScreen prompt,
  where the data lives and that nothing leaves the machine, trying a demo first,
  adding accounts, getting a CSV out of a bank, importing it and what to check
  before pressing the button, clearing the uncategorised pile, setting goals and
  the snowball, reading the fan (the band is the answer, not the line), the
  habits score, the monthly five-minute routine, and a troubleshooting section
  for the four things that actually confuse people.
- **Readable in the app: Help → Getting started, or F1.** Qt renders Markdown
  natively, so the file that ships is the file that displays — one source, no
  conversion step, and it stays legible in a text editor and on GitHub. There is
  an *Open in my text editor* button for reading or printing it outside the app.
- **Offered where it is needed.** The empty Dashboard now has a **Show me how**
  button beside the "no data yet" line, which disappears once there is data.
- The guide is **tested against the interface**: the suite checks that every
  button label it quotes still exists in the UI, that all eight pages and all
  five demo profiles are covered, and that it renders as formatted text rather
  than raw Markdown. A guide naming a button the app no longer has is worse
  than no guide, and that drift is silent otherwise.
- `packaging/doug.spec` ships `docs/`; a build without it opens a window that
  says so rather than a blank one.

## 1.8.1 — the forecast now notices when your habits change

Reported from the turnaround demo: someone who halved their discretionary
spending five months ago and stuck to it was still being projected into a
deficit at 99% shortfall risk — while the Insights page, on the same ledger,
correctly reported a **surplus**. The two halves of the app disagreed.

- **The cause.** The behavioural bootstrap resampled every historical week with
  equal probability, so it projected the average of who you were and who you
  are. On the turnaround demo it assumed $2,222/month of discretionary spending
  when the last five months ran at $1,322. Insights has been recency-weighted
  since 0.8.0; the forecast never was.
- **The fix, and why it is conditional.** Weeks are now drawn by recency —
  same 3-month half-life Insights uses — **but only when the recent third of
  the sample is statistically distinguishable from the rest**. Reweighting
  unconditionally is not a free improvement: on a stable ledger it shrinks the
  effective sample and lets a couple of unusual weeks drag the projection, and
  it measurably *worsened* backtest coverage on the balanced demo, 61% → 10%
  inside the nominal 80% band. Stationarity is the right default.
- **The bar for "changed" is deliberately high**: a large effect (Cohen's
  d ≥ 0.8) at α = 0.01, with at least ~4 months of sustained new behaviour on
  each side of the split. An earlier version fired on a genuine in-sample
  shift in the balanced demo that turned out to be mean-reversion, and
  forecasting from it made things worse. A difference that is real in-sample
  need not persist.
- **The drift-uncertainty term now uses Kish's effective sample size**
  (`1/Σw²`) rather than the raw week count. Down-weighting old weeks means
  fewer of them genuinely inform the mean, and dividing by *n* would claim a
  precision the sample no longer has.
- Result on the turnaround demo: 2-year projection moves from −$6,883 at 99%
  shortfall to **+$6,832 at 10%**, net worth $7,150 → $21,347, matching what
  Insights says. The profiles that really are sinking still are: struggling and
  spiralling remain at 100%.

**Demo data fixed too.** Generated profiles declared monthly investment
contributions and loan payments that never appeared in the ledger, so the
forecast charged cash that the reconstructed history never showed leaving. That
is what made the thriving profile backtest at 1% coverage — a data fault read
as the model being hopeless. Those flows are now written into the ledger, where
the recurrence detector finds them and `auto_link_loans` ties each to its
account.

## 1.8.0 — a fifth demo: what the worst case actually looks like

- **New profile: Spiralling — the wheels are coming off.** An F on the habits
  score (0.4/10), three high-interest debts totalling $36,400, net worth
  −$36,280, and a projection that puts cash at **−$47,000 within a year** with a
  100% chance of going negative and a runway of zero months. Every warning the
  app has fires at once, which is the point: the other four profiles show the
  tool working, this one shows what it is *for*.
- **Sized to be internally consistent, not merely alarming.** An F needs both
  sub-scores on the floor — wants above the guideline *and* a savings rate past
  −50% — because the interaction penalty is what drags the total under 1.2, and
  it only bites when habits and means are bad together. The −$2,440/month
  deficit over the 20-month ledger is about the debt carried across the three
  accounts, so the numbers agree with each other rather than each being extreme
  on its own.
- **Debt payments are written into the ledger**, not left implicit on the
  accounts. Real statements show them, so the recurrence detector finds them
  and `auto_link_loans` ties each to its account by name — the profile behaves
  the way an imported one would instead of omitting the largest fixed cost in
  the picture. `_rows_for` gained a `debt_payments` parameter and the loader an
  `extra_loans` list.
- The goal ladder demonstrates its own edge case here: with no spare income
  nothing moves until the first loan retires in 2030, and then the freed
  payment starts the snowball. It says so plainly rather than inventing a
  schedule.

## 1.7.2 — the score no longer absolves spending it shouldn't

Found by reading the `struggling` demo and disbelieving it: someone spending 39%
of income on wants against a guideline of 30%, and 15% of income underwater, was
told *"it isn't your spending habits causing it."*

- **The cause.** The habits sub-score reads FESR = wants ÷ needs against a
  ceiling of 0.60, which is the 50/30/20 rule expressed as a ratio (30 ÷ 50) —
  that part was right and consistent. But a ratio is scale-free: both sides can
  inflate together and it still reads as disciplined. 69% of income on needs and
  39% on wants gives FESR 0.57, comfortably "within guideline", while spending
  108% of income.
- **What changed.** Not the arithmetic — the sub-scores are doing what they were
  designed to do, and that profile already graded D+. What changed is the
  *verdict*, which made a causal claim the numbers didn't support. The
  bottom-left quadrant is now split three ways using the shares of income the
  rule is actually written in: wants over 30% of income → the shortfall is
  partly the spending; wants inside 30% but essentials over 50% → *squeezed by
  fixed costs*, which absolves spending correctly; both inside → not squeezed.
- **The detail line now leads with all three shares** — needs / wants / saved
  against 50 / 30 / 20 — before quoting the ratio, so "within the guideline" can
  no longer be read off a ratio by someone who is 8% of income in deficit.
- `Aggregates.wants_share` and `.needs_share` added; regression tests cover all
  three branches plus a textbook 50/30/20 case.

## 1.7.1 — forecast page defaults

- The Forecast page opens on **Cash only** with the analysis panel off, rather
  than showing all three charts at once.

## 1.7.0 — four demo datasets, and demos that don't cost you your data

- **Demos open in their own database file.** Loading one used to write sample
  accounts into whatever database was open, which for anyone with real data in
  it was a one-way door: you could look at the demo or keep your ledger, not
  both. Each profile now lives at `demos/<name>.db` beside your real file, so
  a demo is a detour — **File → Return to my own data** puts you back exactly
  where you were, and a loud amber banner makes sure nobody mistakes an
  $86,000 demo brokerage balance for their own. Pages are handed the new store
  rather than rebuilt, so splitter positions, filters and chart toggles survive
  the trip.
- **Four situations to choose from**, each a complete worked example:
  - **Balanced** — the original CSV-based demo. Still the only path that
    exercises the import mapper end to end, which is why it stays as it was.
  - **Struggling** (D+, −15% savings) — a deficit *and* wants above the
    50/30/20 ceiling. Both were needed: the habits score deliberately grades a
    low earner with modest wants a B, "squeezed, not careless", so a profile
    meant to look bad has to be bad on the axis the score actually measures.
  - **Thriving** (A+, 53% savings) — investments compounding, debts nearly gone.
  - **Turnaround** — built for the Progress page. Discretionary spending halves
    five months in; the page detects it at p < 0.0001, the score climbs
    **C− → C− → C+ → B+ → B+** across the checkpoints, and the old projections
    show cash running clean above what they predicted.
- **Every demo arrives with checkpoint history**, so the Progress page has
  something to show the moment it is opened rather than after two months of
  waiting.
- **`Reconstruct history…` on the Progress page**, for real ledgers. Importing
  two years of history on the first run gives you everything that page needs
  and none of the checkpoints. This builds them retroactively — and builds each
  one *only from data that existed at its own date*, refitting the projection
  as of then rather than with hindsight. A backfilled checkpoint is
  indistinguishable from a real one once stored, so a retroactive projection
  built knowing the answer would make the page report the model as far better
  than it is, which is the one thing that page exists to prevent.

## 1.6.0 — Progress: was the forecast right, and did anything improve?

Every other page answers a question about *now*. This one is about the gap
between two nows, which is the only place the app can be caught being wrong and
the only place a slow improvement becomes visible.

- **Checkpoints, recorded automatically on every import.** Each one stores the
  balance sheet, the habits score and its components, and — the part that
  matters — **the forecast fan as it stood at that moment**. None of this is
  recoverable after the fact: the score is recency-weighted, so it cannot be
  recomputed for a past date once newer months exist, and a projection refitted
  later with the outcome already in the data is not a prediction. A projection
  is only a claim if you write it down before you know the answer.
- **Then-and-now chart.** The old fan, drawn against what actually happened
  since. Reports what share of days landed inside the 80% and 50% bands, and
  says plainly what that means: a well-calibrated forecast should be near 80%
  and 50% — much higher and it was too timid, much lower and it was
  overconfident. Cash that ends up outside the band entirely is called *off the
  map*, in either direction, because that is the interesting case.
- **Is the change real, or is it noise?** A permutation test on weekly
  discretionary spending, before the checkpoint against after. Twenty thousand
  reshuffles of which weeks belong to which period, asking how often chance
  alone produces a gap this large. A permutation test rather than a t-test
  because it assumes only exchangeability — weekly spend is right-skewed with
  the occasional very large week, exactly the shape that makes a t-test's
  p-value fiction at n = 8. Recurring bills are excluded: a rent rise is not a
  change of habit, and its monthly timing would break the independence the test
  needs.
  - Reported alongside is a **bootstrap confidence interval on the dollar
    difference**, which is the number that actually answers the question. A
    p-value only says "distinguishable from noise"; the interval says how much,
    and how sure.
  - **A non-significant result is never reported as "no change."** That is the
    classic error and it would be the wrong answer to the exact question this
    page exists for. It says instead how large a shift this many weeks could
    have detected at all — so "it may not feel like I improved, but I did" and
    "there isn't enough data to tell yet" stay distinguishable.
  - Per-category movement is shown but explicitly **not** tested. Twenty
    categories at 5% yields a false positive every run, and presenting that
    beside a real finding would undermine the one defensible number here.
- **Score history** across every checkpoint, and a table of them all.
- Under the hood: `finfore/progress.py`, a `checkpoints` table, and capture
  wired into `refresh_derived`, throttled so re-importing the same export twice
  in an afternoon doesn't create two checkpoints a day apart in name only.
  Statistics run on numpy alone — scipy is in the bundle exclude list, and a
  permutation test needs no special functions anyway.

## 1.5.0 — "Explain me"

- **An explainer video on the Forecast page.** The fan is the least
  self-explanatory thing in the app — a band of shaded outcomes reads as a
  prediction that got fuzzy, rather than as a distribution — so the ▶ **Explain
  me** button sits on the page where that confusion happens rather than under
  Help. Plays in a small dialog with play/pause, a scrubber and a clock.
- **Playback degrades instead of failing.** Qt's multimedia stack is a separate
  backend whose plugins are the classic thing to go missing from a PyInstaller
  bundle, and the failure mode is a black rectangle with no error. If the
  backend is absent or reports a decode error, the video is handed to whatever
  app normally opens `.mp4` files — automatically, not behind a second button.
  The button hides itself entirely if no video is bundled.
- `packaging/doug.spec` now ships `assets/`, resolved through
  `util.resource_path`, so the video works from source and from a frozen build.

## 1.4.1 — entry fix, scoped review queue, custom splits

- **Right-to-left amount entry was broken on any field that already held a
  value.** Clicking a balance of $71,520.00 and typing `500` gave
  $71,520,005.00 — the digits appended instead of replacing. The "is the whole
  field selected?" test compared the selection against the full line-edit text,
  but `QAbstractSpinBox.selectAll()` deliberately leaves the `"$ "` prefix out
  of the selection, so it never matched. It shipped because a field sitting at
  `0.00` behaves identically either way, and the tests only covered that case.
  Now compared against `cleanText()`, and a drag-selection that *does* include
  the prefix counts too. A second, subtler bug went with it: the select-all is
  posted through a zero-timer, leaving a window where the field has focus but
  nothing is highlighted — a keystroke landing there also appended. Regression
  tests drive real `QTest` keystrokes into real dialogs rather than calling the
  handler directly, which is what let the original slip through.
- **Review uncategorized can be scoped to the window the score actually reads.**
  Defaults to it. The habits score weights months by `0.5^(age/3)`, so a
  merchant last seen three years ago carries about 1/256 of last month's weight
  — labelling it is real work that moves nothing. The picker offers 3/6/12
  months, the scoring window, or everything, and reports how many older
  merchants (and how many dollars) the window is hiding. Labels still apply to
  every matching row however old; only which merchants are *offered* changes.
- **Custom distributions are now actually settable.** The custom policy was
  wired through the engine and the store but had no UI, so it silently behaved
  like focus. Selecting it puts a live $/mo box on every incomplete rung; the
  amounts are read as a ratio rather than a cap, so the whole spare pool is
  always allocated, and the note shows the resulting percentages.
- **"Getting there sooner" now answers a date with named things to give up.**
  Pick a target month and Doug works out what the goal must receive per month,
  what it already gets, and the smallest set of discretionary items that covers
  the gap — "Dining $180/mo, Subscriptions $16/mo" rather than "find another
  $196". Only wants are ever proposed: essentials and loan payments are
  excluded by construction, so if the listed cuts fall short the answer is that
  the date is out of reach, said plainly and with the size of the gap, rather
  than a suggestion to cut into rent. Unrecognised categories are offered but
  flagged, because some of that is certainly essential and the app cannot tell
  which.

## 1.4.0 — the goal ladder

- **New Goals page: one priority ladder over debts *and* savings goals.** The
  debt snowball — minimums everywhere, everything spare at one target, roll the
  payment into the next when it clears — normally stops at the last loan, and
  the freed money quietly disperses back into ordinary spending. Here loans and
  goals sit in a single ordered list and the same mechanism runs past the end of
  the debts: the car payment becomes the emergency fund, which becomes the new
  computer. Reorder with ▲/▼; the ladder is what decides where money goes.
  - Three splits, because the honest answer depends on temperament rather than
    arithmetic. **Focus** puts every spare dollar on the top rung (fastest to
    clear, the classic advice). **Weighted** splits by 1/rank so lower goals
    still visibly move — slower overall, but a goal that hasn't moved in two
    years is a goal people abandon, and an abandoned plan returns nothing.
    **Custom** takes explicit amounts.
  - **Goals share one pot and fill in priority order.** Three cash goals against
    one balance used to be three goals all reporting themselves part-funded off
    the same money. The top goal consumes the cash first now, which is what
    having a priority means.
  - **The snowball panel** dates each hand-off: *Mar 2028 — Card debt paid off →
    $150/mo rolls into Emergency fund*. **Getting there sooner** costs extra
    saving in months off the next target rather than dollars, because "save
    $100/mo more" is an instruction nobody follows and "seven months sooner" is
    a reason to.
  - **Add emergency fund…** sizes the goal from your own essential spending and
    puts it at the top — the one target mainstream guidance is unanimous about.
  - Loan minimums keep flowing whatever their rung: a small loan can retire
    before a large top-priority goal, and that is correct, not a bug.
- **Amounts are entered right-to-left, like a card terminal.** Typed digits fill
  in from the right against a fixed decimal point: `1234` is `$12.34`, `123456`
  is `$1,234.56`. There is no longer a moment where the field holds a
  half-finished number wrong by a factor of a hundred — which is how `71520.00`
  went in wrong repeatedly. Typing the amount out in full still works: the
  decimal key is ignored rather than rejected, so `1234.56` delivers the same
  six digits. Rate fields keep ordinary typing; entering `6.4875` as an APR is
  not the same task.
- **Elements within a page can be dragged to resize.** Dashboard, Insights,
  Goals and Forecast are built on splitters now, so the divider between any two
  panels is grabbable — shrink the notes to give the chart the room, then drag
  it back. Handles highlight on hover, and panes have a minimum size so a stray
  drag cannot make one vanish with no way to get it back.

## 1.3.0 — first round of family feedback

Everything here came from watching someone who did not write the app try to
use it.

- **Charts no longer scroll into nowhere.** Every timeline is now bounded to
  its own data: you cannot pan past the first balance or the end of the
  horizon, cannot zoom out into empty space, and cannot zoom in past a
  three-week window. The tester got lost scrolling and had no way back, because
  once the data is off screen nothing on the axes says which direction it went.
  Reference floors are pulled into the bounds when they are near the data and
  left out when they are a full chart-height away — a $0 line below a $500k
  projection costs every pixel of detail and tells you nothing.
- **Forecast charts lead with the projection.** History was pinned at a year
  regardless of horizon, so a 6-month projection was drawn at a third of the
  width with two thirds given to the past. History now scales with the horizon
  (30%, floor 45 days, cap 366), so the thing being asked about is the thing
  you see.
- **APR takes four decimal places.** Real loan paperwork quotes 6.4875%, and
  rounding that to 6.49% moves a long balance visibly. Entry and display both
  keep four places and trim trailing zeros, so 7% still shows as `7%`.
  Return/volatility assumptions stay at two — they are guesses, not statements.
- **Forecast page survives a narrow window.** The control column was pinned at
  374px, so shrinking the window took the space out of the charts alone until
  the cards could not shrink further and overlapped the column beside them. The
  two columns are now a splitter, and chart legends wrap onto extra rows via a
  new `FlowLayout` instead of forcing their parent wider — a nine-entry legend
  in a `QHBoxLayout` was on its own enough to overflow the page.
- **Transactions loads 25 rows, then more on request.** Every row carries a
  live category picker, so rendering thousands meant building thousands of
  widgets before the page would paint — and a list that long is not readable
  anyway. There is a *Show N more* / *Show all* pair and a **Show** filter
  (30 days / 90 days / 12 months / all time / since a date you pick). Changing
  any filter returns to the first page.
- **Loans and investments can be imported from a CSV like every other account
  — and the real reason they couldn't.** `open_import` routed on "are there any
  cash accounts?", so owning a chequing account made every loan and investment
  unreachable from File → Import CSV: the cash importer opened and its account
  list, cash-like only, simply did not contain them. The account picker now
  comes first and routing follows the type. *Update from CSV…* on the Accounts
  page routes the same way instead of claiming the account has never been
  imported.
- **…and those accounts now accept an activity export, not just balances.**
  Lenders mostly export what a bank exports — dated payments and charges, not
  a balance column. `ingest.balances_from_activity` integrates the balance
  history out of those flows and pins it to the balance you entered, the same
  way a cash ledger is anchored. The importer auto-detects which shape of file
  it was handed, previews the reconstruction, and offers a direction toggle for
  exports that sign amounts the other way. Principal-only: interest that never
  appears as a row is not invented, and the preview says so.
- **Clicking an amount field selects what's in it.** A spin box showing `0.00`
  behaved like a text field — clicking dropped a caret next to a zero the user
  then had to notice and delete, which is how `71520.00` got entered several
  times and landed wrong. Focus or first click now selects the value so typing
  replaces it. Clicks on the step arrows still step.

## 1.2.0 — Financing with Doug

- **Renamed to Financing with Doug.** Window, splash, About dialog, entry point
  and PyInstaller spec (`packaging/doug.spec`). Doug's origin story is in the
  About box; clicking the version number in the header gets Doug's opinion on
  something; the splash cycles through what Doug is currently doing. Doug is
  still not a financial advisor.
- **Splash now stays up long enough to read.** It was flashing past because
  startup is fast. `Splash.hold()` keeps it for a minimum lifetime (2.6s,
  `--splash-seconds` to change) while turning the event loop, so the window
  paints and the status line animates instead of the thread blocking on sleep.
- **Window no longer resizes itself.** `minimumSizeHint` was computing to
  ~2139x1194 — a `QStackedWidget` adopts the largest size hint among its pages,
  and the charts and cards added up — so the window was forced open past
  whatever size you set, and moved again on every page or view change. Fixed
  with an explicit `setMinimumSize(980, 620)` (an explicit minimum takes
  precedence over the hint), `Ignored` size policies on the stack and pages, and
  by driving the view switch with layout stretch factors rather than changing
  chart minimum heights. Verified stable across all six pages, all four chart
  views and the series toggles.
- **Floor labels were being clipped.** They sat at 3% along the line, directly
  on top of the y-axis tick labels. The three floors are now staggered at 90%,
  66% and 42% so they clear the axis and cannot overlap each other, and are
  anchored above the line rather than straddling it.
- **Emergency-fund and starter-buffer lines toggle independently** from the
  chart legend, on both the forecast cash chart and the dashboard history
  chart, with the state surviving a re-run.

## 1.1.0 — chart controls & packaging readiness

- **Every chart series can be switched off.** The legend is now the control:
  each entry has a checkbox beside its swatch, covering the two Monte Carlo
  bands, the median, history, the baseline overlay, all three floors, and goal
  lines. Choices survive a re-run — `SeriesRegistry` re-asserts them after the
  clear-and-redraw. The same toggles are on the dashboard history chart.
- **Forecast page decluttered.** A view picker switches between cash + net
  worth, either alone, or analysis only; the analysis panel has its own
  checkbox. A chart shown on its own grows to fill the column instead of
  sitting in a third of it.
- **Control column folds.** Scenario, debt, what-if events and goals are now
  collapsible cards — debt, events and goals start collapsed, since most people
  set them once. As a side effect the `&&` in two card titles now renders as a
  single ampersand, which it never did in a QLabel.
- **Packaging: fixed a latent blocker.** `demo.py` located `sample_data/`
  relative to `__file__`, which breaks in a frozen bundle. `util.resource_path`
  now resolves through `sys._MEIPASS` when present, so the same call works from
  source and from an executable. Verified by simulating a bundle.
- **Startup splash.** Drawn in code rather than loaded from an image, so the
  build needs no extra asset. Reports progress while the database opens and the
  interface is built, and hands off from PyInstaller's bootloader splash if one
  is present. `--no-splash` disables it.
- **`packaging/ledgerline.spec`** ships ready to build, with the data-file
  mapping, pyqtgraph hidden imports and a bundle-size exclude list already
  worked out. Argument parsing is now tolerant of OS-supplied flags, which
  macOS passes to bundled apps.

## 1.0.0 — forecast analytics

- **Backtest.** `File`-free, on the Forecast page: refit the projection at a
  past date using *only* data that existed then, and score it against what
  actually happened. Three lookahead leaks are closed explicitly — recurring
  patterns are re-detected as of the cut-off, the behavioural bootstrap is
  truncated, and starting cash comes from the reconstructed history rather than
  today's balance. Only cash is scored, because cash is exactly reconstructable
  from the ledger while loan/investment balances aren't retained historically.
  Reports coverage of the 80% and 50% bands, typical miss, and bias.
- **Bootstrap now carries parameter uncertainty — found by the backtest.**
  Resampling weeks independently made week-to-week noise average out like
  √t, so the one-year band collapsed to a few percent: it implicitly asserted
  the historical mean weekly net was known exactly. It is estimated from a
  finite sample, with standard error s/√n. Drawing one drift offset per path
  and holding it adds the term that grows *linearly* in t. Before the fix,
  reality fell inside the nominal 80% band **1%** of the time; after, **92%**
  on a well-trained window.
- **Sensitivity (tornado).** Nudges each lever ±20% independently and ranks by
  swing in ending net worth. Deterministic-only, so it runs in well under a
  second. On the demo data: income $83k swing, housing $26k, groceries $11k,
  dining $6k — an honest ordering of what actually moves the outcome.
- **What-if events.** Dated one-off shocks (new car, medical bill, bonus, job
  gap) applied to cash on the day they land, with per-event enable/disable.
  A $28k car 200 days out moves the demo from 0% to 100% probability of going
  negative — the kind of question a smooth trend cannot answer.
- **Runway counter.** "Cash covers 5.4 months of essentials at today's
  balance", shown beside the shortfall risk.
- **Fat-tailed investment shocks.** Student-t (ν=5) innovations rescaled to
  preserve each account's calibrated volatility. Mass beyond 3σ rises from
  0.26% to 1.19% — roughly 4.5× — so the bad paths are no longer quietly
  understated by the Gaussian assumption.

## 0.9.0

- **New category: Charitable donation.** Keyword rules cover named charities,
  religious giving, crowdfunding and pledge drives. Bare "FOUNDATION" and
  "OFFERING" were deliberately left out — they catch bookshops and cosmetics —
  so only anchored variants ("COMMUNITY FOUNDATION", "RELIEF FUND") match.
  Giving gets its **own taxonomy bucket** rather than being folded into wants:
  calling a tithe or a monthly charity commitment "frivolous" is a value
  judgement most people would reject, and it would drag the habits grade down
  for behaviour nobody considers a spending problem. It counts toward total
  spending and shows as its own slice in the composition chart, but stays out
  of the frivolous-to-essential ratio.
- **Forecast charts swapped.** Net cash is now the large chart on top, net
  worth the shorter one below. Cash is the account that actually runs out, and
  every floor below is a cash concept — net worth can look healthy while the
  chequing account is empty.
- **Reference floors on the money charts.** A thick red line at $0 (the line
  not to cross), a dashed amber line at the $1,000 starter buffer, and a dashed
  teal line at the emergency fund. The fund is **computed from your own
  essential spending** — bills, groceries, insurance, medical, fuel and loan
  payments, recency-weighted — not a generic figure, and the month count is
  adjustable (1–12, default 3).
- **Monte Carlo shading is now labelled.** A legend under each chart spells out
  that the outer band is 80% of outcomes (P10–P90), the inner band 50%
  (P25–P75), and the solid line the median — plus the floors and history. An
  unlabelled gradient invites people to read the median as a prediction rather
  than as the middle of a distribution.
- **Shortfall risk.** `run_forecast` now reports the probability that cash dips
  below each floor *at any point* in the horizon, the worst-dip percentiles,
  and the first date at which 1 in 10 paths are underwater. A median line that
  never goes negative can sit on top of a distribution where a quarter of paths
  do; percentiles answer "where do I end up", this answers "how likely am I to
  run out on the way".
- **Taller table rows** on Transactions (38px), Recurring and Review
  uncategorized (36px), so descriptions and inline pickers are no longer
  clipped.

## 0.8.0

- **Score rebuilt as a benchmarked quadrant model.** Two sub-scores, each
  piecewise-linear through published-guidance anchors: habits from the
  frivolous-to-essential ratio (FESR), where the 50/30/20 ceiling of 0.60 scores
  exactly 5/10, and means from the savings rate, where the 20% target scores
  8.5/10. Combined 60/40 with an interaction penalty that only bites when both
  are poor. The four cases now land where they should:
  high FESR + deficit → F; high FESR + high net → B−; low FESR + low net → B;
  low FESR + high net → A. A deepening deficit keeps lowering the score instead
  of plateauing.
- **Uncategorized spending no longer counts as frivolous.** It was inflating the
  ratio and punishing people for spending the app had simply failed to label —
  some of which is genuinely essential. It is now excluded from both sides of
  the ratio and reported separately, with the grade marked *provisional* above
  25% unknown.
- **Recency weighting everywhere.** The hard "last 3 complete months" window is
  replaced by exponential decay with a 3-month half-life over 24 months, so
  last month dominates, six months ago counts a quarter, and two years ago is
  noise. No more cliff where a large purchase silently stops counting.
- **Suggestions ranked by real dollars.** Every lever is costed first and
  ordered afterwards, and nothing is described by position unless it holds that
  position. Fixes prose like "your second-largest after subscriptions" attached
  to $500/mo of travel when subscriptions were $35/mo.
- **Review uncategorized (active-learning queue).** Transactions collapse to far
  fewer merchants than rows, so the new dialog lists unlabelled *merchants*
  ranked by how much labelling each is worth — total dollars, weighted toward
  recent and recurring spend. One decision fixes every past and future charge
  from that merchant.
- **Merchant-similarity propagation.** Character 3-gram Jaccard with a leading-
  token gate and payment-processor prefix stripping, so "SQ *BLUE BOTTLE 0042"
  matches "BLUE BOTTLE COFFEE" while "BLUE BOTTLE" stays apart from "BLUE
  CROSS". Calibrated on realistic pairs; Jaccard was the only measure of three
  that separated cleanly. Runs after labelling to reach merchants never touched.

## 0.7.0

- **Snapshots button fixed.** `SnapshotManagerDialog._reload()` was called five
  times — including from `__init__` — but never defined; its body had been
  absorbed into `_import_csv`. Constructing the dialog raised `AttributeError`
  immediately, so the button did nothing. Add / edit / delete now work, and the
  dialog is available for **every** account type rather than only loans and
  investments.
- **Snapshots mean something on cash accounts.** The newest snapshot at or after
  the account's balance date becomes the anchor the transaction ledger is
  reconstructed from (`history.cash_anchor`), so a snapshot is how you correct
  drift after an import that didn't reach far enough back.
- **Manual re-categorisation on the Transactions page**, three ways: a picker in
  each row's Category cell, a right-click menu on a selection, and the footer
  batch control. All routes go through one method.
- **Corrections are learned.** New `category_feedback` table and `learning.py`.
  Every correction is stored as a training label (normalised key, raw
  description, amount, account type, old and new label, timestamp).
  `LearnedRules` applies them immediately as an exact-match memory ahead of the
  keyword rules, so re-importing a merchant you have already fixed keeps your
  label. `CategoryModel` is the seam a trained classifier drops into;
  `training_frame()` and `coverage()` expose the corpus and report when there is
  enough of it to be worth modelling. See `ml_notes` in `learning.py`.
- **Transactions page crash fixed.** A stored date not matching the column's
  inferred format read back as `NaT`, and `_render` called `.strftime()` on it
  unguarded. `NaT.strftime()` raises, and an exception escaping a Qt slot aborts
  the process — clicking "Transactions" killed the app. Hardened at four levels:
  `store.transactions()` parses with `errors="coerce"`; `add_transactions`
  normalises on write and skips unparseable rows; `util.fmt_date` degrades to a
  placeholder; and page refresh/render are wrapped so a bad row shows a message
  instead of taking the page down.
- **`sys.excepthook` in `run.py`** shows a dialog with the traceback instead of
  letting the window vanish silently.
- **Balance-history CSV import for loans and investments** is reachable from the
  main import path: `File → Import CSV` routes by account type, and
  `File → Import balance history` is a dedicated entry.
- **Habits score rebuilt** around the non-essential-to-essential ratio
  ρ = (discretionary + incidental) / essential, with net income as a bounded
  ±2-point adjustment. Uncategorised spending now counts as non-essential.
  Replaces the old discretionary-over-income measure, which ignored incidental
  spending and compared wants to income rather than to what life actually costs.
- **`finfore/__init__.py` was empty** but `main_window` imports `__version__`
  from it — the app could not start at all. Now defines it.

## 0.6.1 — correctness fixes
Seven bugs, four of which silently produced wrong numbers rather than failing.

- **Uncategorized income was lost and cancelled real spending.** `categorize()`
  assigns `Other income` to every inflow that matches no keyword rule
  (freelance, reimbursements, gifts), but the Insights taxonomy didn't list it
  as income. That money was dropped from income *and* netted against
  uncategorized outflows in the incidental bucket, so income, spending, savings
  rate, discretionary burden, the habits score, and the donut were all wrong for
  anyone with irregular inflows. The taxonomy now derives from
  `categorize.INCOME_CATEGORIES`, and incidental counts outflows only.
- **Credit-card balances counted as both cash and debt** on the dashboard, so
  `cash + investments − debt` didn't equal the reported net worth.
- **Unlinked loan payments were charged twice** — once by the amortization
  engine and again as a generic recurring outflow — overstating monthly cash
  burn by the full payment. Now charged once, with a warning prompting the user
  to link it.
- **Deactivating a recurring pattern barely changed the forecast.** Only active
  patterns were withheld from the behavioral bootstrap, so a deactivated item's
  history was readmitted as variable spending. All known patterns are now
  withheld.
- **Month-end bills were anchored to mid-month.** Day-of-month clustering took a
  plain median across the 31→1 wraparound, so days `{1, 2, 30, 31}` yielded day
  16. The median is now taken in a shifted frame.
- **A single malformed amount aborted an entire import.** `parse_amount` raised
  on trailing-minus (`45.00-`) and other odd cells, and silently misread
  comma-decimal locales (`1.234,56` → 1.23456). It now handles both conventions
  and returns `nan` for anything unparseable, dropping the row instead.
- `update_recurring()` with no fields raised a SQL syntax error; now a no-op.
  `update_snapshot()` rolls back after a constraint violation.
- Suggestions no longer label the second-largest category "your largest"; the
  housing-note threshold is a named constant.
- Regression tests added for all of the above.

## 0.6.0
- **Insights page** — spending composition donut (essential / discretionary /
  incidental, subscriptions carved out), a habits score (0–10 + letter grade,
  weighted toward habits so circumstances aren't penalized), and dollar-ranked,
  non-judgmental reduction suggestions.
- **Snapshot history import** — bulk-import a balance-over-time CSV for a loan
  or investment account (date + balance columns), upserting by date and
  advancing the account's current balance to the newest row.
- **One-click re-import** — the column mapping used for an account's CSV is
  remembered; "Update from CSV…" imports a newer export with no re-setup
  (idempotent, so only new rows are added).
- Snapshot edit/delete, recurring-pattern delete, and goal ETAs (from prior work).

## Earlier (unversioned)
Accounts and balance-sheet modeling, CSV transaction import with debit/credit
and credit-debit-indicator sign handling, recurring detection, Monte Carlo net-
worth forecast with scenarios and goals, dark instrument-panel UI.
