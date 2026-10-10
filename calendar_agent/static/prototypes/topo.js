'use strict';
const {rng, tasksOf, CN_MONTH, WEEK} = window.Sample;

/* ---------- split-flap ---------- */
function flapTo(el, text) {
  const chars = [...text];
  while (el.children.length < chars.length) el.append(document.createElement('span'));
  while (el.children.length > chars.length) el.lastChild.remove();
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  chars.forEach((ch, i) => {
    const s = el.children[i];
    s.className = /\d/.test(ch) ? '' : 'dot';
    if (s.textContent === ch) return;
    if (reduce || !/\d/.test(ch)) { s.textContent = ch; return; }
    let steps = 6 + i * 2, cur = Number(s.textContent) || 0;
    const go = () => {
      if (steps-- <= 0) { s.textContent = ch; return; }
      cur = (cur + 1) % 10; s.textContent = cur; s.classList.remove('tick'); void s.offsetWidth; s.classList.add('tick');
      setTimeout(go, 70);
    };
    go();
  });
}

/* ---------- the map ---------- */
const cv = document.getElementById('map');
const gl = cv.getContext('webgl', {antialias: true, premultipliedAlpha: false});
gl.getExtension('OES_standard_derivatives');
const FS = `#extension GL_OES_standard_derivatives : enable
precision highp float;
uniform vec2 uRes; uniform float uTime; uniform vec3 uDays[42]; uniform vec2 uHover; uniform vec2 uToday; uniform float uCell;
float hash(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+45.32); return fract(p.x*p.y); }
float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f); return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y); }
float fbm(vec2 p){ float a=.5,s=0.; for(int i=0;i<4;i++){ s+=a*noise(p); p=p*2.03+17.; a*=.5; } return s; }
float height(vec2 p){
  float h=0.;
  for(int i=0;i<42;i++){
    vec3 d=uDays[i];
    if(d.z<=0.) continue;
    vec2 q=(p-d.xy)/uCell;
    h+=d.z*exp(-dot(q,q)*2.2);
  }
  // the ground is never quite flat: a slow drifting swell
  h+=.16*fbm(p/uCell*.9+vec2(uTime*.015,-uTime*.01))-.05;
  return h;
}
vec3 ramp(float t){
  t=clamp(t,0.,1.);
  vec3 a=vec3(.07,.19,.23), b=vec3(.17,.44,.42), c=vec3(.61,.75,.48), d=vec3(.94,.71,.42), e=vec3(1.,.89,.72);
  return t<.25?mix(a,b,t/.25):t<.5?mix(b,c,(t-.25)/.25):t<.75?mix(c,d,(t-.5)/.25):mix(d,e,(t-.75)/.25);
}
void main(){
  vec2 p=gl_FragCoord.xy; p.y=uRes.y-p.y;
  float h=height(p);
  float levels=11.;
  float v=h*levels, fw=fwidth(v);
  float line=1.-smoothstep(0.,fw*1.4,abs(fract(v-.5)-.5));
  float major=1.-smoothstep(0.,fw*2.2,abs(fract(v/5.-.5)-.5)*5.);
  vec3 bg=vec3(.027,.043,.063);
  vec3 tint=ramp(h*.95);
  vec3 col=mix(bg,tint*.22,smoothstep(.0,.6,h));            // the hypsometric wash
  col+=tint*line*(.32+.5*smoothstep(.0,.7,h));               // contour lines take the colour of their height
  col+=tint*major*.35;
  // the day under the pointer lifts out of the dark
  float hv=length(p-uHover)/uCell;
  col+=tint*.22*exp(-hv*hv*3.)*(.4+line);
  // today glows
  float td=length(p-uToday)/uCell;
  col+=vec3(.94,.71,.42)*.16*exp(-td*td*4.)*(.7+.3*sin(uTime*2.));
  // vignette and grain
  vec2 uv=gl_FragCoord.xy/uRes;
  col*=1.-.55*pow(length((uv-.5)*vec2(1.,.9)),2.2);
  col+=(hash(gl_FragCoord.xy+fract(uTime))-.5)/60.;
  gl_FragColor=vec4(col,1.);
}`;
const VS = 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}';
const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) console.error(gl.getShaderInfoLog(o)); return o; };
const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(pr); gl.useProgram(pr);
gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer()); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
const U = n => gl.getUniformLocation(pr, n);
const uRes = U('uRes'), uTime = U('uTime'), uDays = U('uDays'), uHover = U('uHover'), uToday = U('uToday'), uCell = U('uCell');

