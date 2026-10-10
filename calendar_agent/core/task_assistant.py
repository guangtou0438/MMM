import json
import logging
from sqlalchemy import select
from ..models import Task, TaskMessage, TaskNote, Subtask, LegacyTask, utc_now
from ..llm.client import Client, ModelError
from ..llm.contract import PlanOutput
from ..llm.prompts import TASK_SYSTEM, correction
from pydantic import ValidationError


def task_chat(factory, task_id, message, client=None):
    with factory() as db:
        task = db.get(Task, task_id)
        if not task: raise KeyError(task_id)
        steps = db.scalars(select(Subtask).where(Subtask.task_id == task_id)).all()
        old = db.scalar(select(LegacyTask).where(LegacyTask.linked_task_id == task_id))
        recent = list(reversed(db.scalars(select(TaskMessage).where(TaskMessage.task_id == task_id).order_by(TaskMessage.id.desc()).limit(6)).all()))
        notes = db.scalars(select(TaskNote).where(TaskNote.task_id == task_id).order_by(TaskNote.id.desc()).limit(3)).all()
        context = {"title": task.title, "steps": [{"title": s.title, "status": s.status} for s in steps],
                   "previous_summary": old.snapshot_json.get("summary") if old else None,
                   "progress_notes": [n.content for n in notes],
                   "recent_dialogue": [{"role": m.role, "content": m.content} for m in recent], "message": message}
        from .conversation import shared_context
        context["shared_context"] = shared_context(db, utc_now())
    messages = [{"role": "system", "content": TASK_SYSTEM}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    model = client or Client()
    for attempt in range(1, 4):
        try:
            result = PlanOutput.model_validate_json(model.complete(messages))
            if result.intent not in ("query", "clarify"):
                raise ValueError("任务对话不能创建计划，请使用 query/clarify")
            reply = result.clarify_question if result.intent == "clarify" else result.reasoning
            if not reply.strip(): raise ValueError("回答不能为空")
            break
        except (ValidationError, ValueError, ModelError) as error:
            detail = str(error)[:800]
            logging.getLogger("mmm.task_chat").warning("task=%s attempt=%s validation_error=%s", task_id, attempt, type(error).__name__)
            messages.append({"role": "user", "content": correction([detail])})
    else:
        reply = "对话生成未通过校验，请补充具体问题或检查模型配置后再试。本次没有修改排期。"
        result = None
    with factory() as db:
        db.add_all([TaskMessage(task_id=task_id, role="user", content=message), TaskMessage(task_id=task_id, role="assistant", content=reply)])
        db.commit()
    return {"reply": reply, "intent": result.intent if result else "clarify"}
