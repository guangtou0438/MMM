const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
 const base=process.env.MMM_TEST_URL||'http://127.0.0.1:8877';
 await page.goto(base+'/');
 for(const [kind,content] of [['preference','喜欢清淡的食物。原话为：我吃饭真的不能太油。'],['preference','通常晚上有空。'],['profile','用户提到自己的城市。持久事实：常住墨尔本。原话为：我住墨尔本。'],['episodic','计划于9月19日看电影。用户于9月再次说"19号出去看电影"。原话为：19号出去看电影。不确定性：月份为推断。']])await page.request.post(base+'/api/memories',{data:{kind,content}});
 await page.getByRole('button',{name:'记忆中心',exact:true}).click();
 await page.getByText('你喜欢清淡的食物，也偏好晚上安排活动。',{exact:true}).waitFor({timeout:30000});
 assert.equal(await page.locator('.memory-portrait-card').count(),5);
 assert.equal(await page.locator('#memory-view input, #memory-view textarea, #memory-view form, #memory-view details').count(),0);
 assert(!(await page.locator('#memory-view').innerText()).includes('原话为'));
 assert(!(await page.locator('#memory-view').innerText()).includes('管理'));
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:`artifacts/memory-portrait-${width}.png`,fullPage:true});}
 const ready=await (await page.request.post(base+'/api/memory/overview/status',{data:{}})).json();
 let polls=0;
 await page.route('**/api/memory/overview/status',route=>route.fulfill({json:++polls<3?{status:'pending',slow:true}:ready}));
 await page.reload();
 await page.getByText('模型回复较慢，仍在后台整理。你可以先去聊天，稍后回来查看。',{exact:true}).waitFor();
 await page.getByText('已根据当前记忆自动整理',{exact:true}).waitFor();
 assert.equal(polls,3);
 await page.unroute('**/api/memory/overview/status');
 await page.route('**/api/memory/overview/status',route=>route.fulfill({json:{status:'failed'}}));
 await page.reload();await page.getByRole('button',{name:'重新整理',exact:true}).waitFor();
 await page.unroute('**/api/memory/overview/status');
 await page.getByRole('button',{name:'重新整理',exact:true}).click();
 await page.getByText('已根据当前记忆自动整理',{exact:true}).waitFor();
 assert.deepEqual(errors,[]);console.log('PASS: automatic portrait, no management or editing controls, no raw quotes, responsive layout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
