# What's new

Plain-English notes about each version, for anyone using the program. (If you
want the engineering detail — why a number is calculated the way it is, what
was tested — that's in `CHANGELOG.md` instead.)

These notes are what appears in the window when the app tells you an update is
available, so it's worth keeping them readable.

---

## 1.22.0 — easier to find your way, and a light mode

- **Five places instead of seven.** The sidebar is now Home, Accounts, Bills &
  payday, Plan and Forecast. Related pages sit together as tabs: Transactions
  is under Accounts, and Goals and the monthly budget are both under Plan.
  Dashboard is now called Home.
- **Home answers first.** It opens on one sentence about where your cash is
  most likely headed, then the numbers behind it, then your net worth chart.
  Anything that needs a look is listed at the bottom, with a button that takes
  you straight to it.
- **A light mode.** Choose **View → Appearance → Light** if the dark screen is
  hard to read where you sit, or **Match my computer** to follow your computer's
  own setting. It changes at once and you stay exactly where you were.
- **A gentler first day.** A new copy of Doug walks you through three steps:
  add your accounts, import a statement, see where you're headed. Pages that
  have nothing yet say what will appear there and what they need. When you
  import, Doug first tells you in plain words what it read from your file (how
  many transactions, which dates, money in and out) so you can check it against
  your statement before anything is saved.
- **Honest when it doesn't know enough.** With only a few weeks of statements,
  Home and Forecast say it's too early to tell instead of guessing. If your
  newest statement is more than a month old, they say so and show the date
  they're going by.
- **A calmer Forecast.** In Simple view the Forecast page shows the answer and
  the chart. **Try a change** opens the few controls most people need, and the
  result updates by itself.
- **Smaller things.** Today is circled on the calendar. Dates read like
  "Oct 20, 2026". Spending is grouped as Needs, Wants and Giving everywhere. The
  Monthly cash flow chart no longer slides up and down when you drag it. The Tab
  key no longer gets stuck on a chart or a hidden tab bar.
- **A user manual.** **Help → User manual (PDF)** opens the complete manual,
  with worked examples, in your PDF reader. The Track record video is shorter
  and plainer.

Your financial records remain stored separately from the program. Forecasts are
estimates from your recorded history, not promises.

## 1.20.0 — clearer forecasts and spending you can trace

- **Two silent explanation videos.** The Forecast video shows how your recorded spending weeks build possible futures. Progress has a separate video showing how saved forecasts compare with later balances and how weekly spending changes are measured. Both use the program's calculations with fictional example data.
- **See what makes up your spending.** On Dashboard → Spending, open How this works to expand Income, Necessities, Wants, Giving, and Unknown into categories and individual transactions. Dates, refunds, month weights, and each transaction's contribution explain the monthly totals.
- **Choose the history that fits your life now.** Advanced Forecast controls let you select how far back the simulation samples spending weeks. After a new job or move, you can also re-estimate regular income and bills from that period.
- **Separate reimbursable business spending.** Business (reimbursable) purchases are excluded from personal spending, scores, and behavioral predictions. A reimbursement marked inside a paycheck is excluded from personal income.
- **Clearer everyday controls.** Transactions and Forecast have improved layouts, readable amounts, and controls that fit smaller windows. Forecast shows the cash verdict and the dollar effect of spending changes, with guidance for first-time setup and clearer recovery when a calculation fails.
- **More transparent goals and accounts.** Goals explains the calculation behind Spare /mo. Account views show balances, terms, and the dates behind them more clearly.

Financial records remain stored separately from the program. Forecasts are estimates from recorded history; a wider or narrower band does not establish prediction accuracy.

## 1.18.2 — easier to find things

- **"Check for updates" is now a button along the top of the window**, next to
  *File*, *Edit* and *Help*. It used to be tucked inside the Help menu, which is
  a silly place for the one button anyone actually gets told to go and press.
  One click, and it always tells you something — including "you're up to date".
- **The front page has been rewritten for people, not programmers.** If you were
  sent a link to download this, the page you landed on now explains what Doug
  actually does, how to install it, how to get your bank's file into it, and
  what each of the eight pages is for — in plain sentences, without the
  mathematics. (The mathematics is still there, just on its own page now, for
  anyone who wants it.)

Nothing about your accounts, transactions or goals changes in this update.

## 1.18.1 — a small one

- **The explainer video stops disappearing.** The short video behind ▶ **Explain
  me** on the Forecast page ends on a few lines of text, and the window used to
  go blank the instant it finished — right when there was something to read.
  It now stays on that last frame until you close it, and the Pause button
  becomes **Replay**.
- **Doug has learned some facts about orangutans.** Click the version number in
  the header. That's all that does.

## 1.16.0 — the first proper release

This is the first version you can just **install**. Download the setup file,
double-click it, click through, done. It doesn't ask for an administrator
password and it puts a shortcut on your desktop.

**It keeps itself up to date.** Doug checks once a day whether there's a newer
version and stays quiet unless there is. When there is, it shows you what
changed and offers to install it — you click one button and it handles the
rest. You can also check any time from the **Check for updates** button along
the top of the window.

Your information is never touched by an update. It's kept separately from the
program itself, so updating — or even uninstalling and reinstalling — picks up
exactly where you left off.

### Things that should make it easier to use

- **It starts simple.** The header has a **Simple view / Everything** switch.
  Simple hides the more technical panels and leaves the pages to what most
  people want. Nothing is lost — flip it any time, and it remembers.
- **A "For nerds" button** on the Forecast, Progress and Insights pages. The
  pages themselves now say things in plain sentences; if you want the
  statistics behind a number, they're one click away instead of in your face.
- **The dashboard tidies up.** The two lower charts fold away with the little
  arrow beside their titles, so the net worth history can have the whole page.

### Things Doug now notices

- **When a bill quietly goes up.** Nobody spots a subscription going from
  $15.49 to $19.49. Doug compares each recurring charge against its own past
  and tells you on the dashboard when one has moved — without pestering you
  about bills that naturally vary, like electricity in summer.
- **The difference between being short and being in trouble.** Dipping five
  dollars below zero on a Thursday used to get the same warning as genuinely
  running out of money. Now it says which one it is, and when it's a timing
  problem, **What's causing this?** shows the bills involved and offers to move
  one past payday.

### Something you can share

**File → Save a month in review** writes one finished month to a single page:
where you stand, what came in and went out, where it went, and the year ahead.
It's an ordinary web page, so it opens on any computer and Ctrl-P turns it into
a PDF you can send to someone.

---

*Everything stays on your own computer. Nothing is uploaded anywhere, and the
only time the program uses the internet at all is to ask whether a newer
version exists.*
