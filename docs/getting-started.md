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
The banner tells you when a demo is open, with a **Return to my own data** button
on it (also under **File**). Your own database is not changed by edits made in
the demo.

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

There are five sections in the sidebar:

| Section | What it contains |
|---|---|
| Home | An answer first (where your cash is heading); then your balances, your history, and anything that needs a look. Tabs: Overview, Spending |
| Accounts | Balances: account balances, dates, loan terms, and investment assumptions. Transactions: imported transactions, search, and category corrections |
| Bills & payday | Calendar of scheduled items; Detected bills & pay |
| Plan | Goals: your goals, their order, and your monthly allowance. Monthly budget: monthly spending amounts and actual goal funding |
| Forecast | Projection controls; Track record comparisons |

The tabs across the top keep related functions together.
Drag chart dividers to change how much space each panel gets.
**Import CSV…** in the header opens the same import as **File → Import CSV…**.
The **View** switch in the header chooses **Simple** or **Everything**. Simple
hides advanced controls and two tabs, Transactions and Track record; it does not
delete settings or data. A link that leads to one of them,
such as **Review** under **Needs a look** on Home, shows the tab until you switch
views. Everything shows every tab. The same choice is under **View** in the
menu bar.

**View → Appearance** chooses **Dark** (the default), **Light**, or **Match my
computer**, which follows your system's light or dark setting. The change applies
at once and keeps your place: the page, what you have typed, and how far you
have scrolled all stay. Doug remembers the choice.

**Help → User manual (PDF)** opens the complete manual, with worked examples and
the calculations, in your computer's PDF reader. **F1** opens this guide.

On a first run, **Bills & payday** and **Forecast** in the sidebar are dimmed and
in italics: they fill in from your statements, so there is little to see until
some are imported. Point at one to see why. They still open, and each page that
has nothing yet says what it will hold and offers the next step: add an account,
then import its statements. **Explore with a demo first** is on each of them.

## 1. Add your accounts

Choose **Add an account** on Home (or **Accounts → Add account**) for each account
you want to track. The dialog opens straight away.

- Checking and savings: enter the **balance at the end of day** (the posted
  balance your bank shows, without pending charges) and its date. Leave
  **Allow this account to go below $0** unchecked when the account cannot
  go below zero; negative reconstructed closes will then be flagged for review.
- Credit cards: enter the amount owed as a positive number. A negative amount
  means the card is in credit.
- Loans: enter the amount owed, APR, regular payment, and payment day.
- Investments: enter the current value, expected annual return, volatility,
  and planned monthly contribution.

Amounts fill in from the cents, so typing 3200 gives $32.00. Type 320000 for
$3,200.00, or paste $3,200.00; the dialog says so under the field.
Use the balance date from the statement, not necessarily today's date.
Doug reconstructs balances from that dated amount and your transactions.
An incorrect balance or date shifts the reconstructed history.
For cash and credit-card ledgers, use the end-of-day posted figure, excluding
pending charges and holds. **Available balance** may already include holds and
cannot safely anchor a posted ledger. If today's posting is unfinished, use
the most recent closed date and that day's closing balance.

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

Choose **File → Import CSV…** (or the **Import CSV…** button in the header),
select the account, and choose the file. With no account yet, the button opens the
add-account dialog first. After your first import Doug opens Home, which shows
where you stand. You can take an import back with Ctrl+Z or the Undo button.
Once a file is chosen, **What Doug read** says how many transactions it found,
the dates they cover and how many are money out and money in, and shows the
first few as they will be saved. If that matches your statement, press Import.
If not, open **Adjust how the file is read**: it holds the separator, date
order, amount signs and column choices, and opens by itself when the file could
not be read. A file whose rows are almost all money in gets a warning, because
some banks list purchases as positive numbers. When checking:

- **Posting / balance date** should use Posting Date when supplied. Weekend
  and holiday purchases may post later; Doug uses the supplied date without
  moving weekends to Monday. The separate Transaction Date is retained.
- Descriptions should identify the transactions.
- Expenses should be negative and income positive.
- Confirm the amount-sign setting, particularly for credit-card exports.

