"""Temporary synthetic-only server for browser regression tests."""
import json
import sys
import tempfile
import time
import re
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from calendar_agent.app import create_app


class SyntheticModel:
    def stream(self, messages, model, max_tokens=None):
        output=self.complete(messages)
        for offset in range(0,len(output),16):
            time.sleep(.06)
            if offset+16>=len(output):time.sleep(.8)
            yield 'content',output[offset:offset+16]

    def complete(self, messages):
        time.sleep(.6)
        if 'summarize_memory' in messages[0]['content']:
            evidence=json.loads(messages[-1]['content'])['memories']
            ids=lambda *kinds:[m['id'] for m in evidence if m['kind'] in kinds]
            text={'personality':('你做事细致，喜欢把节奏握在自己手里。',ids('profile','procedural')),'values':('你看重自在和身边人的感受。',ids('preference','profile')),
                  'goals':('你想把生活过得更规律，也想在设计上更进一步。',ids('episodic','task')),'passions':('你热爱户外和安静的咖啡时光。',ids('preference')),
                  'relationships':('你希望被温和地提醒，而不是被催促。',ids('procedural','profile'))}
            return json.dumps({'sections':[{'kind':k,'text':t if m else '','memory_ids':m[:3]} for k,(t,m) in text.items()]},ensure_ascii=False)
        match=re.fullmatch(r'更新清单测试 #(\d+) v(\d+)',messages[-1]['content'])
        if match:
            return json.dumps({'action':'tool','skill':'calendar','tool':'update_task','expected_version':int(match[2]),
                'arguments':{'session_id':int(match[1]),'changes':{'agenda':[{'text':'模型新增的小步','done':False}]}}},ensure_ascii=False)
        answer='合成测试回答：你的目标已经收到，可以继续查看执行轨迹。'
        if '长回复测试' in messages[-1]['content']:
            answer+='\n'+'\n'.join('合成段落 '+str(i) for i in range(50))
        return json.dumps({'action': 'answer', 'answer': answer}, ensure_ascii=False)


with tempfile.TemporaryDirectory(prefix='mmm-ui-synthetic-') as directory:
    app = create_app(Path(directory)/'test.db', SyntheticModel(), lambda: datetime(2026, 9, 6, tzinfo=timezone.utc), scheduler_enabled=False)
    uvicorn.run(app, host='127.0.0.1', port=8877, access_log=False)
