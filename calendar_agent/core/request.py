"""Resolve dates and conversational continuations in Python, never in the model."""
import math
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from ..config import DISPLAY_TIMEZONE
from .time import resolve_local

DATE = r"(?:[一二三四五六七八九十\d]{1,3})月\s*(?:[一二三四五六七八九十零\d]{1,3})(?:[号日])?(?![零一二三四五六七八九十\d])|20\d{2}-\d{2}-\d{2}|(?:下下周|下周|本周|这周|周)[一二三四五六日天]|大后天|后天|明天|今天"
CLOCK = r"(上午|早上|下午|晚上|傍晚|中午|凌晨)?\s*([零一二两三四五六七八九十\d]{1,3})点(?:(\d{1,2})分?|半)?"


def day_of(text, now):
    match = re.search(DATE, text)
    if not match: return None
    value = match[0]
    today = now.astimezone(ZoneInfo(DISPLAY_TIMEZONE)).date()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        return datetime.strptime(value, "%Y-%m-%d").date()
    if "月" in value:
        month, day = re.fullmatch(r"(.+)月\s*([一二三四五六七八九十零\d]+)(?:[号日])?", value).groups()
        return today.replace(month=number(month), day=number(day))
    if "周" in value:
        weekday = "一二三四五六日".index(value[-1].replace("天", "日"))
        offset = 14 if value.startswith("下下周") else 7 if value.startswith("下周") else 0
        return today - timedelta(days=today.weekday()) + timedelta(days=weekday + offset)
    return today + timedelta(days={"今天": 0, "明天": 1, "后天": 2, "大后天": 3}[value])


def number(value):
    if value.isdigit(): return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if "十" in value:
        a, b = value.split("十")
        return (digits[a] if a else 1) * 10 + (digits[b] if b else 0)
    return digits[value]


def continue_message(pending, current, history):
    """Only attach time/budget-only answers; a new named activity starts fresh."""
    reference = re.fullmatch(r"\s*(.{1,40}?)(?:呢|安排了吗|排好了吗)[？?。\s]*", current)
    if reference:
        subject = reference[1].strip()
        for entry in reversed(history):
            if entry["role"] == "user" and subject in entry["content"] and entry["content"] != current:
                return entry["content"], False, subject
        return current, False, subject
    confirmation = re.fullmatch(r"\s*(?:是的|是|好的|好|对|可以|确定|确认|帮我写在日程里|写在日程里|帮我记在日历里|加入日程|安排到日历|就这个)[。！!\s]*", current)
    if confirmation and pending:
        # Recover an interrupted clarification from older versions as well.
        if not re.search(DATE, pending):
            for entry in reversed(history):
                if entry["role"] == "user" and re.search(DATE, entry["content"]):
                    pending = entry["content"]
                    break
        return pending, True, None
    remainder = re.sub(DATE, "", current)
    remainder = re.sub(r"\d{1,2}:\d{2}|" + CLOCK, "", remainder)
    remainder = re.sub(r"(?:预估|预计|大约|需要|耗时|估计|持续)?\s*\d+(?:\.\d+)?\s*(?:个?小时|分钟|hours?|h\b)", "", remainder, flags=re.I)
    remainder = re.sub(r"[\s，,。.!！?？：:]|改为|改到|改成|截止|之前|以前|完成|提交|交|上午|下午|晚上|早上|中午|是|的|吧", "", remainder)
    attach = bool(pending) and not remainder
    return "\n".join((pending, current)) if attach else current, attach, None


