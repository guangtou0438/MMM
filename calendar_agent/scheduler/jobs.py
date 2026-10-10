"""APScheduler is the sole production trigger for memory writes."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from ..config import DISPLAY_TIMEZONE
from ..models import Notification, Subtask, Task, WorkSession, utc_now
from ..memory import rollup


def scan_due(factory, now=None):
    now = now or utc_now()
    with factory() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        ids = set(db.scalars(select(Notification.subtask_id)))
        started = select(WorkSession.subtask_id)
        steps = db.scalars(select(Subtask).join(Task).where(Subtask.status == 'pending', Subtask.slot_start_utc <= now, Task.status != 'cancelled', Subtask.id.not_in(started)))
        for step in steps:
            if step.id not in ids: db.add(Notification(subtask_id=step.id, created_at=now))
        db.commit()


def run_rollup(factory, kind, now=None):
    now = now or utc_now(); day = now.astimezone(ZoneInfo(DISPLAY_TIMEZONE)).date()
    with factory() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        if kind == 'daily':
            rollup.daily(db, day - timedelta(days=1), now)
            rollup.daily(db, day, now)
        elif kind == 'weekly':
            monday = day - timedelta(days=day.weekday())
            rollup.weekly(db, monday - timedelta(days=7), now)
            rollup.weekly(db, monday, now)
        elif kind == 'monthly':
            previous = day.replace(day=1) - timedelta(days=1)
            rollup.monthly(db, previous.strftime('%Y-%m'), now)
            rollup.monthly(db, day.strftime('%Y-%m'), now)
        elif kind == 'yearly':
            rollup.yearly(db, day.year - 1 if day.month == 1 else day.year, now)
            if day.month == 1: rollup.yearly(db, day.year, now)
        else: raise ValueError('Unknown rollup kind')
        db.commit()


def build_scheduler(factory):
    scheduler = BackgroundScheduler(timezone=DISPLAY_TIMEZONE, job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 3600})
    scheduler.add_job(scan_due, 'interval', seconds=60, args=[factory], id='due_reminders')
    for kind, schedule in [
        ('daily', {'hour':23, 'minute':50}),
        ('weekly', {'day_of_week':'sun', 'hour':23, 'minute':55}),
        ('monthly', {'day':'last', 'hour':23, 'minute':58}),
        ('yearly', {'month':'1,4,7,10', 'day':1, 'hour':0, 'minute':10}),
    ]:
        scheduler.add_job(run_rollup, CronTrigger(timezone=DISPLAY_TIMEZONE, **schedule), args=[factory, kind], id=kind)
    return scheduler
