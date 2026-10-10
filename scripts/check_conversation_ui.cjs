const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}); const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const titleA='侧栏会话甲第一轮 '+Date.now(), titleB='侧栏会话乙 '+Date.now();
 const send=async text=>{await page.locator('#agent-input').fill(text);await page.locator('#agent-form button[type=submit]').click();await page.locator('#agent-status').filter({hasText:'completed'}).waitFor();await page.waitForFunction(()=>document.querySelector('#agent-answer .message:last-child .content')?.textContent.includes('合成测试回答'));};
 try{
 await page.goto('http://127.0.0.1:8877/');await page.locator('#sidebar-new-chat').click();
 await send(titleA);await send('侧栏会话甲第二轮');
 await page.waitForFunction(()=>document.querySelectorAll('#agent-answer .message').length===4);
 assert.equal(await page.locator('.conversation-link').filter({hasText:titleA}).count(),1);
 await page.locator('#sidebar-new-chat').click();assert.equal(await page.locator('#agent-answer .message').count(),0);
 await send(titleB);
 await page.locator('.conversation-link').filter({hasText:titleA}).click();
 await page.waitForFunction(()=>document.querySelectorAll('#agent-answer .message').length===4);
 await send('侧栏会话甲第三轮');await page.waitForFunction(()=>document.querySelectorAll('#agent-answer .message').length===6);
 assert.equal(await page.locator('#agent-answer').getByText(titleB,{exact:true}).count(),0);
 await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#agent-answer .message').length===6);
 assert.equal(await page.locator('.conversation-link[aria-current=true]').count(),1);
 await page.getByRole('button',{name:'记忆中心',exact:true}).click();assert.equal(await page.locator('.conversation-link[aria-current=true]').count(),0);
 await page.locator('.conversation-link').filter({hasText:titleB}).click();await page.waitForFunction(()=>document.querySelectorAll('#agent-answer .message').length===2);
 await page.locator('#agent-input').fill('生成期间切换');await page.locator('#agent-form button[type=submit]').click();
 await page.locator('#agent-view .chat-working').waitFor({state:'visible'});
 await page.locator('#sidebar-new-chat').click();
 await page.waitForTimeout(2200);assert.equal(await page.locator('#agent-answer .message').count(),0);
 await page.locator('.conversation-link').filter({hasText:titleA}).click();await page.waitForFunction(()=>document.querySelectorAll('#agent-answer .message').length===6);
 for(const width of [1440,768,390]){
 await page.setViewportSize({width,height:1000});
 if(width<760){await page.locator('#mobile-menu').click();await page.locator('.conversation-link').filter({hasText:titleB}).click();await page.waitForFunction(()=>document.querySelectorAll('#agent-answer .message').length===4);assert.equal(await page.locator('#mobile-menu').getAttribute('aria-expanded'),'false');}
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:`artifacts/conversation-sidebar-${width}.png`,fullPage:true});
 }
 await page.locator('#mobile-menu').click();await page.screenshot({path:'artifacts/conversation-sidebar-mobile-menu.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('PASS: grouped conversations, complete multi-turn transcript, continue, reload, memory navigation, switch during streaming, mobile navigation, no horizontal overflow or JS errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
