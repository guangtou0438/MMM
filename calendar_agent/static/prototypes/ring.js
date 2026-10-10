'use strict';
/* 时间之环: the whole year as a ring of 365 ticks around a glowing orb.
   The focused day sits at twelve o'clock; drag, scroll or use the keys to turn the year. */
const {tasksOf, CN_MONTH, EN_MONTH, WEEK, dayOfYear} = window.Sample;
const cv = document.getElementById('ring'), ctx = cv.getContext('2d');
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const TAU = Math.PI * 2;
const today = new Date(); today.setHours(0, 0, 0, 0);

let year = today.getFullYear(), days = [], len = 365;
let focus = 0, target = 0, hover = -1, grow = reduce ? 1 : 0, introAt = performance.now();
let W = 0, H = 0, dpr = 1, C = [0, 0], R = 200;

// colour of the season, round the year: winter ice, spring green, summer gold, autumn rust
const STOPS = [[0, [143, 179, 255]], [.2, [150, 222, 200]], [.38, [176, 226, 140]], [.55, [255, 214, 122]], [.72, [255, 160, 104]], [.88, [214, 140, 170]], [1, [143, 179, 255]]];
const season = f => { let i = 0; while (f > STOPS[i + 1][0]) i++; const [a, ca] = STOPS[i], [b, cb] = STOPS[i + 1], t = (f - a) / (b - a); return ca.map((v, k) => Math.round(v + (cb[k] - v) * t)); };

function buildYear() {
  const leap = new Date(year, 1, 29).getMonth() === 1; len = leap ? 366 : 365;
  days = Array.from({length: len}, (_, i) => { const date = new Date(year, 0, i + 1); return {date, tasks: tasksOf(date)}; });
  document.getElementById('yr').textContent = year;
  stars = Array.from({length: 160}, () => [Math.random(), Math.random(), Math.random()]);
}
let stars = [];

function layout() {
  dpr = Math.min(devicePixelRatio || 1, 2); W = innerWidth; H = innerHeight;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const narrow = W < 900;
  C = [narrow ? W / 2 : W * .5, narrow ? H * .42 : H * .53];
  R = Math.min(narrow ? W * .31 : W * .26, H * (narrow ? .24 : .3));
}

/* ---------- the month wheel on the left ---------- */
const monthsEl = document.getElementById('months');
monthsEl.replaceChildren(...CN_MONTH.map((m, i) => { const li = document.createElement('li'); li.innerHTML = `${m}<small>${EN_MONTH[i].slice(0, 3).toUpperCase()}</small>`; li.onclick = () => goTo(dayOfYear(new Date(year, i, 15)) - 1); return li; }));
function syncWheel(mi) {
  monthsEl.style.transform = `translateY(${-mi * 42 - 21}px)`;
  [...monthsEl.children].forEach((li, i) => {
    const d = Math.abs(i - mi);
    li.classList.toggle('on', i === mi);
    li.style.transform = `scale(${Math.max(.62, 1.25 - d * .16)}) translateX(${d * 4}px)`;
    li.style.opacity = Math.max(.12, 1 - d * .22);
    li.style.filter = d > 1 ? `blur(${Math.min(d - 1, 3) * .6}px)` : 'none';
  });
}

/* ---------- the day panel on the right ---------- */
let shown = -1;
function syncPanel(i) {
  if (i === shown) return; shown = i;
  const m = days[i], d = m.date;
  document.getElementById('dk').textContent = `${d.getMonth() + 1} 月 ${d.getDate()} 日 · 星期${WEEK[d.getDay()]}`;
  document.getElementById('list').innerHTML = m.tasks.length
    ? `<ul>${m.tasks.map(t => `<li><time>${t.time}</time><span>${t.title}</span><em>${t.tag}</em></li>`).join('')}</ul>`
    : `<div class="empty">空白的一天。<br>年轮上这一格很短。</div>`;
  syncWheel(d.getMonth());
}

