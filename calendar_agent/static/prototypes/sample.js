'use strict';
/* Sample data shared by the three calendar prototypes. Every day gets the same
   made-up tasks every time, so the prototypes can be compared side by side. */
(() => {
  const POOL = [['周会', '工作'], ['设计评审', '工作'], ['写周报', '工作'], ['项目上线', '工作'], ['客户电话', '工作'], ['健身', '生活'], ['跑步 5km', '生活'], ['读书会', '学习'], ['学日语', '学习'], ['牙医复诊', '生活'], ['给妈妈打电话', '家人'], ['交房租', '生活'], ['朋友聚餐', '朋友'], ['看展', '朋友'], ['机场接人', '家人'], ['整理照片', '生活'], ['买菜', '生活'], ['电影', '朋友']];
  const rng = s => () => { s |= 0; s = s + 0x6D2B79F5 | 0; let t = Math.imul(s ^ s >>> 15, 1 | s); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  function tasksOf(d) {
    const r = rng(d.getFullYear() * 1000 + d.getMonth() * 40 + d.getDate()), wk = d.getDay() % 6 === 0;
    let n = Math.floor(Math.pow(r(), 1.6) * (wk ? 3 : 6));
    if (r() > .93) n += 3;
    const pool = POOL.slice().sort(() => r() - .5);
    return pool.slice(0, n).map(([title, tag]) => { const h = 8 + Math.floor(r() * 13); return {time: `${String(h).padStart(2, '0')}:${r() > .5 ? '30' : '00'}`, title, tag}; }).sort((a, b) => a.time.localeCompare(b.time));
  }
  window.Sample = {
    rng, tasksOf,
    CN_MONTH: ['一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月'],
    EN_MONTH: ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'],
    WEEK: ['日', '一', '二', '三', '四', '五', '六'],
    EN_WEEK: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
    dayOfYear: d => Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - new Date(d.getFullYear(), 0, 1)) / 864e5) + 1,
  };
})();
