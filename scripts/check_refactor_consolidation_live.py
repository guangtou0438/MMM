"""Synthetic DeepSeek independent-verification regression."""
import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.memory.consolidation import enqueue
from calendar_agent.memory.jobs import process_one

results=[]
for content in ['I live in Melbourne, Victoria.', 'My synthetic assignment deadline is 2026-09-12.']:
    with tempfile.TemporaryDirectory(prefix='mmm-verify-') as directory:
        app=create_app(Path(directory)/'synthetic.db',clock=lambda:datetime(2026,9,6,tzinfo=timezone.utc),scheduler_enabled=False)
        with TestClient(app):
            memory=app.state.memory_service
            memory.embedding=None
            source=memory.source(content)
            enqueue(memory,source,'explicit')
            result=process_one(memory,model='deepseek')
            results.append({'source':content,'result':result,'memories':memory.list()})
Path('artifacts/refactor-consolidation-live.json').write_text(json.dumps({'mode':'live','synthetic_only':True,'cases':results},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([r['result'] for r in results]))
sys.exit(not all(r['result']['status']=='done' and r['result']['calls']==2 for r in results))
