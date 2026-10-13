# Getting started with Financing with Doug

Doug uses account balances and bank exports to show your history and estimate
what comes next. Set up the accounts first, import the transactions, and check
the categories and recurring patterns before relying on the forecast.

This guide is available under **Help → Getting started**.

## Install and keep a backup

The Windows download is an installer named
`FinancingWithDoug-<version>-Setup.exe`. Run it and follow the prompts.
Windows may show a SmartScreen warning because the installer is unsigned.
Only continue if you trust the source of the download. A warning does not
establish whether a program is safe.

Financial data is stored in one local database. On Windows the default is
`C:\Users\<you>\.ledgerline\ledgerline.db`; on Linux and macOS it is
`~/.ledgerline/ledgerline.db`. The status bar shows the file currently open.
Doug does not connect to your bank. Its updater contacts GitHub to check and
download releases, without sending your financial data.

Use **File → Back up data…** before making major changes and keep regular
backups somewhere safe. This saves the database currently open, including
accounts, transactions, goals, checkpoints, and database settings. A backup
made while viewing a demo contains the demo. View preferences and the
in-memory undo history are not included.

To restore a backup, close Doug first. Keep a copy of the current database,
then replace it with your backup at the path shown in the status bar.
When running from source, you can open another database directly with
`python run.py --db "path/to/backup.db"`.

## Try a demo

**File → Load demo dataset** opens fictional data in a separate database.
The banner tells you when a demo is open. Your own database is not changed by
edits made in the demo. **File → Return to my own data** returns to it.

| Demo | What to look for |
|---|---|
| Balanced | A modest surplus, a loan, and savings goals |
| Struggling | Spending that exceeds income |
| Thriving | Higher savings and investment growth |
| Turnaround | A reduction in spending partway through the history |
| Spiralling | Several debts and a sustained shortfall |

Demos show how the controls work. They do not establish that the forecast
will predict your own finances accurately.

## Where to find things

There are six sections in the sidebar:

| Section | What it contains |
|---|---|
| Dashboard | Overview of balances and history; Spending analysis |
| Accounts | Account balances, dates, loan terms, and investment assumptions |
| Transactions | Imported transactions, search, and category corrections |
| Bills & payday | Calendar of scheduled items; Recurring patterns |
| Goals | Savings targets and their order |
| Forecast | Projection controls; Progress comparisons |

The tabs across the top keep related functions together.
Drag chart dividers to change how much space each panel gets.
**Simple view** hides advanced controls; it does not delete settings or data.

## 1. Add your accounts

Open **Accounts → Add account** for each account you want to track.

- Checking and savings: enter the balance and the date it was recorded.
- Credit cards: enter the amount owed as a positive number. A negative amount
  means the card is in credit.
- Loans: enter the amount owed, APR, regular payment, and payment day.
- Investments: enter the current value, expected annual return, volatility,
  and planned monthly contribution.

Use the balance date from the statement, not necessarily today's date.
Doug reconstructs balances from that dated amount and your transactions.
An incorrect balance or date shifts the reconstructed history.

Return and volatility are modeling assumptions. Change them if they do not
fit the investment. Future returns are not guaranteed.

### Enter amounts with a fixed decimal

Currency fields work like bank payment fields. Digits enter cents from the
right; you do not need to type the decimal point.

| Digits entered | Amount |
|---|---|
| 1 | $0.01 |
| 1234 | $12.34 |
| 123400 | $1,234.00 |

Clicking into a field selects the existing amount, so the next digit replaces
it. Backspace removes the last digit. Delete clears the amount. Use the minus
key for a negative amount where the field allows one.

Pasting `1234` enters $12.34. Pasting a formatted amount such as
`$1,234.56` keeps its dollar value. Check the displayed amount before saving.
Changes are committed when you press Enter or leave the field.

Percentages do not use this behavior. Enter `6.4875` for a 6.4875% APR.
Controls changed by the app still use dollars internally; this change affects
manual currency entry, not stored values or imported transactions.

## 2. Import bank history

Download a CSV export from your bank. Look for Download, Export, or
Transaction history on its website. A PDF statement is not a CSV.
Use as much history as you can; several complete months are more useful than
a few weeks for detecting patterns and estimating spending.

Choose **File → Import CSV…**, select the account, and choose the file.
For checking, savings, and credit cards, check the transaction preview before
importing:

- Dates should match the bank's export.
- Descriptions should identify the transactions.
- Expenses should be negative and income positive.
- Confirm the amount-sign setting, particularly for credit-card exports.

