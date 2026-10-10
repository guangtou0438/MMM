'use strict';
/* 日历 · 组合原型: one calendar at three distances.
     年  the year as a ring of ticks round an orb
     月  the month as a contour map, busy days rising as hills
     日  the day as a sheet on a tear-off pad
   Zooming in goes 年 → 月 → 日, Esc steps back out. The selected date is shared,
   and one ramp (deep teal, sage, amber) means "how much is on" in every view. */
const {tasksOf, CN_MONTH, EN_MONTH, WEEK, EN_WEEK, dayOfYear} = window.Sample;
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const TAU = Math.PI * 2;
const $ = id => document.getElementById(id);
const today = new Date(); today.setHours(0, 0, 0, 0);
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const pad2 = n => String(n).padStart(2, '0');

/* ---------- the one palette ---------- */
const RAMP = [[0, [16, 40, 45]], [.25, [42, 106, 102]], [.5, [143, 176, 122]], [.75, [233, 168, 95]], [1, [251, 226, 189]]];
const ramp = t => { t = Math.max(0, Math.min(1, t)); let i = 0; while (t > RAMP[i + 1][0]) i++; const [a, ca] = RAMP[i], [b, cb] = RAMP[i + 1], k = (t - a) / (b - a); return ca.map((v, j) => Math.round(v + (cb[j] - v) * k)); };
const level = n => Math.min(1, n / 7);           // the same scale in every view: seven things is a full day
const AMBER = [233, 168, 95];
const GLSL_RAMP = `vec3 ramp(float t){ t=clamp(t,0.,1.);
  vec3 a=vec3(16.,40.,45.)/255., b=vec3(42.,106.,102.)/255., c=vec3(143.,176.,122.)/255., d=vec3(233.,168.,95.)/255., e=vec3(251.,226.,189.)/255.;
  return t<.25?mix(a,b,t/.25):t<.5?mix(b,c,(t-.25)/.25):t<.75?mix(c,d,(t-.5)/.25):mix(d,e,(t-.75)/.25); }`;
const serif = getComputedStyle(document.documentElement).getPropertyValue('--serif');
const sans = getComputedStyle(document.documentElement).getPropertyValue('--sans');

