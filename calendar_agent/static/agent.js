'use strict';
(() => {
  const ic = d => '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="' + d + '"/></svg>';
  const main = document.querySelector('main');
  // The run's plumbing (raw events, token counts) shows only with ?debug; everyone else reads plain words.
  const debug = /[?&]debug\b/.test(location.search);
  const statusText = (status, error) => ({
    failed: '这次没能完成' + (error ? '：' + error : ''), cancelled: '已停止',
    waiting_for_user: '等你补充一点信息'})[status] || '';
  const KIND = {profile: '画像', preference: '偏好', episodic: '经历', task: '任务', procedural: '做事方式'};
  const TOOL = {read_calendar: '查看日历', create_task: '新建任务', update_task: '调整任务', complete_task: '完成任务',
    archive_task: '移到回收站', restore_task: '从回收站恢复', get_weather: '查询天气',
    create_adjustment_suggestion: '提出调整建议', search_memory: '查找记忆'};
  const traceEntry = (type, data) => {
    if (debug) return node('pre', type + '\n' + JSON.stringify(data, null, 2));
    if (type === 'memory.retrieved') {
      const items = ['User Block', 'Task Block', 'Agent Block'].flatMap(k => data[k] || []);
      const box = node('div', ''); box.className = 'trace-step';
      box.append(node('strong', items.length ? `参考了 ${items.length} 条记忆` : '没有找到相关记忆'));
      if (items.length) { const list = node('ul', ''); list.append(...items.slice(0, 4).map(m => node('li', `${KIND[m.kind] || '记忆'} · ${m.content}`))); box.append(list); }
      return box;
    }
    if (type === 'tool.call.started') { const p = node('p', ''); p.className = 'trace-step'; p.append(node('strong', TOOL[data.tool] || '使用工具')); return p; }
    if (type === 'tool.call.completed' && data.status !== 'completed') { const p = node('p', '上一步没有成功，已跳过'); p.className = 'trace-step muted'; return p; }
    return null;
  };
  const chatNav = $('chat-button'), memoryNav = $('memory-button');
  chatNav.onclick = () => show('chat').catch(report);
  memoryNav.onclick = () => show('memory').catch(report);
  const agentView = node('section'); agentView.id = 'agent-view';
  const tip = (title, d, label, id) => `<details name="agent-tools"><summary title="${title}" aria-label="${label}">${ic(d)}</summary><div class="agent-toolbar-content"><div id="${id}" class="agent-trace"></div></div></details>`;
  agentView.innerHTML = `<div class="agent-head"><h2>安排助手</h2><div class="agent-toolbar" aria-label="对话过程与记录">${tip('决策摘要', 'M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9V16h7v-2.1A6 6 0 0 0 12 3z', '决策摘要', 'agent-thinking')}${tip('工具与依据', 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z', '工具与依据', 'agent-trace')}</div></div>
    <p id="agent-status" role="status"></p><div id="agent-answer" class="agent-answer" aria-live="polite"></div><div class="agent-starters" id="agent-starters" role="group" aria-label="可以这样开头"></div>
    <form id="agent-form" class="composer"><label class="sr-only" for="agent-input">你的目标或补充信息</label><textarea id="agent-input" rows="2" maxlength="8000" required aria-describedby="chat-keyboard-hint" placeholder="写下要做的事、想法，或此刻的心情…"></textarea>
    <div class="composer-footer"><div class="actions"><button class="quiet" type="button" id="agent-new">新对话</button><button class="quiet" type="button" id="agent-cancel">停止</button></div><button class="primary send" type="submit" aria-label="发送" title="Enter 发送 · Shift+Enter 换行">${ic('M12 19V5M6 11l6-6 6 6')}</button></div></form>`;
  document.addEventListener('pointerdown', event => {
    if (!event.target.closest('.agent-toolbar')) {
      agentView.querySelectorAll('.agent-toolbar details[open]').forEach(detail => detail.open = false);
    }
  });
  agentView.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      const detail = agentView.querySelector('.agent-toolbar details[open]');
      if (detail) { detail.open = false; detail.querySelector('summary').focus(); }
    }
  });
  const homeChat = $('home-chat-form').closest('.chat-panel');
  // Keep legacy elements available to existing read-only history hooks.
  for (const child of homeChat.children) child.hidden = true;
  homeChat.append(agentView);
  const chatPage = node('section'); chatPage.id = 'chat-view'; chatPage.hidden = true;
  chatPage.setAttribute('aria-label', '聊天'); main.append(chatPage);
  // every chat is kept on the server; on wide screens they stay listed beside the conversation, newest first
  const chatSidebar = node('aside', undefined, 'chat-sidebar'); chatSidebar.setAttribute('aria-label', '聊天记录');
  chatSidebar.innerHTML = `<div class="chat-sidebar-head"><h2>聊天记录</h2><span class="count" id="chat-history-count"></span>
      <button type="button" id="chat-sidebar-toggle" class="ghost-button chat-rail-toggle" aria-expanded="true" aria-label="收起聊天记录" title="收起聊天记录"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></svg></button></div>
    <button type="button" id="chat-sidebar-new" class="chat-new" title="新对话"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg><span>新对话</span></button>
    <label class="chat-search"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg><input type="search" id="chat-search" autocomplete="off" placeholder="找一段对话" aria-label="按标题搜索聊天记录"></label>
    <div id="chat-history" class="chat-history"></div>
    <p class="chat-sidebar-foot" id="chat-sidebar-foot"></p>`;
  // it hangs off the window's left edge, full height, outside the page (a transformed ancestor would trap it)
  document.body.append(chatSidebar);
  const railOpen = node('button', undefined, 'ghost-button chat-rail-open'); railOpen.type = 'button'; railOpen.id = 'chat-rail-open';
  railOpen.setAttribute('aria-label', '展开聊天记录'); railOpen.title = '展开聊天记录';
  railOpen.innerHTML = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></svg>';
  $('menu-button').after(railOpen);
  let activeMode = 'planner', workspaceNavigation = 0;
  const modeSessions = {};
  const storageKey = suffix => 'mmm-' + activeMode + '-' + suffix;
  const following = new Map();
  function chatMotion(container, before) {
    const dots=node('span',undefined,'chat-working');dots.hidden=true;dots.setAttribute('role','status');dots.setAttribute('aria-label','正在思考，准备回答');
    for(let i=0;i<3;i++){const dot=node('i');dot.setAttribute('aria-hidden','true');dots.append(dot);}
    before.before(dots);following.set(container,true);
    container.addEventListener('wheel',event=>{if(event.deltaY<0)following.set(container,false);},{passive:true});
    container.addEventListener('scroll',()=>{if(container.scrollHeight-container.scrollTop-container.clientHeight<24)following.set(container,true);},{passive:true});
    let touchY=0;
    container.addEventListener('touchstart',event=>{touchY=event.touches[0].clientY;},{passive:true});
    container.addEventListener('touchmove',event=>{if(event.touches[0].clientY>touchY+8)following.set(container,false);},{passive:true});
    return dots;
  }
  const homeDots=chatMotion($('agent-answer'),$('agent-answer'));
  const taskDots=chatMotion($('session-messages'),$('session-chat-form'));
  window.addEventListener('wheel',event=>{if(event.deltaY<0)for(const container of following.keys())following.set(container,false);},{passive:true});
  function followAnswer(container) {
    if(!following.get(container)||container.getClientRects().length===0||container.dataset.scrollPending)return;
    container.dataset.scrollPending='true';
    requestAnimationFrame(()=>{
      delete container.dataset.scrollPending;
      if(!following.get(container))return;
      container.scrollTop=container.scrollHeight;
      container.scrollIntoView({block:'nearest',behavior:'auto'});
    });
  }
  const memoryView = node('section'); memoryView.id = 'memory-view'; memoryView.hidden = true;
  // 记忆 is a portrait of the person in five dimensions: 性格, 价值观, 目标, 热爱, 关系需求. A shape shows how much is
  // known of each; every sentence below rests on memories that are listed with it and can be forgotten there.
  memoryView.innerHTML = `<div class="memory-empty" id="memory-empty" hidden><p class="memory-empty-mark" aria-hidden="true">m.</p><h1>还不太认识你</h1><p>说说你是怎样的人、在意什么、想去往哪里，MMM 会慢慢记住。</p><button type="button" class="primary" id="memory-start-chat">和 MMM 聊几句 →</button></div>
    <div class="about" id="about">
      <header class="about-head">
        <div class="about-intro"><h1>关于你</h1><p class="about-lead">从你们的交流里，MMM 慢慢拼出你是一个怎样的人。每一句都来自它真正记得的事。</p>
          <p id="memory-portrait-status" class="about-status" role="status"></p><button id="memory-portrait-retry" class="quiet" hidden>重新整理</button></div>
        <figure class="about-mark"><svg id="about-mark" viewBox="0 0 220 220" role="img" aria-label="五个维度的形状：越往外，了解得越多"></svg></figure>
      </header>
      <ol class="dims" id="dims"></ol>
    </div>`;
  main.append(memoryView);
  const exportPanel = node('div', undefined, 'export-fields');
  exportPanel.innerHTML = `<p class="muted small">仅包含任务字段与状态。</p><div class="two-cols"><div><label for="export-scope">范围</label><select id="export-scope"><option value="week">当前周</option><option value="month">当前月</option></select></div><div><label for="export-format">格式</label><select id="export-format"><option value="csv">CSV</option><option value="ics">ICS</option><option value="pdf">PDF</option></select></div></div><div class="modal-actions"><button id="export-download" class="primary">下载</button></div>`;
  $('export-slot').append(exportPanel);
  $('export-button').onclick = () => { closeMobileMenu(); $('export-dialog').showModal(); };
  for (const id of ['preferences-button', 'refresh']) $(id).addEventListener('click', closeMobileMenu);
  const weatherPanel = node('section', undefined, 'panel weather-panel');
  weatherPanel.innerHTML = '<div class="panel-heading"><h2>天气调整建议</h2><span class="muted small">Open-Meteo</span></div><div id="weather-items"></div>';
  $('work-tools').append(weatherPanel);
  let selected = null, stream = null, cursor = 0, busy = false, homeGeneration = 0, listGeneration = 0;
  let sessionKey = sessionStorage.getItem(storageKey('session')) || 'planner:' + uid();
  sessionStorage.setItem(storageKey('session'), sessionKey);
  const hide = () => { memoryView.hidden = true; };
  const selectNav = active => {
    for (const item of [chatNav, $('home-button'), memoryNav]) {
      item.classList.toggle('active', item === active);
      item.setAttribute('aria-current', item === active ? 'page' : 'false');
      document.querySelector(`.view-tabs [data-for="${item.id}"]`)?.setAttribute('aria-current', item === active ? 'page' : 'false');
    }
  };
  const home = goHome;
  let taskStream = null, taskRun = null;
  let taskNavigation=0;
  const taskScroll=new Map();
  goHome = async (...args) => { const navigation=++workspaceNavigation;taskNavigation++;taskStream?.close(); hide(); chatPage.hidden = true; document.body.dataset.page = 'planner'; selectNav($('home-button')); await home(...args); if(navigation!==workspaceNavigation)return; $('breadcrumb').textContent='日历'; await activateMode('planner'); if(navigation!==workspaceNavigation)return; calendar?.updateSize(); await loadWeather(); await loadRuns(); };
  const open = openSession;
  openSession = async (id,mid) => {
    workspaceNavigation++;
    const navigation=++taskNavigation;
    const [session,runs]=await Promise.all([api('/api/sessions/'+id),api('/api/agent/runs?session_key='+encodeURIComponent('calendar:'+id))]);
    const messages=runs[0]?await api(`/api/agent/runs/${runs[0].id}/conversation`):[];
    if(navigation!==taskNavigation)return;
    const pageY=window.scrollY;
    if(current&&!$('session-view').hidden)taskScroll.set(current.id,$('session-messages').scrollTop);
    taskStream?.close();hide();chatPage.hidden=true;document.body.dataset.page='planner';selectNav($('home-button'));await open(id,mid,session);
    await activateMode('planner');await loadRuns();
    markConversation();
    if(current&&!$('session-view').hidden){
      following.set($('session-messages'),false);
      await loadTaskRuns(current.id,{runs,messages},false);
      if(!mid){$('session-messages').scrollTop=taskScroll.get(current.id)??$('session-messages').scrollHeight;window.scrollTo({top:pageY,behavior:'instant'});}
    }
  };
  let taskLoad=0;
  const pendingTaskSync=new Set();
  async function syncTaskDetails(id) {
    if(current?.id!==id||$('session-view').hidden)return;
    if(agendaView?.id===id&&agendaView.saving){pendingTaskSync.add(id);return;}
    const detail=await api('/api/sessions/'+id);
    if(current?.id!==id||$('session-view').hidden||detail.version<current.version)return;
    if(agendaView?.id===id&&agendaView.saving){pendingTaskSync.add(id);return;}
    const changed=JSON.stringify(current.agenda)!==JSON.stringify(detail.agenda)||current.status!==detail.status||current.archived_at!==detail.archived_at;
    current={...current,...detail};
    const readonly=current.status!=='open'||!!current.archived_at;
    if(changed)renderAgenda(current,readonly);
    else if(agendaView?.id===id)agendaView.version=current.version;
    $('session-title').textContent=current.title;
    $('session-goal').textContent=current.session_goal||'一起讨论，把下一步慢慢理清。';
    $('session-input').disabled=readonly;
    $('session-chat-form').querySelector('button').disabled=readonly||sessionBusy;
  }
  document.addEventListener('agenda-saved',event=>{const id=event.detail.id;if(pendingTaskSync.delete(id))syncTaskDetails(id).catch(report);});
  function turnMessage(role,content) {
    const article=node('article',undefined,'message '+role);
    article.append(node('span',role==='user'?'你':'MMM','role'),node('div',content,'content'));
    return article;
  }
  function positionTurn(turn,focusPage=false) {
    const box=$('session-messages');
    turn.style.minHeight=Math.max(220,box.clientHeight-12)+'px';
    if(!following.get(box))return;
    box.scrollTop+=turn.getBoundingClientRect().top-box.getBoundingClientRect().top-8;
    if(focusPage)box.scrollIntoView({block:'start',behavior:'auto'});
  }
  function followTurn(turn) {
    const box=$('session-messages');
    if(!following.get(box)||!turn.isConnected)return;
    // Keep the user's message anchored while the answer fits the reserved space.
    if(turn.scrollHeight>box.clientHeight)box.scrollTop=box.scrollHeight-box.clientHeight;
  }
  async function loadTaskRuns(id,prefetched=null,autoFollow=true) {
    const loading=++taskLoad;
    taskStream?.close();
    const runs=prefetched?prefetched.runs:await api('/api/agent/runs?session_key='+encodeURIComponent('calendar:'+id));
    const latest=runs[0]?await api('/api/agent/runs/'+runs[0].id):null;
    const messages=prefetched?prefetched.messages:latest?await api(`/api/agent/runs/${latest.id}/conversation`):[];
    if(loading!==taskLoad||current?.id!==id||$('session-view').hidden)return;
    taskRun=latest;
    const transcript=node('div',undefined,'session-agent-history');
    const turn=node('div',undefined,'session-current-turn');
    let lastUser=-1;
    messages.forEach((message,index)=>{if(message.role==='user')lastUser=index;});
    messages.forEach((message,index)=>{(index>=lastUser?turn:transcript).append(turnMessage(message.role,message.content));});
    transcript.append(turn);
    document.querySelector('.session-agent-history')?.remove();
    $('session-messages').querySelector('.empty')?.remove();
    $('session-messages').append(transcript);
    sessionBusy=!!latest&&['queued','running'].includes(latest.status);
    const readonly=current.status!=='open'||!!current.archived_at;
    $('session-chat-form').querySelector('button').disabled=readonly||sessionBusy;
    $('session-chat-status').textContent=readonly?'此会话只读；请先恢复或重新开启。':sessionBusy?'正在准备回答…':latest?.error||'在此任务内继续对话';
    if(!sessionBusy){taskDots.hidden=true;if(autoFollow){positionTurn(turn);await syncTaskDetails(id);}return;}
    const article=turnMessage('assistant',''),answer=article.querySelector('.content');
    article.insertBefore(taskDots,answer);taskDots.hidden=false;turn.append(article);
    if(autoFollow)positionTurn(turn);
    const watchedId=latest.id;
    const source=new EventSource(`/api/agent/runs/${watchedId}/events?after=${latest.event_cursor||0}`);taskStream=source;
    let cursor=latest.event_cursor||0,ending=false;
    const visible=()=>loading===taskLoad&&current?.id===id&&!$('session-view').hidden;
    function output(event,snapshot) {
      const seq=Number(event.lastEventId);if(seq<=cursor||!visible())return;cursor=seq;
      const data=JSON.parse(event.data),text=snapshot?data.text:data.delta;
      if(!text)return;
      answer.textContent=snapshot?text:answer.textContent+text;
      taskDots.hidden=false;article.append(taskDots);
      $('session-chat-status').textContent='正在回答…';followTurn(turn);
    }
    source.addEventListener('answer.delta',event=>output(event,false));
    source.addEventListener('answer.snapshot',event=>output(event,true));
    source.addEventListener('tool.call.completed',()=>{if(visible())syncTaskDetails(id).catch(report);});
    const finished=async()=>{
      if(ending||!visible())return;
      const result=await api('/api/agent/runs/'+watchedId);
      if(['queued','running'].includes(result.status)||!visible())return;
      ending=true;source.close();taskRun=result;sessionBusy=false;taskDots.hidden=true;
      if(result.answer)answer.textContent=result.answer;
      $('session-chat-form').querySelector('button').disabled=current.status!=='open'||!!current.archived_at;
      $('session-chat-status').textContent=result.error||(result.status==='waiting_for_user'?'等待你的补充':'在此任务内继续对话');
      followTurn(turn);await syncTaskDetails(id);await refresh();
    };
    for(const type of ['run.completed','run.failed','run.cancelled','run.waiting_for_user'])source.addEventListener(type,finished);
    source.onerror=async()=>{if(!visible())return;await finished();if(!ending){taskDots.hidden=false;$('session-chat-status').textContent='正在重新连接，回复仍在继续…';}};
  }
  async function show(view, restore = true) {
    if (view === 'agent') view = 'planner';
    if (view === 'planner') { await goHome(); return; }
    if (view === 'chat') {
      workspaceNavigation++;
      taskNavigation++;taskStream?.close();closeMobileMenu();hide();current=null;
      $('home-view').hidden=true;$('session-view').hidden=true;chatPage.hidden=false;
      document.body.dataset.page='chat';selectNav(chatNav);
      $('breadcrumb').textContent='聊天';history.replaceState(null,'','/?view=chat');
      await activateMode('chat');await loadRuns();return;
    }
    workspaceNavigation++;taskNavigation++;
    closeMobileMenu(); $('home-view').hidden = true; $('session-view').hidden = true;
    chatPage.hidden=true;document.body.dataset.page='memory';memoryView.hidden = false;
    markConversation();
    selectNav(memoryNav);
    $('breadcrumb').textContent = '记忆';
    history.replaceState(null, '', '/?view=' + view);
    await loadMemories();
  }
  async function activateMode(mode) {
    if (mode !== activeMode) {
      modeSessions[activeMode] = {sessionKey, runId:selected?.id, draft:$('agent-input').value};
      homeGeneration++;stream?.close();selected=null;busy=false;homeDots.hidden=true;
      activeMode=mode;
      listGeneration++;$('agent-history').replaceChildren();
      const saved=modeSessions[mode];
      sessionKey=saved?.sessionKey || sessionStorage.getItem(storageKey('session')) || mode+':'+uid();
      sessionStorage.setItem(storageKey('session'),sessionKey);
      $('agent-input').value=saved?.draft || '';
      $('agent-answer').replaceChildren();$('agent-status').textContent='';
      $('agent-thinking').replaceChildren();$('agent-trace').replaceChildren();
    }
    (mode==='chat'?chatPage:homeChat).append(agentView);
    agentView.dataset.mode=mode;
    agentView.querySelector('h2').textContent=mode==='chat'?'和 MMM 聊聊':'安排助手';
    $('agent-input').placeholder=mode==='chat'?'有什么想聊的？':'告诉我你想安排什么，或需要调整的日程…';
    $('conversations-label').textContent=mode==='chat'?'对话':'安排对话';
    // a few ways to begin, shown only while the conversation is empty; a tap fills the box, never sends
    $('agent-starters').replaceChildren(...(mode==='chat'?['今天有点累，想聊聊','帮我想个周末计划','最近想早点睡']:['明天下午读书一小时','把这周的安排理一理','周三晚上还有空吗？']).map(text=>{const b=node('button',text,'starter');b.type='button';b.onclick=()=>{const i=$('agent-input');i.value=text;i.focus();i.setSelectionRange(text.length,text.length);};return b;}));
    if (!selected) {
      const generation=homeGeneration;
      const records=await api('/api/agent/conversations');
      if(generation!==homeGeneration)return;
      const id=modeSessions[mode]?.runId || localStorage.getItem(storageKey('last-run'));
      const record=records.find(item=>item.run_id===id && (item.kind==='chat')===(mode==='chat'));
      if(record)await watch(record.run_id);
      else localStorage.removeItem(storageKey('last-run'));
    }
    markConversation();
  }
  async function loadRuns() {
    const generation = ++listGeneration;
    const mode=activeMode;
    const all = await api('/api/agent/conversations');
    const conversations = all.filter(item => (item.kind==='chat') === (mode==='chat'));
    if (generation !== listGeneration) return;
    const entryFor = item => {
      const title = item.title || '已清除的对话';
      const entry = button('', async () => {
        const opening = ++homeGeneration;
        stream?.close();
        await show(item.kind==='chat'?'chat':'planner');
        if (opening !== homeGeneration) return;
        await watch(item.run_id);
        $('agent-input').focus({preventScroll:true});
        if (innerWidth < 760) agentView.scrollIntoView({block:'start'});
      }, 'task-link conversation-link');
      entry.dataset.conversationKey = item.session_key;
      entry.title = title;
      entry.append(node('span', title), node('small', new Date(item.last_activity * 1000).toLocaleDateString('zh-CN', {month:'short', day:'numeric'}) + (['queued','running'].includes(item.status) ? ' · 正在回复' : item.status === 'waiting_for_user' ? ' · 等待补充' : item.status === 'failed' ? ' · 未完成' : '')));
      return entry;
    };
    $('agent-history').replaceChildren(...conversations.map(entryFor));
    if (!conversations.length) $('agent-history').append(node('p', '从新对话开始聊聊', 'conversation-empty'));
    if (mode === 'chat') { chatList = conversations; renderChatHistory(); }
    markConversation();
    return conversations;
  }
  // The chat sidebar reads like a diary's spine: days as calendar numerals, each conversation a stop on one thread.
  let chatList = [];
  const CN_MON = ['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月'], CN_WEEK = '日一二三四五六';
  function chatDay(t) {
    const d = new Date(t * 1000), day0 = new Date(); day0.setHours(0, 0, 0, 0);
    const ago = Math.floor((day0 - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
    if (ago < 7) return {key: 'd' + d.toDateString(), num: String(d.getDate()).padStart(2, '0'), label: ago === 0 ? '今天' : ago === 1 ? '昨天' : '星期' + CN_WEEK[d.getDay()], sub: CN_MON[d.getMonth()], time: true};
    return {key: 'm' + d.getFullYear() + d.getMonth(), num: String(d.getMonth() + 1).padStart(2, '0'), label: CN_MON[d.getMonth()], sub: d.getFullYear() === new Date().getFullYear() ? '' : String(d.getFullYear()), time: false};
  }
  function chatEntry(item, withTime) {
    const d = new Date(item.last_activity * 1000), title = item.title || '已清除的对话';
    const entry = node('button', undefined, 'chat-entry'); entry.type = 'button';
    entry.dataset.conversationKey = item.session_key; entry.title = title;
    const state = ['queued','running'].includes(item.status) ? ['live', '正在回复'] : item.status === 'waiting_for_user' ? ['wait', '等你补充'] : item.status === 'failed' ? ['fail', '没能完成'] : null;
    const meta = node('span', withTime ? d.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', hour12: false}) : `${d.getMonth() + 1}月${d.getDate()}日`, 'chat-entry-meta');
    if (state) { const s = node('span', state[1], 'chat-entry-state ' + state[0]); meta.append(s); }
    entry.append(node('span', title, 'chat-entry-title'), meta);
    entry.onclick = async () => {
      if (narrow.matches) setRail(true);
      const opening = ++homeGeneration; stream?.close();
      await show('chat'); if (opening !== homeGeneration) return;
      await watch(item.run_id); $('agent-input').focus({preventScroll: true});
    };
    return entry;
  }
  function renderChatHistory() {
    const query = $('chat-search').value.trim().toLowerCase();
    const list = query ? chatList.filter(item => (item.title || '').toLowerCase().includes(query)) : chatList;
    const groups = new Map();
    for (const item of list) { const g = chatDay(item.last_activity); if (!groups.has(g.key)) groups.set(g.key, {...g, items: []}); groups.get(g.key).items.push(item); }
    const out = [...groups.values()].map(g => {
      const section = node('section', undefined, 'chat-day');
      const head = node('div', undefined, 'chat-day-head');
      const label = node('span', g.label, 'chat-day-label'); if (g.sub) label.append(node('small', g.sub));
      head.append(node('span', g.num, 'chat-day-num'), label);
      section.append(head, ...g.items.map(item => chatEntry(item, g.time)));
      return section;
    });
    if (!out.length) out.push(node('p', query ? `没有标题里含“${query}”的对话。` : '还没有聊过。说点什么，这里会一直记着。', 'chat-history-empty'));
    $('chat-history').replaceChildren(...out);
    $('chat-history-count').textContent = chatList.length || '';
    $('chat-sidebar-foot').textContent = chatList.length ? `${chatList.length} 段对话，都已保存` : '';
    markConversation();
  }
  $('chat-search').addEventListener('input', renderChatHistory);
  $('chat-search').addEventListener('keydown', e => { if (e.key === 'Escape' && e.target.value) { e.stopPropagation(); e.target.value = ''; renderChatHistory(); } });
  // Folding: on wide screens the page makes room for the sidebar and the choice is remembered;
  // on narrow ones it is a drawer over the page that starts closed and closes again after a pick.
  const narrow = matchMedia('(max-width: 900px)');
  const setRail = (folded, remember = true) => {
    document.body.classList.toggle('chat-folded', folded);
    $('chat-sidebar-toggle').setAttribute('aria-expanded', String(!folded));
    railOpen.setAttribute('aria-expanded', String(!folded));
    if (remember && !narrow.matches) try { localStorage.setItem('mmm-chat-rail', folded ? '1' : '0'); } catch {}
    if (!folded && narrow.matches) $('chat-search').focus({preventScroll: true});
  };
  $('chat-sidebar-toggle').onclick = () => { setRail(true); railOpen.focus(); };
  railOpen.onclick = () => setRail(false);
  chatSidebar.addEventListener('keydown', e => { if (e.key === 'Escape' && narrow.matches) setRail(true); });
  // as a drawer, a tap anywhere outside it closes it
  document.addEventListener('pointerdown', e => { if (narrow.matches && !document.body.classList.contains('chat-folded') && document.body.dataset.page === 'chat' && !chatSidebar.contains(e.target) && !railOpen.contains(e.target)) setRail(true); });
  const railDefault = () => { let saved = '0'; try { saved = localStorage.getItem('mmm-chat-rail') || '0'; } catch {} setRail(narrow.matches || saved === '1', false); };
  narrow.addEventListener('change', railDefault); railDefault();
  function markConversation() {
    document.querySelectorAll('[data-conversation-key]').forEach(entry => entry.setAttribute('aria-current', entry.dataset.conversationKey === sessionKey && (!chatPage.hidden || !$('home-view').hidden) ? 'true' : 'false'));
  }
  async function renderConversation(key, valid) {
    const transcript = await api('/api/agent/conversations/messages?session_key=' + encodeURIComponent(key));
    if (!valid()) return null;
    $('agent-answer').replaceChildren(...transcript.messages.map(message => turnMessage(message.role, message.content)));
    if (!transcript.messages.length && transcript.run_id && !busy) $('agent-answer').append(node('p', '这段对话的正文已清理，你可以继续发起新的交流。', 'muted'));
    return transcript;
  }
  async function watch(id) {
    const generation = ++homeGeneration;
    stream?.close();
    const run = await api('/api/agent/runs/' + id);
    if (generation !== homeGeneration) return;
    selected = run;
    const valid = () => generation === homeGeneration;
    busy=['queued','running'].includes(selected.status);homeDots.hidden=!busy;
    sessionKey = selected.session_key;
    sessionStorage.setItem(storageKey('session'), sessionKey);
    localStorage.setItem(storageKey('last-run'), id);
    $('agent-model').value = selected.model;
    markConversation();
    cursor = run.event_cursor || 0;
    $('agent-thinking').textContent = ''; $('agent-trace').textContent = '';
    const transcript = await renderConversation(sessionKey, valid);
    if (!valid()) return;
    if (transcript.run_id && transcript.run_id !== id) return watch(transcript.run_id);
    const messages = transcript.messages;
    const wasRunning = busy;
    let answer = null;
    if (wasRunning) {
      if (messages?.at(-1)?.role === 'assistant') $('agent-answer').lastElementChild.remove();
      // MMM's reply bubble holds the waiting dots until the first words arrive, as in ai_pet
      const turn = turnMessage('assistant', ''); turn.append(homeDots); homeDots.hidden = false; $('agent-answer').append(turn); answer = turn.querySelector('.content');
    }
    followAnswer($('agent-answer'));
    $('agent-status').textContent = debug ? selected.status + ' · 调用 ' + selected.calls + ' · Token 用量 ' + selected.tokens : statusText(selected.status, selected.error);
    const source = new EventSource(`/api/agent/runs/${id}/events?after=${cursor}`); stream = source;
    let ending = false;
    const finished = async () => {
      if (!valid() || ending) return;
      const status = await api('/api/agent/runs/' + id);
      if (!valid() || ['queued','running'].includes(status.status)) return;
      ending = true; source.close(); selected = status; busy = false; homeDots.hidden = true;
      $('agent-status').textContent = debug ? status.status + (status.error ? ' · ' + status.error : '') : statusText(status.status, status.error);
      await renderConversation(status.session_key, valid);
      if (!valid()) return;
      followAnswer($('agent-answer')); await loadRuns(); await refresh();
    };
    const types = ['run.started', 'thinking.delta', 'thinking.snapshot', 'tool.call.started', 'tool.call.arguments.delta', 'tool.call.completed', 'observation.delta', 'memory.retrieved', 'answer.delta', 'answer.snapshot', 'run.waiting_for_user', 'run.completed', 'run.failed', 'run.cancelled'];
    for (const type of types) source.addEventListener(type, async event => {
      const seq = Number(event.lastEventId); if (!valid() || seq <= cursor) return; cursor = seq;
      const data = JSON.parse(event.data);
      if (type === 'answer.delta') {if(answer) answer.textContent += data.delta;homeDots.hidden=true;followAnswer($('agent-answer'));}
      else if (type === 'answer.snapshot') {if(answer) answer.textContent = data.text;homeDots.hidden=true;followAnswer($('agent-answer'));}
      else if (type === 'thinking.snapshot') $('agent-thinking').append(node('p', data.text));
      else if (type === 'thinking.delta') $('agent-thinking').append(node('p', data.delta));
      else if (!type.startsWith('run.')) { const entry = traceEntry(type, data); if (entry) $('agent-trace').append(entry); }
      if (['run.completed', 'run.failed', 'run.cancelled', 'run.waiting_for_user'].includes(type)) {
        await finished();
      }
    });
    source.onerror = async () => {
      if (!valid()) return;
      await finished().catch(report);
      if (valid() && !ending) $('agent-status').textContent = '连接中断，正在从最后事件继续…';
    };
    await loadRuns();
  }
  $('agent-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    if (!$('agent-input').value.trim()) return;
    busy = true;
    const sendingGeneration = homeGeneration;
    following.set($('agent-answer'),true);
    // show the message and a waiting bubble at once; watch() replaces them with the live run
    const pending = node('div', undefined, 'pending-turn'), waiting = turnMessage('assistant', '');
    waiting.append(homeDots); homeDots.hidden = false;
    pending.append(turnMessage('user', $('agent-input').value.trim()), waiting);
    $('agent-answer').append(pending); followAnswer($('agent-answer'));
    try {
      const content = $('agent-input').value.trim(); if (!content) return;
      const run = ['waiting_for_user', 'failed'].includes(selected?.status)
        ? await post(`/api/agent/runs/${selected.id}/messages`, {content})
        : await post('/api/agent/runs', {content, session_key: sessionKey, request_id: uid(), model: $('agent-model').value, kind: activeMode==='chat'?'chat':'complex'});
      if (sendingGeneration !== homeGeneration) { await loadRuns(); return; }
      $('agent-input').value = ''; await watch(run.id);
    } catch (error) { pending.remove(); if (sendingGeneration === homeGeneration) { report(error); busy = false;homeDots.hidden=true; } }
  });
  $('agent-input').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault(); $('agent-form').requestSubmit();
    }
  });
  async function newConversation() {
    homeGeneration++; stream?.close(); selected = null; busy = false;homeDots.hidden=true;
    localStorage.removeItem(storageKey('last-run')); sessionKey = activeMode+':'+uid(); sessionStorage.setItem(storageKey('session'), sessionKey);
    delete modeSessions[activeMode];
    $('agent-answer').textContent = ''; $('agent-status').textContent = '';
    $('agent-thinking').textContent = ''; $('agent-trace').textContent = ''; $('agent-input').value = '';
    await show(activeMode); markConversation(); $('agent-input').focus({preventScroll:true});
    if (innerWidth < 760) agentView.scrollIntoView({block:'start'});
  }
  $('agent-new').onclick = newConversation;
  $('sidebar-new-chat').onclick = newConversation;
  $('chat-sidebar-new').onclick = newConversation;
  $('agent-cancel').onclick = async () => {
    if (!selected) return;
    const id = selected.id, generation = homeGeneration;
    try { await post(`/api/agent/runs/${id}/cancel`, {}); if (generation === homeGeneration) await watch(id); }
    catch (error) { if (generation === homeGeneration) report(error); }
  };
  // Route every visible composer through the persistent runtime.
  $('session-chat-form').addEventListener('submit', async event => {
    event.preventDefault();event.stopImmediatePropagation();
    if(!current||sessionBusy||current.status!=='open'||current.archived_at)return;
    const id=current.id, input=$('session-input'), content=input.value.trim();if(!content)return;
    sessionBusy=true;$('session-chat-form').querySelector('button').disabled=true;
    taskDots.hidden=false;following.set($('session-messages'),true);
    const optimistic=node('div',undefined,'session-current-turn session-pending-turn');
    const pendingAnswer=turnMessage('assistant','');pendingAnswer.append(taskDots);
    optimistic.append(turnMessage('user',content),pendingAnswer);
    $('session-messages').append(optimistic);input.value='';positionTurn(optimistic,true);
    try {
      await post('/api/agent/runs',{content,session_key:'calendar:'+id,request_id:uid(),model:$('agent-model').value,kind:'complex'});
      await loadTaskRuns(id);optimistic.remove();const turn=document.querySelector('.session-current-turn');if(turn)positionTurn(turn);
    }catch(error){optimistic.remove();sessionBusy=false;taskDots.hidden=true;input.value=content;report(error);if(current?.id===id)await loadTaskRuns(id);}
  },true);
  for (const [form, input] of [['home-chat-form', 'home-input']]) {
    $(form).addEventListener('submit', async event => {
      event.preventDefault(); event.stopImmediatePropagation();
      if (form === 'session-chat-form' && current) sessionKey = 'calendar:' + current.id;
      else sessionKey = sessionStorage.getItem(storageKey('session'));
      selected = null;
      $('agent-input').value = $(input).value; $(input).value = '';
      await show('agent', false); $('agent-form').requestSubmit();
    }, true);
  }
  let portraitGeneration = 0;
  $('memory-portrait-retry').onclick = () => loadPortrait(true);
  $('memory-start-chat').onclick = () => chatNav.click();
  const toMs = t => typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
  // stored memories carry their evidence ("原话为：…", "持久事实：…"); the page shows only the conclusion
  const plain = c => { let t = String(c).split(/原话为[:：]/)[0]; const f = t.match(/持久事实[:：]([^。]+)/); if (f) t = f[1]; return t.replace(/[。\s]+$/, '') || String(c); };
  // The five dimensions of a person, in the order they are read. Each has its own question, tone and a way to start
  // the conversation that would fill it in.
  const DIMS = [
    {kind: 'personality', name: '性格', ask: '你是怎样的人', tone: 'var(--accent)', start: '我想聊聊我是个怎样的人',
      glyph: '<circle cx="12" cy="9" r="4"/><path d="M5 20c1.5-3.5 4-5 7-5s5.5 1.5 7 5"/>'},
    {kind: 'values', name: '价值观', ask: '什么对你最重要', tone: 'var(--amber)', start: '我想说说什么对我最重要',
      glyph: '<path d="M12 4v16M6 8h12M4 14l2-6 2 6a2 2 0 0 1-4 0ZM16 14l2-6 2 6a2 2 0 0 1-4 0Z"/>'},
    {kind: 'goals', name: '目标', ask: '你想去往哪里', tone: 'var(--sage)', start: '聊聊我最近想实现的目标',
      glyph: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".8"/>'},
    {kind: 'passions', name: '热爱', ask: '什么让你投入其中', tone: 'oklch(68% .16 18)', start: '我想聊聊我真正热爱的事',
      glyph: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10Z"/>'},
    {kind: 'relationships', name: '关系需求', ask: '你希望被怎样陪伴', tone: 'oklch(66% .12 205)', start: '我想说说我希望被怎样陪伴',
      glyph: '<circle cx="8.5" cy="9" r="3"/><circle cx="15.5" cy="9" r="3"/><path d="M3 19c1-3 3-4.5 5.5-4.5S13 16 14 19M10 19c1-3 3-4.5 5.5-4.5S20 16 21 19"/>'},
  ];
  let memoryIndex = new Map();
  // a quiet ×; the first press asks, the second forgets
  function forgetButton(m) {
    const b = node('button', undefined, 'about-forget'); b.type = 'button'; b.setAttribute('aria-label', '忘掉：' + plain(m.content));
    b.innerHTML = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7 7 17"/></svg><span>忘掉？</span>';
    b.onclick = async e => {
      e.stopPropagation();
      if (!b.classList.contains('confirm')) { b.classList.add('confirm'); setTimeout(() => b.classList.remove('confirm'), 4000); return; }
      b.disabled = true;
      try { await api('/api/memories/' + m.id, {method: 'DELETE'}); b.closest('li')?.classList.add('leaving'); setTimeout(() => loadMemories().catch(report), 260); }
      catch (error) { b.disabled = false; report(error); }
    };
    return b;
  }
  async function talkAbout(text) {
    await show('chat');
    const box = $('agent-input'); box.value = text; box.focus(); box.setSelectionRange(text.length, text.length);
  }
  // one row per dimension: the question on the left, what MMM understands and the memories it rests on at the right
  function renderDims(sections) {
    $('dims').replaceChildren(...DIMS.map(d => {
      const s = sections?.find(x => x.kind === d.kind), known = !!s?.text;
      const li = node('li', undefined, 'trait' + (known ? '' : ' unknown')); li.id = 'dim-' + d.kind; li.style.setProperty('--tone', d.tone);
      const side = node('div', undefined, 'dim-side');
      side.innerHTML = `<span class="dim-glyph" aria-hidden="true"><svg class="icon" viewBox="0 0 24 24">${d.glyph}</svg></span>`;
      side.append(node('h2', d.name), node('p', d.ask, 'dim-ask'));
      const body = node('div', undefined, 'dim-body');
      if (!sections) body.append(node('p', '正在整理…', 'dim-text pending'));
      else if (known) {
        body.append(node('p', s.text, 'dim-text'));
        const ev = node('ul', undefined, 'dim-evidence'); ev.setAttribute('aria-label', d.name + '的依据');
        ev.append(...s.memory_ids.map(id => memoryIndex.get(id)).filter(Boolean).map(m => { const c = node('li', undefined, 'dim-chip'); c.append(node('span', plain(m.content)), forgetButton(m)); return c; }));
        body.append(ev);
      } else {
        body.append(node('p', '还不了解。聊到这些时，MMM 会记下来。', 'dim-text'));
        const go = node('button', d.start + ' →', 'starter'); go.type = 'button'; go.onclick = () => talkAbout(d.start).catch(report);
        body.append(go);
      }
      li.append(side, body);
      return li;
    }));
    drawShape(sections);
  }
  // the shape of you: five axes, each reaching as far as its evidence; unknown ones stay close to the centre
  function drawShape(sections) {
    const svg = $('about-mark'), C = 110, R = 78, ns = 'http://www.w3.org/2000/svg';
    const ang = i => (-90 + i * 72) * Math.PI / 180, at = (i, r) => [C + r * Math.cos(ang(i)), C + r * Math.sin(ang(i))];
    const reach = DIMS.map(d => { const s = sections?.find(x => x.kind === d.kind); return s?.text ? .42 + .58 * Math.min(s.memory_ids.length, 4) / 4 : .2; });
    const pts = reach.map((v, i) => at(i, R * v));
    // a smooth closed curve through the five points (Catmull-Rom as cubic Béziers)
    let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
    for (let i = 0; i < 5; i++) {
      const p0 = pts[(i + 4) % 5], p1 = pts[i], p2 = pts[(i + 1) % 5], p3 = pts[(i + 2) % 5];
      const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6], c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      d += `C${c1.map(v => v.toFixed(1))},${c2.map(v => v.toFixed(1))},${p2.map(v => v.toFixed(1))}`;
    }
    const el = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
    const guides = [.35, .7, 1].map(f => el('polygon', {points: DIMS.map((_, i) => at(i, R * f).join(',')).join(' '), class: 'guide'}));
    const axes = DIMS.map((_, i) => el('line', {x1: C, y1: C, x2: at(i, R)[0], y2: at(i, R)[1], class: 'axis'}));
    const blob = el('path', {d: d + 'Z', class: 'blob'});
    const dots = pts.map((p, i) => { const c = el('circle', {cx: p[0], cy: p[1], r: 3.4, class: 'dot'}); c.style.fill = DIMS[i].tone; return c; });
    const labels = DIMS.map((dim, i) => {
      const [x, y] = at(i, R + 20), a = el('a', {href: '#dim-' + dim.kind, class: 'label'});
      const t = el('text', {x, y, 'text-anchor': Math.abs(x - C) < 4 ? 'middle' : x > C ? 'start' : 'end', 'dominant-baseline': 'middle'}); t.textContent = dim.name;
      a.append(t); a.addEventListener('click', e => { e.preventDefault(); $('dim-' + dim.kind).scrollIntoView({behavior: reduce() ? 'auto' : 'smooth', block: 'center'}); });
      return a;
    });
    svg.replaceChildren(...guides, ...axes, blob, ...dots, ...labels);
  }
  async function loadMemories() {
    const all = await api('/api/memories');
    memoryIndex = new Map(all.map(m => [m.id, m]));
    $('memory-empty').hidden = all.length > 0; $('about').hidden = all.length === 0;
    if (!all.length) return;
    renderDims(null);
    await loadPortrait();
  }
  async function loadPortrait(retry = false) {
    const generation = ++portraitGeneration;
    $('memory-portrait-retry').hidden = true;
    $('memory-portrait-status').textContent = '';
    try {
      const started = Date.now();
      const model = $('agent-model').value;
      let result;
      while (generation === portraitGeneration && !memoryView.hidden) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        try {
          result = await api('/api/memory/overview/status', {method:'POST', signal:controller.signal, body:JSON.stringify({model,retry})});
        } finally { clearTimeout(timeout); }
        retry = false;
        if (generation !== portraitGeneration || memoryView.hidden) return;
        if (result.status === 'ready') break;
        if (result.status === 'failed') throw new Error('portrait generation failed');
        $('memory-portrait-status').textContent = result.slow ? '写得有点慢，仍在后台进行。可以先去聊天，回来再看。' : '';
        if (Date.now() - started >= 120000) {
          $('memory-portrait-status').textContent = '这次写得比较久，仍在后台进行。稍后回来就能看到。';
          $('memory-portrait-retry').textContent = '查看进度';
          $('memory-portrait-retry').hidden = false;
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 3000));
      }
      if (!result || result.status !== 'ready' || memoryView.hidden || generation !== portraitGeneration) return;
      renderDims(result.sections);
      $('memory-portrait-status').textContent = '';
    } catch (error) {
      if (generation !== portraitGeneration) return;
      $('memory-portrait-status').textContent = '暂时写不出这幅画像，记得的事都还在。可以稍后再试。';
      $('memory-portrait-retry').textContent = '再试一次';
      $('memory-portrait-retry').hidden = false;
    }
  }
  async function loadWeather() {
    const items = await api('/api/weather/suggestions'); $('weather-items').replaceChildren();
    for (const item of items) {
      const data = JSON.parse(item.summary), card = node('div', undefined, 'memory-card');
      card.append(node('p', `任务 #${item.session_id} · ${data.weather.location} · ${data.weather.date} · ${data.message}`));
      const day = node('input'); day.type = 'date'; day.setAttribute('aria-label', '调整后的计划日期'); card.append(day);
      const weatherActions = node('div', undefined, 'actions');
      weatherActions.append(button('接受改期', async () => { const run = await post(`/api/weather/suggestions/${item.id}`, {action: 'accept', planned_date: day.value, model: $('agent-model').value}); await show('agent'); await watch(run.id); }, 'primary'),
        button('忽略', async () => { await post(`/api/weather/suggestions/${item.id}`, {action: 'ignore'}); await loadWeather(); }, 'quiet'));
      card.append(weatherActions);
      $('weather-items').append(card);
    }
    if (!items.length) $('weather-items').append(node('p', '暂无需要调整的天气建议。', 'muted'));
  }
  $('export-download').onclick = async () => {
    try {
      const format = $('export-format').value;
      const response = await fetch('/api/calendar/exports', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({scope: $('export-scope').value, format})});
      if (!response.ok) throw new Error('导出失败：' + response.status);
      const url = URL.createObjectURL(await response.blob()), link = node('a'); link.href = url; link.download = 'calendar.' + format; link.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (error) { report(error); }
  };
  loadWeather().catch(report);
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (calendar && !$('home-view').hidden) {
        calendar.updateSize();
      }
    }, 120);
  });
  const originalNavigate = navigate;
  navigate = async href => {
    const view = new URL(href, location.origin).searchParams.get('view');
    if (['agent', 'chat', 'memory'].includes(view)) return show(view);
    return originalNavigate(href);
  };
})();
