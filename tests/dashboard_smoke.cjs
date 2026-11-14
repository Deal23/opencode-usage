// Lightweight DOM contract smoke test. Real browser layout remains a manual check.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
assert.ok(!/id="(?:fprov|fmodel|from|to)"/.test(html), 'duplicate detail filters remain');
assert.ok(!html.includes('项目 / agent'), 'unsupported search hint remains');
const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    value:'',innerHTML:'',textContent:'',dataset:{},children:[],
    setAttribute(){},addEventListener(){},insertAdjacentHTML(){},
    closest(){return this;},appendChild(){},
  });
  return elements.get(selector);
}
const document = {
  querySelector: element,
  querySelectorAll(selector) { return selector==='.k-value' ? Array.from({length:6},(_,i)=>element('kpi'+i)) : []; },
  createElement:()=>element('style'), head:element('head'),
};
// The obsolete chart has no container in the current template.
const query = document.querySelector;
document.querySelector = selector => selector==='#trend-chart' ? null : query(selector);
const context = vm.createContext({document, console, setTimeout, clearTimeout});
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
vm.runInContext(`
const savedSync=DATA.sync, savedDevices=DATA.devices;
DATA.sync={devices:{'device-a':{name:'device-a',exported_at:'2026-11-12T14:12:57.783123+00:00',received_at:'2026-11-13T05:23:14.309563+00:00',stale:false}}};
DATA.devices=[{id:'device-a',name:'Mac <工作机>'}];
renderSync();
const syncHTML=document.querySelector('#sync-status').innerHTML;
if(!syncHTML.includes('Mac &lt;工作机&gt;') || syncHTML.includes('device-a')) throw Error('device name fallback/escaping failed');
if(!syncHTML.includes('2026/11/12 22:12') || syncHTML.includes('+00:00')) throw Error('readable sync time failed');
if(!syncHTML.includes('未提供检查时间') || syncHTML.includes('<dt>最近检查</dt>')) throw Error('legacy heartbeat display failed');
DATA.sync.devices['device-a'].checked_at='2026-11-13T05:25:00+00:00';
DATA.sync.devices['device-a'].collection_ok=false;
renderSync();
if(!document.querySelector('#sync-status').innerHTML.includes('对方采集失败')) throw Error('collection failure hidden');
if(syncTime('invalid')!=='时间无效' || syncTime(null)!=='未提供') throw Error('invalid sync time failed');
DATA.sync=savedSync;DATA.devices=savedDevices;renderSync();
applyGlobal();
if (DATA.rows.length !== ORIGINAL.rows.length) throw Error('reset lost rows');
document.querySelector('#gprovider').value='does-not-exist';applyGlobal();
if (DATA.rows.length !== 0 || DATA.models.length !== 0) throw Error('empty filter failed');
if (document.querySelector('#activity-chart').innerHTML !== '') throw Error('stale chart');
document.querySelector('#gprovider').value='';
document.querySelector('#gdevice').value='device-a';applyGlobal();
if (DATA.rows.some(r=>!r.device_ids.includes('device-a'))) throw Error('device filter failed');
document.querySelector('#gdevice').value='';applyGlobal();
document.querySelector('#q').value='does-not-exist';render();
if(filtered().length!==0) throw Error('detail search failed');
document.querySelector('#q').value='';render();
`,context);
assert.ok(elements.has('#page-label'));
console.log('Dashboard script/filter smoke test passed');
