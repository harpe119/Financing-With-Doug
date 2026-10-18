# Financing with Doug manual

Open **[financing_with_doug_manual.pdf](financing_with_doug_manual.pdf)** for the
user's manual and calculation guide. **Help → User manual (PDF)** opens the
bundled PDF in the computer's PDF reader. The manual can also be read independently.

The book follows the useful structure of the PRISM manual: a starting path,
task chapters, worked calculations, the calculation methods, and maintenance
instructions. It is written for users without finance or programming training;
the calculation and maintenance sections can be read separately.

The cover uses a fresh full-color, human-looking caricature of Doug based on the
user-supplied photograph. Huge glasses, magnified eyes, and a solemnly goofy
expression carry the family humor. The artwork is `figures/cover-doug-v6.png`;
its reference description, exact generation prompt, and hash are recorded in
[the cover provenance](figures/cover-doug-v6-provenance.md). Earlier concepts and
generation records remain available alongside it.

The title page places the title and subtitle above the portrait, with author,
revision, and date below. Its small caption reads: “Doug's self-portrait.
Accuracy not independently verified.” All typography and the caption are editable
in `chapters/00_frontmatter.tex`; the artwork is unchanged.

## Build

Requirements: the application's Python dependencies, Python 3.10+, and a TeX
installation with `latexmk`, `pdflatex`, PGFPlots/TikZ, `tcolorbox`, `booktabs`,
`titlesec`, `microtype`, and Latin Modern. Matplotlib is not required.

From this directory:

```sh
make                  # regenerate the fictional examples, build PDF, check it
make pdf              # typeset the retained examples without running Doug
make examples         # regenerate examples and their provenance only
make check            # check structure, saved artifacts and PDF-build warnings
make check-source     # report differences from the recorded application sources
make clean            # remove TeX intermediates; retain PDF and example evidence
```

The default interpreter is the parent workspace's `.venv/bin/python` when it
exists, then the repository's `.venv/bin/python`, then `python3`. Override with
`make PYTHON=/path/to/python` when needed. Windows users can invoke the Python
scripts and `latexmk -pdf -interaction=nonstopmode -halt-on-error
financing_with_doug_manual.tex` directly if Make is unavailable.

The example generator runs only against temporary, fictional databases. It
copies the inspected Python package to a temporary directory before importing
it, hashes that snapshot, and publishes generated files only after every example
succeeds. It does not open the normal application database, edit application
code, run an installer, or publish a release. Python bytecode writing is disabled.

The application bundle includes this PDF through the `docs/` data files in
`packaging/doug.spec`. Packaging stops if the PDF is missing. Rebuild the PDF
before packaging a changed edition; the application does not run TeX when a user
opens the manual. `finfore/ui/manual.py` locates the file in both a source
checkout and a frozen build, then opens it through the system's PDF reader.

## Maintain

- Edit `chapters/*.tex`; `financing_with_doug_manual.tex` controls their order.
- Keep task steps together with their expected result and recovery instructions.
- Use `source_map.json` to find the chapters affected by a feature change.
- Update `ManualRevision` in the main TeX file and the front-matter revision table.
- Run `make`, inspect the changed PDF pages, then run `make check-source`.
- `generated/provenance.json` identifies the exact inspected source bytes,
  interpreter, dependencies, date, checks, and retained output hashes. A PDF-only
  build retains that baseline rather than claiming fresh example execution.

GUI work was in progress when the first edition was prepared. The manual uses
grouped navigation and stable, original diagrams rather than screenshots tied
to one transient layout. Verify navigation labels and feature behavior before
shipping an edition. `check-source` intentionally reports subsequent changes;
it does not rewrite chapters or assume that a source change invalidates every
instruction. Appendix D explains the update and review procedure.
