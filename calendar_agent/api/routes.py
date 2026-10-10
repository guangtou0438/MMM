from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import AwareDatetime
from sqlalchemy import select, text

from ..config import DISPLAY_TIMEZONE
from ..core.time import resolve_local
from ..models import Subtask, Task, TaskNote, TaskMessage, LegacyTask, PlanningConversation, PlanAttempt
from ..schemas import ResolveTimes, TaskCreate, ChatInput, NoteInput

router = APIRouter()


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def serialize_task(db, task):
    steps = db.scalars(select(Subtask).where(Subtask.task_id == task.id).order_by(Subtask.id)).all()
    return {"id": task.id, "title": task.title, "deadline_utc": iso(task.deadline_utc),
            "estimated_minutes": task.estimated_minutes, "status": task.status, "created_at": iso(task.created_at),
            "subtasks": [{"id": s.id, "task_id": s.task_id, "title": s.title, "slot_start_utc": iso(s.slot_start_utc),
                          "duration_min": s.duration_min, "depends_on": s.depends_on, "status": s.status,
                          "actual_minutes": s.actual_minutes} for s in steps]}


@router.get("/health")
def health(request: Request):
    from ..config import llm_settings
    cfg = llm_settings()
    return {"status": "ok", "milestone": "P4", "timezone": DISPLAY_TIMEZONE, "llm_enabled": cfg.enabled,
            "model": cfg.model, "scheduler_enabled": request.app.state.scheduler.running, "import_warning": request.app.state.import_warning}


@router.post("/chat")
def chat(payload: ChatInput, request: Request):
    from ..core.planner import plan_request
    from ..models import utc_now
    with request.app.state.planning_lock:
        return plan_request(request.app.state.sessions, payload, request.app.state.model_client, request.app.state.clock or utc_now)


@router.get("/planning/conversations/{conversation_id}")
def conversation(conversation_id: str, request: Request):
    with request.app.state.sessions() as db:
        row = db.get(PlanningConversation, conversation_id)
        if not row: raise HTTPException(404, "对话不存在")
        return {"messages": row.messages_json}


@router.get("/planning/attempts/{request_id}")
def attempts(request_id: str, request: Request):
    with request.app.state.sessions() as db:
        return [{"attempt": row.attempt, "errors": row.errors_json, "created_at": iso(row.created_at)}
                for row in db.scalars(select(PlanAttempt).where(PlanAttempt.request_id == request_id).order_by(PlanAttempt.id))]


@router.get("/legacy/tasks")
def legacy_tasks(request: Request):
    with request.app.state.sessions() as db:
        return [{"source_id": row.source_id, "linked_task_id": row.linked_task_id,
                 "title": row.snapshot_json["title"], "status": row.snapshot_json["status"]} for row in db.scalars(select(LegacyTask))]


@router.get("/legacy/tasks/{source_id}")
def legacy_detail(source_id: str, request: Request):
    with request.app.state.sessions() as db:
        row = db.get(LegacyTask, source_id)
        if not row: raise HTTPException(404, "旧任务不存在")
        return {"task": row.snapshot_json, "linked_task_id": row.linked_task_id}


@router.get("/tasks/{task_id}/resume")
def resume_task(task_id: int, request: Request):
    with request.app.state.sessions() as db:
        task = db.get(Task, task_id)
        if not task: raise HTTPException(404, "任务不存在")
        notes = db.scalars(select(TaskNote).where(TaskNote.task_id == task_id).order_by(TaskNote.created_at)).all()
        steps = db.scalars(select(Subtask).where(Subtask.task_id == task_id, Subtask.status == "pending").order_by(Subtask.slot_start_utc)).all()
        old = db.scalar(select(LegacyTask).where(LegacyTask.linked_task_id == task_id))
        messages = db.scalars(select(TaskMessage).where(TaskMessage.task_id == task_id).order_by(TaskMessage.id)).all()
        return {"hint": f"接下来是「{steps[0].title}」。" if steps else "当前没有待执行的步骤。",
                "notes": [{"content": n.content, "created_at": iso(n.created_at)} for n in notes],
                "messages": [{"role": m.role, "content": m.content, "created_at": iso(m.created_at)} for m in messages],
                "legacy_history": old.snapshot_json if old else None}


@router.post("/tasks/{task_id}/chat")
def task_chat_route(task_id: int, payload: NoteInput, request: Request):
    from ..core.task_assistant import task_chat
    try:
        with request.app.state.planning_lock:
            return task_chat(request.app.state.sessions, task_id, payload.content, request.app.state.model_client)
    except KeyError:
        raise HTTPException(404, "任务不存在")


