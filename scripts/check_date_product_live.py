"""Isolated synthetic functional smoke with configured provider; no user DB writes."""
import json,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.memory.personal import incremental
from calendar_agent.models import MemoryItem
from sqlalchemy import select
now=datetime(2026,9,6,0,tzinfo=timezone.utc)
report=[]
with tempfile.TemporaryDirectory() as temp,TestClient(create_app(Path(temp)/'live.db',clock=lambda:now)) as c:
    for i,content in enumerate(['明天读完示例短篇小说，帮我记成待办。','再帮我添加一个数学作业。']):
        r=c.post('/api/home/chat',json={'request_id':f'live-{i}','content':content})
        report.append({'case':content,'status':r.status_code,'result':r.json()})
        print(json.dumps(report[-1],ensure_ascii=False),flush=True)
    tasks=c.get('/api/home').json()['sessions']
    if tasks:
        s=tasks[0]
        r=c.post('/api/session-chat/'+str(s['id']),json={'request_id':'live-session','version':s['version'],'content':'读书有点累，我更喜欢短句和分步解释。另外明天想试试陶艺，帮我留个待办想法。'})
        report.append({'case':'session-collaboration','status':r.status_code,'result':r.json()});print(json.dumps(report[-1],ensure_ascii=False),flush=True)
        processed=all(incremental(c.app.state.sessions,now) for _ in range(3))
        with c.app.state.sessions() as db:
            memories=db.scalars(select(MemoryItem)).all()
            assert any(5 in m.source_ids for m in memories),'Personal experience was not extracted'
        recall=c.post('/api/home/chat',json={'request_id':'live-recall','content':'你记得我喜欢什么沟通方式吗？'}).json()
        assert recall.get('citations'),'Recall did not cite original evidence'
        report.append({'case':'cross-session-recall','result':recall})
        print(json.dumps(report[-1],ensure_ascii=False),flush=True)
        report.append({'case':'personal-memory','processed':processed})
        print(json.dumps(report[-1],ensure_ascii=False),flush=True)
    Path('artifacts/date-product-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert tasks,'No task created'
    assert all(not r.get('result',{}).get('error') and r.get('status',200)==200 for r in report),report
    assert any(r.get('result',{}).get('suggestion_ids') for r in report),'No session suggestion'
    assert report[-1].get('processed'),'Memory extraction failed'
