from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .models import Base


def create_database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve a consistent pre-Session schema snapshot before any DDL, including
    # older migrations below. Never overwrite a previous recovery artifact.
    if path.is_file() and path.stat().st_size:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            names = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if names and not names.intersection({'tasks', 'calendar_sessions'}):
                raise ValueError('未知数据库 Schema，请选择新的数据库路径。')
            if 'agent_schema' in names and [r[0] for r in source.execute('SELECT version FROM agent_schema')] != [1]:
                raise ValueError('未知 Agent Schema，拒绝写入。')
            needs_backup = source.execute("SELECT 1 FROM sqlite_master WHERE name='calendar_sessions'").fetchone() is None
            if not needs_backup:
                needs_backup = "planned_date" not in {r[1] for r in source.execute("PRAGMA table_info(calendar_sessions)")}
        if needs_backup:
            from uuid import uuid4
            from .session_migration import snapshot_database
            snapshot_database(path, path.with_name(path.name + f".pre-session-{uuid4().hex}.db"))
    engine = create_engine("sqlite:///" + path.resolve().as_posix(), connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")

    # SQLite CHECK changes require a transactional rebuild, preserving IDs/FKs.
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path)) as connection:
        session_sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='calendar_sessions'").fetchone()
        if session_sql and "planned_date" not in {r[1] for r in connection.execute("PRAGMA table_info(calendar_sessions)")}:
            indexes = [r[0] for r in connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='calendar_sessions' AND sql IS NOT NULL")]
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            sql = session_sql[0].replace("CREATE TABLE calendar_sessions", "CREATE TABLE calendar_sessions_upgrade", 1)
            sql = sql.replace("start_at VARCHAR(27) NOT NULL", "start_at VARCHAR(27)").replace("end_at VARCHAR(27) NOT NULL", "end_at VARCHAR(27)")
            sql = sql.replace("'scheduled','active','closed','cancelled'", "'open','completed','scheduled','active','closed','cancelled'")
            connection.execute(sql)
            connection.execute("INSERT INTO calendar_sessions_upgrade SELECT * FROM calendar_sessions")
            connection.execute("DROP TABLE calendar_sessions")
            connection.execute("ALTER TABLE calendar_sessions_upgrade RENAME TO calendar_sessions")
            for sql in indexes: connection.execute(sql)
            for name, kind in (("planned_date", "DATE"), ("deadline_date", "DATE"), ("archived_at", "VARCHAR(27)")):
                connection.execute(f"ALTER TABLE calendar_sessions ADD COLUMN {name} {kind}")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Session date migration failed foreign-key verification")
            connection.commit()
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name='subtasks'").fetchone()
        if row and 'duration_min BETWEEN 45 AND 120' in row[0]:
            indexes = [r[0] for r in connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='subtasks' AND sql IS NOT NULL")]
            connection.execute('PRAGMA foreign_keys=OFF')
            connection.execute('BEGIN IMMEDIATE')
            sql = row[0].replace('CREATE TABLE subtasks', 'CREATE TABLE subtasks_upgrade', 1).replace('duration_min BETWEEN 45 AND 120', 'duration_min > 0')
            connection.execute(sql)
            connection.execute('INSERT INTO subtasks_upgrade SELECT * FROM subtasks')
            connection.execute('DROP TABLE subtasks')
            connection.execute('ALTER TABLE subtasks_upgrade RENAME TO subtasks')
            for sql in indexes: connection.execute(sql)
            if connection.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Subtask migration failed foreign-key verification')
            connection.commit()
    Base.metadata.create_all(engine)
    # Additive migration for pre-M3 databases; existing records stay intact.
    with engine.begin() as connection:
        message_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(assistant_messages)")}
        if "context_source_ids" not in message_columns:
            connection.exec_driver_sql("ALTER TABLE assistant_messages ADD COLUMN context_source_ids JSON NOT NULL DEFAULT '[]'")
        if "origin_key" not in message_columns:
            connection.exec_driver_sql("ALTER TABLE assistant_messages ADD COLUMN origin_key VARCHAR(200)")
            connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_assistant_message_origin ON assistant_messages(origin_key)")
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(sessions)")}
        if "actual_minutes" not in columns:
            connection.exec_driver_sql("ALTER TABLE sessions ADD COLUMN actual_minutes INTEGER CHECK(actual_minutes IS NULL OR actual_minutes >= 0)")
    return engine, sessionmaker(engine, expire_on_commit=False)
