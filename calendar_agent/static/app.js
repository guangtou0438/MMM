'use strict';
const $ = id => document.getElementById(id);
const uid = () => crypto.randomUUID();
let state = null, current = null, calendar = null, editing = null, suggestionEditing = null;
// A page reload starts a fresh home conversation; never persist this ID in storage.
const homeContextId = uid();
let homeBusy = false, sessionBusy = false;
const escapeText = value => String(value ?? '');
function node(tag, text, cls) {const n = document.createElement(tag);if(text !== undefined)n.textContent = escapeText(text);if(cls)n.className=cls;return n;}
function button(text, action, cls='quiet'){const b=node('button',text,cls);b.type='button';b.addEventListener('click',()=>Promise.resolve(action()).catch(report));return b;}
function report(error){$('notice').hidden=false;$('notice').textContent=error.message || String(error);}
async function api(path, options={}){const res=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});let data=await res.json();if(!res.ok){const d=data.detail;const msg=typeof d==='string'?d: d?.message || JSON.stringify(d);const e=new Error(msg);e.detail=d;throw e;}return data;}
const post=(path,data)=>api(path,{method:'POST',body:JSON.stringify(data),...(path.endsWith('/delete-permanently/confirm')?{headers:{'X-UI-Action':'permanent-delete'}}:{})});
function linkSource(c){const a=node('a',c.label||`来源 #${c.message_id}`);a.href=c.href;a.addEventListener('click',e=>{e.preventDefault();navigate(c.href).catch(report);});return a;}
function renderMessages(container, messages){container.replaceChildren();if(!messages.length){const n=node('div','从这里开始，慢慢聊。','empty');n.append(node('small','你的任务会持续保留，不必在今天全部完成。'));container.append(n);}for(const m of messages){const n=node('article',undefined,'message '+m.role);n.id='message-'+m.id;n.append(node('span',m.role==='user'?'你':'MMM','role'),node('div',m.content,'content'));if(m.citations?.length){const c=node('div',undefined,'citations');m.citations.forEach(x=>c.append(linkSource(x)));n.append(c);}container.append(n);}container.scrollTop=container.scrollHeight;}
function taskLink(s){const b=button('',()=>{if(current?.id===s.id&&!$('session-view').hidden)return;return openSession(s.id);},'task-link');b.dataset.sessionId=s.id;return b;}
function populateTasks(container, rows){
  container.replaceChildren();
  for(const s of rows){
    const b=taskLink(s);b.append(node('span',s.title),node('small',(s.planned_date||'历史未安排')+(s.status==='completed'?' · 已完成':'')));
    b.setAttribute('aria-current',current?.id===s.id?'page':'false');
    const item=node('div',undefined,'task-card'+(s.archived_at?' trash-item':''));
    const manage=async action=>{await post(`/api/sessions/${s.id}/${action}`,{request_id:uid(),version:s.version});if(current?.id===s.id)await goHome();else await refresh();};
    const actions=s.archived_at?[button('恢复',()=>manage('restore'),'quiet'),button('永久删除',()=>openDelete(s),'danger')]:[button('调整任务',()=>openEditor(s),'quiet'),button('移入回收站',()=>manage('archive'),'quiet')];
    item.append(b,moreActions(actions));container.append(item);
  }
  if(!rows.length)container.append(node('p','暂无记录','muted small'));
}

