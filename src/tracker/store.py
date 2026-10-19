"""D1 access. The SDK's binding wrapper converts arguments and results between Python and
JavaScript, so queries take Python values and return dicts.
"""

from tracker.quotas import Changes, Quota


def _rows(result) -> list[dict]:
    return list(result.get("results") or [])


async def insert_run(db, run_id: str, trigger: str, started_at: str, pages: int) -> None:
    await db.prepare("INSERT INTO runs (id, trigger, started_at, pages) VALUES (?, ?, ?, ?)").bind(
        run_id, trigger, started_at, pages
    ).run()


async def insert_check(db, *, run_id, provider, page_id, checked_at, hash=None, changed=False, quotas=0, changes=0, error=None) -> None:
    await db.prepare(
        "INSERT INTO checks (run_id, provider, page_id, checked_at, hash, changed, quotas, changes, error)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ).bind(run_id, provider, page_id, checked_at, hash, 1 if changed else 0, quotas, changes, error).run()


async def save_snapshot(db, page_id: str, hash: str, fetched_at: str, content: str) -> None:
    await db.prepare(
        "INSERT OR IGNORE INTO snapshots (page_id, hash, fetched_at, content) VALUES (?, ?, ?, ?)"
    ).bind(page_id, hash, fetched_at, content).run()


async def active_quotas(db, provider: str, page_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM quotas WHERE provider = ? AND active = 1"
    args = [provider]
    if page_id:
        sql += " AND page_id = ?"
        args.append(page_id)
    return _rows(await db.prepare(sql + " ORDER BY service, metric").bind(*args).all())


def row_to_quota(row: dict) -> Quota:
    return Quota(row["service"], row["metric"], row["limit_value"], row["period"], row.get("notes") or "")


async def apply_extraction(db, *, run_id, provider, page_id, quotas: list[Quota], changes: Changes, now: str) -> None:
    """Record one page's new extraction as a single D1 batch, so it lands atomically.

    Unchanged quotas get their `last_seen` bumped. Changed and removed ones are closed
    (active = 0) and changed and added ones open a new row, which keeps history in the
    table itself.
    """
    stmts = []
    closing = [old.key for old, _ in changes.changed] + [q.key for q in changes.removed]
    for key in closing:
        stmts.append(
            db.prepare("UPDATE quotas SET active = 0 WHERE provider = ? AND page_id = ? AND key = ? AND active = 1").bind(provider, page_id, key)
        )
    opening = [new for _, new in changes.changed] + changes.added
    for q in opening:
        stmts.append(
            db.prepare(
                "INSERT INTO quotas (provider, page_id, key, service, metric, limit_value, period, notes, first_seen, last_seen, active)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)"
            ).bind(provider, page_id, q.key, q.service, q.metric, q.limit, q.period, q.notes, now, now)
        )
    opened = {q.key for q in opening}
    for q in quotas:
        if q.key not in opened:
            stmts.append(
                db.prepare("UPDATE quotas SET last_seen = ?, notes = ? WHERE provider = ? AND page_id = ? AND key = ? AND active = 1").bind(
                    now, q.notes, provider, page_id, q.key
                )
            )
    for row in changes.rows():
        stmts.append(
            db.prepare(
                "INSERT INTO changes (run_id, provider, page_id, kind, service, metric, old_value, new_value, detected_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ).bind(run_id, provider, page_id, row["kind"], row["service"], row["metric"], row["old"], row["new"], now)
        )
    if stmts:
        await db.batch(stmts)


async def recent_changes(db, limit: int = 50) -> list[dict]:
    return _rows(await db.prepare("SELECT * FROM changes ORDER BY detected_at DESC, id DESC LIMIT ?").bind(limit).all())


async def changes_for_run(db, run_id: str, provider: str) -> list[dict]:
    return _rows(await db.prepare("SELECT * FROM changes WHERE run_id = ? AND provider = ? ORDER BY page_id, id").bind(run_id, provider).all())


async def recent_runs(db, limit: int = 10) -> list[dict]:
    runs = _rows(await db.prepare("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?").bind(limit).all())
    for run in runs:
        run["checks"] = _rows(
            await db.prepare("SELECT page_id, checked_at, changed, quotas, changes, error FROM checks WHERE run_id = ? ORDER BY page_id").bind(run["id"]).all()
        )
    return runs


async def last_checks(db) -> dict[str, dict]:
    rows = _rows(
        await db.prepare(
            "SELECT c.* FROM checks c JOIN (SELECT page_id, MAX(id) AS id FROM checks GROUP BY page_id) last ON c.id = last.id"
        ).all()
    )
    return {r["page_id"]: r for r in rows}