For loan and investment accounts, the same command opens the balance-history
importer rather than a transaction importer.

An import records a checkpoint for later comparison. Duplicate detection
reduces repeated entries, but you should still review the ledger after an
import. If you change the CSV mapping or amount signs, check the result rather
than assuming it was interpreted correctly.

## 3. Review transactions and patterns

In **Transactions**, search for entries and correct mistaken categories.
Use **Business (reimbursable)** for company travel and other expenses that
will be repaid. Apply the same category to a separate reimbursement deposit.
Both are excluded from the personal score, spending habits, detected spare
money, and repeated forecast samples. It is a per-transaction label; marking
one business flight does not make every future flight a business expense.

If reimbursement arrives inside your paycheck, keep the deposit categorized
as income. Right-click it and select **Business reimbursement…**, then enter
only the reimbursed portion. A $3,500 deposit containing $500 of reimbursement
counts as $3,000 of personal income. The ledger keeps the full $3,500 bank
deposit; the amount tooltip shows the separation. Enter zero to clear it.

Business expenses still affect actual cash balances until repayment arrives.
To model an expected repayment on a known future date, add a one-off income
event in Forecast. The business category does not create a repayment date.

Use **Review uncategorized…** to work through rows that need attention.
The review queue separates purchases from incoming payments. A selection updates
matching uncategorized entries, including older ones outside the displayed
window, without changing already categorized entries. Saved corrections take
precedence on future imports. Similarity suggestions require the same direction
and a clear lead over conflicting labels; their scores are not probabilities.
Check internal transfers: moving money between your own accounts is not
income or spending.
An external transfer is not necessarily an internal move. Equal amounts in two
accounts are not sufficient evidence by themselves. Review transfers and card
payments, especially when you have imported only one side of a payment.

Open **Bills & payday → Recurring patterns** to review detected bills and
paychecks. Confirm each amount, cadence, and date. Deactivate a pattern that
no longer applies. Use Ignore for repeated transactions that should not enter
the projection.

Link a loan-payment pattern to the correct loan. An unlinked loan pattern is
excluded to avoid counting the same payment twice; the loan's configured
payment is used when no active linked pattern covers it.

Patterns are estimates from past transactions. They may miss a bill, detect
a pattern that is not real, or continue a payment that has stopped.
Review them when your circumstances change.

## 4. Use the bills and payday calendar

**Bills & payday → Calendar** shows upcoming scheduled items through the next
year. The schedule begins tomorrow. Select a day to see its items, or press
**Show whole month** to return to the monthly list.

Green dates have income, red dates have payments, and blue dates have both.
The list includes recurring patterns, configured loan payments, investment
contributions, and saved one-off events.

Use the account selector to see items associated with a particular account.
Doug assigns an account only when matching imported transactions identify
one account. A merchant used on several accounts remains unassigned.
**Unassigned items** also includes commitments with no confirmed funding
account, such as a manually configured contribution.

For a selected account, the scheduled balance starts from its recorded or
reconstructed balance and applies assigned scheduled items through month end.
For later months, it includes earlier scheduled items as well.
Negative credit-card positions represent money owed.

This is not a safe-to-spend balance. Everyday spending, future transfers, and
unassigned items are excluded. Amounts and dates can change, and loan payments
are listed as recurring commitments rather than stopped by a payoff calculation.
Use Forecast for modeled spending and loan-payoff estimates.
A timing change in a forecast scenario does not change this baseline calendar
or move an actual bank payment.

## 5. Check spending and goals

**Dashboard → Spending** shows category totals and a spending score.
The score uses the 50/30/20 guideline as a benchmark, not a budget tailored
to you. It compares discretionary spending with needs and savings with income.
A high score does not guarantee that you have enough cash to pay a bill.
Use the explanations alongside the actual amounts.

Open **Goals → Add goal…** to enter a target.
**Add emergency fund…** creates a target based on essential spending.
Review the amount and priority order yourself. These targets are not separate
bank balances or automatic transfers.

**Spare /mo** is the detected monthly personal income minus spending, floored
at zero. It uses up to 24 recorded completed months, weighted toward recent
ones with a three-month half-life. The current month is left out. The
breakdown below the control shows the income, spending and calculation;
hover it for the weighting formula. Recorded loan payments are included in
spending. Verify that the ledger contains all your commitments before treating
this estimate as available money.

Editing Spare /mo creates a manual override. **Use detected** clears it and
keeps the estimate updated after future imports. Existing saved amounts may
be marked manual because older versions accidentally saved detected values
during refresh; use this button to restore automatic detection.

