import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import claude, config, contacts, db, research


def _parallel(rows, fn, label):
    """Run fn(row) across WORKERS threads; fn returns a dict of column updates incl. 'stage'."""
    if not rows:
        print(f"{label}: nothing to do")
        return
    print(f"{label}: {len(rows)} companies")
    with ThreadPoolExecutor(config.WORKERS) as pool:
        futures = {pool.submit(fn, r): r for r in rows}
        for fut in as_completed(futures):
            row = futures[fut]
            with db.connect() as conn:
                try:
                    updates = fut.result()
                    db.set_stage(conn, row["id"], **updates)
                    print(f"  ✓ {row['name']} -> {updates['stage']}")
                except Exception as e:
                    db.set_stage(conn, row["id"], "error", error=f"{label}: {e}")
                    print(f"  ✗ {row['name']}: {e}")


def run_triage():
    with db.connect() as conn:
        rows = db.by_stage(conn, "discovered")
    print(f"Triage: {len(rows)} companies")
    for i in range(0, len(rows), 40):
        batch = rows[i:i + 40]
        items = [{"id": r["id"], "name": r["name"], "what": r["one_liner"], "category": r["category"],
                  "source": r["source"], "funding": r["funding"],
                  **{k: v for k, v in json.loads(r["meta_json"]).items()
                     if k in ("team_size", "location", "industry", "yc_job_posts") and v is not None}} for r in batch]
        scores = {s.id: s for s in claude.triage(items)}
        with db.connect() as conn:
            for r in batch:
                s = scores.get(r["id"])
                if not s:
                    continue
                stage = "triaged" if s.score >= config.MIN_FIT_SCORE else "rejected"
                db.set_stage(conn, r["id"], stage, fit_score=s.score, fit_reason=s.reason)
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM companies WHERE stage='triaged'").fetchone()[0]
    print(f"  {n} companies waiting for research (fit >= {config.MIN_FIT_SCORE})")


def run_research(limit=None):
    with db.connect() as conn:
        rows = db.by_stage(conn, "triaged", limit, order="fit_score DESC, id")

    def work(r):
        data = research.research(r)
        updates = {"stage": "researched", "research_json": json.dumps(data), "error": None}
        if not r["domain"] and data.get("website"):  # news items often arrive without a website
            updates.update(website=data["website"], domain=db.domain_of(data["website"]))
        return updates
    _parallel(rows, work, "Research")


def run_contacts(limit=None):
    with db.connect() as conn:
        rows = db.by_stage(conn, "researched", limit, order="fit_score DESC, id")

    def work(r):
        found = contacts.find(r, json.loads(r["research_json"]))
        with db.connect() as conn:
            conn.execute("DELETE FROM contacts WHERE company_id = ?", (r["id"],))
            conn.executemany(
                """INSERT OR IGNORE INTO contacts (company_id, name, role, email, email_status,
                   source, linkedin, priority) VALUES (?,?,?,?,?,?,?,?)""",
                [(r["id"], c["name"], c["role"], c["email"], c["email_status"], c["source"],
                  c["linkedin"], c["priority"]) for c in found])
        return {"stage": "contacts"}
    _parallel(rows, work, "Contacts")


def run_compose(limit=None, company_ids=None):
    with db.connect() as conn:
        if company_ids:
            marks = ",".join("?" * len(company_ids))
            rows = conn.execute(f"SELECT * FROM companies WHERE id IN ({marks}) AND research_json IS NOT NULL",
                                company_ids).fetchall()
        else:
            rows = db.by_stage(conn, "contacts", limit, order="fit_score DESC, id")

    def work(r):
        with db.connect() as conn:
            people = conn.execute("SELECT * FROM contacts WHERE company_id = ? ORDER BY priority",
                                  (r["id"],)).fetchall()
        people_payload = [{"index": i, "name": p["name"], "role": p["role"],
                           "email_status": p["email_status"], "has_email": bool(p["email"])}
                          for i, p in enumerate(people)]
        company = {"name": r["name"], "website": r["website"], "category": r["category"],
                   "source": r["source"], "funding": r["funding"], "one_liner": r["one_liner"]}
        research_data = {k: v for k, v in json.loads(r["research_json"]).items() if k != "sources"}
        d = claude.compose(company, research_data, people_payload)
        chosen = people[d.contact_index]["id"] if 0 <= d.contact_index < len(people) else None
        with db.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO drafts (company_id, contact_id, subject, hook, body,
                   recommendation, model, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                (r["id"], chosen, d.subject, d.hook, d.body,
                 json.dumps({**d.recommendation.model_dump(), "hook_basis": d.hook_basis,
                             "caveats": d.caveats}),
                 config.COMPOSE_MODEL, db.now()))
        return {"stage": "composed"}
    _parallel(rows, work, "Compose")


def retry_errors():
    """Send errored companies back to the stage they failed in."""
    back = {"Research": "triaged", "Contacts": "researched", "Compose": "contacts"}
    with db.connect() as conn:
        for r in db.by_stage(conn, "error"):
            label = (r["error"] or "").split(":")[0]
            if label in back:
                db.set_stage(conn, r["id"], back[label], error=None)
