import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from calendar_agent.api.calendar_session_schemas import CalendarSessionCreate, CalendarSessionComplete
from calendar_agent.core.session_policy import require_permission, SessionPermissionError
from calendar_agent.database import create_database
from calendar_agent.models import (Task, Subtask, WorkSession, TaskMessage, TaskNote,
    CalendarSession, Topic, SessionExecution, SessionMessage, SessionMigration)
from calendar_agent.session_migration import migrate_sessions, migrate_dates, snapshot_database

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


class SessionPolicyTests(unittest.TestCase):
    def test_dates_never_close_interaction(self):
        row = SimpleNamespace(status='open', archived_at=None)
        for delta in (-365, 0, 365):
            require_permission(row, 'chat', NOW + timedelta(days=delta))
        for status, archived in [('completed', None), ('open', NOW)]:
            with self.assertRaises(SessionPermissionError):
                require_permission(SimpleNamespace(status=status, archived_at=archived), 'chat', NOW)

    def test_strict_new_schema_has_no_minutes_or_entry_flags(self):
        value = dict(request_id='r', version=1)
        self.assertEqual(CalendarSessionComplete(**value).version, 1)
        for changes in ({'actual_minutes': 0}, {'entry': 'user'}, {'version': True}):
            with self.assertRaises(ValidationError): CalendarSessionComplete(**(value | changes))
        self.assertEqual(CalendarSessionCreate(request_id='r', title='读书').agenda, [])


class SessionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "calendar.db"
        self.engine, self.factory = create_database(self.path)
        with self.factory() as db:
            task = Task(title="复习", deadline_utc=NOW, estimated_minutes=60)
            db.add(task); db.flush()
            step = Subtask(task_id=task.id, title="线性代数", slot_start_utc=NOW - timedelta(hours=2), duration_min=60, status="pending")
            db.add(step); db.flush()
            db.add(Subtask(task_id=task.id, title="尚未安排", duration_min=30))
            db.add(TaskMessage(task_id=task.id, role="user", content="任务级消息", created_at=NOW))
            db.add(TaskNote(task_id=task.id, content="任务级笔记", created_at=NOW))
            db.add(WorkSession(subtask_id=step.id, started_at=NOW - timedelta(hours=2), ended_at=NOW - timedelta(hours=1), actual_minutes=0, outcome="partial"))
            db.add(WorkSession(subtask_id=step.id, started_at=NOW - timedelta(minutes=30), actual_minutes=None))
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_idempotent_migration_preserves_evidence_and_unknown(self):
        report = migrate_sessions(self.factory, lambda: NOW)
        self.assertEqual(report, migrate_sessions(self.factory, lambda: NOW + timedelta(days=1)))
        self.assertEqual((report["topics"], report["sessions"], report["executions"]), (1, 1, 2))
        self.assertEqual(len(report["unscheduled_subtask_ids"]), 1)
        self.assertEqual(len(report["review"]), 1)
        with self.factory() as db:
            row = db.scalar(select(CalendarSession))
            self.assertEqual((row.status, row.outcome), ("closed", "unknown"))
            self.assertIsNone(row.closed_at)
            executions = db.scalars(select(SessionExecution).order_by(SessionExecution.id)).all()
            self.assertEqual([e.actual_minutes for e in executions], [0, None])
            self.assertIsNone(executions[1].ended_at)
            self.assertEqual(executions[1].started_at, NOW - timedelta(minutes=30))
            topic = db.scalar(select(Topic))
            self.assertEqual(len(topic.history_json["messages"]), 1)
            self.assertEqual(len(topic.history_json["notes"]), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(SessionMessage)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(WorkSession)), 2)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_v2_retains_v1_report_and_restores_unfinished_past(self):
        previous = migrate_sessions(self.factory, lambda: NOW)
        result = migrate_dates(self.factory, lambda: NOW)
        self.assertEqual(result, migrate_dates(self.factory, lambda: NOW + timedelta(days=1)))
        with self.factory() as db:
            row = db.scalar(select(CalendarSession))
            self.assertEqual(row.status, 'open')
            self.assertEqual(str(row.planned_date), '2026-09-06')
            self.assertIsNone(row.deadline_date)
            self.assertEqual(db.get(SessionMigration, 1).report_json, previous)
            self.assertEqual([e.actual_minutes for e in db.scalars(select(SessionExecution).order_by(SessionExecution.id))], [0, None])
            self.assertEqual(db.execute(text('PRAGMA foreign_key_check')).all(), [])

    def test_v3_preserves_original_message_dates_and_origin_uniqueness(self):
        from calendar_agent.models import PlanningConversation, AssistantMessage, MemoryItem
        from calendar_agent.session_migration import repair_message_origins
        source_time = NOW - timedelta(days=5)
        with self.factory() as db:
            db.add(PlanningConversation(id='original', messages_json=[{'role':'user','content':'原始经历','created_at':source_time.isoformat()}], updated_at=NOW))
            db.commit()
        migrate_dates(self.factory, lambda: NOW)
        with self.factory() as db:
            row = db.scalar(select(AssistantMessage).where(AssistantMessage.conversation_id=='home'))
            self.assertEqual(row.created_at, source_time)
            mid = row.id
            row.origin_key = None; row.created_at = NOW
            db.add(MemoryItem(kind='experience',content='旧日期的派生内容',source_ids=[mid]))
            db.commit()
        report = repair_message_origins(self.factory, lambda: NOW)
        self.assertEqual(report, repair_message_origins(self.factory, lambda: NOW))
        self.assertEqual(report['message_origins_repaired'], 1)
        with self.factory() as db:
            self.assertEqual(db.get(AssistantMessage,mid).created_at, source_time)
            self.assertEqual(db.get(AssistantMessage,mid).origin_key, 'planning:original:0')
            self.assertFalse(db.scalar(select(MemoryItem)).active)

    def test_failed_migration_rolls_back_every_import(self):
        with self.factory() as db:
            db.add(Topic(title="冲突", source_type="tasks", source_id="1")); db.commit()
        with self.assertRaises(IntegrityError):
            migrate_sessions(self.factory, lambda: NOW)
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(CalendarSession)), 0)
            self.assertIsNone(db.get(SessionMigration, 1))
            self.assertEqual(db.scalar(select(func.count()).select_from(Subtask)), 2)

    def test_wal_backup_restore_and_overwrite_protection(self):
        backup = self.path.with_name("backup.db")
        snapshot_database(self.path, backup)
        with self.assertRaises(FileExistsError):
            snapshot_database(self.path, backup)
        migrate_sessions(self.factory, lambda: NOW)
        restored = self.path.with_name("restored.db")
        snapshot_database(backup, restored)
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM calendar_sessions").fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_unknown_schema_is_rejected_before_ddl(self):
        old = self.path.with_name("old.db")
        with closing(sqlite3.connect(old)) as db:
            db.execute("CREATE TABLE sentinel(id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO sentinel VALUES (1)")
            db.commit()
        with self.assertRaisesRegex(ValueError, 'Schema'):
            create_database(old)
        with closing(sqlite3.connect(old)) as db:
            self.assertEqual(db.execute("SELECT * FROM sentinel").fetchall(), [(1,)])
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='calendar_sessions'").fetchone())
        self.assertEqual(len(list(old.parent.glob("old.db.pre-session-*.db"))), 0)


if __name__ == "__main__":
    unittest.main()
