#!/usr/bin/env python3
"""
Aalto supervised-theses + research.fi funding report.

Two independent data pulls, stdlib only (no pip install needed):

1. Supervised master's theses per faculty/department, scraped from the public
   MyCourses page:
       https://mycourses.aalto.fi/mod/page/view.php?id=1074107
   The page has two HTML tables:
     - Table 0: all supervisors of the Department of Computer Science.
     - Table 1: supervisors from other departments, each tagged "(code)"
       (e.g. "Lastname, Firstname (elec)"). We group these by that code.
   Each row is (supervisor, number-of-theses-in-supervision, topic).

Individual researcher names are used only transiently to query research.fi;
they are never printed, plotted, or written to output. The scatter plot and
the exported data are anonymised (one dot per supervisor, no labels).

2. Total amount of granted funding as indicated on research.fi, via the same
   Elasticsearch-backed backend the portal's own frontend calls:
       POST https://researchfi-api-production.2.rahtiapp.fi/portalapi/funding/_search
   We run a `sum` aggregation over the `amount_in_EUR` field. By default this is
   the grand total across all ~23.7k grants; pass --funding-query to restrict it
   (e.g. to a funder or recipient organisation).

3. (--tikz/--png) Per-supervisor lifetime master's-thesis count, estimated by
   searching Aaltodoc, Aalto's DSpace repository of completed theses, for each
   supervisor:
       GET https://aaltodoc.aalto.fi/server/api/discover/search/objects
   matching dc.contributor.supervisor (and .advisor with --include-advisor), and
   restricted to master's-thesis records (dc.type.ontasot = "Master's thesis")
   so doctoral dissertations, licentiate and bachelor's theses are NOT counted;
   pass --all-levels to count every level instead. Counts are further limited to
   a publication-year window on dc.date.issued (default 2017–2025); override with
   --year-min/--year-max (use 'all' to open a side).
   The repository records the same person under several name forms (short/long
   given names, title/affiliation suffixes, spacing quirks), so for each
   supervisor we discover all forms stored for their surname, merge the ones
   whose first name is prefix-compatible in either direction, and count the
   deduped union. Pass --audit-names to dump exactly which forms were merged.
   This is the default X-axis of the scatter (the MyCourses figure only counts
   master's theses *currently* in supervision); use --x-metric current for that.
   For the scatter, each supervisor's research.fi funding (the Y-axis) is scoped
   to the SAME window via fundingStartYear, so both axes cover 2017–2025 rather
   than comparing recent theses against all-time funding.

Usage:
    python3 aalto_theses_and_funding.py
    python3 aalto_theses_and_funding.py --json report.json
    python3 aalto_theses_and_funding.py --funding-query "Aalto"
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

MYCOURSES_URL = "https://mycourses.aalto.fi/mod/page/view.php?id=1074107"
RESEARCHFI_API = (
    "https://researchfi-api-production.2.rahtiapp.fi/portalapi/funding/_search"
)
# Aaltodoc = Aalto's DSpace 7 thesis/publication repository (completed theses).
AALTODOC_API = "https://aaltodoc.aalto.fi/server/api/discover/search/objects"
USER_AGENT = "Mozilla/5.0 (aalto-theses-funding-report)"
TIMEOUT = 30

# Default publication window, applied to BOTH the Aaltodoc master's-thesis count
# (dc.date.issued) and each supervisor's research.fi funding (fundingStartYear),
# so the two scatter axes cover the same years. Set either side to None (via
# --year-min/--year-max all) to leave it open.
_DEFAULT_YEAR_MIN = 2017
_DEFAULT_YEAR_MAX = 2025


# --------------------------------------------------------------------------- #
# 1. MyCourses: supervised theses per faculty
# --------------------------------------------------------------------------- #
class _TableParser(HTMLParser):
    """Collect every <table> as a list of rows, each row a list of cell texts."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_cell = False
        self._rows: list[list[str]] = []
        self._cells: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self._rows = []
        elif tag == "tr" and self._in_table:
            self._cells = []
        elif tag in ("td", "th") and self._in_table:
            self._in_cell = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "table" and self._in_table:
            self.tables.append(self._rows)
            self._in_table = False
        elif tag == "tr" and self._in_table:
            if self._cells:
                self._rows.append(self._cells)
        elif tag in ("td", "th") and self._in_cell:
            text = " ".join("".join(self._buf).split())
            self._cells.append(text)
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell:
            self._buf.append(data)


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _to_int(value: str) -> int:
    """Parse a thesis-count cell; blanks / non-numeric become 0."""
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else 0


