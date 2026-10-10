import sys,tempfile,json
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
now=datetime(2026,9,6,tzinfo=timezone.utc)
with tempfile.TemporaryDirectory() as tmp,TestClient(create_app(Path(tmp)/'test.db',clock=lambda:now)) as c:
    r=c.post('/api/home/chat',json={'request_id':'classify-live','content':'明天跟朋友吃饭，后天学习LeetCode，帮我都放到日历里'}).json()
    items=r.get('saved_sessions',[])
    assert len(items)==2,r
    assert {i['activity_type'] for i in items}=={'task','arrangement'},r
    for i in items:
        if i['activity_type']=='arrangement':
            assert not i['has_session']
            assert c.post(f'/api/session-chat/{i["id"]}',json={'request_id':'no-chat','content':'你好','version':i['version']}).status_code==409
    Path('artifacts/activity-types-live.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{'title':i['title'],'type':i['activity_type']} for i in items],ensure_ascii=False))
