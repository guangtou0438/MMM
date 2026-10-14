import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.memory.retrieval import retrieve
from calendar_agent.memory.consolidation import apply
from calendar_agent.agent.store import timestamp

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


class Model:
    def __init__(self):
        self.outputs = []; self.calls = []; self.gate = None

    def complete(self, messages):
        self.calls.append(messages)
        if self.gate:
            self.gate.wait(5)
        result = self.outputs.pop(0) if self.outputs else {'action': 'answer', 'answer': '完成'}
        if isinstance(result, Exception):
            raise result
        return json.dumps(result, ensure_ascii=False)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.model = Model()
        self.app = create_app(Path(self.temp.name)/'test.db', self.model, lambda: NOW, scheduler_enabled=False)
        self.client = TestClient(self.app); self.client.__enter__()
        self.memory = self.app.state.memory_service
        self.memory.embedding = None

    def tearDown(self):
        if self.model.gate:
            self.model.gate.set()
        self.client.__exit__(None, None, None); self.temp.cleanup()

    def submit(self, content='查询日历', **kwargs):
        return self.client.post('/api/agent/runs', json={'content': content, 'request_id': str(time.time_ns()), **kwargs})

    def wait(self, run_id):
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            run = self.client.get('/api/agent/runs/'+run_id).json()
            if run['status'] in {'completed', 'failed', 'waiting_for_user', 'cancelled'}:
                return run
            time.sleep(.02)
        self.fail('Run did not finish')

    def test_react_tool_observation_and_sse_resume(self):
        self.model.outputs = [{'action': 'tool', 'skill': 'calendar', 'tool': 'read_calendar', 'arguments': {}}, {'action': 'answer', 'answer': '没有待办'}]
        run = self.submit().json(); end = self.wait(run['id'])
        self.assertEqual(end['status'], 'completed', end)
        self.assertEqual(end['calls'], 2)
        events = self.client.get(f"/api/agent/runs/{run['id']}/events").text
        self.assertIn('observation.delta', events); self.assertIn('answer.delta', events)
        replay = self.client.get(f"/api/agent/runs/{run['id']}/events", headers={'Last-Event-ID': '3'}).text
        self.assertNotIn('id: 1\n', replay); self.assertNotIn('id: 3\n', replay)

    def test_conversation_history_keeps_all_turns_and_groups_sessions(self):
        for _ in range(12):
            run = self.submit('重复的问题', session_key='home-a').json()
            self.assertEqual(self.wait(run['id'])['status'], 'completed')
        other = self.submit('另一个对话', session_key='home-b').json()
        self.wait(other['id'])
        transcript = self.client.get('/api/agent/conversations/messages', params={'session_key':'home-a'}).json()
        self.assertEqual(transcript['run_id'], run['id'])
        self.assertEqual(len(transcript['messages']), 24)
        self.assertEqual([m['content'] for m in transcript['messages'] if m['role']=='user'], ['重复的问题']*12)
        conversations = self.client.get('/api/agent/conversations').json()
        self.assertEqual(len(conversations), 2)
        self.assertEqual(conversations[0]['session_key'], 'home-b')
        self.assertEqual(conversations[1]['run_id'], run['id'])
        self.assertEqual(conversations[1]['title'], '重复的问题')

    def test_conversation_history_retains_clarifications(self):
        self.model.outputs = [{'action':'ask','answer':'哪个城市？'}, {'action':'answer','answer':'收到'}]
        run = self.submit('看看天气', session_key='home-clarification').json()
        self.assertEqual(self.wait(run['id'])['status'], 'waiting_for_user')
        self.client.post(f"/api/agent/runs/{run['id']}/messages", json={'content':'墨尔本'})
        self.wait(run['id'])
        next_run = self.submit('谢谢', session_key='home-clarification').json()
        self.wait(next_run['id'])
        messages = self.client.get('/api/agent/conversations/messages', params={'session_key':'home-clarification'}).json()['messages']
        self.assertEqual([m['content'] for m in messages], ['看看天气','哪个城市？','墨尔本','收到','谢谢','完成'])

    def test_home_history_survives_hourly_cleanup_but_honors_source_expiry(self):
        run = self.submit('保留对话', session_key='home-retention').json()
        self.wait(run['id'])
        followup = self.submit('继续这段对话', session_key='home-retention').json()
        self.wait(followup['id'])
        other = self.submit('无关对话', session_key='home-retention-other').json()
        self.wait(other['id'])
        self.memory.retain()
        endpoint = '/api/agent/conversations/messages'
        self.assertEqual(len(self.client.get(endpoint, params={'session_key':'home-retention'}).json()['messages']), 4)
        with self.app.state.agent_store.connection() as db:
            db.execute('UPDATE memory_sources SET purge_after=? WHERE run_id=?', (timestamp()-1,run['id']))
        self.memory.retain()
        self.assertEqual(self.client.get(endpoint, params={'session_key':'home-retention'}).json()['messages'], [])
        self.assertEqual(len(self.client.get(endpoint, params={'session_key':'home-retention-other'}).json()['messages']), 2)
        self.assertEqual([item['session_key'] for item in self.client.get('/api/agent/conversations').json()], ['home-retention-other'])
        resumed = self.submit('重新开始', session_key='home-retention').json()
        self.wait(resumed['id'])
        item = next(item for item in self.client.get('/api/agent/conversations').json() if item['session_key']=='home-retention')
        self.assertEqual(item['title'], '重新开始')

    def test_conversation_list_groups_before_limit_and_excludes_calendar(self):
        run = self.submit('最初的标题', session_key='home-list').json()
        self.wait(run['id'])
        with self.app.state.agent_store.connection() as db:
            for index in range(105):
                db.execute('''INSERT INTO agent_runs(id,request_id,fingerprint,session_key,status,model,kind,goal,created,updated)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', (f'list-{index}', f'list-request-{index}', 'test', 'home-busy', 'completed', 'glm', 'complex', '多轮对话', timestamp()+index, timestamp()+index))
                db.execute('INSERT INTO working_memory VALUES(?,?)', (f'list-{index}', json.dumps([{'role':'user','content':'多轮对话'}])))
            db.execute('''INSERT INTO agent_runs(id,request_id,fingerprint,session_key,status,model,kind,goal,created,updated)
                VALUES('calendar-test','calendar-request','test','calendar:123','completed','glm','complex','任务内聊天',?,?)''', (timestamp(),timestamp()))
        items = self.client.get('/api/agent/conversations').json()
        self.assertEqual(len(items), 2)
        self.assertEqual({item['session_key'] for item in items}, {'home-list','home-busy'})

    def test_same_session_writer_and_idempotent_submit(self):
        self.model.gate = threading.Event()
        payload = {'content': '你好', 'request_id': 'same'}
        first = self.client.post('/api/agent/runs', json=payload)
        second = self.client.post('/api/agent/runs', json=payload)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertEqual(self.submit().status_code, 409)
        self.assertEqual(self.client.post('/api/agent/runs', json=payload | {'content': '别的目标'}).status_code, 409)
        self.model.gate.set(); self.wait(first.json()['id'])

    def test_chat_and_planner_have_distinct_roles_and_permissions(self):
        chat = self.submit('讨论如何安排一周', session_key='chat:roles', kind='chat').json()
        self.wait(chat['id'])
        chat_prompt = self.model.calls[-1][0]['content']
        self.assertIn('职责是日常交流', chat_prompt)
        self.assertIn('不创建、修改或查询日历', chat_prompt)
        planner = self.submit('安排一周', session_key='planner:roles', kind='complex').json()
        self.wait(planner['id'])
        planner_prompt = self.model.calls[-1][0]['content']
        self.assertIn('职责是管理日程', planner_prompt)
        self.assertNotIn('职责是日常交流', planner_prompt)
        self.model.outputs = [{'action':'tool','skill':'calendar','tool':'read_calendar','arguments':{}}]
        blocked = self.wait(self.submit('查看日历',session_key='chat:blocked',kind='chat').json()['id'])
        self.assertEqual(blocked['status'], 'failed')
        self.assertEqual(self.app.state.agent_store.rows('SELECT * FROM tool_calls WHERE run_id=?',(blocked['id'],)), [])

    def test_resume_same_run_and_budget_not_reset(self):
        self.model.outputs = [{'action': 'ask', 'answer': '在哪个城市？'}, {'action': 'answer', 'answer': '收到地点'}]
        run = self.submit().json(); self.assertEqual(self.wait(run['id'])['status'], 'waiting_for_user')
        response = self.client.post(f"/api/agent/runs/{run['id']}/messages", json={'content': '墨尔本'})
        self.assertEqual(response.json()['id'], run['id'])
        end = self.wait(run['id']); self.assertEqual(end['calls'], 2)
        self.assertIn('墨尔本', str(self.model.calls[-1]))

    def test_repeat_and_permanent_delete_are_blocked(self):
        decision = {'action': 'tool', 'skill': 'calendar', 'tool': 'read_calendar', 'arguments': {}}
        self.model.outputs = [decision, decision]
        end = self.wait(self.submit().json()['id'])
        self.assertEqual(end['status'], 'failed'); self.assertIn('重复', end['error'])
        self.model.outputs = [{'action': 'tool', 'skill': 'calendar', 'tool': 'permanent_delete', 'arguments': {}}]
        end = self.wait(self.submit().json()['id'])
        self.assertEqual(end['status'], 'failed')
        self.assertNotIn('permanent_delete', self.app.state.agent.executor.schemas())

    def test_recurring_tasks_expand_weekdays(self):
        executor = self.app.state.agent.executor
        args = {'title': '上课', 'time_text': '18:00-21:00', 'weekdays': [3, 4, 5], 'start_date': '2026-09-07', 'until_date': '2026-09-20'}
        first = executor.execute('create_recurring_tasks', args, 'rec-1', None)
        dates = [t['planned_date'] for t in first['tasks']]
        self.assertEqual(dates, ['2026-09-09', '2026-09-10', '2026-09-11', '2026-09-16', '2026-09-17', '2026-09-18'])
        self.assertIn('18:00-21:00', first['tasks'][0]['session_goal'])
        self.assertEqual(first, executor.execute('create_recurring_tasks', args, 'rec-1', None))
        with self.assertRaises(Exception):
            executor.execute('create_recurring_tasks', args | {'until_date': '2028-01-01'}, 'rec-2', None)

    def test_recurring_daily_habit_without_end_date(self):
        result = self.app.state.agent.executor.execute('create_recurring_tasks', {'title': '每日阅读', 'weekdays': [1, 2, 3, 4, 5, 6, 7]}, 'habit', None)
        dates = [t['planned_date'] for t in result['tasks']]
        self.assertEqual((dates[0], dates[-1], len(dates)), ('2026-09-06', '2026-10-31', 56))
        self.assertEqual((result['covered_until'], result['open_ended']), ('2026-10-31', True))

    def test_tool_write_idempotency_and_version(self):
        executor = self.app.state.agent.executor
        args = {'title': '合成任务', 'planned_date': '2026-09-07'}
        first = executor.execute('create_task', args, 'write-1', None)
        second = executor.execute('create_task', args, 'write-1', None)
        self.assertEqual(first['id'], second['id'])
        with self.assertRaises(Exception):
            executor.execute('update_task', {'session_id': first['id'], 'changes': {'title': '改变'}}, 'write-2', 999)

    def test_memory_correction_forget_and_secret_filter(self):
        source = self.memory.source('I enjoy outdoor running')
        mid = self.memory.add('I enjoy outdoor running', 'preference', [source])
        self.assertEqual(retrieve(self.memory, 'outdoor')[0]['id'], mid)
        new = self.memory.correct(mid, 'I enjoy indoor reading', 1)
        self.assertEqual(retrieve(self.memory, 'outdoor'), [])
        self.assertEqual(retrieve(self.memory, 'indoor')[0]['id'], new)
        self.memory.forget(new)
        self.assertEqual(retrieve(self.memory, 'indoor'), [])
        secret = self.memory.source('API Key sk-secret-value')
        with self.assertRaises(ValueError):
            self.memory.add('key stored', 'profile', [secret])

    def test_high_risk_gate_retention_hash_lineage(self):
        source = self.memory.source('I live in Melbourne')
        operation = {'operation': 'ADD', 'source_ids': [source], 'content': 'I live in Melbourne', 'kind': 'profile'}
        with self.assertRaises(ValueError):
            apply(self.memory, operation)
        ids = apply(self.memory, operation, lambda op, evidence: True)
        self.memory.retain(timestamp()+8*86400)
        row = self.app.state.agent_store.rows('SELECT * FROM memory_sources WHERE id=?', (source,))[0]
        self.assertIsNone(row['body']); self.assertTrue(row['hash']); self.assertIn(ids[0], row['lineage'])

    def test_consolidation_retrieves_old_relevant_memory_for_update_and_split(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        for operation in ('UPDATE', 'SPLIT'):
            with self.subTest(operation=operation):
                original = self.memory.source('喜欢晚上运动，也喜欢晚上阅读。')
                old = self.memory.add('喜欢晚上运动，也喜欢晚上阅读。', 'preference', [original])
                with self.memory.store.connection() as db:
                    db.execute('UPDATE memories SET updated=? WHERE id=?', (timestamp()-86400*365, old))
                for i in range(12):
                    unrelated = self.memory.source(f'工作项目代号 {operation} {i}')
                    self.memory.add(f'工作项目代号 {operation} {i}', 'episodic', [unrelated])
                self.assertNotIn(old, [m['id'] for m in self.memory.list()[:10]])
                source = self.memory.source('把晚上运动和晚上阅读的偏好分别整理。')
                enqueue(self.memory, source, 'explicit')
                self.model.outputs = [{'operation': operation, 'source_ids': [source],
                    'targets': {old: 1}, 'kind': 'preference', 'content': '喜欢晚上运动和晚上阅读。',
                    'fragments': ['喜欢晚上运动。', '喜欢晚上阅读。'] if operation == 'SPLIT' else []},
                    {'approved': True}]
                offset = len(self.model.calls)
                result = process_one(self.memory, self.model)
                self.assertEqual(result['status'], 'done', result)
                proposed = json.loads(self.model.calls[offset][1]['content'])['existing']
                verified = json.loads(self.model.calls[offset+1][1]['content'])['existing_memories']
                self.assertEqual(proposed, verified)
                self.assertIn(old, [m['id'] for m in proposed])
                self.assertFalse(any('工作项目' in m['content'] for m in proposed))
                self.assertEqual(len(result['memory_ids']), 2 if operation == 'SPLIT' else 1)
                row = self.memory.store.rows('SELECT active,accesses FROM memories WHERE id=?', (old,))[0]
                self.assertEqual(row, {'active': 0, 'accesses': 0})

    def test_consolidation_rejects_unretrieved_target(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        old_source = self.memory.source('喜欢晚上运动')
        old = self.memory.add('喜欢晚上运动', 'preference', [old_source])
        source = self.memory.source('项目代号白桦')
        enqueue(self.memory, source, 'explicit')
        self.model.outputs = [{'operation': 'UPDATE', 'targets': {old: 1},
                               'source_ids': [source], 'kind': 'preference', 'content': '错误覆盖'}]
        result = process_one(self.memory, self.model)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['diagnostics']['stage'], 'target_validation')
        self.assertEqual(self.memory.list()[0]['content'], '喜欢晚上运动')

    def test_maintenance_semantic_retrieval_filters_invalid_without_access_changes(self):
        from array import array
        from calendar_agent.memory.retrieval import rebuild
        class Embedding:
            name = 'test'; version = '1'; dimension = 3
            def encode(self, text):
                return array('f', [1., 0., 0.] if text in {'晚间锻炼', 'after dusk exercise'}
                             else [0., 1., 0.]).tobytes()
        self.memory.embedding = Embedding()
        source = self.memory.source('晚间锻炼')
        relevant = self.memory.add('晚间锻炼', 'procedural', [source])
        other_source = self.memory.source('gardening')
        self.memory.add('gardening', 'episodic', [other_source])
        expired = self.memory.add('after dusk exercise', 'preference', [source], expires=timestamp()-1)
        forgotten_source = self.memory.source('after dusk exercise')
        self.memory.add('after dusk exercise', 'preference', [forgotten_source])
        with self.memory.store.connection() as db:
            db.execute('UPDATE memory_sources SET forgotten=1 WHERE id=?', (forgotten_source,))
        rebuild(self.memory)
        hits = retrieve(self.memory, 'after dusk exercise', maintenance=True)
        self.assertEqual([m['id'] for m in hits], [relevant])
        self.assertNotIn(expired, [m['id'] for m in hits])
        self.assertEqual(retrieve(self.memory, 'after dusk exercise'), [])
        self.assertTrue(all(m['accesses'] == 0 for m in self.memory.list()))

    def test_consolidation_diagnostics_distinguish_invalid_json_and_root_shape(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        for raw, expected_type in [('PRIVATE_PAYLOAD invalid JSON', 'json_invalid'),
                                   ('[]', 'model_type')]:
            with self.subTest(expected_type=expected_type):
                source = self.memory.source('Synthetic diagnostic source')
                enqueue(self.memory, source, 'explicit')
                class RawModel:
                    def complete(self, messages):
                        return raw
                result = process_one(self.memory, RawModel())
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['diagnostics']['stage'], 'operation_validation')
                self.assertEqual(result['diagnostics']['validation_errors'][0]['type'], expected_type)
                self.assertIn(expected_type, result['error'])
                self.assertNotIn('PRIVATE_PAYLOAD', json.dumps(result))
                self.assertEqual(self.memory.list(), [])

    def test_consolidation_reports_verifier_json_failure_without_writing(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        source = self.memory.source('Synthetic bicycle name is Ginkgo')
        enqueue(self.memory, source, 'explicit')
        responses = iter([json.dumps({'operation': 'ADD', 'source_ids': [source],
                                      'kind': 'profile', 'content': 'Synthetic bicycle name is Ginkgo'}),
                          'PRIVATE_VERIFIER invalid JSON'])
        class RawModel:
            def complete(self, messages):
                return next(responses)
        result = process_one(self.memory, RawModel())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['diagnostics']['stage'], 'verification_validation')
        self.assertEqual(result['diagnostics']['error_type'], 'JSONDecodeError')
        self.assertNotIn('PRIVATE_VERIFIER', json.dumps(result))
        self.assertEqual(self.memory.list(), [])

    def test_memory_prohibition_does_not_match_positive_compound_words(self):
        for text in ('请分别记住自行车和读书小组的名字。', '请特别记住自行车叫银杏。',
                     '三条信息以 fragments 形式分别记录，后续可以单独纠正。'):
            with self.subTest(text=text):
                source = self.memory.source(text)
                memory_id = self.memory.add(text, 'episodic', [source])
                self.assertTrue(any(m['id']==memory_id for m in self.memory.list()))
        for text in ('别记住这件事', '请别记录这个名字', '这条不要记', '不许记住',
                     '请分别记录，但这条别记', 'do not remember this', "don't remember this"):
            with self.subTest(text=text):
                source = self.memory.source(text)
                with self.assertRaises(ValueError):
                    self.memory.add('Blocked source must stay blocked', 'episodic', [source])

    def test_replay_glm_separate_facts_response_is_not_a_forget_request(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        source = self.memory.source('自行车叫银杏；读书小组叫雾松；最喜欢围棋。')
        enqueue(self.memory, source, 'explicit')
        # Replays the significant fields of the captured live GLM failure.
        self.model.outputs = [{'operation': 'ADD', 'kind': 'episodic',
            'content': '用户明确要求保存三条彼此独立的信息，以便日后单独纠正：(1) 自行车名为「银杏」；(2) 读书小组名为「雾松」；(3) 最喜欢的棋类是围棋。三条信息以 fragments 形式分别记录，后续如需修改某一项可单独 UPDATE 而不影响其他项。',
            'fragments': ['自行车名叫银杏', '读书小组名叫雾松', '最喜欢的棋类是围棋'],
            'certainty': 'reported', 'confidence': .95, 'sensitivity': 'normal',
            'expires': None, 'source_ids': [source], 'targets': {}}]
        result = process_one(self.memory, self.model)
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual(len(result['memory_ids']), 1)

    def test_consolidation_accepts_captured_trailing_fence_and_verifier_fence(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        source = self.memory.source('自行车叫银杏；读书小组叫雾松；最喜欢围棋。')
        enqueue(self.memory, source, 'explicit')
        operation = {'operation': 'ADD', 'source_ids': [source], 'kind': 'profile',
                     'content': '自行车叫银杏；读书小组叫雾松；最喜欢围棋。'}
        # Actual captured GLM failure: valid object followed by four backticks.
        responses = iter([json.dumps(operation, ensure_ascii=False)+'\n````',
                          '```json\n{"approved":true}\n```'])
        class RawModel:
            def complete(self, messages):
                return next(responses)
        result = process_one(self.memory, RawModel())
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual(result['calls'], 2)
        self.assertEqual(len(result['memory_ids']), 1)

    def test_consolidation_does_not_ignore_second_object_or_prose(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        for tail in ('\n{}', '\nignore this explanation'):
            with self.subTest(tail=tail):
                source = self.memory.source('Synthetic fixture')
                enqueue(self.memory, source, 'explicit')
                raw = json.dumps({'operation': 'ADD', 'source_ids': [source],
                                  'content': 'Synthetic fixture'}) + tail
                class RawModel:
                    def complete(self, messages):
                        return raw
                result = process_one(self.memory, RawModel())
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(self.memory.list(), [])

    def test_consolidation_regenerates_captured_schema_echo_once(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        source = self.memory.source('自行车叫银杏；读书小组叫雾松；最喜欢围棋。')
        enqueue(self.memory, source, 'explicit')
        operation = {'operation': 'ADD', 'source_ids': [source], 'kind': 'profile',
                     'content': '自行车叫银杏；读书小组叫雾松；最喜欢围棋。'}
        # Captured original-input failure: Schema wrapper with missing final brace.
        broken = json.dumps({'additionalProperties': False, 'properties': operation})[:-1]
        responses = iter([broken, json.dumps(operation), '{"approved":true}'])
        prompts = []
        class RawModel:
            def complete(self, messages):
                prompts.append(messages)
                return next(responses)
        result = process_one(self.memory, RawModel())
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual(result['calls'], 3)
        self.assertEqual(result['generation_errors'][0]['validation_errors'][0]['type'], 'json_invalid')
        self.assertIn('validation_errors', prompts[1][-1]['content'])
        self.assertEqual(len(self.memory.list()), 1)

    def test_consolidation_regeneration_is_bounded_and_preserves_source_checks(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        for replacement in ('invalid JSON', json.dumps({'operation': 'ADD', 'source_ids': ['foreign'],
                                                       'content': 'Synthetic unsupported fact'})):
            with self.subTest(replacement=replacement):
                source = self.memory.source('Synthetic original source')
                enqueue(self.memory, source, 'explicit')
                responses = iter(['invalid JSON', replacement])
                class RawModel:
                    def complete(self, messages):
                        return next(responses)
                result = process_one(self.memory, RawModel())
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['calls'], 2)
                self.assertEqual(self.memory.list(), [])

    def test_consolidation_retries_split_without_an_existing_target(self):
        from calendar_agent.memory.consolidation import enqueue
        from calendar_agent.memory.jobs import process_one
        source = self.memory.source('自行车叫银杏；读书小组叫雾松；最喜欢围棋。')
        enqueue(self.memory, source, 'explicit')
        operation = {'operation': 'SPLIT', 'source_ids': [source], 'kind': 'profile',
                     'content': '自行车叫银杏；读书小组叫雾松；最喜欢围棋。',
                     'fragments': ['自行车叫银杏', '读书小组叫雾松', '最喜欢围棋']}
        self.model.outputs = [operation, operation | {'operation': 'ADD'}, {'approved': True}]
        result = process_one(self.memory, self.model)
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual(result['calls'], 3)
        self.assertEqual(result['generation_errors'][0]['validation_errors'][0]['type'], 'memory_target_required')
        self.assertEqual(len(self.memory.list()), 1)

    def test_weather_no_outdoor_zero_provider_and_dedupe(self):
        class Weather:
            calls = 0
            def forecast(self, location, day):
                self.calls += 1
                return {'location': location, 'date': str(day), 'rain': 90, 'high': 20, 'low': 10, 'wind': 15}
        service = self.app.state.weather_service; provider = Weather(); service.provider = provider
        self.assertEqual(service.check('Melbourne')['checked'], 0); self.assertEqual(provider.calls, 0)
        task = self.app.state.agent.executor.execute('create_task', {'title': '户外跑步', 'planned_date': '2026-09-07'}, 'outdoor', None)
        service.check('Melbourne'); service.check('Melbourne')
        suggestions = self.client.get('/api/weather/suggestions').json()
        self.assertEqual(len(suggestions), 1)
        actual = self.client.get('/api/sessions/'+str(task['id'])).json()
        self.assertEqual(actual['planned_date'], '2026-09-07'); self.assertEqual(actual['version'], task['version'])

    def test_exports_exclude_private_data_and_leave_no_files(self):
        self.app.state.agent.executor.execute('create_task', {'title': '=1+2', 'planned_date': '2026-09-06', 'session_goal': 'PRIVATE CONTEXT'}, 'export-task', None)
        before = set(Path(self.temp.name).iterdir())
        for format in ('csv', 'ics', 'pdf'):
            response = self.client.post('/api/calendar/exports', json={'scope': 'month', 'format': format})
            self.assertEqual(response.status_code, 200, response.text[:100] if format != 'pdf' else '')
            self.assertNotIn(b'PRIVATE CONTEXT', response.content)
            if format == 'pdf': self.assertTrue(response.content.startswith(b'%PDF'))
            if format == 'csv': self.assertIn("'=1+2", response.text)
        self.assertEqual(before, set(Path(self.temp.name).iterdir()))

    def test_cancel_prevents_late_model_write(self):
        self.model.gate = threading.Event()
        self.model.outputs = [{'action': 'tool', 'skill': 'calendar', 'tool': 'create_task', 'arguments': {'title': 'Must not exist'}}]
        run = self.submit().json()
        deadline = time.monotonic()+3
        while not self.model.calls and time.monotonic() < deadline: time.sleep(.01)
        self.client.post(f"/api/agent/runs/{run['id']}/cancel", json={})
        self.model.gate.set(); time.sleep(.1)
        self.assertEqual(self.wait(run['id'])['status'], 'cancelled')
        self.assertEqual(self.app.state.agent.executor.execute('read_calendar', {}, '', None)['tasks'], [])

    def test_restart_preserves_run_and_waits_before_continuing(self):
        runtime = self.app.state.agent
        runtime.close()
        run = runtime.submit('恢复测试', 'restart', 'restart')
        with runtime.store.connection() as db:
            db.execute("UPDATE agent_runs SET status='running',calls=1 WHERE id=?", (run['id'],))
        from calendar_agent.agent.runtime import Runtime
        replacement = Runtime(runtime.store, self.memory, runtime.executor, self.model)
        self.app.state.agent = replacement; replacement.start()
        self.assertEqual(replacement.store.run(run['id'])['status'], 'waiting_for_user')
        replacement.resume(run['id'], '继续')
        self.assertEqual(self.wait(run['id'])['calls'], 2)

    def test_continuation_ignores_previous_usage_totals(self):
        self.model.outputs = [{'action': 'ask', 'answer': '需要补充信息'}]
        run = self.submit(kind='chat').json(); self.wait(run['id'])
        with self.app.state.agent_store.connection() as db:
            db.execute('UPDATE agent_runs SET calls=100,tokens=1000000,elapsed=10000 WHERE id=?', (run['id'],))
        response = self.client.post(f"/api/agent/runs/{run['id']}/messages", json={'content':'补充'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['id'], run['id'])
        final = self.wait(run['id'])
        self.assertEqual(final['status'], 'completed', final)
        self.assertEqual(final['calls'], 101)

    def test_more_than_six_model_and_skill_calls(self):
        self.model.outputs = [{'action':'tool','skill':'calendar','tool':'read_calendar',
                              'arguments':{'start':f'2026-09-{day:02d}'}} for day in range(7,14)]
        self.model.outputs.append({'action':'answer','answer':'完成七次查询'})
        result = self.wait(self.submit().json()['id'])
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['calls'], 8)

    def test_submit_to_waiting_session_resumes_without_unique_error(self):
        self.model.outputs=[{'action':'ask','answer':'请补充地点'}]
        run=self.submit(session_key='task-conversation').json();self.wait(run['id'])
        payload={'content':'墨尔本','session_key':'task-conversation','request_id':'followup-once'}
        reply=self.client.post('/api/agent/runs',json=payload)
        self.assertEqual(reply.status_code,202,reply.text)
        self.assertEqual(reply.json()['id'],run['id'])
        self.wait(run['id'])
        replay=self.client.post('/api/agent/runs',json=payload)
        self.assertEqual(replay.json()['id'],run['id'])
        messages=self.client.get(f"/api/agent/runs/{run['id']}/conversation").json()
        self.assertEqual(sum(m['content']=='墨尔本' for m in messages),1)

    def test_permanent_delete_requires_ui_and_removes_new_memory(self):
        task = self.app.state.agent.executor.execute('create_task', {'title': 'Synthetic secret task'}, 'delete-memory', None)
        task = self.client.post(f"/api/sessions/{task['id']}/archive", json={'request_id': 'archive', 'version': task['version']}).json()
        endpoint = f"/api/sessions/{task['id']}/delete-permanently/confirm"
        payload = {'request_id': 'delete', 'version': task['version']}
        self.assertEqual(self.client.post(endpoint, json=payload).status_code, 403)
        self.assertEqual(self.client.post(endpoint, json=payload, headers={'X-UI-Action': 'permanent-delete'}).status_code, 200)
        self.assertFalse(any('Synthetic secret task' in m['content'] for m in self.memory.list()))

    def test_locked_memory_and_forgotten_sources_block_operations(self):
        source = self.memory.source('Synthetic reading preference')
        mid = self.memory.add('Synthetic reading preference', 'preference', [source])
        self.client.post(f'/api/memories/{mid}/lock', json={'enabled': True})
        with self.assertRaises(ValueError):
            apply(self.memory, {'operation': 'UPDATE', 'targets': {mid: 2}, 'source_ids': [source], 'content': 'Changed', 'kind': 'preference'}, lambda op, ev: True)
        self.memory.forget(mid)
        with self.assertRaises(ValueError):
            apply(self.memory, {'operation': 'ADD', 'source_ids': [source], 'content': 'Revived', 'kind': 'preference'})

    def test_background_cannot_modify_calendar(self):
        from calendar_agent.skills.registry import authorize
        with self.assertRaises(PermissionError):
            authorize(self.app.state.agent_store, 'calendar', 'update_task', background=True)

    def test_semantic_recall_with_real_sqlite_vec(self):
        # Deterministic vectors exercise the real SQLite extension without downloading a model in unit tests.
        import struct
        from calendar_agent.memory.retrieval import rebuild
        class Embedding:
            name='synthetic-vectors'; version='1'; dimension=3
            def encode(self, value): return struct.pack('3f', 1., 0., 0.)
        self.memory.embedding = Embedding()
        source = self.memory.source('Lives in Melbourne')
        mid = self.memory.add('Lives in Melbourne', 'profile', [source])
        rebuild(self.memory)
        self.assertEqual(retrieve(self.memory, '常住城市', mode='keyword'), [])
        self.assertEqual(retrieve(self.memory, '常住城市')[0]['id'], mid)
        # Retired vectors must be filtered before the vector top-k cutoff.
        with self.app.state.agent_store.connection() as db:
            db.execute('UPDATE memories SET active=0 WHERE id=?', (mid,))
        self.assertEqual(retrieve(self.memory, '常住城市'), [])

    def test_stream_partial_json_escape_and_protocol_normalization(self):
        from calendar_agent.agent.events import partial_answer
        from calendar_agent.providers.protocol import decision_json
        self.assertEqual(partial_answer('{"action":"answer","answer":"你\\n好'), '你\n好')
        self.assertEqual(json.loads(decision_json('{"answer":"hello"}'))['action'], 'answer')
        self.assertNotIn('tool', json.loads(decision_json('{"answer":"hello"}')))
        self.assertEqual(json.loads(decision_json('{"action":"answer","answer":"Created","citation_ids":[12,"mem_valid"]}'))['citation_ids'], ['mem_valid'])

    def test_failed_index_rebuild_preserves_serving_version(self):
        import struct
        from calendar_agent.memory.retrieval import rebuild
        class Embedding:
            name='synthetic-vectors'; version='1'; dimension=3
            def encode(self, value): return struct.pack('3f', 1., 0., 0.)
        class Broken(Embedding):
            version='2'
            def encode(self, value): raise RuntimeError('download unavailable')
        self.memory.embedding = Embedding()
        sid = self.memory.source('Melbourne residence')
        mid = self.memory.add('Melbourne residence', 'profile', [sid])
        rebuild(self.memory)
        with self.assertRaises(RuntimeError): rebuild(self.memory, Broken())
        self.assertEqual(self.app.state.agent_store.rows('SELECT version FROM active_embedding')[0]['version'], '1')
        self.assertEqual(retrieve(self.memory, '居住地')[0]['id'], mid)

    def test_forget_retracts_descendant_with_independent_source(self):
        sid = self.memory.source('Original synthetic preference')
        parent = self.memory.add('Original synthetic preference', 'preference', [sid])
        child_source = self.memory.source('Derived synthetic preference')
        child = self.memory.add('Derived synthetic preference', 'preference', [child_source], parents=[parent])
        self.memory.forget(parent)
        self.assertFalse(any(m['id']==child for m in self.memory.list()))
        self.assertEqual(self.app.state.agent_store.rows('SELECT forgotten FROM memory_sources WHERE id=?', (child_source,))[0]['forgotten'], 1)

    def test_invalid_location_ttl_creates_no_source(self):
        before = len(self.app.state.agent_store.rows('SELECT id FROM memory_sources'))
        response = self.client.post('/api/memory/location', json={'location':'Synthetic town','expires':1})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.app.state.agent_store.rows('SELECT id FROM memory_sources')), before)

    def test_atomic_compensation_and_non_atomic_preservation(self):
        from calendar_agent.skills.registry import SKILLS, Skill
        SKILLS['atomic_demo'] = Skill('atomic_demo', ('create_task',), 'Synthetic atomic flow', atomic=True, writes=True)
        try:
            for skill, title in [('atomic_demo', 'Atomic task'), ('calendar', 'Normal task')]:
                self.model.outputs = [{'action': 'tool', 'skill': skill, 'tool': 'create_task', 'arguments': {'title': title}},
                                      {'action': 'tool', 'skill': skill, 'tool': 'permanent_delete'}]
                result = self.wait(self.submit(content=title).json()['id'])
                self.assertEqual(result['status'], 'failed')
            tasks = self.app.state.agent.executor.execute('read_calendar', {}, '', None)['tasks']
            self.assertEqual([t['title'] for t in tasks], ['Normal task'])
            calls = self.app.state.agent_store.rows("SELECT status FROM tool_calls WHERE skill='atomic_demo'")
            self.assertEqual(calls[0]['status'], 'compensated')
        finally:
            del SKILLS['atomic_demo']

    def test_global_model_concurrency_is_two(self):
        self.model.gate = threading.Event()
        ids = [self.submit(session_key='parallel:'+str(i)).json()['id'] for i in range(3)]
        deadline = time.monotonic()+3
        while len(self.model.calls)<2 and time.monotonic()<deadline: time.sleep(.02)
        self.assertEqual(len(self.model.calls), 2)
        self.assertEqual(sum(self.app.state.agent_store.run(i)['status']=='queued' for i in ids), 1)
        self.model.gate.set()
        for run_id in ids: self.assertEqual(self.wait(run_id)['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