For loan and investment accounts, the same command opens the balance-history
importer rather than a transaction importer.

An import records a checkpoint for later comparison. Duplicate detection
reduces repeated entries, but you should still review the ledger after an
import. If you change the CSV mapping or amount signs, check the result rather
than assuming it was interpreted correctly.

Older imports may have used Transaction Date. On **Update from CSV…**, Doug
recognizes both columns and offers a table of exact matching entries whose
posting dates would change. Review the dates, then choose **Update dates and
import**. Existing categories, review history, IDs and goal links are kept.
Cancel leaves the import unchanged; Undo reverses an approved import.
Ambiguous matches and existing copies on both dates are blocked for review.
Use Accounts → Check my balances → Find duplicate rows and select the seven-day
comparison to inspect possible weekend/holiday copies; wider matches are never
preselected for removal.

Rows marked pending/unposted, or with a blank Posting Date and a valid
Transaction Date, are kept outside balances, spending estimates and forecasts.
**Accounts → Transactions → Tools → Latest CSV import…** lists those entries from each
account's latest imported export. It is an export snapshot, not live bank data.
A new export replaces that snapshot. Source Reference and Transaction Date are
available in transaction details. A malformed row or invalid financial
date/amount stops the import with an explanation instead of silently dropping money.

**Accounts → Check my balances** also compares recorded balances with the
ledger between observations. An unexplained difference is flagged without
creating an adjustment. Running transaction balances marked **≈** use unknown
same-day ordering. **Negative daily closes only** filters by the full day's
close; an intermediate negative row alone does not establish a bank overdraft.

## 3. Review transactions and patterns

In **Accounts → Transactions**, search for entries and correct mistaken categories.
Selecting an account on **Accounts → Balances** offers **View transactions** to open just that account's entries.
Use **Review → Needs attention** to find older categories without recorded
evidence, general retailers, uncertain merchant matches, and possible transfer
pairs. This filter identifies entries to check; it does not mean they are wrong.
**Not reviewed** also includes ordinary keyword rules and remembered-category
assignments that you have not checked individually.

Each description shows whether the category is reviewed, needs checking, or is
not yet reviewed. The attention button opens the full queue, clearing other
filters. Select a row and choose **More → Transaction details…** to see its source,
review history and calculated balance. **Tools → Category sources** adds sources
as a column. After checking an entry, press **Mark reviewed** to confirm its current
category, or choose another category to correct it. You can select several rows
and confirm or correct them together.

Category changes initially apply only to the selected entries. Enable
**More → Remember category changes for future imports** before changing a category
when the merchant should normally use it. Keep this off for a one-off purchase: for example, an
Amazon grocery order should not make all future Amazon purchases groceries.
One-off corrections still become reviewed examples, but do not change the
remembered merchant rule. **Mark reviewed** always confirms only the selected
entries, even when remembering category changes is enabled. Conflicting categories for the same merchant cannot
be remembered together in one batch. All category changes, review state and
saved preferences commit together and support Undo/Redo.

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

Use **Tools → Review uncategorized merchants…** to assign categories by merchant. The queue
separates purchases from incoming payments. Select a merchant to inspect the
transactions affected. By default, changes cover only its unknown entries in
the chosen period; check **Include older matching entries** to extend that
scope. Known or reviewed categories are preserved. Suggestions start unselected;
choose a category or explicitly accept suggestions before applying. Applying a
batch changes only the entries you chose. Check the pending merchant and
transaction counts before saving. Remembering categories for future imports is
off initially and can be enabled explicitly. Similarity suggestions require the
same direction and a clear lead over conflicting labels; their scores are not
probabilities.
Check internal transfers: moving money between your own accounts is not
income or spending.
An external transfer is not necessarily an internal move. Equal amounts in two
accounts are not sufficient evidence by themselves. Review transfers and card
payments, especially when you have imported only one side of a payment.

**Tools → Export reviewed data…** saves a local CSV for later analysis. It contains
current human-reviewed categories, transaction dates, amounts, descriptions,
account types, review timestamps and merchant groups. It excludes automatic
guesses, unverified older entries, uncategorized spending, future transactions,
and business or mixed-reimbursement entries. The export covers the whole ledger,
independently of display filters. It contains financial descriptions; choose
where you want to keep it. Exporting does not train a model or send data anywhere.

