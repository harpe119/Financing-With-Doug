---
target: forecast
total_score: 23
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 2
target_identity: "file:/home/cnharper/projects/fwd/Financing-With-Doug/finfore/ui/forecast_page.py"
target_fingerprint: "sha256:b9b9dbf3ea2304a8134d974d95835f81b6e2414e92a04c39852f454eb1e2b5c5"
target_path: /home/cnharper/projects/fwd/Financing-With-Doug/finfore/ui/forecast_page.py
timestamp: 2026-10-12T18-52-37Z
slug: finfore-ui-forecast-page-py
---
Method: dual-agent (A: /root/forecast_design_review · B: /root/forecast_evidence_review)

Forecast has a strong visual identity and useful financial interactions, but its main answer is too easy to miss. The cash verdict is below the initial viewport, and opening What-if events clips the Add button and percentage values. Preserve the current design and improve the order, fit, and explanation of the existing information.

## Design specificity

The cash-first view, recorded-history handoff, emergency buffer, named goals, category controls, and payment-cause explanation make this a distinct personal-finance workspace. The restrained palette and native controls suit the task. A replacement visual style would not resolve the observed problems.

## Design health

Scores describe usability, not the correctness or predictive accuracy of the financial model.

| Heuristic | Score /4 | Main observation |
|---|---:|---|
| System status | 2 | The financial verdict is below the initial viewport. |
| Real-world language | 2 | Percentages need dollar meaning; goals and safety floors need distinct identities. |
| Control and freedom | 3 | Reset, Undo, event switches, layers, and splitters provide useful control. |
| Consistency | 3 | Coherent native controls; automatic updates coexist with a manual Run button. |
| Error prevention | 2 | Invalid event submissions remain available and silently do nothing. |
| Recognition over recall | 2 | Scenario comparison and goal ranges require interpretation. |
| Efficiency | 3 | Simple and advanced views support different levels of detail. |
| Minimalist design | 2 | The chart dominates while the answer and next action sit below it. |
| Error recovery | 1 | Invalid Add offers no explanation; setup guidance is below an empty plot. |
| Help | 3 | Video, methodology, data review, and cause explanations are useful. |
| **Total** | **23/40** | **Acceptable; focused improvements needed.** |

## What works

- Simple view hides simulation machinery while retaining the cash chart and everyday scenario controls.
- Recorded history joins the forecast at a visible today boundary; the uncertainty bands have explanatory legend labels.
- The cash-crunch explanation distinguishes a timing problem from sustained overspending. Backtesting also explicitly warns when the band performed poorly over the selected historical window.

## Priority issues

1. **[P1] The answer appears below the chart.** Both reviews confirmed that the initial 1280 × 900 view hides the cash verdict. At 980 × 620, the chart's lower half, legend, verdict, and summary require scrolling. A stressed user sees the falling graph before finding the warning or What's causing this? action. Move a concise verdict and relevant next action above the plot, retain nearby data-quality context, and size the chart to the available space. Keep numerical detail expandable. Sources: finfore/ui/forecast_page.py:532, :610, :1003. Suggested command: `$impeccable layout forecast`.

2. **[P1] What-if events breaks the control column.** Opening the card expands the content to 409 px inside a 346 px viewport with horizontal scrolling disabled. The name field shrinks to 39 px; only 2 px of the Add button remain visible. Slider percentages are pushed outside the viewport too. This occurs at both tested window sizes. Use labelled, wrapping or stacked event fields and keep Add fully visible. Disable invalid submission or explain the missing name/nonzero amount inline; the current invalid Add silently returns. Sources: finfore/ui/forecast_page.py:425, :462, :839. Suggested commands: `$impeccable adapt forecast`, then `$impeccable harden forecast`.

