import copy
import json
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["MMM_OFFLINE"] = "1"
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from calendar_agent.app import create_app
from calendar_agent.core.slots import free_slots
from calendar_agent.memory.context import planning_context
from calendar_agent.models import MemoryWeekly, MemoryYearly

NOW = datetime(2026, 9, 5, 0, tzinfo=timezone.utc)
MESSAGE = "我下周五要交 COMP90054 的 assignment，帮我排一下"


class FakeModel:
    """A schema-compliant deterministic model double, not a product fallback."""
    def __init__(self, corrupt=0, hook=None):
        self.calls = []
        self.corrupt = corrupt
        self.hook = hook

    def complete(self, messages):
        self.calls.append(copy.deepcopy(messages))
        request = json.loads(messages[1]["content"])
        data = request["resolved"]
        slots = request["context"]["free_slots"]
        for durations in data["duration_options"]:
            chosen = []
            for slot in slots:
                if slot['capacity_min'] >= durations[len(chosen)]: chosen.append(slot)
                if len(chosen) == len(durations): break
            if len(chosen) == len(durations):
                slots = chosen
                break
        result = {"intent":"create_plan","clarify_question":None,
                  "goal":{"title":"COMP90054 assignment","deadline":data["deadline"],"estimated_hours":data["estimated_hours"]},
                  "subtasks":[{"title":f"工作步骤 {i+1}","slot_start":slots[i]["slot_start"],"duration_min":duration,"depends_on":i-1 if i else None} for i,duration in enumerate(durations)],
                  "reasoning":"先理解要求，再实现与检查。"}
        if len(self.calls) <= self.corrupt:
            result["subtasks"][0]["slot_start"]="2099-01-01T00:00:00Z"
        if self.hook and len(self.calls)==1:self.hook(request)
        return json.dumps(result)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/"calendar.db"
        self.model=FakeModel();self.app=create_app(self.path,model_client=self.model,clock=lambda:NOW)
        self.client=TestClient(self.app);self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()

    def chat(self,message=MESSAGE,**extra):
        response=self.client.post("/chat",json={"message":message,**extra})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_acceptance_1_real_slots_three_to_five_and_deadline(self):
        result=self.chat();self.assertEqual(result["intent"],"create_plan",result)
        task=result["created_tasks"][0];self.assertTrue(3<=len(task["subtasks"])<=5)
        request=json.loads(self.model.calls[0][1]["content"])
        valid={s["slot_start"] for s in request["context"]["free_slots"]}
        deadline=datetime.fromisoformat(task["deadline_utc"].replace("Z","+00:00"))
        self.assertEqual(deadline,datetime(2026,9,11,13,59,tzinfo=timezone.utc))  # next Friday 23:59 Melbourne
        for step in task["subtasks"]:
            self.assertIn(step["slot_start_utc"],valid)
            end=datetime.fromisoformat(step["slot_start_utc"].replace("Z","+00:00"))+timedelta(minutes=step["duration_min"])
            self.assertLessEqual(end,deadline)
        self.assertNotIn("calendar",request["context"])
        self.assertEqual(set(request["context"]),{"yearly_profile","weekly_summaries","free_slots"})

    def test_acceptance_2_occupied_slot_and_repeated_request_avoid_overlap(self):
        self.client.post("/tasks",json={"title":"占满时段","deadline_utc":"2026-09-11T00:00:00Z","estimated_minutes":120,
            "subtasks":[{"title":"固定占用","slot_start_utc":"2026-09-05T00:00:00Z","duration_min":120}]})
        self.chat();self.chat()
        events=self.client.get("/calendar",params={"start":"2026-09-05T00:00Z","end":"2026-09-11T00:00Z"}).json()
        events.sort(key=lambda e:e["start"])
        for a,b in zip(events,events[1:]):self.assertLessEqual(a["end"],b["start"])

    def test_acceptance_3_clarify_and_followup(self):
        result=self.chat("帮我安排一下作业")
        self.assertEqual(result["intent"],"clarify");self.assertEqual(result["created_tasks"],[])
        self.assertEqual(len(self.model.calls),0)
        result=self.chat(MESSAGE,conversation_id=result["conversation_id"])
        self.assertEqual(result["intent"],"create_plan")

    def test_acceptance_4_invalid_slot_retries_with_visible_errors(self):
        self.model.corrupt=1
        with self.assertLogs("mmm.planner",level="WARNING") as captured:result=self.chat()
        self.assertEqual(result["intent"],"create_plan",result);self.assertEqual(len(self.model.calls),2)
        self.assertIn("不是真实可用 slot",str(captured.output))
        self.assertIn("不是真实可用 slot",self.model.calls[1][-1]["content"])
        attempts=self.client.get("/planning/attempts/"+result["request_id"]).json()
        self.assertEqual(len(attempts),2);self.assertTrue(attempts[0]["errors"]);self.assertEqual(attempts[1]["errors"],[])

    def test_exhaustion_never_silently_creates_and_schema_failure_retries(self):
        self.model.corrupt=99;result=self.chat()
        self.assertEqual(len(self.model.calls),3);self.assertEqual(result["intent"],"clarify")
        self.assertEqual(self.client.get("/tasks").json(),[])
        with patch.object(self.model,"complete",return_value='{"intent":"create_plan"}') as call:
            result=self.chat()
            self.assertEqual(call.call_count,3);self.assertEqual(result["intent"],"clarify")

    def test_commit_rechecks_concurrent_occupation(self):
        def occupy(req):
            response=self.client.post("/tasks",json={"title":"竞争请求","deadline_utc":req["resolved"]["deadline"],"estimated_minutes":120,
              "subtasks":[{"title":"抢占","duration_min":120,"slot_start_utc":req["context"]["free_slots"][0]["slot_start"]}]})
            self.assertEqual(response.status_code,201,response.text)
        self.model.hook=occupy;result=self.chat()
        self.assertEqual(result["intent"],"create_plan",result);self.assertEqual(len(self.model.calls),2)

    def test_sleep_fixed_calendar_and_profile_math(self):
        with self.app.state.sessions() as db:
            slots=free_slots(NOW,datetime(2026,9,6,tzinfo=timezone.utc),db,fixed=[{"weekdays":[5],"start":"10:00","end":"12:00"}])
            self.assertNotEqual(slots[0]["slot_start"],"2026-09-05T00:00:00Z")
            db.add(MemoryYearly(year=2026,profile_json={"underestimation_factor":1.5}));db.commit()
        result=self.chat();self.assertEqual(result["created_tasks"][0]["estimated_minutes"],405)

    def test_idempotent_request_and_conversation_persist(self):
        key=str(uuid.uuid4());a=self.chat(request_id=key);b=self.chat(request_id=key)
        self.assertEqual(a,b);self.assertEqual(len(self.model.calls),1)
        messages=self.client.get("/planning/conversations/"+a["conversation_id"]).json()["messages"]
        self.assertEqual(len(messages),2)
        with self.app.state.sessions() as db:
            for table in ("memory_daily","memory_weekly","memory_monthly","memory_yearly"):
                self.assertEqual(db.scalar(text(f"SELECT COUNT(*) FROM {table}")),0)

    def test_context_limits_weekly_and_today_query(self):
        from datetime import date
        with self.app.state.sessions() as db:
            for i in range(1,7):db.add(MemoryWeekly(week_start=date(2026,8,i),summary=f"summary {i}"))
            db.commit();context=planning_context(db,NOW,[])
            self.assertEqual(len(context["weekly_summaries"]),4)
        result=self.chat("今天做什么")
        self.assertEqual(result["intent"],"query");self.assertEqual(len(self.model.calls),0)


if __name__=="__main__":unittest.main()