Open **Bills & payday → Detected bills & pay** to review detected bills and
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
A white ring marks today, whichever day you have selected.
The list includes recurring patterns, configured loan payments, investment
contributions, and saved what-if event occurrences.

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

**Home → Overview** opens with the verdict and your figures, then net worth history, then anything under **Needs a look** at the bottom. Use its account selector,
date ranges, and Layers menu to choose what to inspect; **Expand** gives the
chart the page. If a reconciliation notice appears, **Details** opens the full
explanation.

**Home → Spending** shows category totals and a spending score.
The score uses the 50/30/20 guideline as a benchmark, not a budget tailored
to you. It compares spending on wants with spending on needs, and savings with income.
A high score does not guarantee that you have enough cash to pay a bill.
Use the explanations alongside the actual amounts.
Monthly cash flow and spending by category sit folded at the bottom of the
page; open either one to see it. Clicking a category's bar opens that
category's transactions for the last 90 days.

Open **Plan → Goals → Add goal…** to enter a goal.
Choose **Savings** for a vacation, computer, emergency fund, or another purchase
or reserve. Enter the **Goal amount** and **Already saved**: money you have actually
set aside for that goal. Use **Goal activity…** to record later changes. Each
goal keeps its own progress when you change priorities; your checking balance
does not automatically fund every purchase. Creating or funding a goal in Doug
does not transfer money between accounts.

On **Plan**, review detected income and spending, then check **Override** for
amounts that have changed. Essentials and regular debt payments are separate:
recorded debt payments are removed from essentials before the debt line is
calculated. That line uses the higher of recorded payments or current loan
commitments. Investment contributions come from Accounts. The irregular-expense
reserve is additional money for costs not already covered in the spending lines.
Without complete history, explicitly override income and each spending line
through Uncategorized spending, including $0 amounts, before saving a manual plan.

Expand **Adjust individual spending categories** to change dining, subscriptions,
groceries or another category. These amounts update the essentials and wants
totals. A total override takes precedence; turn it off to edit that group's
individual categories. Category amounts are included in their totals once.

There is one monthly allowance for goals and extra debt. You type it on the
**Goals** tab, and it saves as you type. **Monthly budget** shows it, and shows what
the budget leaves available; if they differ, **Use $X as my allowance** adopts the
budget's figure. Press **Save spending plan** to save the budget rows. A chosen
allowance is kept; with none chosen, saving makes it follow the budget. Goals uses
this allowance and its existing priority order. The answer at the top shows what
remains unassigned or how much the plan exceeds income. Unsaved spending-line
edits are kept when you leave the tab; **Reload saved plan** discards them.
**Inspect income & spending transactions…** traces detected amounts back to
categories and ledger entries. Overrides are planning assumptions; they do not
change imported transactions or Forecast's income and spending controls.
Forecast's **Goals → Use Goals funding plan** applies the saved Plan's income,
spending and investment contributions, together with the shared extra-money
allowance, priority order and allocation policy for loan repayments. Existing
bill dates and spending variation are scaled to the planned monthly amounts.
Amounts without usable history are spread evenly across days. Forecast's
scenario controls apply additional adjustments on top of the saved budget;
they do not edit the plan. **Compare unchanged controls** shows a second curve
without the saved budget or Goals funding, so you can assess their effect.
It starts enabled when goals or a saved funding allowance exist. Home's
outlook, reports and new Track record checkpoints use the same saved choice.
Turn it off to project existing habits using Forecast's separate extra-loan
and rollover controls. Unsaved Plan edits never enter the forecast.
Irregular-expense reserves stay in cash. Goal earmarks keep their underlying
cash or investment holdings; they are not automatically spent when a goal
becomes fully funded. Record or schedule the purchase to model that outflow.

At the top of **Plan → Goals**, **Where your goal savings live** connects each goal
to its accounts. Account bars share a dollar scale; selecting a goal or an
account segment highlights that goal's locations. Each goal's own bar shows
progress toward its goal amount, alongside exact amounts held and still needed.
Recorded purchases count toward progress but are no longer money held.