3. **[P2] Comparison lines and targets need clearer identities.** Increasing Income adds a white baseline line in Simple view, but its identity is hidden in Layers. The balanced demo also shows a $25,000 goal called Emergency fund alongside a calculated three-month emergency floor near $11,000. They are different targets, not duplicate data, but their labels make that distinction weak. Label the existing plan and changed scenario directly, identify goal versus safety floor, and show a compact numerical comparison. Sources: finfore/ui/forecast_page.py:523, :1211, :1240. Suggested command: `$impeccable clarify forecast`.

4. **[P2] Scenario controls need understandable consequences and semantic names.** Groceries 80% requires users to calculate what that change means for their budget. Show the estimated monthly amount or reduction beside the percentage. All six inspected sliders have empty accessible names and descriptions, and their labels have no buddies; the event row also lacks explicit field names. Add category/purpose names, associated labels, and value descriptions. This is confirmed source/runtime evidence, not a demonstrated screen-reader failure. Sources: finfore/ui/forecast_page.py:54, :425, :662. Suggested commands: `$impeccable clarify forecast` and `$impeccable audit forecast`.

5. **[P2] Empty Forecast starts with plotting apparatus.** A new ledger shows a blank money chart with meaningless default ticks, an uncertainty legend, editable controls, and disabled Run. The correct Add accounts/import/demo instruction exists beneath the plot. Replace the blank plotting area with a direct setup message and Add accounts action, plus an optional demo action. Reveal scenario controls when there is something to project. Sources: finfore/ui/forecast_page.py:683, :744. Suggested command: `$impeccable onboard forecast`.

No blocking P0 issue was observed.

## Cognitive load and emotional journey

Grouping and progressive disclosure work well. Hierarchy and working-memory demands are weaker: six equal-weight levers appear before the verdict, percentages need translation, and comparison requires remembering the baseline identity. The number of controls alone does not establish overload; hidden consequences and the clipped form provide concrete evidence. The calm initial composition supports trust, but a warning below the viewport and silent invalid submission weaken reassurance. The payment-cause explanation is a useful endpoint once found.

## Persona concerns

- First-time planner: May confuse the two emergency-fund references, overlook the verdict, or struggle with the clipped event form.
- Frequent planner: Has powerful controls but must infer numerical scenario improvements and resize the splitter to recover hidden inputs.
- Keyboard or assistive-technology user: Slider and event semantics need explicit naming. Windows screen-reader behavior and full keyboard completion were not tested.

## Smaller observations

Reset scenario leaves saved events intact, consistent with its tooltip; Reset controls would make that scope clearer. Goal arrival ranges need a short explanation, and Beyond this horizon could offer a nearby way to extend the forecast. The interaction between automatic updates and Run forecast should be explicit. Synchronous forecast execution has no busy-state handling in the source, but maximum-setting performance and engine-error recovery were not exercised.

## Evidence and limits

Both assessments ran independently, and A finished before the parent read B's detector results. Fresh native Qt captures covered Simple and advanced views at 1280 × 900 and 980 × 620, balanced and stressed demos, scenario changes, events, reset, backtest, and empty stores. The invalid-to-valid event check confirmed silent rejection followed by successful creation. Synthetic stores and isolated preferences were used throughout.

The required detector attempt returned exit 0 and zero findings. Its web rules do not inspect native PyQt rendering; this is not an automated clean bill of health. Native screenshots, geometry, and interactions supplied the useful evidence. Tested opaque theme-color pairs passed text-contrast checks, which does not establish accessibility across every state. Windows rendering, screen readers, exhaustive keyboard use, maximum-size simulations, and catastrophic engine failures remain untested. This critique does not validate financial predictions.

Application source was unchanged. Temporary runners, databases, and preferences were removed. Native screenshot evidence remains under /tmp/forecast_a_*.png and /tmp/fwd-forecast-b-evidence/.

## Questions to consider

1. Which improvement should lead: making the answer and controls visible, clarifying scenario comparisons, or guiding first-time setup?
2. What scope should the next pass cover: the two major layout failures, the top three issues, or all five?
