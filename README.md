# Financing with Doug

*Named after a machine-learning model that was, by every available metric,
bad at its job. Doug has since improved considerably.*

A local, private personal-finance instrument panel. Import your bank's CSV
exports, and Ledgerline reconstructs your net-worth history, detects your
recurring cash flows, and projects your finances forward with a Monte Carlo
model you can steer: spending scenarios, extra loan payments, investing
changes, and savings goals with arrival estimates. Everything runs on your
machine against a single SQLite file — no accounts, no cloud, no telemetry.

Built with PyQt6 and pyqtgraph. The signature view is the forecast fan:
muted history hands off at a dashed "today" line into percentile bands
(P10–P90 and P25–P75) around the median projection, with the deterministic
path threaded through in amber. History is sized as a fraction of the horizon
rather than a fixed year, so the projection is always the larger half of the
chart, and every timeline is bounded to its own data — you cannot pan or zoom
off into empty space and lose your way back.

The running version is shown in the app header and under **Help → About**; see
[`CHANGELOG.md`](CHANGELOG.md) for what changed between versions, or
[`docs/whats-new.md`](docs/whats-new.md) for the same thing in plain sentences.

## Install it

Grab `FinancingWithDoug-<version>-Setup.exe` from the
[latest release](https://github.com/harpe119/Financing-With-Doug/releases/latest)
and double-click it. Next, Next, Finish. It installs **per-user** into
`%LOCALAPPDATA%`, so Windows never asks for an administrator password, and it
offers a desktop shortcut. The first run of an unsigned installer shows
SmartScreen's *"Windows protected your PC"* — **More info → Run anyway**; that
happens once, and it is unavoidable without a code-signing certificate.

After that it keeps itself current: it asks GitHub once a day whether there is
anything newer, stays silent unless there is, and when there is it shows the
release notes and offers **Download and install** — it verifies the published
SHA-256 before running anything. **Help → Check for updates…** does the same on
demand and always answers, including "you're up to date". Your data is never
touched by an update or by an uninstall; it lives in `~/.ledgerline/` and the
uninstaller says so before it runs.

macOS and Linux have no installer — run from source (below), or build a bundle
with the spec described under *Building an executable*.

## Running from source

```bash
pip install -r requirements.txt
python run.py
```

Python ≥ 3.10. Either way, use **File → Load demo dataset** to explore
immediately. Five situations are offered:

| | grade | what it shows |
|---|---|---|
| **Balanced** | A− | The bundled 18-month ledger in two CSV dialects, a car loan, a brokerage and three goals — the only profile that also exercises the import mapper |
| **Struggling** | D+ | A deficit with wants over guideline: money is tight *and* the spending is part of why |
| **Thriving** | A+ | A 53% savings rate, investments compounding, debts nearly gone |
| **Turnaround** | B+ | Discretionary spending halves five months in — built to give the Progress page a real, statistically significant change to detect |
| **Spiralling** | F | Three high-interest debts, spending at 1.7× income, cash projected to −$47k within a year at 100% shortfall probability and a runway of zero months |

Each opens in its **own database file**, so a demo never touches your ledger and
**File → Return to my own data** puts you back where you were; a banner marks
demo data while you are in it. Your data lives at `~/.ledgerline/ledgerline.db`
by default; pass `--db path.db` to use another file.

New to it? **Help → Getting started** (or `F1`) opens a walkthrough that
covers importing a CSV, fixing categories, setting goals and reading the
forecast — the same file is at [`docs/getting-started.md`](docs/getting-started.md).

Run the test suite with `python tests/test_core.py`.

## Workflow

Start on the **Accounts** page: add your checking/savings/credit accounts
with their current balance and as-of date, loans with the amount owed, APR,
payment, and payment day, and investment accounts with an expected annual
return, volatility, and monthly contribution. Then **Import CSV** for each
cash account — the dialog sniffs the delimiter, guesses the column mapping
(single signed amount or debit/credit pair), and previews raw rows before
committing. Imports are idempotent: re-importing an overlapping export adds
only new rows.

The **Recurring** page shows every detected pattern with its cadence,
median amount, confidence, and next expected date. You can deactivate
items, reclassify them (income / bill / ignore), and link a detected
payment to a loan account (usually done automatically). The **Forecast**
page then projects forward: choose a horizon and path count, drag category
and income sliders, add extra loan payments or investing, and read goal
arrival estimates directly under each goal. The cash chart carries three
reference floors — $0 in hard red, the $1,000 starter buffer, and an emergency
fund sized from your own essential spending (months adjustable, default 3) —
and reports the probability that cash dips below each one *at any point* in the
horizon, not just where it ends up.

Under the chart, one sentence gives the **verdict**, and it is deliberately not
that probability. `P(cash < 0 at some point)` cannot distinguish being $5 short
on a Thursday from sliding into insolvency: it answers 100% to both, which is
how a warning stops being read. `forecast.assess_shortfall` measures depth
(against a materiality threshold of roughly three days of the household's own
essential spending), duration, and recovery separately, and picks one of six
levels — `ok`, `thin`, `brief`, `crunch`, `watch`, `high`. Only the last means
*running out of money*, and it requires the dips to be both deep and
non-self-correcting. `crunch` is a genuine squeeze that genuinely clears: it
gets a date and an amount, because a fortnight of bounced payments matters even
when the year after it is excellent. The wording lives in the engine rather
than the page, because the wording *is* the finding.

Beneath it, two analyses: a **tornado
chart** ranking every lever by how much a ±20% move changes the outcome, and a
**backtest** that refits the model at a past date using only data that existed
then and scores it against what really happened — the one check that says
whether the fan deserves to be believed. **What-if events** add dated one-off
shocks so the projection can answer "can I absorb this, and when is the worst
moment?". The ▶ **Explain me** button plays a short video on what the shaded
bands actually mean — it sits on the forecast page rather than under Help
because that is where the misreading happens. It ends on a card of text, so the
player holds that last frame on screen when the video finishes rather than
blanking, and **Pause** becomes **Replay**. If this build's multimedia backend
cannot decode the file at all, it is handed to the system's own video player
rather than failing silently.

Investment shocks are Student-t (ν=5) rescaled to unit variance rather than
Gaussian, because real returns have fatter tails than a normal admits. The
behavioural bootstrap carries **parameter uncertainty** as well as sampling
noise: resampling weeks independently makes week-to-week variation average out
like √t, which implicitly claims the historical mean weekly net is known
exactly. It is estimated from a finite sample, so each path draws its own drift
offset with standard error s/√n — a term that grows linearly in t and dominates
at long horizons. This was not a theoretical worry: before the correction, the
backtest found reality falling inside the nominal 80% band 1% of the time. Balance snapshots (Accounts → **Snapshots…**) can be
added, edited, deleted, or bulk-imported for *any* account: they draw the
history line for loans and investments, and act as a corrective balance anchor
for cash accounts. Before its first dated balance a loan or investment
reads **$0** — a loan taken out in 2025 was not a debt in 2023 — unless the
account is set to hold its first known balance flat instead, for one that was
transferred in already carrying a balance. An account with no snapshots at
all is held flat, so a manually entered loan never appears out of nowhere.

When an account's ledger doesn't reconcile against the balance you entered, the
dashboard says so — and a **Got it** button puts that warning away, because a
warning nobody can dismiss stops being read and takes the next one down with
it. The acknowledgement is recorded against the discrepancy as it currently
stands, not against the account, so importing another partial export moves the
figure and the warning comes back. **Accounts → Check my balances** lists every
account regardless — a page opened on purpose should not hide a finding — and
carries a **Show dashboard warning again** button.

Anything that writes to the database can be taken back. **Ctrl+Z** undoes the
last change and **Ctrl+Shift+Z** (or **Ctrl+Y**) redoes it; both sit on the
**Edit** menu, and an **Undo** button appears in the header whenever there is
something to undo. Each names the step it will reverse — *Undo Import CSV*,
*Undo Delete account* — and one press takes back one *action*, not one database
write, so undoing an import removes its transactions, patterns and checkpoint
together. The history is held in memory and bounded, so it covers the last
couple of dozen changes and does not survive closing the app.

## Importing real bank exports

Banks disagree about signs. Ledgerline supports four conventions:
a standard signed amount column (spending negative), a flipped column
(spending positive, common for credit cards), separate debit/credit
columns, and an unsigned amount column paired with a credit/debit
indicator column (values like `Credit`/`Debit` or `CR`/`DR`), which is
auto-detected by header name or by inspecting the column's values. Parenthesized negatives like `(45.10)` are handled, running
balance columns are ignored, and dates can be parsed day-first. Each row is
hashed on (account, date, description, amount, occurrence index), so
legitimate same-day duplicates survive while re-imports deduplicate — the
occurrence index is what keeps two identical charges on one day, and it is also
why the hash cannot collapse one transaction a bank exported *twice under
different wording*. That case (common around a core conversion, where an ACH row
and a legacy row describe one event) is handled separately and advisorily by
**Accounts → Check my balances → Find duplicate rows**: candidates are matched
on amount, date and merchant similarity, but the decision is driven by the
reconciliation gap, since only the balance can distinguish one payment posted
twice from two identical payments. Removals are recorded so a later re-import
does not restore them.
Matching opposite-amount rows across your own accounts within four days are
paired and categorized as transfers so internal movements never count as
income or spending. Categorization is keyword-based and correctable from the
Transactions page — per row, per selection, or in bulk — and every correction is
remembered and reapplied on later imports (see *Categorisation that learns*
below). The ledger loads 25 rows at a time with *Show more* / *Show all*, and a
**Show** filter for the last 30/90/365 days, all time, or since a date you
pick; every row carries a live category picker, so rendering the whole ledger
at once would mean building thousands of widgets for a list nobody can read.

**Balance history & updates.** Loan and investment accounts accept two shapes
of CSV, because lenders export both and you shouldn't have to know which you
were sent. A *balance* export lists a date and a balance per row. An *activity*
export — far more common — lists dated payments and charges; the balance
history is integrated out of those flows and pinned to the balance you entered
for the account, exactly as a cash ledger is anchored:

$$B(t) = B(t_0) - (F(t) - F(t_0)),$$

so the newest point is a figure you vouched for and older points are walked
back from it. This is principal-only — interest that never appears as a row in
the file is not invented, so balances far behind the anchor read slightly high
on an interest-bearing loan; it is the shape of the history, not a statement of
record. The importer guesses which shape it was handed, previews the
reconstruction before committing, and offers a direction toggle for exports
that sign amounts the other way round. Reach it from **File → Import CSV**
(pick any account, get the right importer), **File → Import balance history**,
**Update from CSV…**, or Snapshots… → Import CSV…. Rows upsert by date and
advance the current balance to the newest one. For transaction accounts the
column mapping is remembered on first import, so **Update from CSV…**
re-imports a newer export with no re-setup — dedup by row hash means only new
rows are added.

## Building a release

On Windows, one command does the lot:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

It runs the test suite first and refuses to build if anything fails — a broken
build that auto-installs itself onto five other machines is the one outcome
worth engineering against — then runs PyInstaller, wraps the result with Inno
Setup (`packaging/installer.iss`), and writes `FinancingWithDoug-<version>-Setup.exe`
plus a `SHA256SUMS.txt` into `dist/installer/`. Upload **both** to the GitHub
release: the checksum file is what the in-app updater checks the download
against, and the tag has to match `__version__` for existing installs to notice.
The version is read from `finfore/__init__.py`, so it is written in one place
and cannot drift from what the app reports. [`docs/releasing.md`](docs/releasing.md)
covers the whole loop.

**The bundle underneath.** `packaging/doug.spec` is usable on its own —
`pip install pyinstaller` then `pyinstaller packaging/doug.spec`, and the result
lands in `dist/`.
PyInstaller does not cross-compile, so build on the OS you are shipping to. The
spec defaults to a folder build rather than one-file because one-file unpacks
itself on every launch; flip `ONEFILE` if a single icon matters more. Bundled
data is resolved through `finfore.util.resource_path`, which follows
`sys._MEIPASS` inside a frozen build and the source tree otherwise.

**Custom icon.** Drop the icon file directly in `packaging/`, next to
`doug.spec`, named:
- `icon.ico` on Windows — must be a real multi-resolution `.ico`, not a
  renamed `.png`
- `icon.icns` on macOS — must be a real `.icns`

The spec finds it automatically by filename and OS; nothing else to edit. If
you only have a `.png`, convert it first (an online favicon/ico generator
works for Windows; `iconutil` or ImageMagick for macOS). Linux ignores this —
icons there come from a `.desktop` file, a separate step not covered by the
spec.

**What to send when there is no installer.** On Windows the answer is the
`Setup.exe` above. Everywhere else, `dist/Financing with Doug/` is the whole
program — the binary plus every library it needs — so zip that entire folder
and send the zip; it will not run if separated from the files beside it. Don't
send just the executable. On macOS, enabling the `BUNDLE` step in the spec
(commented out by default) gets you a proper `Financing with Doug.app`, which
is itself the double-clickable, Dock-draggable unit — zip and send that
instead of the raw `dist/` folder. First launch will need a right-click → Open
on macOS (Gatekeeper), since the build isn't code-signed — worth a heads-up so
it doesn't look like a virus warning.

## Methodology

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

**Variable spending.** Everything not matched to a pattern is treated as
behavior. Historical discretionary spend is binned into a complete-weeks ×
categories matrix; each simulated week resamples an entire historical row,
which preserves the correlation structure across categories (a heavy
travel week is also a heavy dining week), then scales by the scenario's
per-category multipliers before being spread onto days. Irregular income
is bootstrapped the same way under the income multiplier. At least four
complete weeks of history are required; otherwise variable spending is
zero and a warning is shown.

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
quantiles, not simultaneous confidence bands for whole trajectories. Goal
arrival estimates report the deterministic crossing date plus an
optimistic–conservative range taken from the first crossings of the P90
and P10 curves.

**The goal ladder.** The Goals page puts loans and savings goals in one ordered
list and runs a debt snowball past the end of the debts. Required loan payments
flow regardless of position — they are already-committed spending, and a small
loan can retire before a large top-priority goal. Everything *spare* (income
minus spending, recency-weighted, so loan minimums are already netted out) is
split across the incomplete rungs by policy: `focus` gives it all to the top
rung, `weighted` splits by $1/\text{rank}$ normalised, `custom` uses the user's
own amounts as a ratio. Whenever a rung completes, what it was consuming
cascades to the next one — a goal's allocation, or for a loan its entire
minimum payment, which is what makes the snowball grow. Cash goals draw from
one shared balance and are filled in priority order, so a lower goal shows
progress only once the ones above it are covered. The simulation is monthly and
deterministic: this is a schedule to act on, and the Monte Carlo fan on the
Forecast page already answers "what might happen".

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

**Attribution for a squeeze** (`forecast.scheduled_flows` / `crunch_causes`).
The projection replays every recurring flow, loan payment, contribution and
one-off event by name to build its cash path, then discards the names. Keeping
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
is on the page: the Forecast, Progress and Insights pages each carry a **for
nerds** button (`widgets.InfoDialog`, built at click time from the results
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
into an arithmetic one: the goal needs `remaining / months` per month, already
receives some of that, and the gap has to come from somewhere. Doug answers
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
spending.
**Suggestions** cost every lever first and rank by real dollars afterwards, so
nothing is described by position unless it holds that position; essentials
surface only as non-directive notes (e.g. a housing-burden note). Nothing is
hardcoded as "bad"; the largest actual levers surface first and the user
decides.

**Categorisation that learns.** Categories come from a chain of models, highest
precedence first: the user's own corrections, then (in future) a trained
classifier, then the keyword rules, then a sign-based default. Every manual
re-categorisation on the Transactions page is written to `category_feedback` as
a labelled example and applied immediately by `LearnedRules`, an exact-match
memory keyed on the normalised description and the sign of the amount — so
fixing a merchant once is enough, and the next import of that merchant keeps
your label. `learning.coverage()` reports when enough labels have accumulated
to be worth training a real model on (roughly 200 across 5+ categories);
`learning.training_frame()` returns the corpus in model-ready shape. The
`CategoryModel` protocol is the insertion point — a model abstains by returning
`None` and falls through to the layer below.

**Clearing the uncategorized pile.** A trained classifier is deliberately *not*
the first move: training needs labels, and the pile exists because there are
none. The queue is scoped by default to the window the habits score actually
reads — months are weighted `0.5^(age/3)`, so a merchant last seen three years
ago carries about 1/256 of last month's weight and labelling it changes nothing
you are looking at. The window is adjustable and reports what it hides; labels
apply to every matching row however old. Within that window, three cheaper
mechanisms come first, in descending order of value per click. (1) **Merchant clustering** — transactions collapse to far fewer
merchants than rows, so **Transactions → Review uncategorized** ranks unlabelled
merchants by how much labelling each is worth (total dollars, weighted toward
recent and recurring spend); one decision fixes every past and future charge
from that merchant. (2) **Recurrence** is folded into that ranking, since a
merchant already detected as recurring pays off every month. (3) **Merchant
similarity** — character 3-gram Jaccard with a leading-token gate and
payment-processor prefix stripping — generalises those labels to unseen variants
("SQ \*BLUE BOTTLE 0042" to "BLUE BOTTLE COFFEE") while keeping "BLUE BOTTLE"
apart from "BLUE CROSS". Jaccard specifically: measured against realistic pairs
it was the only one of Jaccard, overlap-coefficient and a token/char blend that
separated true variants from lookalikes. Only after those does a fitted model
earn its place, and by then it has a corpus. See `ml_notes` in `learning.py` for
the intended approach and the evaluation trap to avoid.

## Limitations

Credit-card interest is not modeled (balances are treated as static debt
unless imported activity says otherwise), and cash yield enters only
through detected interest deposits rather than an APY model. Recurring
amounts are medians with no inflation or contractual escalators. GBM
assumes independent lognormal monthly returns — no fat tails, volatility
clustering, or drawdown dynamics. The bootstrap assumes your spending
habits are stationary at the weekly scale, except for one step change it will
detect and adapt to — a gradual drift, or a second change on top of the first,
is still projected as an average. The habits score is a transparent heuristic, not a validated financial index: it is only as good as the category assignments, needs a few months of recognized income and some recognized essential spending to compute, and ignores debt load, emergency-fund adequacy, and one-time events. Its constants are anchored on a budgeting rule of thumb, not on population data. Ledgerline produces statistical estimates from your own history; it is a modeling tool, not financial advice.

A natural next step is read-only aggregator support (e.g. SimpleFIN or
Plaid) so imports refresh automatically; the ingestion pipeline was
structured so such a source can drop in beside the CSV path.

## Project layout

```
run.py                    entry point (QApplication + MainWindow)
finfore/
  models.py               dataclasses: accounts, recurring items, goals, scenario
  ingest.py               CSV sniffing, mapping, parsing, dedup hashing
  categorize.py           keyword rules + internal-transfer pairing
  insights.py             taxonomy, habits score, composition, suggestions
  goals.py                priority ladder over debts + goals, snowball cascade
  progress.py             checkpoints, path adherence, permutation test
  learning.py             correction capture, merchant clustering, similarity, ML seam
  recurrence.py           normalization, cadence classification, occurrences
  history.py              balance reconstruction and headline statistics
  forecast.py             deterministic + Monte Carlo engine, loans, goals
  pipeline.py             import + refresh-derived orchestration
  alerts.py               bills that moved, gated on each bill's own MAD
  dedup.py                one transaction a bank exported twice, found by balance
  report.py               one calendar month as a self-contained HTML page
  store.py                SQLite persistence
  undo.py                 snapshot undo/redo stack (Ctrl+Z), bounded in memory
  update.py               "is there something newer?" — stdlib only, no Qt
  demo.py                 one-click demo loader
  ui/                     PyQt6 theme, charts, dialogs, eight pages
  ui/video.py             bundled explainer videos, with a system-player fallback
  ui/guide.py             renders docs/getting-started.md in-app
docs/getting-started.md   the user-facing walkthrough (Help -> Getting started)
docs/releasing.md         bump, build, tag, publish — and what family sees
docs/whats-new.md         plain-English release notes (published with the release)
packaging/build.ps1       test → PyInstaller → Inno Setup → checksums
packaging/doug.spec       the PyInstaller bundle
packaging/installer.iss   the per-user Windows installer
assets/                   explainer videos (bundled into the build)
scripts/generate_sample_data.py   seeded synthetic bank exports
sample_data/              bundled demo CSVs + meta.json
tests/test_core.py        core-logic test suite
```
