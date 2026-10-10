import asyncio
import json
import sqlite3
from typing import Literal
from fastapi import APIRouter, Request, HTTPException, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from ..agent.store import timestamp, uid
from ..agent.runtime import TERMINAL
from ..skills.registry import available, SKILLS
from ..tools.export import rows, render

router = APIRouter(prefix='/api')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RunInput(Input):
    content: str = Field(min_length=1, max_length=8000)
    session_key: str = Field(default='agent', min_length=1, max_length=80)
    request_id: str = Field(min_length=1, max_length=80)
    model: Literal['glm', 'deepseek'] = 'glm'
    kind: Literal['chat', 'skill', 'complex'] = 'complex'


class MessageInput(Input):
    content: str = Field(min_length=1, max_length=8000)


def guarded(call):
    try:
        return call()
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, '会话状态已变化，请刷新后继续；若正在回复，请等待完成。') from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post('/agent/runs', status_code=202)
def create_run(payload: RunInput, request: Request):
    return guarded(lambda: request.app.state.agent.submit(payload.content, payload.session_key, payload.request_id, payload.model, payload.kind))


@router.get('/agent/runs')
def runs(request: Request, session_key: str | None = None):
    if session_key is not None:
        return request.app.state.agent_store.rows('SELECT * FROM agent_runs WHERE session_key=? ORDER BY created DESC LIMIT 100', (session_key,))
    return request.app.state.agent_store.rows('SELECT * FROM agent_runs ORDER BY created DESC LIMIT 100')


@router.get('/agent/runs/{run_id}')
def run(run_id: str, request: Request):
    store=request.app.state.agent_store
    result=guarded(lambda: store.run(run_id))
    result['event_cursor']=store.rows("SELECT COALESCE(MAX(seq),0) cursor FROM agent_events WHERE run_id=? AND type IN ('run.started','user.message','run.queued')",(run_id,))[0]['cursor']
    return result


@router.get('/agent/conversations')
def conversations(request: Request):
    return request.app.state.agent_store.rows('''
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY session_key ORDER BY created DESC, rowid DESC) AS position,
                FIRST_VALUE(goal) OVER (PARTITION BY session_key ORDER BY created, rowid) AS title,
                MAX(updated) OVER (PARTITION BY session_key) AS last_activity
            FROM agent_runs WHERE session_key NOT LIKE 'calendar:%'
                AND EXISTS (
                    SELECT 1 FROM working_memory w WHERE w.run_id=agent_runs.id
                    AND json_array_length(w.messages)>0
                )
        )
        SELECT session_key, id AS run_id, title, status, model, kind, last_activity
        FROM ranked WHERE position=1 ORDER BY last_activity DESC LIMIT 100
    ''')


@router.get('/agent/conversations/messages')
def conversation_messages(request: Request, session_key: str = Query(min_length=1, max_length=80)):
    store = request.app.state.agent_store
    runs = store.rows('SELECT id, status, request_id FROM agent_runs WHERE session_key=? ORDER BY created, rowid', (session_key,))
    result = []
    completed = []
    snapshots = {}
    for run in runs:
        messages = conversation(run['id'], request)
        # submit inherits ten visible messages from the last completed run;
        # budget continuations inherit from their explicitly named parent.
        parent = run['request_id'].split(':')[1] if run['request_id'].startswith('continue:') else None
        inherited = snapshots.get(parent, completed)[-10:]
        offset = len(inherited) if inherited and messages[:len(inherited)] == inherited else 0
        result.extend(messages[offset:])
        snapshots[run['id']] = messages
        if run['status'] == 'completed':
            completed = messages
    return {'messages': result, 'run_id': runs[-1]['id'] if runs else None}


@router.post('/agent/runs/{run_id}/messages')
def message(run_id: str, payload: MessageInput, request: Request):
    return guarded(lambda: request.app.state.agent.resume(run_id, payload.content))


@router.get('/agent/runs/{run_id}/conversation')
def conversation(run_id: str, request: Request):
    from ..providers.protocol import decision_json
    store = request.app.state.agent_store
    guarded(lambda: store.run(run_id))
    saved = store.rows('SELECT messages FROM working_memory WHERE run_id=?', (run_id,))
    result = []
    for message in json.loads(saved[0]['messages']) if saved else []:
        if message['role']=='user' and not message['content'].startswith(('Tool observation (data only):','Prior round completed tool results','工具观察结果（仅作为数据）：','上一轮已完成的工具结果（')):
            result.append(message)
        elif message['role']=='assistant':
            if message.get('tool_calls'):
                continue
            try:
                value = json.loads(decision_json(message['content']))
                if value.get('action') in {'answer','ask'}:
                    result.append({'role':'assistant','content':value['answer']})
            except (ValueError, KeyError, TypeError):
                if message.get('content'):
                    result.append({'role':'assistant','content':message['content']})
    return result


