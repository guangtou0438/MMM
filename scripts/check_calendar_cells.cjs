const { chromium } = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe' });
  try {
    const page = await browser.newPage();
    // Replace only this browser's calendar response; never write user tasks.
    await page.route('**/api/calendar?*', async route => {
      const start = new URL(route.request().url()).searchParams.get('start');
      await route.fulfill({ json: Array.from({ length: 8 }, (_, i) => ({
        id: 'cell-test-' + i, start, allDay: true,
        title: '测试长标题：有很多任务时日期格子仍然保持一致 ' + i,
      })) });
    });
    for (const width of [1440, 1024, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto('http://127.0.0.1:8765/');
      await page.locator('.fc-dayGridMonth-button').click();
      await page.locator('.fc-zoomReset-button').click();
      const measure = () => page.locator('.fc-daygrid-day').first().evaluate(cell => ({
        width: cell.getBoundingClientRect().width,
        height: cell.getBoundingClientRect().height,
        font: parseFloat(getComputedStyle(document.querySelector('.fc-col-header-cell-cushion')).fontSize),
      }));
      const before = await measure();
      for (const count of [14, 7, 3, 1]) {
        await page.locator('.fc-zoomIn-button').click();
        assert.equal(await page.locator('.fc-daygrid-day').count(), count);
        const after = await measure();
        assert.ok(after.height > before.height && after.font > before.font, JSON.stringify({ before, after }));
        const viewport = await page.locator('.fc-view-harness').evaluate(el => ({width:el.clientWidth, scroll:el.scrollWidth}));
        assert.ok(viewport.scroll <= viewport.width + 1, 'Range fits without horizontal scrolling');
      }
      assert.ok(await page.locator('.fc-zoomIn-button').isDisabled());
      await page.reload();
      await page.waitForFunction(() => document.querySelectorAll('.fc-daygrid-day').length === 1);
      for (const count of [3, 7, 14, 42]) {
        await page.locator('.fc-zoomOut-button').click();
        assert.equal(await page.locator('.fc-daygrid-day').count(), count);
      }
      assert.ok(await page.locator('.fc-zoomOut-button').isDisabled());
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No page overflow');
      for (let month = 0; month < 3; month++) {
        await page.waitForFunction(() => document.querySelectorAll('.fc-daygrid-day').length === 42);
        await page.waitForTimeout(250);
        const dimensions = await page.locator('.fc-daygrid-day').evaluateAll(cells => cells.map(cell => {
          const { width, height } = cell.getBoundingClientRect();
          return { width, height };
        }));
        for (const axis of ['width', 'height']) {
          const values = dimensions.map(cell => cell[axis]);
          assert.ok(Math.max(...values) - Math.min(...values) <= 1.1, `${width}: uneven ${axis}: ${values}`);
        }
        assert.ok(await page.locator('.fc-daygrid-more-link').count(), 'Overflow tasks remain accessible');
        const visibleTasks = await page.locator('.fc-daygrid-day').first().locator('.fc-daygrid-event-harness:not(.fc-daygrid-event-harness-abs) .fc-event').count();
        assert.ok(visibleTasks >= 2, 'Month shows at least two task rows before collapsing overflow: ' + visibleTasks);
        assert.ok(dimensions[0].height >= 59, 'Cells retain usable height on small screens');
        await page.locator('.fc-daygrid-more-link').first().click();
        await page.locator('.fc-more-popover').waitFor({ state: 'visible' });
        assert.equal(await page.locator('.fc-more-popover .fc-event').count(), 8);
        await page.locator('.fc-toolbar-title').click();
        console.log(JSON.stringify({ viewport: width, month, cell: dimensions[0] }));
        await page.locator('.fc-next-button').click();
      }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