For loans and investments, use **Update from CSV…** on Accounts when you have
new balance history. Keep account terms current as well as transactions.

## 6. Read the forecast

**Forecast → Projection** shows cash and net worth over the selected horizon.
The center line is the median modeled outcome. Shaded bands show ranges
across simulated paths, not guarantees about future balances.

Check **Data quality** beside the chart before interpreting the projection.
**Review data** shows the recorded dates, gaps and amounts behind each flag,
with buttons to open Accounts, Transactions or the calendar. This stays visible
in Simple view. It checks for balances and transactions older than 30 days,
histories shorter than 90 days, months with no recorded activity, categories
to review, unassigned scheduled outflows and unusual transaction dates.

A quiet month is not necessarily missing history, and an old balance can still
be moved forward by newer transactions. These flags tell you what to verify
against your statements; they do not establish that a forecast is accurate.
The checks read your records without changing them or blocking a forecast.

**Watch explanation** opens the animated video in your computer's video
player. **How this works** gives the written explanation and assumptions.
If the video cannot open, check that the computer has a player for MP4 files.

Change income or category spending to test a scenario. Advanced controls
include extra debt payments, investing, and one-off expenses or income.
The chart updates after you finish an adjustment.
**Reset scenario** restores temporary controls; saved events remain in the
database. Manage goal targets on Goals, not in a second forecast editor.

In Everything view, expand **Forecast history** to choose how far back the
simulation samples spending weeks and irregular income. Presets cover three
months through two years; **Since a date…** is useful after moving or changing
jobs. **All available history** removes the usual 78-week sample cap. Automatic
keeps the existing behavior: up to 78 recorded weeks. Your choice is saved
with the current database and also applies to baseline comparisons, sensitivity
analysis and Progress forecasts. Reset scenario keeps this history setting.

Regular salary and rent normally come from your saved recurring patterns.
Check **Re-estimate paydays and bills too** to detect schedules and amounts
using only the selected period for the forecast. Disabled patterns and loan
links are respected, and saved records stay unchanged. Review the patterns
after a change: short windows can miss quarterly or annual bills, or a new
payday with too few repetitions. The panel shows the number and dates of
complete weeks actually available. Under four weeks, variable spending is
omitted and a warning asks you to extend the period or import recent data.

**What's causing this?** explains a modeled cash dip using nearby scheduled
items. Moving a date in that analysis changes the scenario only. Contact the
payee before changing any actual payment date.

Changing a forecast is not changing your finances. Use the results to compare
assumptions and plans. This app is not financial advice.

## 7. Compare forecasts with history

Open **Forecast → Progress** to compare checkpoints.
**Take a checkpoint now** saves the current balances, score, and forecast.
Imports also save checkpoints automatically.

**Reconstruct history…** creates retrospective comparisons using data available
at each selected past date. These are reconstructed tests, not predictions
that were actually saved then. They can help reveal model errors, but do not
prove that the next forecast will be accurate.

**Clear checkpoints…** deletes only the comparison snapshots. It leaves
accounts, transactions, and goals unchanged.
**What changed?** on Dashboard summarizes changes since the previous import.

## Keep the data current

Import new transactions, confirm balances and dates, and review changed bills
and income. Missing accounts or transactions can distort both history and the
forecast. Save a backup before a large correction.

**File → Save a month in review…** exports an HTML summary for a chosen month.
The exported report contains financial information; treat it as private.
Check the open-database path and demo banner before exporting or backing up.


### Inspecting monthly spending

On Dashboard → Spending, open **How this works**. The **Monthly breakdown** tab
lists Income, Necessities, Wants, Giving, and Unknown. Expand a category to see
the dates, descriptions, original ledger amounts, and weighted monthly
contributions of its transactions. For example, rent appears under Housing;
groceries, utilities, insurance, fuel, transport, debt payments, and health &
fitness also enter Necessities. Categorized refunds reduce their group.

The figures are recency-weighted averages of complete months, rather than a
single month's bills. **Months & exclusions** shows each month's share and
explains what is omitted. Hover a transaction for its account, exact contribution,
and any reimbursement removed from the personal amount. Transfers and
reimbursable business purchases are excluded. A marked reimbursement inside a
paycheck is also excluded from personal income. **How the score works** retains
the score calculation and assumptions.

Forecast → Progress also has **Watch explanation**, a silent animation of
checkpoints, recorded balances versus an earlier forecast, and weekly spending
comparisons. It uses fictional data calculated by the program.
