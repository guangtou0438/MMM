'use strict';
/* 撕页台历: one sheet per day on a pad. Drag the top sheet away and it tears off
   along the perforation, leaving a ragged stub under the binding for a moment.
   Behind the pad, the sky takes the colours of the month. */
const {tasksOf, CN_MONTH, EN_MONTH, WEEK, EN_WEEK, dayOfYear} = window.Sample;
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const today = new Date(); today.setHours(0, 0, 0, 0);
let cur = new Date(today);

/* ---------- the sheets ---------- */
const pad = document.getElementById('pad');
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
function sheetFor(d) {
  const s = document.createElement('article'); s.className = 'sheet';
  const ts = tasksOf(d), doy = dayOfYear(d), left = (new Date(d.getFullYear(), 11, 31) - d) / 864e5;
  const dow = d.getDay(), toWeekend = dow === 0 || dow === 6 ? 0 : 6 - dow;
  s.classList.toggle('weekend', dow === 0 || dow === 6);
  s.innerHTML = `<div class="meta"><span><b>${d.getFullYear()}</b>  ${CN_MONTH[d.getMonth()]} · ${EN_MONTH[d.getMonth()].toUpperCase()}</span><span>第 ${doy} 天</span></div>
    <div class="num">${d.getDate()}</div>
    <div class="wd">星期${WEEK[dow]}<small>${EN_WEEK[dow]}</small></div>
    <hr>
    ${ts.length ? `<ul>${ts.slice(0, 5).map(t => `<li><time>${t.time}</time><span>${t.title}</span></li>`).join('')}${ts.length > 5 ? `<li><time></time><span>还有 ${ts.length - 5} 项…</span></li>` : ''}</ul>` : `<div class="empty">今天什么都没安排。<br>留白也是一种安排。</div>`}
    <div class="foot"><span>${toWeekend ? `距周末还有 ${toWeekend} 天` : '周末愉快'}</span><span>今年还剩 ${Math.round(left)} 天</span></div>`;
  s.setAttribute('aria-label', `${d.getMonth() + 1}月${d.getDate()}日 星期${WEEK[dow]}，${ts.length} 项安排`);
  return s;
}
// the pad shows today's sheet and the corners of the next three under it
function render() {
  pad.querySelectorAll('.sheet').forEach(s => s.remove());
  for (let k = 3; k >= 0; k--) {
    const s = sheetFor(addDays(cur, k));
    s.style.zIndex = 10 - k;
    s.style.transform = `translateY(${k * 5}px) scale(${1 - k * .012}) rotate(${[0, -.6, .8, -.4][k]}deg)`;
    if (k) s.classList.add('under'); else { s.classList.add('top'); hook(s); }
    pad.append(s);
  }
  document.getElementById('back').disabled = false;
  document.getElementById('season').innerHTML = `${cur.getFullYear()} · ${CN_MONTH[cur.getMonth()]}<em>${EN_MONTH[cur.getMonth()]}</em>`;
  setPalette(cur.getMonth());
}

/* ---------- tearing ---------- */
const TORN = 'polygon(0 3%, 4% 1%, 8% 3.4%, 12% 1.2%, 16% 3%, 20% .8%, 24% 2.8%, 28% 1%, 32% 3.2%, 36% 1.2%, 40% 2.6%, 44% .6%, 48% 3%, 52% 1.4%, 56% 3.2%, 60% .8%, 64% 2.8%, 68% 1.2%, 72% 3.4%, 76% 1%, 80% 2.6%, 84% .8%, 88% 3%, 92% 1.2%, 96% 3.2%, 100% 1.4%, 100% 100%, 0 100%)';
let busy = false;
function tear(sheet, vx = 220, vy = 420) {
  if (busy) return; busy = true;
  sheet.style.clipPath = TORN;
  const stub = document.createElement('div'); stub.className = 'stub'; pad.append(stub);
  requestAnimationFrame(() => { stub.style.opacity = 0; setTimeout(() => stub.remove(), 2000); });
  const from = getComputedStyle(sheet).transform;
  const spin = (vx >= 0 ? 1 : -1) * (18 + Math.random() * 16);
  const dur = reduce ? 1 : 900;
  sheet.classList.add('dragging');
  const anim = sheet.animate([
    {transform: from === 'none' ? 'none' : from, opacity: 1},
    {transform: `translate(${vx * 1.6}px, ${vy * 1.2 + 140}px) rotate(${spin}deg)`, opacity: 1, offset: .55},
    {transform: `translate(${vx * 2.6}px, ${vy * 2 + 520}px) rotate(${spin * 1.6}deg)`, opacity: 0},
  ], {duration: dur, easing: 'cubic-bezier(.3, .2, .6, 1)', fill: 'forwards'});
  // the sheets below move up into place while it falls
  const unders = [...pad.querySelectorAll('.sheet.under')].reverse();
  unders.forEach((s, k) => { s.style.transform = `translateY(${k * 5}px) scale(${1 - k * .012}) rotate(${[0, -.6, .8][k] ?? 0}deg)`; if (k === 0) s.classList.remove('under'); });
  anim.onfinish = () => { cur = addDays(cur, 1); render(); busy = false; };
}
function untear() {
  if (busy) return; busy = true;
  const s = sheetFor(addDays(cur, -1)); s.style.zIndex = 20; s.classList.add('dragging'); pad.append(s);
  const anim = s.animate([{transform: 'translate(-60px, -120vh) rotate(-14deg)'}, {transform: 'translate(0, 6px) rotate(1deg)', offset: .8}, {transform: 'none'}], {duration: reduce ? 1 : 700, easing: 'cubic-bezier(.22, .61, .36, 1)'});
  anim.onfinish = () => { cur = addDays(cur, -1); render(); busy = false; };
}
function hook(s) {
  let start = null;
  s.addEventListener('pointerdown', e => { if (busy) return; s.setPointerCapture(e.pointerId); start = {x: e.clientX, y: e.clientY, t: performance.now(), lx: e.clientX, ly: e.clientY, lt: performance.now()}; s.classList.add('dragging'); });
  s.addEventListener('pointermove', e => {
    if (!start) return;
    const dx = e.clientX - start.x, dy = Math.max(-20, e.clientY - start.y);
    // the sheet pivots on its perforation: it bends from the top, never lifts off it until torn
    s.style.transform = `translate(${dx * .35}px, ${dy * .5}px) rotate(${dx * .05}deg) rotateX(${Math.min(dy, 200) * -.06}deg)`;
    start.vx = (e.clientX - start.lx) / Math.max(1, performance.now() - start.lt); start.vy = (e.clientY - start.ly) / Math.max(1, performance.now() - start.lt);
    start.lx = e.clientX; start.ly = e.clientY; start.lt = performance.now();
  });
  s.addEventListener('pointerup', e => {
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y, fast = Math.hypot(start.vx || 0, start.vy || 0) > .7;
    s.classList.remove('dragging');
    if (Math.hypot(dx, dy) > 130 || fast) tear(s, dx || 200, Math.max(dy, 120));
    else s.style.transform = 'none';
    start = null;
  });
}
document.getElementById('tear').onclick = () => { const s = pad.querySelector('.sheet.top'); if (s) tear(s); };
document.getElementById('back').onclick = untear;
document.getElementById('today').onclick = () => { cur = new Date(today); render(); };
addEventListener('keydown', e => { if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); document.getElementById('tear').click(); } if (e.key === 'ArrowLeft') untear(); });

