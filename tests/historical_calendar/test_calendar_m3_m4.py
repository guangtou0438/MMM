"""M3/M4 unified acceptance, using isolated databases and a fixed clock."""
import json
import unittest
from datetime import datetime, timedelta, timezone, date
from sqlalchemy import select, text
import test_calendar_m2 as base
from calendar_agent.models import WorkSession, Subtask, MemoryDaily, MemoryWeekly, MemoryMonthly, MemoryYearly
from calendar_agent.scheduler.jobs import scan_due, run_rollup, build_scheduler
from calendar_agent.memory.context import planning_context
from calendar_agent.app import create_app
from fastapi.testclient import TestClient

class LifecycleTests(unittest.TestCase):
    setUp = base.PlannerTests.setUp
    tearDown = base.PlannerTests.tearDown
    chat = base.PlannerTests.chat

    def task(self, duration=60):
        result = self.client.post('/tasks', json={'title':'示例任务','deadline_utc':'2026-09-12T00:00:00Z','estimated_minutes':duration,'subtasks':[{'title':'示例步骤','duration_min':duration,'slot_start_utc':f'2026-09-05T{len(self.client.get("/tasks").json())*2:02d}:00:00Z'}]})
        self.assertEqual(result.status_code,201,result.text)
        return result.json()['subtasks'][0]['id']

    def start(self, step):
        result = self.client.post('/sessions/start',json={'subtask_id':step})
        self.assertEqual(result.status_code,200,result.text)
        return result.json()['id']

    def complete(self, session, actual=90, outcome='completed'):
        return self.client.post('/sessions/complete',json={'session_id':session,'actual_minutes':actual,'outcome':outcome,'note':'测试记录'})

    def test_due_dedup_and_dismiss_start(self):
        step=self.task()
        for _ in range(2): scan_due(self.app.state.sessions,base.NOW)
        notices=self.client.get('/notifications/pending').json()
        self.assertEqual(len(notices),1)
        session=self.start(step)
        self.assertEqual(self.start(step),session)
        self.assertEqual(self.client.get('/notifications/pending').json(),[])
        self.assertEqual(len(self.client.get('/sessions/active').json()),1)

    def test_actual_required_400_and_partial_accumulation_idempotence(self):
        step=self.task();session=self.start(step)
        for value in ({}, {'actual_minutes':None}, {'actual_minutes':True}, {'actual_minutes':-1}):
            result=self.client.post('/sessions/complete',json={'session_id':session,'outcome':'partial',**value})
            self.assertEqual(result.status_code,400,result.text)
        self.assertEqual(self.complete(session,30,'partial').status_code,200)
        self.assertEqual(self.complete(session,30,'partial').status_code,200)
        second=self.start(step)
        self.assertEqual(self.complete(second,60).status_code,200)
        self.assertEqual(self.complete(second,65).status_code,409)
        with self.app.state.sessions() as db:
            self.assertEqual(db.get(Subtask,step).actual_minutes,90)
            self.assertEqual(db.get(Subtask,step).status,'completed')
            self.assertTrue(db.get(WorkSession,second).ended_at.tzinfo)
            for table in ('memory_daily','memory_weekly','memory_monthly','memory_yearly'):
                self.assertEqual(db.scalar(text(f'SELECT COUNT(*) FROM {table}')),0)

    def test_only_one_active_and_dependency(self):
        first=self.task();second=self.task();session=self.start(first)
        self.assertEqual(self.client.post('/sessions/start',json={'subtask_id':second}).status_code,409)
        self.complete(session)
        with self.app.state.sessions() as db:
            db.get(Subtask,second).depends_on=first;db.get(Subtask,first).status='pending';db.commit()
        self.assertEqual(self.client.post('/sessions/start',json={'subtask_id':second}).status_code,409)

    def test_unified_rollup_then_profile_in_prompt(self):
        step=self.task();self.complete(self.start(step),90)
        for kind in ('daily','weekly','monthly','yearly'): run_rollup(self.app.state.sessions,kind,base.NOW)
        with self.app.state.sessions() as db:
            daily=db.get(MemoryDaily,date(2026,9,5)).content_json
            self.assertEqual(daily['metrics']['actual_minutes'],90)
            profile=db.get(MemoryYearly,2026).profile_json
            self.assertEqual(profile['underestimation_factor'],1.5)
            self.assertEqual(profile['sample_count'],1)
            self.assertEqual(profile['efficient_hours'],[])
            context=planning_context(db,base.NOW,[])
            self.assertNotIn('"days"',str(context['weekly_summaries']))
        result=self.chat()
        self.assertEqual(result['created_tasks'][0]['estimated_minutes'],405)
        self.assertEqual(set(json.loads(self.model.calls[-1][1]['content'])['context']),{'yearly_profile','weekly_summaries','free_slots'})
        run_rollup(self.app.state.sessions,'yearly',base.NOW)
        with self.app.state.sessions() as db:self.assertEqual(db.get(MemoryYearly,2026).profile_json['sample_count'],1)

    def test_rollup_midnight_boundary_and_late_session_repair(self):
        step=self.task();session=self.start(step);self.complete(session,60)
        with self.app.state.sessions() as db:
            row=db.get(WorkSession,session);row.started_at=datetime(2026,9,4,13,0,tzinfo=timezone.utc);row.ended_at=datetime(2026,9,4,14,0,tzinfo=timezone.utc);db.commit()
        run_rollup(self.app.state.sessions,'daily',base.NOW)
        with self.app.state.sessions() as db:
            self.assertEqual(db.get(MemoryDaily,date(2026,9,4)).content_json['metrics']['actual_minutes'],0)
            self.assertEqual(db.get(MemoryDaily,date(2026,9,5)).content_json['metrics']['actual_minutes'],60)

    def test_restart_keeps_active_and_memory_readonly(self):
        step=self.task();session=self.start(step)
        with TestClient(create_app(self.path,model_client=self.model,clock=lambda:base.NOW)) as client:
            self.assertEqual(client.get('/sessions/active').json()[0]['id'],session)
            status=client.get('/memory/status').json()
            self.assertIsNone(status['daily'])
            self.assertEqual(len(status['jobs']),5)
            self.assertFalse(status['scheduler_enabled'])

    def test_scheduler_schedules_and_lifecycle(self):
        scheduler=build_scheduler(self.app.state.sessions)
        self.assertEqual(len(scheduler.get_jobs()),5)
        trigger=scheduler.get_job('monthly').trigger
        next_run=trigger.get_next_fire_time(None,datetime(2026,2,1,tzinfo=timezone.utc))
        self.assertEqual(next_run.day,28)
        self.assertEqual(str(next_run.tzinfo),'Australia/Melbourne')
        with TestClient(create_app(self.path,scheduler_enabled=True)) as client:
            self.assertTrue(client.get('/health').json()['scheduler_enabled'])
        self.assertFalse(client.app.state.scheduler.running)
