'use strict';
// Colour, the ai_pet way: one hue and a day/night switch. Runs before first paint.
// An older choice of look carries over: 手帐 was blue by day, 森林 emerald by night.
try {
  let look = JSON.parse(localStorage.getItem('mmm-look') || 'null');
  if (!look || typeof look.hue !== 'number') {
    const world = localStorage.getItem('mmm-world');
    look = world === 'forest' ? {hue: 162, night: true}
      : world === 'journal' ? {hue: 260, night: false}
      : {hue: 260, night: matchMedia('(prefers-color-scheme: dark)').matches};
    localStorage.setItem('mmm-look', JSON.stringify(look));
  }
  if (look.hue === 294) { look.hue = 293; localStorage.setItem('mmm-look', JSON.stringify(look)); }   // 淡紫 was a twin of 紫罗兰
  document.documentElement.style.setProperty('--hue', look.hue);
  document.documentElement.dataset.hue = look.hue;
  document.documentElement.dataset.theme = look.night ? 'dark' : 'light';
} catch { document.documentElement.dataset.theme = 'light'; }
