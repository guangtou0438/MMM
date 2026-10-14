"""Single write service for date tasks, lifecycle and persisted confirmations."""
import hashlib
import json
import re
from datetime import timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, text
from ..config import DISPLAY_TIMEZONE
from ..models import CalendarSession, LifecycleEvent, OperationReceipt, TaskSuggestion, AssistantMessage, Topic
from .session_policy import require_permission, SessionPermissionError, instant
from .request import day_of, DATE
from ..api.calendar_session_schemas import CalendarSessionCreate, SessionUpdate, Proposal


def today(now): return instant(now).astimezone(ZoneInfo(DISPLAY_TIMEZONE)).date()

def fail(code, message): raise HTTPException(code, message)

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def receipt(db, key, value):
    row = db.get(OperationReceipt, key)
    if row and row.fingerprint != fingerprint(value): fail(409, '请求 ID 已用于不同操作，请刷新后重试。')
    if not row: return None
    result = dict(row.response)
    source_ids = result.pop('_memory_source_ids', [])
    if source_ids:
        from ..memory.personal import blocked_sources
        if blocked_sources(db).intersection(source_ids):
            return {'reply': '这次历史回复使用了已停用的记忆，不再重放。请发送新的问题。', 'citations': [], 'memory_redacted': True}
    return result

def save_receipt(db, key, value, result, now):
    db.add(OperationReceipt(id=key, fingerprint=fingerprint(value), response=result, created_at=now))

def get_session(db, session_id):
    row = db.get(CalendarSession, session_id)
    if not row or is_deleted(row): fail(404, '会话不存在或已永久删除')
    return row

def check(row, action, version=None, entry='user'):
    if action == 'chat' and activity_type(row) == 'arrangement': fail(409, '这是安排，不创建聊天 Session。请在日历中查看或编辑。')
    if version is not None and row.version != version: fail(409, '会话已发生变化，请刷新后重试。')
    try: require_permission(row, action, entry=entry)
    except SessionPermissionError as e:
        fail(409 if e.code in {'SESSION_ARCHIVED', 'SESSION_READ_ONLY'} else 403, e.code)

def event(db, row, kind, detail, now):
    db.add(LifecycleEvent(session_id=row.id, kind=kind, detail=detail, created_at=now))
    row.version += 1
    row.updated_at = now

def serialize(row):
    return {'id': row.id, 'activity_type': activity_type(row), 'has_session': activity_type(row) == 'task', 'title': row.title, 'session_goal': row.session_goal,
            'planned_date': str(row.planned_date) if row.planned_date else None,
            'deadline_date': str(row.deadline_date) if row.deadline_date else None,
            'agenda': row.agenda_json, 'status': row.status,
            'archived_at': row.archived_at.isoformat() if row.archived_at else None,
            'version': row.version, 'created_at': row.created_at.isoformat(),
            'topic_id': row.topic_id, 'migration_metadata': row.migration_metadata}

def rollover(factory, now):
    with factory() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        rows = db.scalars(select(CalendarSession).where(CalendarSession.status == 'open', CalendarSession.archived_at.is_(None), CalendarSession.deadline_date.is_(None), CalendarSession.planned_date < today(now))).all()
        for row in rows:
            if activity_type(row) == 'arrangement': continue
            previous = str(row.planned_date)
            row.planned_date = today(now)
            event(db, row, 'rollover', {'from': previous, 'to': str(row.planned_date)}, now)
        db.commit()
        return len(rows)

def normalized(title): return re.sub(r'[\W_]+', '', title.casefold())

def similar(db, title, exclude=None):
    target = normalized(title)
    rows = db.scalars(select(CalendarSession).where(CalendarSession.status == 'open', CalendarSession.archived_at.is_(None))).all()
    return [row for row in rows if row.id != exclude and (normalized(row.title) in target or target in normalized(row.title) or SequenceMatcher(None, normalized(row.title), target).ratio() >= .65)]