/* ---------- shared state and the header ---------- */
const state = {view: 'month', date: new Date(today)};
function flapTo(el, text) {
  const chars = [...text];
  while (el.children.length < chars.length) el.append(document.createElement('span'));
  while (el.children.length > chars.length) el.lastChild.remove();
  chars.forEach((ch, i) => {
    const s = el.children[i], digit = /\d/.test(ch);
    s.className = digit ? '' : 'dot';
    if (s.textContent === ch) return;
    if (reduce || !digit) { s.textContent = ch; return; }
    let steps = 4 + i, cur = Number(s.textContent) || 0;
    clearTimeout(s._t);
    const go = () => { if (steps-- <= 0) { s.textContent = ch; return; } cur = (cur + 1) % 10; s.textContent = cur; s.classList.remove('tick'); void s.offsetWidth; s.classList.add('tick'); s._t = setTimeout(go, 60); };
    go();
  });
}
const HINTS = {year: '拖动或滚动转动年轮 · 点中心进入这个月 · ↑ ↓ 换月', month: '点一座山打开那一天 · ← → 换月 · Esc 回到全年', day: '拖动纸页把它撕下来 · ← → 换一天 · Esc 回到这个月'};
function header() {
  const d = state.date, v = state.view, m = d.getMonth();
  flapTo($('flap'), v === 'year' ? `${d.getFullYear()}` : v === 'month' ? `${d.getFullYear()}.${pad2(m + 1)}` : `${d.getFullYear()}.${pad2(m + 1)}.${pad2(d.getDate())}`);
  $('sub').textContent = v === 'year' ? `${CN_MONTH[m]} ${d.getDate()} 日` : v === 'month' ? CN_MONTH[m] : `星期${WEEK[d.getDay()]}`;
  $('hint').textContent = HINTS[v];
  document.querySelectorAll('.seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === v)));
}

/* ---------- moving between distances ---------- */
const ORDER = {year: 0, month: 1, day: 2};
const views = {};
let leaving = null, leaveUntil = 0;
function setView(v) {
  if (v === state.view) { views[v].enter(state.date); header(); return; }
  const from = state.view, deeper = ORDER[v] > ORDER[from];
  const oldEl = $(from), newEl = $(v);
  oldEl.classList.remove('active'); oldEl.classList.toggle('out', deeper);
  newEl.classList.toggle('out', !deeper);
  void newEl.offsetWidth;
  newEl.classList.remove('out'); newEl.classList.add('active');
  leaving = from; leaveUntil = performance.now() + 900;
  state.view = v; views[v].enter(state.date); header();
}
document.querySelectorAll('.seg button').forEach(b => b.onclick = () => setView(b.dataset.v));
$('today').onclick = () => { state.date = new Date(today); views[state.view].enter(state.date, true); header(); };
$('prev').onclick = () => views[state.view].step(-1);
$('next').onclick = () => views[state.view].step(1);
addEventListener('keydown', e => {
  if (e.target.closest?.('.day')) return;
  if (e.key === 'Escape') { if (state.view === 'day') setView('month'); else if (state.view === 'month') setView('year'); return; }
  if (e.key === 'Enter') { if (state.view === 'year') setView('month'); else if (state.view === 'month') setView('day'); return; }
  if (views[state.view].key?.(e)) e.preventDefault();
});

/* =====================================================================
   年: the ring
   ===================================================================== */
views.year = (() => {
  const cv = $('ringCv'), ctx = cv.getContext('2d');
  let year = -1, days = [], len = 365, focus = 0, target = 0, hover = -1, introAt = 0, W = 0, H = 0, C = [0, 0], R = 200, orbR = 100, lastIx = -1;
  const stars = Array.from({length: 150}, () => [Math.random(), Math.random(), Math.random()]);
  const monthsEl = $('months');
  monthsEl.replaceChildren(...CN_MONTH.map((m, i) => { const li = document.createElement('li'); li.innerHTML = `${m}<small>${EN_MONTH[i].slice(0, 3).toUpperCase()}</small>`; li.onclick = () => goTo(dayOfYear(new Date(year, i, 15)) - 1); return li; }));
  function build(y) {
    year = y; len = new Date(y, 1, 29).getMonth() === 1 ? 366 : 365;
    days = Array.from({length: len}, (_, i) => { const date = new Date(y, 0, i + 1); return {date, tasks: tasksOf(date)}; });
    introAt = performance.now();
  }
  function size() {
    const dpr = Math.min(devicePixelRatio || 1, 2); W = innerWidth; H = innerHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const narrow = W < 900;
    C = [W / 2, narrow ? H * .46 : H * .55]; R = Math.min(narrow ? W * .31 : W * .25, H * (narrow ? .22 : .29)); orbR = R * .52;
  }
  const wrap = i => ((Math.round(i) % len) + len) % len;
  function goTo(i) { let d = i - target; d = ((d % len) + len * 1.5) % len - len / 2; target += d; }
  function idxAt(x, y) {
    const a = Math.atan2(y - C[1], x - C[0]), r = Math.hypot(x - C[0], y - C[1]);
    if (r < R - 30 || r > R + 90) return -1;
    const rot = -(focus / len) * TAU, f = (((a + Math.PI / 2 - rot) / TAU) % 1 + 1) % 1;
    return wrap(f * len);
  }
  function syncSide(i) {
    const m = days[i], d = m.date;
    $('yk').textContent = `${d.getMonth() + 1} 月 ${d.getDate()} 日 · 星期${WEEK[d.getDay()]}`;
    $('ylist').innerHTML = m.tasks.length ? `<ul>${m.tasks.map(t => `<li><time>${t.time}</time><span>${t.title}</span><em>${t.tag}</em></li>`).join('')}</ul>` : `<div class="empty">空白的一天。</div>`;
    const mi = d.getMonth();
    monthsEl.style.transform = `translateY(${-mi * 42 - 21}px)`;
    [...monthsEl.children].forEach((li, k) => { const dd = Math.abs(k - mi); li.classList.toggle('on', k === mi); li.style.transform = `scale(${Math.max(.62, 1.25 - dd * .16)}) translateX(${dd * 4}px)`; li.style.opacity = Math.max(.12, 1 - dd * .22); li.style.filter = dd > 1 ? `blur(${Math.min(dd - 1, 3) * .6}px)` : 'none'; });
  }
  // the ring turns under the pointer; a click without a drag picks a day, a click on the orb goes into the month
  let drag = null;
  cv.addEventListener('pointerdown', e => { cv.setPointerCapture(e.pointerId); drag = {a: Math.atan2(e.clientY - C[1], e.clientX - C[0]), moved: 0}; cv.classList.add('drag'); });
  cv.addEventListener('pointermove', e => {
    if (drag) { const a = Math.atan2(e.clientY - C[1], e.clientX - C[0]); let da = a - drag.a; if (da > Math.PI) da -= TAU; if (da < -Math.PI) da += TAU; drag.moved += Math.abs(da); drag.a = a; target -= da / TAU * len; focus = target; }
    else { hover = idxAt(e.clientX, e.clientY); cv.style.cursor = Math.hypot(e.clientX - C[0], e.clientY - C[1]) < orbR ? 'zoom-in' : ''; }
  });
  cv.addEventListener('pointerup', e => {
    cv.classList.remove('drag');
    if (drag && drag.moved < .02) {
      if (Math.hypot(e.clientX - C[0], e.clientY - C[1]) < orbR) setView('month');
      else { const i = idxAt(e.clientX, e.clientY); if (i >= 0) goTo(i); }
    } else if (drag) target = Math.round(target);
    drag = null;
  });
  cv.addEventListener('pointerleave', () => { hover = -1; });
  cv.addEventListener('wheel', e => { e.preventDefault(); target = Math.round(target + Math.sign(e.deltaY) * Math.max(1, Math.min(6, Math.abs(e.deltaY) / 40))); }, {passive: false});
  $('toMonth').onclick = () => setView('month');
  function draw(now) {
    focus += (target - focus) * (reduce ? 1 : .12);
    const grow = reduce ? 1 : Math.min(1, (now - introAt) / 1600);
    const fi = wrap(focus), fd = days[fi], rot = -(focus / len) * TAU;
    if (fi !== lastIx) { lastIx = fi; syncSide(fi); if (state.view === 'year' && +state.date !== +fd.date) { state.date = fd.date; header(); } }
    ctx.clearRect(0, 0, W, H);
    const bg = ctx.createRadialGradient(C[0], C[1], R * .2, C[0], C[1], Math.max(W, H) * .8);
    bg.addColorStop(0, 'rgba(42,106,102,.08)'); bg.addColorStop(.3, '#0b0e13'); bg.addColorStop(1, '#090b0f');
    ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
    for (const [x, y, s] of stars) { ctx.fillStyle = `rgba(236,235,230,${.12 + s * .3})`; ctx.fillRect(x * W, y * H, s > .9 ? 1.6 : 1, s > .9 ? 1.6 : 1); }
    for (const k of [1.42, 1.78]) { ctx.strokeStyle = 'rgba(236,235,230,.05)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(C[0], C[1], R * k, 0, TAU); ctx.stroke(); }
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    for (let m = 0; m < 12; m++) {
      const a0 = dayOfYear(new Date(year, m, 1)) - 1, a1 = dayOfYear(new Date(year, m + 1, 0));
      const t0 = -Math.PI / 2 + (a0 / len) * TAU + rot + .006, t1 = -Math.PI / 2 + (a1 / len) * TAU + rot - .006, on = fd.date.getMonth() === m;
      ctx.strokeStyle = on ? 'rgba(236,235,230,.55)' : 'rgba(236,235,230,.14)'; ctx.lineWidth = on ? 1.6 : 1;
      ctx.beginPath(); ctx.arc(C[0], C[1], R - 16, t0, t1); ctx.stroke();
      const tm = (t0 + t1) / 2;
      ctx.fillStyle = on ? 'rgba(236,235,230,.95)' : 'rgba(179,182,185,.55)'; ctx.font = `${on ? 15 : 12}px ${serif}`;
      ctx.fillText(CN_MONTH[m], C[0] + Math.cos(tm) * (R - 40), C[1] + Math.sin(tm) * (R - 40));
    }
    const ti = today.getFullYear() === year ? dayOfYear(today) - 1 : -1;
    for (let i = 0; i < len; i++) {
      const appear = Math.max(0, Math.min(1, grow * 1.6 - i / len * .6)); if (appear <= 0) continue;
      const a = -Math.PI / 2 + (i / len) * TAU + rot, ca = Math.cos(a), sa = Math.sin(a), k = days[i].tasks.length;
      const near = Math.abs(((i - focus) % len + len * 1.5) % len - len / 2), lift = Math.max(0, 1 - near / 4) * 10;
      const L = (6 + level(k) * 52 + lift) * appear, c = i === ti ? AMBER : ramp(.18 + level(k) * .82);
      const alpha = i === fi ? 1 : i < ti ? .38 : .85;
      ctx.strokeStyle = i === hover || i === fi ? 'rgba(255,255,255,.96)' : `rgba(${c.join(',')},${alpha})`;
      ctx.lineWidth = i === fi ? 2.4 : days[i].date.getDate() === 1 ? 1.8 : 1.2;
      ctx.beginPath(); ctx.moveTo(C[0] + ca * R, C[1] + sa * R); ctx.lineTo(C[0] + ca * (R + L), C[1] + sa * (R + L)); ctx.stroke();
      if (i === ti) {
        const p = reduce ? 0 : Math.sin(now / 500) * .5 + .5, x = C[0] + ca * (R + L + 9), y = C[1] + sa * (R + L + 9);
        ctx.fillStyle = `rgb(${AMBER.join(',')})`; ctx.beginPath(); ctx.arc(x, y, 3, 0, TAU); ctx.fill();
        ctx.strokeStyle = `rgba(${AMBER.join(',')},${.3 + p * .3})`; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, 7 + p * 3, 0, TAU); ctx.stroke();
      }
    }
    ctx.fillStyle = 'rgba(236,235,230,.9)'; ctx.beginPath(); ctx.moveTo(C[0], C[1] - R + 4); ctx.lineTo(C[0] - 5, C[1] - R + 13); ctx.lineTo(C[0] + 5, C[1] - R + 13); ctx.closePath(); ctx.fill();
    // the orb: lit by how full the focused day is
    const lc = ramp(.3 + level(fd.tasks.length) * .7);
    const og = ctx.createRadialGradient(C[0] - orbR * .3, C[1] - orbR * .35, orbR * .05, C[0], C[1], orbR);
    og.addColorStop(0, `rgba(${lc.map(v => Math.min(255, v + 50)).join(',')},.92)`); og.addColorStop(.55, `rgba(${lc.join(',')},.45)`); og.addColorStop(1, 'rgba(9,11,15,0)');
    ctx.fillStyle = og; ctx.beginPath(); ctx.arc(C[0], C[1], orbR, 0, TAU); ctx.fill();
    ctx.fillStyle = 'rgba(9,11,15,.42)'; ctx.beginPath(); ctx.arc(C[0], C[1], orbR * .78, 0, TAU); ctx.fill();
    ctx.textBaseline = 'alphabetic'; ctx.fillStyle = '#fff'; ctx.font = `400 ${Math.round(orbR * .72)}px ${serif}`;
    ctx.fillText(fd.date.getDate(), C[0], C[1] + orbR * .18);
    ctx.fillStyle = 'rgba(236,235,230,.82)'; ctx.font = `13px ${serif}`; ctx.fillText(`${CN_MONTH[fd.date.getMonth()]} · 星期${WEEK[fd.date.getDay()]}`, C[0], C[1] + orbR * .4);
    ctx.fillStyle = 'rgba(236,235,230,.6)'; ctx.font = `12px ${sans}`; ctx.fillText(fd.tasks.length ? `${fd.tasks.length} 项安排` : '没有安排', C[0], C[1] + orbR * .68);
    if (hover >= 0 && !drag) {
      const a = -Math.PI / 2 + (hover / len) * TAU + rot, h = days[hover], x = C[0] + Math.cos(a) * (R + 100), y = C[1] + Math.sin(a) * (R + 100);
      const text = `${h.date.getMonth() + 1}月${h.date.getDate()}日 · ${h.tasks.length ? h.tasks.map(t => t.title).slice(0, 2).join('、') + (h.tasks.length > 2 ? '…' : '') : '没有安排'}`;
      ctx.font = `12px ${sans}`; const tw = ctx.measureText(text).width + 22;
      ctx.fillStyle = 'rgba(9,11,15,.9)'; ctx.strokeStyle = 'rgba(236,235,230,.16)'; ctx.beginPath(); ctx.roundRect(x - tw / 2, y - 14, tw, 28, 14); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#ecebe6'; ctx.textBaseline = 'middle'; ctx.fillText(text, x, y);
    }
  }
  return {
    enter(date, jump) {
      const first = year < 0;
      if (date.getFullYear() !== year) build(date.getFullYear());
      const i = dayOfYear(date) - 1;
      if (first && !reduce) { target = i; focus = i - 40; } else if (jump || Math.abs(i - target) > 0) goTo(i);
      lastIx = -1; size();
    },
    step(s) { const d = state.date; state.date = new Date(d.getFullYear() + s, d.getMonth(), Math.min(d.getDate(), 28)); build(state.date.getFullYear()); target = focus = dayOfYear(state.date) - 1; lastIx = -1; header(); },
    key(e) {
      const d = days[wrap(target)].date;
      if (e.key === 'ArrowRight') target = Math.round(target) + 1; else if (e.key === 'ArrowLeft') target = Math.round(target) - 1;
      else if (e.key === 'ArrowDown') goTo(dayOfYear(new Date(year, d.getMonth() + 1, Math.min(d.getDate(), 28))) - 1);
      else if (e.key === 'ArrowUp') goTo(dayOfYear(new Date(year, d.getMonth() - 1, Math.min(d.getDate(), 28))) - 1);
      else return false;
      return true;
    },
    size, draw,
  };
})();

/* =====================================================================
   月: the contour map
   ===================================================================== */
views.month = (() => {
  const cv = $('topoCv'), gl = cv.getContext('webgl', {antialias: true});
  gl.getExtension('OES_standard_derivatives');
  const FS = `#extension GL_OES_standard_derivatives : enable
precision highp float;
uniform vec2 uRes; uniform float uTime; uniform vec3 uDays[42]; uniform vec2 uHover; uniform vec2 uToday; uniform float uCell;
float hash(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+45.32); return fract(p.x*p.y); }
float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f); return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y); }
float fbm(vec2 p){ float a=.5,s=0.; for(int i=0;i<4;i++){ s+=a*noise(p); p=p*2.03+17.; a*=.5; } return s; }
${GLSL_RAMP}
float height(vec2 p){
  float h=0.;
  for(int i=0;i<42;i++){ vec3 d=uDays[i]; if(d.z<=0.) continue; vec2 q=(p-d.xy)/uCell; h+=d.z*exp(-dot(q,q)*2.2); }
  return h+.16*fbm(p/uCell*.9+vec2(uTime*.015,-uTime*.01))-.05;
}
void main(){
  vec2 p=gl_FragCoord.xy; p.y=uRes.y-p.y;
  float h=height(p), v=h*11., fw=fwidth(v);
  float line=1.-smoothstep(0.,fw*1.4,abs(fract(v-.5)-.5));
  float major=1.-smoothstep(0.,fw*2.2,abs(fract(v/5.-.5)-.5)*5.);
  vec3 tint=ramp(h*.95);
  vec3 col=mix(vec3(.035,.043,.059),tint*.22,smoothstep(.0,.6,h));
  col+=tint*line*(.32+.5*smoothstep(.0,.7,h))+tint*major*.35;
  float hv=length(p-uHover)/uCell; col+=tint*.22*exp(-hv*hv*3.)*(.4+line);
  float td=length(p-uToday)/uCell; col+=vec3(.91,.66,.37)*.16*exp(-td*td*4.)*(.7+.3*sin(uTime*2.));
  vec2 uv=gl_FragCoord.xy/uRes; col*=1.-.55*pow(length((uv-.5)*vec2(1.,.9)),2.2);
  col+=(hash(gl_FragCoord.xy+fract(uTime))-.5)/60.;
  gl_FragColor=vec4(col,1.);
}`;
  const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) console.error(gl.getShaderInfoLog(o)); return o; };
  const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}')); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(pr);
  const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const U = n => gl.getUniformLocation(pr, n), u = {res: U('uRes'), time: U('uTime'), days: U('uDays'), hover: U('uHover'), today: U('uToday'), cell: U('uCell')};
  const daysEl = $('days'), tip = $('tip');
  const jitter = Array.from({length: 42}, (_, i) => { const r = window.Sample.rng(i * 97 + 13); return [(r() - .5) * .34, (r() - .5) * .3]; });
  let view = null, month = [], cells = [], cell = 80, dpr = 1, amps = new Float32Array(42), target = new Float32Array(42), hoverIx = -1, hover = [-999, -999], W = 0, H = 0;
  function size() {
    dpr = Math.min(devicePixelRatio || 1, 2); W = innerWidth; H = innerHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    const narrow = W < 900, top = narrow ? 170 : 150, bottom = narrow ? 60 : 80, left = narrow ? 22 : 90, right = narrow ? 22 : 90;
    const gw = W - left - right, gh = H - top - bottom; cell = Math.min(gw / 7, gh / 6);
    const ox = left + (gw - cell * 7) / 2, oy = top + (gh - cell * 6) / 2;
    cells = Array.from({length: 42}, (_, i) => [ox + (i % 7 + .5 + jitter[i][0]) * cell, oy + (Math.floor(i / 7) + .5 + jitter[i][1]) * cell]);
    daysEl.querySelectorAll('.wk').forEach(e => { e.style.left = `${ox + (+e.dataset.wk + .5) * cell}px`; e.style.top = `${oy - 24}px`; });
    daysEl.querySelectorAll('.day').forEach(e => { const [x, y] = cells[e.dataset.i]; e.style.left = `${x}px`; e.style.top = `${y}px`; });
  }
  function build(date) {
    view = new Date(date.getFullYear(), date.getMonth(), 1);
    const lead = view.getDay(), n = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
    month = Array.from({length: 42}, (_, i) => { const d = i - lead + 1; if (d < 1 || d > n) return null; const dd = new Date(view.getFullYear(), view.getMonth(), d); return {date: dd, tasks: tasksOf(dd)}; });
    month.forEach((m, i) => { target[i] = m ? (m.tasks.length ? .12 + .88 * Math.pow(level(m.tasks.length), .85) : .06) : 0; });
    daysEl.replaceChildren(...WEEK.map((w, c) => { const e = document.createElement('div'); e.className = 'wk'; e.dataset.wk = c; e.textContent = w; return e; }), ...month.map((m, i) => {
      const e = document.createElement('div'); e.className = 'day'; e.dataset.i = i;
      if (!m) { e.classList.add('hide'); return e; }
      const d = m.date, k = m.tasks.length;
      e.classList.toggle('weekend', d.getDay() % 6 === 0); e.classList.toggle('busy', k >= 4); e.classList.toggle('today', +d === +today); e.classList.toggle('past', d < today);
      e.innerHTML = `<div class="n">${d.getDate()}</div><div class="c">${'<i></i>'.repeat(Math.min(k, 6))}</div>`;
      e.setAttribute('role', 'button'); e.tabIndex = 0; e.setAttribute('aria-label', `${d.getMonth() + 1}月${d.getDate()}日，${k} 项安排，打开这一天`);
      const open = () => { state.date = d; setView('day'); };
      e.onclick = open; e.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } };
      e.onmouseenter = () => { hoverIx = i; }; e.onmouseleave = () => { if (hoverIx === i) hoverIx = -1; };
      return e;
    }));
    const all = month.filter(Boolean), total = all.reduce((s, m) => s + m.tasks.length, 0), peak = all.reduce((a, b) => b.tasks.length > a.tasks.length ? b : a, all[0]);
    $('stats').innerHTML = `<strong>${total}</strong>本月安排<br>最高峰 ${peak.date.getMonth() + 1}月${peak.date.getDate()}日 · ${peak.tasks.length} 项`;
    size();
  }
  addEventListener('pointermove', e => {
    if (state.view !== 'month') return;
    if (e.target !== cv) { tip.style.opacity = 0; return; }
    let best = -1, bd = 1e9; cells.forEach(([x, y], i) => { if (!month[i]) return; const d = Math.hypot(x - e.clientX, y - e.clientY); if (d < bd) { bd = d; best = i; } });
    hoverIx = bd < cell * .7 ? best : -1;
    if (hoverIx >= 0) { const m = month[hoverIx]; tip.innerHTML = `<b>${m.date.getMonth() + 1}月${m.date.getDate()}日</b> · ${m.tasks.length ? m.tasks.slice(0, 3).map(t => t.title).join('、') + (m.tasks.length > 3 ? ' …' : '') : '没有安排'}`; tip.style.left = `${e.clientX}px`; tip.style.top = `${e.clientY}px`; tip.style.opacity = 1; } else tip.style.opacity = 0;
    cv.style.cursor = hoverIx >= 0 ? 'zoom-in' : '';
  });
  cv.addEventListener('click', () => { if (hoverIx >= 0) { state.date = month[hoverIx].date; setView('day'); } });
  const t0 = performance.now();
  function draw(now) {
    gl.useProgram(pr); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0); gl.viewport(0, 0, cv.width, cv.height);
    for (let i = 0; i < 42; i++) amps[i] += (target[i] - amps[i]) * (reduce ? 1 : .06);
    const hv = hoverIx >= 0 ? cells[hoverIx] : [-999, -999]; hover[0] += (hv[0] - hover[0]) * .2; hover[1] += (hv[1] - hover[1]) * .2;
    const arr = new Float32Array(126); cells.forEach(([x, y], i) => { arr[i * 3] = x * dpr; arr[i * 3 + 1] = y * dpr; arr[i * 3 + 2] = amps[i]; });
    const ti = month.findIndex(m => m && +m.date === +today), tp = ti >= 0 ? cells[ti] : [-999, -999];
    gl.uniform2f(u.res, cv.width, cv.height); gl.uniform1f(u.time, reduce ? 0 : (now - t0) / 1000); gl.uniform3fv(u.days, arr);
    gl.uniform2f(u.hover, hover[0] * dpr, hover[1] * dpr); gl.uniform2f(u.today, tp[0] * dpr, tp[1] * dpr); gl.uniform1f(u.cell, cell * dpr);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
  return {
    enter(date) { if (!view || date.getFullYear() !== view.getFullYear() || date.getMonth() !== view.getMonth()) build(date); else size(); },
    step(s) { const d = state.date; state.date = new Date(d.getFullYear(), d.getMonth() + s, Math.min(d.getDate(), 28)); build(state.date); header(); },
    key(e) { if (e.key === 'ArrowLeft') this.step(-1); else if (e.key === 'ArrowRight') this.step(1); else return false; return true; },
    size, draw,
  };
})();