/* ---------- layout: a 7 x 6 grid, loosened so it reads as land rather than a table ---------- */
const today = new Date(); today.setHours(0, 0, 0, 0);
let view = new Date(today.getFullYear(), today.getMonth(), 1);
let W = 0, H = 0, dpr = 1, cell = 80, cells = [], amps = new Float32Array(42), target = new Float32Array(42), sel = null, hoverIx = -1;
const daysEl = document.getElementById('days');
const jitter = Array.from({length: 42}, (_, i) => { const r = rng(i * 97 + 13); return [(r() - .5) * .34, (r() - .5) * .3]; });
function layout() {
  dpr = Math.min(devicePixelRatio || 1, 2); W = innerWidth; H = innerHeight;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); gl.viewport(0, 0, cv.width, cv.height);
  const mobile = W < 760, top = mobile ? 150 : 150, bottom = mobile ? 70 : 80, left = mobile ? 26 : 90, right = mobile ? 26 : (sel !== null ? 420 : 90);
  const gw = W - left - right, gh = H - top - bottom;
  cell = Math.min(gw / 7, gh / 6);
  const ox = left + (gw - cell * 7) / 2, oy = top + (gh - cell * 6) / 2;
  cells = Array.from({length: 42}, (_, i) => { const c = i % 7, r = Math.floor(i / 7); return [ox + (c + .5 + jitter[i][0]) * cell, oy + (r + .5 + jitter[i][1]) * cell]; });
  for (let c = 0; c < 7; c++) { const el = daysEl.querySelector(`[data-wk="${c}"]`); if (el) { el.style.left = `${ox + (c + .5) * cell}px`; el.style.top = `${oy - 26}px`; } }
  placeLabels();
}
let month = [];
function build() {
  const first = new Date(view.getFullYear(), view.getMonth(), 1), lead = first.getDay(), n = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
  month = Array.from({length: 42}, (_, i) => { const d = i - lead + 1; if (d < 1 || d > n) return null; const date = new Date(view.getFullYear(), view.getMonth(), d); return {date, tasks: tasksOf(date)}; });
  const max = Math.max(4, ...month.filter(Boolean).map(m => m.tasks.length));
  month.forEach((m, i) => { target[i] = m ? .1 + .9 * Math.pow(m.tasks.length / max, .85) * (m.tasks.length ? 1 : 0) + (m ? .06 : 0) : 0; });
  // labels
  daysEl.replaceChildren(...WEEK.map((w, c) => { const e = document.createElement('div'); e.className = 'wk'; e.dataset.wk = c; e.textContent = w; return e; }), ...month.map((m, i) => {
    const e = document.createElement('div'); e.className = 'day'; e.dataset.i = i;
    if (!m) { e.classList.add('hide'); return e; }
    const d = m.date, k = m.tasks.length;
    e.classList.toggle('weekend', d.getDay() % 6 === 0); e.classList.toggle('busy', k >= 4); e.classList.toggle('today', +d === +today); e.classList.toggle('past', d < today);
    e.innerHTML = `<div class="n">${d.getDate()}</div><div class="c">${'<i></i>'.repeat(Math.min(k, 6))}</div>`;
    e.setAttribute('role', 'button'); e.tabIndex = 0; e.setAttribute('aria-label', `${d.getMonth() + 1}月${d.getDate()}日，${k} 项安排`);
    e.onclick = () => open(i); e.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(i); } };
    e.onmouseenter = () => { hoverIx = i; }; e.onmouseleave = () => { if (hoverIx === i) hoverIx = -1; };
    return e;
  }));
  flapTo(document.getElementById('flap'), `${view.getFullYear()}.${String(view.getMonth() + 1).padStart(2, '0')}`);
  document.getElementById('cn').textContent = CN_MONTH[view.getMonth()];
  const all = month.filter(Boolean), total = all.reduce((s, m) => s + m.tasks.length, 0), peak = all.reduce((a, b) => b.tasks.length > a.tasks.length ? b : a, all[0]);
  document.getElementById('stats').innerHTML = `<strong>${total}</strong>本月安排<br>最高峰 ${peak.date.getMonth() + 1}月${peak.date.getDate()}日 · ${peak.tasks.length} 项`;
  layout();
}
function placeLabels() { daysEl.querySelectorAll('.day').forEach(e => { const [x, y] = cells[e.dataset.i]; e.style.left = `${x}px`; e.style.top = `${y}px`; }); }

