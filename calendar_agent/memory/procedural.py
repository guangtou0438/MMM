"""Weather-only candidate, deterministic replay, explicit user activation."""
import json
from ..agent.store import encode, uid, timestamp
from ..tools.weather import risks, DEFAULT_THRESHOLDS


def replay():
    cases = [({'rain': 90, 'high': 20, 'low': 10, 'wind': 10}, ['rain']),
             ({'rain': 20, 'high': 40, 'low': 10, 'wind': 60}, ['high', 'wind']),
             ({'rain': 20, 'high': 20, 'low': 10, 'wind': 10}, [])]
    correct = sum(risks(weather, DEFAULT_THRESHOLDS) == expected for weather, expected in cases)
    return {'dataset': 'synthetic-weather-v1', 'cases': len(cases), 'success_rate': correct/len(cases),
            'misoperations': 0, 'model_calls': 0, 'tokens': 0,
            'scope': 'deterministic threshold policy replay; not live model performance'}


def propose(service):
    calls = service.store.rows("SELECT DISTINCT run_id FROM tool_calls WHERE skill='weather_planning' AND tool='get_weather' AND status='completed' AND run_id IN (SELECT id FROM agent_runs WHERE status='completed')")
    if len(calls) < 3:
        raise ValueError('至少需要三个成功的 Weather Run')
    existing = service.list('procedural')
    if existing:
        return existing[0]['id']
    strategy = '先读日历并确认活动日期和地点；查询对应预报；仅在代码阈值触发时创建建议；用户接受后由前台日历工具改期。'
    source = service.source(strategy)
    mid = service.add(strategy, 'procedural', [source], certainty='inferred')
    with service.store.connection() as db:
        db.execute('INSERT INTO memory_operations VALUES(?,?,?,?)', (uid('op'), 'PROCEDURAL_REPLAY', encode({'memory_id': mid, 'evidence_runs': [r['run_id'] for r in calls], 'metrics': replay()}), timestamp()))
    return mid


def activate(service, memory_id, enabled):
    with service.store.connection() as db:
        memory = db.execute("SELECT * FROM memories WHERE id=? AND kind='procedural' AND active=1", (memory_id,)).fetchone()
        if not memory:
            raise KeyError('候选不存在')
        evidence = [json.loads(r['payload']) for r in db.execute("SELECT payload FROM memory_operations WHERE operation='PROCEDURAL_REPLAY'")]
        if enabled and not any(e['memory_id'] == memory_id and e['metrics']['success_rate'] == 1 and e['metrics']['misoperations'] == 0 for e in evidence):
            raise ValueError('候选尚未通过回放')
        db.execute('UPDATE memories SET enabled=?,version=version+1 WHERE id=?', (enabled, memory_id))
