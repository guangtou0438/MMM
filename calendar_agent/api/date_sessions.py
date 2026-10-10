"""The only active task API. Older write routes are retired at the app boundary."""
from datetime import date
from typing import Literal
from pydantic import Field
from ..schemas import Input
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from ..models import (CalendarSession, AssistantMessage, LifecycleEvent, TaskSuggestion, Topic,
                      MemoryJob, HomeReview, SessionMigration, utc_now)
from .calendar_session_schemas import (CalendarSessionCreate, SessionAction, SessionUpdate, ChatInput, SuggestionDecision)
from ..core import date_tasks as service
from ..memory.personal import blocked_sources, citation

router = APIRouter(prefix='/api')

class ReviewSettings(Input):
    enabled: bool
    hour: int = Field(ge=0, le=23)
    combine: bool
    catch_up: Literal['none', 'latest']
    deadline_days: int = Field(default=3, ge=0, le=30)

@router.get('/preferences')
def get_preferences(request: Request):
    from ..scheduler.personal_periods import preferences
    with factory(request)() as db: return preferences(db)

@router.put('/preferences')
def put_preferences(payload: ReviewSettings, request: Request):
    from ..models import AppPreference
    with factory(request)() as db:
        row = db.get(AppPreference, 'reviews')
        if not row: row = AppPreference(key='reviews', value=payload.model_dump()); db.add(row)
        else: row.value = payload.model_dump()
        db.commit()
        return row.value

def clock(request): return (request.app.state.clock or utc_now)()

def factory(request): return request.app.state.sessions

def message(row):
    return {'id': row.id, 'role': row.role, 'content': row.content, 'citations': row.citations, 'created_at': row.created_at.isoformat(), 'session_id': row.session_id}

@router.get('/home')
def home(request: Request):
    now = clock(request)
    service.rollover(factory(request), now)
    with factory(request)() as db:
        from ..scheduler.personal_periods import preferences
        rows = db.scalars(select(CalendarSession).order_by(CalendarSession.planned_date, CalendarSession.id)).all()
        blocked = blocked_sources(db)
        suggestions = db.scalars(select(TaskSuggestion).where(TaskSuggestion.status == 'pending').order_by(TaskSuggestion.id)).all()
        return {'today': str(service.today(now)), 'preferences': preferences(db), 'sessions': [service.serialize(r) for r in rows if r.archived_at is None and not service.is_deleted(r)],
                'trash': [service.serialize(r) for r in rows if r.archived_at is not None and not service.is_deleted(r)],
                'suggestions': [{'id': r.id, 'proposal': r.proposal, 'source_message_id': r.source_message_id, 'source': citation(db.get(AssistantMessage, r.source_message_id))} for r in suggestions],
                'reviews': [{'key': r.key, 'content': r.content, 'citations': [citation(m) for i in r.source_ids if (m := db.get(AssistantMessage, i))]} for r in db.scalars(select(HomeReview).where(HomeReview.dismissed_at.is_(None))) if not blocked.intersection(r.source_ids)],
                'topics': [{'id': r.id, 'title': r.title} for r in db.scalars(select(Topic))],
                'memory': {'pending_or_failed': len(db.scalars(select(MemoryJob).where(MemoryJob.status != 'done')).all())},
                'migration': db.get(SessionMigration, 2).report_json if db.get(SessionMigration, 2) else None}

@router.get('/calendar')
def calendar(request: Request, start: date, end: date):
    if end <= start or (end-start).days > 370: service.fail(422, '日期范围应为 1–370 天')
    service.rollover(factory(request), clock(request))
    with factory(request)() as db:
        rows = db.scalars(select(CalendarSession).where(CalendarSession.archived_at.is_(None), CalendarSession.planned_date >= start, CalendarSession.planned_date < end)).all()
        return [{'id': str(r.id), 'title': ('安排 · ' if service.activity_type(r) == 'arrangement' else '任务 · ') + ('✓ ' if r.status == 'completed' else '') + r.title,
                 'start': str(r.planned_date), 'allDay': True, 'color': '#b97720' if service.activity_type(r) == 'arrangement' else '#5156c7',
                 'extendedProps': service.serialize(r)} for r in rows]