def create(db, payload, now, *, confirmed_duplicates=()):
    if payload.planned_date and payload.planned_date < today(now):
        fail(422, '不能在过去的日期创建新任务，请选择今天或未来日期。已有历史会话仍可继续。')
    if (payload.requires_deadline or re.search(r'作业|assignment|homework', payload.title, re.I)) and not payload.deadline_date:
        fail(422, '这项作业或有期限的任务需要截止日期，请补充。')
    duplicates = similar(db, payload.title)
    if any(r.id not in confirmed_duplicates for r in duplicates):
        fail(409, {'message': '发现相似未完成任务，请先确认补充已有任务还是另建。', 'duplicates': [serialize(r) for r in duplicates]})
    row = CalendarSession(title=payload.title.strip(), session_goal=payload.session_goal,
        planned_date=payload.planned_date or today(now), deadline_date=payload.deadline_date,
        agenda_json=[a.model_dump() for a in payload.agenda], migration_metadata={'activity_type': payload.activity_type or classify_activity(payload.title)}, status='open', created_at=now, updated_at=now)
    db.add(row); db.flush()
    db.add(LifecycleEvent(session_id=row.id, kind='created', detail={'planned_date': str(row.planned_date)}, created_at=now))
    return row

def create_recurring(db, payload, now):
    start = max(payload.start_date or today(now), today(now))
    if payload.until_date < start: fail(422, '重复截止日期不能早于开始日期。')
    if (payload.until_date - start).days > 366: fail(422, '重复范围最长一年，请缩短截止日期。')
    days = sorted(set(payload.weekdays))
    dates = [start + timedelta(n) for n in range((payload.until_date - start).days + 1) if (start + timedelta(n)).isoweekday() in days]
    if not dates: fail(422, '所选范围内没有符合的日期。')
    if len(dates) > 60: fail(422, f'将生成 {len(dates)} 条，超过 60 条上限，请缩短范围。')
    goal = (f'时间：{payload.time_text}\n' if payload.time_text else '') + payload.session_goal
    rows, confirmed = [], []
    for day in dates:
        row = create(db, CalendarSessionCreate(request_id='recurring', activity_type=payload.activity_type, title=payload.title,
                                               session_goal=goal, planned_date=day), now, confirmed_duplicates=confirmed)
        confirmed.append(row.id); rows.append(row)
    return rows

def update(db, row, payload, now, entry='user'):
    check(row, 'update', payload.version, entry)
    data = payload.model_dump(exclude_unset=True)
    changes = {}
    if data.get('activity_type'):
        changes['activity_type'] = {'from': activity_type(row), 'to': data['activity_type']}
        row.migration_metadata = dict(row.migration_metadata or {}) | {'activity_type': data['activity_type']}
    for field in ('title', 'session_goal', 'planned_date', 'deadline_date', 'agenda'):
        if field not in data: continue
        value = data[field]
        if field in ('title', 'session_goal', 'planned_date', 'agenda') and value is None: fail(422, f'{field} 不可为空')
        attr = 'agenda_json' if field == 'agenda' else field
        changes[field] = {'from': getattr(row, attr), 'to': value}
        setattr(row, attr, value)
    if re.search(r'作业|assignment|homework', row.title, re.I) and not row.deadline_date: fail(422, '作业必须保留截止日期')
    event(db, row, 'updated', json.loads(json.dumps(changes, default=str)), now)
    return row

def lifecycle(db, row, action, version, now, entry='user'):
    check(row, action, version, entry)
    if action == 'complete': row.status = 'completed'
    elif action == 'reopen': row.status = 'open'
    elif action == 'archive': row.archived_at = now
    elif action == 'restore': row.archived_at = None
    else: fail(422, '不支持的操作')
    event(db, row, action, {'actor': 'user' if entry == 'user' else 'home_assistant'}, now)
    return row

def resolve_date(value, now):
    if value is None: return None
    try:
        matches = re.findall(DATE, value)
        if len(matches) != 1: fail(422, '日期不明确，请使用 YYYY-MM-DD 或今天、下周五。')
        result = day_of(value, now)
        if result is None: fail(422, '无法解析日期，请使用 YYYY-MM-DD。')
        return result
    except (ValueError, KeyError): fail(422, '日期无效，请检查月份和日期。')

def resolve_proposal(proposal, now):
    p = proposal.model_dump(exclude_none=True)
    if 'date_text' in p: p['planned_date'] = str(resolve_date(p.pop('date_text'), now))
    if 'deadline_text' in p: p['deadline_date'] = str(resolve_date(p.pop('deadline_text'), now))
    if p['kind'] == 'create': p.setdefault('planned_date', str(today(now)))
    return p

