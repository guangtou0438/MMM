"""Synthetic tool workflows and real runtime procedural replay, with optional live LLMs."""
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from ..app import create_app
from ..memory.procedural import propose, activate
from .metrics import mean, tool_accuracy

NOW = datetime(2026,9,6,tzinfo=timezone.utc)
WEATHER_CASES = [
    {'rain':90,'high':20,'low':10,'wind':10},
    {'rain':10,'high':40,'low':10,'wind':10},
    {'rain':10,'high':20,'low':10,'wind':60}]


class Forecast:
    def __init__(self): self.value=WEATHER_CASES[0]
    def forecast(self,location,day):
        return self.value | {'location':location,'date':str(day),'provider':'synthetic-replay'}


class FixtureModel:
    def __init__(self): self.outputs=[]
    def stream(self,messages,model,max_tokens=4096):
        yield 'content',json.dumps(self.outputs.pop(0),ensure_ascii=False)
        # Fixture usage intentionally absent; reserve costs are not billed Token measurements.
        yield 'usage',1000


def wait(client,run_id):
    end=time.monotonic()+200
    while time.monotonic()<end:
        run=client.get('/api/agent/runs/'+run_id).json()
        if run['status'] in {'completed','failed','cancelled','waiting_for_user'}: return run
        time.sleep(.02)
    raise TimeoutError('Synthetic workflow did not terminate')


def evaluate_workflows(live=False):
    result={'mode':'live' if live else 'fixture','weather':'synthetic, never real personal activity','models':[]}
    for model in ('glm','deepseek'):
        with tempfile.TemporaryDirectory(prefix='mmm-workflow-') as directory:
            fixture=FixtureModel()
            app=create_app(Path(directory)/'synthetic.db',None if live else fixture,lambda:NOW,scheduler_enabled=False)
            with TestClient(app) as client:
                app.state.memory_service.embedding=None
                weather=Forecast();app.state.weather_service.provider=weather
                records=[]
                def run_case(goal,expected,outputs,check,phase='baseline'):
                    fixture.outputs=outputs
                    started=time.monotonic()
                    run=client.post('/api/agent/runs',json={'content':goal,'request_id':str(time.time_ns()),'session_key':str(time.time_ns()),'model':model,'kind':'complex'}).json()
                    final=wait(client,run['id'])
                    calls=app.state.agent_store.rows('SELECT tool,status FROM tool_calls WHERE run_id=?',(run['id'],))
                    selected={call['tool'] for call in calls if call['status']=='completed'}
                    record={'phase':phase,'goal':goal,'run_id':run['id'],'success':int(final['status']=='completed' and check()),
                            'tool_accuracy':tool_accuracy(expected,calls),'steps':final['calls'],
                            'provider_tokens':final['actual_tokens'] if live else None,'seconds':time.monotonic()-started,
                            'status':final['status'],'error':final['error'],'tools':calls}
                    records.append(record)
                    return record
                create={'action':'tool','skill':'calendar','tool':'create_task','arguments':{'title':'Synthetic report','planned_date':'2026-09-07'}}
                run_case('Create a task titled Synthetic report on 2026-09-07.', ['create_task'],[create,{'action':'answer','answer':'Created'}],
                         lambda:any(t['title']=='Synthetic report' for t in client.get('/api/home').json()['sessions']))
                response=client.post('/api/sessions',json={'request_id':'seed:outdoor','title':'Outdoor running','planned_date':'2026-09-07'})
                response.raise_for_status()
                tasks=[response.json()]*3
                for phase in ('before','after'):
                    if phase=='after':
                        try:
                            candidate=propose(app.state.memory_service)
                            activate(app.state.memory_service,candidate,True)
                        except ValueError as error:
                            result['models'].append({'model':model,'candidate_status':'unavailable','reason':str(error),'records':records})
                            break
                    for index,task in enumerate(tasks):
                        weather.value=WEATHER_CASES[index]
                        output=[{'action':'tool','skill':'weather_planning','tool':'get_weather','arguments':{'location':'Melbourne, Victoria','day':'2026-09-07'}},
                                {'action':'tool','skill':'weather_planning','tool':'create_adjustment_suggestion','arguments':{'session_id':task['id'],'location':'Melbourne, Victoria'}},
                                {'action':'answer','answer':'Weather suggestion created; calendar unchanged.'}]
                        record=run_case(f"Check the weather for outdoor running task #{task['id']} in Melbourne, Victoria on 2026-09-07 and create a suggestion if thresholds are crossed. Do not change the calendar.",
                            ['get_weather','create_adjustment_suggestion'],output,
                            lambda:any(s['session_id']==task['id'] for s in client.get('/api/weather/suggestions').json()),phase)
                        current=client.get('/api/sessions/'+str(task['id'])).json()
                        record['misoperations']=int(current['planned_date']!=task['planned_date'] or current['version']!=task['version'])
                else:
                    metrics={phase:{metric:mean([r.get(metric) for r in records if r['phase']==phase]) for metric in ('success','tool_accuracy','steps','provider_tokens','seconds','misoperations')} for phase in ('baseline','before','after')}
                    result['models'].append({'model':model,'candidate_status':'enabled_by_synthetic_user','metrics':metrics,'records':records})
    return result
