"""FTS5 + sqlite-vec recall; failures yield no fabricated evidence."""
import math
import re
from ..agent.store import timestamp


def active_provider(service):
    active = service.store.rows('SELECT * FROM active_embedding WHERE singleton=1')
    if not active or not service.embedding:
        return service.embedding
    row = active[0]
    if (row['model'],row['version']) == (service.embedding.name,service.embedding.version):
        return service.embedding
    from ..providers.embedding import LocalEmbedding
    return LocalEmbedding(row['model'],row['version'],row['dimension'])


def retrieve(service, query, limit=8, mode='hybrid', *, maintenance=False):
    if not query.strip():
        return []
    candidates = {}
    try:
        terms = ' OR '.join('"' + t.replace('"', '""') + '"' for t in query.split()[:16])
        if maintenance:
            # FTS uses trigrams: whole Chinese sentences miss related paraphrases.
            tokens = []
            for part in re.findall(r'[\u4e00-\u9fff]+|[A-Za-z0-9_]+', query):
                tokens.extend([part[i:i+3] for i in range(len(part)-2)]
                              if re.search(r'[\u4e00-\u9fff]', part) else [part])
            terms = ' OR '.join('"'+t+'"' for t in dict.fromkeys(tokens))
        ordering = ' ORDER BY score' if maintenance else ''
        rows = service.store.rows('SELECT id,bm25(memories_fts) score FROM memories_fts WHERE memories_fts MATCH ?'+ordering+' LIMIT 40', (terms,))
        candidates.update({r['id']: {'keyword': 1/(1+rank*.1), 'vector': 0.} for rank, r in enumerate(rows)})
    except Exception:
        pass  # The API exposes provider status separately; never invent a hit.
    if mode == 'hybrid' and service.embedding and service.store.rows('SELECT 1 FROM memory_embeddings LIMIT 1'):
        try:
            import sqlite_vec
            provider = active_provider(service)
            vector = provider.encode(query)
            with service.store.connection(False) as db:
                db.enable_load_extension(True)
                try:
                    sqlite_vec.load(db)
                finally:
                    db.enable_load_extension(False)
                procedural_filter = '' if maintenance else " AND (m.kind<>'procedural' OR m.enabled=1)"
                rows = db.execute('SELECT e.memory_id,vec_distance_cosine(e.vector,?) distance FROM memory_embeddings e JOIN memories m ON m.id=e.memory_id WHERE e.model=? AND e.version=? AND e.dimension=? AND m.active=1 AND (m.expires IS NULL OR m.expires>?)'+procedural_filter+' ORDER BY distance LIMIT 40',
                                  (vector, provider.name, provider.version, provider.dimension, timestamp()))
                for r in rows:
                    if r['distance'] < .8:
                        candidates.setdefault(r['memory_id'], {'keyword': 0., 'vector': 0.})['vector'] = 1-r['distance']
        except (ImportError, RuntimeError, OSError, ValueError):
            pass
    now = timestamp()
    hits = []
    for memory in service.list():
        signals = candidates.get(memory['id'])
        if not signals or memory['expires'] and memory['expires'] <= now:
            continue
        if not maintenance and memory['kind'] == 'procedural' and not memory['enabled']:
            continue
        sources = service.store.rows('SELECT id FROM memory_sources WHERE forgotten=1')
        if {s['id'] for s in sources}.intersection(memory['source_ids']):
            continue
        score = .4*signals['vector'] + .3*signals['keyword'] + .15*memory['confidence'] + .1*math.exp(-(now-memory['updated'])/(30*86400)) + .05/(1+memory['accesses'])
        if maintenance:
            score = .4*signals['vector'] + .3*signals['keyword']
        hits.append(memory | {'score': score})
    hits.sort(key=lambda m: m['score'], reverse=True)
    if not maintenance:
        with service.store.connection() as db:
            for m in hits[:limit]:
                db.execute('UPDATE memories SET accesses=accesses+1,accessed=? WHERE id=?', (now, m['id']))
    return hits[:limit]


def rebuild(service, provider=None):
    provider = provider or active_provider(service)
    if not provider:
        return {'indexed': 0, 'status': 'unavailable'}
    # Generate outside transaction. Existing version continues serving until atomic swap.
    rows = [(m, provider.encode(m['content'])) for m in service.list()]
    with service.store.connection() as db:
        captured = {memory['id']:memory['version'] for memory, vector in rows}
        current = {m['id']:m['version'] for m in db.execute('SELECT id,version FROM memories WHERE active=1')}
        if captured != current:
            raise RuntimeError('构建期间记忆已变化，旧索引继续服务，请重试')
        for memory, vector in rows:
            current = db.execute('SELECT active,version FROM memories WHERE id=?', (memory['id'],)).fetchone()
            if current and current['active'] and current['version'] == memory['version']:
                db.execute('INSERT OR REPLACE INTO memory_embeddings VALUES(?,?,?,?,?)', (memory['id'], provider.name, provider.version, provider.dimension, vector))
        db.execute('INSERT OR REPLACE INTO active_embedding VALUES(1,?,?,?)', (provider.name,provider.version,provider.dimension))
    return {'indexed': len(rows), 'status': 'ready'}


def process_index_job(service):
    from ..providers.embedding import LocalEmbedding
    from ..agent.errors import public_error
    with service.store.connection() as db:
        job = db.execute("SELECT * FROM embedding_jobs WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
        if not job: return
        job = dict(job)
        db.execute("UPDATE embedding_jobs SET status='running' WHERE id=?", (job['id'],))
    status,error = 'done',None
    try:
        rebuild(service, LocalEmbedding(job['model'],job['version']))
    except Exception as exception:
        status,error = 'failed',public_error(exception)
    with service.store.connection() as db:
        db.execute('UPDATE embedding_jobs SET status=?,error=? WHERE id=?',(status,error,job['id']))
