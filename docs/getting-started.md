# Getting started with Financing with Doug

Welcome. This walks you through going from a fresh install to a working picture
of your money, in about twenty minutes of setup and five minutes a month after
that.

You can reopen this any time from **Help → Getting started**.

---

## Before anything else: your data stays here

Everything lives in a single file on this computer — by default at
`C:\Users\<you>\.ledgerline\ledgerline.db` on Windows, or
`~/.ledgerline/ledgerline.db` on Mac.

There is no account to create, no cloud, no sync, and nothing is ever uploaded.
The program does not connect to the internet at all. If you delete that file,
the data is gone; if you copy it to another computer, it goes with you.

That also means **nobody can recover it for you**. If the numbers start to
matter to you, copy that file somewhere safe now and then.

---

## "Windows protected your PC" / "unidentified developer"

The first time you run it, Windows may show a blue **SmartScreen** box, or a Mac
may refuse to open it. This is not a virus warning. It appears for any program
that has not been code-signed, which costs money per year and is not worth it
for something handed between family.

- **Windows** — click *More info*, then *Run anyway*.
- **Mac** — right-click the app, choose *Open*, then confirm.

You only have to do this once.

Also: keep the whole folder together. The `.exe` will not run on its own — it
needs the files sitting beside it. To get a desktop icon, right-click the
`.exe` → **Send to → Desktop (create shortcut)**. Move the *shortcut* wherever
you like; leave the folder alone.

---

## Try it without your own data first

**File → Load demo dataset** opens one of five fictional households. Nothing
you do in a demo touches your own data — each one lives in its own separate
file, an amber banner across the top reminds you where you are, and
**File → Return to my own data** puts you straight back.

| Demo | What it shows |
|---|---|
| **Balanced** | An ordinary situation — a modest surplus, a car loan part-paid |
| **Struggling** | Spending past the edge, with the card carrying the difference |
| **Thriving** | A big savings rate and investments compounding |
| **Turnaround** | Someone who cut their spending hard five months ago |
| **Spiralling** | The worst case: three debts, an F grade, cash running out |

This is the fastest way to see what every page is *for* before your own numbers
are in. **Spiralling** and **Thriving** are worth a look side by side.

---

## The eight pages, in one line each

The list down the left side is the whole program:

| Page | What it answers |
|---|---|
| **Dashboard** | Where do I stand right now? Net worth, cash, debt, and how it got there |
| **Accounts** | What do I own and owe? Set up and edit everything here |
| **Transactions** | The ledger itself — search it, and correct any category |
| **Recurring** | What repeats every month, and how sure is it? |
| **Insights** | Where does the money go, and is that sensible? |
| **Goals** | What am I saving for, and in what order? |
| **Forecast** | Where is this heading, and how confident should I be? |
| **Progress** | Was the old forecast right, and am I actually improving? |

**Dashboard** is the home screen. It has no settings — it just shows the
headline numbers and three charts: your net worth over time, income against
spending each month, and where the last 90 days went. Every panel divider on
this and other pages can be **dragged** to give one chart more room.

---

## Step 1 — Add your accounts

Go to **Accounts** and click **Add account** for each one you want tracked.

**Type amounts the way you would say them.** `1234.56` is one thousand two
hundred and thirty-four dollars and fifty-six cents; `1234` is one thousand two
hundred and thirty-four dollars. Dollar signs and commas pasted from a statement
are ignored, so `$1,234.56` works too. Clicking into a field selects what is
already there, so typing straight away replaces it rather than adding to it.

What to enter for each type:

- **Checking / Savings** — the balance right now, and today's date.
- **Credit card** — the amount you owe, as a **positive** number. (A negative
  figure means the card is in credit — you are owed money.)
- **Loan** — the amount owed as a **positive** number too, plus the APR, the
  monthly payment, and which day of the month it comes out. APR takes four
  decimal places, so `6.4875%` off your statement goes in exactly as written.
