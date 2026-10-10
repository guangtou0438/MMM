"""Current product regressions. Deterministic model doubles never claim live quality."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select, text, func
from fastapi.testclient import TestClient
from calendar_agent.app import create_app
from calendar_agent.models import *
from calendar_agent.core.date_tasks import rollover
from calendar_agent.memory.personal import incremental, retrieve, apply_directive, maintenance, period_keys, run_job
from calendar_agent.api.calendar_session_schemas import MemoryAction

NOW = datetime(2026, 9, 6, 0, tzinfo=timezone.utc)

class Model:
    def __init__(self): self.outputs=[]; self.calls=[]; self.callback=None
    def complete(self,messages):
        self.calls.append(messages)
        if self.callback:
            callback=self.callback; self.callback=None; callback()
        result=self.outputs.pop(0) if self.outputs else {'reply':'我们可以从一个小步骤开始。'}
        if isinstance(result, Exception): raise result
        return result if isinstance(result,str) else json.dumps(result,ensure_ascii=False)

class ProductTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.model=Model(); self.now=NOW; self.n=0
        self.app=create_app(Path(self.tmp.name)/'test.db',self.model,lambda:self.now)
        # Existing product actions simulate the explicit browser confirmation control.
        self.client=TestClient(self.app,headers={'X-UI-Action':'permanent-delete'});self.client.__enter__();self.factory=self.app.state.sessions
    def tearDown(self): self.client.__exit__(None,None,None);self.tmp.cleanup()
    def uid(self): self.n+=1;return str(self.n)
    def create(self,title='读书',**extra):
        actual_now=self.now
        if extra.get('planned_date') and extra['planned_date'] < str(self.now.astimezone(ZoneInfo('Australia/Melbourne')).date()):
            self.now=datetime.fromisoformat(extra['planned_date']).replace(tzinfo=timezone.utc)
        try:
            response=self.client.post('/api/sessions',json={'request_id':self.uid(),'title':title,'activity_type':'task',**extra})
        finally:
            self.now=actual_now
        self.assertEqual(response.status_code,201,response.text);return response.json()
    def action(self,s,action,**extra):return self.client.post(f'/api/sessions/{s["id"]}/{action}',json={'request_id':self.uid(),'version':s['version'],**extra})
    def detail(self,s):return self.client.get('/api/sessions/'+str(s['id'])).json()
    def chat(self,content='你好',s=None,**extra):
        data={'request_id':self.uid(),'content':content,**extra}
        if s:data['version']=s['version']
        return self.client.post('/api/session-chat/'+str(s['id']) if s else '/api/home/chat',json=data)
    def test_trash_permanent_delete_guards_and_replay(self):
        s=self.create('待删除任务')
        url=f'/api/sessions/{s["id"]}/delete-permanently/confirm'
        self.assertEqual(self.client.post(url,json={'request_id':self.uid(),'version':s['version']}).status_code,409)
        self.chat('只用于删除测试的经历',s=s)
        s=self.action(s,'archive').json()
        self.assertEqual(self.client.post(url,json={'request_id':self.uid(),'version':s['version']-1}).status_code,409)
        payload={'request_id':self.uid(),'version':s['version']}
        a=self.client.post(url,json=payload);self.assertEqual(a.status_code,200,a.text)
        self.assertEqual(self.client.post(url,json=payload).json(),a.json())
        self.assertEqual(self.client.get('/api/sessions/'+str(s['id'])).status_code,404)
        self.assertEqual(self.action(s,'restore').status_code,404)
        self.assertEqual(self.client.get('/api/home').json()['trash'],[])
        self.assertEqual(self.chat(s=s).status_code,404)
        with self.factory() as db:
            self.assertFalse(db.execute(text('PRAGMA foreign_key_check')).all())
            messages=db.scalars(select(AssistantMessage).where(AssistantMessage.session_id==s['id'])).all()
            self.assertTrue(all(not m.content for m in messages))
            self.assertEqual(self.client.get('/api/messages/'+str(messages[0].id)).status_code,404)
            self.assertNotIn('只用于删除测试',str(retrieve(db,'经历')))
        newer=self.create('另一件事情');self.assertGreater(newer['id'],s['id'])

    def test_delete_retracts_derived_memory_suggestions_and_cached_results(self):
        s=self.create('删除来源');self.chat('我喜欢画画',s=s)
        with self.factory() as db:
            source=db.scalars(select(AssistantMessage).where(AssistantMessage.session_id==s['id'])).first()
            mid=source.id
            db.add(MemoryItem(kind='preference',content='喜欢画画',source_ids=[mid]))
            db.add(HomeReview(key='delete-review',content='画画回顾',source_ids=[mid]))
            db.add(AssistantMessage(conversation_id='home',role='assistant',content='画画记忆',citations=[{'message_id':mid,'href':f'/?session={s["id"]}&message={mid}'}]))
            suggestion=TaskSuggestion(source_message_id=mid,proposal={'kind':'create','title':'画画'})
            db.add(suggestion);db.commit();suggestion_id=suggestion.id
        s=self.action(s,'archive').json()
        r=self.client.post(f'/api/sessions/{s["id"]}/delete-permanently/confirm',json={'request_id':self.uid(),'version':s['version']})
        self.assertEqual(r.status_code,200,r.text)
        home=self.client.get('/api/home').json();self.assertEqual(home['suggestions'],[]);self.assertEqual(home['reviews'],[])
        with self.factory() as db:
            self.assertTrue(all(not m.active and not m.content for m in db.scalars(select(MemoryItem))))
            self.assertEqual(db.get(TaskSuggestion,suggestion_id).status,'deleted')
            self.assertNotIn('删除来源',str([r.response for r in db.scalars(select(OperationReceipt))]))
        messages=self.client.get('/api/home/messages').json()
        self.assertTrue(all(not m['citations'] for m in messages))
        self.assertNotIn('画画记忆',str(messages))

    def test_session_rename_uses_current_id_with_same_title_elsewhere(self):
        s=self.create('出去玩',planned_date='2026-09-10')
        with self.factory() as db:
            other=CalendarSession(title='出去玩',planned_date=date(2026,9,20),status='open')
            db.add(other);db.commit();other_id=other.id
        self.model.outputs=[{'reply':'你想把哪个出去玩的日子改成和他一起呢？两天都改吗？','proposals':[{'kind':'update','title':'和孙秦宇出去玩'}]}]
        result=self.chat('把任务改成和孙秦宇出去玩',s=s).json()
        self.assertNotIn('哪个',result['reply']);self.assertIn('2026-09-10',result['reply'])
        context=json.loads(self.model.calls[0][1]['content'].split('：\n',1)[1])
        self.assertEqual(context['default_task_target']['target_session_id'],s['id'])
        suggestion_id=result['suggestion_ids'][0]
        proposal=self.client.get('/api/home').json()['suggestions'][0]['proposal']
        self.assertEqual(proposal['target_session_id'],s['id']);self.assertNotIn('planned_date',proposal)
        self.assertEqual(self.detail(s)['title'],'出去玩')
        accepted=self.client.post(f'/api/suggestions/{suggestion_id}',json={'request_id':self.uid(),'action':'accept'})
        self.assertEqual(accepted.status_code,200,accepted.text)
        self.assertEqual(self.detail(s)['title'],'和孙秦宇出去玩')
        self.assertEqual(self.client.get(f'/api/sessions/{other_id}').json()['title'],'出去玩')

    def test_session_rename_repairs_unnecessary_question_and_wrong_target(self):
        s=self.create('出去玩',planned_date='2026-09-10')
        self.model.outputs=[{'reply':'请问哪一天？'}, {'reply':'好的','proposals':[{'kind':'update','target_session_id':999,'title':'和孙秦宇出去玩'}]}, {'reply':'好的','proposals':[{'kind':'update','target_session_id':s['id'],'title':'和孙秦宇出去玩'}]}]
        result=self.chat('把任务改成和孙秦宇出去玩',s=s).json()
        self.assertEqual(len(result['attempts']),2);self.assertEqual(len(result['suggestion_ids']),1)
        self.assertEqual(self.detail(s)['title'],'出去玩')

    def test_session_general_question_does_not_force_rename(self):
        s=self.create('出去玩')
        self.model.outputs=[{'reply':'你想和谁一起出去？'}]
        result=self.chat('你觉得我应该和谁出去玩？',s=s).json()
        self.assertEqual(result['suggestion_ids'],[]);self.assertEqual(result['attempts'],[])

    def test_home_reload_context_isolated_but_memory_and_calendar_remain(self):
        a='11111111-1111-4111-8111-111111111111';b='22222222-2222-4222-8222-222222222222'
        task=self.create('保留的日历任务')
        self.chat('我喜欢画画',home_context_id=a)
        self.chat('接着说',home_context_id=a)
        same_history=self.model.calls[-1][2:]
        self.assertTrue(any(m['content']=='我喜欢画画' for m in same_history))
        self.assertEqual(self.client.get('/api/home/messages',params={'home_context_id':b}).json(),[])
        self.chat('你记得我的爱好吗',home_context_id=b)
        model_input=self.model.calls[-1]
        self.assertEqual([m['content'] for m in model_input[2:]],['你记得我的爱好吗'])
        context=json.loads(model_input[1]['content'].split('：\n',1)[1])
        self.assertTrue(any(m['content']=='我喜欢画画' for m in context['memory']['recent_evidence']))
        self.assertTrue(any(t['id']==task['id'] for t in context['calendar']))
        old=self.client.get('/api/home/messages',params={'home_context_id':a}).json()
        self.assertEqual(len(old),4)
        self.assertEqual(self.client.get('/api/messages/'+str(old[0]['id'])).status_code,200)
        self.assertEqual(len(self.client.get('/api/home/messages',params={'home_context_id':b}).json()),2)
        self.assertEqual(self.chat(s=task,home_context_id=a).status_code,422)

    def test_home_context_is_part_of_idempotency_scope(self):
        a='11111111-1111-4111-8111-111111111111';b='22222222-2222-4222-8222-222222222222'
        data={'request_id':'context-retry','content':'你好','home_context_id':a}
        first=self.client.post('/api/home/chat',json=data)
        self.assertEqual(first.json(),self.client.post('/api/home/chat',json=data).json())
        self.assertEqual(self.client.post('/api/home/chat',json=data|{'home_context_id':b}).status_code,409)
        self.assertEqual(self.client.post('/api/home/chat',json=data|{'home_context_id':'bad'}).status_code,422)

    def test_arrangement_classification_no_chat_and_no_rollover(self):
        for title in ['和朋友吃饭','和孙秦宇出去玩','去接女朋友']:
            r=self.client.post('/api/sessions',json={'request_id':self.uid(),'title':title})
            self.assertEqual(r.status_code,201,r.text);item=r.json()
            self.assertEqual(item['activity_type'],'arrangement');self.assertFalse(item['has_session'])
            self.assertEqual(self.chat(s=item).status_code,409)
            with self.factory() as db:
                self.assertEqual(db.scalars(select(AssistantMessage).where(AssistantMessage.session_id==item['id'])).all(),[])
            self.now+=timedelta(days=1);rollover(self.factory,self.now)
            self.assertEqual(self.detail(item)['planned_date'],item['planned_date'])
        events=self.client.get('/api/calendar?start=2026-09-01&end=2026-10-01').json()
        self.assertTrue(all(e['title'].startswith('安排') for e in events))

    def test_task_classification_and_manual_conversion_preserves_history(self):
        for title in ['学习leetcode','探讨系统设计']:
            item=self.client.post('/api/sessions',json={'request_id':self.uid(),'title':title}).json()
            self.assertEqual(item['activity_type'],'task');self.assertEqual(self.chat(s=item).status_code,200)
        item=self.create('出去玩');self.chat(s=item)
        r=self.client.patch(f'/api/sessions/{item["id"]}',json={'request_id':self.uid(),'version':item['version'],'activity_type':'arrangement'})
        self.assertEqual(r.status_code,200,r.text);item=r.json()
        self.assertEqual(len(self.detail(item)['messages']),2)
        self.assertEqual(self.chat(s=item).status_code,409)
        restored=self.client.patch(f'/api/sessions/{item["id"]}',json={'request_id':self.uid(),'version':item['version'],'activity_type':'task'}).json()
        self.assertEqual(self.chat(s=restored).status_code,200)

    def test_arrangement_complete_reopen_archive_restore(self):
        item=self.create('出去玩',activity_type='arrangement')
        complete=self.action(item,'complete');self.assertEqual(complete.status_code,200,complete.text)
        item=complete.json();self.assertEqual(item['status'],'completed');self.assertFalse(item['has_session'])
        item=self.action(item,'archive').json();item=self.action(item,'restore').json()
        self.assertEqual(item['status'],'completed')
        item=self.action(item,'reopen').json();self.assertEqual(item['status'],'open')
        self.assertEqual(self.chat(s=item).status_code,409)

    def test_date_rollover_deadline_and_future_stable_ids(self):
        old=self.create(planned_date='2026-09-01'); future=self.create('散步',planned_date='2026-09-10'); deadline=self.create('交作业',planned_date='2026-09-01',deadline_date='2026-09-03')
        rollover(self.factory,self.now);rollover(self.factory,self.now)
        self.assertEqual(self.detail(old)['planned_date'],'2026-09-06');self.assertEqual(self.detail(future)['planned_date'],'2026-09-10');self.assertEqual(self.detail(deadline)['planned_date'],'2026-09-01')
        self.assertEqual(len([e for e in self.detail(old)['history'] if e['kind']=='rollover']),1)
        events=self.client.get('/api/calendar?start=2026-09-01&end=2026-10-01').json()
        self.assertTrue(all(e['allDay'] for e in events));self.assertEqual(len(events),3)
    def test_dst_server_clock_and_future_chat(self):
        s=self.create(planned_date='2026-12-01');self.assertEqual(self.chat(s=s).status_code,200)
        self.now=datetime(2026,10,3,15,59,tzinfo=timezone.utc)
        a=self.create('夏令时测试');self.assertEqual(a['planned_date'],'2026-10-04')
        self.now+=timedelta(hours=2);rollover(self.factory,self.now);self.assertEqual(self.detail(a)['planned_date'],'2026-10-04')
    def test_complete_reopen_archive_restore_preserves_messages(self):
        s=self.create();self.chat(s=s);r=self.action(s,'complete');self.assertEqual(r.status_code,200,r.text);s=r.json()
        self.assertEqual(self.chat(s=s).status_code,409);s=self.action(s,'archive').json();self.assertEqual(s['status'],'completed');s=self.action(s,'restore').json();self.assertEqual(s['status'],'completed');s=self.action(s,'reopen').json();self.assertEqual(self.chat(s=s).status_code,200)
        self.assertEqual(len(self.detail(s)['messages']),4)
    def test_manual_only_and_retired_apis(self):
        s=self.create(agenda=[{'text':'一小步','done':True}]);self.assertEqual(s['status'],'open')
        self.assertEqual(self.action(s,'complete',entry='user').status_code,422)
        for path in ['/sessions/start','/sessions/complete','/tasks','/tasks/1/chat','/chat']:
            self.assertEqual(self.client.post(path,json={}).status_code,410)
        self.assertEqual(self.client.delete('/tasks/1').status_code,410)
        from calendar_agent.core.session_policy import require_permission,SessionPermissionError
        with self.factory() as db:
            with self.assertRaises(SessionPermissionError):require_permission(db.get(CalendarSession,s['id']),'complete',entry='home')
    def test_stale_version_and_request_id_replay(self):
        p={'request_id':'same','title':'散步'}
        a=self.client.post('/api/sessions',json=p);b=self.client.post('/api/sessions',json=p);self.assertEqual(a.json(),b.json())
        self.assertEqual(self.client.post('/api/sessions',json=p|{'title':'不同'}).status_code,409)
        s=a.json();self.action(s,'archive');self.assertEqual(self.action(s,'complete').status_code,409)
    def test_model_output_rechecked_after_completion(self):
        s=self.create();self.model.callback=lambda:self.action(s,'complete')
        r=self.chat(s=s);self.assertEqual(r.status_code,409);self.assertEqual(len(self.detail(s)['messages']),1)
    def test_previous_tab_version_can_chat_after_only_rollover(self):
        s=self.create()
        self.now+=timedelta(days=1)
        rollover(self.factory,self.now)
        self.assertEqual(self.chat('继续昨天的话题',s).status_code,200)

    def test_midnight_during_model_call_does_not_close(self):
        s=self.create();self.model.callback=lambda:rollover(self.factory,self.now+timedelta(days=1))
        self.assertEqual(self.chat(s=s).status_code,200)
    def test_session_proposals_persist_accept_ignore(self):
        s=self.create();self.model.outputs=[{'reply':'我会把想法放到首页待确认。','proposals':[{'kind':'create','title':'陶艺','date_text':'明天'}]}]
        response=self.chat('明天试试陶艺',s).json();self.assertEqual(len(response['suggestion_ids']),1)
        home=self.client.get('/api/home').json();self.assertEqual(len(home['sessions']),1);sid=home['suggestions'][0]['id']
        p={'request_id':'accept','action':'accept'}
        a=self.client.post('/api/suggestions/'+str(sid),json=p);self.assertEqual(a.status_code,200,a.text);self.assertEqual(a.json(),self.client.post('/api/suggestions/'+str(sid),json=p).json())
        self.assertEqual(len(self.client.get('/api/home').json()['sessions']),2)
        self.model.outputs=[{'reply':'另一个建议。','proposals':[{'kind':'create','title':'游泳'}]}];self.chat('想游泳',s)
        suggestion=self.client.get('/api/home').json()['suggestions'][0]
        self.client.post('/api/suggestions/'+str(suggestion['id']),json={'request_id':'ignore','action':'ignore'})
        self.assertEqual(self.client.get('/api/home').json()['suggestions'],[])
    def test_duplicate_requires_confirmation_and_assignment_deadline(self):
        self.create();self.assertEqual(self.client.post('/api/sessions',json={'request_id':self.uid(),'title':'读书'}).status_code,409)
        self.assertEqual(self.client.post('/api/sessions',json={'request_id':self.uid(),'title':'数学作业'}).status_code,422)
        pending=self.client.get('/api/home').json()['suggestions'][0]
        self.client.post('/api/suggestions/'+str(pending['id']),json={'request_id':self.uid(),'action':'ignore'})
        self.model.outputs=[{'reply':'发现相似任务，请确认。','proposals':[{'kind':'create','title':'读书'}]}]
        self.chat('添加读书任务');home=self.client.get('/api/home').json();self.assertEqual(len(home['sessions']),1);self.assertEqual(len(home['suggestions']),1)
        p=home['suggestions'][0];r=self.client.post('/api/suggestions/'+str(p['id']),json={'request_id':'yes','action':'accept'});self.assertEqual(r.status_code,200,r.text)
    def test_home_model_date_and_business_retry(self):
        self.model.outputs=[{'reply':'已理解','proposals':[{'kind':'create','title':'数学作业'}]}, {'reply':'作业哪天截止？'}]
        r=self.chat('添加数学作业').json();self.assertIn('哪天',r['reply']);self.assertEqual(len(self.model.calls),2)
        self.model.outputs=[{'reply':'安排读书。','proposals':[{'kind':'create','title':'读书','date_text':'下周五'}]}]
        r=self.chat('下周五读书').json();self.assertEqual(r['saved_sessions'][0]['planned_date'],'2026-09-11')
    def test_latest_memory_sources_and_forgetting_correction(self):
        self.chat('我喜欢短一点的回答')
        with self.factory() as db:
            original=db.scalar(select(AssistantMessage).where(AssistantMessage.role=='user'))
            mid=original.id
            context,sources=retrieve(db,'喜欢');self.assertIn(mid,sources)
        self.model.outputs=[{'items':[{'kind':'preference','content':'喜欢简短回答','source_ids':[mid],'certainty':'reported'}]}]
        self.assertTrue(incremental(self.factory,self.now,self.model))
        self.model.outputs=[{'reply':'好的。','citation_ids':[mid],'memory_actions':[{'kind':'forget','source_ids':[mid],'instruction':'忘掉简短回答偏好'}]}]
        r=self.chat('忘掉我喜欢简短回答的偏好').json();self.assertEqual(r['citations'],[])
        with self.factory() as db:
            context,sources=retrieve(db,'喜欢');self.assertNotIn(mid,sources)
            self.assertEqual(db.get(AssistantMessage,mid).content,'我喜欢短一点的回答')
            self.assertFalse(db.scalar(select(MemoryItem)).active)
        calls = len(self.model.calls)
        self.assertFalse(incremental(self.factory,self.now,self.model))
        self.assertEqual(len(self.model.calls), calls)
        self.chat('今天聊点什么')
        self.assertNotIn('我喜欢短一点的回答',json.dumps(self.model.calls[-1],ensure_ascii=False))
    def test_background_failure_and_late_suppression(self):
        self.chat('我今天终于完成了第一幅画');mid=1
        from calendar_agent.llm.client import ModelError
        self.model.outputs=[ModelError('offline')];self.assertFalse(run_job(self.factory,'test',[mid],self.now,self.model))
        def forget():
            with self.factory() as db:
                apply_directive(db,MemoryAction(kind='forget',source_ids=[mid],instruction='忘掉'),self.now);db.commit()
        self.model.outputs=[{'items':[{'kind':'milestone','content':'完成画作','source_ids':[mid],'certainty':'reported'}]}];self.model.callback=forget
        self.assertTrue(run_job(self.factory,'test',[mid],self.now,self.model))
        with self.factory() as db:self.assertEqual(db.scalar(select(func.count()).select_from(MemoryItem)),0)
    def test_period_keys_and_daily_no_notification(self):
        periods=period_keys(datetime(2027,1,1,0,tzinfo=timezone.utc));self.assertEqual([p[0] for p in periods],['daily','monthly','quarterly','yearly'])
        self.now-=timedelta(days=1);self.chat('完成了一个小目标');self.now+=timedelta(days=1)
        self.model.outputs=[{'items':[]},{'items':[],'review':'进步'}];maintenance(self.factory,self.now,self.model)
        self.assertEqual(self.client.get('/api/home').json()['reviews'],[])
    def test_archive_is_readable_memory_and_no_rollover(self):
        s=self.create(planned_date='2026-09-01');self.chat('我最近喜欢画画',s);s=self.action(s,'archive').json();rollover(self.factory,self.now)
        self.assertEqual(self.detail(s)['planned_date'],'2026-09-01')
        with self.factory() as db:
            _,sources=retrieve(db,'画画');self.assertTrue(sources)
        self.assertEqual(self.client.get('/api/messages/1').status_code,200)
    def test_invalid_model_schema_does_not_write(self):
        self.model.outputs=['{"reply":"ok","complete":true}']*3
        r=self.chat().json();self.assertTrue(r['error']);self.assertEqual(len(self.client.get('/api/home').json()['sessions']),0)
    def test_cross_origin_and_forged_permissions(self):
        r=self.client.post('/api/sessions',json={'request_id':'x','title':'Hi'},headers={'Origin':'https://bad.example'});self.assertEqual(r.status_code,403)
        self.assertEqual(self.chat('hello',entry='home').status_code,422)

    def test_forget_blocks_cached_reply_and_inflight_context(self):
        self.chat('我偏好简短回答')
        self.model.outputs=[{'reply':'你喜欢简短回答。','citation_ids':[1]}]
        request={'request_id':'cached','content':'我的偏好是什么'}
        self.assertEqual(self.client.post('/api/home/chat',json=request).status_code,200)
        with self.factory() as db:
            apply_directive(db,MemoryAction(kind='forget',source_ids=[1],instruction='忘掉偏好'),self.now);db.commit()
        r=self.client.post('/api/home/chat',json=request).json()
        self.assertTrue(r['memory_redacted']);self.assertNotIn('喜欢简短',r['reply'])
        self.chat('我正在学习陶艺')
        with self.factory() as db:
            mid=db.scalar(select(AssistantMessage.id).where(AssistantMessage.content=='我正在学习陶艺'))
        def forget():
            with self.factory() as db:
                apply_directive(db,MemoryAction(kind='forget',source_ids=[mid],instruction='忘掉陶艺'),self.now);db.commit()
        self.model.callback=forget
        self.assertEqual(self.chat('我最近在学习什么').status_code,409)

    def test_relative_date_continuation_is_anchored(self):
        self.model.outputs=[{'reply':'希望先做什么？'}]
        self.chat('明天想读书')
        self.now+=timedelta(days=1)
        self.model.outputs=[{'reply':'可以。','proposals':[{'kind':'create','title':'读书','date_text':'明天'}]}]
        r=self.chat('先读一本小说').json()
        self.assertEqual(r['saved_sessions'][0]['planned_date'],'2026-09-07')

    def test_schema_does_not_accept_hallucinated_source_or_date(self):
        self.model.outputs=[{'reply':'参考历史','citation_ids':[999]}]*3
        self.assertTrue(self.chat().json()['error'])
        self.model.outputs=[{'reply':'创建','proposals':[{'kind':'create','title':'散步','date_text':'2030-01-01'}]}]*3
        self.assertTrue(self.chat('帮我添加散步').json()['error'])
        self.assertFalse(self.client.get('/api/home').json()['sessions'])

    def test_long_message_is_processed_in_bounded_chunks(self):
        import os
        from unittest.mock import patch
        self.chat('甲'*4500+'乙'*4500)
        self.model.outputs=[{'items':[]}]*5
        with patch.dict(os.environ,{'MMM_MEMORY_CONTEXT_CHARS':'4000'}):
            for i in range(5):self.assertTrue(incremental(self.factory,self.now,self.model))
            self.assertFalse(incremental(self.factory,self.now,self.model))
        for call in self.model.calls[-5:]:
            data=json.loads(call[-1]['content'])
            self.assertLessEqual(sum(len(s['content']) for s in data['sources']),2000)
        with self.factory() as db:
            self.assertEqual(len(db.get(AssistantMessage,1).content),9000)

    def test_period_review_delivery_combines_once_and_suppresses(self):
        from calendar_agent.scheduler.personal_periods import organize_periods
        self.now=datetime(2026,12,31,0,tzinfo=timezone.utc)
        self.chat('我完成了自己的第一幅画，感到开心')
        policy={'enabled':True,'hour':9,'combine':True,'catch_up':'none','deadline_days':3}
        self.assertEqual(self.client.put('/api/preferences',json=policy).status_code,200)
        self.now=datetime(2027,1,1,0,tzinfo=timezone.utc)
        self.model.outputs=[{'items':[],'review':'完成画作是值得记住的进步。'}]*15
        for i in range(4):organize_periods(self.factory,self.now,self.model)
        reviews=self.client.get('/api/home').json()['reviews']
        self.assertEqual(len(reviews),1)
        self.assertIn('月度',reviews[0]['content']);self.assertIn('季度',reviews[0]['content']);self.assertIn('年度',reviews[0]['content'])
        organize_periods(self.factory,self.now,self.model)
        self.assertEqual(len(self.client.get('/api/home').json()['reviews']),1)
        with self.factory() as db:
            apply_directive(db,MemoryAction(kind='forget',source_ids=[1],instruction='忘掉画作'),self.now);db.commit()
        self.assertEqual(self.client.get('/api/home').json()['reviews'],[])

    def test_catchup_preference_is_explicit(self):
        from calendar_agent.scheduler.personal_periods import organize_periods
        self.now=datetime(2026,8,25,0,tzinfo=timezone.utc);self.chat('完成一个小目标')
        self.now=datetime(2026,9,6,0,tzinfo=timezone.utc)
        self.model.outputs=[{'items':[],'review':'一项进步'}]*10
        self.client.put('/api/preferences',json={'enabled':True,'hour':9,'combine':False,'catch_up':'none','deadline_days':3})
        for i in range(3):organize_periods(self.factory,self.now,self.model)
        self.assertFalse(self.client.get('/api/home').json()['reviews'])
        self.client.put('/api/preferences',json={'enabled':True,'hour':9,'combine':False,'catch_up':'latest','deadline_days':3})
        organize_periods(self.factory,self.now,self.model)
        self.assertEqual(len(self.client.get('/api/home').json()['reviews']),1)

    def test_manual_duplicate_confirmation_is_persisted_and_idempotent(self):
        self.create('阅读')
        p={'request_id':'duplicate','title':'阅读'}
        r=self.client.post('/api/sessions',json=p)
        self.assertEqual(r.status_code,409)
        sid=r.json()['detail']['suggestion_id']
        self.assertEqual(self.client.post('/api/sessions',json=p).json(),r.json())
        r=self.client.post('/api/suggestions/'+str(sid),json={'request_id':'accept-manual','action':'accept'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(len(self.client.get('/api/home').json()['sessions']),2)

    def test_home_explicit_archive_and_session_update_cannot_write(self):
        s=self.create('小说阅读')
        self.model.outputs=[{'reply':'已完成修改','proposals':[{'kind':'archive','target_session_id':s['id'],'version':s['version']}]}]
        r=self.chat('删除小说阅读').json();self.assertTrue(r['saved_sessions'][0]['archived_at'])
        s=self.create('陶艺练习')
        self.model.outputs=[{'reply':'已更新','proposals':[{'kind':'update','target_session_id':s['id'],'version':s['version'],'session_goal':'做一个杯子'}]}]
        r=self.chat('把目标改成做一个杯子',s).json();self.assertEqual(self.detail(s)['session_goal'],'');self.assertEqual(len(r['suggestion_ids']),1)

    def test_month_day_without_suffix_and_equivalent_model_spelling(self):
        from calendar_agent.core.request import day_of
        for token in ('九月20','九月二十号','9月20日','9月20'):
            self.assertEqual(str(day_of(token,self.now)),'2026-09-20')
        self.model.outputs=[{'reply':'好的','proposals':[{'kind':'create','title':'出去玩','date_text':'九月二十号'}]}]
        r=self.chat('我九月20想出去玩').json()
        self.assertEqual(r['saved_sessions'][0]['planned_date'],'2026-09-20')

    def test_both_dates_followup_and_legacy_assistant_not_used(self):
        with self.factory() as db:
            db.add(AssistantMessage(conversation_id='home',role='user',content='九月十号想出去玩',created_at=self.now))
            db.add(AssistantMessage(conversation_id='home',role='assistant',content='必须告诉我玩多久',origin_key='planning:test:1',created_at=self.now))
            db.add(AssistantMessage(conversation_id='home',role='user',content='我九月20想出去玩',created_at=self.now))
            db.add(AssistantMessage(conversation_id='home',role='assistant',content='两天都出去玩吗？',created_at=self.now))
            db.commit()
        self.model.outputs=[{'reply':'两天分别安排','proposals':[{'kind':'create','title':'出去玩','date_text':'九月十号'},{'kind':'create','title':'出去玩','date_text':'9月20日'}]}]
        r=self.chat('都出去玩').json()
        self.assertEqual([s['planned_date'] for s in r['saved_sessions']],['2026-09-10','2026-09-20'])
        self.assertEqual(r['suggestion_ids'],[])
        self.assertNotIn('必须告诉我玩多久',json.dumps(self.model.calls[0],ensure_ascii=False))

    def test_new_past_tasks_rejected_but_history_is_readable(self):
        r=self.client.post('/api/sessions',json={'request_id':'past','title':'不能新增','planned_date':'2026-09-05'})
        self.assertEqual(r.status_code,422)
        historical=self.create('已存在的历史',planned_date='2026-09-05',deadline_date='2026-09-05')
        self.assertEqual(self.chat('继续',historical).status_code,200)
        self.assertEqual(self.detail(historical)['planned_date'],'2026-09-05')

    def test_failed_output_has_saved_diagnostics_without_blame(self):
        self.model.outputs=['not JSON']*3
        r=self.chat('都出去玩').json()
        self.assertTrue(r['error'])
        self.assertNotIn('补充明确日期',r['reply'])
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(PlanAttempt)),3)

if __name__=='__main__': unittest.main()