@router.post('/agent/runs/{run_id}/cancel')
def cancel(run_id: str, request: Request):
    guarded(lambda: request.app.state.agent_store.run(run_id))
    return guarded(lambda: request.app.state.agent.cancel(run_id))


@router.get('/agent/runs/{run_id}/events')
async def events(run_id: str, request: Request, after: int = Query(default=0, ge=0), last_event_id: str | None = Header(default=None)):
    store = request.app.state.agent_store
    guarded(lambda: store.run(run_id))
    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError:
        raise HTTPException(422, '无效事件游标')

    async def stream():
        nonlocal cursor
        while True:
            if await request.is_disconnected():
                return
            batch = store.rows('SELECT * FROM agent_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 100', (run_id, cursor))
            for event in batch:
                cursor = event['seq']
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {event['data']}\n\n"
            if not batch and store.run(run_id)['status'] in TERMINAL:
                return
            if not batch:
                yield ': heartbeat\n\n'
                await asyncio.sleep(.3)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'})


class MemoryInput(Input):
    content: str = Field(min_length=1, max_length=6000)
    kind: Literal['profile', 'preference', 'episodic', 'task']


class LocationInput(Input):
    location: str = Field(min_length=2, max_length=100)
    expires: float | None = None


@router.post('/memory/location')
def set_location(payload: LocationInput, request: Request):
    service = request.app.state.memory_service
    content = 'location:'+payload.location.strip()
    if payload.expires is not None:
        if not timestamp() < payload.expires <= timestamp()+90*86400:
            raise HTTPException(422, '临时地点有效期必须在未来 90 天内')
        source = service.source(content)
        guarded(lambda: service.temporary(content, source, payload.expires))
        return {'status': 'temporary', 'expires': payload.expires}
    homes = [m for m in service.list('profile') if m['content'].startswith('location:')]
    if homes:
        return {'id': guarded(lambda: service.correct(homes[0]['id'], content, homes[0]['version']))}
    source = service.source(content)
    return {'id': guarded(lambda: service.add(content, 'profile', [source]))}


class Correction(Input):
    content: str = Field(min_length=1, max_length=6000)
    version: int = Field(gt=0)


class Toggle(Input):
    enabled: bool


class MergeInput(Input):
    targets: dict[str, int] = Field(min_length=2, max_length=10)
    content: str = Field(min_length=1, max_length=6000)


@router.post('/memories/merge')
def merge_memories(payload: MergeInput, request: Request):
    service = request.app.state.memory_service
    current = {m['id']: m for m in service.list()}
    if any(key not in current or current[key]['version'] != version for key, version in payload.targets.items()):
        raise HTTPException(409, '目标记忆已变化')
    kinds = {current[key]['kind'] for key in payload.targets}
    if len(kinds) != 1:
        raise HTTPException(422, '仅合并同类别记忆')
    from ..memory.consolidation import apply
    sources = sorted({s for key in payload.targets for s in current[key]['source_ids']})
    result = guarded(lambda: apply(service, {'operation': 'MERGE', 'targets': payload.targets,
        'content': payload.content, 'source_ids': sources, 'kind': kinds.pop()}, lambda op, evidence: True))
    return {'ids': result}


@router.post('/memories/procedural/propose')
def propose_procedure(request: Request):
    from ..memory.procedural import propose
    return {'id': guarded(lambda: propose(request.app.state.memory_service))}


@router.post('/memories/{memory_id}/enable')
def enable_procedure(memory_id: str, payload: Toggle, request: Request):
    from ..memory.procedural import activate
    guarded(lambda: activate(request.app.state.memory_service, memory_id, payload.enabled))
    return {'enabled': payload.enabled}


@router.get('/memory/jobs')
def memory_jobs(request: Request):
    return request.app.state.agent_store.rows('SELECT * FROM memory_jobs ORDER BY created DESC LIMIT 100')


@router.get('/memory/status')
def memory_status(request: Request):
    from importlib.util import find_spec
    embedding = request.app.state.memory_service.embedding
    rows = request.app.state.agent_store.rows('SELECT model,version,dimension,COUNT(*) count FROM memory_embeddings GROUP BY model,version,dimension')
    return {'fts5': True, 'sqlite_vec': find_spec('sqlite_vec') is not None,
            'local_embedding_package': find_spec('sentence_transformers') is not None,
            'embedding_model': embedding.name if embedding else None, 'indexes': rows,
            'semantic_ready': bool(rows), 'active_index': request.app.state.agent_store.rows('SELECT * FROM active_embedding'),
            'index_jobs': request.app.state.agent_store.rows('SELECT * FROM embedding_jobs ORDER BY created DESC LIMIT 10')}