def queue(db, proposal, source_id, now):
    # Persist a stable proposal, including the versions the user will confirm.
    p = dict(proposal)
    if p['kind'] == 'create': p['_duplicates'] = [r.id for r in similar(db, p.get('title', ''))]
    elif p.get('target_session_id'):
        row = get_session(db, p['target_session_id'])
        p.setdefault('version', row.version)
    existing = db.scalars(select(TaskSuggestion).where(TaskSuggestion.source_message_id == source_id)).all()
    for row in existing:
        if row.proposal == p: return row
    row = TaskSuggestion(source_message_id=source_id, proposal=p, created_at=now)
    db.add(row); db.flush()
    return row

def apply_proposal(db, p, now, key, *, confirmed=False, entry='home'):
    p = dict(p)
    kind = p.pop('kind')
    duplicates = p.pop('_duplicates', [])
    target = p.pop('target_session_id', None)
    version = p.pop('version', None)
    if kind == 'create':
        payload = CalendarSessionCreate(request_id=key, **p)
        return create(db, payload, now, confirmed_duplicates=duplicates if confirmed else ())
    p.pop('requires_deadline', None)
    if not target or not version: fail(422, '操作需要明确的目标会话和版本')
    row = get_session(db, target)
    if kind == 'update': return update(db, row, SessionUpdate(request_id=key, version=version, **p), now, entry)
    if kind not in {'archive', 'restore'} or p: fail(422, '管理操作字段无效')
    return lifecycle(db, row, kind, version, now, entry)


def is_deleted(row):
    return bool((row.migration_metadata or {}).get('permanently_deleted_at'))