/* ---------- turning ---------- */
const wrap = i => ((Math.round(i) % len) + len) % len;
function goTo(i) { let d = i - target; d = ((d % len) + len * 1.5) % len - len / 2; target += d; }
function idxAt(x, y) {
  const a = Math.atan2(y - C[1], x - C[0]), r = Math.hypot(x - C[0], y - C[1]);
  if (r < R - 30 || r > R + 90) return -1;
  const rot = -(focus / len) * TAU, f = (((a + Math.PI / 2 - rot) / TAU) % 1 + 1) % 1;
  return wrap(f * len);
}
let drag = null;
cv.addEventListener('pointerdown', e => { cv.setPointerCapture(e.pointerId); drag = {a: Math.atan2(e.clientY - C[1], e.clientX - C[0]), t: target, moved: 0}; cv.classList.add('drag'); });
cv.addEventListener('pointermove', e => {
  if (drag) {
    const a = Math.atan2(e.clientY - C[1], e.clientX - C[0]); let da = a - drag.a; if (da > Math.PI) da -= TAU; if (da < -Math.PI) da += TAU;
    drag.moved += Math.abs(da); drag.a = a; target -= da / TAU * len; focus = target;
  } else hover = idxAt(e.clientX, e.clientY);
});
cv.addEventListener('pointerup', e => {
  cv.classList.remove('drag');
  if (drag && drag.moved < .02) { const i = idxAt(e.clientX, e.clientY); if (i >= 0) goTo(i); }
  else if (drag) target = Math.round(target);
  drag = null;
});
cv.addEventListener('pointerleave', () => { hover = -1; });
cv.addEventListener('wheel', e => { e.preventDefault(); target += Math.sign(e.deltaY) * Math.max(1, Math.min(6, Math.abs(e.deltaY) / 40)); target = Math.round(target); }, {passive: false});
addEventListener('keydown', e => {
  const i = wrap(target), d = days[i].date;
  if (e.key === 'ArrowRight') target = Math.round(target) + 1;
  else if (e.key === 'ArrowLeft') target = Math.round(target) - 1;
  else if (e.key === 'ArrowDown') goTo(dayOfYear(new Date(year, d.getMonth() + 1, Math.min(d.getDate(), 28))) - 1);
  else if (e.key === 'ArrowUp') goTo(dayOfYear(new Date(year, d.getMonth() - 1, Math.min(d.getDate(), 28))) - 1);
  else return;
  e.preventDefault();
});
document.getElementById('py').onclick = () => { year--; buildYear(); target = focus = 0; shown = -1; introAt = performance.now(); grow = reduce ? 1 : 0; };
document.getElementById('ny').onclick = () => { year++; buildYear(); target = focus = 0; shown = -1; introAt = performance.now(); grow = reduce ? 1 : 0; };
addEventListener('resize', layout);

