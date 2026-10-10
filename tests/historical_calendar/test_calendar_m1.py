import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import StatementError

from calendar_agent.app import create_app
from calendar_agent.models import Base, MemoryDaily, Subtask, Task, WorkSession


class M1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "calendar.db"
        self.app = create_app(self.path)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def payload(self, start="2026-09-10T09:00:00+10:00"):
        return {"title": "COMP90054 assignment", "deadline_utc": "2026-09-18T17:00:00+10:00", "estimated_minutes": 90,
                "subtasks": [{"title": "阅读要求", "slot_start_utc": start, "duration_min": 90, "depends_on": None}]}

    def create(self, payload=None):
        response = self.client.post("/tasks", json=payload or self.payload())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_schema_all_layers_and_actual_field(self):
        self.assertEqual(set(inspect(self.app.state.engine).get_table_names()),set(Base.metadata.tables))
        self.assertTrue({"tasks","subtasks","sessions","memory_daily","memory_weekly","memory_monthly","memory_yearly"}.issubset(Base.metadata.tables))
        cols={c["name"]:c for c in inspect(self.app.state.engine).get_columns("subtasks")}
        self.assertIn("actual_minutes",cols)
        self.assertTrue(cols["actual_minutes"]["nullable"])

    def test_create_read_utc_serialization_and_raw_storage(self):
        task=self.create()
        self.assertEqual(task["deadline_utc"],"2026-09-18T07:00:00Z")
        self.assertEqual(task["subtasks"][0]["slot_start_utc"],"2026-09-09T23:00:00Z")
        self.assertIsNone(task["subtasks"][0]["actual_minutes"])
        self.assertEqual(self.client.get(f"/tasks/{task['id']}").json(),task)
        with self.app.state.sessions() as db:
            raw=db.execute(text("SELECT deadline_utc,created_at FROM tasks")).one()
            self.assertTrue(all(value.endswith("Z") for value in raw))
            self.assertEqual(db.get(Task,task["id"]).deadline_utc.tzinfo,timezone.utc)

    def test_calendar_interval_overlap_and_half_open_boundaries(self):
        self.create()
        response=self.client.get("/calendar",params={"start":"2026-09-10T00:00:00Z","end":"2026-09-10T02:00:00Z"})
        self.assertEqual(response.status_code,200,response.text)
        events=response.json()
        self.assertEqual(len(events),1)  # starts the prior UTC day, overlaps query
        self.assertEqual(events[0]["end"],"2026-09-10T00:30:00Z")
        self.assertFalse(events[0]["allDay"])
        self.assertEqual(self.client.get("/calendar",params={"start":"2026-09-10T00:30:00Z","end":"2026-09-11T00:00:00Z"}).json(),[])

    def test_naive_datetime_and_invalid_ranges_rejected(self):
        data=self.payload();data["deadline_utc"]="2026-09-18T17:00:00"
        self.assertEqual(self.client.post("/tasks",json=data).status_code,422)
        for start,end,status in [("2026-09-01","2026-09-02",422),("2026-09-02T00:00Z","2026-09-01T00:00Z",400),
                                 ("2026-01-01T00:00Z","2028-01-01T00:00Z",400)]:
            self.assertEqual(self.client.get("/calendar",params={"start":start,"end":end}).status_code,status)
        with self.app.state.sessions() as db:
            db.add(Task(title="naive",deadline_utc=datetime(2026,9,5),estimated_minutes=60))
            with self.assertRaises(StatementError):db.commit()

    def test_melbourne_dst_gap_fold_and_seasonal_offsets(self):
        response=self.client.post("/time/resolve",json={"values":["2026-01-15T09:00","2026-07-15T09:00"]})
        self.assertEqual(response.json()["utc"],["2026-01-14T22:00:00Z","2026-07-14T23:00:00Z"])
        for wall in ("2026-10-04T02:30","2026-04-05T02:30"):
            self.assertEqual(self.client.post("/time/resolve",json={"values":[wall]}).status_code,400)
        self.assertEqual(self.client.post("/time/resolve",json={"values":["2026-04-05T03:30"]}).status_code,200)

    def test_conflicts_and_deadline_leave_database_unchanged(self):
        self.create()
        self.assertEqual(self.client.post("/tasks",json=self.payload("2026-09-10T09:30:00+10:00")).status_code,409)
        self.create(self.payload("2026-09-10T10:30:00+10:00"))  # adjacent is allowed
        data=self.payload("2026-09-18T16:00:00+10:00")
        self.assertEqual(self.client.post("/tasks",json=data).status_code,400)
        self.assertEqual(len(self.client.get("/tasks").json()),2)

    def test_dependency_validation_and_persisted_ids(self):
        data=self.payload();data["subtasks"].append({"title":"动手实现","slot_start_utc":"2026-09-10T11:00:00+10:00","duration_min":60,"depends_on":0})
        task=self.create(data)
        self.assertEqual(task["subtasks"][1]["depends_on"],task["subtasks"][0]["id"])
        for index in (0,1,9):
            bad=self.payload();bad["subtasks"][0]["depends_on"]=index
            self.assertEqual(self.client.post("/tasks",json=bad).status_code,400)
        data["subtasks"][1]["slot_start_utc"]="2026-09-10T08:00:00+10:00"
        self.assertEqual(self.client.post("/tasks",json=data).status_code,400)

    def test_unscheduled_and_payload_validation(self):
        data=self.payload();data["subtasks"][0]["slot_start_utc"]=None
        self.create(data)
        events=self.client.get("/calendar",params={"start":"2026-09-01T00:00Z","end":"2026-10-01T00:00Z"}).json()
        self.assertEqual(events,[])
        for duration in (0,-1,True,60.5):
            bad=self.payload();bad["subtasks"][0]["duration_min"]=duration
            self.assertEqual(self.client.post("/tasks",json=bad).status_code,422)
        bad=self.payload();bad["title"]=" "
        self.assertEqual(self.client.post("/tasks",json=bad).status_code,422)

    def test_delete_cascades_and_persistence(self):
        task=self.create()
        with self.app.state.sessions() as db:
            db.add(WorkSession(subtask_id=task["subtasks"][0]["id"]))
            db.commit()
        with TestClient(create_app(self.path)) as reopened:
            self.assertEqual(len(reopened.get("/tasks").json()),1)
        self.assertEqual(self.client.delete(f"/tasks/{task['id']}").status_code,204)
        with self.app.state.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Subtask)),0)
            self.assertEqual(db.scalar(select(func.count()).select_from(WorkSession)),0)
        self.assertEqual(self.client.delete(f"/tasks/{task['id']}").status_code,404)

    def test_parallel_conflicting_creates_only_one_succeeds(self):
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.client.post("/tasks",json=self.payload()).status_code,range(2)))
        self.assertEqual(sorted(results),[201,409])

    def test_m1_never_runs_llm_or_rollup(self):
        self.create()
        self.client.get("/tasks")
        with self.app.state.sessions() as db:
            for table in ("memory_daily","memory_weekly","memory_monthly","memory_yearly"):
                self.assertEqual(db.scalar(text(f"SELECT COUNT(*) FROM {table}")),0)
        health=self.client.get("/health").json()
        self.assertFalse(health["llm_enabled"])
        self.assertFalse(health["scheduler_enabled"])
        self.assertEqual(self.client.post("/chat",json={"message":"规划作业"}).json()["intent"],"clarify")

    def test_assets_and_local_boundary(self):
        self.assertEqual(self.client.get("/").status_code,200)
        for path in ("app.js","style.css","vendor/fullcalendar.min.js","vendor/luxon.min.js","vendor/fullcalendar-luxon.min.js","vendor/zh-cn.min.js"):
            self.assertEqual(self.client.get("/static/"+path).status_code,200)
        self.assertEqual(self.client.get("/.env").status_code,404)
        self.assertEqual(self.client.post("/tasks",json=self.payload(),headers={"Origin":"https://evil.example"}).status_code,403)
        self.assertEqual(self.client.get("/health",headers={"Host":"evil.example"}).status_code,400)


if __name__ == "__main__":
    unittest.main()
