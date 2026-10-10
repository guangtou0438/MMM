"""Source-aware memory with immediate correction and transitive forgetting."""
import hashlib
import json
import re
from threading import Lock
from ..agent.store import uid, timestamp, encode

KINDS = {'profile', 'preference', 'episodic', 'task', 'procedural'}
SECRET = re.compile(r'密码|验证码|身份证|银行卡|API[ _-]?KEY|password|secret|sk-[\w-]+|\b\d{16,19}\b', re.I)
# “分别记录” and “特别记住” are affirmative compound words, not “别记”.
# Still scan the rest of the text for an actual prohibition.
DO_NOT_REMEMBER = re.compile(r'不要记|(?<![分特])别记|不许记|do not remember|don.t remember', re.I)


class MemoryService:
    def __init__(self, store, embedding=None):
        self.store = store
        self.embedding = embedding
        self.overview_lock = Lock()
        self.overview_cache = None

    def source(self, content, run_id=None, db=None):
        if db is None:
            with self.store.connection() as transaction:
                return self.source(content, run_id, transaction)
        source_id = uid('src')
        blocked = bool(SECRET.search(content) or DO_NOT_REMEMBER.search(content))
        db.execute('INSERT INTO memory_sources(id,run_id,body,hash,created,forgotten) VALUES(?,?,?,?,?,?)',
                   (source_id, run_id, None if blocked else content, hashlib.sha256(content.encode()).hexdigest(), timestamp(), blocked))
        return source_id

    def list(self, kind=None):
        sql = 'SELECT * FROM memories WHERE active=1'
        rows = self.store.rows(sql + (' AND kind=?' if kind else '') + ' ORDER BY updated DESC', (kind,) if kind else ())
        for r in rows:
            r['source_ids'] = json.loads(r['source_ids'])
            r['parent_ids'] = json.loads(r['parent_ids'])
        return rows

    def add(self, content, kind, source_ids, confidence=.9, certainty='reported', sensitivity='normal', expires=None, parents=(), db=None):
        if db is None:
            with self.store.connection() as transaction:
                return self.add(content, kind, source_ids, confidence, certainty, sensitivity, expires, parents, transaction)
        if kind not in KINDS or not content.strip() or SECRET.search(content) or DO_NOT_REMEMBER.search(content):
            raise ValueError('记忆类别或内容不符合策略')
        if not source_ids:
            raise ValueError('记忆必须有来源')
        for source_id in source_ids:
            source = db.execute('SELECT * FROM memory_sources WHERE id=?', (source_id,)).fetchone()
            if not source or source['forgotten']:
                raise ValueError('来源不存在或已被要求忘记')
        existing = db.execute('SELECT id FROM memories WHERE active=1 AND kind=? AND content=? AND source_ids=?', (kind, content, encode(source_ids))).fetchone()
        if existing:
            return existing[0]
        memory_id = uid('mem')
        now = timestamp()
        db.execute('INSERT INTO memories(id,kind,content,source_ids,confidence,certainty,sensitivity,created,updated,expires,parent_ids) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (memory_id, kind, content, encode(source_ids), confidence, certainty, sensitivity, now, now, expires, encode(parents)))
        db.execute('INSERT INTO memories_fts VALUES(?,?)', (memory_id, content))
        for source_id in source_ids:
            lineage = json.loads(db.execute('SELECT lineage FROM memory_sources WHERE id=?', (source_id,)).fetchone()[0])
            db.execute('UPDATE memory_sources SET lineage=? WHERE id=?', (encode(lineage + [memory_id]), source_id))
        return memory_id

    def correct(self, memory_id, content, version):
        if SECRET.search(content) or not content.strip():
            raise ValueError('无效记忆内容')
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM memories WHERE id=? AND active=1', (memory_id,)).fetchone()
            if not row or row['version'] != version:
                raise ValueError('记忆版本已变化')
            source = self.source(content, db=db)
            # Retire every derivative of the corrected evidence immediately.
            retired = {memory_id}
            old_sources = set(json.loads(row['source_ids']))
            for source_id in old_sources:
                db.execute('UPDATE memory_sources SET forgotten=1 WHERE id=?', (source_id,))
                db.execute("UPDATE memory_jobs SET status='cancelled' WHERE source_id=?", (source_id,))
            changed = True
            while changed:
                changed = False
                for child in db.execute('SELECT id,parent_ids,source_ids FROM memories WHERE active=1'):
                    if child['id'] not in retired and (retired.intersection(json.loads(child['parent_ids'])) or old_sources.intersection(json.loads(child['source_ids']))):
                        retired.add(child['id']); changed = True
            for target in retired:
                db.execute('UPDATE memories SET active=0,version=version+1 WHERE id=?', (target,))
                db.execute('DELETE FROM memories_fts WHERE id=?', (target,))
                db.execute('DELETE FROM memory_embeddings WHERE memory_id=?', (target,))
            result = self.add(content, row['kind'], [source], parents=[memory_id], db=db)
            db.execute('UPDATE memories SET locked=1 WHERE id=?', (result,))
            db.execute('INSERT INTO memory_operations VALUES(?,?,?,?)',
                       (uid('op'), 'UPDATE', encode({'target': memory_id, 'version': version, 'result': result}), timestamp()))
            db.execute("UPDATE agent_runs SET status='cancelled',error='记忆已纠正，请开始新 Run' WHERE status IN ('running','queued','waiting_for_user')")
            db.execute('DELETE FROM working_memory')
        return result

    def forget(self, memory_id):
        with self.store.connection() as db:
            row = db.execute('SELECT source_ids FROM memories WHERE id=?', (memory_id,)).fetchone()
            if not row:
                raise KeyError('记忆不存在')
            sources = set(json.loads(row[0]))
            retired = {memory_id}
            # Shared sources and descendants are excluded from all future consolidation.
            changed = True
            while changed:
                changed = False
                for m in db.execute('SELECT id,source_ids,parent_ids FROM memories'):
                    ids = set(json.loads(m['source_ids']))
                    related = m['id'] in retired or bool(ids & sources) or bool(retired.intersection(json.loads(m['parent_ids'])))
                    if related and (m['id'] not in retired or not ids <= sources):
                        retired.add(m['id']); sources |= ids; changed = True
            for source_id in sources:
                db.execute('UPDATE memory_sources SET forgotten=1,purge_after=? WHERE id=?', (timestamp()+7*86400, source_id))
                db.execute('DELETE FROM short_term_memories WHERE source_id=?', (source_id,))
                db.execute("UPDATE memory_jobs SET status='cancelled' WHERE source_id=?", (source_id,))
            for m in db.execute('SELECT id,source_ids FROM memories').fetchall():
                if m['id'] in retired:
                    db.execute("UPDATE memories SET active=0,content='',version=version+1 WHERE id=?", (m['id'],))
                    db.execute('DELETE FROM memories_fts WHERE id=?', (m['id'],))
                    db.execute('DELETE FROM memory_embeddings WHERE memory_id=?', (m['id'],))
            # Stored prompts/traces can contain derived content. Clear globally in this single-user app.
            db.execute('DELETE FROM working_memory')
            db.execute('DELETE FROM agent_events')
            db.execute("UPDATE agent_runs SET goal='',answer='',error='上下文已因忘记操作清除',status=CASE WHEN status IN ('queued','running','waiting_for_user') THEN 'cancelled' ELSE status END")
            db.execute("UPDATE tool_calls SET arguments='{}',result=NULL,compensation=NULL")
            db.execute("UPDATE agent_runs SET citations='[]'")

    def temporary(self, content, source_id, expires):
        if expires <= timestamp() or expires > timestamp()+90*86400 or SECRET.search(content):
            raise ValueError('短期记忆需要有效 TTL')
        with self.store.connection() as db:
            source = db.execute('SELECT forgotten FROM memory_sources WHERE id=?', (source_id,)).fetchone()
            if not source or source[0]:
                raise ValueError('来源不可用')
            db.execute('INSERT INTO short_term_memories VALUES(?,?,?,?)', (uid('stm'), content, source_id, expires))

    def retain(self, now=None):
        now = timestamp() if now is None else now
        with self.store.connection() as db:
            db.execute("UPDATE agent_runs SET goal='原始正文已按保留策略清理' WHERE id IN (SELECT run_id FROM memory_sources WHERE purge_after IS NOT NULL AND purge_after<=?)", (now,))
            db.execute('UPDATE memory_sources SET body=NULL WHERE purge_after IS NOT NULL AND purge_after<=?', (now,))
            db.execute('DELETE FROM short_term_memories WHERE expires<=?', (now,))
            db.execute('DELETE FROM agent_events WHERE run_id IN (SELECT id FROM agent_runs WHERE status IN (\'completed\',\'failed\',\'cancelled\') AND (created<? OR id NOT IN (SELECT id FROM agent_runs ORDER BY created DESC LIMIT 100)))', (now-7*86400,))
            # Home conversations are user-visible history, not disposable run
            # buffers. Still honor explicit source expiry and forgetting.
            db.execute("""DELETE FROM working_memory WHERE run_id IN (
                SELECT id FROM agent_runs WHERE status IN ('completed','failed','cancelled')
                AND (session_key LIKE 'calendar:%' OR session_key IN (
                    SELECT r.session_key FROM agent_runs r JOIN memory_sources s ON s.run_id=r.id
                    WHERE s.purge_after IS NOT NULL AND s.purge_after<=?
                ))
            )""", (now,))