- **Investment** — the current value, an expected annual return (7% is a common
  long-run assumption for a broad stock fund), a volatility (15% is typical),
  and anything you add each month.

Do not agonise over the investment numbers. They are assumptions, not facts, and
you can change them later and re-run.

> **The "as of" date matters.** The program rebuilds your history by working
> backwards from the balance you enter on the date you say it was true. If you
> type last month's balance with today's date, everything before it shifts.

---

## Step 2 — Get a CSV from your bank

Every bank calls this something slightly different. Log in and look for:

> *Download* · *Export* · *Download transactions* · *Statements & activity*

Choose **CSV** (sometimes called "spreadsheet" or "Excel" format — CSV is the
one you want, not PDF). Ask for as long a period as it will give you. **Twelve
to eighteen months is ideal**; anything under three months is too short for the
program to find your recurring bills or say much about your habits.

Save it somewhere you can find it again. The Downloads folder is fine.

---

## Step 3 — Import it

**File → Import CSV…**, pick the account, and choose the file.

The import screen tries to work the file out on its own and shows you the raw
rows underneath so you can check. It handles the four conventions banks
actually use — a single signed amount column, a flipped one (common on credit
cards), separate debit and credit columns, or an unsigned amount with a
`Credit`/`Debit` indicator column.

**What to check before pressing Import:**

- Does the **Date** column point at the real date?
- Does the **Description** column show merchant names?
- Do **spending rows show as negative** in the preview? If they are the wrong
  way round, change **Amounts** to the flipped option.
- If dates are day-first (`31/12/2025`), tick **Day-first dates**.

Then press **Import**. Nothing is destroyed if you get it wrong — importing the
same file twice adds nothing, because every row is fingerprinted. If the mapping
was wrong, fix it and import again.

### Loans and investments take a CSV too

Those accounts accept either shape of export:

- a **balance** file — a date and a balance on each row; or
- an **activity** file — dated payments and charges, which is what most lenders
  actually send.

Pick the account in **File → Import CSV…** and you get the right importer
automatically. For an activity file the program works the balance history out of
the payments and pins it to the balance you entered. It shows you a preview
first — if the line comes out upside down, untick *"Payments in this file reduce
the balance"*.

### Later on: updating

Next month, download a fresh export and use **Accounts → Update from CSV…**.
It remembers how the file was laid out, so there is no setup the second time,
and only genuinely new rows are added.

---

## Step 4 — Fix the categories

Categories drive the score, the charts and the forecast, so this is the step
that pays for itself.

Go to **Transactions → Review uncategorized…**. This lists **merchants**, not
transactions, biggest first — labelling "SAFEWAY" once fixes every Safeway
charge you have ever made and every one you make in future. Twenty minutes here
does more than an hour of editing rows one at a time.

By default it only shows merchants from the period that actually affects your
score. The score looks back 24 months but weights recent months far more
heavily — **the last 3 months are about half of it**, the last 6 about three
quarters, and anything over a year about 6%. So labelling a merchant you last
saw in 2022 is real work that changes almost nothing. If the list is longer
than one screen, the header says so.

Two things worth knowing:

- **It learns.** Every correction is remembered, so next month's import already
  knows that merchant.
- **It generalises.** Label `BLUE BOTTLE COFFEE` and it will also catch
  `SQ *BLUE BOTTLE 0042` — while keeping `BLUE CROSS` well clear.

You can also change any single category directly on the **Transactions** page,
or right-click a selection.

---

## Step 5 — Check the recurring items

The **Recurring** page shows every repeating payment it found — rent, salary,
subscriptions, loan payments — with how often it happens and how confident it is.

Have a quick look and:

- Untick **On** for anything that has stopped, or that still happens but should
  not count towards the forecast (a cancelled subscription, a one-off that
  looked regular).
- **Right-click** a row to say what it really is — income, a bill, not real at
  all, or a payment on one of your loans. Linking a loan payment matters:
  otherwise it gets counted twice, and there is a warning when one is unlinked.
