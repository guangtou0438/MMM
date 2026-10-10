'use strict';
/* ==========================================================================
   TimeCalendar: the home calendar, at three distances.
     年  the year as a ring of ticks round an orb
     月  the month as a contour map; the busier a day, the higher its hill
     日  the day as a sheet on a tear-off pad, with that day's items
   Zoom in 年 → 月 → 日 and back out with Esc; the selected date is shared.

   It keeps the interface the app already uses for its calendar, so app.js and
   agent.js are untouched apart from constructing this instead:
     new TimeCalendar(root, {initialDate, now, events(info, ok, fail), dateClick(dateStr),
                             eventClick(id), eventDrop})
     getDate() · setOption('now', d) · refetchEvents() · updateSize() · changeView(type, date)
   Events come from /api/calendar in FullCalendar's shape; only the date, title,
   kind, status, version and agenda are used. One ramp (deep teal → sage → amber)
   means "how much is on" in every view: four or more items is a full day.
   ========================================================================== */
(() => {
  const TAU = Math.PI * 2;
  const CN_MONTH = ['一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月'];
  const EN_MONTH = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const WEEK = ['日', '一', '二', '三', '四', '五', '六'];
  const EN_WEEK = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const FULL = 4;
  const STORE = 'mmm.timecal.view';
  const pad2 = n => String(n).padStart(2, '0');
  const key = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const parse = s => { const [y, m, d] = String(s).slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const dayOfYear = d => Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - new Date(d.getFullYear(), 0, 1)) / 864e5) + 1;
  const level = n => Math.min(1, n / FULL);
  /* The palette is the world's, read back from the .tc tokens by retheme(): card
     background, ink, and a ramp that climbs from the card through the accent to
     amber. The defaults below are only what shows before the first read. */
  const T = {bg: [15, 18, 22], ink: [236, 235, 230], ink3: [140, 146, 152], accent: [42, 106, 102], amber: [233, 168, 95], dark: true, ramp: []};
  const mixc = (a, b, k) => a.map((v, j) => Math.round(v + (b[j] - v) * k));
  const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  // On a dark card the peaks go past amber toward the ink (they glow); on a light one they stop at amber, or they would turn to mud
  function buildRamp() {
    T.ramp = T.dark ? [mixc(T.bg, T.accent, .3), mixc(T.bg, T.accent, .65), T.accent, T.amber, mixc(T.amber, T.ink, .45)]
      : [mixc(T.bg, T.accent, .25), mixc(T.bg, T.accent, .55), T.accent, mixc(T.accent, T.amber, .6), T.amber];
  }
  buildRamp();
  const ramp = t => { t = Math.max(0, Math.min(1, t)) * 4; const i = Math.min(3, Math.floor(t)); return mixc(T.ramp[i], T.ramp[i + 1], t - i); };
  const GLSL_RAMP = `uniform vec3 uR[5];
  vec3 ramp(float t){ t=clamp(t,0.,1.);
  return t<.25?mix(uR[0],uR[1],t/.25):t<.5?mix(uR[1],uR[2],(t-.25)/.25):t<.75?mix(uR[2],uR[3],(t-.5)/.25):mix(uR[3],uR[4],(t-.75)/.25); }`;
  const rampUniform = () => new Float32Array(T.ramp.flat().map(v => v / 255));
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const reduce = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ORDER = {year: 0, month: 1, day: 2};
  const HINTS = {year: '拖动或滚动转动年轮 · 点中心进入这个月', month: '点一天打开 · ← → 翻页 · + − 缩放 · Esc 回到全年', day: '拖动纸页撕下 · ← → 换一天 · Esc 回到这个月'};

  function glProgram(gl, fs) {
    const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) console.warn('timecal shader:', gl.getShaderInfoLog(o)); return o; };
    const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}')); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(pr);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    return {pr, use(w, h) { gl.useProgram(pr); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0); gl.viewport(0, 0, w, h); }, u: n => gl.getUniformLocation(pr, n)};
  }

  class TimeCalendar {
    constructor(root, o) {
      this.root = root; this.o = o;
      this.now = parse(o.now); this.date = parse(o.initialDate || o.now);
      this.cache = new Map(); this.months = new Set(); this.token = 0;
      let saved = null; try { saved = localStorage.getItem(STORE); } catch (_) {}
      this.view = ORDER[saved] !== undefined ? saved : 'month';
      root.classList.add('tc');
      root.innerHTML = `
        <div class="tc-head">
          <div class="tc-date"><div class="tc-flap" aria-live="polite"></div><div class="tc-sub"></div></div>
          <div class="tc-nav">
            <button type="button" class="tc-pill round" data-act="prev" aria-label="往前">‹</button>
            <div class="tc-seg" role="group" aria-label="视图"><button type="button" data-v="year">年</button><button type="button" data-v="month">月</button><button type="button" data-v="day">日</button></div>
            <button type="button" class="tc-pill round" data-act="next" aria-label="往后">›</button>
            <button type="button" class="tc-pill" data-act="today">今天</button>
          </div>
        </div>
        <div class="tc-stage">
          <section class="tc-view tc-year" data-view="year" aria-label="年视图">
            <canvas aria-label="一年的日历，拖动或滚动转动"></canvas>
            <aside class="tc-side" aria-live="polite"><div class="k"></div><div class="list"></div><button type="button" class="tc-pill" data-act="toMonth">打开这个月 →</button></aside>
          </section>
          <section class="tc-view tc-month" data-view="month" aria-label="月视图">
            <canvas aria-hidden="true"></canvas><div class="tc-days"></div><div class="tc-flat" hidden>这个月还是平地 · 点任意一天去添加</div><div class="tc-tip"></div>
          </section>
          <section class="tc-view tc-dayv" data-view="day" aria-label="日视图">
            <canvas aria-hidden="true"></canvas><div class="tc-pad"><div class="tc-binding"></div></div>
          </section>
        </div>
        <div class="tc-foot"><span class="tc-ramp">空<i></i>满</span>
          <div class="tc-modes" role="group" aria-label="月视图的显示">
            <div class="tc-seg mini" role="group" aria-label="看地形还是看详细"><button type="button" data-mm="map" title="等高线地形：越忙山越高">地形</button><button type="button" data-mm="detail" title="每天写着具体安排">详细</button></div>
            <div class="tc-seg mini" role="group" aria-label="范围"><button type="button" data-mm="month">整月</button><button type="button" data-mm="two" title="放大到两周（+ / −，或双指缩放）">两周</button></div>
          </div><span class="tc-hint"></span></div>`;
      this.$ = s => root.querySelector(s);
      this.retheme();
      // a world switch (or the sky world turning dark at dusk) re-tokens the page; follow it
      // (the time world rewrites its tokens every frame, so this only marks; frame() rereads at most twice a second)
      new MutationObserver(() => { this.themeDirty = true; })
        .observe(document.documentElement, {attributes: true, attributeFilter: ['data-hue', 'data-theme', 'style']});
      this.views ={year: this.makeYear(), month: this.makeMonth(), day: this.makeDay()};
      root.querySelectorAll('.tc-seg button[data-v]').forEach(b => b.onclick = () => this.setView(b.dataset.v));
      this.$('[data-act=prev]').onclick = () => this.views[this.view].step(-1);
      this.$('[data-act=next]').onclick = () => this.views[this.view].step(1);
      this.$('[data-act=today]').onclick = () => { this.date = new Date(this.now); this.views[this.view].enter(true); this.header(); this.load(); };
      this.$('[data-act=toMonth]').onclick = () => this.setView('month');
      root.tabIndex = -1;
      root.addEventListener('keydown', e => {
        if (e.target.closest('.tc-item, .tc-day, input, textarea')) return;
        if (e.key === 'Escape') { if (this.view === 'day') this.setView('month'); else if (this.view === 'month') this.setView('year'); else return; }
        else if (e.key === 'Enter' && e.target === root) { if (this.view === 'year') this.setView('month'); else if (this.view === 'month') this.setView('day'); }
        else if (!this.views[this.view].key(e)) return;
        e.preventDefault();
      });
      this.$(`[data-view="${this.view}"]`).classList.add('active');
      new ResizeObserver(() => this.updateSize()).observe(this.$('.tc-stage'));
      this.updateSize();
      this.views[this.view].enter(true);
      this.header();
      this.load();
      const loop = now => { this.frame(now); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }

    /* ---------- the interface the app calls ---------- */
    getDate() { return this.date; }
    setOption(name, value) { if (name === 'now') { const n = parse(value); if (+n !== +this.now) { this.now = n; this.changed(); } } }
    refetchEvents() { this.months.clear(); return this.load(); }
    updateSize() { this.W = this.$('.tc-stage').clientWidth; this.H = this.$('.tc-stage').clientHeight; Object.values(this.views).forEach(v => v.size()); }
    changeView(type, date) {
      if (date) this.date = parse(date);
      if (ORDER[type] !== undefined) this.setView(type);   // the old grid's names (dayGridMonth, listMonth) mean nothing here
    }

    /* ---------- the world's palette, for the canvases ---------- */
    retheme() {
      // Let the browser resolve each token (var(), color-mix, any colour syntax) on a probe,
      // then flatten it to sRGB through a 1×1 canvas. A see-through surface (the sky world's
      // glass) is laid over the page colour so the ramp has a solid floor to climb from.
      const px = (this._px ||= document.createElement('canvas').getContext('2d', {willReadFrequently: true}));
      const read = (v, under) => {
        probe.style.color = `var(${v})`; const css = getComputedStyle(probe).color;
        px.globalCompositeOperation = 'copy'; px.fillStyle = under ? rgba(under) : 'rgba(0,0,0,0)'; px.fillRect(0, 0, 1, 1);
        px.globalCompositeOperation = 'source-over'; px.fillStyle = css; px.fillRect(0, 0, 1, 1);
        return [...px.getImageData(0, 0, 1, 1).data.slice(0, 3)];
      };
      const sig = ['--tc-bg', '--tc-ink', '--tc-ink-3', '--tc-teal', '--tc-amber', '--tc-serif', '--tc-sans'].map(v => getComputedStyle(this.root).getPropertyValue(v)).join('|') + document.documentElement.dataset.theme;
      if (sig === this._sig) return;
      this._sig = sig;
      const probe = document.createElement('i'); probe.style.display = 'none'; this.root.append(probe);
      T.dark = document.documentElement.dataset.theme === 'dark';
      const floor = T.dark ? [12, 13, 16] : [250, 250, 248];
      T.bg = read('--tc-bg', floor); T.ink = read('--tc-ink', T.bg); T.ink3 = read('--tc-ink-3', T.bg);
      T.accent = read('--tc-teal', T.bg); T.amber = read('--tc-amber', T.bg);
      T.dark = (T.bg[0] * .299 + T.bg[1] * .587 + T.bg[2] * .114) < 128;
      probe.remove(); buildRamp();
      const cs = getComputedStyle(this.root);
      T.serif = cs.getPropertyValue('--tc-serif').trim() || 'serif'; T.sans = cs.getPropertyValue('--tc-sans').trim() || 'sans-serif';
      this.views && Object.values(this.views).forEach(v => v.data());
    }

    /* ---------- data ---------- */
    items(d) { return this.cache.get(typeof d === 'string' ? d : key(d)) || []; }
    async load() {
      const v = this.view, d = this.date, two = v === 'month' && this.views.month.two();
      const span = two ? [new Date(d.getFullYear(), d.getMonth() - 1, 1), new Date(d.getFullYear(), d.getMonth() + 2, 1)]   // two weeks can straddle either edge of the month
        : v === 'year' ? [new Date(d.getFullYear(), 0, 1), new Date(d.getFullYear() + 1, 0, 1)]
        : v === 'day' ? [new Date(d.getFullYear(), d.getMonth(), 1), new Date(addDays(d, 5).getFullYear(), addDays(d, 5).getMonth() + 1, 1)]
          : [new Date(d.getFullYear(), d.getMonth(), 1), new Date(d.getFullYear(), d.getMonth() + 1, 1)];
      const months = []; for (let m = new Date(span[0]); m < span[1]; m = new Date(m.getFullYear(), m.getMonth() + 1, 1)) months.push(key(m).slice(0, 7));
      if (months.every(m => this.months.has(m))) return;
      await new Promise(resolve => this.o.events({startStr: key(span[0]), endStr: key(span[1])}, evs => {
        for (let x = new Date(span[0]); x < span[1]; x = addDays(x, 1)) this.cache.set(key(x), []);
        for (const e of evs || []) { const n = this.normalize(e); (this.cache.get(n.date) || this.cache.set(n.date, []).get(n.date)).push(n); }
        months.forEach(m => this.months.add(m));
        this.changed();
        resolve();
      }, resolve));
    }
    normalize(e) {
      const x = e.extendedProps || {}, agenda = Array.isArray(x.agenda) ? x.agenda : [];
      return {id: String(e.id), date: String(e.start).slice(0, 10), title: x.title || e.title, arr: x.activity_type === 'arrangement',
        done: x.status === 'completed', version: x.version, total: agenda.length, finished: agenda.filter(a => a.done).length};
    }
    changed() { Object.values(this.views).forEach(v => v.data()); }
    itemButton(it) {
      const b = document.createElement('button'); b.type = 'button';
      b.className = 'tc-item' + (it.arr ? ' arr' : '') + (it.done ? ' done' : '');
      b.innerHTML = `<span class="m" aria-hidden="true">${it.done ? '✓' : it.arr ? '○' : '◆'}</span><span class="t">${esc(it.title)}</span><span class="p">${it.total ? `${it.finished}/${it.total}` : ''}</span>`;
      b.setAttribute('aria-label', `打开${it.arr ? '安排' : '任务'}：${it.title}${it.done ? '（已完成）' : ''}`);
      b.onclick = () => this.o.eventClick?.(it.id);
      return b;
    }

    /* ---------- moving between distances ---------- */
    setView(v) {
      if (v === this.view) { this.views[v].enter(true); this.header(); return; }
      const from = this.view, deeper = ORDER[v] > ORDER[from];
      const oldEl = this.$(`[data-view="${from}"]`), newEl = this.$(`[data-view="${v}"]`);
      oldEl.classList.remove('active'); oldEl.classList.toggle('out', deeper);
      newEl.classList.toggle('out', !deeper); void newEl.offsetWidth; newEl.classList.remove('out'); newEl.classList.add('active');
      this.leaving = from; this.leaveUntil = performance.now() + 900;
      this.view = v; try { localStorage.setItem(STORE, v); } catch (_) {}
      this.views[v].enter(false); this.header(); this.load();
      this.root.focus({preventScroll: true});
    }
    header() {
      const d = this.date, v = this.view, m = d.getMonth();
      // one fixed-width date in every view (nothing shifts under the pointer); the parts below the view's scale are dimmed
      this.flapTo(`${d.getFullYear()}.${pad2(m + 1)}.${pad2(d.getDate())}`);
      [...this.$('.tc-flap').children].forEach((s, i) => s.classList.toggle('dim', i >= (v === 'year' ? 4 : v === 'month' ? 7 : 10)));
      this.$('.tc-sub').textContent = `星期${WEEK[d.getDay()]}`;
      this.$('.tc-hint').textContent = HINTS[v];
      this.root.dataset.v = v;
      this.root.querySelectorAll('.tc-seg button[data-v]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === v)));
    }
    flapTo(text) {
      const el = this.$('.tc-flap'), chars = [...text], rm = reduce();
      while (el.children.length < chars.length) el.append(document.createElement('span'));
      while (el.children.length > chars.length) el.lastChild.remove();
      chars.forEach((ch, i) => {
        const s = el.children[i], digit = /\d/.test(ch);
        s.className = digit ? '' : 'dot';
        if (s.textContent === ch) return;
        if (rm || !digit) { s.textContent = ch; return; }
        let steps = 4 + i, cur = Number(s.textContent) || 0;
        clearTimeout(s._t);
        const go = () => { if (steps-- <= 0) { s.textContent = ch; return; } cur = (cur + 1) % 10; s.textContent = cur; s.classList.remove('tick'); void s.offsetWidth; s.classList.add('tick'); s._t = setTimeout(go, 60); };
        go();
      });
    }
    frame(now) {
      // nothing to draw while the calendar is off screen (another page, a hidden tab)
      if (document.hidden || !this.root.offsetParent || !this.W) return;
      if (this.themeDirty && !(now - this.themedAt < 500)) { this.themeDirty = false; this.themedAt = now; this.retheme(); }
      this.views[this.view].draw(now);
      if (this.leaving && now < this.leaveUntil) this.views[this.leaving].draw(now); else this.leaving = null;
    }

    /* =================================================================
       年
       ================================================================= */
    makeYear() {
      const cal = this, sec = this.$('.tc-year'), cv = sec.querySelector('canvas'), ctx = cv.getContext('2d');
      let year = -1, len = 365, focus = 0, target = 0, hover = -1, introAt = 0, W = 0, H = 0, C = [0, 0], R = 120, orbR = 60, lastIx = -1, dpr = 1;
      const stars = Array.from({length: 90}, () => [Math.random(), Math.random(), Math.random()]);
      const dateAt = i => new Date(year, 0, i + 1);
      const wrap = i => ((Math.round(i) % len) + len) % len;
      function goTo(i) { let d = i - target; d = ((d % len) + len * 1.5) % len - len / 2; target += d; }
      function idxAt(x, y) {
        const a = Math.atan2(y - C[1], x - C[0]), r = Math.hypot(x - C[0], y - C[1]);
        if (r < R - 24 || r > R + 70) return -1;
        const rot = -(focus / len) * TAU, f = (((a + Math.PI / 2 - rot) / TAU) % 1 + 1) % 1;
        return wrap(f * len);
      }
      function side(i) {
        const d = dateAt(i), its = cal.items(d);
        sec.querySelector('.k').textContent = `${d.getMonth() + 1} 月 ${d.getDate()} 日 · 星期${WEEK[d.getDay()]}`;
        const list = sec.querySelector('.list'); list.replaceChildren();
        if (its.length) { const ul = document.createElement('ul'); its.forEach(it => { const li = document.createElement('li'); li.append(cal.itemButton(it)); ul.append(li); }); list.append(ul); }
        else list.innerHTML = '<div class="empty">空白的一天。</div>';
      }
      const pt = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
      let drag = null;
      cv.addEventListener('pointerdown', e => { const [x, y] = pt(e); cv.setPointerCapture(e.pointerId); drag = {a: Math.atan2(y - C[1], x - C[0]), moved: 0}; cv.classList.add('drag'); });
      cv.addEventListener('pointermove', e => {
        const [x, y] = pt(e);
        if (drag) { const a = Math.atan2(y - C[1], x - C[0]); let da = a - drag.a; if (da > Math.PI) da -= TAU; if (da < -Math.PI) da += TAU; drag.moved += Math.abs(da); drag.a = a; target -= da / TAU * len; focus = target; }
        else { hover = idxAt(x, y); cv.style.cursor = Math.hypot(x - C[0], y - C[1]) < orbR ? 'zoom-in' : ''; }
      });
      cv.addEventListener('pointerup', e => {
        const [x, y] = pt(e); cv.classList.remove('drag');
        if (drag && drag.moved < .02) { if (Math.hypot(x - C[0], y - C[1]) < orbR) cal.setView('month'); else { const i = idxAt(x, y); if (i >= 0) goTo(i); } }
        else if (drag) target = Math.round(target);
        drag = null;
      });
      cv.addEventListener('pointerleave', () => { hover = -1; });
      cv.addEventListener('wheel', e => { e.preventDefault(); target = Math.round(target + Math.sign(e.deltaY) * Math.max(1, Math.min(6, Math.abs(e.deltaY) / 40))); }, {passive: false});
      return {
        size() {
          dpr = Math.min(devicePixelRatio || 1, 2); W = cal.W; H = cal.H;
          cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          const narrow = W < 560;
          const tight = !narrow && W < 900;   // leave the side panel its own room
          C = [narrow ? W / 2 : W * (tight ? .31 : .35), narrow ? H * .36 : H * .5];
          R = Math.min(narrow ? W * .28 : W * (tight ? .165 : .19), H * (narrow ? .24 : .32)); orbR = R * .52;
        },
        enter(jump) {
          const d = cal.date, first = year < 0;
          if (d.getFullYear() !== year) { year = d.getFullYear(); len = new Date(year, 1, 29).getMonth() === 1 ? 366 : 365; introAt = performance.now(); target = focus = dayOfYear(d) - 1; if (!reduce()) focus -= 40; }
          else if (jump || first) goTo(dayOfYear(d) - 1);
          else goTo(dayOfYear(d) - 1);
          lastIx = -1;
        },
        data() { lastIx = -1; },
        step(s) { const d = cal.date; cal.date = new Date(d.getFullYear() + s, d.getMonth(), Math.min(d.getDate(), 28)); this.enter(true); cal.header(); cal.load(); },
        key(e) {
          const d = dateAt(wrap(target));
          if (e.key === 'ArrowRight') target = Math.round(target) + 1; else if (e.key === 'ArrowLeft') target = Math.round(target) - 1;
          else if (e.key === 'ArrowDown') goTo(dayOfYear(new Date(year, d.getMonth() + 1, Math.min(d.getDate(), 28))) - 1);
          else if (e.key === 'ArrowUp') goTo(dayOfYear(new Date(year, d.getMonth() - 1, Math.min(d.getDate(), 28))) - 1);
          else return false;
          return true;
        },
        draw(now) {
          const rm = reduce();
          focus += (target - focus) * (rm ? 1 : .12);
          const grow = rm ? 1 : Math.min(1, (now - introAt) / 1600);
          const fi = wrap(focus), fd = dateAt(fi), rot = -(focus / len) * TAU, fItems = cal.items(fd);
          // The selected date follows where the ring is headed, not where it is on the way:
          // the intro spin and every glide pass through other days, and leaving mid-spin
          // must not open one of those. Drags, the wheel, keys and clicks all move `target`.
          const gi = wrap(target);
          if (gi !== lastIx) { lastIx = gi; side(gi); const td = dateAt(gi); if (cal.view === 'year' && +cal.date !== +td) { cal.date = td; cal.header(); } }
          const serif = T.serif, sans = T.sans, ink = T.ink;
          // the card's own colour shows through; the ring only adds a glow of the accent round itself
          ctx.clearRect(0, 0, W, H);
          const bg = ctx.createRadialGradient(C[0], C[1], R * .2, C[0], C[1], R * 1.75);   // ends well inside the stage, so there is no edge to see
          bg.addColorStop(0, rgba(T.accent, T.dark ? .14 : .1)); bg.addColorStop(1, rgba(T.accent, 0));
          ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
          if (T.dark) for (const [x, y, s] of stars) { ctx.fillStyle = rgba(ink, .08 + s * .24); ctx.fillRect(x * W, y * H, s > .9 ? 1.5 : 1, s > .9 ? 1.5 : 1); }
          ctx.strokeStyle = rgba(ink, .07); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(C[0], C[1], R * 1.5, 0, TAU); ctx.stroke();
          ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          for (let m = 0; m < 12; m++) {
            const a0 = dayOfYear(new Date(year, m, 1)) - 1, a1 = dayOfYear(new Date(year, m + 1, 0));
            const t0 = -Math.PI / 2 + (a0 / len) * TAU + rot + .008, t1 = -Math.PI / 2 + (a1 / len) * TAU + rot - .008, on = fd.getMonth() === m;
            ctx.strokeStyle = rgba(ink, on ? .55 : .16); ctx.lineWidth = on ? 1.5 : 1;
            ctx.beginPath(); ctx.arc(C[0], C[1], R - 12, t0, t1); ctx.stroke();
            if (R > 90 || on) { const tm = (t0 + t1) / 2; ctx.fillStyle = on ? rgba(ink, .95) : rgba(T.ink3, .8); ctx.font = `${on ? 13 : 11}px ${serif}`; ctx.fillText(CN_MONTH[m], C[0] + Math.cos(tm) * (R - 30), C[1] + Math.sin(tm) * (R - 30)); }
          }
          const ti = cal.now.getFullYear() === year ? dayOfYear(cal.now) - 1 : -1;
          const unit = Math.max(18, R * .36);
          for (let i = 0; i < len; i++) {
            const appear = Math.max(0, Math.min(1, grow * 1.6 - i / len * .6)); if (appear <= 0) continue;
            const a = -Math.PI / 2 + (i / len) * TAU + rot, ca = Math.cos(a), sa = Math.sin(a), k = cal.items(dateAt(i)).length;
            const near = Math.abs(((i - focus) % len + len * 1.5) % len - len / 2), lift = Math.max(0, 1 - near / 4) * 7;
            const L = (4 + level(k) * unit + lift) * appear, c = i === ti ? T.amber : ramp(.3 + level(k) * .7);
            ctx.strokeStyle = i === hover || i === fi ? rgba(ink, .96) : rgba(c, i < ti ? .45 : .9);
            ctx.lineWidth = i === fi ? 2.2 : dateAt(i).getDate() === 1 ? 1.6 : 1.1;
            ctx.beginPath(); ctx.moveTo(C[0] + ca * R, C[1] + sa * R); ctx.lineTo(C[0] + ca * (R + L), C[1] + sa * (R + L)); ctx.stroke();
            if (i === ti) {
              const p = rm ? 0 : Math.sin(now / 500) * .5 + .5, x = C[0] + ca * (R + L + 8), y = C[1] + sa * (R + L + 8);
              ctx.fillStyle = rgba(T.amber); ctx.beginPath(); ctx.arc(x, y, 2.6, 0, TAU); ctx.fill();
              ctx.strokeStyle = rgba(T.amber, .3 + p * .3); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, 6 + p * 2.5, 0, TAU); ctx.stroke();
            }
          }
          ctx.fillStyle = rgba(ink, .9); ctx.beginPath(); ctx.moveTo(C[0], C[1] - R + 3); ctx.lineTo(C[0] - 4, C[1] - R + 11); ctx.lineTo(C[0] + 4, C[1] - R + 11); ctx.closePath(); ctx.fill();
          const lc = ramp(.3 + level(fItems.length) * .7);
          const og = ctx.createRadialGradient(C[0] - orbR * .3, C[1] - orbR * .35, orbR * .05, C[0], C[1], orbR);
          og.addColorStop(0, rgba(mixc(lc, T.ink, .25), .92)); og.addColorStop(.55, rgba(lc, .45)); og.addColorStop(1, rgba(T.bg, 0));
          ctx.fillStyle = og; ctx.beginPath(); ctx.arc(C[0], C[1], orbR, 0, TAU); ctx.fill();
          ctx.fillStyle = rgba(T.bg, .62); ctx.beginPath(); ctx.arc(C[0], C[1], orbR * .78, 0, TAU); ctx.fill();
          ctx.textBaseline = 'alphabetic'; ctx.fillStyle = rgba(ink); ctx.font = `400 ${Math.round(orbR * .72)}px ${serif}`;
          ctx.fillText(fd.getDate(), C[0], C[1] + orbR * .16);
          ctx.fillStyle = rgba(ink, .82); ctx.font = `12px ${serif}`; ctx.fillText(`${CN_MONTH[fd.getMonth()]} · 星期${WEEK[fd.getDay()]}`, C[0], C[1] + orbR * .42);
          ctx.fillStyle = rgba(ink, .62); ctx.font = `12px ${sans}`; ctx.fillText(fItems.length ? `${fItems.length} 项` : '没有安排', C[0], C[1] + orbR * .7);
          if (hover >= 0 && !drag) {
            const a = -Math.PI / 2 + (hover / len) * TAU + rot, hd = dateAt(hover), hi = cal.items(hd);
            const x = Math.max(90, Math.min(W - 90, C[0] + Math.cos(a) * (R + 80))), y = Math.max(16, Math.min(H - 16, C[1] + Math.sin(a) * (R + 80)));
            const text = `${hd.getMonth() + 1}月${hd.getDate()}日 · ${hi.length ? hi.map(t => t.title).slice(0, 2).join('、') + (hi.length > 2 ? '…' : '') : '没有安排'}`;
            ctx.font = `12px ${sans}`; const tw = Math.min(ctx.measureText(text).width + 22, 300);
            ctx.fillStyle = rgba(T.bg, .94); ctx.strokeStyle = rgba(ink, .18); ctx.beginPath(); ctx.roundRect(x - tw / 2, y - 13, tw, 26, 13); ctx.fill(); ctx.stroke();
            ctx.fillStyle = rgba(ink); ctx.textBaseline = 'middle'; ctx.fillText(text, x, y, tw - 16);
          }
        },
      };
    }

    /* =================================================================
       月
       ================================================================= */
    makeMonth() {
      const cal = this, sec = this.$('.tc-month'), cv = sec.querySelector('canvas'), gl = cv.getContext('webgl', {antialias: true, premultipliedAlpha: true});
      const daysEl = sec.querySelector('.tc-days'), tip = sec.querySelector('.tc-tip'), flat = sec.querySelector('.tc-flat');
      let prog = null, u = {};
      if (gl) {
        gl.getExtension('OES_standard_derivatives');
        prog = glProgram(gl, `#extension GL_OES_standard_derivatives : enable
precision highp float;
uniform vec2 uRes; uniform float uTime; uniform vec3 uDays[42]; uniform vec2 uHover; uniform vec2 uToday; uniform float uCell;
uniform vec3 uInk; uniform vec3 uAmber; uniform float uDark;
float hash(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+45.32); return fract(p.x*p.y); }
float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f); return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y); }
float fbm(vec2 p){ float a=.5,s=0.; for(int i=0;i<4;i++){ s+=a*noise(p); p=p*2.03+17.; a*=.5; } return s; }
${GLSL_RAMP}
float height(vec2 p){
  float h=0.;
  for(int i=0;i<42;i++){ vec3 d=uDays[i]; if(d.z<=0.) continue; vec2 q=(p-d.xy)/uCell; h+=d.z*exp(-dot(q,q)*2.2); }
  // the ground the hills stand on: a slow rolling plain, so an empty month still reads as a map
  return h+.26*fbm(p/uCell*.5+vec2(uTime*.012,-uTime*.008));
}
vec4 over(vec3 c,float a,vec4 under){ return vec4(c*a,a)+under*(1.-a); }
void main(){
  vec2 p=gl_FragCoord.xy; p.y=uRes.y-p.y;
  float h=height(p), v=h*12., fw=max(fwidth(v),1e-4);
  float line=1.-smoothstep(0.,fw*1.25,abs(fract(v-.5)-.5));
  float major=1.-smoothstep(0.,fw*1.25,abs(fract(v/4.-.5)-.5)*4.);
  float lift=smoothstep(.14,.95,h);
  vec3 tint=ramp(.1+h*.9);
  // Drawn over a clear canvas (premultiplied): the card's own colour is the sea level.
  // Hills are washed in the ramp; the contour lines start out in ink, so even the
  // plain shows its rings, and take the ramp's colour as they climb.
  vec4 c=vec4(0.);
  c=over(tint,lift*(.2+.3*uDark),c);
  vec3 lc=mix(uInk,tint,smoothstep(.08,.5,h));
  c=over(lc,clamp(line*(.2+.55*lift)+major*(.16+.3*lift),0.,1.),c);
  float hv=length(p-uHover)/uCell; c=over(tint,.2*exp(-hv*hv*3.),c);
  float td=length(p-uToday)/uCell; c=over(uAmber,(.1+.14*uDark)*exp(-td*td*4.)*(.75+.25*sin(uTime*2.)),c);
  // dissolve toward the stage edges so the map has no seam against the header and footer
  vec2 e=gl_FragCoord.xy/uRes; c*=smoothstep(0.,.07,e.x)*smoothstep(0.,.07,1.-e.x)*smoothstep(0.,.1,e.y)*smoothstep(0.,.1,1.-e.y);
  gl_FragColor=c;
}`);
        ['uRes', 'uTime', 'uDays', 'uHover', 'uToday', 'uCell', 'uInk', 'uAmber', 'uDark', 'uR'].forEach(n => { u[n] = prog.u(n); });
      }
      let shown = null, month = [], cells = [], cell = 60, cw = 60, ch = 60, dpr = 1, W = 0, H = 0, amps = new Float32Array(42), target = new Float32Array(42), hoverIx = -1, hover = [-999, -999];
      // Two ways to look (the contour map, or each day's own cards) and two distances (the month, or two weeks up close).
      // Both are remembered; the map stays underneath the cards, faded, so the shape of the month is never lost.
      // The day cells are one even grid. What changes is the things inside them: a task with steps is a small card
      // with its progress, an arrangement a capsule, something finished a struck line; up close each opens into a
      // fuller card, and when the distance changes every card travels from where it was to where it now belongs.
      let ui = {}; try { ui = JSON.parse(localStorage.getItem('tc-month-ui')) || {}; } catch (_) {}
      let detail = !!ui.detail, two = !!ui.two, builtTwo = null;
      let rowH = [], rowY = [], hs = [];
      const t0 = performance.now();
      const open = d => { cal.date = d; cal.setView('day'); };
      const modeButtons = cal.root.querySelectorAll('[data-mm]');
      const todayIx = () => { const i = month.findIndex(dd => dd && +dd === +cal.now); return i >= 0 ? i : Math.max(0, month.findIndex(Boolean)); };
      // FLIP: note where every cell and card was, change the layout, then let them travel from there.
      const EASE = 'cubic-bezier(.2, .8, .2, 1)', BASE = 'translate(-50%, -50%)';
      function rects() {
        const m = new Map();
        daysEl.querySelectorAll('.tc-day[data-key]').forEach(e => m.set('d' + e.dataset.key, e.getBoundingClientRect()));
        daysEl.querySelectorAll('.tc-chip[data-key]').forEach(e => m.set('c' + e.dataset.key, e.getBoundingClientRect()));
        return m;
      }
      function travel(before, how = {}) {
        if (reduce()) return;
        const from = how.from ?? 0, fc = from % 7, fr = Math.floor(from / 7);
        daysEl.querySelectorAll('.tc-day[data-key]').forEach(e => {
          const i = +e.dataset.i, c = i % 7, r = Math.floor(i / 7);
          if (how.enter === 'zoom') { e.animate([{opacity: 0}, {opacity: 1}], {duration: 260, easing: 'ease-out'}); return; }
          const was = before.get('d' + e.dataset.key), now = e.getBoundingClientRect();
          const delay = how.enter === 'ripple' ? (Math.abs(c - fc) + Math.abs(r - fr)) * 30 : how.enter === 'slide' ? c * 24 : 0;
          if (was && was.width && now.width && how.enter !== 'slide') {
            const dx = was.left + was.width / 2 - (now.left + now.width / 2), dy = was.top + was.height / 2 - (now.top + now.height / 2);
            const sx = was.width / now.width, sy = was.height / now.height;
            if (Math.abs(dx) + Math.abs(dy) < .5 && Math.abs(sx - 1) + Math.abs(sy - 1) < .02) return;
            e.animate([{transform: `${BASE} translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`}, {transform: BASE}], {duration: 480, delay, easing: EASE, fill: 'backwards'});
          } else {
            const start = how.enter === 'slide' ? `${BASE} translateX(${(how.dir || 1) * 32}px)` : `${BASE} scale(.72)`;
            e.animate([{transform: start, opacity: 0}, {transform: BASE, opacity: 1}], {duration: 440, delay: delay + (how.enter ? 0 : 120), easing: EASE, fill: 'backwards'});
          }
        });
        if (how.enter !== 'zoom') return;
        // the cards fly: each from its old place and size to its new one, and the cells let them pass over their edges
        const cells = new Set();
        daysEl.querySelectorAll('.tc-chip[data-key]').forEach((e, n) => {
          const was = before.get('c' + e.dataset.key), now = e.getBoundingClientRect();
          if (!now.width) return;
          const cellEl = e.closest('.tc-day'); cells.add(cellEl);
          if (was && was.width) {
            e.animate([{transform: `translate(${was.left - now.left}px, ${was.top - now.top}px) scale(${was.width / now.width}, ${was.height / now.height})`, transformOrigin: '0 0'},
              {transform: 'none', transformOrigin: '0 0'}], {duration: 520, delay: n * 12, easing: EASE, fill: 'backwards'});
          } else e.animate([{opacity: 0, transform: 'translateY(6px)'}, {opacity: 1, transform: 'none'}], {duration: 360, delay: 200 + n * 12, easing: EASE, fill: 'backwards'});
        });
        cells.forEach(c => { c.classList.add('flying'); setTimeout(() => c.classList.remove('flying'), 900); });
      }
      function setMode(next) {
        const was = two, wasDetail = detail, before = rects();
        if ('detail' in next) detail = next.detail; if ('two' in next) two = next.two;
        try { localStorage.setItem('tc-month-ui', JSON.stringify({detail, two})); } catch (_) {}
        build(); cal.header();
        if (was !== two) cal.load();
        travel(before, {enter: detail !== wasDetail ? 'ripple' : was !== two && detail ? 'zoom' : '', from: todayIx()});
      }
      modeButtons.forEach(b => b.onclick = () => setMode(b.dataset.mm === 'map' || b.dataset.mm === 'detail' ? {detail: b.dataset.mm === 'detail'} : {two: b.dataset.mm === 'two'}));
      // pinch on a trackpad (or ctrl + wheel) zooms between the month and two weeks
      sec.addEventListener('wheel', e => { if (!e.ctrlKey) return; e.preventDefault(); if ((e.deltaY < 0) !== two) setMode({two: e.deltaY < 0}); }, {passive: false});
      function place() {
        const rows = month.length / 7 || 6, top = 30, bottom = 30, side = W < 560 ? 10 : 30, gw = W - side * 2, gh = H - top - bottom;
        // the map keeps square cells; the cards and the close-up use the whole stage, in one even grid
        if (detail || two) { cw = gw / 7; ch = gh / rows; } else cw = ch = Math.max(10, Math.min(gw / 7, gh / rows));
        cell = Math.max(10, Math.min(cw, ch * (two && !detail ? .75 : 1)));
        rowH = Array(rows).fill(ch);
        const ox = side + (gw - cw * 7) / 2;
        let y = top + (gh - ch * rows) / 2; rowY = rowH.map(h => { const at = y; y += h; return at; });
        sec.style.setProperty('--cw', `${cw}px`); sec.style.setProperty('--ch', `${ch}px`);
        // every date sits on the grid, straight under its weekday
        hs = Array.from({length: month.length}, () => ch);
        cells = Array.from({length: month.length}, (_, i) => [ox + (i % 7 + .5) * cw, rowY[Math.floor(i / 7)] + ch / 2]);
        daysEl.querySelectorAll('.tc-wk').forEach(e => { e.style.left = `${ox + (+e.dataset.wk + .5) * cw}px`; e.style.top = `${Math.max(4, rowY[0] - 22)}px`; });
        daysEl.querySelectorAll('.tc-day').forEach(e => { const [x, yy] = cells[e.dataset.i]; e.style.left = `${x}px`; e.style.top = `${yy}px`; });
      }
      function build() {
        const d = cal.date; builtTwo = two;
        if (two) { shown = addDays(d, -d.getDay()); month = Array.from({length: 14}, (_, i) => addDays(shown, i)); }
        else {
          shown = new Date(d.getFullYear(), d.getMonth(), 1);
          const lead = shown.getDay(), n = new Date(shown.getFullYear(), shown.getMonth() + 1, 0).getDate();
          month = Array.from({length: 42}, (_, i) => { const x = i - lead + 1; return x < 1 || x > n ? null : new Date(shown.getFullYear(), shown.getMonth(), x); });
        }
        sec.classList.toggle('detail', detail); sec.classList.toggle('two', two);
        cal.root.classList.toggle('tc-detail', detail);
        modeButtons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mm === (detail ? 'detail' : 'map') || b.dataset.mm === (two ? 'two' : 'month'))));
        daysEl.replaceChildren(...WEEK.map((w, c) => { const e = document.createElement('div'); e.className = 'tc-wk'; e.dataset.wk = c; e.textContent = w; e.setAttribute('aria-hidden', 'true'); return e; }), ...month.map((dd, i) => {
          const e = document.createElement('button'); e.type = 'button'; e.className = 'tc-day'; e.dataset.i = i;
          if (!dd) { e.classList.add('hide'); e.tabIndex = -1; e.setAttribute('aria-hidden', 'true'); return e; }
          e.dataset.key = key(dd);
          e.onclick = () => open(dd);
          e.onmouseenter = () => { hoverIx = i; }; e.onmouseleave = () => { if (hoverIx === i) hoverIx = -1; };
          return e;
        }));
        place(); data();   // place first: a cell needs its size to know how many cards fit
      }
      // A card's shape says what it is. Heights here match the CSS, so a cell knows how many it can hold.
      const cardOf = t => t.done ? 'done' : t.arr ? 'arr' : t.total ? 'steps' : 'task';
      // One big, the rest small: a day's first open thing is its lead card (a label, a fuller title, its progress);
      // everything after it is a compact line, so each cell reads as "this, and also these".
      const HEIGHT = {month: {done: 16, arr: 18, task: 18, steps: 18, lead: 35, leadSteps: 35}, two: {done: 22, arr: 26, task: 26, steps: 26, lead: 78, leadSteps: 92}};
      const TAG = {task: '待办', arr: '安排', steps: '进度'};
      function card(t, lead) {
        const kind = cardOf(t), id = esc(String(t.id)), title = `<span class="tc-chip-t">${esc(t.title)}</span>`;
        if (!lead) return `<span class="tc-chip ${kind}" data-key="${id}">${title}${kind === 'steps' && two ? `<b class="tc-chip-n">${t.finished}/${t.total}</b>` : ''}</span>`;
        const steps = kind === 'steps' ? `<span class="tc-steps"><i style="--p:${(t.finished / t.total).toFixed(2)}"></i></span>` : '';
        return `<span class="tc-chip lead ${kind}" data-key="${id}"><span class="tc-chip-k">${TAG[kind]}${kind === 'steps' ? `<b>${t.finished}/${t.total}</b>` : ''}</span>${title}${steps}</span>`;
      }
      function data() {
        let total = 0; target.fill(0);
        const gap = two ? 5 : 3, sizes = HEIGHT[two ? 'two' : 'month'];
        month.forEach((dd, i) => {
          const e = daysEl.querySelector(`.tc-day[data-i="${i}"]`);
          if (!dd) { target[i] = 0; return; }
          const its = cal.items(dd), k = its.length; total += k;
          target[i] = k ? .12 + .88 * Math.pow(level(k), .85) : .05;
          if (!e) return;
          e.classList.toggle('weekend', dd.getDay() % 6 === 0); e.classList.toggle('busy', k >= 3); e.classList.toggle('today', +dd === +cal.now); e.classList.toggle('past', dd < cal.now);
          e.classList.toggle('other', two && dd.getMonth() !== cal.date.getMonth());
          // in the close-up a month's first day (and the first cell) says which month it is
          const label = two && (i === 0 || dd.getDate() === 1) ? `${dd.getDate()}<small>${dd.getMonth() + 1}月</small>` : `${dd.getDate()}`;
          if (detail) {
            // open things first (tasks with steps, then tasks, then arrangements), finished ones last
            const order = {steps: 0, task: 1, arr: 2, done: 3}, sorted = its.slice().sort((a, b) => order[cardOf(a)] - order[cardOf(b)]);
            // fill the cell top-down until the next card would not fit; the rest is counted
            // the first open thing leads, if the cell has room for it (and the 还有 line under it); otherwise it stays small
            let room = hs[i] - (two ? 50 : 32), fit = 0, lead = false;
            const first = sorted[0], leadH = first && cardOf(first) !== 'done' ? sizes[cardOf(first) === 'steps' ? 'leadSteps' : 'lead'] + gap : 0;
            if (leadH && room - leadH >= (k > 1 ? 16 : 0) && cw >= 64) { room -= leadH; fit = 1; lead = true; }
            for (const t of sorted.slice(fit)) { const h = sizes[cardOf(t)] + gap; if (room - h < (fit + 1 < k ? 16 : 0)) break; room -= h; fit++; }
            // when not even one card fits, every thing becomes a pebble in its own colour, so the day still shows what it holds
            const pebbles = !fit && k ? `<span class="tc-pebbles" aria-hidden="true">${sorted.slice(0, 8).map(t => `<i class="${cardOf(t)}"></i>`).join('')}</span>` : '';
            e.innerHTML = `<div class="n">${label}</div><div class="tc-lis">${sorted.slice(0, fit).map((t, n) => card(t, lead && !n)).join('')}${pebbles}${k > fit && fit ? `<span class="tc-more">还有 ${k - fit} 项</span>` : ''}</div>`;
          } else e.innerHTML = `<div class="n">${label}</div><div class="c">${'<i></i>'.repeat(Math.min(k, 6))}</div>`;
          e.setAttribute('aria-label', `${dd.getMonth() + 1}月${dd.getDate()}日，${k} 项，打开这一天`);
        });
        flat.textContent = (two ? '这两周' : '这个月') + '还是平地 · 点任意一天去添加';
        flat.hidden = total > 0 || detail;
      }
      const relayout = () => { place(); data(); };
      sec.addEventListener('pointermove', e => {
        if (e.target !== cv) { tip.style.opacity = 0; return; }
        const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
        let best = -1, bd = 1e9; cells.forEach(([cx, cy], i) => { if (!month[i]) return; const d = Math.hypot(cx - x, cy - y); if (d < bd) { bd = d; best = i; } });
        hoverIx = bd < cell * .7 ? best : -1;
        if (hoverIx >= 0) {
          const dd = month[hoverIx], its = cal.items(dd);
          tip.innerHTML = `<b>${dd.getMonth() + 1}月${dd.getDate()}日</b> · ${its.length ? its.slice(0, 3).map(t => esc(t.title)).join('、') + (its.length > 3 ? ' …' : '') : '没有安排'}`;
          tip.style.left = `${Math.min(x, W - 200)}px`; tip.style.top = `${y}px`; tip.style.opacity = 1;
        } else tip.style.opacity = 0;
        cv.style.cursor = hoverIx >= 0 ? 'zoom-in' : '';
      });
      sec.addEventListener('pointerleave', () => { tip.style.opacity = 0; hoverIx = -1; });
      cv.addEventListener('click', () => { if (hoverIx >= 0) open(month[hoverIx]); });
      return {
        size() { dpr = Math.min(devicePixelRatio || 1, 2); W = cal.W; H = cal.H; cv.width = Math.max(1, Math.round(W * dpr)); cv.height = Math.max(1, Math.round(H * dpr)); place(); if (shown) data(); },
        enter() {
          const d = cal.date;
          const inside = shown && builtTwo === two && (two ? d >= shown && d < addDays(shown, 14) : d.getFullYear() === shown.getFullYear() && d.getMonth() === shown.getMonth());
          if (!inside) build(); else { place(); data(); }
        },
        data() { if (shown) relayout(); },
        two: () => two,
        // a page is a month, or in the close-up two weeks
        step(s) { const d = cal.date; cal.date = two ? addDays(d, 14 * s) : new Date(d.getFullYear(), d.getMonth() + s, Math.min(d.getDate(), 28)); build(); travel(new Map(), {enter: 'slide', dir: s}); cal.header(); cal.load(); },
        key(e) {
          if (e.key === 'ArrowLeft') this.step(-1); else if (e.key === 'ArrowRight') this.step(1);
          else if ((e.key === '+' || e.key === '=') && !two) setMode({two: true}); else if ((e.key === '-' || e.key === '_') && two) setMode({two: false});
          else return false;
          return true;
        },
        draw(now) {
          if (!prog) return;
          prog.use(cv.width, cv.height);
          const rm = reduce();
          for (let i = 0; i < 42; i++) amps[i] += (target[i] - amps[i]) * (rm ? 1 : .06);
          const hv = hoverIx >= 0 ? cells[hoverIx] : [-999, -999]; hover[0] += (hv[0] - hover[0]) * .2; hover[1] += (hv[1] - hover[1]) * .2;
          const arr = new Float32Array(126); cells.forEach(([x, y], i) => { arr[i * 3] = x * dpr; arr[i * 3 + 1] = y * dpr; arr[i * 3 + 2] = amps[i]; });
          const ti = month.findIndex(dd => dd && +dd === +cal.now), tp = ti >= 0 ? cells[ti] : [-999, -999];
          gl.uniform2f(u.uRes, cv.width, cv.height); gl.uniform1f(u.uTime, rm ? 0 : (now - t0) / 1000); gl.uniform3fv(u.uDays, arr);
          gl.uniform2f(u.uHover, hover[0] * dpr, hover[1] * dpr); gl.uniform2f(u.uToday, tp[0] * dpr, tp[1] * dpr); gl.uniform1f(u.uCell, cell * dpr);
          gl.uniform3fv(u.uInk, T.ink.map(v => v / 255)); gl.uniform3fv(u.uAmber, T.amber.map(v => v / 255)); gl.uniform1f(u.uDark, T.dark ? 1 : 0); gl.uniform3fv(u.uR, rampUniform());
          gl.drawArrays(gl.TRIANGLES, 0, 3);
        },
      };
    }

    /* =================================================================
       日
       ================================================================= */
    makeDay() {
      const cal = this, sec = this.$('.tc-dayv'), pad = sec.querySelector('.tc-pad'), cv = sec.querySelector('canvas'), gl = cv.getContext('webgl');
      const TORN = 'polygon(0 3%, 4% 1%, 8% 3.4%, 12% 1.2%, 16% 3%, 20% .8%, 24% 2.8%, 28% 1%, 32% 3.2%, 36% 1.2%, 40% 2.6%, 44% .6%, 48% 3%, 52% 1.4%, 56% 3.2%, 60% .8%, 64% 2.8%, 68% 1.2%, 72% 3.4%, 76% 1%, 80% 2.6%, 84% .8%, 88% 3%, 92% 1.2%, 96% 3.2%, 100% 1.4%, 100% 100%, 0 100%)';
      let busy = false, shown = null, silk = 0, silkTarget = 0, prog = null, u = {};
      const t0 = performance.now();
      if (gl) {
        prog = glProgram(gl, `precision highp float;
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
  // silk in the ramp's colours, laid over the card (premultiplied), thinning toward the edges
  float edge=(1.-smoothstep(.25,.75,length((uv-vec2(.5,.48))*vec2(1.1,1.))))*smoothstep(0.,.12,uv.x)*smoothstep(0.,.12,1.-uv.x)*smoothstep(0.,.15,uv.y)*smoothstep(0.,.15,1.-uv.y);
  float a=smoothstep(.35,.85,f)*.42*edge; vec4 c=vec4(ramp(.15+.55*f+.3*uLoad)*a,a);
  float b=smoothstep(.72,.98,f)*.3*(.4+.6*uv.y)*edge; c=vec4(ramp(.6+.4*uLoad)*b,b)+c*(1.-b);
  gl_FragColor=c;
}`);
        ['uRes', 'uTime', 'uLoad', 'uR'].forEach(n => { u[n] = prog.u(n); });
      }
      function sheetFor(d) {
        const s = document.createElement('article'); s.className = 'tc-sheet';
        const its = cal.items(d), dow = d.getDay(), left = Math.round((new Date(d.getFullYear(), 11, 31) - d) / 864e5), toWeekend = dow === 0 || dow === 6 ? 0 : 6 - dow;
        const bars = Array.from({length: FULL}, (_, k) => k < its.length ? `<i style="background:rgb(${ramp(.25 + (k / (FULL - 1)) * .75).join(',')})"></i>` : '<i></i>').join('');
        s.classList.toggle('weekend', dow === 0 || dow === 6);
        s.innerHTML = `<div class="meta"><span><b>${d.getFullYear()}</b>  ${CN_MONTH[d.getMonth()]}<span class="en"> · ${EN_MONTH[d.getMonth()].toUpperCase()}</span></span><span class="doy">第 ${dayOfYear(d)} 天</span></div>
          <div class="num">${d.getDate()}</div>
          <div class="wd">星期${WEEK[dow]}<small>${EN_WEEK[dow]}</small><span class="load" title="这一天有多满：${its.length} 项">${bars}</span></div>
          <hr><div class="list"></div>
          <div class="foot"><span>${+d === +cal.now ? '今天' : toWeekend ? `距周末还有 ${toWeekend} 天` : '周末愉快'}</span><span>今年还剩 ${left} 天</span></div>`;
        const list = s.querySelector('.list');
        if (its.length) its.forEach(it => list.append(cal.itemButton(it)));
        else list.innerHTML = `<div class="empty">${d < cal.now ? '这一天没有记录。' : '什么都没安排。留白也是一种安排。'}</div>`;
        if (d >= cal.now && cal.o.dateClick) {
          const add = document.createElement('button'); add.type = 'button'; add.className = 'add'; add.textContent = '＋ 新建';
          add.onclick = () => cal.o.dateClick(key(d)); list.append(add);
        }
        s.setAttribute('aria-label', `${d.getMonth() + 1}月${d.getDate()}日 星期${WEEK[dow]}，${its.length} 项`);
        return s;
      }
      function render() {
        pad.querySelectorAll('.tc-sheet').forEach(s => s.remove());
        for (let k = 3; k >= 0; k--) {
          const s = sheetFor(addDays(cal.date, k));
          s.style.zIndex = 10 - k; s.style.transform = `translateY(${k * 5}px) scale(${1 - k * .012}) rotate(${[0, -.6, .8, -.4][k]}deg)`;
          if (k) { s.classList.add('under'); s.inert = true; } else { s.classList.add('top'); hook(s); }
          pad.append(s);
        }
        shown = new Date(cal.date); silkTarget = level(cal.items(cal.date).length);
      }
      function tear(sheet, vx = 220, vy = 420) {
        if (busy) return; busy = true;
        sheet.style.clipPath = TORN;
        const stub = document.createElement('div'); stub.className = 'tc-stub'; pad.append(stub);
        requestAnimationFrame(() => { stub.style.opacity = 0; setTimeout(() => stub.remove(), 2000); });
        const from = getComputedStyle(sheet).transform, spin = (vx >= 0 ? 1 : -1) * (18 + Math.random() * 16);
        sheet.classList.add('dragging');
        const anim = sheet.animate([{transform: from === 'none' ? 'none' : from, opacity: 1}, {transform: `translate(${vx * 1.4}px, ${vy * 1.1 + 120}px) rotate(${spin}deg)`, opacity: 1, offset: .55}, {transform: `translate(${vx * 2.2}px, ${vy * 1.8 + 420}px) rotate(${spin * 1.6}deg)`, opacity: 0}], {duration: reduce() ? 1 : 900, easing: 'cubic-bezier(.3, .2, .6, 1)', fill: 'forwards'});
        [...pad.querySelectorAll('.tc-sheet.under')].reverse().forEach((s, k) => { s.style.transform = `translateY(${k * 5}px) scale(${1 - k * .012}) rotate(${[0, -.6, .8][k] ?? 0}deg)`; if (!k) s.classList.remove('under'); });
        cal.date = addDays(cal.date, 1); cal.header(); cal.load();
        anim.onfinish = () => { render(); busy = false; };
      }
      function untear() {
        if (busy) return; busy = true;
        const s = sheetFor(addDays(cal.date, -1)); s.style.zIndex = 20; s.classList.add('dragging'); pad.append(s);
        cal.date = addDays(cal.date, -1); cal.header(); cal.load();
        s.animate([{transform: 'translate(-50px, -110%) rotate(-12deg)', opacity: 0}, {transform: 'translate(0, 6px) rotate(1deg)', opacity: 1, offset: .8}, {transform: 'none', opacity: 1}], {duration: reduce() ? 1 : 650, easing: 'cubic-bezier(.22, .61, .36, 1)'}).onfinish = () => { render(); busy = false; };
      }
      function hook(s) {
        let st = null;
        s.addEventListener('pointerdown', e => { if (busy || e.target.closest('button')) return; s.setPointerCapture(e.pointerId); st = {x: e.clientX, y: e.clientY, lx: e.clientX, ly: e.clientY, lt: performance.now()}; s.classList.add('dragging'); });
        s.addEventListener('pointermove', e => {
          if (!st) return;
          const dx = e.clientX - st.x, dy = Math.max(-20, e.clientY - st.y);
          s.style.transform = `translate(${dx * .35}px, ${dy * .5}px) rotate(${dx * .05}deg) rotateX(${Math.min(dy, 200) * -.06}deg)`;
          const dt = Math.max(1, performance.now() - st.lt); st.vx = (e.clientX - st.lx) / dt; st.vy = (e.clientY - st.ly) / dt; st.lx = e.clientX; st.ly = e.clientY; st.lt = performance.now();
        });
        const end = e => {
          if (!st) return;
          const dx = e.clientX - st.x, dy = e.clientY - st.y, fast = Math.hypot(st.vx || 0, st.vy || 0) > .7;
          s.classList.remove('dragging');
          if (Math.hypot(dx, dy) > 110 || fast) tear(s, dx || 200, Math.max(dy, 120)); else s.style.transform = 'none';
          st = null;
        };
        s.addEventListener('pointerup', end); s.addEventListener('pointercancel', end);
      }
      return {
        size() { const k = Math.min(devicePixelRatio || 1, 1.5) * .6; cv.width = Math.max(1, Math.round(cal.W * k)); cv.height = Math.max(1, Math.round(cal.H * k)); },
        enter() { if (!shown || +shown !== +cal.date || !pad.querySelector('.tc-sheet')) render(); },
        data() { if (!busy && shown) render(); },
        step(s) { if (s > 0) { const top = pad.querySelector('.tc-sheet.top'); if (top) tear(top); } else untear(); },
        key(e) { if (e.key === 'ArrowRight') this.step(1); else if (e.key === 'ArrowLeft') this.step(-1); else return false; return true; },
        draw(now) {
          if (!prog) return;
          silk += (silkTarget - silk) * .04;
          prog.use(cv.width, cv.height);
          gl.uniform2f(u.uRes, cv.width, cv.height); gl.uniform1f(u.uTime, reduce() ? 0 : (now - t0) / 1000); gl.uniform1f(u.uLoad, silk); gl.uniform3fv(u.uR, rampUniform());
          gl.drawArrays(gl.TRIANGLES, 0, 3);
        },
      };
    }
  }
  window.TimeCalendar = TimeCalendar;
})();
