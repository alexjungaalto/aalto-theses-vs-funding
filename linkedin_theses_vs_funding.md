# LinkedIn draft — thesis supervision vs. research funding

Do the professors who supervise the most master's theses also raise the most research funding?

I spent an evening answering this with nothing but open data. One anonymous dot per supervisor, ~100 of them across two Aalto University departments:

📈 x — master's theses supervised in 2017–2025 (from the university thesis repository)
💶 y — research funding attributed to that person over the same 2017–2025 window (their share, € million)

The result: **no strong relationship.** Some colleagues have supervised 100+ master's theses over these nine years on modest funding; some of the best-funded have supervised comparatively few. Supervision load and grant income look like two largely independent things.

But honestly, the more interesting part was getting the data *right*.

Naively matching people by name badly undercounts. The repository stores the same person under many forms — a short given name vs its full spelling, title/affiliation suffixes, spacing quirks, accented vs unaccented letters. So for each supervisor the script discovers every name form on record and merges only the ones that are genuinely the same name — while refusing to merge bare initials or hyphenated compound names, which would silently fold in a *different* person.

One subtlety that matters: the repository stores bachelor's theses, master's theses and doctoral dissertations side by side, so I explicitly restrict the count to master's-thesis records — supervised doctoral dissertations are a different thing and don't belong in this number.

I also fixed the window in time — and applied it to *both* axes. The plotted counts are master's theses completed in 2017–2025, and the funding is each person's share of grants that *started* in those same nine years. Comparing recent supervision against a whole career's worth of funding would have been apples-to-oranges; now both sides cover the same period, so a 30-year veteran and a freshly appointed professor are comparable.

How do I know the matching works? I checked my own count against a list I maintain by hand at ml-theses.org. On a lifetime basis it shows 126 master's theses; the automated pipeline returns 123 (dropping four doctoral dissertations that a naïve, level-agnostic count would have wrongly folded in). Close enough to trust — and every name-form merge is logged so false ones are visible.

🔗 Full code, methodology and the (anonymous) plot:
github.com/alexjungaalto/aalto-theses-vs-funding

📊 Data sources (all open, queried live):
• Currently-supervised theses — Aalto MyCourses public supervisor list
• Completed master's theses (2017–2025) — Aaltodoc institutional repository (DSpace)
• Research funding — research.fi, the Finnish national research information hub (Ministry of Education and Culture / CSC)

Everything is anonymised — names are used only to run the queries, never stored or plotted. The point is the distribution, not individuals.

Would you expect supervision and funding to correlate in your field? Curious whether this independence holds elsewhere.

#OpenData #ResearchFunding #HigherEducation #DataViz #AcademicTwitter #research
