# Methodology

How every number on screen is actually arrived at. This was the back half of
`README.md` until that page was rewritten for the people being handed the
program; none of it has changed, it just no longer sits between a relative and
the install instructions.

If you want the same thing in two paragraphs, **Help → Methodology** in the app
is the short version, and [`whats-new.md`](whats-new.md) is the plain-English
account of what each release changed.

---

**Data quality.** The forecast's visible check reads the database without
modifying it. A balance is flagged when its most recent account date or
snapshot is more than 30 days old, or lies in the future. Checking, savings
and credit-card accounts are checked individually for transactions through
today; a latest date older than 30 days is flagged. Those cutoffs are reminders,
not measures of forecast accuracy. A non-transfer, nonzero cash transaction
span shorter than 90 days is flagged as a short history.

For each cash ledger, the check looks for empty months among the last 12
completed calendar months, after the first recorded month. It excludes the
current unfinished month and counts transfers as activity. A flagged month
may simply have no activity; the app cannot establish that an export is
missing. Loan and investment accounts are not expected to have daily ledgers.

Category review totals cover the last 180 days through today, inclusive:
nonzero cash transactions labeled Misc, Other income or blank. The outflow
share divides their negative amounts by all recorded non-transfer cash
outflows in that window. Inflows are shown separately, not netted against
spending. These are unweighted recorded amounts, not the spending score's
recency-weighted estimates, and known labels are not independently verified.

The same baseline schedule and account-assignment rules as the calendar
identify outflows without a payment account in the next 30 days. They remain
part of the combined projection but do not affect an account-specific calendar
balance. As on the calendar, listed loan payments are not payoff-truncated.
Invalid and future transaction dates are reported separately and excluded
from the historical checks; the check itself does not change the forecast
engine or any stored record. Passing every check establishes neither ledger
completeness nor future accuracy.

**History reconstruction.** For a cash account with an anchor balance
$B_0$ entered as of $t_0$ and daily net ledger flow $f(t)$ with cumulative
sum $F(t)=\sum_{\tau\le t} f(\tau)$, the balance series is

$$B(t) = B_0 + F(t) - F(t_0),$$

which is exact wherever the ledger is complete and anchored to your true
balance even if imports don't reach back to the account's origin. $B_0$ is the
account's current balance, or the most recent snapshot if you have added one
dated at or after it — which is how a snapshot corrects drift on a cash
account.

The series is held **flat before the earlier of the account's anchor date and
its first transaction**, because before that the account knows nothing. The
chart spans the *union* of every account's history, so an account imported six
months ago sits on an axis that may start years earlier; across that empty
stretch $F(t)=0$, and $B_0-F(t_0)$ then walks today's balance back through
flows that had not happened yet, producing a confident and badly wrong number.
Flat is the right fill rather than dropping the account, because an account
with no transactions at all already holds its balance flat across the whole
chart — and excluding it instead would make net worth step up on the day its
ledger happens to begin.

A **credit card is debt, not negative cash**: its balance is split per day the
same way the stat cards split it, into $\max(0,-B)$ of debt and $\max(0,B)$ of
cash. Folding the whole card into cash left the chart's net-cash line a full
card balance below the net-cash figure on the same page, with the debt line
ignoring cards entirely; net worth cancelled the error, which is why only the
components were visibly wrong. Loan and investment histories interpolate
linearly in time through balance snapshots. Net worth is
$N(t) = C(t) + \sum_i V_i(t) - \sum_j L_j(t)$ for cash $C$, investments $V$,
and debts $L$.

**Balance history from two shapes of file.** Loan and investment accounts accept
either a *balance* export (a date and a balance per row) or an *activity* export
(dated payments and charges, far more common). For the latter the balance
history is integrated out of those flows and pinned to the balance you entered
for the account, exactly as a cash ledger is anchored:

$$B(t) = B(t_0) - (F(t) - F(t_0)),$$

so the newest point is a figure you vouched for and older points are walked
back from it. This is principal-only — interest that never appears as a row in
the file is not invented, so balances far behind the anchor read slightly high
on an interest-bearing loan; it is the shape of the history, not a statement of
record.