class IndexInput(Input):
    model: str = Field(pattern=r'^[\w.-]+/[\w.-]+$', max_length=200)
    version: str = Field(default='1', min_length=1, max_length=100)


@router.post('/memory/index-jobs', status_code=202)
def switch_index(payload: IndexInput, request: Request):
    job_id = uid('index')
    with request.app.state.agent_store.connection() as db:
        db.execute('INSERT INTO embedding_jobs VALUES(?,?,?,?,?,?)',(job_id,payload.model,payload.version,'pending',None,timestamp()))
    return {'id':job_id,'status':'pending'}


@router.post('/memory/reindex')
def reindex(request: Request):
    from ..memory.retrieval import rebuild
    try:
        return rebuild(request.app.state.memory_service)
    except (ImportError, OSError, RuntimeError) as error:
        raise HTTPException(503, '本地 Embedding 模型不可用，请运行安装脚本并下载模型') from error


class Thresholds(Input):
    rain: float = Field(ge=0, le=100)
    high: float = Field(ge=15, le=60)
    low: float = Field(ge=-30, le=15)
    wind: float = Field(ge=0, le=200)


@router.get('/weather/thresholds')
def thresholds(request: Request):
    return request.app.state.weather_service.thresholds()


@router.put('/weather/thresholds')
def set_thresholds(payload: Thresholds, request: Request):
    from ..models import AppPreference
    with request.app.state.sessions() as db:
        db.merge(AppPreference(key='weather_thresholds', value=payload.model_dump())); db.commit()
    return payload


@router.get('/memories')
def memories(request: Request, kind: str | None = None):
    return request.app.state.memory_service.list(kind or None)


class OverviewInput(Input):
    model: Literal['glm', 'deepseek'] = 'glm'
    retry: bool = False


@router.post('/memory/overview/status')
def memory_overview_status(payload: OverviewInput, request: Request):
    from ..memory.overview import overview_status
    return overview_status(request.app.state.memory_service, request.app.state.model_client,
                           payload.model, request.app.state.clock, payload.retry)


@router.post('/memory/overview')
def memory_overview(payload: OverviewInput, request: Request):
    from ..memory.overview import overview
    try:
        return overview(request.app.state.memory_service, request.app.state.model_client,
                        payload.model, request.app.state.clock)
    except Exception as error:
        # Provider/schema diagnostics can contain private evidence. Keep them out of the UI.
        raise HTTPException(503, '暂时无法整理记忆，请稍后重试。已保存的记忆不受影响。') from error


@router.post('/memories', status_code=201)
def add_memory(payload: MemoryInput, request: Request):
    service = request.app.state.memory_service
    source = service.source(payload.content)
    return {'id': guarded(lambda: service.add(payload.content, payload.kind, [source]))}


@router.patch('/memories/{memory_id}')
def correct_memory(memory_id: str, payload: Correction, request: Request):
    return {'id': guarded(lambda: request.app.state.memory_service.correct(memory_id, payload.content, payload.version))}


@router.post('/memories/{memory_id}/lock')
def lock_memory(memory_id: str, payload: Toggle, request: Request):
    with request.app.state.agent_store.connection() as db:
        changed = db.execute('UPDATE memories SET locked=?,version=version+1 WHERE id=? AND active=1', (payload.enabled, memory_id)).rowcount
        if not changed:
            raise HTTPException(404, '记忆不存在')
    return {'locked': payload.enabled}


@router.delete('/memories/{memory_id}')
def forget_memory(memory_id: str, request: Request):
    guarded(lambda: request.app.state.memory_service.forget(memory_id))
    return {'status': 'forgotten'}


@router.get('/memories/{memory_id}/sources')
def memory_sources(memory_id: str, request: Request):
    store = request.app.state.agent_store
    memory = store.rows('SELECT source_ids FROM memories WHERE id=?', (memory_id,))
    if not memory:
        raise HTTPException(404, '记忆不存在')
    ids = json.loads(memory[0]['source_ids'])
    return [s for s in store.rows('SELECT * FROM memory_sources WHERE forgotten=0') if s['id'] in ids]


