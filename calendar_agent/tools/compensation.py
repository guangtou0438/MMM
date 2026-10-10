"""Server-owned inverse operations. Never exposed to the model registry."""
from sqlalchemy import text
from ..core import date_tasks
from ..api.calendar_session_schemas import SessionUpdate


def snapshot(executor, tool, arguments):
    if tool not in {'update_task', 'complete_task', 'archive_task', 'restore_task'}:
        return None
    with executor.factory() as db:
        return date_tasks.serialize(date_tasks.get_session(db, arguments['session_id']))


def inverse(tool, before, result):
    if not isinstance(result, dict) or 'id' not in result or 'version' not in result:
        return None
    value = {'session_id': result['id'], 'version': result['version']}
    if tool == 'create_task': return value | {'action': 'archive'}
    if tool == 'update_task':
        return value | {'action': 'update', 'changes': {key: before[key] for key in ('title', 'session_goal', 'planned_date', 'deadline_date', 'agenda', 'activity_type')}}
    actions = {'complete_task': 'reopen', 'archive_task': 'restore', 'restore_task': 'archive'}
    return value | {'action': actions[tool]} if tool in actions else None


def execute(executor, compensation, request_id):
    with executor.factory() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        cached = date_tasks.receipt(db, 'undo:'+request_id, compensation)
        if cached is not None: return cached
        row = date_tasks.get_session(db, compensation['session_id'])
        if compensation['action'] == 'update':
            row = date_tasks.update(db, row, SessionUpdate(request_id=request_id, version=compensation['version'], **compensation['changes']), executor.clock())
        else:
            row = date_tasks.lifecycle(db, row, compensation['action'], compensation['version'], executor.clock())
        result = date_tasks.serialize(row)
        date_tasks.save_receipt(db, 'undo:'+request_id, compensation, result, executor.clock()); db.commit()
        return result