**Recurring detection.** Descriptions are normalized (case-folded, noise
tokens and trailing identifiers stripped) and grouped by (name, sign).
Groups with at least three events are classified by their inter-arrival
intervals: the median interval and its dispersion are tested against
cadence targets (weekly 7 d, biweekly 14 d, twice-monthly, monthly 30.4 d,
quarterly, annual), with twice-monthly distinguished from biweekly by the
presence of exactly two day-of-month clusters. Each item gets a confidence
score $c = 0.30\,c_{\text{count}} + 0.40\,c_{\text{interval}} +
0.30\,c_{\text{amount}}$, and items below 0.45 are dropped. Forward
amounts use the median observed amount.

**Forward model.** Time advances on a daily grid from today through the
horizon. Active recurring items replay on their schedule (days of month
clamped to short months). Loans update monthly on their payment day at
$r_m = \mathrm{APR}/12$:

$$B_{k+1} = B_k(1 + r_m) - p_k,$$

with the final payment truncated at payoff and accrued interest tracked.
Scenario extra payment is applied avalanche-style (highest APR first), and
when a loan retires its payment can optionally snowball into the next. If
a loan has no known payment, a 60-month amortizing payment
$p = B_0 r_m / \left(1-(1+r_m)^{-60}\right)$ is assumed and flagged.

Investment accounts follow geometric Brownian motion stepped at month
starts with the true month length $\Delta t_k$ (in years):

$$V_{k+1} = V_k \exp\!\left[(\mu - \tfrac{1}{2}\sigma^2)\,\Delta t_k
+ \sigma\sqrt{\Delta t_k}\,Z_k\right] + a, \qquad Z_k \sim \mathcal N(0,1),$$

where $\mu$ is the expected annual return, $\sigma$ the annual volatility,
and $a$ the monthly contribution (debited from cash). The deterministic
path uses the per-step expectation $e^{\mu \Delta t_k}$; since the
lognormal mean exceeds its median, the deterministic line sits slightly
above the Monte Carlo P50 — that gap is real, not a bug.

Investment shocks are Student-t (ν=5) rescaled to unit variance rather than
Gaussian, because real returns have fatter tails than a normal admits.

**Variable spending.** Everything not matched to a pattern is treated as
behavior. Historical discretionary spend is binned into a complete-weeks ×
categories matrix; each simulated week resamples an entire historical row,
which preserves the correlation structure across categories (a heavy
travel week is also a heavy dining week), then scales by the scenario's
per-category multipliers before being spread onto days. Irregular income
is bootstrapped the same way under the income multiplier. At least four
complete weeks of history are required; otherwise variable spending is
zero and a warning is shown.

**History selection.** The advanced Forecast history control is saved per
database. Automatic retains the existing cap of 78 recorded weekly samples.
A rolling period applies a hard calendar cut-off relative to the projection's
date; Since a date applies a fixed cut-off; All available history removes the
cap. Explicit selections include only Monday–Sunday weeks fully inside both
the cut-off and the recorded ledger span. Quiet weeks inside that span remain
zero-spending samples. No older week is used to fill a short selection.

The optional payday/bill refit detects patterns within the same period without
editing saved patterns. It preserves disabled/ignored status and loan links,
uses recently detected amounts and cadences, and omits patterns not detected
in the period with a warning. All known pattern keys are still withheld from
weekly sampling, including missing or disabled ones, to avoid replaying a
scheduled payment as discretionary spending. Automatic refits use the last
78 calendar weeks. Loan terms and investment assumptions remain account
settings. Short periods can miss infrequent schedules and require review.

Backtests apply rolling selections relative to their historical refit date,
and detect patterns only from transactions available then. A fixed start date
later than that refit date leaves no training data, so the backtest is
unavailable rather than silently sampling older weeks.

The behavioural bootstrap carries **parameter uncertainty** as well as sampling
noise: resampling weeks independently makes week-to-week variation average out
like √t, which implicitly claims the historical mean weekly net is known
exactly. It is estimated from a finite sample, so each path draws its own drift
offset with standard error s/√n — a term that grows linearly in t and dominates
at long horizons. This was not a theoretical worry: before the correction, the
backtest found reality falling inside the nominal 80% band 1% of the time.

Weeks are normally drawn **uniformly**, because stationarity is the right
default. When the most recent third of the sample is statistically
distinguishable from the rest, they are drawn by **recency** instead — the same
3-month half-life the habits score uses — so someone who halved their spending
five months ago is projected as who they are now rather than as the average of
who they were and who they are. Before this, that person was forecast into a
deficit at 99% shortfall risk while the Insights page, reading the same ledger,
correctly reported a surplus.

