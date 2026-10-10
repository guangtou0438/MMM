"""General conversation with bounded, code-generated calendar context."""
import json
import re
from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from pydantic import ValidationError
from ..models import UserFact, Subtask, Task
from ..config import DISPLAY_TIMEZONE
from ..llm.client import Client, ModelError
from ..llm.contract import PlanOutput
from ..llm.prompts import GENERAL_SYSTEM, correction
from .request import DATE


def is_general(message):
    if "今天" in message and any(x in message for x in ("做什么", "安排了什么", "有什么任务")): return False
    if re.search(r'有什么安排|有哪些日程|忙不忙|解释|为什么|怎么理解|聊聊', message): return True
    if re.search(r'我的名字是|我叫|叫我|我叫什么|我的名字|你好|谢谢', message): return True
    if re.search(r'是什么|心情|感觉|你觉得|怎么样|为什么|怎么办', message): return True
    return not re.search(DATE + r'|安排|日程|规划|截止|作业|帮我排|标注|预计|预估|小时|分钟|\d{1,2}:\d{2}', message)


def shared_context(db, now):
    facts = {r.key:r.value for r in db.scalars(select(UserFact))}
    rows = db.execute(select(Subtask, Task).join(Task).where(Subtask.slot_start_utc >= now, Subtask.slot_start_utc < now+timedelta(days=7), Subtask.status.not_in(('completed','skipped')), Task.status != 'cancelled').order_by(Subtask.slot_start_utc).limit(8)).all()
    summary = '；'.join(f'{s.slot_start_utc.astimezone(ZoneInfo(DISPLAY_TIMEZONE)):%m-%d %H:%M} {t.title} / {s.title}，{s.duration_min} 分钟' for s,t in rows)
    return {'user_facts':facts, 'upcoming_summary':summary or '未来七天暂无已排期任务。', 'summary_scope':'未来七天最多八条，非完整日历', 'now_local':now.astimezone(ZoneInfo(DISPLAY_TIMEZONE)).isoformat()}


def converse(factory, message, history, now, client=None):
    name = re.fullmatch(r'\s*(?:我的名字是|我叫|叫我)\s*([^，。！？\n]{1,80})[。！!\s]*',message)
    with factory() as db:
        if name:
            value=name[1].strip(); row=db.get(UserFact,'name')
            if row: row.value=value; row.updated_at=now
            else: db.add(UserFact(key='name',value=value,updated_at=now))
            db.commit()
            return {'intent':'query','reply':f'记住了，你叫 {value}。之后的新对话也会使用这个名字。你可以直接聊天，需要安排日程时告诉我就好。','created_tasks':[]}
        context = shared_context(db, now)
    messages=[{'role':'system','content':GENERAL_SYSTEM},{'role':'user','content':json.dumps({'message':message,'recent_dialogue':history[-12:],'context':context},ensure_ascii=False)}]
    client=client or Client()
    for attempt in range(3):
        try:
            result=PlanOutput.model_validate_json(client.complete(messages))
            if result.intent not in ('query','clarify'): raise ValueError('普通对话只允许 query/clarify，不可声称已修改日历')
            reply=result.reasoning if result.intent=='query' else result.clarify_question
            if not reply.strip(): raise ValueError('回答不能为空')
            return {'intent':result.intent,'reply':reply,'created_tasks':[]}
        except (ValidationError, ValueError, ModelError) as error:
            messages.append({'role':'user','content':correction([str(error)[:1000]])})
    return {'intent':'clarify','reply':'对话生成连续校验失败，请稍后重试；你的日程和已保存信息没有丢失。','created_tasks':[]}
