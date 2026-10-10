"""Event-triggered operations. Risky semantic changes fail closed."""
import re
from .operations import MemoryOperation
from ..agent.store import uid, timestamp, encode

HIGH_RISK = re.compile(r'健康|情绪|关系|住|地点|承诺|明天|\d{4}-\d{2}-\d{2}|health|live|promise', re.I)


def apply(service, operation, verifier=None, force_verification=False):
    op = MemoryOperation.model_validate(operation)
    sources = service.store.rows('SELECT * FROM memory_sources WHERE forgotten=0')
    evidence = {s['id']: s['body'] for s in sources if s['id'] in op.source_ids}
    if len(evidence) != len(set(op.source_ids)) or not all(evidence.values()):
        raise ValueError('来源缺失、已忘记或原文已清理')
    if op.kind == 'preference' and op.certainty == 'inferred' and len(set(op.source_ids)) < 2:
        raise ValueError('隐式偏好至少需要两条独立来源证据')
    risky = force_verification or op.sensitivity == 'sensitive' or op.kind in {'profile', 'task'} or bool(op.targets) or bool(HIGH_RISK.search(op.content))
    if risky and (verifier is None or not verifier(op.model_dump(), evidence)):
        raise ValueError('高风险操作未通过独立校验')
    with service.store.connection() as db:
        for sid in op.source_ids:
            if db.execute('SELECT forgotten FROM memory_sources WHERE id=?', (sid,)).fetchone()[0]:
                raise ValueError('来源已在校验期间被忘记')
        for target, version in op.targets.items():
            row = db.execute('SELECT * FROM memories WHERE id=?', (target,)).fetchone()
            if not row or row['version'] != version or row['locked'] or not row['active']:
                raise ValueError('目标已变化、失效或锁定')
        if op.operation in {'NOOP', 'CONFLICT'}:
            result = []
        elif op.operation in {'EXPIRE', 'INVALIDATE'}:
            if not op.targets:
                raise ValueError('需要目标版本')
            for target in op.targets:
                db.execute('UPDATE memories SET active=0,version=version+1 WHERE id=?', (target,))
                db.execute('DELETE FROM memories_fts WHERE id=?', (target,))
            result = []
        elif op.operation == 'DEMOTE':
            if not op.targets or op.expires is None or not timestamp() < op.expires <= timestamp()+90*86400:
                raise ValueError('DEMOTE 需要目标和不超过 90 天的 TTL')
            result = [uid('stm')]
            db.execute('INSERT INTO short_term_memories VALUES(?,?,?,?)', (result[0], op.content, op.source_ids[0], op.expires))
            for target in op.targets:
                db.execute('UPDATE memories SET active=0,version=version+1 WHERE id=?', (target,))
                db.execute('DELETE FROM memories_fts WHERE id=?', (target,))
        elif op.operation in {'ADD', 'UPDATE', 'MERGE', 'PROMOTE', 'SPLIT'}:
            if op.operation != 'ADD' and not op.targets:
                raise ValueError('需要目标版本')
            fragments = op.fragments if op.operation == 'SPLIT' else [op.content]
            if not fragments or op.operation == 'SPLIT' and len(fragments) < 2:
                raise ValueError('SPLIT 至少需要两条原子记忆')
            result = [service.add(content, op.kind, op.source_ids, op.confidence, op.certainty, op.sensitivity, expires=op.expires, parents=list(op.targets), db=db) for content in fragments]
            for target in op.targets:
                db.execute('UPDATE memories SET active=0,version=version+1 WHERE id=?', (target,))
                db.execute('DELETE FROM memories_fts WHERE id=?', (target,))
        db.execute('INSERT INTO memory_operations VALUES(?,?,?,?)', (uid('op'), op.operation, encode(op.model_dump() | {'result': result}), timestamp()))
        # Destructive compression always needs an independent quality gate, even for ordinary memory.
        if result and risky:
            for sid in op.source_ids:
                db.execute("UPDATE memory_sources SET purge_after=?,compressor='memory-ir-v1' WHERE id=?", (timestamp()+7*86400, sid))
    return result


def enqueue(service, source_id, trigger):
    if trigger not in {'explicit', 'task_changed', 'idle', 'stable_preference', 'pattern', 'budget', 'retention'}:
        raise ValueError('未知巩固事件')
    with service.store.connection() as db:
        pending = db.execute("SELECT 1 FROM memory_jobs WHERE source_id=? AND trigger=? AND status IN ('pending','running')", (source_id, trigger)).fetchone()
        if pending:
            return
        db.execute('INSERT INTO memory_jobs VALUES(?,?,?,?,?,?)', (uid('job'), source_id, trigger, 'pending', None, timestamp()))
