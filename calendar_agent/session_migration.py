"""Offline, additive P0 migration rehearsal. Never switches the live write source.

python -m calendar_agent.session_migration SOURCE_DB NEW_REHEARSAL_DB
Run only with the old application stopped before an eventual P1 cutover.
"""
import argparse
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, text
from .models import (CalendarSession, SessionExecution, SessionMigration, Topic,
                     Subtask, Task, TaskMessage, TaskNote, WorkSession, utc_now)


def snapshot_database(source: Path, destination: Path) -> Path:
    """SQLite online backup includes committed WAL; refuse to overwrite anything."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("数据库备份完整性检查失败")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def migrate_sessions(factory, clock=utc_now):
    """One atomic, versioned import; old tables remain the source archive.

    Calendar's legacy columns already use UTCDateTime. Malformed/naive timestamps
    fail the transaction instead of interpreting UTC as Melbourne a second time.
    """
    with factory() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        existing = db.get(SessionMigration, 1)
        if existing:
            return existing.report_json
        now = clock()
        report = {"topics": 0, "sessions": 0, "executions": 0,
                  "unscheduled_subtask_ids": [], "review": []}
        topics = {}
        for task in db.scalars(select(Task).order_by(Task.id)).all():
            history = {"task_id": task.id, "status": task.status,
                       "deadline_utc": task.deadline_utc.isoformat(),
                       "estimated_minutes": task.estimated_minutes,
                       "messages": [], "notes": []}
            for key, model in (("messages", TaskMessage), ("notes", TaskNote)):
                for row in db.scalars(select(model).where(model.task_id == task.id).order_by(model.id)):
                    evidence = {"source_type": model.__tablename__, "source_id": row.id,
                                "content": row.content, "created_at": row.created_at.isoformat()}
                    if key == "messages":
                        evidence["role"] = row.role
                    history[key].append(evidence)
            topic = Topic(title=task.title, source_type="tasks", source_id=str(task.id), history_json=history)
            db.add(topic)
            db.flush()
            topics[task.id] = (topic.id, task.status)
            report["topics"] += 1
        for step in db.scalars(select(Subtask).order_by(Subtask.id)).all():
            if step.slot_start_utc is None:
                report["unscheduled_subtask_ids"].append(step.id)
                continue
            end = step.slot_start_utc + timedelta(minutes=step.duration_min)
            topic_id, task_status = topics[step.task_id]
            executions = db.scalars(select(WorkSession).where(WorkSession.subtask_id == step.id).order_by(WorkSession.id)).all()
            cancelled = step.status == "skipped" or task_status == "cancelled"
            closed = step.status == "completed" or end <= now
            outcome = "completed" if step.status == "completed" else "unknown" if closed else None
            if closed and step.status != "completed" and executions:
                latest = executions[-1]
                if latest.ended_at and latest.outcome in {"partial", "blocked", "unknown"}:
                    outcome = latest.outcome
            row = CalendarSession(
                title=step.title, topic_id=topic_id, start_at=step.slot_start_utc, end_at=end,
                status="cancelled" if cancelled else "closed" if closed else "scheduled",
                outcome=None if cancelled else outcome,
                # Migration observation time isn't an inferred actual finish time.
                closed_at=None, close_reason="migration" if cancelled or closed else None,
                created_at=now, updated_at=now, source_type="subtasks", source_id=str(step.id),
                migration_metadata={"status": step.status, "depends_on": step.depends_on,
                                    "actual_minutes": step.actual_minutes, "observed_at": now.isoformat()})
            db.add(row)
            db.flush()
            report["sessions"] += 1
            for execution in executions:
                flags = []
                if execution.started_at < row.start_at or execution.started_at >= row.end_at:
                    flags.append("start_outside_plan")
                if execution.ended_at and (execution.ended_at > row.end_at or execution.ended_at < execution.started_at):
                    flags.append("end_outside_plan")
                if execution.ended_at is None:
                    flags.append("legacy_open_execution")
                if flags:
                    report["review"].append({"source_type": "sessions", "source_id": execution.id, "flags": flags})
                db.add(SessionExecution(calendar_session_id=row.id, started_at=execution.started_at,
                    ended_at=execution.ended_at, actual_minutes=execution.actual_minutes,
                    outcome=execution.outcome, note=execution.note, source_type="sessions",
                    source_id=str(execution.id), migration_metadata={"flags": flags}))
                report["executions"] += 1
        db.add(SessionMigration(version=1, applied_at=now, report_json=report))
        db.flush()
        if db.execute(text("PRAGMA foreign_key_check")).first():
            raise ValueError("迁移外键校验失败")
        db.commit()
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, help="必须是不存在的新文件；不会切换活动库")
    args = parser.parse_args()
    snapshot_database(args.source, args.destination)
    from .database import create_database
    engine, factory = create_database(args.destination)
    try:
        import json
        print(json.dumps(migrate_dates(factory), ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


def migrate_dates(factory, clock=utc_now):
    """Version 2 preserves v1's report and original tables, then activates dates."""
    from zoneinfo import ZoneInfo
    from .config import DISPLAY_TIMEZONE
    from .models import LifecycleEvent, AssistantMessage, SessionMessage, PlanningConversation, LegacyTask
    with factory() as db:
        if db.get(SessionMigration, 2): return db.get(SessionMigration, 2).report_json
    migrate_sessions(factory, clock)
    with factory() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        if db.get(SessionMigration, 2): return db.get(SessionMigration, 2).report_json
        now = clock()
        report = {'sessions': 0, 'messages': 0, 'deadline_review': [], 'version': 2}
        for row in db.scalars(select(CalendarSession)):
            original = dict(row.migration_metadata)
            original['v1_status'] = row.status
            original['legacy_start_at'] = row.start_at.isoformat() if row.start_at else None
            original['legacy_end_at'] = row.end_at.isoformat() if row.end_at else None
            row.planned_date = row.start_at.astimezone(ZoneInfo(DISPLAY_TIMEZONE)).date() if row.start_at else None
            step = db.get(Subtask, int(row.source_id)) if row.source_type == 'subtasks' else None
            completed = step.status == 'completed' if step else row.outcome == 'completed'
            row.archived_at = now if row.status == 'cancelled' else None
            row.status = 'completed' if completed else 'open'
            # Old planners often invented this deadline. Retain for review only.
            topic = db.get(Topic, row.topic_id) if row.topic_id else None
            if topic and topic.history_json.get('deadline_utc'):
                original['unconfirmed_deadline_utc'] = topic.history_json['deadline_utc']
                report['deadline_review'].append(row.id)
            row.migration_metadata = original
            db.add(LifecycleEvent(session_id=row.id, kind='migration_v2', detail={'previous_status': original['v1_status'], 'deadline_requires_review': row.id in report['deadline_review']}, created_at=now))
            report['sessions'] += 1
        for msg in db.scalars(select(SessionMessage).order_by(SessionMessage.id)):
            db.add(AssistantMessage(session_id=msg.calendar_session_id, conversation_id=f'session:{msg.calendar_session_id}', role=msg.role, content=msg.content, created_at=msg.created_at))
            report['messages'] += 1
        for topic in db.scalars(select(Topic)):
            for msg in topic.history_json.get('messages', []) + topic.history_json.get('notes', []):
                from datetime import datetime
                db.add(AssistantMessage(conversation_id=f'topic:{topic.id}', role=msg.get('role', 'user'), content=msg['content'], created_at=datetime.fromisoformat(msg['created_at'])))
                report['messages'] += 1
        for legacy in db.scalars(select(LegacyTask)):
            snapshot = legacy.snapshot_json
            topic = Topic(title=snapshot.get('title', '旧 MMM 任务'), source_type='legacy_tasks', source_id=legacy.source_id, history_json=snapshot)
            db.add(topic); db.flush()
            for msg in snapshot.get('conversation_history', []):
                from datetime import datetime
                db.add(AssistantMessage(conversation_id=f'topic:{topic.id}', role=msg.get('role', 'user'), content=msg['content'], created_at=datetime.fromisoformat(msg['created_at'])))
                report['messages'] += 1
        for conv in db.scalars(select(PlanningConversation)):
            for index, msg in enumerate(conv.messages_json):
                if msg.get('role') in ('user', 'assistant') and msg.get('content'):
                    from datetime import datetime
                    stamp = datetime.fromisoformat(msg['created_at']) if msg.get('created_at') else conv.updated_at
                    db.add(AssistantMessage(conversation_id='home', role=msg['role'], content=msg['content'], created_at=stamp, origin_key=f'planning:{conv.id}:{index}'))
                    report['messages'] += 1
        db.add(SessionMigration(version=2, applied_at=now, report_json=report))
        db.flush()
        if db.execute(text('PRAGMA foreign_key_check')).first(): raise ValueError('迁移外键检查失败')
        db.commit()
        return report


