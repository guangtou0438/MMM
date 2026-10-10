import sys,tempfile,json
from pathlib import Path
from datetime import datetime,timezone,date
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.models import CalendarSession,AssistantMessage
now=datetime(2026,9,6,tzinfo=timezone.utc)
with tempfile.TemporaryDirectory() as temp,TestClient(create_app(Path(temp)/'test.db',clock=lambda:now)) as c:
    with c.app.state.sessions() as db:
        a=CalendarSession(title='出去玩',planned_date=date(2026,9,10),status='open')
        b=CalendarSession(title='出去玩',planned_date=date(2026,9,20),status='open')
        db.add_all([a,b]);db.add(AssistantMessage(conversation_id='home',role='user',content='我的名字是Tommy，九月十号和二十号都出去玩',created_at=now));db.commit()
        aid,bid,version=a.id,b.id,a.version
    r=c.post(f'/api/session-chat/{aid}',json={'request_id':'rename-live','version':version,'content':'把任务改成和孙秦宇出去玩'})
    result=r.json();assert r.status_code==200,result
    suggestions=c.get('/api/home').json()['suggestions']
    assert len(suggestions)==1,result
    p=suggestions[0]['proposal'];assert p['kind']=='update' and p['target_session_id']==aid and p['title']=='和孙秦宇出去玩',p
    assert '哪个' not in result['reply'] and '两天都' not in result['reply'],result
    accepted=c.post(f'/api/suggestions/{suggestions[0]["id"]}',json={'request_id':'accept-rename-live','action':'accept'})
    assert accepted.status_code==200,accepted.text
    assert c.get(f'/api/sessions/{bid}').json()['title']=='出去玩'
    Path('artifacts/session-rename-live.json').write_text(json.dumps({'result':result,'proposal':p,'accepted':accepted.json()},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'reply':result['reply'],'proposal':p,'other_task_unchanged':True},ensure_ascii=False))
