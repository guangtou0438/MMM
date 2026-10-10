"""Regression cases from the user's conversation; isolated SQLite databases."""
import json
from datetime import timedelta
from zoneinfo import ZoneInfo
import unittest
import test_calendar_m2 as base
from test_calendar_m2 import NOW, MESSAGE
from calendar_agent.core.request import resolve_request, continue_message


class ConversationRegressionTests(unittest.TestCase):
    setUp = base.PlannerTests.setUp
    tearDown = base.PlannerTests.tearDown
    chat = base.PlannerTests.chat
    def test_today_and_tomorrow_no_buffer(self):
        for text, offset in (("我今天想完成leetcode学习", 0), ("我明天想完成leetcode学习", 1)):
            result = self.chat(text)
            self.assertEqual(result['intent'], 'create_plan', result)
            for step in result['created_tasks'][0]['subtasks']:
                from datetime import datetime
                local = datetime.fromisoformat(step['slot_start_utc']).astimezone(ZoneInfo('Australia/Melbourne'))
                self.assertEqual(local.date(), NOW.astimezone(ZoneInfo('Australia/Melbourne')).date()+timedelta(days=offset))

    def test_unavailable_days_are_removed_from_prompt(self):
        result = self.chat(MESSAGE + '，明天要出远门没时间，后天要上课')
        self.assertEqual(result['intent'], 'create_plan', result)
        from datetime import datetime
        slots = json.loads(self.model.calls[-1][1]['content'])['context']['free_slots']
        for slot in slots:
            self.assertNotIn(datetime.fromisoformat(slot['slot_start']).astimezone(ZoneInfo('Australia/Melbourne')).day, (6,7))

    def test_new_subject_does_not_inherit_pending(self):
        first = self.chat('我明天想完成leetcode学习，预计 100 小时')
        self.assertEqual(first['intent'], 'clarify')
        lunch = self.chat('我大后天要去朋友家吃午饭', conversation_id=first['conversation_id'])
        self.assertEqual(lunch['intent'], 'clarify')
        self.assertIn('几点', lunch['reply'])
        cv = self.chat('我下周六六点要改简历，帮我标注一下', conversation_id=first['conversation_id'])
        self.assertIn('上午', cv['reply'])
        follow = self.chat('简历呢', conversation_id=first['conversation_id'])
        self.assertIn('六点', follow['reply'])
        self.assertNotIn('leetcode', follow['reply'].lower())
        done = self.chat('下午六点，预计 1 小时', conversation_id=first['conversation_id'])
        self.assertEqual(done['intent'], 'create_plan', done)
        data = json.loads(self.model.calls[-1][1]['content'])
        self.assertNotIn('leetcode', data['message'].lower())
        self.assertIn('简历', data['message'])
        self.assertEqual(data['resolved']['mode'], 'event')
        step = done['created_tasks'][0]['subtasks']
        self.assertEqual(len(step), 1)
        self.assertEqual(step[0]['slot_start_utc'], '2026-09-12T08:00:00Z')

    def test_lunch_followup_and_chinese_clock(self):
        first = self.chat('我大后天要去朋友家吃午饭')
        done = self.chat('中午十二点，预计 1 小时', conversation_id=first['conversation_id'])
        self.assertEqual(done['intent'], 'create_plan', done)
        self.assertEqual(done['created_tasks'][0]['subtasks'][0]['slot_start_utc'], '2026-09-08T02:00:00Z')

    def test_exact_event_conflict_does_not_move_it(self):
        self.chat('下周六18:00改简历，预计 1 小时')
        result = self.chat('下周六18:00开会，预计 1 小时')
        self.assertEqual(result['intent'], 'clarify')
        self.assertEqual(len(self.model.calls), 1)

    def test_reference_to_existing_task_is_read_only(self):
        first = self.chat(MESSAGE)
        result = self.chat('COMP90054呢', conversation_id=first['conversation_id'])
        self.assertEqual(result['intent'], 'query')
        self.assertEqual(result['created_tasks'], [])
        self.assertEqual(len(self.model.calls), 1)

    def test_explicit_early_event_and_last_hour_deadline(self):
        event = self.chat('明天早上六点改简历，预计 1 小时')
        self.assertEqual(event['intent'], 'create_plan', event)
        self.assertEqual(event['created_tasks'][0]['subtasks'][0]['slot_start_utc'], '2026-09-05T20:00:00Z')
        result = self.chat('今天 11:00 之前完成测试，预计 1 小时')
        self.assertEqual(result['intent'], 'create_plan', result)

    def test_standalone_class_needs_specific_time(self):
        result = self.chat('明天要上课')
        self.assertEqual(result['intent'], 'clarify')
        self.assertIn('几点', result['reply'])

    def test_meal_popup_confirmation_and_persisted_context(self):
        result = self.chat('我明天要去朋友家吃饭，中午')
        conversation = result['conversation_id']
        for answer in ('是的', '帮我写在日程里'):
            self.assertEqual(result['intent'], 'clarify')
            self.assertIn('12:00', result['clarification']['time_options'])
            self.assertEqual(result['clarification']['date'], '2026-09-06')
            self.assertEqual(len(self.model.calls), 0)
            result = self.chat(answer, conversation_id=conversation)
        restored = self.client.get('/planning/conversations/'+conversation).json()
        self.assertEqual(restored['messages'][-1]['clarification'], result['clarification'])
        done = self.chat('2026-09-06 12:30，预计 60 分钟', conversation_id=conversation)
        self.assertEqual(done['intent'], 'create_plan', done)
        data = json.loads(self.model.calls[-1][1]['content'])
        self.assertIn('朋友家吃饭', data['message'])
        self.assertEqual(len(done['created_tasks'][0]['subtasks']), 1)
        self.assertEqual(done['created_tasks'][0]['subtasks'][0]['slot_start_utc'], '2026-09-06T02:30:00Z')
        self.assertIsNone(self.client.get('/planning/conversations/'+conversation).json()['messages'][-1]['clarification'])

    def test_pending_relative_date_is_frozen_across_midnight(self):
        from calendar_agent.core.planner import plan_request
        from calendar_agent.schemas import ChatInput
        result = self.chat('我明天要去朋友家吃饭，中午')
        result = plan_request(self.app.state.sessions, ChatInput(message='是的', conversation_id=result['conversation_id']), client=self.model, clock=lambda: NOW+timedelta(days=1))
        self.assertEqual(result['clarification']['date'], '2026-09-06')
        self.assertEqual(result['intent'], 'clarify')

    def test_chinese_date_and_unrestricted_event_duration(self):
        result=self.chat('九月十号我想出去玩')
        self.assertEqual(result['clarification']['date'],'2026-09-10')
        for text,minutes in [('明天 12:00 开会，预计 1 分钟',1),('下周六 12:00 出游，预计 48 小时',2880)]:
            result=self.chat(text)
            self.assertEqual(result['intent'],'create_plan',result)
            self.assertEqual(result['created_tasks'][0]['subtasks'][0]['duration_min'],minutes)

    def test_general_chat_name_and_calendar_context(self):
        from unittest.mock import patch
        named=self.chat('我的名字是Tommy')
        self.assertEqual(named['intent'],'query')
        self.assertIn('Tommy',named['reply'])
        self.chat(MESSAGE)
        answer=json.dumps({'intent':'query','clarify_question':None,'goal':None,'subtasks':[],'reasoning':'Tommy，我们可以聊学习方法。'})
        with patch.object(self.model,'complete',return_value=answer) as call:
            result=self.chat('聊聊学习方法')
            context=json.loads(call.call_args.args[0][1]['content'])['context']
            self.assertEqual(context['user_facts']['name'],'Tommy')
            self.assertIn('COMP90054',context['upcoming_summary'])
            self.assertEqual(result['intent'],'query')

    def test_no_past_manual_or_llm_plan_and_task_colors(self):
        body={'title':'future','deadline_utc':'2026-09-10T00:00:00Z','estimated_minutes':1,'subtasks':[{'title':'step','duration_min':1,'slot_start_utc':'2026-09-04T00:00:00Z'}]}
        self.assertEqual(self.client.post('/tasks',json=body).status_code,400)
        result=self.chat('2026-09-04 12:00 开会，预计 1 分钟')
        self.assertEqual(result['intent'],'clarify')
        for hour in (1,2):
            body['subtasks'][0]['slot_start_utc']=f'2026-09-05T0{hour}:00:00Z'
            self.assertEqual(self.client.post('/tasks',json=body).status_code,201)
        events=self.client.get('/calendar',params={'start':'2026-09-05T00:00Z','end':'2026-09-06T00:00Z'}).json()
        self.assertEqual(len({e['color'] for e in events}),2)
