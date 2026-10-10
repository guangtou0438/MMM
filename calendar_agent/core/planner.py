import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select, text

from ..config import DISPLAY_TIMEZONE
from ..llm.client import Client, ModelError
from ..llm.contract import PlanOutput
from ..llm.prompts import SYSTEM, correction, user_payload
from ..memory.context import planning_context, today_context
from ..models import LegacyTask, PlanAttempt, PlanningConversation, PlanningRequest, Subtask, Task, utc_now
from .request import continue_message, duration_options, resolve_request
from .slots import free_slots, stamp
from .time import resolve_local
from .validator import validate_plan

log = logging.getLogger("mmm.planner")


def clarify(question):
    return {"intent": "clarify", "reply": question, "created_tasks": []}


def plan_request(factory, payload, client=None, clock=utc_now):
    request_id = payload.request_id or str(uuid.uuid4())
    conversation_id = payload.conversation_id or str(uuid.uuid4())
    with factory() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        previous = db.get(PlanningRequest, request_id)
        if previous:
            if previous.response_json: return previous.response_json
            return {**clarify("上一请求仍在处理中，请稍后再查看。"), "request_id": request_id, "conversation_id": conversation_id}
        db.add(PlanningRequest(id=request_id))
        thread = db.get(PlanningConversation, conversation_id)
        if not thread:
            thread = PlanningConversation(id=conversation_id, messages_json=[], pending_message="")
            db.add(thread)
        message, continuing, reference = continue_message(thread.pending_message, payload.message, thread.messages_json)
        recent_history = list(thread.messages_json)
        if len(message) > 10000:
            message = payload.message
        legacy_source_id = payload.legacy_task_id or (thread.legacy_task_id if continuing else None)
        thread.legacy_task_id = legacy_source_id
        legacy = db.get(LegacyTask, legacy_source_id) if legacy_source_id else None
        if legacy_source_id and not legacy:
            db.rollback()
            return {**clarify("找不到该旧任务，请刷新后重试。"), "conversation_id": conversation_id, "request_id": request_id}
        if legacy:
            message = f"任务名称：{legacy.snapshot_json['title']}\n" + message
        thread.messages_json = [*thread.messages_json, {"role": "user", "content": payload.message, "created_at": stamp(clock())}]
        db.commit()

    def finish(result):
        result.update(conversation_id=conversation_id, request_id=request_id)
        with factory() as db:
            row = db.get(PlanningRequest, request_id)
            # A successful plan + cached response are committed atomically below.
            if row.response_json: result = row.response_json
            else: row.response_json = result
            thread = db.get(PlanningConversation, conversation_id)
            thread.messages_json = [*thread.messages_json, {"role": "assistant", "content": result["reply"], "created_at": stamp(clock()), "clarification": result.get("clarification")}]
            thread.pending_message = message if result["intent"] == "clarify" else ""
            if result["intent"] != "clarify": thread.legacy_task_id = None
            thread.updated_at = clock()
            db.commit()
        return result

    from .conversation import is_general, converse
    if not continuing and not reference and not payload.legacy_task_id and is_general(payload.message):
        return finish(converse(factory, payload.message, recent_history, clock(), client))

    if reference:
        with factory() as db:
            tasks = list(db.scalars(select(Task).where(Task.title.contains(reference, autoescape=True), Task.status != "cancelled").order_by(Task.id.desc()).limit(5)))
            if tasks:
                lines = []
                for task in tasks:
                    steps = list(db.scalars(select(Subtask).where(Subtask.task_id == task.id).order_by(Subtask.slot_start_utc)))
                    times = "、".join(s.slot_start_utc.astimezone(ZoneInfo(DISPLAY_TIMEZONE)).strftime("%m-%d %H:%M") for s in steps if s.slot_start_utc)
                    lines.append(f"「{task.title}」：{times or '尚未排期'}（墨尔本时间）")
                return finish({"intent": "query", "reply": "\n".join(lines), "created_tasks": []})

    if "今天" in payload.message and any(x in payload.message for x in ("做什么", "安排了什么", "有什么任务")):
        day = clock().astimezone(ZoneInfo(DISPLAY_TIMEZONE)).date()
        start = resolve_local(f"{day}T00:00")
        end = resolve_local(f"{day + timedelta(days=1)}T00:00")
        with factory() as db: context = today_context(db, start, end)
        reply = "\n".join(f"{s['title']} · {datetime.fromisoformat(s['slot_start']).astimezone(ZoneInfo(DISPLAY_TIMEZONE)):%H:%M}" for s in context["subtasks"])
        return finish({"intent": "query", "reply": reply or "今天还没有已排期的步骤。告诉我任务和截止日期，我来安排。", "created_tasks": []})
    try:
        resolved, question = resolve_request(message, clock())
        if question:
            detail = (resolved or {}).get("clarification")
            if detail:
                # Freeze relative dates while waiting, including across midnight/restarts.
                from .request import DATE, day_of
                import re
                message = re.sub(DATE, lambda m: day_of(m[0], clock()).isoformat(), message)
            return finish({**clarify(question), "clarification": detail})
        with factory() as db:
            context = planning_context(db, clock().astimezone(ZoneInfo(DISPLAY_TIMEZONE)), [])
        factor = context["yearly_profile"].get("underestimation_factor", 1.0)
        if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or not 1 <= factor <= 4:
            return finish(clarify("画像中的耗时低估系数无效，需要检查记忆数据后再规划。"))
        minutes = math.ceil(resolved["base_minutes"] * (1 if resolved["mode"] == "event" else factor))
        options = [[minutes]] if resolved["mode"] == "event" else duration_options(minutes)
        if not options: return finish(clarify("调整后的工作量过大，请先拆成更小的目标。"))
        deadline = resolved["deadline"]
        values = {"deadline": stamp(deadline), "estimated_hours": minutes / 60, "duration_options": options,
                  "underestimation_factor": factor, "mode": resolved["mode"]}
        from ..models import UserFact
        with factory() as db: values["user_facts"] = {f.key:f.value for f in db.scalars(select(UserFact))}
    except (ValueError, TypeError, KeyError) as error:
        return finish(clarify("无法确定可用时间：" + str(error)))

    def available(db):
        slots = free_slots(max(clock(), resolved["start"]), deadline, db, excluded=resolved["excluded"], chunk_minutes=max(minutes, 120),
                           awake=("00:00", "24:00") if resolved["mode"] == "event" else None)
        if resolved["mode"] == "event":
            slots = [s for s in slots if s["slot_start"] == stamp(resolved["start"]) and s["capacity_min"] >= minutes]
        for slot in slots:
            slot["local_hour"] = datetime.fromisoformat(slot["slot_start"]).astimezone(ZoneInfo(DISPLAY_TIMEZONE)).hour
        return slots[:180]

    client = client or Client()
    messages = [{"role": "system", "content": SYSTEM}]
    for attempt in range(1, 4):  # Initial call + at most two correction retries.
        errors = []
        try:
            with factory() as db:
                slots = available(db)
                context = planning_context(db, clock().astimezone(ZoneInfo(DISPLAY_TIMEZONE)), slots)
            if not slots or sum(s["capacity_min"] for s in slots) < minutes:
                return finish(clarify("指定时段不可用或截止前空闲时间不足，请调整时间或工作量。"))
            user = {"role": "user", "content": user_payload(message, context, values)}
            if len(messages) == 1: messages.append(user)
            else: messages[1] = user
            raw = client.complete(messages)
            plan = PlanOutput.model_validate_json(raw)
            errors = validate_plan(plan, slots, deadline, minutes, options)
            if not errors and plan.intent in ("clarify", "query"):
                if plan.intent == "query":
                    errors = ["用户请求安排任务；若信息不足请返回 clarify，而不是 query。"]
                else:
                    return finish(clarify(plan.clarify_question))
            if not errors:
                with factory() as db:
                    db.execute(text("BEGIN IMMEDIATE"))
                    fresh = available(db)
                    errors = validate_plan(plan, fresh, deadline, minutes, options)
                    if not errors:
                        legacy = db.get(LegacyTask, legacy_source_id) if legacy_source_id else None
                        if legacy and legacy.linked_task_id:
                            raise ValueError("这个旧任务已关联日历任务，请先查看现有安排。")
                        task = Task(title=plan.goal.title, deadline_utc=deadline, estimated_minutes=minutes)
                        db.add(task); db.flush()
                        inserted = []
                        for sub in plan.subtasks:
                            row = Subtask(task_id=task.id, title=sub.title, slot_start_utc=sub.slot_start, duration_min=sub.duration_min,
                                          depends_on=inserted[sub.depends_on].id if sub.depends_on is not None else None)
                            db.add(row); db.flush(); inserted.append(row)
                        if legacy: legacy.linked_task_id = task.id
                        from ..api.routes import serialize_task
                        note = "\n".join(x for x in (resolved["deadline_note"], resolved["estimate_note"]) if x)
                        times = "\n".join(f"{s.slot_start_utc.astimezone(ZoneInfo(DISPLAY_TIMEZONE)):%m-%d %H:%M} · {s.title}（{s.duration_min} 分钟）" for s in inserted)
                        result = {"intent": plan.intent, "reply": f"已为「{task.title}」安排 {len(inserted)} {'条日程' if resolved['mode'] == 'event' else '个步骤'}，共 {minutes} 分钟。\n{times}\n以上为墨尔本时间。\n{note}".strip(),
                                  "created_tasks": [serialize_task(db, task)], "request_id": request_id, "conversation_id": conversation_id}
                        db.get(PlanningRequest, request_id).response_json = result
                        db.add(PlanAttempt(request_id=request_id, attempt=attempt, errors_json=[]))
                        db.commit()
                if not errors: return finish(result)
            messages.append({"role": "assistant", "content": raw[:40000]})
        except ValidationError as error:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors(include_input=False, include_url=False)]
        except (ModelError, ValueError, TypeError, KeyError) as error:
            errors = [str(error)]
        with factory() as db:
            db.add(PlanAttempt(request_id=request_id, attempt=attempt, errors_json=errors)); db.commit()
        log.warning("request=%s attempt=%d validation_errors=%s", request_id, attempt, errors)
        messages.append({"role": "user", "content": correction(errors)})
    return finish(clarify("计划连续校验失败，未保存任何排期。请补充更明确的任务、工作量或截止时间后重试。最后错误：" + "; ".join(errors)[:500]))
