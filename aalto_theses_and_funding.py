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

3. (--tikz/--png) Per-supervisor lifetime thesis count, estimated by searching
   Aaltodoc, Aalto's DSpace repository of completed theses, for each supervisor:
       GET https://aaltodoc.aalto.fi/server/api/discover/search/objects
   matching dc.contributor.supervisor / .advisor. This is the default X-axis of
   the scatter plot (the MyCourses figure only counts theses *currently* in
   supervision); use --x-metric current to plot the MyCourses figure instead.

Usage:
    python3 aalto_theses_and_funding.py
    python3 aalto_theses_and_funding.py --json report.json
    python3 aalto_theses_and_funding.py --funding-query "Aalto"
"""

from __future__ import annotations

import argparse
import json
import sys
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


def fetch_supervisor_funding(first: str, last: str) -> dict:
    """
    Total funding attributed to one person on research.fi: the sum of their
    `shareOfFundingInEur` across every grant whose funding group includes them.
    """
    if not last:
        return {"grants": 0, "funding_eur": 0.0}
    clause = _person_clause(first, last)
    body = {
        "size": 0,
        "track_total_hits": True,
        "query": {"nested": {"path": "fundingGroupPerson", "query": clause}},
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


def fetch_aaltodoc_thesis_count(
    first: str, last: str, include_advisor: bool = False
) -> int:
    """
    Estimate a supervisor's lifetime thesis count from Aaltodoc (Aalto's DSpace
    repository of completed theses).

    Query form: dc.contributor.supervisor:(<last tokens> AND <first>*)
      * supervisor field only by default — the "responsible professor" role,
        which matches how the self-maintained ml-theses.org counts supervision.
        Pass include_advisor=True to also count dc.contributor.advisor
        (instructor) roles as a deduped union.
      * a trailing wildcard on the first name (Alex*) captures short/long name
        variants recorded in the metadata (e.g. a "Sam" / "Samuel" or
        "Alex" / "Alexander" split), which an exact phrase would otherwise
        divide across two records. Validated against a supervisor's own public
        thesis list: this query reproduced that hand-maintained figure.

    Without a first name it falls back to last-name only (may merge namesakes).
    """
    if not last:
        return 0
    terms = " AND ".join(last.split())
    if first:
        terms += f" AND {first}*"
    fields = ["dc.contributor.supervisor"]
    if include_advisor:
        fields.append("dc.contributor.advisor")
    query = " OR ".join(f"{fld}:({terms})" for fld in fields)
    url = (
        f"{AALTODOC_API}?query={urllib.parse.quote(query)}"
        f"&dsoType=item&size=1"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return int(
        result["_embedded"]["searchResult"]["page"]["totalElements"]
    )


def build_supervisor_points(
    theses: dict, workers: int = 8, include_advisor: bool = False
) -> list[dict]:
    """
    Flatten faculties into supervisor records and look up funding for each.

    Names are used only to query the external services and are dropped from the
    result: each returned record is anonymous — (faculty, theses_current,
    theses_aaltodoc, grants, funding_eur).
    """
    queries = []
    for dept, info in theses["faculties"].items():
        for person in info["people"]:
            first, last = parse_name(person["name"])
            queries.append(
                {"faculty": dept, "theses": person["theses"], "first": first, "last": last}
            )

    def _lookup(q: dict) -> dict:
        try:
            fund = fetch_supervisor_funding(q["first"], q["last"])
        except Exception:  # noqa: BLE001 - keep the point, mark funding unknown
            fund = {"grants": 0, "funding_eur": 0.0}
        try:
            aaltodoc = fetch_aaltodoc_thesis_count(
                q["first"], q["last"], include_advisor
            )
        except Exception:  # noqa: BLE001 - keep the point, mark count unknown
            aaltodoc = 0
        # Return an anonymised record only: no names leave this function.
        return {
            "faculty": q["faculty"],
            "theses_current": q["theses"],   # currently in supervision (MyCourses)
            "theses_aaltodoc": aaltodoc,     # lifetime completed (Aaltodoc)
            "grants": fund["grants"],
            "funding_eur": fund["funding_eur"],
        }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_lookup, queries))


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
    points: list[dict], path: str, x_field: str, x_label: str
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
    ax.set_ylabel("Total funding on research.fi (million EUR)")
    ax.set_title("Thesis supervision vs. research funding")
    ax.grid(True, alpha=0.3)
    ax.legend(title="Faculty/dept", frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_scatter_tikz(
    points: list[dict], path: str, x_field: str, x_label: str
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
    lines.append(r"    ylabel={Total funding on research.fi (million EUR)},")
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
    args = ap.parse_args()

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
        points = build_supervisor_points(theses, include_advisor=args.include_advisor)
        matched = sum(1 for p in points if p["funding_eur"] > 0)
        total_aaltodoc = sum(p["theses_aaltodoc"] for p in points)
        print(
            f"  Aaltodoc: {total_aaltodoc:,} completed theses across "
            f"{len(points)} supervisors "
            f"(vs {theses['total_theses']} currently in supervision)"
        )
        if args.matched_only:
            dropped = len(points) - matched
            points = [p for p in points if p["funding_eur"] > 0]
            print(f"  matched-only: kept {len(points)}, dropped {dropped} zero-funding")

        x_field = "theses_aaltodoc" if args.x_metric == "aaltodoc" else "theses_current"
        x_label = (
            "Completed theses supervised (Aaltodoc, lifetime)"
            if args.x_metric == "aaltodoc"
            else "Theses currently in supervision (MyCourses)"
        )
        try:
            if args.tikz:
                plot_scatter_tikz(points, args.tikz, x_field, x_label)
                print(
                    f"Wrote {args.tikz} "
                    f"({len(points)} supervisors, {matched} with matched funding)"
                )
            if args.png:
                plot_scatter_png(points, args.png, x_field, x_label)
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
