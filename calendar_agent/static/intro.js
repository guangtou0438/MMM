'use strict';
/* ==========================================================================
   Intro: the launch moment. A cloud of particles (up to a million) spirals in
   and settles into a glowing "M", a light sweeps across it, then it drifts
   apart and the app shows through.

   Loaded in <head> without defer: the first lines decide, before first paint,
   whether to play at all (once per tab session; never with reduced motion or
   under browser automation; ?intro forces it). The overlay is hidden by CSS unless data-intro="play",
   so if anything here fails the app is never covered.
   All motion lives in the vertex shader; the CPU only builds the "M" once.
   ========================================================================== */
(() => {
  const root = document.documentElement;
  // read now: the app tidies the address bar before DOMContentLoaded
  const query = new URLSearchParams(location.search), forced = query.has('intro');
  const still = parseFloat(query.get('intro'));   // ?intro=2.8 freezes that moment, for tuning
  let seen = false;
  try { seen = sessionStorage.getItem('mmm-intro') === '1'; } catch {}
  if (!forced && (seen || navigator.webdriver || matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
  root.dataset.intro = 'play';
  try { sessionStorage.setItem('mmm-intro', '1'); } catch {}   // counted once it starts, even if never finished

  const ASSEMBLE = 2.6, HOLD = 1.3, DISSOLVE = 1.1;   // seconds
  let finish = () => { root.dataset.intro = 'done'; };

  addEventListener('DOMContentLoaded', () => {
    const box = document.getElementById('intro'), cv = box && box.querySelector('canvas');
    const gl = cv && cv.getContext('webgl', {alpha: true, premultipliedAlpha: true, antialias: false});
    const end = () => {
      root.dataset.intro = 'done';
      setTimeout(() => box && box.remove(), 700);
    };
    if (!gl) { end(); return; }

    // ---- the M: a bold rounded stroke, rasterised once and sampled -------
    const W = 512, H = 420, ink = document.createElement('canvas');
    ink.width = W; ink.height = H;
    const c2 = ink.getContext('2d', {willReadFrequently: true});
    c2.lineCap = c2.lineJoin = 'round'; c2.lineWidth = 70; c2.strokeStyle = '#fff';
    c2.filter = 'blur(4px)';
    c2.beginPath(); c2.moveTo(86, 350); c2.lineTo(86, 70); c2.lineTo(256, 250); c2.lineTo(426, 70); c2.lineTo(426, 350); c2.stroke();
    const alpha = c2.getImageData(0, 0, W, H).data, cells = [];
    for (let i = 3; i < alpha.length; i += 4) if (alpha[i] > 8) cells.push((i - 3) >> 2);

    const small = innerWidth < 768 || (navigator.hardwareConcurrency || 8) <= 4;
    const N = small ? 260000 : 1000000;
    const target = new Float32Array(N * 3), seed = new Float32Array(N * 4);
    const R = Math.random, unit = 1 / W;
    for (let n = 0; n < N; n++) {
      let cell, a;
      do { cell = cells[(R() * cells.length) | 0]; a = alpha[cell * 4 + 3] / 255; } while (R() > a);
      const halo = R() < .05;   // a thin dust around the letter, for glow
      let x = ((cell % W) + R() - W / 2) * unit, y = (H / 2 - ((cell / W) | 0) - R()) * unit;
      let z = (R() - .5) * .14 * a;
      if (halo) { const s = .06 + R() * .1, t = R() * 6.283; x += Math.cos(t) * s; y += Math.sin(t) * s; z *= 3; }
      const i = n * 3, j = n * 4;
      target[i] = x * 2; target[i + 1] = y * 2; target[i + 2] = z;
      seed[j] = R(); seed[j + 1] = R(); seed[j + 2] = R(); seed[j + 3] = halo ? -.001 - R() : .001 + R();
    }

    // ---- GPU ------------------------------------------------------------
    const vs = `precision highp float;
attribute vec3 aT; attribute vec4 aS;
uniform float uT, uOut, uAspect, uDpr; uniform vec3 uC[3];
varying vec3 vC; varying float vA;
const float A = ${ASSEMBLE.toFixed(2)};
vec3 rotY(vec3 p, float a){ float c=cos(a), s=sin(a); return vec3(c*p.x+s*p.z, p.y, -s*p.x+c*p.z); }
void main(){
  float halo = step(aS.w, 0.); float r4 = abs(aS.w);
  // start: a loose shell far around the letter
  float th = aS.x*6.2832, ph = acos(2.*aS.y-1.), rad = 1.6 + 2.4*aS.z;
  vec3 start = vec3(sin(ph)*cos(th), cos(ph)*.7, sin(ph)*sin(th)) * rad;
  // each particle leaves at its own moment and spirals in
  float p = clamp((uT - .1 - r4*.9) / (A - 1.), 0., 1.);
  float e = p < .5 ? 4.*p*p*p : 1. - pow(-2.*p + 2., 3.) / 2.;
  vec3 pos = mix(rotY(start, (1.-e)*3.2), aT, e);
  // breath while it holds
  pos += vec3(sin(uT*1.3 + aS.x*40.), cos(uT*1.1 + aS.y*40.), sin(uT*.9 + aS.z*40.)) * (.004 + .02*halo);
  // dissolve: drift up and out, staggered
  float o = clamp((uOut - r4*.45) / .55, 0., 1.);
  pos += vec3((aS.x-.5)*.9, .35 + aS.y*.9, (aS.z-.5)*1.4) * o*o * 1.3;
  // camera: slow push and a gentle sway
  pos = rotY(pos, .18*sin(uT*.45) * (1. - .6*e));
  float cz = 3.6 - .5*smoothstep(0., A + 1.5, uT);
  float d = max(cz - pos.z, .2), fit = min(1., uAspect / .82);
  gl_Position = vec4(pos.x*2.2*fit/d/uAspect, pos.y*2.2*fit/d, 0., 1.);
  gl_PointSize = uDpr * (1. + .9*aS.z) * 3.2 / d * fit + 3.*halo*uDpr;
  // colour runs across the letter; a light sweeps through after it lands
  float g = clamp(aT.x*.5 + .5, 0., 1.);
  vec3 col = g < .5 ? mix(uC[0], uC[1], g*2.) : mix(uC[1], uC[2], g*2. - 1.);
  float sweep = exp(-pow((aT.x + aT.y*.35 - mix(-1.8, 1.8, clamp((uT - A + .5)/1.4, 0., 1.)))*3., 2.));
  vC = col * (1. + 1.6*sweep) + sweep*.25;
  vA = (halo > .5 ? .18 : .55) * smoothstep(0., .6, uT - r4*.4) * (1. - o) * mix(.55, 1., e);
}`;
    const fs = `precision mediump float; varying vec3 vC; varying float vA;
void main(){ float d = length(gl_PointCoord - .5); float a = smoothstep(.5, .0, d) * vA; gl_FragColor = vec4(vC*a, a); }`;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
    let pr;
    try {
      pr = gl.createProgram();
      gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs));
      gl.bindAttribLocation(pr, 0, 'aT'); gl.bindAttribLocation(pr, 1, 'aS');
      gl.linkProgram(pr); if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(pr));
    } catch (err) { console.warn('intro:', err); end(); return; }
    gl.useProgram(pr);
    const buf = (data, loc, size) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer()); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
    };
    buf(target, 0, 3); buf(seed, 1, 4);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
    const u = n => gl.getUniformLocation(pr, n), uT = u('uT'), uOut = u('uOut'), uAspect = u('uAspect'), uDpr = u('uDpr');

    // the brand's dawn sweep at the chosen hue: deep accent → violet → amber
    const px = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
    const rgb = css => {
      const probe = document.createElement('i'); probe.style.cssText = `display:none;color:${css}`; document.body.append(probe);
      const c = getComputedStyle(probe).color; probe.remove();
      px.clearRect(0, 0, 1, 1); px.fillStyle = c; px.fillRect(0, 0, 1, 1);
      return [...px.getImageData(0, 0, 1, 1).data.slice(0, 3)].map(v => v / 255);
    };
    gl.uniform3fv(u('uC'), new Float32Array([
      ...rgb('oklch(62% .2 var(--hue))'), ...rgb('oklch(68% .22 calc(var(--hue) + 45))'), ...rgb('oklch(82% .15 70)')]));

    const size = () => {
      const dpr = Math.min(devicePixelRatio || 1, 2);
      cv.width = Math.round(innerWidth * dpr); cv.height = Math.round(innerHeight * dpr);
      gl.viewport(0, 0, cv.width, cv.height);
      gl.uniform1f(uAspect, innerWidth / innerHeight); gl.uniform1f(uDpr, dpr);
    };
    size(); addEventListener('resize', size);

    // ---- timeline -------------------------------------------------------
    // the clock starts on the first painted frame and stands still while the tab is hidden
    let t0 = 0, prev = 0, leaving = 0, done = false;   // leaving: time the dissolve began
    const leave = t => { if (!leaving) { leaving = t; root.dataset.intro = 'leaving'; } };
    finish = () => leave(t0 ? (performance.now() - t0) / 1000 : 0);
    box.addEventListener('pointerdown', finish);
    addEventListener('keydown', finish, {once: true});

    function frame(now) {
      if (done) return;
      if (!t0) { t0 = prev = now; box.classList.add('running'); }
      if (now - prev > 200) t0 += now - prev - 16;
      prev = now;
      const t = still >= 0 ? still : (now - t0) / 1000;
      if (t > ASSEMBLE + HOLD && !(still >= 0)) leave(t);
      const out = leaving ? Math.min(1, (t - leaving) / DISSOLVE) : 0;
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform1f(uT, t); gl.uniform1f(uOut, out);
      gl.drawArrays(gl.POINTS, 0, N);
      if (out >= 1) { done = true; removeEventListener('resize', size); end(); return; }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
})();
