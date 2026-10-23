"""SQLite-only activity rules shared by live draining and cold migration."""


def activity_counts(db) -> dict:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "missions" not in tables:
        raise ValueError("Cannot verify idle state of an unknown database")
    phase = "'unknown'"
    if "events" in tables:
        phase = """coalesce((SELECT json_extract(e.payload, '$.phase') FROM events e
            WHERE e.mission_id=missions.id AND e.event_type='discussion.v2.state'
            ORDER BY e.id DESC LIMIT 1), 'unknown')"""
    # Ordinary blocked tasks have stopped. A paused V2 discussion still owns its
    # conversation until an ended event is present, regardless of mission status.
    missions = db.execute(f"""SELECT count(*) FROM missions WHERE
        coalesce(json_extract(data, '$.status'), 'unknown') NOT IN
            ('completed','failed','completed_with_warnings','blocked')
        OR (json_extract(data, '$.discussion.protocol')='deliberation_v2'
            AND {phase}!='ended')""").fetchone()[0]
    maintenance = 0
    if "maintenance_runs" in tables:
        maintenance = db.execute("""SELECT count(*) FROM maintenance_runs WHERE
            coalesce(json_extract(data, '$.phase'), 'unknown') NOT IN
                ('completed','blocked','failed','cancelled')""").fetchone()[0]
    grants = 0
    if "execution_grants" in tables:
        grants = db.execute("SELECT count(*) FROM execution_grants WHERE state='active'").fetchone()[0]
    return {"active_missions": missions, "active_maintenance": maintenance, "active_grants": grants}
