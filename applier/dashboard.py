import hmac
import json
import re
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, abort, jsonify, render_template, request

from . import config, db

app = Flask(__name__)


@app.before_request
def require_password():
    if not config.DASHBOARD_PASSWORD:
        return None  # local use only; run.py refuses to expose it without a password
    auth = request.authorization
    if auth and hmac.compare_digest(auth.password or "", config.DASHBOARD_PASSWORD):
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Applier"'})


def _signature() -> str:
    path = config.PROFILE_DIR / "signature.txt"
    return path.read_text().strip() if path.exists() else ""


@app.get("/")
def index():
    show = request.args.get("show", "pending")
    with db.connect() as conn:
        where = "" if show == "all" else "AND c.outreach = ?"
        rows = conn.execute(
            f"""SELECT c.*, d.subject, d.hook, d.body, d.recommendation, d.contact_id, d.model,
                       d.created_at AS drafted_at
                FROM companies c JOIN drafts d ON d.company_id = c.id
                WHERE c.stage = 'composed' {where}
                ORDER BY d.created_at DESC, c.fit_score DESC""",
            () if show == "all" else (show,)).fetchall()
        leads = []
        for r in rows:
            people = conn.execute(
                "SELECT * FROM contacts WHERE company_id = ? ORDER BY priority", (r["id"],)).fetchall()
            leads.append({
                **dict(r),
                "research": json.loads(r["research_json"] or "{}"),
                "rec": (rec := json.loads(r["recommendation"] or "{}")),
                "checks": _sentences(rec.get("caveats")),
                "contacts": [dict(p) for p in people],
            })
        counts = dict(conn.execute(
            "SELECT outreach, COUNT(*) FROM companies WHERE stage='composed' GROUP BY outreach").fetchall())
        pipeline = dict(conn.execute("SELECT stage, COUNT(*) FROM companies GROUP BY stage").fetchall())
        runs = {k: conn.execute("SELECT * FROM runs WHERE kind = ? ORDER BY id DESC LIMIT 1", (k,)).fetchone()
                for k in ("watch", "daily")}
    # "New" = drafted in the last 24 hours, i.e. since you last sat down with it.
    new_since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    return render_template("index.html", leads=leads, show=show, counts=counts, pipeline=pipeline,
                           signature=_signature(), runs=runs, new_since=new_since, ago=_ago)


def _sentences(text: str | None) -> list[str]:
    """Caveats arrive as one paragraph; the dashboard shows them as a checklist."""
    return [t.strip() for t in re.split(r"(?<=[.!?])\s+(?=[A-Z'\"])", text or "") if t.strip()]


def _ago(ts: str | None) -> str:
    if not ts:
        return "never"
    mins = int((datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() // 60)
    return f"{mins} min ago" if mins < 60 else f"{mins // 60} h ago" if mins < 48 * 60 else f"{mins // 1440} days ago"


@app.post("/api/lead/<int:cid>")
def update_lead(cid):
    data = request.get_json(force=True)
    with db.connect() as conn:
        if "outreach" in data:
            if data["outreach"] not in ("pending", "done", "skipped"):
                abort(400)
            conn.execute("UPDATE companies SET outreach = ?, updated_at = ? WHERE id = ?",
                         (data["outreach"], db.now(), cid))
        if "notes" in data:
            conn.execute("UPDATE companies SET notes = ? WHERE id = ?", (data["notes"], cid))
        if "body" in data or "subject" in data or "contact_id" in data:
            for col in ("body", "subject", "contact_id"):
                if col in data:
                    conn.execute(f"UPDATE drafts SET {col} = ? WHERE company_id = ?", (data[col], cid))
    return jsonify(ok=True)