- **Delete** only removes patterns that no longer occur at all. If matching
  transactions are still in your ledger, re-detecting simply finds it again.

---

## Step 6 — Set your goals

Go to **Goals**. This page is one **priority ladder** covering your debts *and*
your savings goals together, because they compete for the same spare money.

1. **Add emergency fund…** sizes one from your own essential spending. Most
   guidance puts this first, so it goes to the top.
2. **Add goal…** for anything else — a car, a holiday, a deposit.
3. Use **▲ Higher** / **▼ Lower** to put them in the order you actually care
   about.

Then choose how spare money is split:

- **Focus** — everything spare goes at the top target. Clears the ladder
  fastest.
- **Weighted** — split by priority, so lower goals still visibly move. Slower,
  but a goal that has not moved in two years is a goal people give up on.
- **Custom** — you type the amounts.

**The snowball is the point.** When a loan is paid off, its whole monthly
payment rolls into whatever is next — the panel dates each hand-off, like
*"Mar 2028 — Car loan paid off → $412/mo rolls into Emergency fund."* That is
how the last debt becomes the first savings goal instead of quietly disappearing
into everyday spending.

**"Getting there sooner"** answers a different question: pick a date you want
something by, and it tells you what you would have to give up to hit it — by
name, in dollars. If the wants on the table do not add up, it says the date is
out of reach rather than suggesting you cut into rent.

---

## Step 7 — Read the forecast

The **Forecast** page projects forward. It runs hundreds of simulated futures
rather than drawing one line, because one line would be a prediction and this
is not one.

The shading is the honest part:

- the **dark band** is the middle 50% of outcomes;
- the **light band** is the middle 80%;
- the **solid line** is the median — half of futures land above it, half below.

**The band is the answer, not the line.** Press **▶ Explain me** for a short
video on it.

Three reference lines sit on the cash chart: **$0** in red, the **$1,000 starter
buffer**, and an **emergency fund** sized from your own essential spending.

Under the chart, one sentence says whether the money actually runs out. It is
not a probability, because a probability cannot tell the difference between
being $5 short on a Thursday and sliding quietly into insolvency — and *"how
often does the line touch zero"* answers **100%** to both. What it weighs
instead is how far below zero you go, for how long, and whether it comes back:

| It says | It means |
|---|---|
| **Cash holds up** | Nothing goes under. |
| **Cash gets thin** | Never negative, but the buffer keeps being eaten. |
| **A short dip, not a shortfall** | It crosses zero by trivial amounts and recovers. Move a payment. |
| **A tight patch, then it clears** | A real squeeze — deep enough to bounce a payment — on a date, followed by recovery. |
| **Worth watching** | Some futures go genuinely short, most come back. |
| **High risk of running out of cash** | Deep *and* not self-correcting. This is the one that means what it says. |

The exact percentages behind all of that are under **🤓 For nerds**.

When the dip is a bounded one, a **What's causing this?** button appears beside
it. That lists what is actually scheduled to leave in the run-up — rent, a car
payment, a subscription — says when the next money lands, and offers to move one
of them past that date. Moving a payment does not make it go away; it is the
right fix when two things land in the wrong order, and the wrong one when there
simply is not enough coming in.

Then have a play. Drag the category sliders, add extra loan payments, add a
**what-if event** for a dated one-off like a new car or a bonus, and watch the
fan move.

Two analyses sit underneath:

- **Sensitivity** ranks which lever actually moves your outcome. It is often not
  the one that feels biggest day to day.
- **Backtest** refits the model at a past date using *only* what was known then
  and scores it against what really happened. This is the one that tells you
  whether to believe the fan at all. Coverage near 80% means well calibrated.

---

## Step 8 — The habits score

The **Insights** page gives a 0–10 score and a letter grade, benchmarked against
the **50/30/20 rule** — 50% of income on needs, 30% on wants, 20% saved.

