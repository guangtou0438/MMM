"""Focused synthetic live regression for calendar IDs mistaken for memory citations."""
import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.evaluation.workflows import wait

with tempfile.TemporaryDirectory(prefix='mmm-protocol-') as directory:
    app=create_app(Path(directory)/'synthetic.db',clock=lambda:datetime(2026,9,6,tzinfo=timezone.utc),scheduler_enabled=False)
    with TestClient(app) as client:
        app.state.memory_service.embedding=None
        run=client.post('/api/agent/runs',json={'content':'Create a task titled Synthetic report on 2026-09-07.','request_id':'protocol-check','model':'glm','kind':'complex'}).json()
        result=wait(client,run['id'])
        tasks=client.get('/api/home').json()['sessions']
        output={'mode':'live','synthetic_only':True,'status':result['status'],'error':result['error'],
                'calls':result['calls'],'provider_tokens':result['actual_tokens'],'answer':result['answer'],
                'created':any(t['title']=='Synthetic report' for t in tasks)}
        Path('artifacts/refactor-protocol-live.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(output,ensure_ascii=True))
        sys.exit(not(output['status']=='completed' and output['created']))
