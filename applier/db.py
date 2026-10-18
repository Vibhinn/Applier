import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id            INTEGER PRIMARY KEY,
    key           TEXT UNIQUE NOT NULL,      -- normalised domain, or name if no domain
    name          TEXT NOT NULL,
    website       TEXT,
    domain        TEXT,
    category      TEXT,                      -- yc | funded | gcc
    one_liner     TEXT,
    source        TEXT,                      -- e.g. "YC Summer 2026", "Inc42"
    source_url    TEXT,
    funding       TEXT,
    meta_json     TEXT DEFAULT '{}',
    fit_score     INTEGER,
    fit_reason    TEXT,
    research_json TEXT,
    stage         TEXT NOT NULL DEFAULT 'discovered',
    error         TEXT,
    outreach      TEXT NOT NULL DEFAULT 'pending',
    notes         TEXT DEFAULT '',
    discovered_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id           INTEGER PRIMARY KEY,
    company_id   INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name         TEXT,
    role         TEXT,
    email        TEXT,
    email_status TEXT,                       -- verified | likely | guessed | none
    source       TEXT,
    linkedin     TEXT,
    priority     INTEGER DEFAULT 50,         -- lower = better target
    UNIQUE(company_id, email)
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,               -- scout | daily
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER,
    summary     TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
    company_id     INTEGER PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    contact_id     INTEGER,
    subject        TEXT,
    hook           TEXT,
    body           TEXT,                     -- may contain {first_name}
    recommendation TEXT,                     -- JSON
    model          TEXT,
    created_at     TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # The scheduler and the dashboard use the database at the same time: WAL + a busy timeout.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    d = re.sub(r"^https?://", "", url.strip().lower()).split("/")[0].split("?")[0]
    d = d.removeprefix("www.")
    return d or None


def company_key(name: str, website: str | None) -> str:
    return domain_of(website) or re.sub(r"[^a-z0-9]", "", name.lower())


def upsert_company(conn, *, name, website=None, category, one_liner=None, source=None,
                   source_url=None, funding=None, meta=None) -> bool:
    """Insert if new. Returns True when a new company was added."""
    key = company_key(name, website)
    if not key:
        return False
    # The same company sometimes comes back with a slightly different website or a suffix
    # like "Lumio (Circuit House Technologies)".
    base_name = re.sub(r"\s*\(.*?\)", "", name).strip().lower()
    if conn.execute("SELECT 1 FROM companies WHERE lower(name) = ? OR lower(name) LIKE ? || ' (%'",
                    (base_name, base_name)).fetchone():
        return False
    cur = conn.execute(
        """INSERT OR IGNORE INTO companies
           (key, name, website, domain, category, one_liner, source, source_url, funding,
            meta_json, discovered_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (key, name, website, domain_of(website), category, one_liner, source, source_url,
         funding, json.dumps(meta or {}), now(), now()),
    )
    return cur.rowcount == 1


def set_stage(conn, company_id: int, stage: str, **fields):
    fields["stage"] = stage
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE companies SET {cols} WHERE id = ?", (*fields.values(), company_id))


def by_stage(conn, stage: str, limit: int | None = None, order: str = "id"):
    sql = f"SELECT * FROM companies WHERE stage = ? ORDER BY {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (stage,)).fetchall()


@contextmanager
def logged_run(kind: str):
    """Record a scheduled run so the dashboard can show when it last ran and whether it failed."""
    with connect() as conn:
        run_id = conn.execute("INSERT INTO runs (kind, started_at) VALUES (?, ?)", (kind, now())).lastrowid
    result = {"summary": ""}
    try:
        yield result
    except BaseException as e:
        with connect() as conn:
            conn.execute("UPDATE runs SET finished_at = ?, ok = 0, summary = ? WHERE id = ?",
                         (now(), f"{type(e).__name__}: {e}", run_id))
        raise
    with connect() as conn:
        conn.execute("UPDATE runs SET finished_at = ?, ok = 1, summary = ? WHERE id = ?",
                     (now(), result["summary"], run_id))
