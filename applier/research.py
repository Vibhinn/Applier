=import json
from datetime import date

from . import gemini, links

PROMPT = """Today is {today}. Research this company using Google Search. I'm a software engineer
preparing a cold email to them, so I need specifics I can verify, not marketing copy.

Company: {name}
Website: {website}
Known so far: {known}

Find:
1. What they actually build, for whom, and how (product, customers, tech stack if visible from
   their engineering blog, GitHub, job posts or talks).
2. RECENT signals (last ~6 months, newest first): posts by founders or employees on LinkedIn or
   X/Twitter, company blog / engineering blog posts, launches, funding announcements, podcast or
   interview quotes. Especially anything revealing a problem they're working on, a challenge
   they're facing, or what they're trying to hire for.
3. People: founders, CTO / engineering leaders, India site leader (for big companies), and
   HR / talent-acquisition people. Include LinkedIn URLs when you find them. If an email
   address is published publicly (website, blog, GitHub, press release), include it.
4. Open engineering roles, and where the team is located (do they hire in India / remote?).
5. The company's website, and its email domain if that differs from the website domain.

Only report things you saw in search results. Say "not found" rather than guessing.
"""

SPEC = """A JSON object:
{
  "summary": str,                       // 2-3 sentences, plain language
  "product": str,
  "customers": str or null,
  "tech_stack": [str],
  "stage_and_size": str or null,        // e.g. "Seed, ~15 people"
  "locations": str or null,
  "hiring_in_india_or_remote": "yes" | "no" | "unknown",
  "open_roles": [str],
  "signals": [{"date": str or null, "who": str or null, "what": str, "url": str or null}],
  "current_challenges": [str],          // inferred from the signals; say which signal
  "people": [{"name": str, "role": str, "linkedin": str or null, "email": str or null}],
  "website": str or null,                // the company's own site
  "email_domain": str or null
}
"""


def research(company) -> dict:
    meta = json.loads(company["meta_json"] or "{}")
    known = {k: v for k, v in {
        "one_liner": company["one_liner"], "source": company["source"],
        "funding": company["funding"], **meta}.items() if v}
    data, sources = gemini.grounded_json(PROMPT.format(
        today=date.today(), name=company["name"], website=company["website"] or "unknown",
        known=json.dumps(known, ensure_ascii=False)), SPEC)
    if not isinstance(data, dict):
        raise ValueError("Research did not return an object")
    data["sources"] = sources
    signals = [sig for sig in data.get("signals") or [] if isinstance(sig, dict)]
    for sig, link_ok in zip(signals, links.check_all([sig.get("url") for sig in signals])):
        if not link_ok:
            sig["url"] = None  # Gemini invented it; the real pages are in data["sources"]
    data["signals"] = signals
    return data