def resolve_request(message, now):
    original = message
    clauses = [c.strip() for c in re.split(r"[，,。；;\n]", message) if c.strip()]
    excluded = []
    primary = []
    for clause in clauses:
        unavailable = re.search(r"没时间|没空|不方便|不能安排", clause) or (len([c for c in clauses if re.search(DATE, c)]) > 1 and re.search(r"出远门|上课", clause))
        if unavailable and day_of(clause, now):
            day = day_of(clause, now)
            # Missing hours means conservatively exclude this day for this request.
            excluded.append((resolve_local(f"{day}T00:00"), resolve_local(f"{day + timedelta(days=1)}T00:00")))
        else:
            primary.append(clause)
    dated = [c for c in primary if re.search(DATE, c)]
    selected = dated[-1] if dated else ""
    day = day_of(selected, now)
    if day is None:
        return None, "请告诉我具体任务和日期，例如明天学习 LeetCode，或下周五交 COMP90054 assignment。"
    if day < now.astimezone(ZoneInfo(DISPLAY_TIMEZONE)).date():
        return None, "这个日期已经过去，请选择未来的日期。"
    # Later clock-only answers override the initial ambiguous clock.
    clock_clauses = [c for c in primary if re.search(r"\d{1,2}:\d{2}|" + CLOCK, c)]
    clock_text = re.sub(DATE, "", clock_clauses[-1] if clock_clauses else selected)
    time_match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", clock_text)
    chinese_time = re.search(CLOCK, clock_text)
    explicit_clock = bool(time_match or chinese_time)
    deadline_mode = bool(re.search(r"截止|之前|以前|要交|提交|\bassignment\b|作业|交(?:[，,。\s]|$)", original, re.I)) or (explicit_clock and "完成" in original)
    event = not deadline_mode and (explicit_clock or bool(re.search(r"标注|记到|记在|出去玩|出游|吃饭|吃午饭|吃晚饭|聚餐|约会|开会|预约|上课|出远门", original)))
    def ask_time(question, choices=None):
        if choices is None:
            choices = ["11:30", "12:00", "12:30", "13:00"] if re.search(r"中午|午饭", original) else ["18:00", "18:30", "19:00"] if re.search(r"晚上|晚饭", original) else ["09:00", "14:00", "18:00"]
        return {"clarification": {"kind": "event_time", "question": question, "date": day.isoformat(),
                "timezone": DISPLAY_TIMEZONE, "time_options": choices, "duration_min": 60}}, question
    if chinese_time and not time_match:
        hour = number(chinese_time[2]); minute = int(chinese_time[3] or (30 if "半" in chinese_time[0] else 0))
        if not chinese_time[1] and 1 <= hour <= 12:
            return ask_time(f"关于「{selected}」：{chinese_time[2]}点是上午还是下午／晚上？", [f"{hour % 12:02d}:{minute:02d}", f"{hour % 12 + 12:02d}:{minute:02d}"])
        if chinese_time[1] in ("下午", "晚上", "傍晚", "中午") and hour < 12: hour += 12
        if chinese_time[1] == "凌晨" and hour == 12: hour = 0
        wall_time = f"{hour:02d}:{minute:02d}"
    elif time_match:
        wall_time = f"{int(time_match[1]):02d}:{int(time_match[2]):02d}"
    else:
        wall_time = "23:59"
        if event or re.search(r"上午|下午|晚上|中午|凌晨", original):
            return ask_time(f"关于「{selected}」：具体几点开始、持续多久？请选择时间，或填写自定义时间。")
    estimates = list(re.finditer(r"(?:预估|预计|大约|需要|耗时|估计|持续)?\s*(\d+(?:\.\d+)?)\s*(个?小时|分钟|hours?|h\b)", original, re.I))
    estimate = estimates[-1] if estimates else None
    minutes = (float(estimate[1]) * (1 if estimate[2] == "分钟" else 60)) if estimate else (60 if event else float(os.getenv("CALENDAR_DEFAULT_ESTIMATED_MINUTES", "270")))
    if not math.isfinite(minutes) or minutes <= 0:
        return None, "请提供大于零的时长。"
    minutes = math.ceil(minutes)
    point = resolve_local(f"{day}T{wall_time}")
    start = point if event else max(now, resolve_local(f"{day}T00:00")) if not deadline_mode else now
    deadline = point + timedelta(minutes=math.ceil(minutes)) if event else point
    if deadline <= now or (event and start < now):
        return None, "这个时间已经过去，请提供未来的日期或时刻。"
    notes = []
    if not explicit_clock: notes.append("未指定截止时刻，按当天 23:59；可随时补充具体时间。")
    if excluded: notes.append("本次规划已避开你提到的不可用日期（未给具体时段，暂按整天避开）。")
    return {"deadline": deadline, "start": start, "excluded": excluded, "mode": "event" if event else "plan",
            "base_minutes": math.ceil(minutes), "deadline_note": "\n".join(notes),
            "estimate_note": "" if estimate else f"未指定时长，暂按 {minutes:g} 分钟{'标注' if event else '的可调整预算规划'}。"}, None


def duration_options(minutes):
    """Integer-minute distributions, without arbitrary duration limits."""
    options = []
    for n in (3, 4, 5, 1, 2, 8, 14):
        if minutes < n: continue
        base, remainder = divmod(minutes, n)
        options.append([base + (i < remainder) for i in range(n)])
    return options
