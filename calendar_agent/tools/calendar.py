from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from ..api.calendar_session_schemas import CalendarSessionCreate, SessionUpdate
from ..core import date_tasks
from ..models import CalendarSession


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ReadCalendar(Arguments):
    start: date | None = None
    end: date | None = None


class Target(Arguments):
    session_id: int = Field(gt=0)


class Update(Target):
    changes: dict


SCHEMAS = {'read_calendar': ReadCalendar, 'create_task': CalendarSessionCreate,
           'update_task': Update, 'complete_task': Target, 'archive_task': Target, 'restore_task': Target}


def execute(factory, name, arguments, request_id, version, now):
    values = dict(arguments)
    if name == 'create_task':
        values['request_id'] = request_id
    payload = SCHEMAS[name].model_validate(values)
    with factory() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        if name == 'read_calendar':
            query = select(CalendarSession).where(CalendarSession.archived_at.is_(None)).limit(100)
            rows = [date_tasks.serialize(r) for r in db.scalars(query)
                    if not date_tasks.is_deleted(r) and (not payload.start or r.planned_date and r.planned_date >= payload.start)
                    and (not payload.end or r.planned_date and r.planned_date <= payload.end)]
            return {'tasks': rows}
        value = {'tool': name, 'arguments': arguments, 'version': version}
        key = 'tool:' + request_id
        cached = date_tasks.receipt(db, key, value)
        if cached is not None:
            return cached
        if name == 'create_task':
            row = date_tasks.create(db, payload, now)
        else:
            if version is None:
                raise ValueError('写操作必须提供 expected_version')
            row = date_tasks.get_session(db, payload.session_id)
            if name == 'update_task':
                if {'version', 'request_id'} & payload.changes.keys():
                    raise ValueError('版本与幂等标识由服务端提供')
                update = SessionUpdate(request_id=request_id, version=version, **payload.changes)
                row = date_tasks.update(db, row, update, now)
            else:
                row = date_tasks.lifecycle(db, row, name.removesuffix('_task'), version, now)
        result = date_tasks.serialize(row)
        date_tasks.save_receipt(db, key, value, result, now)
        db.commit()
        return result
