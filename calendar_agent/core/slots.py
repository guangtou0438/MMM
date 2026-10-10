"""All interval arithmetic happens here, never in the LLM."""
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from ..config import DISPLAY_TIMEZONE
from ..models import Subtask
from .time import resolve_local


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def free_slots(now, deadline, db, *, fixed=None, awake=None, excluded=(), chunk_minutes=120):
    """Disjoint slots, capped at 120 minutes, up to the actual deadline.

    CALENDAR_FIXED_SCHEDULES: [{weekdays:[0..6], start:'09:00', end:'10:00'}].
    Fixed events must be same-day; awake defaults to 08:00–22:00 Melbourne.
    Configuration/DST errors fail explicitly rather than exposing false slots.
    """
    if now.tzinfo is None or deadline.tzinfo is None:
        raise ValueError("free_slots 需要带时区的时间。")
    now, deadline = now.astimezone(timezone.utc), deadline.astimezone(timezone.utc)
    if deadline <= now:
        return []
    if deadline - now > timedelta(days=90):
        raise ValueError("目前支持 90 天内的规划，请缩小期限。")
    fixed = json.loads(os.getenv("CALENDAR_FIXED_SCHEDULES", "[]")) if fixed is None else fixed
    if not isinstance(fixed, list):
        raise ValueError("固定日程配置必须是列表。")
    awake = awake or (os.getenv("CALENDAR_AWAKE_START", "08:00"), os.getenv("CALENDAR_AWAKE_END", "22:00"))
    occupied = [(s.slot_start_utc, s.slot_start_utc + timedelta(minutes=s.duration_min))
                for s in db.scalars(select(Subtask).where(Subtask.slot_start_utc.is_not(None), Subtask.status != "skipped"))]
    zone = ZoneInfo(DISPLAY_TIMEZONE)
    day = now.astimezone(zone).date()
    last = deadline.astimezone(zone).date()
    result = []
    while day <= last:
        begin = resolve_local(f"{day.isoformat()}T{awake[0]}")
        finish = resolve_local(f"{day + timedelta(days=1)}T00:00") if awake[1] == "24:00" else resolve_local(f"{day.isoformat()}T{awake[1]}")
        if finish <= begin:
            raise ValueError("清醒时段的结束必须晚于开始。")
        blocked = [*occupied, *excluded]
        for rule in fixed:
            if not isinstance(rule, dict) or set(rule) != {"weekdays", "start", "end"}:
                raise ValueError("固定日程需要 weekdays/start/end。")
            if not isinstance(rule["weekdays"], list) or any(type(x) is not int or not 0 <= x <= 6 for x in rule["weekdays"]):
                raise ValueError("weekdays 必须是 0（周一）至 6 的整数列表。")
            if day.weekday() in rule["weekdays"]:
                a, b = [resolve_local(f"{day.isoformat()}T{rule[k]}") for k in ("start", "end")]
                if b <= a:
                    raise ValueError("固定日程结束必须晚于开始；跨日请拆成两条。")
                blocked.append((a, b))
        begin, finish = max(begin, now), min(finish, deadline)
        # Round up to a minute so choices don't contain arbitrary seconds.
        if begin.second or begin.microsecond:
            begin = begin.replace(second=0, microsecond=0) + timedelta(minutes=1)
        intervals = [(begin, finish)] if begin < finish else []
        for a, b in sorted(blocked):
            fragments = []
            for start, end in intervals:
                if b <= start or a >= end:
                    fragments.append((start, end))
                else:
                    if start < a: fragments.append((start, a))
                    if b < end: fragments.append((b, end))
            intervals = fragments
        for start, end in intervals:
            while end - start >= timedelta(minutes=1):
                minutes = min(chunk_minutes, int((end - start).total_seconds() // 60))
                if result and result[-1]["end"] == stamp(start) and result[-1]["capacity_min"] + minutes <= chunk_minutes:
                    result[-1]["end"] = stamp(start + timedelta(minutes=minutes))
                    result[-1]["capacity_min"] += minutes
                else:
                    result.append({"slot_start": stamp(start), "end": stamp(start + timedelta(minutes=minutes)), "capacity_min": minutes})
                start += timedelta(minutes=minutes)
        day += timedelta(days=1)
    return result
