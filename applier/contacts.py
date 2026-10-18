import re

import requests

from . import config

HUNTER = "https://api.hunter.io/v2"

ROLE_PRIORITY = [  # (regex on role, priority) — lower is a better target
    (r"\b(co-?founder|founder|ceo)\b", 10),
    (r"\b(cto|chief technology|vp eng|head of eng|engineering (manager|director|lead))", 15),
    (r"\b(site lead|country head|india head|center head|gcc head|managing director)", 20),
    (r"\b(talent|recruit|hr|people|hiring)", 25),
    (r"\b(engineer|developer|architect)", 40),
]


def role_priority(role: str | None) -> int:
    for pattern, prio in ROLE_PRIORITY:
        if role and re.search(pattern, role, re.I):
            return prio
    return 60


def _hunter(path: str, **params):
    if not config.HUNTER_API_KEY:
        return None
    r = requests.get(f"{HUNTER}/{path}", params={**params, "api_key": config.HUNTER_API_KEY},
                     timeout=30)
    if r.status_code != 200:
        return None
    return r.json().get("data")


def _split(name: str) -> tuple[str, str]:
    parts = re.sub(r"[^a-zA-Z\s-]", "", name).lower().split()
    return (parts[0], parts[-1]) if len(parts) >= 2 else ((parts or [""])[0], "")


def _apply_pattern(pattern: str, name: str, domain: str) -> str | None:
    first, last = _split(name)
    if not first:
        return None
    local = (pattern.replace("{first}", first).replace("{last}", last)
             .replace("{f}", first[:1]).replace("{l}", last[:1]))
    return f"{local}@{domain}" if "{" not in local and local else None


def find(company, research: dict) -> list[dict]:
    domain = research.get("email_domain") or company["domain"]
    if not domain:
        return []
    people = [p for p in research.get("people") or [] if p.get("name")]
    found: dict[str, dict] = {}

    def add(email, name, role, status, source, linkedin=None):
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            return
        current = found.get(email)
        if current and current["email_status"] == "verified":
            if not current["name"] and name:  # e.g. founders@ inbox: greet the first founder by name
                current.update(name=name, role=role, priority=role_priority(role))
            return
        found[email] = dict(email=email, name=name, role=role, email_status=status,
                            source=source, linkedin=linkedin or (current or {}).get("linkedin"),
                            priority=role_priority(role))

    pattern = None
    data = _hunter("domain-search", domain=domain, limit=10)
    if data:
        pattern = data.get("pattern")
        for e in data.get("emails") or []:
            name = " ".join(filter(None, [e.get("first_name"), e.get("last_name")])) or None
            status = "verified" if (e.get("verification") or {}).get("status") == "valid" else "likely"
            add(e.get("value"), name, e.get("position"), status, "hunter", e.get("linkedin"))

    people.sort(key=lambda p: role_priority(p.get("role")))
    finder_budget = 2
    for p in people[:6]:
        if any((c["name"] or "").lower() == p["name"].lower() for c in found.values()):
            continue  # Hunter's domain search already had them
        if p.get("email"):
            add(p["email"], p["name"], p.get("role"), "likely", "public (research)", p.get("linkedin"))
            continue
        first, last = _split(p["name"])
        hit = None
        if last and finder_budget:
            finder_budget -= 1
            hit = _hunter("email-finder", domain=domain, first_name=first, last_name=last)
        if hit and hit.get("email"):
            status = "verified" if (hit.get("verification") or {}).get("status") == "valid" else "likely"
            add(hit["email"], p["name"], p.get("role"), status, "hunter finder", p.get("linkedin"))
            continue
        guess = _apply_pattern(pattern or "{first}", p["name"], domain)
        add(guess, p["name"], p.get("role"), "guessed",
            f"pattern {pattern or '{first}'}@", p.get("linkedin"))

    for c in sorted(found.values(), key=lambda c: c["priority"])[:3]:
        if c["email_status"] == "guessed":
            v = _hunter("email-verifier", email=c["email"])
            if v and v.get("status") == "valid":
                c["email_status"], c["source"] = "verified", c["source"] + " + verified"

    for p in people:
        if not any(c["name"] == p["name"] for c in found.values()) and p.get("linkedin"):
            found[f"li:{p['linkedin']}"] = dict(email=None, name=p["name"], role=p.get("role"),
                                                 email_status="none", source="research",
                                                 linkedin=p["linkedin"], priority=role_priority(p.get("role")) + 100)
    return sorted(found.values(), key=lambda c: (c["priority"], c["email_status"] != "verified"))
