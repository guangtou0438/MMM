// Supplementary browser evidence because Archify's DevTools pipe fails on this Windows host.
const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto');
const {pathToFileURL}=require('node:url');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe'});
 try{
  const page=await browser.newPage();
  for(const name of ['architecture','dataflow','run-lifecycle','memory-lifecycle']){
   const file=path.resolve('docs/diagrams/'+name+'.html'), bytes=fs.readFileSync(file);
   const record={kind:'supplementary-browser',artifact:{sha256:crypto.createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length},measurements:[],screenshots:[],visualReview:'pending'};
   for(const [width,height] of [[1440,900],[1600,1000],[1920,1080],[2048,1320]]){
    await page.setViewportSize({width,height});
    await page.goto(pathToFileURL(file).href+'?theme=light');
    await page.evaluate(()=>document.fonts.ready);
    const m=await page.evaluate(()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight}));
    if(m.scrollWidth>width||m.scrollHeight>height)throw new Error(name+JSON.stringify(m));
    record.measurements.push(m);
    if(width===1440||width===2048){
     for(const theme of ['light','dark']){
      await page.goto(pathToFileURL(file).href+'?theme='+theme);await page.evaluate(()=>document.fonts.ready);
      const screenshot=`artifacts/refactor-${name}-${width}-${theme}.png`;
      await page.screenshot({path:screenshot,fullPage:true});record.screenshots.push(screenshot);
     }
    }
   }
   fs.writeFileSync('docs/diagrams/'+name+'.supplementary-browser.json',JSON.stringify(record,null,2));
  }
  console.log('Four diagrams: all four desktop sizes contained; light/dark screenshots captured.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
