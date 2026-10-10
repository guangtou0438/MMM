const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('calendar_agent/static/app.js','utf8');
const start = source.indexOf("for(const id of ['home-input','session-input']){");
const end = source.indexOf("function showThemeState",start);
assert.ok(start>=0 && end>start);
const inputs = {};
for (const id of ['home-input','session-input']) {
  const handlers = {}; const submit = {disabled:false};
  inputs[id] = {value:'你好',disabled:false,handlers,submit,count:0,
    addEventListener(name,fn){handlers[name]=fn;},
    form:{querySelector(){return submit;},requestSubmit(){inputs[id].count++;}}};
}
vm.runInNewContext(source.slice(start,end),{$:id=>inputs[id]});
for(const input of Object.values(inputs)) {
  const enter = extra=>input.handlers.keydown({key:'Enter',preventDefault(){},...extra});
  enter({shiftKey:true});assert.equal(input.count,0);
  input.handlers.compositionstart();enter({});assert.equal(input.count,0);
  input.handlers.compositionend();enter({isComposing:true});enter({keyCode:229});assert.equal(input.count,0);
  input.submit.disabled=true;enter({});assert.equal(input.count,0);
  input.submit.disabled=false;input.value='  ';enter({});assert.equal(input.count,0);
  input.value='发送';enter({});assert.equal(input.count,1);
  input.disabled=true;enter({});assert.equal(input.count,1);
}
console.log('Both composers: Enter, Shift+Enter, IME, blank and disabled checks passed.');