async function refresh(){state=await api('/api/home');const open=state.sessions.filter(s=>s.status==='open'&&s.activity_type!=='arrangement');$('open-count').textContent=open.length;$('trash-count').textContent=state.trash.length;$('empty-trash').hidden=!state.trash.length;populateTasks($('task-list'),open.filter(s=>s.title.toLocaleLowerCase().includes($('task-search').value.trim().toLocaleLowerCase())));populateTasks($('trash-list'),state.trash);$('topic-list').replaceChildren(...state.topics.map(t=>button(t.title,()=>openTopic(t.id),'task-link')));
const reminders=$('reminders');reminders.replaceChildren();for(const s of open.filter(s=>s.deadline_date)){const delta=Math.round((Date.parse(s.deadline_date)-Date.parse(state.today))/86400000);if(delta<=(state.preferences?.deadline_days??3))reminders.append(button(`${delta<0?'已逾期 '+(-delta)+' 天':delta===0?'今天截止':delta+' 天后截止'} · ${s.title}`,()=>openSession(s.id),'reminder'+(delta<0?' overdue':'')));}
renderSuggestions();renderReviews();if(calendar){calendar.setOption('now',state.today);calendar.refetchEvents();}}
function renderSuggestions(){$('suggestions-panel').hidden=!state.suggestions.length;const box=$('suggestions');box.replaceChildren();for(const s of state.suggestions){const p=s.proposal;const row=node('div',undefined,'suggestion');const content=node('div');const names={create:'新待办',update:'调整任务',archive:'移入回收站',restore:'恢复任务'};content.append(node('strong',`${names[p.kind]} · ${p.title||'#'+p.target_session_id}`));content.append(node('p',p.session_goal || (p.planned_date?'计划日 '+p.planned_date:'请确认此项操作'),'muted small'));if(p._duplicates?.length)content.append(node('p','相似任务：'+p._duplicates.map(i=>state.sessions.find(r=>r.id===i)?.title||'#'+i).join('、')+'。接受将另建一项。','error'));content.append(linkSource(s.source));const acts=node('div',undefined,'actions');acts.append(button('接受',()=>decide(s.id,'accept'),'primary'));if(['create','update'].includes(p.kind))acts.append(button('修改',()=>openSuggestion(s),'secondary'));acts.append(button('忽略',()=>decide(s.id,'ignore'),'quiet'));row.append(content,acts);box.append(row);}}
function renderReviews(){$('reviews').replaceChildren();for(const r of state.reviews){const n=node('section',undefined,'panel review');const h=node('div',undefined,'panel-heading');h.append(node('h2','成长回顾 · '+r.key),button('收起',async()=>{await post('/api/reviews/dismiss',{key:r.key});await refresh();}));n.append(h,node('p',r.content));const citations=node('div',undefined,'citations');r.citations.slice(0,12).forEach(c=>citations.append(linkSource(c)));n.append(citations);$('reviews').append(n);}}
async function decide(id,action,proposal){await post('/api/suggestions/'+id,{request_id:uid(),action,...(proposal?{proposal}: {})});await refresh();}
async function loadHomeMessages(){renderMessages($('home-messages'),await api('/api/home/messages?home_context_id='+homeContextId));}
async function goHome(){closeMobileMenu();current=null;$('home-view').hidden=false;$('session-view').hidden=true;$('breadcrumb').textContent='日历';history.replaceState(null,'','/');await refresh();calendar?.updateSize();await loadHomeMessages();}
async function openSession(id,mid,loaded){closeMobileMenu();current=loaded||await api('/api/sessions/'+id);if(current.activity_type==='arrangement'){const item=current;await goHome();openEditor(item);history.replaceState(null,'','/?arrangement='+id);if(mid){const message=await api('/api/messages/'+mid);$('task-dialog').close();$('history-title').textContent='原始聊天记录';renderMessages($('history-content'),[message]);$('history-dialog').showModal();}return;}$('home-view').hidden=true;$('session-view').hidden=false;$('breadcrumb').textContent='任务';history.replaceState(null,'',`/?session=${id}${mid?'&message='+mid:''}`);$('session-title').textContent=current.title;$('session-meta').textContent=(current.planned_date||'历史任务')+' · '+(current.archived_at?'回收站':current.status==='completed'?'已完成 · 重新开启后继续':'进行中');$('session-goal').textContent=current.session_goal||'一起讨论，把下一步慢慢理清。';if(current.migration_metadata?.unconfirmed_deadline_utc&&!current.deadline_date)$('session-goal').append(node('small','\n旧截止日期待核对：请在「调整任务」中确认；原值保留在任务历史中。','muted'));const actions=$('session-actions');actions.replaceChildren();if(current.archived_at){actions.append(button('恢复任务',()=>lifecycle('restore'),'primary'),moreActions([button('永久删除',()=>openDelete(current),'danger')]));}else{if(current.status==='open')actions.append(button('✓ 完成任务',()=>lifecycle('complete'),'primary'),button('调整任务',()=>openEditor(current),'secondary'));else actions.append(button('重新开启',()=>lifecycle('reopen'),'primary'));actions.append(moreActions([button('移入回收站',()=>lifecycle('archive'),'quiet')]));}
const readonly=current.status!=='open'||!!current.archived_at;$('session-input').disabled=readonly;$('session-chat-form').querySelector('button').disabled=readonly||sessionBusy;$('session-chat-status').textContent=readonly?'此会话只读；请先恢复或重新开启。':'任务调整会转为首页待确认建议';renderAgenda(current,readonly);
const names={created:'创建任务',rollover:'自动顺延',updated:'调整任务',complete:'手动完成',reopen:'重新开启',archive:'移入回收站',restore:'恢复任务',migration_v2:'保留原任务并迁移'};$('session-history').replaceChildren(...current.history.map(e=>node('div',(names[e.kind]||e.kind)+' · '+new Date(e.created_at).toLocaleString('zh-CN',{timeZone:'Australia/Melbourne'})+(e.kind==='rollover'?`\n${e.detail.from} → ${e.detail.to}`:''),'history-event')));$('view-topic').hidden=!current.topic_id;$('view-topic').onclick=()=>openTopic(current.topic_id).catch(report);renderMessages($('session-messages'),current.messages);document.querySelectorAll('[data-session-id]').forEach(b=>b.setAttribute('aria-current',String(b.dataset.sessionId)===String(id)?'page':'false'));if(mid)highlight(mid);}
let agendaView = null;
function renderAgenda(session, readonly) {
  const view={id:session.id,version:session.version,confirmed:session.agenda.map(a=>({...a})),wanted:session.agenda.map(a=>({...a})),saving:false,checks:[]};
  agendaView=view;
  const box=$('session-agenda');box.replaceChildren();
  const hint=node('p','清单已全部完成，准备好时可点击「完成任务」。','muted small agenda-completion');
  const updateHint=()=>{hint.style.visibility=view.wanted.length&&view.wanted.every(a=>a.done)&&!readonly?'visible':'hidden';};
  async function save() {
    if(view.saving)return;
    view.saving=true;box.setAttribute('aria-busy','true');
    try {
      while(JSON.stringify(view.wanted)!==JSON.stringify(view.confirmed)) {
        const submitted=view.wanted.map(a=>({...a}));
        const result=await api('/api/sessions/'+view.id,{method:'PATCH',body:JSON.stringify({request_id:uid(),version:view.version,agenda:submitted})});
        view.version=result.version;view.confirmed=submitted;
        if(agendaView===view&&current?.id===view.id){current.version=result.version;current.agenda=submitted;}
      }
    } catch(error) {
      view.wanted=view.confirmed.map(a=>({...a}));
      if(agendaView===view){view.checks.forEach((checkbox,i)=>checkbox.checked=!!view.wanted[i].done);updateHint();report(error);}
    } finally {
      view.saving=false;
      if(agendaView===view)box.removeAttribute('aria-busy');
      document.dispatchEvent(new CustomEvent('agenda-saved',{detail:{id:view.id}}));
    }
  }
  view.wanted.forEach((item,index)=>{
    const label=node('label',undefined,'agenda-item'), checkbox=node('input');
    checkbox.type='checkbox';checkbox.checked=!!item.done;checkbox.disabled=readonly;view.checks.push(checkbox);
    checkbox.addEventListener('change',()=>{view.wanted[index].done=checkbox.checked;updateHint();save();});
    label.append(checkbox,node('span',item.text));box.append(label);
  });
  if(!view.wanted.length)box.append(node('p','还没有清单，随时可以一起讨论。','muted small'));
  else {box.append(hint);updateHint();}
}
async function lifecycle(action){const id=current.id;await post(`/api/sessions/${id}/${action}`,{request_id:uid(),version:current.version});await openSession(id);await refresh();}
function moreActions(items){
  const wrap=node('div',undefined,'card-overflow');
  const trigger=button('⋯',()=>{},'overflow-trigger');trigger.setAttribute('aria-label','更多操作');trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-expanded','false');
  const list=node('div',undefined,'floating-actions');list.id='actions-'+uid();list.setAttribute('popover','auto');list.setAttribute('role','menu');trigger.setAttribute('aria-controls',list.id);
  for(const item of items){
    item.setAttribute('role','menuitem');
    const label=item.textContent;
    const paths=/删除/.test(label)?'M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6':/恢复|开启/.test(label)?'M4 9a8 8 0 1 1 0 7M4 4v5h5':/调整|改名/.test(label)?'m4 16 12-12 4 4L8 20H4v-4M13 7l4 4':'M3 5h18v4H3zM5 9v12h14V9M9 13h6';
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('class','icon');icon.setAttribute('aria-hidden','true');
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',paths);icon.append(path);item.prepend(icon);
    item.addEventListener('click',()=>list.hidePopover());list.append(item);
  }
  const place=()=>{const a=trigger.getBoundingClientRect(),r=list.getBoundingClientRect();const dialog=trigger.closest('dialog')?.getBoundingClientRect();const minX=Math.max(8,(dialog?.left??0)+8),maxX=Math.min(innerWidth-8,(dialog?.right??innerWidth)-8);const minY=Math.max(8,(dialog?.top??0)+8),maxY=Math.min(innerHeight-8,(dialog?.bottom??innerHeight)-8);const left=Math.max(minX,Math.min(a.right-r.width,maxX-r.width));const top=a.bottom+r.height+6<=maxY?a.bottom+6:Math.max(minY,a.top-r.height-6);list.style.left=left+'px';list.style.top=top+'px';};
  trigger.addEventListener('click',()=>{list.togglePopover();if(list.matches(':popover-open'))place();});
  list.addEventListener('toggle',()=>trigger.setAttribute('aria-expanded',String(list.matches(':popover-open'))));
  trigger.addEventListener('keydown',e=>{if(e.key==='ArrowDown'){e.preventDefault();if(!list.matches(':popover-open'))list.showPopover();place();items[0]?.focus();}});
  list.addEventListener('keydown',e=>{const i=items.indexOf(document.activeElement);if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();items[e.key==='Home'?0:e.key==='End'?items.length-1:(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();}if(e.key==='Escape'){list.hidePopover();trigger.focus();}});
  wrap.append(trigger,list);return wrap;
}

async function arrangementAction(s,action){await post(`/api/sessions/${s.id}/${action}`,{request_id:uid(),version:s.version});$('task-dialog').close();if(action==='complete'||action==='archive')await goHome();else await openSession(s.id);}
const KIND_HELP={'':'MMM 会根据名称判断是任务还是安排。',task:'任务可以继续和 MMM 讨论、拆成小步。',arrangement:'安排只记在日历上，比如约会、会议。'};
function updateEditorType(){const kind=$('activity-type').value,arrangement=kind==='arrangement';for(const r of document.getElementsByName('activity-kind'))r.checked=r.value===kind;$('kind-help').textContent=KIND_HELP[kind]||'';$('task-work-fields').hidden=arrangement;$('task-agenda-fields').hidden=arrangement;$('task-date-help').textContent=arrangement?'安排保留在原日期，不会自动顺延。':'设了截止日期会提前提醒；没完成的任务会自动顺延。';}
function fillEditor(s){
  $('activity-type').value=s?.activity_type||'';
  const readonly=!!s?.archived_at||s?.status==='completed';
  for(const field of $('task-form').querySelectorAll('input,textarea,select'))field.disabled=readonly;
  const save=$('task-form').querySelector('[type=submit]');save.hidden=readonly;save.className=s?.activity_type==='arrangement'?'secondary':'primary';
  $('arrangement-actions').replaceChildren();
  if(s?.activity_type==='arrangement'){
    if(!s.archived_at)$('arrangement-actions').append(button(s.status==='completed'?'重新开启':'完成安排',()=>arrangementAction(s,s.status==='completed'?'reopen':'complete'),'primary'));
    else $('arrangement-actions').append(button('恢复安排',()=>arrangementAction(s,'restore'),'primary'));
    const extra=[];
    if(s.messages?.length)extra.push(button('查看原有聊天记录',()=>{$('task-dialog').close();$('history-title').textContent=s.title+' · 原有聊天（只读）';renderMessages($('history-content'),s.messages);$('history-dialog').showModal();},'quiet'));
    extra.push(s.archived_at?button('永久删除',()=>{$('task-dialog').close();openDelete(s);},'danger'):button('移入回收站',()=>arrangementAction(s,'archive'),'quiet'));
    $('arrangement-actions').append(moreActions(extra));
  }
  $('task-date').min=(!s||suggestionEditing?.proposal.kind==='create')?state.today:'';
  $('task-title').value=s?.title||'';$('task-goal').value=s?.session_goal||'';$('task-date').value=s?.planned_date||state.today;$('task-deadline').value=s?.deadline_date||'';
  $('task-agenda').value=(s?.agenda||[]).map(a=>typeof a==='string'?a:a.text).join('\n');$('task-error').textContent='';updateEditorType();
}

function openEditor(s=null,day=null){if(!s&&day&&day<state.today)return;editing=s;suggestionEditing=null;fillEditor(s);if(day)$('task-date').value=day;$('task-form-heading').textContent=s?(s.activity_type==='arrangement'?(s.archived_at?'回收站中的安排':s.status==='completed'?'已完成的安排':'安排详情'):'调整任务'):'新建';$('task-dialog').showModal();}
function openSuggestion(s){editing=null;suggestionEditing=s;const target=state.sessions.find(t=>t.id===s.proposal.target_session_id);fillEditor({...target,...s.proposal});$('task-form-heading').textContent='修改并接受建议';$('task-dialog').showModal();}
$('task-form').addEventListener('submit',async e=>{e.preventDefault();const save=e.submitter;save.disabled=true;try{const agenda=$('task-agenda').value.split('\n').map(s=>s.trim()).filter(Boolean).map(text=>({text,done:editing?.agenda.find(a=>a.text===text)?.done||false}));const data={...($('activity-type').value?{activity_type:$('activity-type').value}:{}),title:$('task-title').value.trim(),session_goal:$('task-goal').value,planned_date:$('task-date').value,deadline_date:$('task-deadline').value||null,agenda};if(suggestionEditing)await decide(suggestionEditing.id,'accept',data);else if(editing)await api('/api/sessions/'+editing.id,{method:'PATCH',body:JSON.stringify({...data,request_id:uid(),version:editing.version})});else await post('/api/sessions',{...data,request_id:uid()});$('task-dialog').close();await refresh();if(current&&editing?.id===current.id)await openSession(current.id);}catch(e){$('task-error').textContent=e.message;if(e.detail?.duplicates){$('task-error').append(document.createElement('br'));if(e.detail.suggestion_id)$('task-error').append(button('确认另建一项',async()=>{await decide(e.detail.suggestion_id,'accept');$('task-dialog').close();},'primary')); e.detail.duplicates.forEach(s=>$('task-error').append(button('查看「'+s.title+'」',async()=>{$('task-dialog').close();await openSession(s.id);},'secondary')));}}finally{save.disabled=false;}});
async function sendChat(e,session){e.preventDefault();const input=$(session?'session-input':'home-input');const form=e.currentTarget;const content=input.value.trim();if(!content||(session?sessionBusy:homeBusy))return;const sid=session?current.id:null;const request={content,request_id:uid(),...(session?{version:current.version}:{home_context_id:homeContextId})};const submit=form.querySelector('button');submit.disabled=true;if(session)sessionBusy=true;else homeBusy=true;input.value='';const target=$(session?'session-messages':'home-messages');const optimistic=node('article',undefined,'message user');optimistic.append(node('span','你','role'),node('div',content,'content'));target.append(optimistic);const pending=node('p','MMM 正在思考…','muted small');target.append(pending);target.scrollTop=target.scrollHeight;try{const result=await post(session?'/api/session-chat/'+sid:'/api/home/chat',request);if(result.error){report(new Error(result.reply));if(result.attempts?.length){const details=node('details');details.append(node('summary','查看失败原因'));for(const a of result.attempts)details.append(node('p',a.error,'small'));$('notice').append(details);}input.value=content;}if(session&&current?.id===sid)await openSession(sid);else if(!session)await loadHomeMessages();await refresh();}catch(error){input.value=content;report(error);}finally{pending.remove();if(session)sessionBusy=false;else homeBusy=false;submit.disabled=session?(!current||current.status!=='open'||!!current.archived_at):false;}}
$('home-chat-form').addEventListener('submit',e=>sendChat(e,false));$('session-chat-form').addEventListener('submit',e=>sendChat(e,true));
async function openTopic(id,mid){const t=await api('/api/topics/'+id);$('history-title').textContent=t.title+' · 原始历史（只读）';$('history-content').replaceChildren();renderMessages($('history-content'),t.messages);const detail=node('details');detail.append(node('summary','查看原始任务记录'),node('pre',JSON.stringify(t.history,null,2)));$('history-content').append(detail);if(!$('history-dialog').open)$('history-dialog').showModal();if(mid)highlight(mid);}
function highlight(id){const el=$('message-'+id);if(el){el.classList.add('highlight');el.scrollIntoView({block:'center',behavior:'smooth'});}}
async function navigate(url){const u=new URL(url,location.origin);const sid=(u.searchParams.get('session')||u.searchParams.get('arrangement')),mid=u.searchParams.get('message'),topic=u.searchParams.get('topic');if(topic){await openTopic(topic,mid);return;}if(sid){await openSession(sid,mid);return;}await goHome();if(mid){highlight(mid);if(!$('message-'+mid)){const m=await api('/api/messages/'+mid);$('history-title').textContent='原始消息 · 只读';renderMessages($('history-content'),[m]);$('history-dialog').showModal();}}}
$('home-button').onclick=()=>goHome().catch(report);$('back-home').onclick=()=>goHome().catch(report);$('create').onclick=()=>openEditor();$('refresh').onclick=()=>refresh().catch(report);document.querySelectorAll('.close').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
async function init(){const health=await api('/health');$('model-state').textContent=health.llm_enabled?'已连接 · '+([...$('agent-model').options].find(o=>health.model.toLowerCase().startsWith(o.value))?.text||health.model):'模型未配置 · 手动待办可用';if(health.import_warning)report(new Error(health.import_warning));await refresh();calendar=new TimeCalendar($('calendar'),{initialDate:state.today,now:state.today,events:async(info,success,failure)=>{try{success(await api(`/api/calendar?start=${info.startStr.slice(0,10)}&end=${info.endStr.slice(0,10)}`));}catch(e){failure(e);report(e);}},dateClick:dateStr=>{if(dateStr>=state.today)openEditor(null,dateStr);},eventClick:id=>openSession(id).catch(report),eventDrop:async(ev,date)=>{try{await api(`/api/sessions/${ev.id}`,{method:'PATCH',body:JSON.stringify({request_id:uid(),version:ev.version,planned_date:date})});await refresh();}catch(e){report(e);throw e;}}});await loadHomeMessages();const old=await api('/legacy/tasks');$('legacy-list').replaceChildren(...old.map(t=>button(t.title,async()=>{const r=await api('/legacy/tasks/'+encodeURIComponent(t.source_id));$('history-title').textContent=t.title+' · MMM 原始记录';$('history-content').replaceChildren(node('pre',JSON.stringify(r.task,null,2)));$('history-dialog').showModal();},'task-link')));await navigate(location.href);setInterval(()=>{if(!document.hidden&&!homeBusy&&!sessionBusy)refresh().catch(report);},30000);}
$('preferences-button').onclick=async()=>{try{const p=await api('/api/preferences');$('reviews-enabled').checked=p.enabled;$('review-hour').value=p.hour;$('review-combine').checked=p.combine;$('review-catchup').value=p.catch_up;$('deadline-days').value=p.deadline_days;$('preferences-error').textContent='';$('preferences-dialog').showModal();}catch(e){report(e);}};
$('preferences-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/preferences',{method:'PUT',body:JSON.stringify({enabled:$('reviews-enabled').checked,hour:Number($('review-hour').value),combine:$('review-combine').checked,catch_up:$('review-catchup').value,deadline_days:Number($('deadline-days').value)})});$('preferences-dialog').close();await refresh();}catch(e){$('preferences-error').textContent=e.message;}});
init().catch(report);

