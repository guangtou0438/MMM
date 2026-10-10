"""Explicit live M2 smoke test, isolated from the user's database."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.config import llm_settings

with tempfile.TemporaryDirectory() as temp:
    with TestClient(create_app(Path(temp)/"live.db")) as client:
        result=client.post("/chat",json={"message":"我下周五要交 COMP90054 的 assignment，帮我排一下"}).json()
        attempts=client.get("/planning/attempts/"+result["request_id"]).json()
        dialogue = None
        if result["created_tasks"]:
            task_id = result["created_tasks"][0]["id"]
            dialogue = client.post(f"/tasks/{task_id}/chat",json={"content":"我不理解 A* 搜索里的启发函数，请用一个简短例子解释。"}).json()
        report={"model":llm_settings().model,"result":result,"attempts":attempts,"task_dialogue":dialogue}
        out=Path("artifacts/calendar-m2-live.json")
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"intent":result["intent"],"tasks":len(result["created_tasks"]),"attempts":len(attempts),"task_dialogue_intent":dialogue.get("intent") if dialogue else None},ensure_ascii=False))
        if result["intent"]!="create_plan":raise SystemExit(1)
        if not dialogue or dialogue.get("intent")!="query":raise SystemExit(1)