def _department_of(name_cell: str, default: str) -> str:
    """
    Extract a department code from a supervisor cell.

    Table 1 tags names like "Alku, Paavo (elec)". If no "(code)" is present
    (Table 0), fall back to `default` (the table's own department heading).
    """
    if name_cell.endswith(")") and "(" in name_cell:
        code = name_cell[name_cell.rfind("(") + 1 : -1].strip()
        if code:
            return code.upper()
    return default


def scrape_theses_per_faculty(html: str) -> dict:
    parser = _TableParser()
    parser.feed(html)

    # Keep only tables that look like the supervisor tables: a header row whose
    # second column mentions "theses", plus at least one data row.
    supervisor_tables = [
        rows
        for rows in parser.tables
        if len(rows) >= 2
        and len(rows[0]) >= 2
        and "theses" in rows[0][1].lower()
    ]
    if not supervisor_tables:
        raise RuntimeError(
            "No supervisor tables found on the MyCourses page. "
            "The page layout may have changed."
        )

    faculties: dict[str, dict] = defaultdict(
        lambda: {"supervisors": 0, "theses": 0, "people": []}
    )

    for rows in supervisor_tables:
        header = rows[0]
        # The header's first cell names the table's home department, e.g.
        # "Supervisors from The Department of Computer Science".
        heading = header[0]
        default_dept = (
            heading.split("from", 1)[1].strip()
            if "from" in heading.lower()
            else heading.strip()
        ) or "Unknown"
        # Normalise the CS table's long heading to a short label.
        if "computer science" in default_dept.lower():
            default_dept = "Computer Science (CS)"

        for row in rows[1:]:
            if len(row) < 2 or not row[0].strip():
                continue
            name = row[0].strip()
            count = _to_int(row[1])
            dept = _department_of(name, default_dept)
            bucket = faculties[dept]
            bucket["supervisors"] += 1
            bucket["theses"] += count
            bucket["people"].append({"name": name, "theses": count})

    total_theses = sum(f["theses"] for f in faculties.values())
    total_supervisors = sum(f["supervisors"] for f in faculties.values())
    return {
        "source": MYCOURSES_URL,
        "faculties": dict(faculties),
        "total_theses": total_theses,
        "total_supervisors": total_supervisors,
    }


