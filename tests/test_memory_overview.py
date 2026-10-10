import json
import tempfile
import unittest
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.memory.overview import overview, overview_status, KINDS
from calendar_agent.memory.service import MemoryService


class PortraitModel:
    def __init__(self):
        self.calls = []
        self.callback = None
        self.bad_id = False

    def complete(self, messages):
        data = json.loads(messages[-1]['content'])
        self.calls.append(data)
        if self.callback:
            self.callback()
        return json.dumps({'sections': [
            {'kind': kind, 'text': '你热爱清淡的饮食。' if kind == 'passions' else '',
             'memory_ids': [('invalid' if self.bad_id else data['memories'][0]['id'])] if kind == 'passions' else []}
            for kind in KINDS]})


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model = PortraitModel()
        self.now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        self.app = create_app(Path(self.temp.name)/'test.db', self.model, lambda: self.now, scheduler_enabled=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.service = self.app.state.memory_service
        self.service.embedding = None

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def add(self, content='喜欢清淡的食物', expires=None):
        return self.service.add(content, 'preference', [self.service.source(content)], expires=expires)

    def get(self):
        return overview(self.service, self.model, clock=lambda: self.now)

    def test_empty_and_expired_have_no_model_call(self):
        self.add(expires=self.now.timestamp()-1)
        self.assertTrue(all(not s['text'] for s in self.get()['sections']))
        self.assertEqual(self.model.calls, [])

    def test_saved_summary_survives_service_restart_and_forgetting_erases_it(self):
        memory_id = self.add()
        result = self.get()
        with self.service.store.connection() as db:
            db.execute('UPDATE memories SET accesses=accesses+1,accessed=? WHERE id=?', (self.now.timestamp(),memory_id))
        restarted = MemoryService(self.service.store)
        self.assertEqual(overview(restarted, self.model, clock=lambda:self.now), result)
        self.assertEqual(len(self.model.calls), 1)
        self.service.forget(memory_id)
        self.assertEqual(self.service.store.rows('SELECT * FROM memory_portrait_cache'), [])
        self.assertTrue(all(not s['text'] for s in overview(restarted, self.model, clock=lambda:self.now)['sections']))

    def test_slow_model_does_not_block_status_or_duplicate_generation(self):
        self.add()
        release = threading.Event()
        self.model.callback = lambda: release.wait(5)
        try:
            started = time.monotonic()
            for _ in range(3):
                response = self.client.post('/api/memory/overview/status', json={})
                self.assertEqual(response.json()['status'], 'pending')
            self.assertLess(time.monotonic()-started, 1)
        finally:
            release.set()
        deadline = time.monotonic()+3
        while self.service._portrait_job['status']=='pending' and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(overview_status(self.service,self.model,clock=lambda:self.now)['status'], 'ready')
        self.assertEqual(len(self.model.calls), 1)

    def test_failure_is_reported_without_automatic_retry_loop(self):
        self.add()
        self.model.bad_id = True
        overview_status(self.service, self.model, clock=lambda:self.now)
        deadline = time.monotonic()+3
        while self.service._portrait_job['status']=='pending' and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(overview_status(self.service,self.model,clock=lambda:self.now)['status'], 'failed')
        self.assertEqual(len(self.model.calls), 1)

    def test_cache_correction_and_forgetting(self):
        memory_id = self.add()
        self.assertEqual(self.get(), self.get())
        self.assertEqual(len(self.model.calls), 1)
        self.service.correct(memory_id, '喜欢清淡、少油的食物', 1)
        self.get()
        self.assertEqual(len(self.model.calls), 2)
        self.assertEqual(self.model.calls[-1]['memories'][0]['content'], '喜欢清淡、少油的食物')
        for memory in self.service.list():
            self.service.forget(memory['id'])
        self.assertTrue(all(not s['text'] for s in self.get()['sections']))

    def test_reject_untraceable_or_stale_output(self):
        memory_id = self.add()
        self.model.bad_id = True
        response = self.client.post('/api/memory/overview', json={})
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.service.overview_cache)
        self.model.bad_id = False
        self.model.callback = lambda: self.service.correct(memory_id, '喜欢咖啡', 1)
        with self.assertRaisesRegex(ValueError, '发生变化'):
            self.get()
        self.assertIsNone(self.service.overview_cache)

    def test_one_off_plans_cannot_become_passions(self):
        self.service.add('计划去喝奶茶', 'task', [self.service.source('计划去喝奶茶')])
        passions = next(s for s in self.get()['sections'] if s['kind'] == 'passions')
        self.assertEqual(passions['text'], '')
        self.assertEqual(passions['memory_ids'], [])

    def test_portrait_speaks_in_five_person_dimensions(self):
        self.add()
        self.assertEqual([s['kind'] for s in self.get()['sections']], list(KINDS))
        self.assertEqual(KINDS, ('personality', 'values', 'goals', 'passions', 'relationships'))

    def test_date_and_calendar_state_invalidate(self):
        self.add()
        with self.service.store.connection() as db:
            db.execute("INSERT INTO calendar_sessions(id,title,session_goal,agenda_json,status,version,created_at,updated_at,migration_metadata) VALUES(1,'私密标题','','[]','open',1,'2026-09-01','2026-09-01','{}')")
        self.get()
        self.assertEqual(self.model.calls[-1]['today'], '2026-09-16')
        with self.service.store.connection() as db:
            db.execute("UPDATE calendar_sessions SET status='completed' WHERE id=1")
        self.get()
        self.assertEqual(len(self.model.calls), 2)
        self.assertEqual(self.model.calls[-1]['task_statuses'][0]['status'], 'completed')
        self.assertNotIn('私密标题', json.dumps(self.model.calls))
        self.now = datetime(2026, 9, 17, tzinfo=timezone.utc)
        self.get()
        self.assertEqual(len(self.model.calls), 3)