@router.get('/sessions/{session_id}')
def detail(session_id: int, request: Request):
    with factory(request)() as db:
        row = service.get_session(db, session_id)
        return service.serialize(row) | {'messages': [message(m) for m in db.scalars(select(AssistantMessage).where(AssistantMessage.session_id == session_id).order_by(AssistantMessage.id))],
                'history': [{'kind': e.kind, 'detail': e.detail, 'created_at': e.created_at.isoformat()} for e in db.scalars(select(LifecycleEvent).where(LifecycleEvent.session_id == session_id).order_by(LifecycleEvent.id))]}

@router.get('/home/messages')
def home_messages(request: Request, home_context_id: str | None = Query(default=None, pattern=r"^[a-f0-9-]{36}$")):
    conversation_id = "home:" + home_context_id if home_context_id else "home"
    with factory(request)() as db:
        return [message(m) for m in db.scalars(select(AssistantMessage).where(AssistantMessage.conversation_id == conversation_id).order_by(AssistantMessage.id))]

@router.get('/messages/{message_id}')
def source(message_id: int, request: Request):
    with factory(request)() as db:
        row = db.get(AssistantMessage, message_id)
        if not row or row.conversation_id.startswith('deleted:'): service.fail(404, '来源消息不存在或已删除')
        return message(row) | {'source': citation(row)}

@router.get('/topics/{topic_id}')
def topic(topic_id: int, request: Request):
    with factory(request)() as db:
        row = db.get(Topic, topic_id)
        if not row: service.fail(404, '历史主题不存在')
        return {'id': row.id, 'title': row.title, 'history': row.history_json,
                'messages': [message(m) for m in db.scalars(select(AssistantMessage).where(AssistantMessage.conversation_id == f'topic:{topic_id}').order_by(AssistantMessage.id))]}

@router.post('/sessions', status_code=201)
def create(payload: CalendarSessionCreate, request: Request):
    now = clock(request); value = payload.model_dump(mode='json'); key = 'create:' + payload.request_id
    with factory(request)() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        result = service.receipt(db, key, value)
        if result is not None:
            return JSONResponse({'detail':result}, status_code=409) if result.get('suggestion_id') else result
        if payload.duplicate_confirmation_id is not None: service.fail(422, '请在首页建议队列确认相似任务')
        try:
            with db.begin_nested(): row = service.create(db, payload, now)
        except HTTPException as error:
            if error.status_code != 409 or not isinstance(error.detail, dict): raise
            source = AssistantMessage(conversation_id='home', role='user', content='手动创建待办：'+payload.title, created_at=now)
            db.add(source);db.flush()
            proposal = {'kind':'create', **{k:v for k,v in value.items() if k not in {'request_id', 'duplicate_confirmation_id'} and v is not None}}
            proposal.setdefault('planned_date',str(service.today(now)))
            suggestion = service.queue(db,proposal,source.id,now)
            result = error.detail | {'suggestion_id':suggestion.id}
            service.save_receipt(db,key,value,result,now);db.commit()
            return JSONResponse({'detail':result},status_code=409)
        result = service.serialize(row)
        service.save_receipt(db, key, value, result, now); db.commit()
        return result

@router.patch('/sessions/{session_id}')
def edit(session_id: int, payload: SessionUpdate, request: Request):
    now = clock(request); value = {'session_id': session_id, **payload.model_dump(mode='json', exclude_unset=True)}; key = 'edit:' + payload.request_id
    with factory(request)() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        result = service.receipt(db, key, value)
        if result is not None: return result
        row = service.update(db, service.get_session(db, session_id), payload, now)
        result = service.serialize(row)
        service.save_receipt(db, key, value, result, now); db.commit()
        return result

