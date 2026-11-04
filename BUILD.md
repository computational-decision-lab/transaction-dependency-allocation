# Build

## Manuscript

From `paper/`, with TeX Live, `latexmk`, BibTeX, and the IEEEtran class installed:

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

The manuscript uses the IEEEtran conference layout on US Letter paper. The generated output is `main.pdf`.

## Analysis code

Use Python 3 and install the pinned analysis packages:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r ../requirements.txt
```

Bitcoin Core binaries are intentionally not included. The exact versions, commits, and build requirements are documented in the paper and archived verification records. Core replay requires separately built or obtained binaries matching those declarations.
