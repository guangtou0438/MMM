import sys,tempfile,json
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.models import AssistantMessage
now=datetime(2026,9,6,0,tzinfo=timezone.utc)
with tempfile.TemporaryDirectory() as temp,TestClient(create_app(Path(temp)/'test.db',clock=lambda:now)) as c:
    with c.app.state.sessions() as db:
        for role,content in [('user','九月十号我想出去玩'),('assistant','具体几点开始、持续多久？'),('user','我九月20想出去玩'),('assistant','你之前说过九月十号想出去玩，现在提到的是九月二十号，是想改到9月20日吗？另外具体想做什么活动、大概玩多久？')]:
            db.add(AssistantMessage(conversation_id='home',role=role,content=content,created_at=now))
        db.commit()
    result=c.post('/api/home/chat',json={'request_id':'both-dates-live','content':'都出去玩'}).json()
    Path('artifacts/date-followup-live.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    assert sorted(s['planned_date'] for s in result.get('saved_sessions',[]))==['2026-09-10','2026-09-20']
