import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.providers.llm import LLMProvider
from calendar_agent.providers.function_calling import decision_tools
from calendar_agent.memory.consolidation import enqueue
from calendar_agent.memory.jobs import process_one


TOOL = {'type': 'function', 'function': {'name': 'submit', 'parameters': {
    'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}}}


class NativeProviderTests(unittest.TestCase):
    def test_request_uses_tools_and_collects_streamed_arguments(self):
        captured = []
        chunks = [
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call_1', 'function': {'name': 'submit', 'arguments': '{"value":'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"test"}'}}]}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}], 'usage': {'total_tokens': 17}}]
        def respond(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, text='\n\n'.join('data: '+json.dumps(x) for x in chunks)+'\n\ndata: [DONE]\n\n')
        client_type = httpx.Client
        with patch.dict(os.environ, {'MMM_OFFLINE': '0', 'ZHIPU_API_KEY': 'synthetic-test-key'}), \
             patch('calendar_agent.providers.llm.httpx.Client', side_effect=lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw)):
            events = list(LLMProvider().stream_functions([{'role':'user','content':'fixture'}], 'glm', [TOOL]))
        self.assertEqual(captured[0]['tools'], [TOOL])
        self.assertEqual(captured[0]['tool_choice'], 'auto')
        self.assertNotIn('response_format', captured[0])
        self.assertIn(('usage', 17), events)
        call = events[-1][1]
        self.assertEqual(call['arguments'], {'value':'test'})
        self.assertEqual(call['message']['tool_calls'][0]['id'], 'call_1')

    def test_rejects_plain_json_unknown_multiple_and_truncated_calls(self):
        class Provider(LLMProvider):
            def _stream(self, *args):
                yield from self.events
        cases = [
            [('delta', {'content':'{"value":"test"}'}), ('finish','stop')],
            [('delta', {'tool_calls':[{'id':'a','function':{'name':'unknown','arguments':'{}'}}]}), ('finish','tool_calls')],
            [('delta', {'tool_calls':[{'index':i,'id':str(i),'function':{'name':'submit','arguments':'{}'}} for i in range(2)]}), ('finish','tool_calls')],
            [('delta', {'tool_calls':[{'id':'a','function':{'name':'submit','arguments':'{}'}}]}), ('finish','length')]]
        for events in cases:
            with self.subTest(events=events):
                provider = Provider(); provider.events = events
                with self.assertRaises(ValueError):
                    list(provider.stream_functions([], 'glm', [TOOL]))

    def test_answer_arguments_stream_without_exposing_reasoning(self):
        class Provider(LLMProvider):
            def _stream(self, *args):
                yield 'delta', {'reasoning_content':'PRIVATE', 'tool_calls':[{'id':'a','function':{'name':'complete_answer','arguments':'{"answer":"你好'}}]}
                yield 'delta', {'tool_calls':[{'function':{'arguments':'世界","citation_ids":[]}'}}]}
                yield 'finish', 'tool_calls'
        tools, _ = decision_tools({}, [])
        events = list(Provider().stream_functions([], 'glm', tools))
        self.assertEqual(''.join(v for k,v in events if k=='answer.delta'), '你好世界')
        self.assertNotIn('PRIVATE', ''.join(v for k,v in events if k=='answer.delta'))