@router.post('/sessions/{session_id}/{action}')
def action(session_id: int, action: str, payload: SessionAction, request: Request):
    if action not in {'complete', 'reopen', 'archive', 'restore'}: service.fail(404, '操作不存在')
    now = clock(request); value = {'session_id': session_id, 'action': action, **payload.model_dump()}; key = 'action:' + payload.request_id
    with factory(request)() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        result = service.receipt(db, key, value)
        if result is not None: return result
        row = service.lifecycle(db, service.get_session(db, session_id), action, payload.version, now)
        result = service.serialize(row)
        service.save_receipt(db, key, value, result, now); db.commit()
        return result

@router.post('/home/chat')
def home_chat(payload: ChatInput, request: Request):
    from ..core.personal_assistant import chat
    with request.app.state.planning_lock:
        return chat(factory(request), payload, clock(request), request.app.state.model_client)

# Separate URL avoids the user-only action route and exposes no operation selector.
@router.post('/session-chat/{session_id}')
def session_chat(session_id: int, payload: ChatInput, request: Request):
    from ..core.personal_assistant import chat
    if payload.version is None: service.fail(422, '需要会话版本')
    with request.app.state.planning_lock:
        return chat(factory(request), payload, clock(request), request.app.state.model_client, session_id)

@router.post('/suggestions/{suggestion_id}')
def decide(suggestion_id: int, payload: SuggestionDecision, request: Request):
    now = clock(request); value = {'suggestion_id': suggestion_id, **payload.model_dump()}; key = 'suggestion:' + payload.request_id
    with factory(request)() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        result = service.receipt(db, key, value)
        if result is not None: return result
        row = db.get(TaskSuggestion, suggestion_id)
        if not row: service.fail(404, '建议不存在')
        if row.status != 'pending': return row.result or {'status': row.status}
        if payload.action == 'ignore':
            row.status = 'ignored'; result = {'status': 'ignored'}
        else:
            proposal = dict(row.proposal)
            if payload.proposal is not None:
                # Only editable fields; confirmation/version/target come from the stored proposal.
                allowed = {'title', 'session_goal', 'planned_date', 'deadline_date', 'agenda', 'activity_type'}
                if set(payload.proposal) - allowed: service.fail(422, '包含不可修改的建议字段')
                proposal.update(payload.proposal)
                if 'title' in payload.proposal and payload.proposal['title'] != row.proposal.get('title'): proposal['_duplicates'] = []
            target = service.apply_proposal(db, proposal, now, payload.request_id, confirmed=True)
            row.status = 'accepted'; row.target_session_id = target.id
            result = {'status': 'accepted', 'session': service.serialize(target)}
        row.result = result; row.handled_at = now
        service.save_receipt(db, key, value, result, now); db.commit()
        return result

@router.post('/reviews/dismiss')
def dismiss_review(payload: dict, request: Request):
    if set(payload) != {'key'} or not isinstance(payload['key'], str): service.fail(422, '需要回顾 key')
    with factory(request)() as db:
        row = db.get(HomeReview, payload['key'])
        if not row: service.fail(404, '回顾不存在')
        row.dismissed_at = clock(request); db.commit()
        return {'status': 'dismissed'}


@router.post('/sessions/{session_id}/delete-permanently/confirm')
def delete_permanently(session_id: int, payload: SessionAction, request: Request):
    if request.headers.get('x-ui-action') != 'permanent-delete':
        service.fail(403, '永久删除必须由明确的 UI 确认控件触发')
    now = clock(request)
    value = {'session_id': session_id, **payload.model_dump()}
    key = 'delete:' + payload.request_id
    with request.app.state.planning_lock, factory(request)() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        cached = service.receipt(db, key, value)
        if cached is not None: return cached
        result = service.permanently_delete(db, service.get_session(db, session_id), payload.version, now)
        service.save_receipt(db, key, value, result, now)
        db.commit()
        return result