# --------------------------------------------------------------------------- #
# 2. research.fi: total granted funding
# --------------------------------------------------------------------------- #
def fetch_total_funding(query_string: str | None = None) -> dict:
    """
    Sum `amount_in_EUR` over the funding index. Optionally restrict with a
    free-text query_string (matches funder / recipient / project fields).
    """
    if query_string:
        query = {
            "query_string": {
                "query": query_string,
                "default_operator": "AND",
            }
        }
    else:
        query = {"match_all": {}}

    body = {
        "size": 0,
        "track_total_hits": True,
        "query": query,
        "aggs": {"total_eur": {"sum": {"field": "amount_in_EUR"}}},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        RESEARCHFI_API + "?request_cache=true",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    return {
        "source": RESEARCHFI_API,
        "query": query_string or "(all granted funding)",
        "grant_count": result["hits"]["total"]["value"],
        "total_eur": result["aggregations"]["total_eur"]["value"],
    }


# --------------------------------------------------------------------------- #
# 3. Per-supervisor funding (for the scatter plot)
# --------------------------------------------------------------------------- #
def parse_name(cell: str) -> tuple[str, str]:
    """
    Split a MyCourses supervisor cell into (first_name, last_name).

    Handles both layouts:
      "Lastname, Firstname (elec)" -> ("Firstname", "Lastname")  [Table 1]
      "Firstname Lastname"         -> ("Firstname", "Lastname")  [Table 0]
    Only the first given name is returned, to match research.fi leniently.
    """
    name = cell.strip()
    if name.endswith(")") and "(" in name:  # drop trailing "(elec)" tag
        name = name[: name.rfind("(")].strip()
    if "," in name:  # "Last, First"
        last, _, first = name.partition(",")
        last, first = last.strip(), first.strip()
    else:  # "First ... Last"
        parts = name.split()
        last = parts[-1] if parts else ""
        first = " ".join(parts[:-1])
    first_token = first.split()[0] if first.split() else ""
    return first_token, last


def _person_clause(first: str, last: str) -> dict:
    must = [
        {"match_phrase": {"fundingGroupPerson.fundingGroupPersonLastName": last}}
    ]
    if first:
        must.append(
            {"match": {"fundingGroupPerson.fundingGroupPersonFirstNames": first}}
        )
    return {"bool": {"must": must}}


def fetch_supervisor_funding(
    first: str,
    last: str,
    year_min: int | None = _DEFAULT_YEAR_MIN,
    year_max: int | None = _DEFAULT_YEAR_MAX,
) -> dict:
    """
    Total funding attributed to one person on research.fi: the sum of their
    `shareOfFundingInEur` across every grant whose funding group includes them
    and whose funding start year (`fundingStartYear`) falls in [year_min,
    year_max]. Pass year_min/year_max=None to leave that side open (all-time).

    The window is applied on fundingStartYear, the only reliable date on these
    records (fundingEndYear is frequently a 1900 placeholder), so the y-axis is
    scoped to the same period as the master's-thesis x-axis.
    """
    if not last:
        return {"grants": 0, "funding_eur": 0.0}
    clause = _person_clause(first, last)
    outer_must: list = [{"nested": {"path": "fundingGroupPerson", "query": clause}}]
    if year_min is not None or year_max is not None:
        rng: dict = {}
        if year_min is not None:
            rng["gte"] = year_min
        if year_max is not None:
            rng["lte"] = year_max
        outer_must.append({"range": {"fundingStartYear": rng}})
    body = {
        "size": 0,
        "track_total_hits": True,
        "query": {"bool": {"must": outer_must}},
        "aggs": {
            "fgp": {
                "nested": {"path": "fundingGroupPerson"},
                "aggs": {
                    "sel": {
                        "filter": clause,
                        "aggs": {
                            "share": {
                                "sum": {
                                    "field": "fundingGroupPerson.shareOfFundingInEur"
                                }
                            }
                        },
                    }
                },
            }
        },
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        RESEARCHFI_API + "?request_cache=true",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return {
        "grants": result["hits"]["total"]["value"],
        "funding_eur": result["aggregations"]["fgp"]["sel"]["share"]["value"],
    }


_SUP_FIELD = "dc.contributor.supervisor"
_ADV_FIELD = "dc.contributor.advisor"
# Aaltodoc tags each item's academic level in dc.type.ontasot. Every master's
# thesis carries the English umbrella value "Master's thesis" (covering both
# "Diplomityö" and "Pro gradu"); bachelor's theses, licentiate theses and
# doctoral dissertations carry their own values. Restricting counts to this
# value is what makes the figure "master's theses supervised" rather than "all
# theses + dissertations supervised". Use the English value, not "Diplomityö",
# which alone would miss pro gradu master's theses.
_MASTER_LEVEL_CLAUSE = 'dc.type.ontasot:"Master\'s thesis"'
# dc.date.issued is stored as full ISO dates (e.g. "2025-05-26"); a [MIN TO MAX]
# range on it is inclusive of the whole of both boundary years. The window
# defaults live near the top of the module (_DEFAULT_YEAR_MIN/_MAX).


def _ascii_lower(s: str) -> str:
    """Fold to lowercase ASCII (drops diacritics), for name comparison."""
    return (
        unicodedata.normalize("NFKD", s)
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .strip()
    )


def _name_key(firstname: str) -> str:
    """
    Reduce a given-name string to a clean comparison key: ASCII-fold, take the
    first name only, keep letters and internal hyphens, and stop at the first
    packing/punctuation character. So:
        "<Given>, Prof., Aalto ..." -> "<given>"
        "<Given>|<CoName>"          -> "<given>"   (packed co-supervisor split off)
        "<Given>.,"                 -> "<given>"
        "<A>-<B>"                   -> "<a>-<b>"    (hyphen kept: compound name)
    """
    tok = _ascii_lower(firstname).lstrip()
    out = []
    for ch in tok:
        if ch.isalpha() or ch == "-":
            out.append(ch)
        else:
            break
    return "".join(out).strip("-")


def _first_compatible(target_key: str, form_key: str) -> bool:
    """
    True only when two name keys are the SAME name, one possibly truncated:
    a short given name vs its longer spelling, or a spelling that drops one
    trailing letter. It deliberately does NOT merge:
      * bare single-letter initials (an "X." vs a full given name) — too
        ambiguous, and
      * added name components (one part vs a hyphenated compound name) — a
        different identity, detected because the extra characters begin with a
        hyphen.
    It also cannot bridge non-prefix nicknames (a familiar form unrelated to the
    formal given name). Merges are always reported in the audit so any residual
    false merge is visible.
    """
    a, b = target_key, form_key
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 2:            # bare initial: refuse to merge
        return False
    if not long.startswith(short):
        return False
    return long[len(short):][:1].isalpha()   # reject hyphen/component additions


def _aaltodoc_get(query: str, size: int = 1, page: int = 0) -> dict:
    url = (
        f"{AALTODOC_API}?query={urllib.parse.quote(query)}"
        f"&dsoType=item&size={size}&page={page}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _discover_surname_forms(
    last: str, fields: tuple, max_pages: int = 15
) -> tuple[set, bool]:
    """
    Enumerate the distinct given-name forms Aaltodoc actually records for a
    surname, by scanning the supervisor/advisor fields. Returns (set of first
    forms, truncated?). `truncated` is True if the surname has more matching
    items than max_pages*100 (rare) and discovery may be incomplete.
    """
    nlast = _ascii_lower(last)
    forms: set = set()
    truncated = False
    for fld in fields:
        page = 0
        query = f"{fld}:({last})"
        while True:
            data = _aaltodoc_get(query, size=100, page=page)
            sr = data["_embedded"]["searchResult"]
            for obj in sr["_embedded"].get("objects", []):
                md = obj["_embedded"]["indexableObject"]["metadata"]
                for v in md.get(fld, []):
                    val = v["value"]
                    if "," in val:
                        surname, rest = val.split(",", 1)
                    else:  # e.g. "<Surname> <Given>, Prof." (no comma separator)
                        toks = val.split()
                        surname = toks[0] if toks else ""
                        rest = " ".join(toks[1:])
                    key = _name_key(rest)
                    surtoks = [_ascii_lower(t) for t in surname.replace("-", " ").split()]
                    if key and nlast in surtoks:
                        forms.add(key)
            total_pages = sr["page"]["totalPages"]
            page += 1
            if page >= total_pages:
                break
            if page >= max_pages:
                truncated = True
                break
    return forms, truncated


def _with_filters(
    name_query: str, master_only: bool, year_min: int | None, year_max: int | None
) -> str:
    """
    AND the level and publication-year restrictions onto an OR-group of name
    clauses. A [year_min TO year_max] range on dc.date.issued is inclusive of the
    whole of both boundary years; either bound may be None to leave that side open.
    """
    query = f"({name_query})"
    if master_only:
        query += f" AND {_MASTER_LEVEL_CLAUSE}"
    if year_min is not None or year_max is not None:
        lo = str(year_min) if year_min is not None else "*"
        hi = str(year_max) if year_max is not None else "*"
        query += f" AND dc.date.issued:[{lo} TO {hi}]"
    return query


def fetch_aaltodoc_thesis_count(
    first: str,
    last: str,
    include_advisor: bool = False,
    master_only: bool = True,
    year_min: int | None = _DEFAULT_YEAR_MIN,
    year_max: int | None = _DEFAULT_YEAR_MAX,
) -> tuple[int, list, bool]:
    """
    Estimate a supervisor's master's-thesis count from Aaltodoc (Aalto's DSpace
    repository of completed theses) over a publication-year window, honestly
    merging first-name variants.

    Method:
      1. Discover every given-name form the repository stores for this surname
         (handles title/affiliation suffixes, missing commas, spacing).
      2. Keep the forms prefix-compatible with the target first name, in BOTH
         directions (so short->long AND long->short are covered).
      3. Count the deduped union of those forms as a phrase query, restricted to
         master's-thesis records only (dc.type.ontasot) and to the
         [year_min, year_max] window on dc.date.issued, so doctoral
         dissertations, licentiate and bachelor's theses, and theses outside the
         window are NOT counted.

    Name-form discovery in step 1 is deliberately left unfiltered (a name variant
    is a name variant regardless of level or year), so the filters never weaken
    name matching; they only narrow what is finally counted.

    Supervisor field only by default (the "responsible professor" role, which
    matches how the self-maintained ml-theses.org counts supervision); pass
    include_advisor=True to also count advisor/instructor roles. Pass
    master_only=False to count all thesis/dissertation levels, and
    year_min/year_max=None to leave that side of the window open.

    Returns (count, merged_first_forms, truncated). merged_first_forms lets the
    caller audit exactly which name variants were combined.
    """
    if not last:
        return 0, [], False
    fields = (_SUP_FIELD, _ADV_FIELD) if include_advisor else (_SUP_FIELD,)
    target = _name_key(first)
    forms, truncated = _discover_surname_forms(last, fields)
    kept = sorted(f for f in forms if _first_compatible(target, f))
    if not kept:
        # Discovery found no compatible form; fall back to an exact phrase on
        # the MyCourses name so we still return a best-effort number.
        if not first:
            return 0, [], truncated
        name_query = " OR ".join(f'{fld}:"{last}, {first}"' for fld in fields)
        query = _with_filters(name_query, master_only, year_min, year_max)
        cnt = _aaltodoc_get(query)["_embedded"]["searchResult"]["page"]["totalElements"]
        return int(cnt), [target] if target else [], truncated
    # Count the deduped union. A hyphenated key becomes an ordered phrase (its
    # two parts, space-separated) so it matches the compound name but not the
    # bare single part; a simple key matches its token anywhere in the value.
    clauses = [
        f'{fld}:"{last}, {form.replace("-", " ")}"' for form in kept for fld in fields
    ]
    name_query = " OR ".join(clauses)
    query = _with_filters(name_query, master_only, year_min, year_max)
    cnt = _aaltodoc_get(query)["_embedded"]["searchResult"]["page"]["totalElements"]
    return int(cnt), kept, truncated


def build_supervisor_points(
    theses: dict, workers: int = 8, include_advisor: bool = False,
    master_only: bool = True,
    year_min: int | None = _DEFAULT_YEAR_MIN,
    year_max: int | None = _DEFAULT_YEAR_MAX,
) -> list[dict]:
    """
    Flatten faculties into supervisor records and look up funding for each.

    Returns (points, audit). `points` are anonymous — names are used only to
    query the external services and never appear in a point. `audit` is a
    parallel, NAMED list (surname, first name, merged variant forms) for local
    verification; it is never written to the anonymised outputs.
    """
    queries = []
    for dept, info in theses["faculties"].items():
        for person in info["people"]:
            first, last = parse_name(person["name"])
            queries.append(
                {"faculty": dept, "theses": person["theses"], "first": first, "last": last}
            )

    def _lookup(q: dict) -> tuple:
        try:
            fund = fetch_supervisor_funding(
                q["first"], q["last"], year_min, year_max
            )
        except Exception:  # noqa: BLE001 - keep the point, mark funding unknown
            fund = {"grants": 0, "funding_eur": 0.0}
        try:
            adoc, forms, truncated = fetch_aaltodoc_thesis_count(
                q["first"], q["last"], include_advisor, master_only,
                year_min, year_max,
            )
        except Exception:  # noqa: BLE001 - keep the point, mark count unknown
            adoc, forms, truncated = 0, [], False
        # Anonymised record: no names, but keep how many name-forms were merged
        # and whether discovery was truncated, so the estimate stays honest.
        point = {
            "faculty": q["faculty"],
            "theses_current": q["theses"],       # master's, currently supervised (MyCourses)
            "theses_aaltodoc": adoc,             # completed master's theses in window (Aaltodoc)
            "aaltodoc_name_forms": len(forms),   # # of first-name variants merged
            "aaltodoc_truncated": truncated,
            "grants": fund["grants"],
            "funding_eur": fund["funding_eur"],
        }
        # Separate, NAMED audit row (never written to the anonymised outputs).
        audit = {
            "last": q["last"],
            "first": q["first"],
            "faculty": q["faculty"],
            "theses_aaltodoc": adoc,
            "merged_first_forms": forms,
            "truncated": truncated,
        }
        return point, audit

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_lookup, queries))
    points = [r[0] for r in results]
    audit = [r[1] for r in results]
    return points, audit