Checking, savings and investment accounts appear separately. A CD entered as
an Investment can hold a goal earmark at its tracked value. This does not sell
the CD or make it spendable cash; maturity and withdrawal restrictions are not
modeled. An earmark is a record of purpose, not a bank transfer.

Saved money without a known source is shown as **Account not assigned**, with
a patterned part of the goal bar. Select the goal and choose **Locate savings…**
to assign all or part to an account. Doug does not guess the account or add
more progress. Until it is located, an account's unassigned portion may contain
that money. Negative balances and earmarks exceeding a balance remain visible.

Balance milestones appear separately because they do not reserve an account.
For a baseline you want to protect, use **Savings for a purchase or reserve**,
record the amount actually set aside, then locate it in the appropriate account.

Select a savings goal and open **Record / view goal activity…** on Plan, or
**Goal activity…** on the Goals tab:

- **Set aside money** earmarks existing holdings for the selected goal. Choose
  the checking, savings or investment account holding them, or leave their
  account unassigned.
- **Locate existing savings** assigns money already included in **Still saved**
  to an account. This adds no new progress.
- **Spend toward goal** reduces savings held and records a purchase. Progress
  includes both remaining savings and purchases, so buying the computer does
  not make its goal appear unfunded again.
- **Release savings** reduces progress. Use this when drawing from an emergency
  reserve you intend to rebuild, or redirecting money to another purpose.

You can link a contribution to an imported deposit or a purchase to an expense
in that account. Linked activity uses the transaction date. A transaction can
be split between goals only within its available amount; business reimbursements
are excluded. Removing an imported row retains the activity and its transaction
reference. Goal activity and saved plans support Undo/Redo.

The line under **Activity** says what the chosen activity does. To remove an
entry recorded by mistake, select it in the list and choose **Delete
activity…**. Its effect on the goal is undone as if it had never been recorded.
Doug refuses when later activity depends on it, such as a purchase made from the
money a contribution set aside; delete the later entry first. Releasing savings
is different: it records that money left the goal, and keeps the history.

New contributions must fit both unassigned funding balances after credit-card
balances and the selected account's unassigned balance. Investment values are
included in this funding check; that total is not a measure of spendable cash.
These checks depend on current balances and complete imports.
Previously entered savings are preserved without an inferred account. After the first
activity, use the activity controls rather than editing the saved total directly.
Plan's monthly actual column shows recorded contributions minus releases;
locating savings and purchases do not count as new contributions. If a purchase
is already covered by a goal reserve, adjust its spending allowance to avoid
budgeting it twice.

**Add emergency fund…** creates a goal based on your spending on needs.
Review the amount and priority order yourself. These goals are not separate
bank balances or automatic transfers.

Existing **Cash balance milestone** and **Net worth milestone** goals retain
their balance-based progress. Cash milestones share net cash by priority and
include money set aside for savings goals. Net worth milestones also include
investments. These household milestones can overlap your purchase savings;
they do not represent additional money set aside. Savings goals show their
contribution schedule on Goals; Forecast does not assume that this schedule is
executed and directs you back to Goals for those dates.

**Monthly allowance** is the detected monthly personal income minus spending, floored
at zero. It uses up to 24 recorded completed months, weighted toward recent
ones with a three-month half-life. The current month is left out. The
breakdown below the control shows the income, spending and calculation;
hover it for the weighting formula. Recorded loan payments are included in
spending. Verify that the ledger contains all your commitments before treating
this estimate as available money.

Loans appear automatically from Accounts. Their existing monthly payment comes
from active linked **loan payment** patterns on **Bills & payday**, falling back
to the monthly payment entered on Accounts. Multiple linked payments are added
at their monthly equivalent. **Total /mo** includes that existing payment plus
the extra money allocated from spare income. Select a row to see the breakdown.
With **Custom**, **Share → Extra /mo** pairs a relative share with the resulting
extra dollars for the first plan month. Shares are proportions, not dollar
commitments: with a $350 allowance, shares of 100 and 100 allocate $175 each.
Shares of 1 and 1 give the same result. All-zero shares fund the first unfinished
goal. The selected row still shows existing payments plus extra funding.
Lower-priority loans keep receiving existing payments.
When a loan is paid off, its payment becomes available for the remaining goals.
If no payment is configured or linked, the plan warns you instead of assuming one.