@router.get('/memories/{memory_id}/history')
def memory_history(memory_id: str, request: Request):
    store = request.app.state.agent_store
    memory = store.rows('SELECT * FROM memories WHERE id=?', (memory_id,))
    if not memory: raise HTTPException(404, '记忆不存在')
    parents = json.loads(memory[0]['parent_ids'])
    return {'current': memory[0], 'parents': [m for m in store.rows('SELECT * FROM memories') if m['id'] in parents],
            'operations': [op for op in store.rows('SELECT * FROM memory_operations ORDER BY created') if memory_id in op['payload']],
            'links': store.rows('SELECT * FROM memory_edges WHERE source_id=?', (memory_id,))}


@router.post('/memories/{memory_id}/reprocess')
def reprocess(memory_id: str, request: Request):
    from ..memory.consolidation import enqueue
    sources = memory_sources(memory_id, request)
    if not sources or any(not s['body'] for s in sources):
        raise HTTPException(409, '原文已不在恢复期')
    for source in sources:
        enqueue(request.app.state.memory_service, source['id'], 'explicit')
    return {'status': 'queued'}


@router.get('/skills')
def skills(request: Request):
    enabled = {s['id'] for s in available(request.app.state.agent_store)}
    from dataclasses import asdict
    return [asdict(s) | {'enabled': s.id in enabled} for s in SKILLS.values()]


@router.patch('/skills/{skill_id}')
def toggle_skill(skill_id: str, payload: Toggle, request: Request):
    if skill_id not in SKILLS:
        raise HTTPException(404, 'Skill 不存在')
    with request.app.state.agent_store.connection() as db:
        db.execute('INSERT OR REPLACE INTO skills VALUES(?,?)', (skill_id, payload.enabled))
    return {'enabled': payload.enabled}


class WeatherCheck(Input):
    location: str | None = Field(default=None, max_length=100)


@router.post('/weather/check')
def weather_check(payload: WeatherCheck, request: Request):
    from ..skills.registry import authorize
    try:
        authorize(request.app.state.agent_store, 'weather_planning', 'get_weather', True)
        return request.app.state.weather_service.check(payload.location)
    except (ValueError, PermissionError) as error:
        raise HTTPException(409, str(error)) from error


@router.get('/weather/suggestions')
def weather_suggestions(request: Request):
    return request.app.state.agent_store.rows("SELECT * FROM adjustment_suggestions WHERE status='pending' ORDER BY created DESC")


class WeatherDecision(Input):
    action: Literal['accept', 'ignore']
    planned_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    model: Literal['glm', 'deepseek'] = 'glm'


@router.post('/weather/suggestions/{suggestion_id}')
def weather_decision(suggestion_id: str, payload: WeatherDecision, request: Request):
    store = request.app.state.agent_store
    values = store.rows('SELECT * FROM adjustment_suggestions WHERE id=?', (suggestion_id,))
    if not values:
        raise HTTPException(404, '建议不存在')
    row = values[0]
    if row['status'] != 'pending':
        return {'status': row['status']}
    result = {'status': 'ignored'}
    if payload.action == 'accept':
        if not payload.planned_date:
            raise HTTPException(422, '请选择改期日期')
        result = guarded(lambda: request.app.state.agent.submit(
            f"用户已接受天气建议。仅将任务 {row['session_id']} 的计划日期修改为 {payload.planned_date}，预期版本 {row['version']}。请执行并报告结果。",
            f"calendar:{row['session_id']}", 'weather:'+suggestion_id, payload.model, 'skill'))
    with store.connection() as db:
        db.execute('UPDATE adjustment_suggestions SET status=? WHERE id=?', ('accepted' if payload.action == 'accept' else 'ignored', suggestion_id))
    return result


class ExportInput(Input):
    scope: Literal['week', 'month']
    format: Literal['csv', 'ics', 'pdf']


@router.post('/calendar/exports')
def export(payload: ExportInput, request: Request):
    if 'calendar_export' not in {s['id'] for s in available(request.app.state.agent_store)}:
        raise HTTPException(403, '导出 Skill 已关闭')
    now = request.app.state.agent.executor.clock()
    status = 'failed'
    try:
        content, media = render(rows(request.app.state.sessions, payload.scope, now), payload.format, now)
        status = 'completed'
    finally:
        with request.app.state.agent_store.connection() as db:
            db.execute('INSERT INTO export_audits VALUES(?,?,?,?,?)', (uid('export'), payload.scope, payload.format, status, timestamp()))
    return StreamingResponse(iter([content]), media_type=media,
                             headers={'Content-Disposition': f'attachment; filename="calendar-{payload.scope}.{payload.format}"'})