/* ---------- the day sheet ---------- */
const sheet = document.getElementById('sheet'), body = document.getElementById('sheetBody');
function open(i) {
  const m = month[i]; if (!m) return;
  sel = i; daysEl.querySelectorAll('.day').forEach(e => e.classList.toggle('sel', +e.dataset.i === i));
  const d = m.date, doy = Math.round((d - new Date(d.getFullYear(), 0, 1)) / 864e5) + 1;
  body.innerHTML = `<div class="d">${d.getDate()}</div><div class="w">${CN_MONTH[d.getMonth()]} · 星期${WEEK[d.getDay()]}</div><div class="alt">今年第 ${doy} 天</div>` +
    (m.tasks.length ? `<ul>${m.tasks.map(t => `<li><time>${t.time}</time><span>${t.title}<small>${t.tag}</small></span></li>`).join('')}</ul>` : `<div class="empty">这一天是平地。<br>没有安排，留给自己。</div>`);
  sheet.classList.add('open'); layout();
}
document.getElementById('close').onclick = () => { sheet.classList.remove('open'); sel = null; daysEl.querySelectorAll('.day').forEach(e => e.classList.remove('sel')); layout(); };
document.getElementById('prev').onclick = () => { view = new Date(view.getFullYear(), view.getMonth() - 1, 1); build(); };
document.getElementById('next').onclick = () => { view = new Date(view.getFullYear(), view.getMonth() + 1, 1); build(); };
addEventListener('keydown', e => { if (e.target.closest?.('.day')) return; if (e.key === 'ArrowLeft') document.getElementById('prev').click(); if (e.key === 'ArrowRight') document.getElementById('next').click(); if (e.key === 'Escape') document.getElementById('close').click(); });
addEventListener('resize', layout);

/* ---------- hover anywhere on the land finds the nearest day ---------- */
const tip = document.getElementById('tip'); let mouse = [-999, -999];
addEventListener('pointermove', e => {
  mouse = [e.clientX, e.clientY];
  if (e.target !== cv) { tip.style.opacity = 0; return; }
  let best = -1, bd = 1e9; cells.forEach(([x, y], i) => { if (!month[i]) return; const d = Math.hypot(x - mouse[0], y - mouse[1]); if (d < bd) { bd = d; best = i; } });
  hoverIx = bd < cell * .7 ? best : -1;
  if (hoverIx >= 0) { const m = month[hoverIx]; tip.innerHTML = `<b>${m.date.getMonth() + 1}月${m.date.getDate()}日</b> · ${m.tasks.length ? m.tasks.slice(0, 3).map(t => t.title).join('、') + (m.tasks.length > 3 ? ' …' : '') : '没有安排'}`; tip.style.left = `${mouse[0]}px`; tip.style.top = `${mouse[1]}px`; tip.style.opacity = 1; } else tip.style.opacity = 0;
});
cv.addEventListener('click', () => { if (hoverIx >= 0) open(hoverIx); });

/* ---------- render: heights glide from month to month ---------- */
const start = performance.now(); let hover = [-999, -999];
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
function frame(now) {
  const t = (now - start) / 1000;
  for (let i = 0; i < 42; i++) amps[i] += (target[i] - amps[i]) * (reduce ? 1 : .06);
  const hv = hoverIx >= 0 ? cells[hoverIx] : [-999, -999];
  hover[0] += (hv[0] - hover[0]) * .2; hover[1] += (hv[1] - hover[1]) * .2;
  const arr = new Float32Array(42 * 3); cells.forEach(([x, y], i) => { arr[i * 3] = x * dpr; arr[i * 3 + 1] = y * dpr; arr[i * 3 + 2] = amps[i]; });
  const ti = month.findIndex(m => m && +m.date === +today), tp = ti >= 0 ? cells[ti] : [-999, -999];
  gl.uniform2f(uRes, cv.width, cv.height); gl.uniform1f(uTime, reduce ? 0 : t); gl.uniform3fv(uDays, arr);
  gl.uniform2f(uHover, hover[0] * dpr, hover[1] * dpr); gl.uniform2f(uToday, tp[0] * dpr, tp[1] * dpr); gl.uniform1f(uCell, cell * dpr);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  requestAnimationFrame(frame);
}
build(); requestAnimationFrame(frame);
