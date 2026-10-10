"""Transactional session lifecycle. No rollups or model calls here."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select, text, func
from ..models import WorkSession, Subtask, Task, Notification, utc_now
from .session_schemas import SessionStart, SessionComplete
from .routes import iso

router = APIRouter()

def serialize(row):
    return {key: getattr(row, key) for key in ('id', 'subtask_id', 'outcome', 'note', 'actual_minutes')} | {'started_at': iso(row.started_at), 'ended_at': iso(row.ended_at)}

@router.get('/sessions/active')
def active(request: Request):
    with request.app.state.sessions() as db:
        return [serialize(s) | {'title': db.get(Subtask, s.subtask_id).title} for s in db.scalars(select(WorkSession).where(WorkSession.ended_at.is_(None)))]

@router.post('/sessions/start')
def start(payload: SessionStart, request: Request):
    now = (request.app.state.clock or utc_now)()
    with request.app.state.sessions() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        step = db.get(Subtask, payload.subtask_id)
        if not step: raise HTTPException(404, '步骤不存在')
        existing = db.scalar(select(WorkSession).where(WorkSession.subtask_id == step.id, WorkSession.ended_at.is_(None)))
        if existing: return serialize(existing) | {'reply': f'继续「{step.title}」，计时已开始。'}
        task = db.get(Task, step.task_id)
        if step.status in ('completed', 'skipped') or task.status == 'cancelled': raise HTTPException(409, '该步骤不能开始')
        if db.scalar(select(WorkSession.id).where(WorkSession.ended_at.is_(None))): raise HTTPException(409, '请先结束当前计时会话')
        if step.depends_on and db.get(Subtask, step.depends_on).status != 'completed': raise HTTPException(409, '请先完成依赖步骤')
        previous = db.scalar(select(WorkSession).where(WorkSession.subtask_id == step.id).order_by(WorkSession.id.desc()))
        row = WorkSession(subtask_id=step.id, started_at=now)
        db.add(row); step.status = 'in_progress'; task.status = 'in_progress'
        notification = db.scalar(select(Notification).where(Notification.subtask_id == step.id))
        if notification: notification.dismissed_at = now
        reply = f'开始「{task.title}」中的「{step.title}」，计划 {step.duration_min} 分钟。结束时请填写实际用时。'
        if previous and previous.note: reply += '\n上次记录：' + previous.note[:600]
        db.commit()
        return serialize(row) | {'reply': reply}

@router.post('/sessions/complete')
def complete(payload: SessionComplete, request: Request):
    now = (request.app.state.clock or utc_now)()
    with request.app.state.sessions() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        row = db.get(WorkSession, payload.session_id)
        if not row: raise HTTPException(404, '会话不存在')
        if row.ended_at:
            if (row.actual_minutes, row.outcome, row.note) != (payload.actual_minutes, payload.outcome, payload.note): raise HTTPException(409, '会话已结束，不能重复覆盖记录')
            return serialize(row)
        if now < row.started_at: raise HTTPException(400, '结束时间不能早于开始时间')
        row.actual_minutes = payload.actual_minutes; row.outcome = payload.outcome; row.note = payload.note; row.ended_at = now
        db.flush()
        step = db.get(Subtask, row.subtask_id)
        step.actual_minutes = db.scalar(select(func.sum(WorkSession.actual_minutes)).where(WorkSession.subtask_id == step.id)) or 0
        step.status = 'completed' if payload.outcome == 'completed' else 'pending'
        task = db.get(Task, step.task_id)
        db.flush()
        remaining = db.scalar(select(Subtask.id).where(Subtask.task_id == task.id, Subtask.status.not_in(('completed', 'skipped'))).limit(1))
        task.status = 'completed' if remaining is None else 'in_progress'
        db.commit()
        return serialize(row) | {'subtask_actual_minutes': step.actual_minutes, 'subtask_status': step.status}

@router.get('/notifications/pending')
def pending(request: Request):
    with request.app.state.sessions() as db:
        rows = db.execute(select(Notification, Subtask).join(Subtask).join(Task).where(Notification.dismissed_at.is_(None), Subtask.status == 'pending', Task.status != 'cancelled').order_by(Notification.created_at)).all()
        return [{'id': n.id, 'subtask_id': s.id, 'title': s.title, 'slot_start_utc': iso(s.slot_start_utc)} for n,s in rows]

@router.post('/notifications/{notification_id}/dismiss')
def dismiss(notification_id: int, request: Request):
    with request.app.state.sessions() as db:
        row = db.get(Notification, notification_id)
        if not row: raise HTTPException(404, '提醒不存在')
        row.dismissed_at = (request.app.state.clock or utc_now)(); db.commit()
        return {'ok': True}

@router.get('/memory/status')
def memory_status(request: Request):
    from ..models import MemoryDaily, MemoryWeekly, MemoryMonthly, MemoryYearly
    from ..memory.context import summary_text
    with request.app.state.sessions() as db:
        result = {}
        for key, model, field in [('daily', MemoryDaily, MemoryDaily.date), ('weekly', MemoryWeekly, MemoryWeekly.week_start), ('monthly', MemoryMonthly, MemoryMonthly.month), ('yearly', MemoryYearly, MemoryYearly.year)]:
            row = db.scalar(select(model).order_by(field.desc()))
            result[key] = None if not row else {'period': str(getattr(row, field.key)), 'summary': row.content_json['summary'] if key == 'daily' else row.profile_json.get('summary', '') if key == 'yearly' else summary_text(row.summary)}
        result['scheduler_enabled'] = request.app.state.scheduler.running
        result['jobs'] = [{'id': job.id, 'next_run': iso(getattr(job, 'next_run_time', None))} for job in request.app.state.scheduler.get_jobs()]
        return result
