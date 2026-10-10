"""Synthetic live regression. All activities are invented test fixtures."""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app

now = datetime(2026, 9, 5, tzinfo=timezone.utc)
cases = [
    ('测试样例：今天完成示例文档，预计 1 小时', 'create_plan', '文档'),
    ('测试样例：明天学习示例算法，预计 1 小时', 'create_plan', '算法'),
    ('测试样例：大后天安排演示聚餐', 'clarify', None),
    ('测试样例：下周六六点编辑演示幻灯片，帮我标注一下', 'clarify', None),
    ('幻灯片呢', 'clarify', None),
    ('下午六点，预计 1 小时', 'create_plan', '幻灯片'),
    ('幻灯片呢', 'query', None),
]
report = []
with tempfile.TemporaryDirectory() as temp, TestClient(create_app(Path(temp)/'live.db', clock=lambda: now)) as client:
    conversation = None
    for message, intent, subject in cases:
        result = client.post('/chat', json={'message': message, 'conversation_id': conversation}).json()
        conversation = result['conversation_id']
        report.append({'message': message, 'result': result})
        Path('artifacts/calendar-conversation-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'message':message, 'intent': result['intent'], 'reply': result['reply']}, ensure_ascii=False), flush=True)
        assert result['intent'] == intent, result
        if subject: assert subject.lower() in result['created_tasks'][0]['title'].lower(), result