/* =====================================================================
   日: the tear-off pad
   ===================================================================== */
views.day = (() => {
  const pad = $('pad');
  const TORN = 'polygon(0 3%, 4% 1%, 8% 3.4%, 12% 1.2%, 16% 3%, 20% .8%, 24% 2.8%, 28% 1%, 32% 3.2%, 36% 1.2%, 40% 2.6%, 44% .6%, 48% 3%, 52% 1.4%, 56% 3.2%, 60% .8%, 64% 2.8%, 68% 1.2%, 72% 3.4%, 76% 1%, 80% 2.6%, 84% .8%, 88% 3%, 92% 1.2%, 96% 3.2%, 100% 1.4%, 100% 100%, 0 100%)';
  let busy = false;
  function sheetFor(d) {
    const s = document.createElement('article'); s.className = 'sheet';
    const ts = tasksOf(d), dow = d.getDay(), left = Math.round((new Date(d.getFullYear(), 11, 31) - d) / 864e5), toWeekend = dow === 0 || dow === 6 ? 0 : 6 - dow;
    const lv = level(ts.length), bars = Array.from({length: 7}, (_, k) => k < ts.length ? `<i style="background:rgb(${ramp(.25 + (k / 6) * .75).join(',')})"></i>` : '<i></i>').join('');
    s.classList.toggle('weekend', dow === 0 || dow === 6);
    s.innerHTML = `<div class="meta"><span><b>${d.getFullYear()}</b>  ${CN_MONTH[d.getMonth()]} · ${EN_MONTH[d.getMonth()].toUpperCase()}</span><span>第 ${dayOfYear(d)} 天</span></div>
      <div class="num">${d.getDate()}</div>
      <div class="wd">星期${WEEK[dow]}<small>${EN_WEEK[dow]}</small><span class="load" title="这一天有多满">${bars}</span></div>
      <hr>
      ${ts.length ? `<ul>${ts.slice(0, 5).map(t => `<li><time>${t.time}</time><span>${t.title}</span><em>${t.tag}</em></li>`).join('')}${ts.length > 5 ? `<li><time></time><span>还有 ${ts.length - 5} 项…</span></li>` : ''}</ul>` : `<div class="empty">今天什么都没安排。<br>留白也是一种安排。</div>`}
      <div class="foot"><span>${toWeekend ? `距周末还有 ${toWeekend} 天` : '周末愉快'}</span><span>今年还剩 ${left} 天</span></div>`;
    s.setAttribute('aria-label', `${d.getMonth() + 1}月${d.getDate()}日 星期${WEEK[dow]}，${ts.length} 项安排`);
    s.dataset.lv = lv;
    return s;
  }
  function render() {
    pad.querySelectorAll('.sheet').forEach(s => s.remove());
    for (let k = 3; k >= 0; k--) {
      const s = sheetFor(addDays(state.date, k));
      s.style.zIndex = 10 - k; s.style.transform = `translateY(${k * 5}px) scale(${1 - k * .012}) rotate(${[0, -.6, .8, -.4][k]}deg)`;
      if (k) s.classList.add('under'); else { s.classList.add('top'); hook(s); }
      pad.append(s);
    }
    silkTarget = level(tasksOf(state.date).length);
  }
  function tear(sheet, vx = 220, vy = 420) {
    if (busy) return; busy = true;
    sheet.style.clipPath = TORN;
    const stub = document.createElement('div'); stub.className = 'stub'; pad.append(stub);
    requestAnimationFrame(() => { stub.style.opacity = 0; setTimeout(() => stub.remove(), 2000); });
    const from = getComputedStyle(sheet).transform, spin = (vx >= 0 ? 1 : -1) * (18 + Math.random() * 16);
    sheet.classList.add('dragging');
    const anim = sheet.animate([{transform: from === 'none' ? 'none' : from, opacity: 1}, {transform: `translate(${vx * 1.6}px, ${vy * 1.2 + 140}px) rotate(${spin}deg)`, opacity: 1, offset: .55}, {transform: `translate(${vx * 2.6}px, ${vy * 2 + 520}px) rotate(${spin * 1.6}deg)`, opacity: 0}], {duration: reduce ? 1 : 900, easing: 'cubic-bezier(.3, .2, .6, 1)', fill: 'forwards'});
    [...pad.querySelectorAll('.sheet.under')].reverse().forEach((s, k) => { s.style.transform = `translateY(${k * 5}px) scale(${1 - k * .012}) rotate(${[0, -.6, .8][k] ?? 0}deg)`; if (!k) s.classList.remove('under'); });
    state.date = addDays(state.date, 1); header();
    anim.onfinish = () => { render(); busy = false; };
  }
  function untear() {
    if (busy) return; busy = true;
    const s = sheetFor(addDays(state.date, -1)); s.style.zIndex = 20; s.classList.add('dragging'); pad.append(s);
    state.date = addDays(state.date, -1); header();
    s.animate([{transform: 'translate(-60px, -120vh) rotate(-14deg)'}, {transform: 'translate(0, 6px) rotate(1deg)', offset: .8}, {transform: 'none'}], {duration: reduce ? 1 : 700, easing: 'cubic-bezier(.22, .61, .36, 1)'}).onfinish = () => { render(); busy = false; };
  }
  function hook(s) {
    let st = null;
    s.addEventListener('pointerdown', e => { if (busy) return; s.setPointerCapture(e.pointerId); st = {x: e.clientX, y: e.clientY, lx: e.clientX, ly: e.clientY, lt: performance.now()}; s.classList.add('dragging'); });
    s.addEventListener('pointermove', e => {
      if (!st) return;
      const dx = e.clientX - st.x, dy = Math.max(-20, e.clientY - st.y);
      s.style.transform = `translate(${dx * .35}px, ${dy * .5}px) rotate(${dx * .05}deg) rotateX(${Math.min(dy, 200) * -.06}deg)`;
      const dt = Math.max(1, performance.now() - st.lt); st.vx = (e.clientX - st.lx) / dt; st.vy = (e.clientY - st.ly) / dt; st.lx = e.clientX; st.ly = e.clientY; st.lt = performance.now();
    });
    s.addEventListener('pointerup', e => {
      if (!st) return;
      const dx = e.clientX - st.x, dy = e.clientY - st.y, fast = Math.hypot(st.vx || 0, st.vy || 0) > .7;
      s.classList.remove('dragging');
      if (Math.hypot(dx, dy) > 130 || fast) tear(s, dx || 200, Math.max(dy, 120)); else s.style.transform = 'none';
      st = null;
    });
  }
  $('tear').onclick = () => { const s = pad.querySelector('.sheet.top'); if (s) tear(s); };
  $('back').onclick = untear;

  // behind the pad: slow silk in the same ramp, brighter and warmer the fuller the day
  const cv = $('silkCv'), gl = cv.getContext('webgl');
  const FS = `precision highp float;
uniform vec2 uRes; uniform float uTime; uniform float uLoad;
float hash(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+45.32); return fract(p.x*p.y); }
float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f); return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y); }
float fbm(vec2 p){ float a=.5,s=0.; for(int i=0;i<5;i++){ s+=a*noise(p); p=p*2.02+11.; a*=.5; } return s; }
${GLSL_RAMP}
void main(){
  vec2 uv=gl_FragCoord.xy/uRes, p=uv*vec2(uRes.x/uRes.y,1.)*1.6;
  float t=uTime*.03;
  vec2 q=vec2(fbm(p+t),fbm(p+vec2(5.2,1.3)-t));
  float f=fbm(p+2.4*q+vec2(t*.6,-t*.4));
  vec3 base=vec3(.035,.043,.059);
  vec3 silk=ramp(.15+.55*f+.3*uLoad);
  vec3 col=mix(base,silk*.55,smoothstep(.35,.85,f)*.75);
  col=mix(col,ramp(.6+.4*uLoad)*.5,smoothstep(.72,.98,f)*.4*(.4+.6*uv.y));
  col*=1.-.6*pow(length((uv-vec2(.5,.48))*vec2(1.1,1.)),1.8);
  col+=(hash(gl_FragCoord.xy+fract(uTime))-.5)/50.;
  gl_FragColor=vec4(col,1.);
}`;
  const sh = (t, src) => { const o = gl.createShader(t); gl.shaderSource(o, src); gl.compileShader(o); if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) console.error(gl.getShaderInfoLog(o)); return o; };
  const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}')); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(pr);
  const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const uRes = gl.getUniformLocation(pr, 'uRes'), uTime = gl.getUniformLocation(pr, 'uTime'), uLoad = gl.getUniformLocation(pr, 'uLoad');
  let silk = 0, silkTarget = 0, shown = null;
  const t0 = performance.now();
  function size() { const k = Math.min(devicePixelRatio || 1, 1.5) * .6; cv.width = Math.round(innerWidth * k); cv.height = Math.round(innerHeight * k); }
  function draw(now) {
    silk += (silkTarget - silk) * .04;
    gl.useProgram(pr); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0); gl.viewport(0, 0, cv.width, cv.height);
    gl.uniform2f(uRes, cv.width, cv.height); gl.uniform1f(uTime, reduce ? 0 : (now - t0) / 1000); gl.uniform1f(uLoad, silk);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
  return {
    enter(date) { size(); if (!shown || +shown !== +date || !pad.querySelector('.sheet')) { shown = new Date(date); render(); } },
    step(s) { if (s > 0) { const top = pad.querySelector('.sheet.top'); if (top) tear(top); } else untear(); },
    key(e) { if (e.key === 'ArrowRight' || e.key === ' ') this.step(1); else if (e.key === 'ArrowLeft') this.step(-1); else return false; return true; },
    size, draw,
    sync() { shown = new Date(state.date); },
  };
})();

/* ---------- one loop for whatever is on screen ---------- */
addEventListener('resize', () => views[state.view].size());
function loop(now) {
  views[state.view].draw(now);
  if (leaving && now < leaveUntil) views[leaving].draw(now); else leaving = null;
  if (state.view === 'day') views.day.sync();
  requestAnimationFrame(loop);
}
views.year.enter(state.date); views.year.size();
views.day.size();
views.month.enter(state.date);
header();
requestAnimationFrame(loop);