It scores two things separately, on purpose:

- **Habits** — how you spend, measured as wants against needs. This is the part
  you control.
- **Means** — whether you are living within your income.

Which is why a low earner spending carefully still grades well, while the same
shortfall caused by discretionary spending does not. The page tells you which
case you are in, in plain words.

Anything the program could not categorise is **left out** of the ratio rather
than assumed frivolous — punishing you for what it failed to label would be
unfair. If a lot is uncategorised the grade is marked *provisional*, which is a
hint to go back to Step 4.

---

## Step 9 — Come back next month

This is where it gets useful, and it only takes a few minutes.

1. Download a fresh CSV from your bank.
2. **Accounts → Update from CSV…** (no setup needed the second time).
3. Update any balances that have drifted.

Each import quietly records a **checkpoint** — your score, your balances, and
the forecast being made that day. The **Progress** page then compares them:

- **Did the old forecast hold?** It draws what was predicted against what
  actually happened. Cash outside the band entirely is flagged either way,
  because a projection your behaviour broke is more interesting than one it
  matched.
- **Did anything actually improve?** It tests whether your spending really
  changed or whether it is just noise, and reports how sure it is. A
  non-significant result is never reported as "no change" — it tells you how big
  a shift this much data could have detected at all.

That second one is there for a specific feeling: *"it does not feel like I am
getting anywhere."* Sometimes the numbers disagree with the feeling, and it is
worth knowing which is right.

Checkpoints are taken automatically, so there is normally nothing to do. If you
want to mark a particular moment — just before a big change, say — there is
**Take a checkpoint now** on the Progress page.

> **Already have a year of history?** **Progress → Reconstruct history…** builds
> the missing checkpoints from data you already have, so the page works today
> instead of in six months. Each reconstructed one is worked out using *only*
> the data that existed on its own date, never with hindsight, which is what
> keeps the page honest about how accurate Doug has actually been.

Nothing on the Progress page touches your money. Taking a checkpoint,
reconstructing them, or clearing every last one never alters an account, a
transaction, a goal or an imported row — the **What is a checkpoint?** button
in the toolbar says so at more length.

### Keeping it up to date

**Check for updates** — its own button along the top of the window, beside
*File*, *Edit* and *Help* — asks whether a newer version has been released,
shows what changed, and, if you want it, downloads and installs it for you.
The app also checks quietly in the background once a day and stays silent
unless there is something new.

Updating never touches your accounts, transactions, goals or settings. They are
stored separately from the program itself, so an update (or even uninstalling
and reinstalling) picks up exactly where you left off.

### If you get something wrong: Ctrl+Z

Every change can be taken back. Press **Ctrl+Z** — or click the **Undo** button
that appears at the top of the window the moment there is something to undo —
and the last thing you did is reversed. **Ctrl+Shift+Z** (or **Ctrl+Y**) puts it
back again, and both live on the **Edit** menu.

It tells you what it is about to undo, so you never have to guess: the button
reads *Undo import CSV*, or *Undo delete account*, or *Undo reset all data*. And
one press takes back one *thing you did*, not one internal step — undoing an
import removes the transactions, the recurring patterns it found and the
checkpoint it recorded, all together.

Two things worth knowing. It remembers roughly the last couple of dozen changes,
not everything since the beginning of time. And the history is kept in memory
while the program is open, so **closing the app clears it** — if you have just
deleted something you want back, undo it before you quit.

### "This history doesn't add up"

If an account's imported rows and the balance you entered cannot both be true,
the dashboard says so rather than drawing a confident wrong line. Usually the
export is missing withdrawals, doesn't reach back far enough, or contains the
same transaction twice.

Once you have read it, press **Got it** and it goes away. It comes back if the
figures change — importing another partial export, for instance — because
dismissing it says "I know about this discrepancy", not "stop checking this
account". **Accounts → Check my balances** always shows every account whether
or not you dismissed the banner, and has a **Show dashboard warning again**
button if you want the reminder back.

