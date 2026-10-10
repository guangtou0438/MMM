from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / 'calendar_agent/static/index.html'
content = path.read_text(encoding='utf-8')
if '/static/agent.js' not in content:
    content = content.replace('</head>', '<link rel="stylesheet" href="/static/agent.css"><script src="/static/agent.js" defer></script></head>')
    content = content.replace('刷新页面开启新对话，过往聊天会作为记忆保留。', '发送后进入 Agent 对话，可查看工具轨迹并恢复运行。')
    path.write_text(content, encoding='utf-8')
