"""Small SQLite repository. Transactions never span provider calls."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


def uid(prefix):
    return prefix + '_' + uuid4().hex


def timestamp():
    return datetime.now(timezone.utc).timestamp()


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


SCHEMA = '''
CREATE TABLE IF NOT EXISTS agent_schema(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS agent_runs(
 id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
 session_key TEXT NOT NULL, status TEXT NOT NULL, model TEXT NOT NULL, kind TEXT NOT NULL,
 goal TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', error TEXT,
 calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, updated REAL NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
 elapsed REAL NOT NULL DEFAULT 0, actual_tokens INTEGER, citations TEXT NOT NULL DEFAULT '[]');
CREATE UNIQUE INDEX IF NOT EXISTS agent_session_writer ON agent_runs(session_key)
 WHERE status IN ('queued','running','waiting_for_user');
CREATE TABLE IF NOT EXISTS agent_events(
 run_id TEXT NOT NULL REFERENCES agent_runs(id), seq INTEGER NOT NULL,
 type TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(run_id,seq));
CREATE TABLE IF NOT EXISTS working_memory(
 run_id TEXT PRIMARY KEY REFERENCES agent_runs(id), messages TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_calls(
 request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, skill TEXT NOT NULL, tool TEXT NOT NULL,
 arguments TEXT NOT NULL, result TEXT, status TEXT NOT NULL, compensation TEXT,
 created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS memory_sources(
 id TEXT PRIMARY KEY, run_id TEXT, body TEXT, hash TEXT NOT NULL, created REAL NOT NULL,
 forgotten INTEGER NOT NULL DEFAULT 0, purge_after REAL, compressor TEXT, lineage TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS memories(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, source_ids TEXT NOT NULL,
 confidence REAL NOT NULL, certainty TEXT NOT NULL, sensitivity TEXT NOT NULL DEFAULT 'normal',
 version INTEGER NOT NULL DEFAULT 1, locked INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, updated REAL NOT NULL, expires REAL, parent_ids TEXT NOT NULL DEFAULT '[]',
 accesses INTEGER NOT NULL DEFAULT 0, accessed REAL);
CREATE TABLE IF NOT EXISTS short_term_memories(
 id TEXT PRIMARY KEY, content TEXT NOT NULL, source_id TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memory_operations(
 id TEXT PRIMARY KEY, operation TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memory_edges(
 source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL,
 PRIMARY KEY(source_id,target_id,relation));
CREATE TABLE IF NOT EXISTS memory_jobs(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, trigger TEXT NOT NULL,
 status TEXT NOT NULL, error TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memory_embeddings(
 memory_id TEXT NOT NULL, model TEXT NOT NULL, version TEXT NOT NULL, dimension INTEGER NOT NULL,
 vector BLOB NOT NULL, PRIMARY KEY(memory_id, model, version));
CREATE TABLE IF NOT EXISTS active_embedding(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), model TEXT NOT NULL,
 version TEXT NOT NULL, dimension INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS embedding_jobs(
 id TEXT PRIMARY KEY, model TEXT NOT NULL, version TEXT NOT NULL,
 status TEXT NOT NULL, error TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS weather_cache(
 key TEXT PRIMARY KEY, summary TEXT NOT NULL, queried REAL NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS adjustment_suggestions(
 id TEXT PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, session_id INTEGER NOT NULL,
 version INTEGER NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS export_audits(
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, format TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(id UNINDEXED, content, tokenize='trigram');
'''


class Store:
    def __init__(self, engine):
        self.engine = engine
        with self.connection() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_schema'").fetchone()
            if exists and [r[0] for r in db.execute('SELECT version FROM agent_schema')] != [1]:
                raise RuntimeError('Unknown agent schema; select a fresh database.')
            db.executescript(SCHEMA)
            if not exists:
                db.execute('INSERT INTO agent_schema VALUES(1)')

    @contextmanager
    def connection(self, write=True):
        import sqlite3
        connection = self.engine.raw_connection()
        db = connection.driver_connection
        old_factory = db.row_factory
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            db.row_factory = old_factory
            connection.close()

    def rows(self, sql, args=()):
        with self.connection(False) as db:
            return [dict(r) for r in db.execute(sql, args)]

    def run(self, run_id):
        rows = self.rows('SELECT * FROM agent_runs WHERE id=?', (run_id,))
        if not rows:
            raise KeyError('Run not found')
        return rows[0]

    def compact(self, run_id, db):
        groups = []
        for row in db.execute('SELECT * FROM agent_events WHERE run_id=? ORDER BY seq', (run_id,)):
            if row['type'] not in {'answer.delta', 'thinking.delta'}:
                groups.append([]); continue
            if not groups or not groups[-1] or groups[-1][-1]['type'] != row['type']:
                groups.append([])
            groups[-1].append(dict(row))
        for group in groups:
            if len(group) < 2:
                continue
            snapshot = ''.join(json.loads(event['data']).get('delta', '') for event in group)
            db.execute('UPDATE agent_events SET type=?,data=?,created=? WHERE run_id=? AND seq=?',
                       (group[-1]['type'].replace('.delta', '.snapshot'), encode({'text': snapshot}), group[0]['created'], run_id, group[-1]['seq']))
            for event in group[:-1]:
                db.execute('DELETE FROM agent_events WHERE run_id=? AND seq=?', (run_id, event['seq']))

    def event(self, run_id, kind, value, db=None):
        if db is None:
            with self.connection() as transaction:
                return self.event(run_id, kind, value, transaction)
        run = db.execute('SELECT status FROM agent_runs WHERE id=?', (run_id,)).fetchone()
        if run and run[0] == 'cancelled' and kind != 'run.cancelled':
            return None
        seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM agent_events WHERE run_id=?', (run_id,)).fetchone()[0]
        db.execute('INSERT INTO agent_events VALUES(?,?,?,?,?)', (run_id, seq, kind, encode(value), timestamp()))
        return seq