### Simple view

The header carries a **Simple view / Everything** switch, and it starts on
Simple. That puts away the machinery — Monte Carlo path counts, the sensitivity
and backtest panels, the checkpoint table, the Clear button — and leaves the
pages to what most people actually want. It hides no findings: the charts, the
verdicts and every **For nerds** panel stay exactly where they are. Click it
once for everything, and it is remembered.

### Save a month in review

**File → Save a month in review…** writes one finished month to a single
printable page: where you stand, what came in and went out, where it went, your
grade in plain words, anything that changed, and the year ahead. It is a plain
web page with nothing attached — it opens in any browser, offline, and
**Ctrl-P** turns it into a PDF you can send to someone who does not have this
program.

### When a bill goes up on its own

Nobody notices a subscription creeping from $15.49 to $19.49. Doug does: each
recurring charge is compared against **its own** history, and a blue strip
appears on the Dashboard when one moves, with **What changed?** for the detail.

The trick is not spotting a change, it is not spotting hundreds. A bill that
varies by nature — electricity in August, a phone bill with roaming on it — is
different every month, so a charge has to move by more than three times its own
usual swing before it is mentioned at all. A subscription that has been the same
eleven months running has no usual swing, so a two-dollar rise is enormous and
gets flagged; the power bill mostly stays quiet. Price *cuts* and pay rises are
reported the same way, as good news.

Switching a pattern off on the **Recurring** page stops it being checked.

### For nerds

The Progress, Forecast and Insights pages each carry a **🤓 For nerds** button.
The page itself sticks to plain sentences — *yes, you really are spending
less* — and the button opens the working behind it: p-values, confidence
intervals, how the permutation test is built, how well-calibrated the forecast
bands have been, and how the habits grade is put together. Nothing is hidden;
it is just not in the way.

---

## A five-minute monthly routine

1. Download the CSV → **Update from CSV…**
2. **Review uncategorized…** — clear the top few merchants
3. Glance at **Progress** — did anything move?
4. Glance at **Goals** — are the dates still what you expected?

That is it.

---

## Things that trip people up

**"My net worth looks wrong."** Check the *as-of* dates on the Accounts page.
History is rebuilt backwards from the balance you entered, so a stale balance
tilts everything before it. **Accounts → Snapshots…** lets you record a balance
on a specific date to correct drift.

**"My spending looks too high."** Money moved between your own accounts is
supposed to be ignored, and it is auto-detected when both sides are imported.
Import *all* your cash accounts, not just one, and re-check anything labelled
`Transfer`.

**"It says I have no recurring items."** It needs at least three occurrences to
call something a pattern. Import more history.

**"The forecast looks nothing like my situation."** Look at the **Backtest**
first. If coverage is far off 80%, the model is not describing you well — most
often because a large chunk of spending is still uncategorised, or the ledger is
too short.

**"I changed my spending months ago but the forecast has not noticed."** It
will, once the change has held for about four months against a similar stretch
of before. It deliberately waits: a quiet couple of months is not a new habit,
and reacting to every dip would make the projection jumpy and useless.

---

## What this is not

Doug produces statistical estimates from your own transaction history. It is a
modelling tool, not financial advice, and it is not a substitute for a financial
adviser on anything that matters — tax, retirement, insurance, or debt you
cannot service.

The forecast assumes the future roughly resembles the past. It does not know
about your job, your health, or next year's rent rise. It cannot model credit
card interest, and it treats a recurring bill as fixed forever with no inflation.
The habits score is a transparent rule of thumb anchored on published budgeting
guidance, not a validated financial index.

Everything it does is written down in **Help → Methodology**, and at more length
in `docs/methodology.md` in the program folder. Nothing here is a black box — if
a number looks wrong, it can be traced.

---

*Doug would like you to know that Doug is still not a financial advisor.*
