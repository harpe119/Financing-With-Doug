---
target: transactions
total_score: 25
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 1
target_identity: "file:/home/cnharper/projects/fwd/Financing-With-Doug/finfore/ui/transactions.py"
target_fingerprint: "sha256:36b53cb81fd0b15400eb162c49b20f09ddf39b1d5b0b0a394b3d2cfd3cc7f66c"
target_path: /home/cnharper/projects/fwd/Financing-With-Doug/finfore/ui/transactions.py
timestamp: 2026-10-12T16-43-02Z
slug: finfore-ui-transactions-py
---
Method: dual-agent (A: /root/transactions_design_review · B: /root/transactions_evidence_review)

Transactions has a useful foundation, but the controls need clearer organization. The biggest problem is concrete: at the supported 980 × 620 window size, toolbar labels are cut off and search collapses to an ellipsis. Keep the existing visual identity and improve the everyday task of finding and correcting transactions.

## Design specificity

The signed amounts, account context, merchant corrections, and reimbursement treatment make this a finance ledger with useful product-specific behavior. Familiar native controls suit the task. The opportunity is clearer structure and visible financial treatment, rather than added decoration.

## Design health

Scores are design judgments based on source review and synthetic native interaction evidence, not financial correctness scores.

| Heuristic | Score /4 | Main observation |
|---|---:|---|
| System status | 3 | Counts and correction feedback work; selection and split states need visibility. |
| Real-world language | 3 | Familiar ledger terms; learned corrections need explanation. |
| Control and freedom | 3 | Undo and cancel exist; no combined filter reset. |
| Consistency | 3 | Native controls and coherent styling. |
| Error prevention | 2 | Apply can be enabled without selected rows. |
| Recognition over recall | 2 | Reimbursement editing and saved splits are easy to miss. |
| Efficiency | 3 | Inline edits, batch edits, merchant review, and paging. |
| Minimalist design | 2 | Actions, filters, and permanent instructions compete. |
| Error recovery | 2 | Selection errors explain recovery; empty results and other errors are weaker. |
| Help | 2 | Useful guidance is dense and distant from the action. |
| **Total** | **25/40** | **Acceptable; focused improvements needed.** |

## What works

- Category corrections happen directly in the row; batch changes support larger cleanups.
- Signed, right-aligned amounts communicate direction without relying on color alone.
- Paging, hidden-history counts, correction feedback, and Undo make the ledger manageable and reversible. Successful category edits visibly confirm the result; selection is retained in the tested batch edit.

## Priority issues

1. **[P1] The toolbar does not fit the supported minimum window.** In the full 980 × 620 app, Import/Review text and filter values are clipped, and search becomes an ellipsis. Put Import and Review on an action row, with search and filters below; preserve readable control widths, including custom-date mode. Source: finfore/ui/transactions.py:100–133. Suggested command: `/impeccable adapt transactions`.

2. **[P2] Long labels crowd out useful transaction details.** With long account and custom-category names, those columns expand to content width, descriptions shrink, and Amount moves offscreen at the narrow size. Bound account/category widths, elide long names with tooltips, and preserve space for Description and Amount. Horizontal scrolling currently provides a workaround. Source: finfore/ui/transactions.py:144–149. Suggested command: `/impeccable layout transactions`.

3. **[P2] Reimbursement splits are difficult to find and verify.** A paycheck containing a saved reimbursement looks like an ordinary deposit; the split is shown only in tooltips. Editing requires right-clicking an eligible deposit. Add a compact visible split indicator and an eligible selected-row action, retaining the actual bank amount. Source: finfore/ui/transactions.py:338–342 and 397–412. Suggested command: `/impeccable clarify transactions`.

4. **[P2] Bulk editing lacks visible selection scope.** Apply becomes enabled after choosing a category even with no selected rows. Clicking it safely explains “Nothing selected,” but the interface could prevent this. Show “N transactions selected” and enable Apply only when a selection and category are present. Source: finfore/ui/transactions.py:176–189 and 445–450. Suggested command: `/impeccable harden transactions`.

5. **[P2] No-match searches offer little recovery guidance.** A blank table with “0 transactions · net $0.00” does not clearly distinguish a filtered result from no imported history. Search already has a clear button; add an in-table “No transactions match these filters” message and an appropriate reset action. Source: finfore/ui/transactions.py:296–322 and 355–361. Suggested command: `/impeccable harden transactions`.

No blocking P0 issue was observed.

## Cognitive load and emotional journey

The ledger is easy to scan at larger sizes, but the toolbar mixes actions and filters, and the permanent footer combines category correction, merchant learning, business exceptions, and paycheck separation. Grouping and progressive disclosure are the main weaknesses. The number of options alone is not proof of overload; observed clipping and weak grouping provide the evidence. Importing has a clear starting point and corrections provide reassurance. An empty search or an invisible saved split weakens that reassurance.

## Persona concerns

- **First-time user:** May miss reimbursement editing or struggle to understand clipped filters.
- **Frequent user:** Has useful bulk tools but no selected count and no enabled column sorting.
- **Keyboard or assistive-technology user:** Many per-row combo boxes and labels without explicit buddy bindings merit a dedicated accessibility check. No Windows screen-reader failure was verified.

## Smaller observations

Shorten the permanent multi-topic instruction and reveal the relevant explanation when editing. Keep the useful default-date and hidden-history disclosure. Render errors expose exception text; useful recovery instructions depend on the failure and were not exercised. Sorting would help investigation, but is not needed to resolve the five issues above.

## Evidence and limits

Both assessments ran independently; A completed before B's detector findings were disclosed. Fresh native captures covered large/narrow desktop, long labels, filtered results, selection, and reimbursement menu/dialog. Synthetic stores only; no personal financial database opened. Linux offscreen Qt evidence does not establish Windows DPI, NVDA, or end-to-end keyboard behavior.

The required detector attempt returned exit 0 and zero findings. It does not inspect PyQt rendering, so this is not an automated clean bill of health; native screenshots, measurements, and interactions supplied the meaningful evidence. Browser overlays are inapplicable to this native page. Application source was unchanged.

## Questions to consider

1. Which priority should come first: readable layout and ledger columns, or reimbursement and selection clarity?
2. What scope suits this pass: all five issues, the first three, or only the narrow-window layout?