def repair_message_origins(factory, clock=utc_now):
    """Add provenance to early v2 imports; keep message IDs and earlier reports."""
    from datetime import datetime
    from .models import AssistantMessage, PlanningConversation, MemoryItem, MemoryJob, HomeReview
    with factory() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        if db.get(SessionMigration, 3): return db.get(SessionMigration, 3).report_json
        repaired = 0
        repaired_ids = set()
        for conv in db.scalars(select(PlanningConversation)):
            for index, msg in enumerate(conv.messages_json):
                key = f'planning:{conv.id}:{index}'
                if db.scalar(select(AssistantMessage).where(AssistantMessage.origin_key == key)): continue
                row = db.scalar(select(AssistantMessage).where(AssistantMessage.origin_key.is_(None), AssistantMessage.conversation_id == 'home', AssistantMessage.role == msg.get('role'), AssistantMessage.content == msg.get('content')).order_by(AssistantMessage.id))
                if not row: continue
                row.origin_key = key
                if msg.get('created_at'): row.created_at = datetime.fromisoformat(msg['created_at'])
                repaired += 1
                repaired_ids.add(row.id)
        if repaired_ids:
            for item in db.scalars(select(MemoryItem)):
                if repaired_ids.intersection(item.source_ids): item.active = False
            for job in db.scalars(select(MemoryJob)):
                if repaired_ids.intersection(job.source_ids):
                    job.status = 'pending'; job.lease_until = None; job.payload = {}
            for review in db.scalars(select(HomeReview)):
                if repaired_ids.intersection(review.source_ids):
                    review.dismissed_at = clock(); review.content = '来源日期已校正，回顾待重新整理。'
        report = {'message_origins_repaired': repaired}
        db.add(SessionMigration(version=3, applied_at=clock(), report_json=report));db.commit()
        return report


if __name__ == '__main__':
    main()