The bar for "changed" is deliberately high: a large effect (Cohen's
$d \ge 0.8$) at $\alpha = 0.01$, with at least four months of sustained new
behaviour on each side of the split. Reweighting unconditionally is *not* a free
improvement — it shrinks the effective sample, lets a couple of unusual weeks
drag the whole projection, and measurably worsened backtest coverage on stable
data (61% → 10% inside the nominal 80% band). An earlier version also fired on a
real in-sample shift that turned out to be mean-reversion: a difference that is
genuine in sample need not persist, which is what the sustained-duration
requirement encodes. When recency weighting is in effect, the drift standard
error divides by Kish's effective sample size $1/\sum w_i^2$ rather than the raw
week count, because fewer weeks are genuinely informing the mean and dividing by
$n$ would claim a precision the sample no longer has.

**Uncertainty and goals.** Percentile bands (P10/P25/P50/P75/P90) are
computed pointwise across paths at each date — they are marginal
quantiles, not simultaneous confidence bands for whole trajectories. Cash and
net worth milestone arrival estimates report the deterministic crossing date plus an
optimistic–conservative range taken from the first crossings of the P90
and P10 curves.

**The verdict under the forecast chart.** One sentence, and it is deliberately
not the shortfall probability. `P(cash < 0 at some point)` cannot distinguish
being $5 short on a Thursday from sliding into insolvency: it answers 100% to
both, which is how a warning stops being read. `forecast.assess_shortfall`
measures depth (against a materiality threshold of roughly three days of the
household's own essential spending), duration, and recovery separately, and
picks one of six levels — `ok`, `thin`, `brief`, `crunch`, `watch`, `high`. Only
the last means *running out of money*, and it requires the dips to be both deep
and non-self-correcting. `crunch` is a genuine squeeze that genuinely clears: it
gets a date and an amount, because a fortnight of bounced payments matters even
when the year after it is excellent. The wording lives in the engine rather than
the page, because the wording *is* the finding.

**The goal ladder.** The Goals page puts loans and savings goals in one ordered
list and runs a debt snowball past the end of the debts. Required loan payments
flow regardless of position — they are already-committed spending, and a small
loan can retire before a large top-priority goal. Everything *spare* (income
minus spending, recency-weighted, so loan minimums are already netted out) is
split across the incomplete rungs by policy: `focus` gives it all to the top
rung, `weighted` splits by $1/\text{rank}$ normalised, `custom` uses the user's
own amounts as a ratio. Whenever a rung completes, what it was consuming
cascades to the next one — a goal's allocation, or for a loan its entire
minimum payment, which is what makes the snowball grow. Any unused part of a
final payment or contribution is redistributed within the same month. Active,
personal recurring payments linked to a loan supply its monthly payment; their
monthly equivalents are summed and replace the account's configured payment.
The account payment is the fallback when no such schedule exists. Missing or
interest-only payments are reported explicitly.

Savings goals record their own user-entered `saved_amount`, independent of
priority and total checking balances. These amounts earmark existing cash;
they do not create an asset or trigger transfers. The page warns when recorded
savings exceed current net cash. Users update actual progress themselves;
projected contributions do not silently become actual savings. Goal activity
records contributions, account assignments, purchases and releases. Purchase
activity reduces `saved_amount` and increases `spent_amount`; progress equals
their sum, capped at the target. Releases reduce only saved progress, allowing
reserves to be rebuilt. Existing saved totals remain unlocated opening savings.
Once activity exists, direct edits to the saved total or goal kind are refused.

Earmarks are conserved globally and by source account. New contributions cannot
exceed current reconstructed checking/savings balances, net of credit balances
and existing goal savings. Account-specific claims are also subtracted before
accepting a contribution or assignment. Purchases and releases cannot overdraw
the goal's selected funding bucket. This does not move money or create ledger
rows. Linked transactions must have the appropriate sign, account and date;
their cumulative allocation cannot exceed the original personal amount.
Transaction hashes retain allocation evidence after row deletion. Account
deletion makes its remaining earmarks unlocated. Undo restores the complete
activity and goal state together.

The monthly spending plan uses the same complete-month weighted evidence as
spending analytics. Recorded debt payments are carved out of the floored
essentials total. The detected debt line is the larger of this amount and
current regular loan payments, rather than their sum. Investment contributions
use Accounts settings; additional irregular reserves default to zero. Each
line can be overridden. Remaining monthly money is income minus spending,
reserves, investing and the goal/extra-debt allowance. Negative results remain
visible. The allowance shares `plan_surplus` with Goals; the detected spare
amount uses the saved plan when one exists. Saving a plan is one atomic,
undoable action. It does not change the forecast scenario or schedule bank
transfers. Historical purchases covered by a reserve may require a spending
override; Doug does not automatically remove those transactions from analytics.

Legacy cash goals draw from
one shared balance and are filled in priority order, so a lower goal shows
progress only once the ones above it are covered. The simulation is monthly and
deterministic: this is a schedule to act on, and the Monte Carlo fan on the
Forecast page already answers "what might happen".
Cash and net worth milestones measure household totals, including earmarked
money. They may overlap savings goals and are not additional pots of money.
Only cash and net worth milestones are evaluated as balance crossings in
Forecast. Earmarked purchase goals use the deterministic priority plan on Goals;
the forecast does not model its savings transfers or their eventual purchase.

The detected spare amount is `max(0, I − S)`, where income and personal
spending are averaged across up to 24 recorded completed calendar months,
with weights `0.5^(month age / 3)`. The current month and future transactions
are excluded. Recorded loan payments are already part of spending; the
calculation does not establish that every planned payment was imported.
The page shows the weighted income, spending and resulting surplus. Automatic
detection stays automatic after refreshes and imports. Only editing the amount
creates a manual override; Use detected clears that override even when the
spinner's value stays the same. Existing saved amounts are treated as manual
because older versions did not distinguish intentional overrides from values
that were accidentally saved during refresh.

**Reimbursable business activity.** Business (reimbursable) is a deliberate
per-transaction category for company expenses and separate repayments. Both
signs are excluded from personal income/spending analytics, habits, goals'
detected surplus, recurrence detection and forecast weekly samples. It is
not learned as a merchant rule: the same airline or hotel can serve both a
personal and a business trip.

A business reimbursement included in a paycheck is stored separately against
the original deposit's transaction ID. Personal analytics use the deposit
minus that portion, while bank history keeps its full amount. This adjustment
is idempotent, survives re-import and backups, and can be undone. Existing
recurring schedules affected by business corrections are refitted in memory
from personal transactions, preserving manual switches and loan links.

Actual account balances and reconstructed cash history include business
transactions. Exclusion from habits does not imply reimbursement has arrived.
An expected reimbursement with a known future date can be entered as a one-off
income event; the app does not invent an amount or payment date.

**Bills that moved on their own** (`alerts.py`). Each active recurring pattern's
per-occurrence amounts are rebuilt from the ledger — the stored item keeps only
the median, and a summary cannot be asked whether it moved — and the newest run
is compared against the preceding baseline. The gate is the baseline's own
**median absolute deviation**, not a fixed percentage: a change must exceed
3·MAD, a dollar, and $24 a year. That is the whole design. A constant
subscription has MAD = 0, so a two-dollar rise is unmissable; a power bill that
swings $60 either way has to move a very long way, which is what keeps August
out of the alert list. Trailing occurrences at the same new level are collapsed
into one finding, so a rise three months ago is reported once rather than three
times, and anything older than ~13 months is history rather than news.

**What-if events** (`events.py`). Each event stores a signed per-occurrence
amount, first date, repeat interval (Once, Daily, Weekly, Every 2 weeks,
Monthly, Every 3 months, Every 6 months, or Yearly), optional inclusive end date,
and an active flag. The shared schedule generator supplies dated impulses to
both the forecast and scheduled-flow/Calendar views. Fixed-day intervals are
anchored at the first date. Calendar intervals always add months to that
original anchor, clamping only that occurrence to the month's last day. A
February clamp therefore does not shift later purchases to an earlier day.
The first date is included, but the forecast applies only dates strictly after
its starting balance date and within the horizon. Each impulse affects every
cash path and net worth equally. These are additional planned flows, not learned
spending, and are not scaled by category-spending or income sliders. Both
comparison curves retain saved events. Current saved hypotheticals are excluded
from historical refits; explicitly supplied scenario events remain explicit
inputs. Existing databases migrate their events to Once. Editing updates the
same event ID and retains its active flag, with financial Undo/Redo support.

**Attribution for a squeeze** (`forecast.scheduled_flows` / `crunch_causes`).
The projection replays every recurring flow, loan payment, contribution and
what-if event occurrence by name to build its cash path, then discards the names. Keeping
them lets a dated warning answer *caused by what?*, and lets the answer be
acted on: moving a payment is stored as the pair of offsetting `OneOffEvent`s
it really is, which the engine already understands and the user can undo. The
look-back is capped at 45 days — beyond that a "cause" is not an event, it is a
pattern of living, and the tornado chart is the right instrument.

**A month that can leave** (`report.py`). One finished calendar month rendered
to a self-contained HTML page: styling inline, no scripts, no external
requests, light-themed because it is meant to be printed. Ctrl-P in any browser
makes a PDF. A rolling recency-weighted window is right for judging habits and
wrong for a document with a month's name at the top, so this one aggregates the
calendar month directly.

**Progress over time.** Every import records a *checkpoint*: the balance sheet,
the habits score with its components, and the forecast fan as it stood at that
moment. All three are unrecoverable after the fact — the score is
recency-weighted and so cannot be recomputed for a past date once newer months
exist, and a projection refitted later with the outcome already in the data is
not a prediction. Writing the claim down before the answer is known is the only
way the model can be caught being wrong.

**Reconstruct history…** builds those checkpoints retroactively for a ledger
that already has the data, so the page works on a first import rather than in
two months. Each reconstructed point is computed only from data that existed at
its own date — patterns re-detected as of then, the bootstrap truncated, cash
read from the reconstructed history — because a backfilled checkpoint is
indistinguishable from a real one once stored, and a projection built knowing
how it turned out would make this page report the model as far better than it
is.

The Progress page then draws an old fan against what actually happened, and
reports the share of days that landed inside the 80% and 50% bands. Those are
the numbers to read: a well-calibrated forecast should sit near 80% and 50%.
Much higher means it was too timid; much lower means it was overconfident. Cash
that ends outside the band entirely is flagged in either direction, because a
projection your behaviour broke is more informative than one it satisfied.

**Is the change real?** Weekly discretionary spending before a checkpoint is
compared with after by a **permutation test**: reshuffle which weeks belong to
which period 20,000 times and ask how often chance alone produces a gap this
large. Permutation rather than Welch's *t* because it assumes only
exchangeability — weekly spend is right-skewed with an occasional very large
week, which is exactly the shape that makes a *t*-test's p-value fiction at
$n = 8$. Recurring bills are excluded: a rent rise is not a change of habit, and
its fixed monthly timing would violate the independence the test needs.

Beside the p-value sits a bootstrap 95% interval on the dollar difference,
because that is the number that answers the question — a p-value says only
"distinguishable from noise", the interval says how much and how sure. Neither
is on the page: Forecast's Projection and Progress tabs and Dashboard's Spending
tab each carry a **How
this works** button (`widgets.InfoDialog`, built at click time from the results
currently on screen), and everything quantitative lives behind it. The page
states the finding in a sentence; the window states the working. This is not
about hiding the statistics — it is that a card headed *is this real or is it
noise* which then answers `p = 0.031` has replied in a unit the reader has no
intuition for, and the sentence that matters has to compete with it. A
non-significant result is **never** reported as "no change"; it reports instead
the smallest shift this many weeks could have resolved, so *"it may not feel
like I improved, but I did"* stays distinguishable from *"there isn't enough
data yet."* Per-category movement is shown but deliberately untested: twenty
categories at 5% yields a false positive every run, and printing that beside a
real finding would undermine the one defensible number on the page.

**Costing a deadline.** Naming a target month for a goal turns the question
into a budget calculation. Doug replays the full ladder and searches for the
smallest extra monthly spare amount that finishes the selected goal by that
month. This includes loan interest, existing payments, higher priorities,
custom splits and payment rollovers. The current month is included as an
installment. The standalone pace shown alongside is the remaining amount
divided by installments, or the amortizing payment for an interest-bearing loan;
it is not the extra household budget required by the ladder. Doug answers
with the smallest set of *named* discretionary items that covers it — the
frivolous recurring charges and category totals, largest first — because "find
another $196/month" is not an instruction anyone can act on and "the dining
budget and two subscriptions" is. Essentials and loan payments are excluded by
construction rather than ranked last: a plan that reaches a goal by cutting
rent is not a plan. So when the wants on the table do not add up, the honest
answer is that the date is unreachable, and that is what it says, with the size
of the shortfall. Uncategorised spending is offered but marked, since some of
it is certainly essential and the app does not know which.

**Reproducibility.** The simulator uses a fixed RNG seed, so scenario and
baseline runs share random draws (common random numbers). Differences you
see between a scenario and its baseline overlay are therefore due to the
scenario, not sampling noise.

**Spending insights.** Every category is sorted by a taxonomy (in
`insights.py`, kept as a plain editable mapping) into *essential*,
*discretionary*, or *incidental*; income and internal transfers are excluded
from spending. Monthly figures average the last three **complete** calendar
months (the current partial month is dropped). Let $I$ be average monthly
income, $S$ total spending, $D$ discretionary spending, and define the
savings rate $r=(I-S)/I$ and discretionary burden $b=D/I$.

The **habits score** separates what a person controls from what they don't,
and benchmarks both against published guidance rather than taste. Let $E$ be
average monthly essential spending, $F$ frivolous (wants) spending, $I$ income
and $S$ total spending. Define the frivolous-to-essential ratio and the savings
rate:

$$\rho = F/E, \qquad r = (I-S)/I.$$

Two sub-scores are read off piecewise-linear tables anchored on the 50/30/20
rule (50% needs, 30% wants, 20% savings — Warren & Tyagi, *All Your Worth*):
$H(\rho)$ with $\rho=0.60 \to 5.0$ (30 wants / 50 needs is exactly the
recommended ceiling), and $M(r)$ with $r=0.20 \to 8.5$ and $r=0$ (breaking
even) $\to 4.0$. They combine as

$$\text{score} = 0.6\,H + 0.4\,M - \lambda\Big(1-\tfrac{H}{10}\Big)\Big(1-\tfrac{M}{10}\Big),
\qquad \lambda = 2.5.$$

The penalty is a product, so it is negligible unless *both* are poor and
largest when both are worst. That matters because high discretionary spending
together with a deficit is not the average of two mediocre situations — it is
the one case where the shortfall is being caused by the spending. The four
cases land as:

| | low net income | high net income |
|---|---|---|
| **high $\rho$** | F — overspending | B− — comfortable but loose |
| **low $\rho$** | B — squeezed, not careless | A — disciplined and saving |

**Why $\rho$ compares wants to needs, and what that misses.** Measuring wants
against *income* would grade a low earner down for a circumstance rather than a
habit, which is the thing this score is trying not to do. But $\rho$ is
scale-free: both sides can inflate together and the ratio still reads as
disciplined. Someone spending 69% of income on essentials and 39% on wants has
$\rho = 0.57$ — inside the ceiling — while being 15% of income underwater.

The sub-scores are left alone, because the arithmetic is doing what it was
designed to do and the resulting grade is appropriately bad either way. What is
*not* left alone is the verdict. The bottom-left quadrant used to say flatly
"it isn't your spending habits causing it", and that claim is only true when
spending really is modest against income. It is now split three ways by the
shares of income the rule is actually written in:

- wants over 30% of income → *"your wants are modest next to your bills, but
  both are large against your income — the shortfall is partly the spending"*;
- wants inside 30% but essentials over 50% → *"squeezed by fixed costs"*, which
  does absolve spending, correctly;
- both inside guideline → not squeezed at all.

The detail line now quotes all three shares (needs / wants / saved against
50 / 30 / 20) before the ratio, so nobody can read "within the guideline" off a
ratio while being 8% of income in deficit.

A deepening deficit keeps lowering the score rather than plateauing. Two people
$600/mo underwater can land at F and B respectively, which is the entire point.

Uncategorized spending is **excluded** from both sides of $\rho$ rather than
assumed frivolous — some of it is genuinely essential, and punishing someone for
what the app failed to label is not a judgement about their habits. It is
reported separately, and above 25% of spending the grade is marked provisional.

All monthly figures are **recency-weighted**: every complete month contributes
with weight $0.5^{\,m/h}$ where $m$ is months ago and $h=3$. Six months ago
counts a quarter, two years ago about 1/256. This replaces a hard three-month
window, which discarded usable history and put a cliff at the boundary where a
large purchase silently stopped counting.

Both numbers matter and quoting either alone misleads: the lookback is 24
months, but the decay means **the last 3 months are about 50% of the score**,
the last 6 about 75%, and everything beyond a year about 6%. The Insights page
states this in a line generated from the constants, so the description cannot
drift from the arithmetic.

Charitable giving is its own bucket, neither essential nor frivolous. It counts
toward total spending — the money is gone either way — but is kept out of
$\rho$, because grading someone down for tithing or a monthly charity
commitment would be a value judgement rather than a statement about their
spending discipline.

The **composition donut** carves subscriptions and giving out of frivolous
spending. **Suggestions** cost every lever first and rank by real dollars
afterwards, so nothing is described by position unless it holds that position;
essentials surface only as non-directive notes (e.g. a housing-burden note).
Nothing is hardcoded as "bad"; the largest actual levers surface first and the
user decides.

**Categorisation that learns.** The default chain is exact saved corrections,
merchant similarity, keyword rules, and a sign-based fallback. No statistical
classifier is trained or downloaded. Corrections are stored locally in
`category_feedback` and keyed by normalized description and amount direction.
Purchases and refunds therefore have separate correction histories. The most
recent remembered exact correction takes precedence. New feedback records retain
the transaction ID, transaction date, account type, previous category, new
category, decision timestamp and remember scope. One-off reviews remain label
evidence but are excluded from both exact and similarity merchant rules.
`Store.review_transactions()` commits category changes, human review state and
feedback atomically. A failed feedback write rolls back the category change;
Undo restores the whole decision. Confirmations can retain the current category.
In Transactions, Mark reviewed always uses one-off scope. Category changes
initially apply only to the selected entries; More contains an explicit option
to remember subsequent changes for future imports. The default does not create
a merchant rule merely because the user checked a historical entry.
Conflicting remembered categories for one merchant/direction in a single batch
are rejected; users can confirm them with one-off scope instead.

Imported categories retain their actual source and explanation: keyword,
sign fallback, broad retailer, remembered correction, similarity or additional
model. Transfer pairing records its own evidence. These automatic assignments
are not human reviews. Migration preserves previous categories and merchant
feedback without fabricating review state or provenance. Confirmed transaction
categories are protected from automatic recategorization and transfer pairing,
including one-off decisions without a remembered merchant rule.

Keyword matching normalizes punctuation, spacing, and accents, then uses word
boundaries or explicitly listed abbreviation stems. This prevents `RENT` from
matching `PARENT`, for example. Specific descriptions are checked before broad
merchant rules: Costco gas is Fuel rather than Groceries, while Disney World is
Travel rather than Subscriptions. Negative interest is treated as a debt cost,
not investment income. These are defaults; the user can correct them.

Merchant similarity uses character 3-gram Jaccard overlap after stripping known
processor prefixes. The leading merchant token and amount direction must agree.
The best category must clear a 0.25 overlap floor and lead the next different
category by at least 0.10. These scores are heuristics, not probabilities.
Conflicting matches abstain and fall through to keyword rules. Transfer labels
are never propagated by similarity; explicit corrections remain available.
These checks improve specific regression cases but do not establish an accuracy
rate on unseen bank exports.

Transfer pairing requires equal opposite nonzero amounts in different accounts
within four days. For the full ledger, a transfer or card-payment description
must support the match, and neither leg can be a known purchase or earned income.
Explicit non-transfer corrections are excluded before pairing. A bare external
transfer description is not classified as an internal transfer by the keyword
fallback. Pairing remains a heuristic: equal amounts and related dates do not
prove that two rows represent the same payment. Review the resulting labels.

**Reviewing uncategorized entries.** The queue groups normalized merchants and
directions, then ranks them by total dollars plus twice recent dollars, with a
1.5 multiplier for recurring patterns. The default window matches the spending
score's lookback and can be changed. The GUI previews affected transactions and
defaults to the displayed period; older matching entries require an explicit
choice. Known and reviewed categories are unchanged. Suggestions start
unselected. A merchant batch is one atomic review operation; applying it does
not enable remembering unless the user explicitly checks that option. The
pending count includes the actual eligible rows and updates with period scope.
Applying a batch does
not silently propagate other categories. Imports use remembered corrections for future matching
entries. Recurring detection uses the latest known ledger category rather than
discarding it and guessing again from the description.

`category_review.reviewed_dataset()` exports current, explicitly human-reviewed
transaction labels, rather than treating automatic predictions as correct
examples. It retains transaction and review dates plus normalized merchant
groups (purchases and refunds share a group). Business entries, deposits with a
reimbursement split, unknown spending labels, nonfinite amounts, invalid dates
and future transactions are excluded. A past `as_of` cutoff excludes later
transactions and later reviews. This conservatively omits a transaction whose
latest label was reviewed later; it does not reconstruct previous labels or
past account metadata. The CSV export replaces its destination atomically and
refuses the open database or an alias of that database.

The older `learning.training_frame()` is a deduplicated merchant-feedback
summary, not the transaction-level truth used by this export. Its coverage
threshold is not a readiness or accuracy result. No statistical classifier or
new forecasting model is enabled. These dates and groups provide a basis for
later temporal and merchant-held-out evaluations against the existing category
chain. Merchant groups use the current normalization and still need inspection
for alternate names. Forecast evaluation also needs observed cash-flow outcomes
and a separate chronological benchmark; reviewed category labels alone do not
establish forecasting accuracy.

**Dedup, and the one case a row hash cannot catch.** Each row is hashed on
(account, date, description, amount, occurrence index), so legitimate same-day
duplicates survive while re-imports deduplicate — the occurrence index is what
keeps two identical charges on one day, and it is also why the hash cannot
collapse one transaction a bank exported *twice under different wording*. That
case (common around a core conversion, where an ACH row and a legacy row
describe one event) is handled separately and advisorily by **Accounts → Check
my balances → Find duplicate rows**: candidates are matched on amount, date and
merchant similarity, but the decision is driven by the reconciliation gap, since
only the balance can distinguish one payment posted twice from two identical
payments. Removals are recorded so a later re-import does not restore them.

Matching opposite-amount rows across your own accounts within four days are
paired and categorized as transfers so internal movements never count as income
or spending.

---

## Limitations

Credit-card interest is not modeled (balances are treated as static debt
unless imported activity says otherwise), and cash yield enters only
through detected interest deposits rather than an APY model. Recurring
amounts are medians with no inflation or contractual escalators. GBM
assumes independent lognormal monthly returns — no fat tails, volatility
clustering, or drawdown dynamics. The bootstrap assumes your spending
habits are stationary at the weekly scale, except for one step change it will
detect and adapt to — a gradual drift, or a second change on top of the first,
is still projected as an average. The habits score is a transparent heuristic,
not a validated financial index: it is only as good as the category
assignments, needs a few months of recognized income and some recognized
essential spending to compute, and ignores debt load, emergency-fund adequacy,
and one-time events. Its constants are anchored on a budgeting rule of thumb,
not on population data. Ledgerline produces statistical estimates from your own
history; it is a modeling tool, not financial advice.

A natural next step is read-only aggregator support (e.g. SimpleFIN or
Plaid) so imports refresh automatically; the ingestion pipeline was
structured so such a source can drop in beside the CSV path.


## CSV overlap and reconstructed account balances

Import matching first reserves exact stored hashes, then compares remaining
rows as a multiset of account, calendar date, signed amount rounded to cents,
and description. Only Unicode canonical composition, case, and whitespace are
normalized in descriptions; reference numbers and punctuation remain
significant. The file's row position is not an identity. Occurrence counts
preserve identical purchases within one export, and stored category corrections
and reimbursement markers remain attached to the existing transaction IDs.
Removed-row identities are also retained; older removals without those fields
continue to use their original hashes.

Without a bank transaction identifier, identical entries in separate partial
exports cannot always be distinguished. Overlap matching treats the counts as
observations of the same account/date/amount/description group. Meaningful
wording or date changes are advisory duplicate-review cases, rather than
permission to silently discard a financial record. Previously imported
possible duplicates are not automatically deleted.

The transaction balance view uses the original bank amounts, including transfers
and reimbursable business. If cumulative flow through a row is C and cumulative
flow through the balance anchor's calendar day is C0, the balance after that row
is B0 + C - C0. B0 is an end-of-day entered balance or newer cash snapshot.
Same-day rows use transaction ID order. Date, category, search, and pagination
filters are applied after calculating balances. Displayed row balances are
rounded to cents so floating-point residue cannot create a spurious negative
balance. These are reconstructions, not independently verified bank balances;
missing, duplicated, or incorrectly signed transactions and an incorrect anchor
can all affect them.

Separate Dashboard account lines reuse the cash and snapshot history routines.
Loans are negative amounts owed and credit cards keep their signed balance.
The chart's account selection changes neither household totals nor forecast
inputs. Investment and loan values between dated snapshots remain interpolated,
with the existing account prehistory preference applied before the first one.