Editing Monthly allowance creates a manual override. **Use Plan / history** clears it and
keeps the estimate updated after future imports. Once you save a monthly Plan,
the detected amount instead comes from that plan's income minus spending,
reserves and investment contributions. Existing saved amounts may
be marked manual because older versions accidentally saved detected values
during refresh; use this button to restore automatic detection.

For loans and investments, use **Update from CSV…** on Accounts when you have
new balance history. Keep account terms current as well as transactions.

## 6. Read the forecast

**Forecast → Projection** shows cash and net worth over the selected horizon.
The center line is the median modeled outcome. Shaded bands show ranges
across simulated paths, not guarantees about future balances.

Check the **Data quality** line above the chart before interpreting the
projection. **Review data** shows the recorded dates, gaps and amounts behind each flag,
with buttons to open Accounts, Transactions or the calendar. This stays visible
in Simple view. It checks for balances and transactions older than 30 days,
histories shorter than 90 days, months with no recorded activity, categories
to review, unassigned scheduled outflows and unusual transaction dates.

A quiet month is not necessarily missing history, and an old balance can still
be moved forward by newer transactions. These flags tell you what to verify
against your statements; they do not establish that a forecast is accurate.
The checks read your records without changing them or blocking a forecast.

**Explain → Watch the video** opens the animated video in your computer's
video player. **Explain → How this works** gives the written explanation and
assumptions. If the video cannot open, check that the computer has a player for
MP4 files.

Change income or category spending to test a scenario. In Simple view the
Forecast page opens on the answer and the chart, with one **Try a change**
button; it opens the controls (length, cash buffer, income and the three biggest
spending categories, with What-if events and Goals folded below), and **Hide
changes** closes them. Everything view keeps the controls beside the chart, with
the advanced ones too: extra debt payments, investing, and planned expenses or
income. The chart updates after you finish an adjustment, so there is no refresh
button in Simple view. The $1,000 starter buffer line is drawn in Everything view
only; Simple view keeps the $0 line and your cash buffer.
**Reset scenario** restores temporary controls; saved events remain in the
database. Manage goals on Goals, not in a second forecast editor.

Expand **What-if events** to add a planned purchase. Enter its name, the amount
**per purchase** (negative for a cost), and a date. **Repeat** offers Once,
Daily, Weekly, Every 2 weeks, Monthly, Every 3 months, Every 6 months, or Yearly.
Repeating events also offer an optional **End date**, inclusive. For example,
enter `−$50` and Weekly to model an additional $50 purchase every week.
Calendar repeats stay anchored to the original day; January 31 becomes the
last day of February, then March 31. An event repeats only within its schedule
and the selected forecast length.

Select **Edit** beside a saved event to change its name, amount, date, repeat
interval, or end date. **Save changes** updates that event; **Cancel edit**
leaves it as it was. Editing preserves its checked/unchecked state. A repeating
event may retain an earlier start date when edited; only future occurrences
affect the forecast, since today's balance is already the starting point.

These events add to your existing projected spending and also appear in the
Calendar and shortfall explanation. They do not create transactions or detected
bills, and they do not alter historical spending or Goals' detected spare cash.
Both comparison curves include saved events; use the event checkbox to compare
with and without the purchase. Remove the what-if if the same purchase later
becomes part of your imported history, to avoid adding it twice.

In Everything view, expand **Forecast history** to choose how far back the
simulation samples spending weeks and irregular income. Presets cover three
months through two years; **Since a date…** is useful after moving or changing
jobs. **All available history** removes the usual 78-week sample cap. Automatic
keeps the existing behavior: up to 78 recorded weeks. Your choice is saved
with the current database and also applies to baseline comparisons, sensitivity
analysis and Track record forecasts. Reset scenario keeps this history setting.

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

