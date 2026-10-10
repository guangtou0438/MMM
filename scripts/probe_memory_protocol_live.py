"""Capture synthetic-only extraction responses and exact validation failures."""
import argparse
import hashlib
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from pydantic import ValidationError
from calendar_agent.app import create_app
from calendar_agent.memory.consolidation import enqueue
from calendar_agent.memory.jobs import process_one
from calendar_agent.memory.operations import MemoryOperation
from calendar_agent.memory.service import SECRET
from calendar_agent.providers.llm import LLMProvider

SOURCE = '请记住这三件事：我的自行车叫银杏；我的读书小组叫雾松；我最喜欢的棋类是围棋。'


class Recorder:
    def __init__(self):
        self.calls = []

    def stream(self, messages, model, max_tokens):
        call = {'model': model, 'raw': '', 'tokens': None}
        self.calls.append(call)
        for kind, value in LLMProvider().stream(messages, model, max_tokens):
            if kind == 'content':
                call['raw'] += value
            elif kind == 'usage':
                call['tokens'] = value
            yield kind, value

    def stream_structured(self, messages, model, name, schema, max_tokens=None):
        call = {'model': model, 'function': name, 'schema': schema, 'transport': 'native_function_call', 'raw': '', 'tokens': None}
        self.calls.append(call)
        for kind, value in LLMProvider().stream_structured(messages, model, name, schema, max_tokens):
            if kind == 'content':
                call['raw'] += value
            elif kind == 'usage':
                call['tokens'] = value
            yield kind, value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempts', type=int, default=3)
    parser.add_argument('--model', default='glm', choices=['glm', 'deepseek'])
    parser.add_argument('--stop-on-failure', action='store_true')
    parser.add_argument('--source', default=SOURCE)
    parser.add_argument('--recall', action='store_true', help='Verify a new-session answer after indexing.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if SECRET.search(args.source):
        parser.error('Use synthetic source text without secrets.')
    report = {'synthetic_only': True, 'source': args.source, 'attempts': []}
    for attempt in range(args.attempts):
        with tempfile.TemporaryDirectory(prefix='mmm-protocol-') as directory:
            app = create_app(Path(directory)/'synthetic.db', scheduler_enabled=False)
            with TestClient(app) as client:
                memory = app.state.memory_service
                if not args.recall:
                    memory.embedding = None
                sid = memory.source(args.source)
                enqueue(memory, sid, 'explicit')
                recorder = Recorder()
                result = process_one(memory, recorder, args.model)
                raw = recorder.calls[0]['raw'] if recorder.calls else ''
                try:
                    MemoryOperation.model_validate_json(raw)
                    raw_errors = []
                except ValidationError as exc:
                    raw_errors = exc.errors(include_input=False, include_context=False, include_url=False)
                for call in recorder.calls:
                    call['sha256'] = hashlib.sha256(call['raw'].encode()).hexdigest()
                    if SECRET.search(call['raw']):
                        call['raw'] = '[REDACTED: secret-like content]'
                evidence = {'attempt': attempt+1, 'result': result,
                    'raw_validation_errors': raw_errors, 'calls': recorder.calls,
                    'memories': memory.list()}
                if args.recall and result['status'] == 'done':
                    from calendar_agent.memory.retrieval import rebuild
                    evidence['index'] = rebuild(memory)
                    response = client.post('/api/agent/runs', json={
                        'content': '我的自行车叫什么、读书小组叫什么、最喜欢哪种棋类？',
                        'session_key': uuid.uuid4().hex, 'request_id': uuid.uuid4().hex,
                        'model': args.model, 'kind': 'complex'})
                    response.raise_for_status()
                    rid = response.json()['id']
                    deadline = time.monotonic()+120
                    while time.monotonic() < deadline:
                        run = client.get('/api/agent/runs/'+rid).json()
                        if run['status'] in {'completed', 'failed', 'cancelled', 'waiting_for_user'}:
                            break
                        time.sleep(.1)
                    else:
                        client.post('/api/agent/runs/'+rid+'/cancel', json={})
                        run = client.get('/api/agent/runs/'+rid).json()
                    evidence['recall'] = {key: run[key] for key in ('status','answer','error','calls','actual_tokens','model','citations')}
                report['attempts'].append(evidence)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({'attempt': attempt+1, 'result': result, 'raw_errors': raw_errors}, ensure_ascii=False), flush=True)
                if args.stop_on_failure and result['status'] != 'done':
                    break


if __name__ == '__main__':
    main()
