'use strict';
/* ==========================================================================
   Mist: the day view's silk smoke, spread behind the whole app.
   A fixed canvas under every page (calendar, chat, task, memory). It is drawn
   at a fraction of the screen's resolution (smoke has no edges to lose), at
   about 30 frames a second, only while the tab is visible; with reduced motion
   it draws one still frame. Colours are the look's own tokens: the smoke
   climbs from the accent toward amber, laid over the page (premultiplied).
   ========================================================================== */
(() => {
  const cv = document.getElementById('mist');
  const gl = cv && cv.getContext('webgl', {alpha: true, premultipliedAlpha: true, antialias: false});
  if (!gl) return;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const SCALE = .35;

  const fs = `precision mediump float;
uniform vec2 uRes; uniform float uTime; uniform vec3 uR[5]; uniform float uDark;
float hash(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+45.32); return fract(p.x*p.y); }
float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f); return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y); }
float fbm(vec2 p){ float a=.5,s=0.; for(int i=0;i<5;i++){ s+=a*noise(p); p=p*2.02+11.; a*=.5; } return s; }
vec3 ramp(float t){ t=clamp(t,0.,1.);
  return t<.25?mix(uR[0],uR[1],t/.25):t<.5?mix(uR[1],uR[2],(t-.25)/.25):t<.75?mix(uR[2],uR[3],(t-.5)/.25):mix(uR[3],uR[4],(t-.75)/.25); }
void main(){
  vec2 uv=gl_FragCoord.xy/uRes, p=uv*vec2(uRes.x/uRes.y,1.)*1.3;
  float t=uTime*.03;
  vec2 q=vec2(fbm(p+t),fbm(p+vec2(5.2,1.3)-t));
  float f=fbm(p+2.4*q+vec2(t*.6,-t*.4));
  float strength=mix(.32,.36,uDark);
  float a=smoothstep(.35,.85,f)*strength; vec4 c=vec4(ramp(.15+.6*f)*a,a);
  float b=smoothstep(.72,.98,f)*strength*.7*(.4+.6*uv.y); c=vec4(ramp(.75)*b,b)+c*(1.-b);
  gl_FragColor=c;
}`;
  const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.warn('mist shader:', gl.getShaderInfoLog(s)); return s; };
  const pr = gl.createProgram();
  gl.attachShader(pr, sh(gl.VERTEX_SHADER, 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}'));
  gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(pr); gl.useProgram(pr);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  const u = n => gl.getUniformLocation(pr, n), uRes = u('uRes'), uTime = u('uTime'), uR = u('uR'), uDark = u('uDark');

  // the look's colours, resolved by the browser and flattened to sRGB on a 1×1 canvas
  const px = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const read = (name, under) => {
    const probe = document.createElement('i'); probe.style.cssText = `display:none;color:var(${name})`; document.body.append(probe);
    const css = getComputedStyle(probe).color; probe.remove();
    px.globalCompositeOperation = 'copy'; px.fillStyle = `rgb(${under})`; px.fillRect(0, 0, 1, 1);
    px.globalCompositeOperation = 'source-over'; px.fillStyle = css; px.fillRect(0, 0, 1, 1);
    return [...px.getImageData(0, 0, 1, 1).data.slice(0, 3)];
  };
  const mixc = (a, b, k) => a.map((v, j) => v + (b[j] - v) * k);
  function theme() {
    const dark = document.documentElement.dataset.theme === 'dark';
    const page = read('--page', dark ? [0, 0, 0] : [255, 255, 255]), ink = read('--ink', page);
    const accent = read('--accent', page), amber = read('--amber', page);
    const stops = dark ? [mixc(page, accent, .3), mixc(page, accent, .65), accent, amber, mixc(amber, ink, .45)]
      : [mixc(page, accent, .25), mixc(page, accent, .55), accent, mixc(accent, amber, .6), amber];
    gl.uniform3fv(uR, new Float32Array(stops.flat().map(v => v / 255)));
    gl.uniform1f(uDark, dark ? 1 : 0);
  }

  function size() {
    const k = Math.min(devicePixelRatio || 1, 2) * SCALE;
    cv.width = Math.max(1, Math.round(innerWidth * k)); cv.height = Math.max(1, Math.round(innerHeight * k));
    gl.viewport(0, 0, cv.width, cv.height); gl.uniform2f(uRes, cv.width, cv.height);
  }
  const t0 = performance.now();
  let last = 0, still = false;
  function draw(now) { gl.uniform1f(uTime, reduce.matches ? 0 : (now - t0) / 1000); gl.drawArrays(gl.TRIANGLES, 0, 3); }
  function loop(now) {
    requestAnimationFrame(loop);
    if (document.hidden) return;
    if (reduce.matches) { if (!still) { still = true; draw(now); } return; }
    still = false;
    if (now - last < 33) return;   // smoke drifts; thirty frames a second is plenty
    last = now; draw(now);
  }
  size(); theme();
  addEventListener('resize', () => { size(); still = false; });
  new MutationObserver(() => requestAnimationFrame(() => { theme(); still = false; }))
    .observe(document.documentElement, {attributes: true, attributeFilter: ['data-hue', 'data-theme']});
  requestAnimationFrame(loop);
})();