Open **Forecast → Track record** to compare checkpoints.
**Take a checkpoint now** saves the current balances, score, and forecast.
Imports also save checkpoints automatically.

**Reconstruct history…** creates retrospective comparisons using data available
at each selected past date. These are reconstructed tests, not predictions
that were actually saved then. They can help reveal model errors, but do not
prove that the next forecast will be accurate.

**Clear checkpoints…** deletes only the comparison snapshots. It leaves
accounts, transactions, and goals unchanged.
**What changed?** on Home summarizes changes since the previous import.

## Keep the data current

Import new transactions, confirm balances and dates, and review changed bills
and income. Missing accounts or transactions can distort both history and the
forecast. Save a backup before a large correction.

**File → Save a month in review…** exports an HTML summary for a chosen month.
The exported report contains financial information; treat it as private.
Check the open-database path and demo banner before exporting or backing up.


### Inspecting monthly spending

On Home → Spending, open **How this works**. The **Monthly breakdown** tab
lists Income, Needs, Wants, Giving, and Uncategorized. Expand a category to see
the dates, descriptions, original ledger amounts, and weighted monthly
contributions of its transactions. For example, rent appears under Housing;
groceries, utilities, insurance, fuel, transport, debt payments, and health &
fitness also enter Needs. Categorized refunds reduce their group.

The figures are recency-weighted averages of complete months, rather than a
single month's bills. **Months & exclusions** shows each month's share and
explains what is omitted. Hover a transaction for its account, exact contribution,
and any reimbursement removed from the personal amount. Transfers and
reimbursable business purchases are excluded. A marked reimbursement inside a
paycheck is also excluded from personal income. **How the score works** retains
the score calculation and assumptions.

Forecast → Track record also has **Explain → Watch the video**, a silent animation of
checkpoints, recorded balances versus an earlier forecast, and weekly spending
comparisons. It uses fictional data calculated by the program.


### Updating an account and tracing its balance

**Accounts → Update from CSV…** reuses that account's saved column mapping.
The newer export may overlap transactions already imported. Doug checks the
stored hashes and the transaction contents, including occurrence counts, so
older hashes or changes in capitalization and whitespace do not add another
copy. Repeated identical purchases within an export remain separate entries.
Importing transactions keeps the entered balance and its date as the anchor;
use **Update balance…** when you have a newer balance to enter.

Accounts, Home totals, Goals balance milestones and the Forecast starting
balances use the balance calculated through today from those dated observations
and later transactions. Investment and loan snapshots also carry through to
their forecast starting values. If you correct a balance on a date that already
has a snapshot, both records use the corrected amount. Historical Track record
checkpoints remain records of what was saved on their original dates.

On **Transactions**, select an account and enable **Tools → Running account balances**.
The **Account balance** column gives its calculated balance after each entry.
Amounts include cents in this view. Hover the balance, or open
**More → Transaction details…**, to see the amount before and after the transaction
and the dated balance used to reconstruct it. Extra columns scroll horizontally
on narrow windows so merchant descriptions keep a readable width.
**Negative balances only** and **Oldest first** help locate a drop below zero.
Search, category, period, and page limits do not change the calculation: it
always uses the full bank ledger, including transfers and business expenses.
Rows on the same day follow import order, which may differ from bank posting
order. A negative credit-card balance represents money owed.

**Tools → Review duplicates…** opens possible duplicate postings for the selected
account, or all accounts if no filter is selected. Review the entries before
confirming removal. Undo restores removed entries; remembered removals stop
them returning in later imports. Meaningful changes in a bank's descriptions
or posting dates still need review.

### Showing selected accounts on Home

Above the history plot, choose **Chart view → Separate accounts**, then use
the accounts menu to select the balances you want to compare. For example,
select checking and your 401(k) to draw one line for each. **Combined totals**
shows the selected accounts together. The selection is remembered for each
database and applies only to that chart; the overview totals and other panels
still include all accounts.

Checking, savings, and credit-card lines use the transaction ledger and a dated
balance. Investment and loan lines use dated balance snapshots. Money owed is
drawn below zero. Hover the plot to read the selected accounts' exact balances.
