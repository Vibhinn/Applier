import argparse

from applier import config, db, pipeline, scout


def stats():
    with db.connect() as conn:
        rows = conn.execute("SELECT stage, COUNT(*) n FROM companies GROUP BY stage").fetchall()
        out = conn.execute("SELECT outreach, COUNT(*) n FROM companies WHERE stage='composed' "
                           "GROUP BY outreach").fetchall()
    print("Pipeline:", ", ".join(f"{r['stage']}={r['n']}" for r in rows) or "empty")
    print("Outreach:", ", ".join(f"{r['outreach']}={r['n']}" for r in out) or "none yet")


def pipeline_counts() -> str:
    with db.connect() as conn:
        rows = conn.execute("SELECT stage, COUNT(*) n FROM companies GROUP BY stage").fetchall()
    return ", ".join(f"{r['stage']}={r['n']}" for r in rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scout", help="discover companies")
    s.add_argument("--only", nargs="+", choices=["yc", "funded", "gcc"], default=["yc", "funded", "gcc"])
    sub.add_parser("triage", help="score discovered companies against your profile")
    for name in ("research", "contacts", "compose", "all"):
        p = sub.add_parser(name)
        p.add_argument("--limit", type=int, help="max companies to process in this stage")
    sub.choices["compose"].add_argument("--ids", type=int, nargs="+", help="(re)write drafts for these company ids")
    sub.add_parser("watch", help="scheduled: scout + triage (run every few hours)")
    sub.add_parser("daily", help="scheduled: research, contacts and drafts for the top DAILY_LIMIT companies")
    sub.add_parser("retry", help="send errored companies back to the stage they failed in")
    sub.add_parser("stats")
    sub.add_parser("models", help="list Gemini models your key can use")
    d = sub.add_parser("dashboard")
    d.add_argument("--port", type=int, default=5050)
    d.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to serve other machines")
    a = ap.parse_args()

    if a.cmd == "scout":
        scout.run(a.only)
    elif a.cmd == "triage":
        pipeline.run_triage()
    elif a.cmd == "research":
        pipeline.run_research(a.limit)
    elif a.cmd == "contacts":
        pipeline.run_contacts(a.limit)
    elif a.cmd == "compose":
        pipeline.run_compose(a.limit, a.ids)
    elif a.cmd == "all":
        scout.run()
        pipeline.run_triage()
        pipeline.run_research(a.limit)
        pipeline.run_contacts(a.limit)
        pipeline.run_compose(a.limit)
    elif a.cmd == "watch":
        with db.logged_run("watch") as run:
            scout.run()
            pipeline.run_triage()
            run["summary"] = pipeline_counts()
    elif a.cmd == "daily":
        with db.logged_run("daily") as run:
            pipeline.retry_errors()
            pipeline.run_research(config.DAILY_LIMIT)
            pipeline.run_contacts(config.DAILY_LIMIT)
            pipeline.run_compose(config.DAILY_LIMIT)
            run["summary"] = pipeline_counts()
    elif a.cmd == "retry":
        pipeline.retry_errors()
    elif a.cmd == "models":
        from applier import gemini
        print("\n".join(sorted(gemini.list_models())))
        print("\nauto-pick:", gemini.model())
        return
    elif a.cmd == "dashboard":
        from applier.dashboard import app
        if a.host != "127.0.0.1" and not config.DASHBOARD_PASSWORD:
            raise SystemExit("Set DASHBOARD_PASSWORD in .env before exposing the dashboard.")
        from waitress import serve
        print(f"Dashboard: http://{a.host}:{a.port}")
        serve(app, host=a.host, port=a.port)
        return
    stats()


if __name__ == "__main__":
    main()
