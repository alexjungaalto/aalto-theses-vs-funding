# Thesis supervision vs. research funding

A small, self-contained demo that puts **one anonymous dot per thesis supervisor**
on a single chart:

- **x** — number of master's theses supervised in **2017–2025** (from Aaltodoc)
- **y** — research funding attributed to that person over the **same 2017–2025 window** (their share, € million, from research.fi)

Across ~100 supervisors in two Aalto University departments, supervision volume
and grant income look **largely independent** — the teaching-heavy supervisors
are not the best funded, and vice-versa.

![scatter](theses_vs_funding_anon.png)

## Data sources (all open)

| Signal | Source | Endpoint |
|---|---|---|
| Theses currently in supervision | Aalto **MyCourses** public supervisor list | `mycourses.aalto.fi/mod/page/view.php?id=1074107` |
| Completed master's theses (2017–2025) | **Aaltodoc** institutional repository (DSpace 7) | `aaltodoc.aalto.fi/server/api/discover/search/objects` |
| Granted research funding | **research.fi** national research information hub | `researchfi-api-production.2.rahtiapp.fi/portalapi/funding/_search` |

All three are queried live by the script. No API keys required.

## Usage

```bash
python3 aalto_theses_and_funding.py \
    --matched-only \
    --tikz theses_vs_funding.tex \
    --png  theses_vs_funding_anon.png \
    --json theses_vs_funding.json --no-people
pdflatex theses_vs_funding.tex        # -> theses_vs_funding.pdf
```

Useful flags:

- `--x-metric {aaltodoc,current}` — completed theses in the window (default) vs. currently-in-supervision on the x-axis
- `--year-min YEAR` / `--year-max YEAR` — publication-year window on the Aaltodoc count (default `2017`/`2025`, inclusive of the whole boundary year; pass `all` to open a side)
- `--all-levels` — count all thesis/dissertation levels; by default only master's theses are counted (`dc.type.ontasot = "Master's thesis"`), excluding doctoral, licentiate and bachelor's
- `--matched-only` — drop supervisors with no matched funding record (cleaner plot)
- `--include-advisor` — also count Aaltodoc *advisor* (instructor) roles, not just *supervisor*
- `--funding-query TEXT` — restrict the research.fi total (e.g. an organisation)

## Method notes & caveats

- **Everything is anonymised.** Names are used only transiently to run the
  queries; they are never printed, plotted, or written to disk. Every output
  record is `(faculty, theses, grants, funding_eur)`.
- **Name matching handles first-name variants, honestly.** Aaltodoc records the
  same person under many forms — short/long given names (a short form vs its
  full spelling), title/affiliation suffixes (`"<Surname>, <Given>, Prof., Aalto
  University…"`), spacing and punctuation quirks, and diacritics (accented vs
  unaccented). For each supervisor the script **discovers every given-name form
  stored for their surname**, then merges only the forms that are the *same name
  truncated*. It deliberately does **not** merge bare initials (an *X.* vs a full
  given name) or added name components (one part vs a hyphenated compound name),
  which would inflate the count for a different person. Validated against one
  supervisor's own hand-maintained public thesis list: on a **lifetime** basis
  it shows 126 master's theses and this method returns 123 (correctly dropping
  four supervised doctoral dissertations a level-agnostic count would fold in).
- **Only master's theses are counted.** Aaltodoc stores bachelor's theses,
  master's theses, licentiate theses and doctoral dissertations side by side.
  The count is restricted to master's-thesis records (`dc.type.ontasot =
  "Master's thesis"`, the English umbrella value covering both *diplomityö* and
  *pro gradu*), so supervised **doctoral dissertations are not counted** — they
  are a different kind of supervision. Pass `--all-levels` to include every level.
- **Fixed 2017–2025 window.** The plotted x-axis counts master's theses
  *completed in 2017–2025* (`dc.date.issued`), so supervisors are compared over
  the same recent nine years rather than by career length — otherwise a 30-year
  veteran and a newly appointed professor are not comparable. Change the window
  with `--year-min`/`--year-max`.
- **Auditability.** Run with `--audit-names PATH` to dump a local,
  non-anonymous CSV of exactly which name forms were merged for each person, so
  any residual false merge is visible. That file is intentionally **not**
  included here (it contains names).
- Remaining limits: non-prefix nicknames (a familiar form unrelated to the
  formal given name) are not bridged (undercount), and two real people who share
  a surname and a first-name prefix can still merge (rare, and surfaced by the
  audit). Treat counts as good estimates, not exact.
- **Funding** is each person's own share (`shareOfFundingInEur`) of the grants
  they appear on, so consortia are not double-counted. To match the thesis
  x-axis, it is scoped to grants whose **funding start year (`fundingStartYear`)
  falls in the same 2017–2025 window** — the only reliable date on these records
  (`fundingEndYear` is frequently a `1900` placeholder). Both scatter axes
  therefore cover the same period; pass `--year-min all --year-max all` for the
  all-time total instead. (The headline "total granted funding on research.fi"
  printed by the script is a separate, dataset-wide context figure and is not
  windowed.)
- Supervisors with **no matched funding record** are omitted from the
  `--matched-only` chart rather than plotted as a misleading €0.

## Files

- `aalto_theses_and_funding.py` — the script (Python 3, standard library + matplotlib for the optional PNG)
- `theses_vs_funding.tex` / `.pdf` — TikZ/pgfplots scatter (anonymous, larger markers)
- `theses_vs_funding_anon.png` — matplotlib preview
- `theses_vs_funding.json` — anonymised per-supervisor data
- `linkedin_theses_vs_funding.md` — a write-up draft
