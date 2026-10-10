"""Synthetic native function-call create/update/clarification smoke test."""
import argparse
import json
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.providers.llm import LLMProvider


class TrackedProvider(LLMProvider):
    def __init__(self):
        self.requests = []

    def _stream(self, messages, model, max_tokens=None, tools=None):
        entry = {'model': model, 'native_functions': [t['function']['name'] for t in tools or []],
                 'tool_result_ids': [m['tool_call_id'] for m in messages if m['role']=='tool'],
                 'received_calls': [], 'finish': None}
        self.requests.append(entry)
        for kind, value in super()._stream(messages, model, max_tokens, tools):
            if kind=='delta' and value.get('tool_calls'):
                entry['received_calls'].append(value['tool_calls'])
            elif kind=='finish':
                entry['finish'] = value
            yield kind, value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=['glm','deepseek'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    provider = TrackedProvider()
    report = {'synthetic_only': True, 'model': args.model, 'cases': [], 'requests': provider.requests}
    with tempfile.TemporaryDirectory(prefix='mmm-native-fc-') as directory:
        app = create_app(Path(directory)/'synthetic.db', provider, scheduler_enabled=False)
        with TestClient(app) as client:
            app.state.memory_service.embedding = None
            day = (datetime.now(timezone.utc)+timedelta(days=5)).date().isoformat()
            for case, content in [
                ('create', f'请在{day}创建一个名为原生调用验收的任务。'),
                ('update', '把刚创建的原生调用验收任务名称改成原生调用已验证。'),
                ('clarify', '请帮我创建另一个任务，但任务名称和日期请先问我，不要自行决定。')]:
                response = client.post('/api/agent/runs', json={'content':content,'request_id':uuid.uuid4().hex,
                    'session_key':'native-live','model':args.model,'kind':'complex'})
                response.raise_for_status()
                rid = response.json()['id']
                deadline = time.monotonic()+180
                while time.monotonic()<deadline:
                    run = client.get('/api/agent/runs/'+rid).json()
                    if run['status'] in {'completed','waiting_for_user','failed','cancelled'}:
                        break
                    time.sleep(.1)
                else:
                    client.post('/api/agent/runs/'+rid+'/cancel', json={})
                    run = client.get('/api/agent/runs/'+rid).json()
                tasks = app.state.agent_store.rows('SELECT id,title,version FROM calendar_sessions')
                expected_title = '原生调用验收' if case=='create' else '原生调用已验证'
                passed = len(tasks)==1 and tasks[0]['title']==expected_title
                passed = passed and run['status']==('waiting_for_user' if case=='clarify' else 'completed')
                report['cases'].append({'case':case,'input':content,'passed':passed,
                    'run':{k:run[k] for k in ('status','answer','error','calls','actual_tokens')},'tasks':tasks,
                    'tools':app.state.agent_store.rows('SELECT tool,arguments,status,result FROM tool_calls WHERE run_id=?',(rid,))})
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'case':case,'passed':passed,'status':run['status'],'error':run['error']},ensure_ascii=False),flush=True)
    sys.exit(not all(c['passed'] for c in report['cases']))


if __name__=='__main__':
    main()