let deleting = null;
function openEmptyTrash(){deleting={all:true,ids:state.trash.map(s=>s.id)};$('delete-title').textContent='清空回收站？共 '+deleting.ids.length+' 项将被永久删除';$('delete-error').textContent='';$('delete-dialog').showModal();}
$('empty-trash').onclick=openEmptyTrash;
function openDelete(s){deleting={...s};$('delete-title').textContent='永久删除「'+s.title+'」？';$('delete-error').textContent='';$('delete-dialog').showModal();}
$('delete-confirm').onclick=async()=>{const save=$('delete-confirm');save.disabled=true;try{if(deleting.all){await post('/api/trash/delete-permanently/confirm',{request_id:uid(),session_ids:deleting.ids});$('delete-dialog').close();if(current&&deleting.ids.includes(current.id))await goHome();else{await refresh();await loadHomeMessages();}return;}await post(`/api/sessions/${deleting.id}/delete-permanently/confirm`,{request_id:uid(),version:deleting.version});$('delete-dialog').close();if(current?.id===deleting.id)await goHome();else{await refresh();await loadHomeMessages();}}catch(e){$('delete-error').textContent=e.message;}finally{save.disabled=false;}};

// The menu is a popover: it light-dismisses by itself, and navigating closes it too.
function closeMobileMenu(){const m=$('menu');if(m.matches(':popover-open'))m.hidePopover();}
$('menu').addEventListener('toggle',()=>$('menu-button').setAttribute('aria-expanded',String($('menu').matches(':popover-open'))));
$('task-search').addEventListener('input',()=>{if(state)populateTasks($('task-list'),state.sessions.filter(s=>s.status==='open'&&s.activity_type!=='arrangement'&&s.title.toLocaleLowerCase().includes($('task-search').value.trim().toLocaleLowerCase())));});