@router.post("/tasks/{task_id}/notes", status_code=201)
def add_note(task_id: int, payload: NoteInput, request: Request):
    with request.app.state.sessions() as db:
        if not db.get(Task, task_id): raise HTTPException(404, "任务不存在")
        row = TaskNote(task_id=task_id, content=payload.content)
        db.add(row); db.commit()
        return {"content": row.content, "created_at": iso(row.created_at)}


@router.post("/time/resolve")
def resolve_times(payload: ResolveTimes):
    try:
        return {"utc": [iso(resolve_local(value)) for value in payload.values]}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/tasks")
def list_tasks(request: Request):
    with request.app.state.sessions() as db:
        return [serialize_task(db, task) for task in db.scalars(select(Task).order_by(Task.deadline_utc, Task.id))]


@router.get("/tasks/{task_id}")
def get_task(task_id: int, request: Request):
    with request.app.state.sessions() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在。")
        return serialize_task(db, task)


@router.post("/tasks", status_code=201)
def create_task(payload: TaskCreate, request: Request):
    from ..models import utc_now
    now = (request.app.state.clock or utc_now)()
    if payload.deadline_utc <= now or any(s.slot_start_utc and s.slot_start_utc < now for s in payload.subtasks):
        raise HTTPException(400, "不能在过去创建任务或排期，请选择未来的时间。")
    # This is manual-entry validation, NOT free-slot generation / LLM planning.
    intervals = []
    for i, sub in enumerate(payload.subtasks):
        if sub.depends_on is not None:
            if sub.depends_on >= i:
                raise HTTPException(400, f"步骤 {i + 1} 只能依赖此前的步骤，不能依赖自身或形成循环。")
            parent = payload.subtasks[sub.depends_on]
            if sub.slot_start_utc and (not parent.slot_start_utc or parent.slot_start_utc + timedelta(minutes=parent.duration_min) > sub.slot_start_utc):
                raise HTTPException(400, f"步骤 {i + 1} 必须安排在依赖步骤结束之后。")
        if sub.slot_start_utc:
            end = sub.slot_start_utc + timedelta(minutes=sub.duration_min)
            if end > payload.deadline_utc:
                raise HTTPException(400, f"步骤「{sub.title}」结束时间超过任务 deadline。")
            if any(sub.slot_start_utc < other_end and end > other_start for other_start, other_end in intervals):
                raise HTTPException(409, "新任务内部的步骤时间重叠。")
            intervals.append((sub.slot_start_utc, end))
    with request.app.state.sessions() as db:
        # Reserve SQLite's writer lock BEFORE checking availability, so two
        # parallel creates cannot both accept the same vacant interval.
        db.execute(text("BEGIN IMMEDIATE"))
        existing = db.scalars(select(Subtask).where(Subtask.slot_start_utc.is_not(None), Subtask.status != "skipped")).all()
        for start, end in intervals:
            for sub in existing:
                if start < sub.slot_start_utc + timedelta(minutes=sub.duration_min) and end > sub.slot_start_utc:
                    raise HTTPException(409, f"与「{sub.title}」的日程重叠，请选择其他时间。")
        task = Task(title=payload.title, deadline_utc=payload.deadline_utc, estimated_minutes=payload.estimated_minutes)
        db.add(task)
        db.flush()
        inserted = []
        for sub in payload.subtasks:
            row = Subtask(task_id=task.id, title=sub.title, slot_start_utc=sub.slot_start_utc,
                          duration_min=sub.duration_min, depends_on=inserted[sub.depends_on].id if sub.depends_on is not None else None)
            db.add(row)
            db.flush()
            inserted.append(row)
        db.commit()
        return serialize_task(db, task)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, request: Request):
    with request.app.state.sessions() as db:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在。")
        db.delete(task)
        db.commit()
    return Response(status_code=204)


@router.get("/calendar")
def calendar(request: Request, start: Annotated[AwareDatetime, Query()], end: Annotated[AwareDatetime, Query()]):
    if end <= start or end - start > timedelta(days=370):
        raise HTTPException(400, "日历范围需要 start < end，且不超过 370 天。")
    with request.app.state.sessions() as db:
        rows = db.execute(select(Subtask, Task).join(Task).where(Subtask.slot_start_utc < end, Subtask.status != "skipped")).all()
        return [{"id": str(sub.id), "title": sub.title, "start": iso(sub.slot_start_utc),
                 "color": f"hsl({(task.id * 137) % 360}, 58%, 42%)",
                 "textColor": "#ffffff",
                 "end": iso(sub.slot_start_utc + timedelta(minutes=sub.duration_min)), "allDay": False,
                 "extendedProps": {"task_id": task.id, "task_title": task.title, "deadline_utc": iso(task.deadline_utc),
                                   "duration_min": sub.duration_min, "actual_minutes": sub.actual_minutes, "status": sub.status}}
                for sub, task in rows if sub.slot_start_utc + timedelta(minutes=sub.duration_min) > start]
