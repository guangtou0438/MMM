"""Read-only selection of summary layers. Never performs a rollup."""
from sqlalchemy import select
import json
from zoneinfo import ZoneInfo
from ..config import DISPLAY_TIMEZONE
from ..models import MemoryDaily, MemoryWeekly, MemoryYearly, Subtask


def planning_context(db, now, slots):
    profile = db.scalar(select(MemoryYearly).where(MemoryYearly.year <= now.year).order_by(MemoryYearly.year.desc()))
    weekly = db.scalars(select(MemoryWeekly).order_by(MemoryWeekly.week_start.desc()).limit(4)).all()
    return {"yearly_profile": profile.profile_json if profile else {},
            "weekly_summaries": [summary_text(w.summary)[:1800] for w in weekly], "free_slots": slots}


def summary_text(value):
    try: return json.loads(value).get("summary", value)
    except (ValueError, AttributeError): return value


def today_context(db, start, end):
    steps = db.scalars(select(Subtask).where(Subtask.slot_start_utc >= start, Subtask.slot_start_utc < end)).all()
    daily = db.scalar(select(MemoryDaily).where(MemoryDaily.date <= start.astimezone(ZoneInfo(DISPLAY_TIMEZONE)).date()).order_by(MemoryDaily.date.desc()))
    return {"subtasks": [{"title": s.title, "slot_start": s.slot_start_utc.isoformat(), "status": s.status} for s in steps],
            "daily_summary": (daily.content_json.get("summary", "") if daily else "")}
