import hashlib
import re
from datetime import date, timedelta
from pydantic import Field
from .calendar import Arguments, Target, execute as calendar_execute
from ..agent.store import timestamp, uid, encode


class Forecast(Arguments):
    location: str = Field(min_length=2, max_length=100)
    day: date


class Suggest(Target):
    location: str = Field(min_length=2, max_length=100)


DEFAULT_THRESHOLDS = {'rain': 70, 'high': 35, 'low': 2, 'wind': 45}
OUTDOOR = re.compile(r'户外|跑步|徒步|骑行|野餐|outdoor|running|hiking|cycling', re.I)


def risks(summary, thresholds):
    return [key for key in ('rain', 'high', 'wind') if summary[key] >= thresholds[key]] + (['low'] if summary['low'] <= thresholds['low'] else [])


class WeatherService:
    def __init__(self, store, factory, provider, clock, memory):
        self.store, self.factory, self.provider, self.clock, self.memory = store, factory, provider, clock, memory

    def thresholds(self):
        from sqlalchemy import select
        from ..models import AppPreference
        with self.factory() as db:
            row = db.get(AppPreference, 'weather_thresholds')
            return row.value if row else DEFAULT_THRESHOLDS

    def suggest(self, session_id, location):
        from ..models import CalendarSession
        from ..core.date_tasks import is_deleted
        with self.factory() as db:
            task = db.get(CalendarSession, session_id)
            if not task or is_deleted(task) or task.archived_at or task.status != 'open' or not task.planned_date:
                raise ValueError('天气建议需要有效的未完成日历任务')
            version, day = task.version, task.planned_date
        forecast = self.provider.forecast(location, day)
        crossed = risks(forecast, self.thresholds())
        if not crossed:
            return {'status': 'quiet', 'risks': []}
        dedupe = hashlib.sha256(encode([session_id, str(day), location.casefold(), sorted(crossed)]).encode()).hexdigest()
        summary = {'weather': forecast, 'risks': crossed, 'message': '预报达到提醒阈值，可选择改期或室内活动。'}
        with self.store.connection() as db:
            db.execute('INSERT OR IGNORE INTO adjustment_suggestions(id,dedupe,session_id,version,summary,created) VALUES(?,?,?,?,?,?)',
                       (uid('weather'), dedupe, session_id, version, encode(summary), timestamp()))
            return dict(db.execute('SELECT * FROM adjustment_suggestions WHERE dedupe=?', (dedupe,)).fetchone())

    def check(self, location=None):
        from ..core.date_tasks import today
        tomorrow = today(self.clock()) + timedelta(days=1)
        tasks = calendar_execute(self.factory, 'read_calendar', {'start': str(tomorrow), 'end': str(tomorrow)}, '', None, self.clock())['tasks']
        relevant = [t for t in tasks if t['status'] == 'open' and OUTDOOR.search(t['title']+' '+t['session_goal'])]
        if not relevant:
            return {'status': 'quiet', 'checked': 0, 'suggestions': []}
        if not location:
            temporary = self.store.rows('SELECT content FROM short_term_memories WHERE expires>? AND content LIKE ? ORDER BY expires DESC', (timestamp(), 'location:%'))
            homes = [m['content'][9:] for m in self.memory.list('profile') if m['content'].startswith('location:')]
            places = [m['content'][9:] for m in temporary] or homes
            if len(set(places)) != 1:
                return {'status': 'waiting_for_user', 'message': '请在记忆中心设置唯一地点，格式 location:Melbourne, Victoria', 'checked': 0}
            location = places[0]
        return {'status': 'completed', 'checked': len(relevant), 'suggestions': [self.suggest(t['id'], location) for t in relevant]}
