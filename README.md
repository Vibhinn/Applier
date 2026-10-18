# Applier

Finds recently funded startups and new GCCs in India, researches each one, finds who to email,
and drafts one short, specific cold email per company. You review everything on a local
dashboard, copy-paste and send it yourself, and mark it done or skipped.

```
scout ─▶ triage ─▶ research ─▶ contacts ─▶ compose ─▶ dashboard (you)
YC JSON    Haiku     Gemini +     Hunter /     Opus       verify, copy,
+ RSS      ranks     Google       public /     writes     send, mark
+ manual   fit 1-10  Search       guessed      email      done/skip
```

It's a plain Python pipeline, not a multi-agent system. I made each step as one well-defined call, an in-process SQLite (`data/applier.db`) was enough.

## Where companies come from

- **YC**: the community-maintained [yc-oss](https://github.com/yc-oss/api) JSON of the YC
  directory. It takes the two most recent batches.
- **Funding rounds**: RSS from Entrackr, Inc42, YourStory and TechCrunch India. Claude Haiku
  picks the funding announcements out of the headlines.
- **GCCs**: RSS from ET GCC, PuneGCC and GCCLens, plus your own list in
  `data/gcc_manual.csv` (`name,website,note`). GCC news is messy, so add the ones you care
  about by hand.

## Cost (rough)

Discovery is nearly free: the feeds cost nothing and Haiku reads the headlines for a fraction
of a cent. The real cost per drafted email is Gemini research (about 12 grounded Google
searches per company) plus about $0.05-0.10 for the Opus draft. Only companies that pass
triage get researched. Hunter's free plan (50 searches a month) covers about 16 companies.

## Running it 24x7 on a server

On the server, `watch` (discover and rank) runs every 3 hours and `daily` (research and drafts
for the top `DAILY_LIMIT` companies) runs at 1 pm IST. New drafts appear on the dashboard every
day, and its header shows when each job last ran, in red if one failed.
