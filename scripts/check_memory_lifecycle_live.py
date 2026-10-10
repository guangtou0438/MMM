"""Synthetic end-to-end memory audit. Never opens the user's database.

Runs real configured providers and local embeddings; persists raw evidence after
each step. Scheduler timing is accelerated by explicitly draining its job handler.
Answer quality is reviewed from evidence, not scored by keyword coincidence.
"""
import argparse
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.memory.jobs import process_one
from calendar_agent.memory.retrieval import rebuild, retrieve
from calendar_agent.memory.blocks import assemble
from calendar_agent.agent.store import timestamp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', default=['glm', 'deepseek'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'synthetic_only': True, 'mode': 'live', 'started': timestamp(),
              'scope': 'Agent API + real consolidation + local embedding; manual scheduler drain; no browser test',
              'cases': [], 'checks': []}

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def record(item):
        report['cases'].append(item)
        save()
        print(json.dumps({k: item.get(k) for k in ('model', 'case', 'status', 'answer')}, ensure_ascii=False), flush=True)

    for model in args.models:
        with tempfile.TemporaryDirectory(prefix='mmm-memory-audit-') as directory:
            app = create_app(Path(directory) / 'synthetic.db', scheduler_enabled=False)
            with TestClient(app) as client:
                memory = app.state.memory_service
                store = app.state.agent_store

                def ask(case, content, expected, session=None):
                    started = time.monotonic()
                    response = client.post('/api/agent/runs', json={
                        'content': content, 'session_key': session or uuid.uuid4().hex,
                        'request_id': uuid.uuid4().hex, 'model': model, 'kind': 'complex'})
                    response.raise_for_status()
                    rid = response.json()['id']
                    while time.monotonic() - started < 150:
                        run = client.get('/api/agent/runs/' + rid).json()
                        if run['status'] in {'completed', 'failed', 'cancelled', 'waiting_for_user'}:
                            break
                        time.sleep(.1)
                    else:
                        client.post('/api/agent/runs/' + rid + '/cancel')
                        run = client.get('/api/agent/runs/' + rid).json()
                    item = {'model': model, 'case': case, 'input': content, 'expected': expected,
                            'status': run['status'], 'answer': run['answer'], 'error': run['error'],
                            'actual_model': run['model'], 'calls': run['calls'], 'tokens': run['actual_tokens'],
                            'seconds': time.monotonic()-started, 'citations': json.loads(run['citations']),
                            'events': store.rows('SELECT type,data FROM agent_events WHERE run_id=? ORDER BY seq', (rid,)),
                            'memories': memory.list(),
                            'tools': store.rows('SELECT tool,arguments,status,result FROM tool_calls WHERE run_id=?', (rid,))}
                    record(item)
                    return item

                def drain(case):
                    jobs = []
                    for _ in range(12):
                        result = process_one(memory, model=model)
                        if result['status'] in {'idle', 'deferred'}:
                            break
                        jobs.append(result)
                    try:
                        index = rebuild(memory)
                    except Exception as exc:
                        index = {'status': 'unavailable', 'error_type': type(exc).__name__}
                    record({'model': model, 'case': case, 'status': 'observed', 'jobs': jobs,
                            'index': index, 'memories': memory.list(),
                            'operations': store.rows('SELECT operation,payload FROM memory_operations'),
                            'sources': store.rows('SELECT id,body,purge_after,forgotten FROM memory_sources')})

                ask('unknown', '我的阅读俱乐部叫什么名字？', '承认不知道或询问，不编造名字')
                ask('remember', '请记住：我参加的阅读俱乐部叫青苔灯塔。', '接收明确记忆请求')
                drain('consolidation')
                ask('cross_session', '我的阅读俱乐部叫什么名字？', '青苔灯塔；新会话无原始聊天上下文')
                ask('paraphrase', '我加入的那个读书社群叫什么？', '青苔灯塔；同义改写召回')

                targets = [m for m in memory.list() if '青苔灯塔' in m['content']]
                if targets:
                    target = targets[0]
                    response = client.patch('/api/memories/' + target['id'], json={
                        'content': '我参加的阅读俱乐部叫琥珀渡口。', 'version': target['version']})
                    response.raise_for_status()
                    corrected = response.json()['id']
                    # Deliberately query before reindex: UI correction does not schedule one.
                    ask('correct_immediate', '我的阅读俱乐部叫什么名字？', '琥珀渡口，不使用旧名字；未手动重建索引')
                    drain('index_after_correction')
                    ask('correct_indexed', '我的阅读俱乐部叫什么名字？', '琥珀渡口；索引更新后')
                    response = client.delete('/api/memories/' + corrected)
                    response.raise_for_status()
                    ask('forget', '我的阅读俱乐部叫什么名字？', '不知道，不再输出两个已忘记的名字')
                else:
                    record({'model': model, 'case': 'correction_and_forget', 'status': 'blocked',
                            'reason': '目标记忆未形成，无法测试纠正与遗忘'})

                ask('multi_write', '请记住这三件事：我的自行车叫银杏；我的读书小组叫雾松；我最喜欢的棋类是围棋。', '完整保留三个独立事实')
                drain('multi_consolidation')
                ask('multi_read', '我的自行车叫什么、读书小组叫什么、最喜欢哪种棋类？', '银杏、雾松、围棋三项完整且不串用')

                # Seed only the conflict fixture, explicitly distinguish from auto extraction.
                response = client.post('/api/memories', json={'content': '我的常住城市是南京。', 'kind': 'profile'})
                response.raise_for_status()
                for i in range(12):
                    client.post('/api/memories', json={'content': f'虚构收藏记录：第{i+1}本画册的标签是纸鹤{i+1}。', 'kind': 'episodic'}).raise_for_status()
                ask('buried_conflict_write', '请记住：我的常住城市是苏州。', '识别与旧南京记录的冲突或明确更新，不能静默保留两个当前常住地')
                drain('buried_conflict_consolidation')
                ask('buried_conflict_read', '我的常住城市是哪座？如果记录矛盾，请明确指出。', '采用有依据的新信息或澄清冲突，不错误断言南京')

                # Check retention behavior without changing wall time for unrelated records.
                memory.retain(timestamp()+8*86400)
                ask('after_retention', '我的自行车叫什么、读书小组叫什么、最喜欢哪种棋类？', '清理来源正文后仍保留银杏、雾松、围棋')

    # Deterministic targeted checks, kept separate from live model evidence.
    with tempfile.TemporaryDirectory(prefix='mmm-memory-check-') as directory:
        app = create_app(Path(directory) / 'synthetic.db', scheduler_enabled=False)
        with TestClient(app):
            memory = app.state.memory_service
            memory.embedding = None
            source = memory.source('自行车叫银杏。读书小组叫雾松。')
            first = memory.add('自行车叫银杏。', 'profile', [source])
            second = memory.add('读书小组叫雾松。', 'profile', [source])
            memory.forget(first)
            report['checks'].append({'case': 'forget_preserves_unrelated_fact_from_shared_source',
                                     'passed': any(m['id']==second for m in memory.list()),
                                     'expected': '忘记自行车名字后，读书小组名字仍可用'})
            source = memory.source('临时地点是苏州')
            memory.temporary('location:苏州', source, timestamp()+30)
            before = assemble(memory, '当前临时地点')['Temporary']
            memory.retain(timestamp()+60)
            after = assemble(memory, '当前临时地点')['Temporary']
            report['checks'].append({'case': 'temporary_expiry', 'passed': bool(before) and not after})
    report['finished'] = timestamp()
    save()
    print('FINISHED ' + str(args.output), flush=True)


if __name__ == '__main__':
    main()
