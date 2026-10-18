import json
import threading
from datetime import date
from functools import lru_cache

import anthropic
from pydantic import BaseModel, Field

from . import config


_lock = threading.Lock()
_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    with _lock:
        if _client is None:
            if not config.ANTHROPIC_API_KEY:
                raise SystemExit("ANTHROPIC_API_KEY is not set in .env")
            _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        return _client


def _read(name: str) -> str:
    path = config.PROFILE_DIR / name
    return path.read_text().strip() if path.exists() else ""


@lru_cache(maxsize=1)
def profile() -> dict:
    p = {"me": _read("me.md"), "resume": _read("resume.md"), "style": _read("email_style.md")}
    if "TODO" in p["resume"] or not p["resume"]:
        raise SystemExit("Fill in profile/resume.md (and profile/me.md, profile/email_style.md) first.")
    return p


# ---------------------------------------------------------------- triage

class Score(BaseModel):
    id: int
    score: int = Field(description="1-10: how strong a fit this company is for the candidate")
    reason: str = Field(description="One short sentence")


class Scores(BaseModel):
    scores: list[Score]


def triage(companies: list[dict]) -> list[Score]:
    p = profile()
    resp = client().messages.parse(
        model=config.TRIAGE_MODEL,
        max_tokens=8000,
        system=(
            "You screen companies for a job seeker doing cold outreach. Score each company 1-10 "
            "on how likely the candidate is a credible hire there AND the company is likely to "
            "hire someone at their level soon. 8+ = clear match with their skills; 5 = plausible; "
            "<=3 = wrong domain or level. Be calibrated, not generous. Write each reason to the "
            "candidate as 'you'.\n\n"
            f"## Candidate\n{p['me']}\n\n## Resume\n{p['resume']}"
        ),
        messages=[{"role": "user", "content": json.dumps(companies, ensure_ascii=False)}],
        output_format=Scores,
    )
    return resp.parsed_output.scores if resp.parsed_output else []


# ---------------------------------------------------------------- news extraction

class Announcement(BaseModel):
    index: int = Field(description="The 'i' of the headline this came from")
    company: str = Field(description="The company that raised money / is opening the center")
    website: str | None = Field(description="Only if stated or certain; else null")
    one_liner: str = Field(description="What the company does (<= 20 words); for GCCs include city and headcount if given")
    details: str | None = Field(description="Funding: round, amount, lead investors")
    location: str | None


class Announcements(BaseModel):
    items: list[Announcement]


EXTRACT_RULES = {
    "funded": "Keep only headlines announcing that a startup raised a funding round (any stage). Skip "
              "results, layoffs, IPO news, acquisitions, fund launches by VCs, and roundups that name no company.",
    "gcc": "Keep only headlines announcing a company opening, launching or expanding a global capability "
           "center, technology / engineering / innovation center or hub in India. Skip opinion pieces, "
           "reports, appointments without a new center, and certifications.",
}


def extract_announcements(items: list[dict], category: str) -> list[Announcement]:
    """Turn RSS headlines into structured announcements. One cheap call per 60 headlines."""
    out = []
    for i in range(0, len(items), 60):
        resp = client().messages.parse(
            model=config.TRIAGE_MODEL,
            max_tokens=8000,
            system=f"You extract company announcements from news headlines. {EXTRACT_RULES[category]} "
                   "Use only what the headline and summary say.",
            messages=[{"role": "user", "content": json.dumps(items[i:i + 60], ensure_ascii=False)}],
            output_format=Announcements,
        )
        out += resp.parsed_output.items if resp.parsed_output else []
    return out


# ---------------------------------------------------------------- compose

class Recommendation(BaseModel):
    title: str = Field(description="A concrete thing to build or learn, e.g. 'Build a mini X using Y'")
    why: str = Field(description="Why this would impress this specific team's hiring manager")
    steps: list[str] = Field(description="3-5 concrete steps")
    time_needed: str = Field(description="Realistic estimate, e.g. '1 weekend', '4-5 evenings'")


class Draft(BaseModel):
    contact_index: int = Field(description="Index into the contacts list of the best person to email")
    hook: str = Field(description="The single opening line that makes them keep reading")
    hook_basis: str = Field(description="The specific fact/post/source the hook is based on")
    subject: str
    body: str = Field(description="Full email. Greet with {first_name}. No signature block.")
    recommendation: Recommendation
    caveats: str = Field(description="Anything the candidate should double-check before sending; empty if none")


COMPOSE_INSTRUCTIONS = """You write cold job-outreach emails on behalf of the candidate below.

How a good email from this candidate works:
- The FIRST line is the hook. It must be about *them*, specific and verifiable: a problem the
  team is working on right now, a post the founder wrote, a launch, a hiring push, a technical
  choice — taken from the research you're given — tied in the same breath to something the
  candidate has actually done. No flattery, no "I came across", no "I hope this finds you well".
- Then: who the candidate is, the two or three resume proofs most relevant to *this* team's
  problem (with the real numbers), and a low-friction ask (a quick chat, or which role to
  apply for). Mention that the resume is attached.
- Write in the candidate's own voice, learned from their sample emails: conversational,
  confident, self-aware and funny, with short paragraphs, playful asides and a light,
  human close. Bold claims are fine when a concrete number backs them up. Use fresh jokes
  rather than reusing the samples' exact lines (James Bond, the hedgehog, the deliberate
  typo); each recipient should get an email that feels written for them. Keep the humour
  subordinate to substance: the hook and proofs must land even if the jokes don't.
  Scale the playfulness to the reader: full voice for startup founders and engineers, a
  noticeably more measured version for HR at large companies and GCCs (no alcohol jokes there).
- 120-200 words. Plain text, no markdown, no bullet points, no emojis.
- Never invent facts about the candidate or the company. If the research has no real recent
  signal, build the hook from what the company does and say so in `caveats`.
- Greet with "Hi {first_name}," literally (the placeholder is filled in later). Do not sign off
  with a name or links; the candidate's signature is added separately.

Pick the contact: for small startups prefer a founder/CTO, then an engineering lead, then
HR/talent. For large companies and GCCs prefer the India engineering/site leader or the
talent-acquisition lead for tech hiring. Prefer verified emails when the people are otherwise
equal.

Write `caveats` and `recommendation` to the candidate directly, as "you".

Also recommend ONE small project or learning the candidate could do in about a week that maps
directly onto this company's tech or problem and would give them something to show the hiring
manager. Make it specific to this company, not generic advice."""


@lru_cache(maxsize=1)
def _compose_system() -> list[dict]:
    p = profile()
    text = (
        f"{COMPOSE_INSTRUCTIONS}\n\n## The candidate\n{p['me']}\n\n## Resume\n{p['resume']}\n\n"
        f"## Sample emails the candidate has written (match this voice)\n{p['style'] or '(none given)'}"
    )
    # Identical for every company, so it's cached across the whole run.
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def compose(company: dict, research: dict, contacts: list[dict]) -> Draft:
    payload = {"today": date.today().isoformat(), "company": company, "research": research,
               "contacts": contacts}
    resp = client().beta.messages.parse(
        model=config.COMPOSE_MODEL,
        max_tokens=16000,
        # If a safety classifier ever declines, the API reruns on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high"},
        system=_compose_system(),
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}],
        output_format=Draft,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"Claude returned no draft (stop_reason={resp.stop_reason})")
    return resp.parsed_output
