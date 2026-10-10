"""Date-based permissions. The caller's entry is server-owned, never model input."""
from datetime import datetime, timezone

class SessionPermissionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

def instant(value: datetime):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('时间必须包含时区')
    return value.astimezone(timezone.utc)

def require_permission(session, action, now=None, *, entry='user'):
    if action == 'complete' and entry != 'user':
        raise SessionPermissionError('MANUAL_COMPLETION_ONLY')
    if action in {'update', 'archive', 'restore', 'reopen'} and entry == 'session':
        raise SessionPermissionError('HOME_CONFIRMATION_REQUIRED')
    if action == 'restore':
        if session.archived_at is None: raise SessionPermissionError('NOT_ARCHIVED')
        return
    if session.archived_at is not None:
        raise SessionPermissionError('SESSION_ARCHIVED')
    if action == 'reopen':
        if session.status != 'completed': raise SessionPermissionError('NOT_COMPLETED')
        return
    if action == 'archive': return
    if session.status != 'open':
        raise SessionPermissionError('SESSION_READ_ONLY')