/* ---------- drawing ---------- */
function draw(now) {
  focus += (target - focus) * (reduce ? 1 : .12);
  grow = reduce ? 1 : Math.min(1, (now - introAt) / 1800);
  const fi = wrap(focus), fd = days[fi], rot = -(focus / len) * TAU;
  syncPanel(fi);
  ctx.clearRect(0, 0, W, H);
  // night, a few stars, two faint orbits
  const bg = ctx.createRadialGradient(C[0], C[1], R * .2, C[0], C[1], Math.max(W, H) * .8);
  const sc = season(fi / len);
  bg.addColorStop(0, `rgba(${sc.join(',')},.07)`); bg.addColorStop(.3, 'rgba(10,12,20,1)'); bg.addColorStop(1, '#05060a');
  ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
  for (const [x, y, s] of stars) { ctx.fillStyle = `rgba(230,235,255,${.15 + s * .35})`; ctx.fillRect(x * W, y * H, s > .9 ? 1.6 : 1, s > .9 ? 1.6 : 1); }
  ctx.lineWidth = 1;
  for (const k of [1.42, 1.78]) { ctx.strokeStyle = 'rgba(238,240,244,.05)'; ctx.beginPath(); ctx.arc(C[0], C[1], R * k, 0, TAU); ctx.stroke(); }

  // month arcs and names inside the ring
  for (let m = 0; m < 12; m++) {
    const a0 = dayOfYear(new Date(year, m, 1)) - 1, a1 = dayOfYear(new Date(year, m + 1, 0));
    const t0 = -Math.PI / 2 + (a0 / len) * TAU + rot + .006, t1 = -Math.PI / 2 + (a1 / len) * TAU + rot - .006;
    const on = fd.date.getMonth() === m;
    ctx.strokeStyle = on ? 'rgba(238,240,244,.55)' : 'rgba(238,240,244,.14)'; ctx.lineWidth = on ? 1.6 : 1;
    ctx.beginPath(); ctx.arc(C[0], C[1], R - 16, t0, t1); ctx.stroke();
    const tm = (t0 + t1) / 2, rr = R - 40;
    ctx.fillStyle = on ? 'rgba(238,240,244,.95)' : 'rgba(168,174,187,.55)';
    ctx.font = `${on ? 15 : 12}px ${getComputedStyle(document.body).getPropertyValue('--serif')}`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(CN_MONTH[m], C[0] + Math.cos(tm) * rr, C[1] + Math.sin(tm) * rr);
  }

  // the days
  const ti = today.getFullYear() === year ? dayOfYear(today) - 1 : -1;
  for (let i = 0; i < len; i++) {
    const a = -Math.PI / 2 + (i / len) * TAU + rot, ca = Math.cos(a), sa = Math.sin(a);
    const k = days[i].tasks.length, appear = Math.max(0, Math.min(1, grow * 1.6 - i / len * .6));
    if (appear <= 0) continue;
    const near = Math.abs(((i - focus) % len + len * 1.5) % len - len / 2);
    const lift = Math.max(0, 1 - near / 4) * 10;
    const L = (6 + k * 7.5 + lift) * appear;
    const c = season(i / len), past = i < ti;
    let alpha = past ? .38 : .82; if (i === fi) alpha = 1;
    ctx.strokeStyle = i === hover ? 'rgba(255,255,255,.95)' : `rgba(${c.join(',')},${alpha})`;
    ctx.lineWidth = i === fi ? 2.4 : days[i].date.getDate() === 1 ? 1.8 : 1.2;
    ctx.beginPath(); ctx.moveTo(C[0] + ca * R, C[1] + sa * R); ctx.lineTo(C[0] + ca * (R + L), C[1] + sa * (R + L)); ctx.stroke();
    if (i === ti) {
      const pulse = reduce ? 0 : Math.sin(now / 500) * .5 + .5;
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(C[0] + ca * (R + L + 9), C[1] + sa * (R + L + 9), 3, 0, TAU); ctx.fill();
      ctx.strokeStyle = `rgba(255,255,255,${.25 + pulse * .25})`; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(C[0] + ca * (R + L + 9), C[1] + sa * (R + L + 9), 7 + pulse * 3, 0, TAU); ctx.stroke();
    }
  }
  // the pointer at twelve o'clock
  ctx.fillStyle = 'rgba(238,240,244,.9)'; ctx.beginPath(); ctx.moveTo(C[0], C[1] - R + 4); ctx.lineTo(C[0] - 5, C[1] - R + 13); ctx.lineTo(C[0] + 5, C[1] - R + 13); ctx.closePath(); ctx.fill();

  // the orb in the middle, coloured by the season of the focused day
  const orbR = R * .52;
  const og = ctx.createRadialGradient(C[0] - orbR * .3, C[1] - orbR * .35, orbR * .05, C[0], C[1], orbR);
  og.addColorStop(0, `rgba(${season(fi / len).map(v => Math.min(255, v + 60)).join(',')},.95)`);
  og.addColorStop(.55, `rgba(${sc.join(',')},.55)`); og.addColorStop(1, 'rgba(12,14,22,.0)');
  ctx.fillStyle = og; ctx.beginPath(); ctx.arc(C[0], C[1], orbR, 0, TAU); ctx.fill();
  ctx.fillStyle = 'rgba(6,7,11,.38)'; ctx.beginPath(); ctx.arc(C[0], C[1], orbR * .78, 0, TAU); ctx.fill();
  const serif = getComputedStyle(document.body).getPropertyValue('--serif');
  ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = '#fff'; ctx.font = `400 ${Math.round(orbR * .72)}px ${serif}`;
  ctx.fillText(fd.date.getDate(), C[0], C[1] + orbR * .18);
  ctx.fillStyle = 'rgba(238,240,244,.82)'; ctx.font = `13px ${serif}`;
  ctx.fillText(`${CN_MONTH[fd.date.getMonth()]} · 星期${WEEK[fd.date.getDay()]}`, C[0], C[1] + orbR * .4);
  ctx.fillStyle = 'rgba(238,240,244,.6)'; ctx.font = '12px "Segoe UI", "Microsoft YaHei UI", sans-serif';
  ctx.fillText(fd.tasks.length ? `${fd.tasks.length} 项安排` : '没有安排', C[0], C[1] + orbR * .68);

  // the hovered day, named beside the ring
  if (hover >= 0 && !drag) {
    const a = -Math.PI / 2 + (hover / len) * TAU + rot, h = days[hover];
    const x = C[0] + Math.cos(a) * (R + 100), y = C[1] + Math.sin(a) * (R + 100);
    const text = `${h.date.getMonth() + 1}月${h.date.getDate()}日 · ${h.tasks.length ? h.tasks.map(t => t.title).slice(0, 2).join('、') + (h.tasks.length > 2 ? '…' : '') : '没有安排'}`;
    ctx.font = '12px "Segoe UI", "Microsoft YaHei UI", sans-serif';
    const tw = ctx.measureText(text).width + 22;
    ctx.fillStyle = 'rgba(10,12,20,.88)'; ctx.strokeStyle = 'rgba(238,240,244,.16)';
    ctx.beginPath(); ctx.roundRect(x - tw / 2, y - 14, tw, 28, 14); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#e8ebf2'; ctx.textBaseline = 'middle'; ctx.fillText(text, x, y);
  }
  requestAnimationFrame(draw);
}

buildYear(); layout();
target = focus = today.getFullYear() === year ? dayOfYear(today) - 1 : 0;
if (!reduce) focus = target - 40;
requestAnimationFrame(draw);