# A small, colour-blind-friendly palette (name, pgf RGB, matplotlib hex, mark).
_PALETTE = [
    ("cbBlue", (31, 119, 180), "#1f77b4", "*"),
    ("cbOrange", (255, 127, 14), "#ff7f0e", "square*"),
    ("cbGreen", (44, 160, 44), "#2ca02c", "triangle*"),
    ("cbRed", (214, 39, 40), "#d62728", "diamond*"),
    ("cbPurple", (148, 103, 189), "#9467bd", "pentagon*"),
    ("cbBrown", (140, 86, 75), "#8c564b", "otimes*"),
]


def plot_scatter_png(
    points: list[dict], path: str, x_field: str, x_label: str,
    y_label: str = "Total funding on research.fi (million EUR)",
) -> None:
    """Anonymous preview PNG (no names/labels), larger markers."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    faculties = sorted({p["faculty"] for p in points})
    style = {f: _PALETTE[i % len(_PALETTE)] for i, f in enumerate(faculties)}
    mpl_marker = {"*": "o", "square*": "s", "triangle*": "^", "diamond*": "D",
                  "pentagon*": "p", "otimes*": "P"}

    fig, ax = plt.subplots(figsize=(11, 7.5))
    for f in faculties:
        pts = [p for p in points if p["faculty"] == f]
        _, _, hexc, mark = style[f]
        ax.scatter(
            [p[x_field] for p in pts],
            [p["funding_eur"] / 1e6 for p in pts],
            s=130,  # larger markers
            alpha=0.8,
            color=hexc,
            marker=mpl_marker[mark],
            edgecolors="white",
            linewidths=0.6,
            label=f,
        )
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title("Thesis supervision vs. research funding")
    ax.grid(True, alpha=0.3)
    ax.legend(title="Faculty/dept", frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_scatter_tikz(
    points: list[dict], path: str, x_field: str, x_label: str,
    y_label: str = "Total funding on research.fi (million EUR)",
) -> None:
    """
    Write a standalone, compilable TikZ/pgfplots scatter plot.

    One \\addplot per faculty, larger markers (mark size=3.2pt), no names or
    per-point labels. Compile with: pdflatex <file.tex>  (or lualatex).
    """
    faculties = sorted({p["faculty"] for p in points})
    style = {f: _PALETTE[i % len(_PALETTE)] for i, f in enumerate(faculties)}

    lines: list[str] = []
    lines.append(r"\documentclass[border=6pt]{standalone}")
    lines.append(r"\usepackage{pgfplots}")
    lines.append(r"\pgfplotsset{compat=1.18}")
    # Named colours for the palette.
    for f in faculties:
        cname, (r, g, b), _, _ = style[f]
        lines.append(rf"\definecolor{{{cname}}}{{RGB}}{{{r},{g},{b}}}")
    lines.append(r"\begin{document}")
    lines.append(r"\begin{tikzpicture}")
    lines.append(r"\begin{axis}[")
    lines.append(r"    width=14cm, height=9.5cm,")
    lines.append(rf"    xlabel={{{x_label}}},")
    lines.append(rf"    ylabel={{{y_label}}},")
    lines.append(r"    title={Thesis supervision load vs.\ research funding},")
    lines.append(r"    grid=both, grid style={gray!18},")
    lines.append(r"    axis lines=left,")
    lines.append(r"    legend pos=north east, legend cell align={left},")
    lines.append(r"    legend style={draw=none, font=\small},")
    lines.append(r"    xmin=-0.5,")
    lines.append(r"]")
    for f in faculties:
        cname, _, _, mark = style[f]
        lines.append(
            rf"\addplot[only marks, mark={mark}, mark size=3.2pt, "
            rf"color={cname}, fill={cname}, fill opacity=0.8, "
            rf"draw=white, line width=0.4pt] coordinates {{"
        )
        coords = " ".join(
            f"({p[x_field]},{p['funding_eur'] / 1e6:.4f})"
            for p in points
            if p["faculty"] == f
        )
        lines.append("    " + coords)
        lines.append(r"};")
        # Escape any stray underscores in faculty labels for LaTeX.
        legend = f.replace("_", r"\_")
        lines.append(rf"\addlegendentry{{{legend}}}")
    lines.append(r"\end{axis}")
    lines.append(r"\end{tikzpicture}")
    lines.append(r"\end{document}")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_report(theses: dict, funding: dict) -> None:
    print("=" * 66)
    print("Supervised master's theses per faculty/department")
    print(f"source: {theses['source']}")
    print("=" * 66)
    ordered = sorted(
        theses["faculties"].items(), key=lambda kv: kv[1]["theses"], reverse=True
    )
    print(f"{'Faculty/dept':<28}{'Supervisors':>12}{'Theses':>10}")
    print("-" * 66)
    for dept, info in ordered:
        print(f"{dept:<28}{info['supervisors']:>12}{info['theses']:>10}")
    print("-" * 66)
    print(
        f"{'TOTAL':<28}{theses['total_supervisors']:>12}"
        f"{theses['total_theses']:>10}"
    )

    print()
    print("=" * 66)
    print("Total granted funding on research.fi")
    print(f"source: {funding['source']}")
    print("=" * 66)
    print(f"filter      : {funding['query']}")
    print(f"grant count : {funding['grant_count']:,}")
    print(f"total funding: EUR {funding['total_eur']:,.2f}")
    print(f"             (approx. EUR {funding['total_eur'] / 1e9:.2f} billion)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--funding-query",
        metavar="TEXT",
        default=None,
        help="Restrict research.fi funding total (e.g. 'Aalto'). "
        "Default: grand total across all grants.",
    )
    ap.add_argument(
        "--json",
        metavar="PATH",
        default=None,
        help="Also write the combined result to this JSON file.",
    )
    ap.add_argument(
        "--no-people",
        action="store_true",
        help="Omit the per-supervisor lists from the JSON output.",
    )
    ap.add_argument(
        "--tikz",
        metavar="PATH",
        default=None,
        help="Look up each supervisor's funding on research.fi and write an "
        "anonymous TikZ/pgfplots scatter (x=theses, y=funding) to this .tex file.",
    )
    ap.add_argument(
        "--png",
        metavar="PATH",
        default=None,
        help="Also write an anonymous matplotlib preview PNG of the scatter.",
    )
    ap.add_argument(
        "--matched-only",
        action="store_true",
        help="Drop supervisors with no matched research.fi funding (funding=0) "
        "from the scatter, for a cleaner plot free of name-match failures.",
    )
    ap.add_argument(
        "--x-metric",
        choices=["aaltodoc", "current"],
        default="aaltodoc",
        help="X-axis: 'aaltodoc' = lifetime completed theses from the Aaltodoc "
        "repository (default); 'current' = theses currently in supervision "
        "(MyCourses).",
    )
    ap.add_argument(
        "--include-advisor",
        action="store_true",
        help="Count Aaltodoc advisor (instructor) roles in addition to "
        "supervisor (responsible professor). Default: supervisor only, which "
        "matches how ml-theses.org counts supervision.",
    )
    ap.add_argument(
        "--all-levels",
        action="store_true",
        help="Count all thesis/dissertation levels in Aaltodoc. Default: "
        "master's theses only (dc.type.ontasot = \"Master's thesis\"), so "
        "doctoral dissertations, licentiate and bachelor's theses are excluded.",
    )
    ap.add_argument(
        "--year-min",
        default=str(_DEFAULT_YEAR_MIN),
        metavar="YEAR",
        help=f"Earliest publication year to count in Aaltodoc (dc.date.issued). "
        f"Default {_DEFAULT_YEAR_MIN}. Use 'all' for no lower bound.",
    )
    ap.add_argument(
        "--year-max",
        default=str(_DEFAULT_YEAR_MAX),
        metavar="YEAR",
        help=f"Latest publication year to count in Aaltodoc (inclusive of the "
        f"whole year). Default {_DEFAULT_YEAR_MAX}. Use 'all' for no upper bound.",
    )
    ap.add_argument(
        "--audit-names",
        metavar="PATH",
        default=None,
        help="Write a LOCAL, NON-ANONYMOUS CSV (name + which Aaltodoc first-name "
        "variants were merged + count) so the variant merging can be checked. "
        "Do not publish this file.",
    )
    args = ap.parse_args()

    def _parse_year(val: str) -> int | None:
        if val.strip().lower() == "all":
            return None
        try:
            return int(val)
        except ValueError:
            ap.error(f"--year-* must be a 4-digit year or 'all', got {val!r}")

    year_min = _parse_year(args.year_min)
    year_max = _parse_year(args.year_max)

    try:
        html = _fetch(MYCOURSES_URL)
        theses = scrape_theses_per_faculty(html)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR fetching/parsing MyCourses page: {exc}", file=sys.stderr)
        return 1

    try:
        funding = fetch_total_funding(args.funding_query)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR querying research.fi: {exc}", file=sys.stderr)
        return 1

    print_report(theses, funding)

    points = None
    if args.tikz or args.png:
        print("\nLooking up per-supervisor funding on research.fi ...", flush=True)
        points, audit = build_supervisor_points(
            theses, include_advisor=args.include_advisor,
            master_only=not args.all_levels,
            year_min=year_min, year_max=year_max,
        )
        matched = sum(1 for p in points if p["funding_eur"] > 0)
        total_aaltodoc = sum(p["theses_aaltodoc"] for p in points)
        merged = sum(1 for p in points if p["aaltodoc_name_forms"] > 1)
        truncated = sum(1 for p in points if p["aaltodoc_truncated"])
        level_word = "theses (all levels)" if args.all_levels else "master's theses"
        window = (
            f"{year_min if year_min is not None else '…'}–"
            f"{year_max if year_max is not None else '…'}"
            if (year_min is not None or year_max is not None)
            else "all years"
        )
        print(
            f"  Aaltodoc: {total_aaltodoc:,} completed {level_word} ({window}) "
            f"across {len(points)} supervisors "
            f"(vs {theses['total_theses']} currently in supervision)"
        )
        print(
            f"  name-variant merging: {merged} supervisor(s) had >1 first-name "
            f"form combined; {truncated} had truncated discovery"
        )
        if args.audit_names:
            with open(args.audit_names, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(
                    ["last", "first", "faculty", "theses_aaltodoc",
                     "merged_first_forms", "truncated"]
                )
                for a in sorted(audit, key=lambda r: -r["theses_aaltodoc"]):
                    w.writerow([
                        a["last"], a["first"], a["faculty"], a["theses_aaltodoc"],
                        " | ".join(a["merged_first_forms"]), a["truncated"],
                    ])
            print(f"  Wrote NON-ANONYMOUS audit {args.audit_names} (do not publish)")
        if args.matched_only:
            dropped = len(points) - matched
            points = [p for p in points if p["funding_eur"] > 0]
            print(f"  matched-only: kept {len(points)}, dropped {dropped} zero-funding")

        x_field = "theses_aaltodoc" if args.x_metric == "aaltodoc" else "theses_current"
        level_word = "theses" if args.all_levels else "master's theses"
        x_label = (
            f"Completed {level_word} supervised (Aaltodoc, {window})"
            if args.x_metric == "aaltodoc"
            else "Master's theses currently in supervision (MyCourses)"
        )
        y_window = "" if window == "all years" else f", {window} start"
        y_label = f"Research funding on research.fi (million EUR{y_window})"
        try:
            if args.tikz:
                plot_scatter_tikz(points, args.tikz, x_field, x_label, y_label)
                print(
                    f"Wrote {args.tikz} "
                    f"({len(points)} supervisors, {matched} with matched funding)"
                )
            if args.png:
                plot_scatter_png(points, args.png, x_field, x_label, y_label)
                print(f"Wrote {args.png}")
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR building scatter plot: {exc}", file=sys.stderr)
            return 1

    if args.json:
        payload = {"theses_per_faculty": theses, "funding": funding}
        if points is not None:
            payload["supervisors"] = points
        if args.no_people:
            for f in payload["theses_per_faculty"]["faculties"].values():
                f.pop("people", None)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"\nWrote {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
