# Screenshots

Pictures used by [`../../README.md`](../../README.md). The front page has a
commented-out placeholder at every spot where one belongs — search it for
`PICTURE:` and each block says what to capture and what to name the file.

To fill one in: drop the `.png` here under the name the placeholder gives, then
delete the `<!--` line and the `-->` line around the `![...](...)` line so it
stops being a comment and starts being a picture.

The names the README expects:

| File | What it shows |
|---|---|
| `dashboard.png` | The hero shot at the very top — the Dashboard, looking inviting |
| `download.png` | The GitHub releases page, with the Setup.exe under *Assets* circled |
| `smartscreen.png` | The blue "Windows protected your PC" box, with *More info* circled |
| `check-for-updates.png` | The top of the window, showing the **Check for updates** button |
| `add-account.png` | The Add account dialog, filled in |
| `bank-export.png` | A bank website with the Download/Export button circled |
| `import.png` | The import screen: column mapping plus the preview rows |
| `review.png` | Transactions → Review uncategorized, with merchants listed |
| `page-dashboard.png` … `page-progress.png` | One per page, for the tour section |

Nothing breaks if a picture is missing — the placeholder is a comment, so the
page reads fine without it.

**Practical notes.** Use PNG. Keep each one under about 500 KB: GitHub keeps
every version of a binary file forever, so a 4 MB screenshot replaced three
times is 12 MB in this repository permanently. **Win+Shift+S** on Windows gives
you a cropped grab; roughly 1200 px wide is plenty.

**Use a demo dataset, not your own.** **File → Load demo dataset → Balanced**
gives you a realistic, entirely fictional household. Everything in this folder
is public to anyone who can see the repository.
