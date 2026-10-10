"""Code-owned due dates, resumable budgeted reduction, and independent delivery."""
import hashlib
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from ..config import DISPLAY_TIMEZONE
from ..models import AppPreference, AssistantMessage, MemoryJob, HomeReview
from ..memory.personal import blocked_sources, budget, run_job

DEFAULTS = {'enabled': False, 'hour': 9, 'combine': True, 'catch_up': 'none', 'deadline_days': 3}

def preferences(db):
    row = db.get(AppPreference, 'reviews')
    return DEFAULTS | (row.value if row else {})

def next_month(day): return date(day.year+1,1,1) if day.month==12 else date(day.year,day.month+1,1)

def periods_for(day):
    month = day.replace(day=1)
    qmonth = (day.month-1)//3*3+1
    qstart = date(day.year,qmonth,1)
    qend = date(day.year+1,1,1) if qmonth==10 else date(day.year,qmonth+3,1)
    return [('daily',str(day),day,day+timedelta(days=1)),
            ('monthly',day.strftime('%Y-%m'),month,next_month(month)),
            ('quarterly',f'{day.year}-Q{(day.month-1)//3+1}',qstart,qend),
            ('yearly',str(day.year),date(day.year,1,1),date(day.year+1,1,1))]

def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()[:16]

def groups(values, cap):
    group, size = [], 0
    for value in values:
        cost = len(json.dumps(value,ensure_ascii=False))
        if group and size+cost>cap:
            yield group;group=[];size=0
        group.append(value);size+=cost
    if group: yield group

def organize_periods(factory, now, client=None):
    local = now.astimezone(ZoneInfo(DISPLAY_TIMEZONE)); day = local.date()
    with factory() as db:
        policy = preferences(db); blocked = blocked_sources(db)
        rows = [r for r in db.scalars(select(AssistantMessage).where(AssistantMessage.role=='user').order_by(AssistantMessage.id)) if r.id not in blocked]
        work = {}
        for row in rows:
            source_day = row.created_at.astimezone(ZoneInfo(DISPLAY_TIMEZONE)).date()
            for p in periods_for(source_day):
                if p[3]<day or (p[3]==day and (p[0]=='daily' or local.hour>=policy['hour'])): work[p] = True
    # Work is bounded per tick and each reduction node is independently restartable.
    calls_left = 2
    roots = []
    for kind, name, start, end in sorted(work,key=lambda p:({'daily':0,'monthly':1,'quarterly':2,'yearly':3}[p[0]],p[1])):
        relevant = [r for r in rows if start<=r.created_at.astimezone(ZoneInfo(DISPLAY_TIMEZONE)).date()<end]
        values = []
        chunk = max(800,budget()//4)
        lower_kind = {'monthly':'daily','quarterly':'monthly','yearly':'quarterly'}.get(kind)
        covered = set()
        for child_kind, child_name, child_start, child_end, child in roots:
            if child_kind != lower_kind or not (start<=child_start and child_end<=end): continue
            if not child['content']: continue
            covered.update(child['source_ids'])
            for offset in range(0,len(child['content']),chunk):
                values.append({'source_ids':child['source_ids'],'content':child['content'][offset:offset+chunk], 'child_job':child['key']})
        for row in relevant:
            if row.id in covered: continue
            for offset in range(0,len(row.content),chunk):
                values.append({'source_ids':[row.id], 'content':row.content[offset:offset+chunk], 'range':[offset,min(offset+chunk,len(row.content))], 'created_at':row.created_at.isoformat()})
        level = 0
        root = None
        while values:
            parent_values = []; complete = True
            for group in groups(values,budget()//2):
                key = f'period:{kind}:{name}:{level}:{digest(group)}'
                ids = sorted({i for value in group for i in value['source_ids']})
                with factory() as db:
                    job = db.get(MemoryJob,key)
                    ready = job and job.status=='done'
                    retry_after = job.updated_at+timedelta(seconds=min(1800,30*(2**min(job.attempts,6)))) if job and job.status=='failed' else now
                if not ready and calls_left>0 and now>=retry_after:
                    run_job(factory,key,ids,now,client,kind,supplied_evidence=group);calls_left-=1
                with factory() as db:
                    job = db.get(MemoryJob,key)
                    if not job or job.status!='done': complete=False;continue
                    safe_ids = [i for i in job.source_ids if i not in blocked_sources(db)]
                    if not safe_ids: continue
                    # The model controls content and detail; the reducer controls only resource bounds.
                    payload = job.payload
                    content = payload.get('review') or '\n'.join(i['content'] for i in payload.get('items',[]))
                    root = {'key':key,'content':content,'source_ids':safe_ids}
                    # Prevent a verbose output from defeating the next-level budget.
                    for offset in range(0,max(1,len(content)),chunk):
                        parent_values.append({'source_ids':safe_ids,'content':content[offset:offset+chunk],'child_job':key})
            if not complete: root=None;break
            group_count = len(list(groups(values,budget()//2)))
            if group_count==1: break
            if not parent_values or level>=6: root=None;break
            values=parent_values;level+=1
        if root: roots.append((kind,name,start,end,root))
    if not policy['enabled']: return
    # No catch-up is silently assumed: it is an explicit stored user preference.
    eligible = []
    for kind,name,start,end,root in roots:
        if kind=='daily' or not root['content'].strip(): continue
        if policy['catch_up']=='none' and end!=day: continue
        if policy['catch_up']=='latest':
            current_start = next(p[2] for p in periods_for(day) if p[0]==kind)
            if end!=current_start: continue
        eligible.append((kind,name,start,end,root))
    delivery_groups = {}
    for p in eligible:
        notification = 'review:'+str(p[3]) if policy['combine'] else p[0]+':'+p[1]
        delivery_groups.setdefault(notification,[]).append(p)
    with factory() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        blocked = blocked_sources(db)
        for key, parts in delivery_groups.items():
            # On overlapping boundaries, wait for all due nonempty periods, then send once.
            if policy['combine']:
                needed = {(p[0],p[1]) for p in work if p[0]!='daily' and str(p[3])==key.split(':',1)[1]}
                if not needed.issubset({(p[0],p[1]) for p in parts}): continue
            if db.get(HomeReview,key): continue
            safe = [p for p in parts if not blocked.intersection(p[4]['source_ids'])]
            if not safe: continue
            content = '\n\n'.join(f'{ {"monthly":"月度", "quarterly":"季度", "yearly":"年度"}[p[0]]} · {p[1]}\n{p[4]["content"]}' for p in safe)
            db.add(HomeReview(key=key,content=content,source_ids=sorted({i for p in safe for i in p[4]['source_ids']}),created_at=now))
        db.commit()
