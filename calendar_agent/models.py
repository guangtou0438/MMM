"""All instant columns normalize to UTC, including SQLite serialization."""
from datetime import date as date_cls, datetime, timezone

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now():
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    # SQLite doesn't preserve timezone=True. Fixed-width UTC strings also sort
    # chronologically, unlike mixed-offset ISO strings.
    impl = String(27)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("入库时间必须包含时区。")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def process_result_value(self, value, dialect):
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    deadline_utc: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    estimated_minutes: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="planned")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    __table_args__ = (CheckConstraint("estimated_minutes > 0"),
                     CheckConstraint("status IN ('planned','in_progress','completed','cancelled')"))


class Subtask(Base):
    __tablename__ = "subtasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    slot_start_utc: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    duration_min: Mapped[int]
    depends_on: Mapped[int | None] = mapped_column(ForeignKey("subtasks.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    actual_minutes: Mapped[int | None]
    __table_args__ = (CheckConstraint("duration_min > 0"),
                     CheckConstraint("actual_minutes IS NULL OR actual_minutes >= 0"),
                     CheckConstraint("depends_on IS NULL OR depends_on != id"),
                     CheckConstraint("status IN ('pending','in_progress','completed','skipped')"))


class WorkSession(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    subtask_id: Mapped[int] = mapped_column(ForeignKey("subtasks.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    actual_minutes: Mapped[int | None]
    __table_args__ = (CheckConstraint("ended_at IS NULL OR ended_at >= started_at"),
                     CheckConstraint("actual_minutes IS NULL OR actual_minutes >= 0"))


class MemoryDaily(Base):
    __tablename__ = "memory_daily"
    date: Mapped[date_cls] = mapped_column(Date, primary_key=True)
    content_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MemoryWeekly(Base):
    __tablename__ = "memory_weekly"
    week_start: Mapped[date_cls] = mapped_column(Date, primary_key=True)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MemoryMonthly(Base):
    __tablename__ = "memory_monthly"
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MemoryYearly(Base):
    __tablename__ = "memory_yearly"
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_json: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class PlanningConversation(Base):
    __tablename__ = "planning_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    messages_json: Mapped[list] = mapped_column(JSON, default=list)
    pending_message: Mapped[str] = mapped_column(Text, default="")
    legacy_task_id: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PlanningRequest(Base):
    __tablename__ = "planning_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    response_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PlanAttempt(Base):
    __tablename__ = "plan_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt: Mapped[int]
    errors_json: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class LegacyTask(Base):
    __tablename__ = "legacy_tasks"
    source_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    linked_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class TaskNote(Base):
    __tablename__ = "task_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class TaskMessage(Base):
    __tablename__ = "task_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    subtask_id: Mapped[int] = mapped_column(ForeignKey("subtasks.id", ondelete="CASCADE"), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    dismissed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class UserFact(Base):
    __tablename__ = "user_facts"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Topic(Base):
    __tablename__ = "session_topics"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(80))
    # Task-level evidence stays here, never duplicated into Session messages.
    history_json: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("source_type", "source_id"),)


class CalendarSession(Base):
    __tablename__ = "calendar_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    session_goal: Mapped[str] = mapped_column(Text, default="")
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("session_topics.id"))
    # Legacy instants are retained as evidence only; new tasks use local dates.
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    planned_date: Mapped[date_cls | None] = mapped_column(Date, index=True)
    deadline_date: Mapped[date_cls | None] = mapped_column(Date, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    agenda_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open")
    outcome: Mapped[str | None] = mapped_column(String(20))
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    close_reason: Mapped[str | None] = mapped_column(String(40))
    continued_from_session_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_sessions.id"))
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(80))
    migration_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (
        UniqueConstraint("source_type", "source_id"),
        CheckConstraint("end_at > start_at"),
        CheckConstraint("version > 0"),
        CheckConstraint("status IN ('open','completed','scheduled','active','closed','cancelled')"),
        CheckConstraint("outcome IS NULL OR outcome IN ('completed','partial','blocked','unknown')"),
        CheckConstraint("continued_from_session_id IS NULL OR continued_from_session_id != id"),
    )


class SessionMessage(Base):
    __tablename__ = "session_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_session_id: Mapped[int] = mapped_column(ForeignKey("calendar_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    __table_args__ = (CheckConstraint("role IN ('user','assistant','system')"),)


class SessionExecution(Base):
    __tablename__ = "session_executions"
    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_session_id: Mapped[int] = mapped_column(ForeignKey("calendar_sessions.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    actual_minutes: Mapped[int | None]
    outcome: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(80))
    migration_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("source_type", "source_id"),
                     CheckConstraint("actual_minutes IS NULL OR actual_minutes >= 0"))


class SessionMigration(Base):
    __tablename__ = "session_migrations"
    version: Mapped[int] = mapped_column(primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    report_json: Mapped[dict] = mapped_column(JSON)


class LifecycleEvent(Base):
    __tablename__ = "session_lifecycle_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("calendar_sessions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_sessions.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(String(80), index=True, default="home")
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    context_source_ids: Mapped[list] = mapped_column(JSON, default=list)
    origin_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class TaskSuggestion(Base):
    __tablename__ = "task_suggestions"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[int] = mapped_column(ForeignKey("assistant_messages.id"))
    proposal: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    target_session_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_sessions.id"))
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    handled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OperationReceipt(Base):
    __tablename__ = "operation_receipts"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MemoryItem(Base):
    __tablename__ = "personal_memory_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text)
    source_ids: Mapped[list] = mapped_column(JSON)
    related_ids: Mapped[list] = mapped_column(JSON, default=list)
    certainty: Mapped[str] = mapped_column(String(30), default="reported")
    occurred_on: Mapped[str | None] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(default=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MemoryDirective(Base):
    __tablename__ = "personal_memory_directives"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    source_ids: Mapped[list] = mapped_column(JSON)
    instruction: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MemoryJob(Base):
    __tablename__ = "personal_memory_jobs"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class HomeReview(Base):
    __tablename__ = "home_reviews"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    source_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    dismissed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AppPreference(Base):
    __tablename__ = 'app_preferences'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
