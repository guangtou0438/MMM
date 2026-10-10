"""Deterministic rollups. Only scheduler/jobs.py invokes these in production.

Summaries contain aggregate counts, never raw conversations. Rebuilding a period
is idempotent and repairs sessions recorded after the nightly snapshot.
"""
import json
from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from ..config import DISPLAY_TIMEZONE
from ..core.time import resolve_local
from ..models import MemoryDaily, MemoryWeekly, MemoryMonthly, MemoryYearly, WorkSession, Subtask

ZONE = ZoneInfo(DISPLAY_TIMEZONE)

def bounds(day):
    return resolve_local(f'{day}T00:00'), resolve_local(f'{day + timedelta(days=1)}T00:00')

def save(db, model, key, **values):
    row = db.get(model, key)
    if row:
        for name, value in values.items(): setattr(row, name, value)
    else:
        pk = list(model.__table__.primary_key)[0].name
        row = model(**{pk: key}, **values); db.add(row)
    db.flush()
    return row

def daily(db, day, now):
    start, end = bounds(day)
    sessions = list(db.scalars(select(WorkSession).where(WorkSession.ended_at >= start, WorkSession.ended_at < end, WorkSession.actual_minutes.is_not(None))))
    metrics = {'sessions': len(sessions), 'actual_minutes': sum(s.actual_minutes for s in sessions), 'completed_steps': 0, 'estimated_minutes': 0, 'completed_actual_minutes': 0, 'hours': {}}
    for session in sessions:
        if session.outcome != 'completed': continue
        step = db.get(Subtask, session.subtask_id)
        if not step or step.status != 'completed': continue
        # One final completion per step; partial sessions contribute to its ratio.
        metrics['completed_steps'] += 1
        metrics['estimated_minutes'] += step.duration_min
        metrics['completed_actual_minutes'] += step.actual_minutes or 0
        hour = str(session.started_at.astimezone(ZONE).hour)
        bucket = metrics['hours'].setdefault(hour, {'samples': 0, 'actual': 0, 'estimated': 0})
        bucket['samples'] += 1; bucket['actual'] += step.actual_minutes or 0; bucket['estimated'] += step.duration_min
    summary = f"{day}：结束 {len(sessions)} 次会话，实际投入 {metrics['actual_minutes']} 分钟，完成 {metrics['completed_steps']} 个步骤。"
    return save(db, MemoryDaily, day, content_json={'summary': summary, 'metrics': metrics}, created_at=now)

def weekly(db, week_start, now):
    days = {}
    for i in range(7):
        day = week_start + timedelta(days=i)
        if day > now.astimezone(ZONE).date(): break
        row = daily(db, day, now)
        days[str(day)] = row.content_json['metrics']
    return save(db, MemoryWeekly, week_start, summary=json.dumps({'summary': f'{week_start} 当周：' + describe(days), 'days': days}, ensure_ascii=False), created_at=now)

def describe(days):
    return f"完成 {sum(v['completed_steps'] for v in days.values())} 个步骤，实际投入 {sum(v['actual_minutes'] for v in days.values())} 分钟。"

def monthly(db, month, now):
    from datetime import date
    first = date.fromisoformat(month + '-01')
    end = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    cursor = first - timedelta(days=first.weekday())
    days = {}
    while cursor < end:
        week = json.loads(weekly(db, cursor, now).summary)
        days.update({d: m for d,m in week['days'].items() if d.startswith(month)})
        cursor += timedelta(days=7)
    totals = {'completed_steps': 0, 'estimated_minutes': 0, 'completed_actual_minutes': 0, 'actual_minutes': 0, 'hours': {}}
    for metrics in days.values():
        for key in ('completed_steps', 'estimated_minutes', 'completed_actual_minutes', 'actual_minutes'): totals[key] += metrics[key]
        for hour, bucket in metrics['hours'].items():
            target = totals['hours'].setdefault(hour, {'samples': 0, 'actual': 0, 'estimated': 0})
            for key in bucket: target[key] += bucket[key]
    return save(db, MemoryMonthly, month, summary=json.dumps({'summary': f'{month}：' + describe(days), 'metrics': totals}, ensure_ascii=False), created_at=now)

def yearly(db, year, now):
    actual = estimated = samples = 0
    hours = {}
    last_month = now.astimezone(ZONE).month if year == now.astimezone(ZONE).year else 12
    for month in range(1, last_month + 1):
        metrics = json.loads(monthly(db, f'{year}-{month:02d}', now).summary)['metrics']
        actual += metrics['completed_actual_minutes']; estimated += metrics['estimated_minutes']; samples += metrics['completed_steps']
        for hour, bucket in metrics['hours'].items():
            target = hours.setdefault(hour, {'samples': 0, 'actual': 0, 'estimated': 0})
            for key in bucket: target[key] += bucket[key]
    ratio = actual / estimated if estimated else None
    efficient = sorted(({'hour': int(h), 'samples': v['samples'], 'ratio': round(v['actual']/v['estimated'], 3)} for h,v in hours.items() if v['samples'] >= 3), key=lambda v: v['ratio'])[:3]
    profile = {'summary': f'{year} 年已完成 {samples} 个步骤；' + (f'实际/预估耗时比 {ratio:.2f}。' if ratio is not None else '暂无实际耗时样本。'),
               'underestimation_factor': max(1.0, min(4.0, ratio)) if ratio is not None else 1.0,
               'observed_ratio': ratio, 'sample_count': samples, 'efficient_hours': efficient,
               'efficiency_note': '按已完成步骤的实际/预估耗时比排序；每个时段至少 3 个样本，仅供参考。'}
    return save(db, MemoryYearly, year, profile_json=profile, updated_at=now)
