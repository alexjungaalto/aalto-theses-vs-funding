# LinkedIn draft — thesis supervision vs. research funding

Do the professors who supervise the most master's theses also raise the most research funding?

I spent an evening answering this with nothing but open data. One anonymous dot per supervisor, ~100 of them across two Aalto University departments:

📈 x — lifetime master's theses supervised (from the university thesis repository)
💶 y — total research funding attributed to that person (their share, € million)

The result: **no strong relationship.** Some colleagues have archived 300+ supervised theses on modest funding; some of the best-funded have supervised comparatively few. Supervision load and grant income look like two largely independent things.

But honestly, the more interesting part was getting the data *right*.

Naively matching people by name badly undercounts. The repository stores the same person under many forms — a short given name vs its full spelling, title/affiliation suffixes, spacing quirks, accented vs unaccented letters. So for each supervisor the script discovers every name form on record and merges only the ones that are genuinely the same name — while refusing to merge bare initials or hyphenated compound names, which would silently fold in a *different* person.

How do I know it works? I checked my own number against a list I maintain by hand at ml-theses.org: it shows 126 completed theses; the automated pipeline returns 127. Close enough to trust — and every merge is logged so false ones are visible.

🔗 Full code, methodology and the (anonymous) plot:
github.com/alexjungaalto/aalto-theses-vs-funding

📊 Data sources (all open, queried live):
• Currently-supervised theses — Aalto MyCourses public supervisor list
• Lifetime completed theses — Aaltodoc institutional repository (DSpace)
• Research funding — research.fi, the Finnish national research information hub (Ministry of Education and Culture / CSC)

Everything is anonymised — names are used only to run the queries, never stored or plotted. The point is the distribution, not individuals.

Would you expect supervision and funding to correlate in your field? Curious whether this independence holds elsewhere.

#OpenData #ResearchFunding #HigherEducation #DataViz #AcademicTwitter #research