/* ---------- the sky behind the pad, in the month's colours ---------- */
const PAL = [['#1b2a4a', '#6c8fc7', '#e7eefb'], ['#2a1f3d', '#a26fa8', '#f3d6e4'], ['#19302a', '#5fa37a', '#e6f2d8'], ['#2f2236', '#e19ab0', '#fbe3ea'], ['#14322f', '#3fa38a', '#d9f3e3'], ['#10243a', '#3a7fb5', '#cfe8f5'],
  ['#0d2b3a', '#21a0b5', '#fff2c4'], ['#2a1a10', '#e08a3c', '#ffe2b0'], ['#261f12', '#c49a4a', '#f6e7c4'], ['#2a140e', '#c4592f', '#f4c27a'], ['#1d1a17', '#8a6a4f', '#e3d3bf'], ['#0f1726', '#3c5a8a', '#f1e6d0']];
const hex = s => [1, 3, 5].map(i => parseInt(s.slice(i, i + 2), 16) / 255);
let pal = PAL[cur.getMonth()].map(hex).flat(), palTarget = pal.slice();
function setPalette(m) { palTarget = PAL[m].map(hex).flat(); }
const cv = document.getElementById('sky'), gl = cv.getContext('webgl');
const FS = `precision highp float;
uniform vec2 uRes; uniform float uTime; uniform vec3 uA, uB, uC;
float hash(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+45.32); return fract(p.x*p.y); }
float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f); return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y); }
float fbm(vec2 p){ float a=.5,s=0.; for(int i=0;i<5;i++){ s+=a*noise(p); p=p*2.02+11.; a*=.5; } return s; }
void main(){
  vec2 uv=gl_FragCoord.xy/uRes, p=uv*vec2(uRes.x/uRes.y,1.)*1.6;
  float t=uTime*.03;
  // slow silk: noise warped by noise
  vec2 q=vec2(fbm(p+t),fbm(p+vec2(5.2,1.3)-t));
  float f=fbm(p+2.4*q+vec2(t*.6,-t*.4));
  vec3 col=mix(uA,uB,smoothstep(.25,.75,f));
  col=mix(col,uC,smoothstep(.62,.95,f)*.55*(.4+.6*uv.y));
  col=mix(col,uA*.6,smoothstep(.2,1.,length((uv-vec2(.5,.45))*vec2(1.1,1.))));
  col+=(hash(gl_FragCoord.xy+fract(uTime))-.5)/45.;
  gl_FragColor=vec4(col,1.);
}`;
const sh = (t, src) => { const o = gl.createShader(t); gl.shaderSource(o, src); gl.compileShader(o); if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) console.error(gl.getShaderInfoLog(o)); return o; };
const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}')); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(pr); gl.useProgram(pr);
gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer()); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
const U = n => gl.getUniformLocation(pr, n), uRes = U('uRes'), uTime = U('uTime'), uA = U('uA'), uB = U('uB'), uC = U('uC');
function size() { const k = Math.min(devicePixelRatio || 1, 1.5) * .6; cv.width = Math.round(innerWidth * k); cv.height = Math.round(innerHeight * k); gl.viewport(0, 0, cv.width, cv.height); }
addEventListener('resize', size); size();
const t0 = performance.now();
function frame(now) {
  for (let i = 0; i < 9; i++) pal[i] += (palTarget[i] - pal[i]) * .04;
  gl.uniform2f(uRes, cv.width, cv.height); gl.uniform1f(uTime, reduce ? 0 : (now - t0) / 1000);
  gl.uniform3f(uA, pal[0], pal[1], pal[2]); gl.uniform3f(uB, pal[3], pal[4], pal[5]); gl.uniform3f(uC, pal[6], pal[7], pal[8]);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  requestAnimationFrame(frame);
}
render(); requestAnimationFrame(frame);
