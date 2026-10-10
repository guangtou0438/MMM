const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const titlePlan='安排周五的活动 '+Date.now(), titleChat='聊聊一个想法 '+Date.now();
 const send=async(text,kind)=>{await page.locator('#agent-input').fill(text);const request=page.waitForRequest(r=>r.url().endsWith('/api/agent/runs')&&r.method()==='POST');await page.locator('#agent-form button[type=submit]').click();const data=(await request).postDataJSON();assert.equal(data.kind,kind);await page.locator('#agent-status').filter({hasText:'completed'}).waitFor();await page.waitForFunction(()=>document.querySelector('#agent-answer .message:last-child .content')?.textContent.includes('合成测试回答'));return data.session_key;};
 try{
 await page.goto('http://127.0.0.1:8877/');await page.locator('#agent-input').waitFor();
 const planKey=await send(titlePlan,'complex');await page.locator('#agent-input').fill('安排页的草稿');
 await page.locator('#chat-button').click();assert(await page.locator('#chat-view').isVisible());assert(!(await page.locator('#calendar').isVisible()));assert(!(await page.locator('#create').isVisible()));assert(!(await page.locator('#preferences-button').isVisible()));
 assert.equal(await page.locator('#agent-input').inputValue(),'');assert.equal(await page.locator('.conversation-link').filter({hasText:titlePlan}).count(),0);
 const chatKey=await send(titleChat,'chat');assert.notEqual(planKey,chatKey);await page.locator('#agent-input').fill('聊天页的草稿');
 await page.locator('#home-button').click();await page.waitForFunction(()=>document.querySelector('#agent-view').dataset.mode==='planner');assert.equal(await page.locator('#agent-input').inputValue(),'安排页的草稿');assert(await page.locator('#calendar').isVisible());
 await page.locator('#chat-button').click();assert.equal(await page.locator('#agent-input').inputValue(),'聊天页的草稿');assert.equal(await page.locator('#agent-answer .message.user').innerText(),'你\n'+titleChat);
 await page.reload();await page.locator('#chat-view #agent-answer .message').first().waitFor();assert.equal(await page.locator('.conversation-link').filter({hasText:titleChat}).count(),1);
 for(const width of [1440,768,390]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));const input=await page.locator('#agent-form').boundingBox();assert(input.y+input.height<=902);await page.screenshot({path:`artifacts/workspace-chat-${width}.png`,fullPage:true});}
 await page.locator('#mobile-menu').click();await page.locator('#home-button').click();assert(await page.locator('#calendar').isVisible());assert(!(await page.locator('#chat-view').isVisible()));
 await page.setViewportSize({width:1440,height:1000});await page.locator('.conversation-link').filter({hasText:titlePlan}).waitFor();await page.waitForTimeout(300);await page.screenshot({path:'artifacts/workspace-calendar.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('PASS: independent pages, role payloads, histories, drafts, reload, mobile navigation, visible composer, no JS errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
