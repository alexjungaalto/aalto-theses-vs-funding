# LinkedIn draft — thesis supervision vs. research funding

Do the professors who supervise the most master's theses also raise the most research funding?

I pulled three open data sources and put one dot per supervisor on a single chart:

📈 x-axis — lifetime number of theses supervised, counted from the university's thesis repository (~7,500 across this group)
💶 y-axis — total research funding attributed to that person (their share, in € million)

The answer, for the ~60 supervisors across two Aalto University departments who could be matched to a funding record: there is **no strong relationship**. Teaching-heavy supervisors with 250–380 archived theses sit on modest funding, while several of the best-funded have supervised comparatively few. Supervision volume and grant income look like two largely independent things.

A few honest notes, because the method matters:
• People are matched by name across the sources; supervisors with no matching funding record are left out of this chart rather than plotted as zero.
• The lifetime thesis count comes from full-text repository search on supervisor/advisor fields — a robust estimate, though namesakes and metadata gaps add noise.
• "Funding" is each person's own share of every grant they appear on (no double-counting of consortia), cumulative over roughly 2014–2027 as covered by the funding database.
• All points are anonymised — this is about the distribution, not individuals.

📊 Data sources (all open):
• Currently-supervised theses — Aalto University MyCourses public supervisor list (mycourses.aalto.fi)
• Lifetime completed theses — Aaltodoc, Aalto's institutional repository (aaltodoc.aalto.fi, DSpace)
• Research funding — research.fi, the Finnish national research information hub (granted-funding dataset, Ministry of Education and Culture / CSC)

Plot generated with a small Python script (pulls all three sources live) and rendered in TikZ/pgfplots.

Would you expect a correlation in your field? Curious whether this flatness holds elsewhere.

#OpenData #ResearchFunding #HigherEducation #DataViz #research
