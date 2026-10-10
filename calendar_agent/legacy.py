"""One-way, idempotent import of MMM history. Source SQLite stays read-only.

Legacy tasks have no reliable deadline/estimate; never invent calendar events.
They are visible in the same workspace and can be linked after clarification.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from .core.time import resolve_local
from .models import LegacyTask

TIME_KEYS = {"created_at", "updated_at", "last_accessed", "completed_at", "archived_at", "changed_at", "last_seen"}


def normalize(value):
    if isinstance(value, list): return [normalize(x) for x in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in TIME_KEYS and item:
                parsed = datetime.fromisoformat(item)
                converted = parsed.astimezone(timezone.utc) if parsed.tzinfo else resolve_local(item)
                result[key] = converted.isoformat().replace("+00:00", "Z")
            else: result[key] = normalize(item)
        return result
    return value


def import_legacy(factory, source: Path):
    if not source.exists(): return 0
    source = source.resolve()
    conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "task_sessions" not in tables: return 0
        snapshots = []
        for row in conn.execute("SELECT * FROM task_sessions ORDER BY created_at"):
            task = dict(row)
            task.pop("embedding", None)
            task["invoked_skills"] = json.loads(task["invoked_skills"] or "[]")
            task["subtasks"] = [dict(r) for r in conn.execute("SELECT * FROM subtasks WHERE task_id=? ORDER BY seq", (row["id"],))]
            task["conversation_history"] = [dict(r) for r in conn.execute("SELECT role,content,created_at FROM messages WHERE task_id=? ORDER BY id", (row["id"],))]
            task["events"] = [dict(r) for r in conn.execute("SELECT * FROM subtask_events WHERE task_id=? ORDER BY changed_at", (row["id"],))]
            task["patterns"] = []
            if "semantic_memory" in tables:
                for pattern_row in conn.execute("SELECT * FROM semantic_memory"):
                    pattern = dict(pattern_row)
                    if row["id"] in json.loads(pattern["evidence_session_ids"]):
                        pattern.pop("embedding", None)
                        pattern["evidence_session_ids"] = json.loads(pattern["evidence_session_ids"])
                        task["patterns"].append(pattern)
            task["source_time_assumption"] = "Legacy naive timestamps interpreted as Australia/Melbourne; original database retained."
            snapshots.append(normalize(task))
        with factory() as db:
            existing = set(db.scalars(select(LegacyTask.source_id)))
            count = 0
            for task in snapshots:
                if task["id"] not in existing:
                    db.add(LegacyTask(source_id=task["id"], snapshot_json=task)); count += 1
            db.commit()
            return count
    finally:
        conn.close()
