const { chromium } = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const crypto = require('node:crypto');
(async () => {
  const target = path.join(__dirname, 'mmm-architecture.html');
  const bytes = fs.readFileSync(target);
  const browser = await chromium.launch({headless:true, executablePath:'C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe'});
  try {
    const page = await browser.newPage();
    const record = {kind:'supplementary-browser', artifact:{sha256:crypto.createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length}, measurements:[], screenshots:[]};
    for (const [width,height] of [[1440,900],[1600,1000],[1920,1080],[2048,1320]]) {
      await page.setViewportSize({width,height});
      await page.goto(pathToFileURL(target).href+'?theme=light');
      await page.evaluate(()=>document.fonts.ready);
      await page.waitForTimeout(400);
      record.measurements.push(await page.evaluate(()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,theme:document.documentElement.dataset.theme})));
      if(width===1440||width===2048) {
        for(const theme of ['light','dark']) {
          await page.goto(pathToFileURL(target).href+'?theme='+theme);
          await page.evaluate(()=>document.fonts.ready);
          await page.waitForTimeout(300);
          const name=`mmm-${width}-${theme}.png`;
          await page.screenshot({path:path.join(__dirname,name),fullPage:true});
          record.screenshots.push(name);
        }
      }
    }
    record.buttons=await page.getByRole('button').allTextContents();
    fs.writeFileSync(path.join(__dirname,'supplementary-browser.json'),JSON.stringify(record,null,2));
    console.log(JSON.stringify(record,null,2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
