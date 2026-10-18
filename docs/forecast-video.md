# The forecast explanation video

The silent video at [`assets/monte_carlo.mp4`](../assets/monte_carlo.mp4) explains
Doug's forecast through animation. It follows the original video's approach:
whole spending weeks move from history into a future, a balance curve grows,
and many futures become a shaded range. Text is limited to short titles, object
labels, amounts and chart legends, with one closing statement about replaying
your real history. There are no caption strips, subtitle tracks or narration.

The opening shows scheduled money moving the balance up or down. The week
animation then follows the exact historical-week draws used by two simulated
paths, including a week drawn twice. The chart extends from five weeks to a
year before additional futures appear. Fifty representative curves are drawn;
the forecast uses 1,000 paths.

To explain the bands visually, all 1,000 simulated balances are sorted at a
chosen date and displayed as dots beside the chart. Braces first split them
into halves at the median, then highlight the middle 80% and middle 50%. The
date marker moves and the balances are sorted again. This shows that the bands
summarize balances at each date, rather than identifying one group of futures
that stays inside them for the entire year.

A Dining control reduces the purple spending segment by 25%, and the engine's
changed forecast appears beside the baseline. A separate cash example shows
an expense, a dip below zero and recovery. The final view presents the cash
and net-worth fans in Doug's chart colors. Shading uses the same alpha values
as `finfore.ui.charts.fan_parts`.

The ending recalls that the cash band is built from recorded paychecks, bills
and spending weeks, replayed into a thousand possible futures in this example.
It asks what would happen if the good and hard weeks happened again. Sampling
reflects the frequency of the recorded weeks, with recency weighting when the
engine detects a sustained change; these are modeled probabilities, not known
probabilities of future events. Investment returns and uncertainty in average
weekly cash flow also contribute to the net-worth forecast.

All numbers are fictional. The renderer uses `finfore.forecast.run_forecast`
and an in-memory database; it does not open personal financial data or change
the application engine. The example contains $6,000 cash, $10,000 invested, an
$8,000 loan, scheduled income and bills, and a year of synthetic transactions.
It starts on January 1, 2026, runs for 365 days and uses 1,000 paths with seed 7.
The shortage example starts with $2,000 cash and includes a $3,400 repair.

Regular payments also appear in the fictional historical ledger. The engine
excludes known patterns from the variable-spending sample. Week colors identify
Groceries, Dining and Other; their segment lengths share a dollar scale. A week
identifier and total follow the week when it is copied. For the scenario, only
the Dining component shrinks. The displayed increase is the difference between
the two ending medians, not a guaranteed increase in real cash.

## Render or revise

Use a separate rendering environment so Manim is not an application dependency.
The tested environment uses Python 3.12, Manim Community 0.19.0 and the Cairo
backend. Linux requires Cairo and Pango development packages; see the
[Manim installation guide](https://docs.manim.community/en/stable/installation.html)
for platform setup. Labels use DejaVu Sans; no LaTeX is needed.

From the repository root:

```bash
python -m venv .venv-video
# Linux/macOS:
source .venv-video/bin/activate
# Windows PowerShell:
# .venv-video\Scripts\Activate.ps1
pip install -r scripts/video/requirements.txt
python scripts/video/example.py
python scripts/video/render.py --preview
python scripts/video/render.py
```

The preview is `build/video/monte_carlo_preview.mp4` at 960 × 540 and 15 fps.
The full render replaces `assets/monte_carlo.mp4` at 1920 × 1080 and 30 fps only
after rendering and packaging succeed. It retains chapter markers and removes
the old same-name SRT file, which video players might otherwise load
automatically. Temporary media and chapter timing stay under the ignored
`build/video/` directory. The existing app button and installer use this MP4.

Edit `scripts/video/monte_carlo.py` for the animation and short labels, or
`scripts/video/example.py` for the fictional input. Chapter positions and the
displayed week draws are recorded in `build/video/timeline.json`. After edits,
review the preview for motion, timing, clipping and transitions before rendering
the full video.

`example.py` checks that captured paths reproduce the engine's percentile
arrays. It also checks that the scheduled-money curve agrees with the
application's scheduled flows. Recording the random generator's returned week
indices leaves its draws unchanged. These checks establish consistency with the
implementation; they do not measure prediction accuracy on real financial data.


## Progress explainer

Forecast → Track record has its own **Explain → Watch the video** item. It opens the
bundled `assets/progress.mp4` in the system video player. The silent animation
uses the same palette and chapter markers as the Monte Carlo video. It is
written for a nontechnical adult: each scene introduces one question, shows an
example, and leaves time to read a plain-language explanation of the result.
Manim is needed only to render the asset.

The video stays under two minutes by focusing on the results. It introduces a
checkpoint as a saved record of balances, score and forecast. The example saves
one on April 6, 2026 before adding later transactions. That forecast remains
fixed while recorded cash extends to June 29. The video compares cash on the
same date and explains that a higher recorded balance can reveal an
underestimate in the earlier forecast.

The spending example compares wants outside regular bills and uncategorized
spending. It has 11 complete weeks before the checkpoint and 12 after, with
weekly averages of about $304 and $136. The drop is about $168 per week. An
estimated range of $107–$234 less per week shows uncertainty in the size of
that change. The video distinguishes insufficient history from an unclear
result and states the minimum of four complete weeks on each side.

The detailed statistical walkthrough and historical reconstruction are omitted
from the short video. The figures still come from the app's calculations. The
fixture checks the 20,000 permutation draws and 4,000 bootstrap estimates
against those results; the displayed range contains the middle 95% of the
bootstrap estimates. These comparisons describe recorded spending and do not
establish a cause or guarantee future savings.

The closing comparison briefly defines the habits score and net worth beside
the spending and cash results. The final steps are to import new transactions,
check categories, and compare with an earlier checkpoint in Track record.
Reading pauses remain at normal speed. The renderer rejects a Progress video
that reaches 120 seconds.

The fixture is in `scripts/video/progress_example.py`; the animation is in
`scripts/video/progress.py`. Both use a fictional in-memory database. From the
rendering environment, run:

```bash
python scripts/video/progress_example.py
python scripts/video/render.py --video progress --preview
python scripts/video/render.py --video progress
```

The preview is `build/video/progress_preview.mp4`; the full render replaces
`assets/progress.mp4` after successful rendering and packaging. Chapter times and
example results are recorded in `build/video/progress_timeline.json`. The file
also records `review_times` for the reading pauses, allowing frame checks after
rendering. Text outside the frame stops the render at those pauses. Both
bundled videos are included by `packaging/doug.spec`.
