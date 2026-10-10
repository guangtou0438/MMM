"""Calendar semantic references, never a second source of task dates/status."""
from .consolidation import enqueue


def sync(service, task):
    target = 'calendar:' + str(task['id'])
    content = f"任务 #{task['id']}：{task['title']}。背景：{task.get('session_goal', '')}"
    with service.store.connection() as db:
        import json
        row = db.execute('SELECT migration_metadata FROM calendar_sessions WHERE id=?', (task['id'],)).fetchone()
        if not row or json.loads(row[0] or '{}').get('permanently_deleted_at'):
            raise ValueError('任务已永久删除')
        existing = db.execute("SELECT m.id,m.content FROM memories m JOIN memory_edges e ON e.source_id=m.id WHERE e.target_id=? AND e.relation='task' AND m.active=1", (target,)).fetchone()
        if existing and existing['content'] == content:
            return existing['id']
        source = service.source(content, db=db)
        for old in db.execute("SELECT source_id FROM memory_edges WHERE target_id=? AND relation='task'", (target,)).fetchall():
            db.execute('UPDATE memories SET active=0,version=version+1 WHERE id=?', (old[0],))
            db.execute('DELETE FROM memories_fts WHERE id=?', (old[0],))
        memory = service.add(content, 'task', [source], db=db)
        db.execute('INSERT INTO memory_edges VALUES(?,?,?)', (memory, target, 'task'))
    enqueue(service, source, 'task_changed')
    return memory