def permanently_delete(db, row, version, now):
    """Erase live task content, retaining identifier tombstones to prevent ID reuse."""
    from sqlalchemy import delete
    from ..models import (SessionMessage, SessionExecution, MemoryItem, MemoryJob, HomeReview)
    from ..memory.personal import blocked_sources
    if row.version != version: fail(409, '会话已发生变化，请刷新后重试。')
    if row.archived_at is None: fail(409, '请先将任务移入回收站。')
    if db.execute(text("SELECT 1 FROM sqlite_master WHERE name='memory_edges'")).first():
        # New memory references share the calendar transaction, so deletion and
        # concurrent task-memory synchronization cannot resurrect each other.
        source_ids = set()
        memory_ids = {r[0] for r in db.execute(text("SELECT source_id FROM memory_edges WHERE target_id=:target AND relation='task'"), {'target': 'calendar:'+str(row.id)})}
        for mid in memory_ids:
            item = db.execute(text('SELECT source_ids FROM memories WHERE id=:id'), {'id': mid}).first()
            if item: source_ids.update(json.loads(item[0]))
        all_memories = db.execute(text('SELECT id,source_ids,parent_ids FROM memories')).all()
        changed = True
        while changed:
            changed = False
            for mid, sources, parents in all_memories:
                ids = set(json.loads(sources))
                related = mid in memory_ids or bool(ids & source_ids) or bool(memory_ids.intersection(json.loads(parents)))
                if related and (mid not in memory_ids or not ids <= source_ids):
                    memory_ids.add(mid); source_ids.update(ids); changed = True
        for source_id in source_ids:
            db.execute(text('UPDATE memory_sources SET forgotten=1,body=NULL WHERE id=:id'), {'id': source_id})
            db.execute(text("UPDATE memory_jobs SET status='cancelled' WHERE source_id=:id"), {'id': source_id})
        for item in db.execute(text('SELECT id,source_ids FROM memories')).all():
            if item[0] in memory_ids:
                db.execute(text("UPDATE memories SET active=0,content='',version=version+1 WHERE id=:id"), {'id': item[0]})
                db.execute(text('DELETE FROM memories_fts WHERE id=:id'), {'id': item[0]})
                db.execute(text('DELETE FROM memory_embeddings WHERE memory_id=:id'), {'id': item[0]})
        db.execute(text('DELETE FROM working_memory'))
        db.execute(text('DELETE FROM agent_events'))
        db.execute(text("UPDATE agent_runs SET goal='',answer='',status=CASE WHEN status IN ('queued','running','waiting_for_user') THEN 'cancelled' ELSE status END"))
        db.execute(text("UPDATE tool_calls SET arguments='{}',result=NULL,compensation=NULL"))
        db.execute(text("UPDATE agent_runs SET citations='[]'"))
        db.execute(text("UPDATE adjustment_suggestions SET summary='{}',status='deleted' WHERE session_id=:id"), {'id': row.id})
    messages = db.scalars(select(AssistantMessage).where(AssistantMessage.session_id == row.id)).all()
    ids = {m.id for m in messages}
    for m in messages:
        m.content = ''; m.citations = []; m.context_source_ids = []
        m.conversation_id = f'deleted:{row.id}'
    db.flush()
    blocked = blocked_sources(db)
    # Suppress derived evidence even when background extraction is already running.
    for m in db.scalars(select(AssistantMessage)):
        if m.id in blocked - ids:
            m.content = '这条回复引用的任务已删除，相关内容已撤回。'
            m.context_source_ids = sorted(set(m.context_source_ids) | ids)
        m.citations = [c for c in m.citations if c.get('message_id') not in blocked]
    for item in db.scalars(select(MemoryItem)):
        if blocked.intersection(item.source_ids): item.active = False; item.content = ''; item.metadata_json = {}
    for job in db.scalars(select(MemoryJob)):
        if blocked.intersection(job.source_ids):
            job.payload = {}; job.error = None; job.status = 'done'; job.lease_until = None
    for review in db.scalars(select(HomeReview)):
        if blocked.intersection(review.source_ids): review.content = ''; review.dismissed_at = now
    suggestion_ids = set()
    for suggestion in db.scalars(select(TaskSuggestion)):
        p = suggestion.proposal
        if suggestion.source_message_id in blocked or suggestion.target_session_id == row.id or p.get('target_session_id') == row.id or row.id in p.get('_duplicates', []):
            suggestion_ids.add(suggestion.id)
            suggestion.status = 'deleted'; suggestion.proposal = {}; suggestion.result = {'status': 'deleted'}; suggestion.handled_at = now
    def references(value):
        if isinstance(value, list): return any(references(v) for v in value)
        if not isinstance(value, dict): return False
        if value.get('session_id') == row.id or value.get('target_session_id') == row.id: return True
        if value.get('id') == row.id and 'planned_date' in value: return True
        if value.get('message_id') in blocked or value.get('suggestion_id') in suggestion_ids: return True
        if blocked.intersection(value.get('_memory_source_ids', [])): return True
        return any(references(v) for v in value.values())
    for cached in db.scalars(select(OperationReceipt)):
        if references(cached.response): cached.response = {'error': True, 'reply': '相关任务已永久删除，此操作不能重放。', 'deleted': True}
    for model, column in ((SessionMessage, SessionMessage.calendar_session_id), (SessionExecution, SessionExecution.calendar_session_id), (LifecycleEvent, LifecycleEvent.session_id)):
        db.execute(delete(model).where(column == row.id))
    row.title = '已删除任务'; row.session_goal = ''; row.agenda_json = []
    row.planned_date = None; row.deadline_date = None; row.start_at = None; row.end_at = None
    row.topic_id = None; row.continued_from_session_id = None; row.outcome = None; row.close_reason = None
    row.migration_metadata = {'permanently_deleted_at': now.isoformat()}
    row.version += 1; row.updated_at = now
    return {'status': 'deleted', 'session_id': row.id}


def classify_activity(title):
    # Explicit type wins; conservative fallback for manual input and old records.
    if re.search(r'作业|学习|leetcode|探讨|讨论|研究|复习|练习|实现|开发|调试|assignment|homework', title, re.I): return 'task'
    if re.search(r'吃饭|午饭|晚饭|早饭|聚餐|出去玩|出游|旅游|旅行|散步|约会|接人|接女朋友|接男朋友|看电影|看医生|就诊|聚会', title): return 'arrangement'
    return 'task'


def activity_type(row):
    return (row.migration_metadata or {}).get('activity_type') or classify_activity(row.title)


def classify_existing(factory):
    with factory() as db:
        for row in db.scalars(select(CalendarSession)):
            if not is_deleted(row) and 'activity_type' not in (row.migration_metadata or {}):
                row.migration_metadata = dict(row.migration_metadata or {}) | {'activity_type': classify_activity(row.title)}
        db.commit()