for(const r of document.getElementsByName('activity-kind'))r.addEventListener('change',()=>{$('activity-type').value=r.value;updateEditorType();});
for(const id of ['home-input','session-input']){
  const input=$(id);let composing=false;
  input.addEventListener('compositionstart',()=>{composing=true;});
  input.addEventListener('compositionend',()=>{composing=false;});
  input.addEventListener('keydown',e=>{
    if(e.key!=='Enter'||e.shiftKey||e.isComposing||composing||e.keyCode===229)return;
    e.preventDefault();const form=input.form;const submit=form.querySelector('[type=submit]');
    if(!input.disabled&&!submit.disabled&&input.value.trim())form.requestSubmit(submit);
  });
}



// Colour, as in ai_pet: ten hues and a day/night switch. theme.js applied the saved look
// before paint; the calendar and the mist watch :root and re-read the tokens themselves.
const SUN='<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',MOON='<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/></svg>';
const HUES=[{name:'玫红',hue:18,hex:'#e11d48'},{name:'粉色',hue:350,hex:'#f472b6'},{name:'橙色',hue:56,hex:'#fb923c'},{name:'黄色',hue:92,hex:'#facc15'},{name:'青柠',hue:131,hex:'#84cc16'},{name:'翡翠绿',hue:162,hex:'#10b981'},{name:'青色',hue:200,hex:'#06b6d4'},{name:'天蓝',hue:237,hex:'#0ea5e9'},{name:'蓝色',hue:260,hex:'#3b82f6'},{name:'紫罗兰',hue:293,hex:'#8b5cf6'}];
function readLook(){try{const l=JSON.parse(localStorage.getItem('mmm-look'));if(l&&typeof l.hue==='number')return l;}catch{}return{hue:260,night:document.documentElement.dataset.theme==='dark'};}
function setLook(patch){
  const look={...readLook(),...patch};try{localStorage.setItem('mmm-look',JSON.stringify(look));}catch{}
  const root=document.documentElement;
  const apply=()=>{root.style.setProperty('--hue',look.hue);root.dataset.hue=look.hue;root.dataset.theme=look.night?'dark':'light';showLook();};
  if(document.startViewTransition&&!document.hidden&&!matchMedia('(prefers-reduced-motion: reduce)').matches)document.startViewTransition(apply);else apply();
}
function showLook(){
  const look=readLook();
  $('swatches').querySelectorAll('.swatch').forEach(b=>{const on=Number(b.dataset.hue)===look.hue;b.setAttribute('aria-checked',String(on));b.tabIndex=on?0:-1;});
  $('night-toggle').setAttribute('aria-checked',String(!!look.night));$('night-toggle').firstElementChild.innerHTML=look.night?MOON:SUN;
  requestAnimationFrame(()=>{document.querySelector('meta[name=theme-color]').content=getComputedStyle(document.body).backgroundColor;});
}
$('swatches').append(...HUES.map(t=>{const b=node('button',undefined,'swatch');b.type='button';b.setAttribute('role','radio');b.dataset.hue=t.hue;b.style.setProperty('--color',t.hex);b.title=t.name;b.setAttribute('aria-label',t.name);b.addEventListener('click',()=>setLook({hue:t.hue}));return b;}));
$('night-toggle').addEventListener('click',()=>setLook({night:!readLook().night}));
// The dock: each swatch swells with the pointer's nearness (squared, so the nearest stands out) and lifts a little.
(()=>{const box=$('swatches'),maxScale=2.4,spread=70,maxLift=10;
  box.addEventListener('mousemove',e=>{for(const item of box.children){const r=item.getBoundingClientRect(),near=Math.max(0,1-Math.abs(e.clientX-(r.left+r.width/2))/spread)**2;const base=parseFloat(getComputedStyle(item).getPropertyValue('--base-scale'))||1;item.style.setProperty('--hover-scale',(base+near*(maxScale-base)).toFixed(3));item.style.setProperty('--lift',`-${(near*maxLift).toFixed(1)}px`);}});
  box.addEventListener('mouseleave',()=>{for(const item of box.children){item.style.removeProperty('--hover-scale');item.style.removeProperty('--lift');}});
})();
showLook();
// The top bar's view tabs drive the original nav buttons (kept hidden in the list menu), and settings live in their own dialog.
document.querySelectorAll('.view-tabs [data-for]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.for).click()));
$('settings-button').addEventListener('click',()=>$('settings-dialog').showModal());
for(const id of ['export-button','preferences-button'])$(id).addEventListener('click',()=>$('settings-dialog').close(),{capture:true});
// Keys, for whoever prefers them: 1 2 3 switch views, N creates, / goes to the message box. Never while typing or in a dialog.
document.addEventListener('keydown',e=>{
  if(e.defaultPrevented||e.ctrlKey||e.metaKey||e.altKey||e.isComposing||document.querySelector('dialog[open]')||document.documentElement.dataset.intro==='play')return;
  if(e.target.closest('input,textarea,select,[contenteditable]'))return;
  const view={'1':'home-button','2':'chat-button','3':'memory-button'}[e.key];
  if(view){e.preventDefault();$(view).click();}
  else if(e.key==='n'||e.key==='N'){e.preventDefault();openEditor();}
  else if(e.key==='/'){const box=[...document.querySelectorAll('main textarea')].find(t=>t.offsetParent);if(box){e.preventDefault();box.focus();}}
});
// the swatches are one radio group: Tab reaches the chosen one, arrows move between colours
$('swatches').addEventListener('keydown',e=>{const list=[...$('swatches').children],i=list.indexOf(document.activeElement);if(i<0)return;const step={ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1}[e.key];if(!step)return;e.preventDefault();const next=list[(i+step+list.length)%list.length];next.click();next.focus();});
