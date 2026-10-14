/* Minimal Markdown -> DOM renderer. Builds nodes only (no innerHTML), so model text cannot inject markup. */
(() => {
  const el = (tag, ...kids) => { const n = document.createElement(tag); n.append(...kids); return n; };
  const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+?\*\*|__[^_\n]+?__)|(\*[^*\s][^*\n]*?\*|(?<![\w])_[^_\s][^_\n]*?_(?![\w]))|(~~[^~\n]+?~~)|(\[[^\]\n]+\]\([^)\s]+\))/g;

  function inline(text) {
    const out = []; let last = 0;
    // matchAll clones the regex, so recursive calls cannot reset this loop's lastIndex.
    for (const m of text.matchAll(INLINE)) {
      if (m.index > last) out.push(text.slice(last, m.index));
      const s = m[0];
      if (m[1]) out.push(el('code', s.slice(1, -1)));
      else if (m[2]) out.push(el('strong', ...inline(s.slice(2, -2))));
      else if (m[3]) out.push(el('em', ...inline(s.slice(1, -1))));
      else if (m[4]) out.push(el('del', ...inline(s.slice(2, -2))));
      else {
        const [, label, url] = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(s);
        if (/^https?:\/\//i.test(url)) {
          const a = el('a', label); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; out.push(a);
        } else out.push(s);
      }
      last = m.index + s.length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  const cells = line => line.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  const isRule = l => /^\s*([-*_])(\s*\1){2,}\s*$/.test(l);
  const isTableSep = l => /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(l);
  const aligns = l => cells(l).map(c => c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : c.startsWith(':') ? 'left' : '');
  const cell = (tag, text, align) => { const n = el(tag, ...inline(text)); if (align) n.style.textAlign = align; return n; };
  const startsBlock = l => /^(\s*([-*+]|\d+[.)])\s+|#{1,6}\s|>|```)/.test(l) || isRule(l);

  function render(src) {
    const frag = document.createDocumentFragment();
    const lines = String(src).replace(/\r\n?/g, '\n').split('\n');
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      let m;
      if ((m = /^```(\S*)/.exec(line))) {
        const body = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
        i++; // closing fence; an unfinished fence while streaming simply shows what has arrived
        const code = el('code', body.join('\n')); if (m[1]) code.dataset.lang = m[1];
        frag.append(el('pre', code)); continue;
      }
      if ((m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line))) {
        frag.append(el('h' + Math.min(m[1].length + 2, 6), ...inline(m[2]))); i++; continue;
      }
      if (isRule(line)) { frag.append(el('hr')); i++; continue; }
      if (/^>/.test(line)) {
        const body = [];
        while (i < lines.length && /^>/.test(lines[i])) body.push(lines[i++].replace(/^>\s?/, ''));
        frag.append(el('blockquote', render(body.join('\n')))); continue;
      }
      if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        const head = cells(line), align = aligns(lines[i + 1]), rows = []; i += 2;
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
        const table = el('table', el('thead', el('tr', ...head.map((c, k) => cell('th', c, align[k])))),
          el('tbody', ...rows.map(r => el('tr', ...head.map((_, k) => cell('td', r[k] ?? '', align[k]))))));
        const wrap = el('div', table); wrap.className = 'md-table'; frag.append(wrap); continue;
      }
      if ((m = /^(\s*)([-*+]|\d+[.)])\s+/.exec(line))) {
        const ordered = /\d/.test(m[2]), list = el(ordered ? 'ol' : 'ul');
        if (ordered && parseInt(m[2], 10) !== 1) list.start = parseInt(m[2], 10);
        while (i < lines.length && (m = /^(\s*)([-*+]|\d+[.)])\s+(.*)/.exec(lines[i])) && /\d/.test(m[2]) === ordered) {
          const item = el('li', ...inline(m[3])); i++;
          const nested = [];
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) ) nested.push(lines[i++].replace(/^\s{2,4}/, ''));
          if (nested.length) item.append(render(nested.join('\n')));
          list.append(item);
        }
        frag.append(list); continue;
      }
      const para = [line]; i++;
      while (i < lines.length && lines[i].trim() && !startsBlock(lines[i]) && !(lines[i].includes('|') && isTableSep(lines[i + 1] ?? ''))) para.push(lines[i++]);
      const p = el('p');
      para.forEach((l, k) => { if (k) p.append(document.createElement('br')); p.append(...inline(l.trim())); });
      frag.append(p);
    }
    return frag;
  }

  // Keep the raw text on the node so streamed deltas can be appended and re-rendered.
  function setMarkdown(node, text) {
    node.dataset.raw = text;
    node.classList.add('md');
    node.replaceChildren(render(text));
  }
  const appendMarkdown = (node, delta) => setMarkdown(node, (node.dataset.raw || '') + delta);

  window.MD = { render, setMarkdown, appendMarkdown };
})();