class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        class NativeModel:
            def __init__(self):
                self.requests = []; self.structured = []
            def stream_functions(self, messages, model, tools, max_tokens=None):
                self.requests.append((messages, tools))
                if len(self.requests)==1:
                    name, arguments = 'calendar__read_calendar', {}
                else:
                    name, arguments = 'complete_answer', {'answer':'已读取日历', 'citation_ids':[]}
                message = {'role':'assistant','content':None,'reasoning_content':'private provider reasoning',
                           'tool_calls':[{'id':'native_'+str(len(self.requests)), 'type':'function',
                                          'function':{'name':name,'arguments':json.dumps(arguments)}}]}
                yield 'usage', 10
                yield 'function', {'name':name, 'arguments':arguments, 'message':message}
            def stream_structured(self, messages, model, name, schema, max_tokens=None):
                self.structured.append((messages, name, schema))
                if name=='propose_memory_operation':
                    source = json.loads(messages[1]['content'])['source_id']
                    result = {'operation':'ADD', 'kind':'profile','source_ids':[source],'content':'自行车叫银杏'}
                else:
                    result = {'approved':True,'reason':'来源支持'}
                yield 'usage', 10
                yield 'content', json.dumps(result)
        self.model = NativeModel()
        self.app = create_app(Path(self.temp.name)/'test.db', self.model, scheduler_enabled=False)
        self.client = TestClient(self.app); self.client.__enter__()
        self.memory = self.app.state.memory_service; self.memory.embedding = None

    def tearDown(self):
        self.client.__exit__(None,None,None); self.temp.cleanup()

    def test_memory_schema_is_api_argument_and_verification_still_runs(self):
        source = self.memory.source('自行车叫银杏')
        enqueue(self.memory, source, 'explicit')
        result = process_one(self.memory, self.model)
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual(result['calls'], 2)
        self.assertEqual([r[1] for r in self.model.structured], ['propose_memory_operation','verify_memory_operation'])
        self.assertIn('source_ids', self.model.structured[0][2]['required'])
        self.assertNotIn('"properties":', self.model.structured[0][0][0]['content'])

    def test_native_memory_protocol_failure_retries_once(self):
        from calendar_agent.providers.llm import FunctionCallProtocolError
        original = self.model.stream_structured
        attempts = []
        def flaky(*args):
            attempts.append(1)
            if len(attempts)==1:
                raise FunctionCallProtocolError('function_call_incomplete','missing call')
            yield from original(*args)
        self.model.stream_structured = flaky
        source = self.memory.source('自行车叫银杏')
        enqueue(self.memory, source, 'explicit')
        result = process_one(self.memory, self.model)
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual(result['calls'], 3)
        self.assertEqual(result['generation_errors'][0]['validation_errors'][0]['type'], 'function_call_incomplete')

    def test_tool_result_preserves_native_call_id_and_final_answer(self):
        run = self.client.post('/api/agent/runs', json={'content':'读取日历','request_id':'native-test','kind':'complex'}).json()
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            result = self.client.get('/api/agent/runs/'+run['id']).json()
            if result['status'] in {'completed','failed'}:
                break
            time.sleep(.05)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['answer'], '已读取日历')
        messages, tools = self.model.requests[1]
        self.assertEqual(messages[-1]['role'], 'tool')
        self.assertEqual(messages[-1]['tool_call_id'], messages[-2]['tool_calls'][0]['id'])
        self.assertEqual(messages[-2]['reasoning_content'], 'private provider reasoning')
        self.assertIsNone(messages[-2]['content'])
        self.assertNotIn('"action"', json.dumps(messages[-2]))
        events = self.app.state.agent_store.rows('SELECT data FROM agent_events WHERE run_id=?', (run['id'],))
        self.assertNotIn('private provider reasoning', json.dumps(events))
        conversation = self.client.get('/api/agent/runs/'+run['id']+'/conversation').json()
        self.assertEqual(conversation, [
            {'role':'user', 'content':'读取日历'},
            {'role':'assistant', 'content':'已读取日历'}])

        follow_up = self.client.post('/api/agent/runs', json={
            'content':'谢谢', 'request_id':'native-follow-up', 'kind':'complex'}).json()
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            continued = self.client.get('/api/agent/runs/'+follow_up['id']).json()
            if continued['status'] in {'completed','failed'}:
                break
            time.sleep(.05)
        self.assertEqual(continued['status'], 'completed', continued)
        next_messages, _ = self.model.requests[2]
        self.assertFalse(any(message.get('tool_calls') for message in next_messages))
        self.assertEqual(next_messages[-2:], [
            {'role':'assistant', 'content':'已读取日历'},
            {'role':'user', 'content':'谢谢'}])

    def test_disabled_skills_and_chat_do_not_advertise_write_tools(self):
        tools, _ = decision_tools(self.app.state.agent.executor.schemas(), [], chat=False)
        self.assertEqual({t['function']['name'] for t in tools}, {'complete_answer','request_clarification'})
        from calendar_agent.skills.registry import available
        tools, _ = decision_tools(self.app.state.agent.executor.schemas(), available(self.app.state.agent_store), chat=True)
        self.assertEqual(len(tools), 2)

    def test_native_chat_prompt_and_tools_are_conversational(self):
        self.model.requests.append(([], []))  # Synthetic model now returns an answer.
        run = self.client.post('/api/agent/runs', json={'content':'聊聊学习方法','request_id':'chat-role','session_key':'chat:role','kind':'chat'}).json()
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            result = self.client.get('/api/agent/runs/'+run['id']).json()
            if result['status'] in {'completed','failed'}:
                break
            time.sleep(.02)
        self.assertEqual(result['status'], 'completed')
        messages, tools = self.model.requests[-1]
        self.assertIn('职责是日常交流', messages[0]['content'])
        self.assertEqual({tool['function']['name'] for tool in tools}, {'complete_answer','request_clarification'})

    def test_chat_streams_text_when_the_client_can(self):
        # GLM sends function-call arguments in one piece, so chat must use the
        # JSON-mode text stream to show its reply as it is written.
        streamed = []
        def stream(messages, model, max_tokens=None):
            streamed.append(messages)
            for part in ['{"action":"answer","answer":"', '你好，', '我是 MMM', '"}']:
                yield 'content', part
        self.model.stream = stream
        run = self.client.post('/api/agent/runs', json={'content':'你是谁','request_id':'chat-stream','session_key':'chat:stream','kind':'chat'}).json()
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            result = self.client.get('/api/agent/runs/'+run['id']).json()
            if result['status'] in {'completed','failed'}:
                break
            time.sleep(.02)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['answer'], '你好，我是 MMM')
        self.assertEqual(len(streamed), 1)
        self.assertIn('职责是日常交流', streamed[0][0]['content'])
        self.assertEqual(self.model.requests, [])   # no function-calling request for chat


if __name__=='__main__':
    unittest.main()
