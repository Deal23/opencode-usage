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
    value:'',innerHTML:'',textContent:'',dataset:{},children:[],open:false,
    setAttribute(){},addEventListener(){},insertAdjacentHTML(){},
    closest(){return this;},appendChild(){},showModal(){this.open=true;},close(){this.open=false;},
    querySelector:element,getBoundingClientRect(){return {left:0,width:850};},setPointerCapture(){},releasePointerCapture(){},
  });
  return elements.get(selector);
}
const document = {
  querySelector: element,
  querySelectorAll(selector) {
    if(selector==='.k-value') return Array.from({length:6},(_,i)=>element('kpi'+i));
    if(selector==='#activity-controls button') return ['0','30','7'].map(days=>{const button=element('period'+days);button.dataset.days=days;return button;});
    return [];
  },
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
const saved=DATA;
DATA={...DATA,rows:[
  {d:'2026-01-02',sid:'s1',title:'<script>',tot:10,cost:1,cost_missing:false,cr:1,prov:'p',model:'m'},
  {d:'2026-01-02',sid:'s1',title:'<script>',tot:5,cost:0,cost_missing:true,cr:0,prov:'p',model:'m2'},
  {d:'2026-01-02',sid:'s2',title:'other',tot:3,cost:2,cost_missing:false,cr:0,prov:'p',model:'m'}
],activity_daily:[{date:'2026-01-01',total:0,cost:0},{date:'2026-01-02',total:18,cost:3},{date:'2026-01-10',total:0,cost:0}],activity_hours:[]};
activitySpan=0;activityOffset=0;drawActivity();
const chartElement=document.querySelector('#activity-chart'), hit={closest:()=>({dataset:{day:'2026-01-02'}})};
chartElement.onwheel({clientX:60,deltaY:-1,preventDefault(){}});
if(activitySpan!==7) throw Error('wheel zoom failed');
chartElement.onpointerdown({button:0,clientX:500,shiftKey:false,pointerId:1,target:hit});
chartElement.onpointermove({clientX:396});chartElement.onpointerup({clientX:396,pointerId:1});
if(activityOffset!==1) throw Error('drag pan failed');
const chart=document.querySelector('#activity-chart').innerHTML;
if((chart.match(/data-day=/g)||[]).length!==7 || !chart.includes('2026-01-02') || !chart.includes('2026-01-08')) throw Error('zoom and pan failed');
chartElement.onpointerdown({button:0,clientX:80,shiftKey:false,pointerId:1,target:hit});
chartElement.onpointerup({clientX:80,pointerId:1});
if(!document.querySelector('#activity-dialog').open) throw Error('dialog did not open');
if(!document.querySelector('#activity-dialog-title').textContent.includes('2026-01-02')) throw Error('pointer click selected wrong date');
let details=document.querySelector('#activity-day-detail').innerHTML;
if(!details.includes('&lt;script&gt;') || details.includes('<script>') || !details.includes('15') || !details.includes('1 条缺价')) throw Error('daily session details failed');
if(!details.includes('p / m · 66.7%') || !details.includes('p / m2 · 33.3%')) throw Error('daily model shares failed');
if(details.indexOf('&lt;script&gt;')>details.indexOf('other')) throw Error('sessions not ranked by daily tokens');
DATA={...DATA,rows:DATA.rows.filter(r=>r.sid==='s2'),activity_daily:[{date:'2026-01-02',total:3,cost:2}]};
drawActivity();details=document.querySelector('#activity-day-detail').innerHTML;
if(details.includes('&lt;script&gt;') || !details.includes('other')) throw Error('selected-day details ignored filter');
let prevented=false;
document.querySelector('#activity-chart').onkeydown({key:'Enter',preventDefault:()=>{prevented=true;},target:{closest:()=>({dataset:{day:'2026-01-02'}})}});
if(!prevented || !document.querySelector('#activity-day-detail').innerHTML.includes('other')) throw Error('keyboard day selection failed');
showActivityDay('2026-01-03');
if(!document.querySelector('#activity-day-detail').innerHTML.includes('当天没有匹配')) throw Error('empty day failed');
document.querySelector('#activity-close').onclick();
if(document.querySelector('#activity-dialog').open) throw Error('dialog did not close');
DATA={...DATA,activity_daily:[{date:'2026-01-01',total:0,cost:0},{date:'2026-01-10',total:0,cost:0}]};activitySpan=0;activityOffset=0;drawActivity();
document.querySelectorAll('#activity-controls button')[2].onclick();
if(activitySpan!==7 || activityOffset!==3) throw Error('week preset failed');
document.querySelectorAll('#activity-controls button')[0].onclick();
if(activitySpan!==10 || activityOffset!==0) throw Error('all preset failed');
chartElement.onpointerdown({button:0,clientX:205,shiftKey:true,pointerId:1,target:hit});
chartElement.onpointermove({clientX:422.5});chartElement.onpointerup({clientX:422.5,pointerId:1});
if(activityOffset!==2 || activitySpan!==4) throw Error('box zoom failed');
DATA={...DATA,rows:[],activity_daily:[],activity_hours:[]};drawActivity();
if(document.querySelector('#activity-chart').innerHTML!=='') throw Error('empty filtered window failed');
DATA=saved;activitySpan=0;activityOffset=0;activitySelected='';applyGlobal();
const models=Array.from({length:106},(_,i)=>({prov:'p',model:i===0?'<unsafe>':'model'+i,total:106-i,cost:i,input:0,output:0,cache:0,messages:1,share:1}));
DATA={...DATA,models,projects:[],sessions:[]};tablePages.mtab=0;renderSummaries();renderModelDistribution('#model-distribution','total',n=>nf(n));renderModelDistribution('#model-cost-distribution','cost',n=>nf(n));
if((document.querySelector('#mtab tbody').innerHTML.match(/<tr>/g)||[]).length!==100 || document.querySelector('#mtab-pagination').hidden) throw Error('model pagination failed');
document.querySelector('#mtab-next').onclick();
if((document.querySelector('#mtab tbody').innerHTML.match(/<tr>/g)||[]).length!==6) throw Error('second model page failed');
if(!document.querySelector('#model-distribution').innerHTML.includes('其他模型') || !document.querySelector('#model-distribution').innerHTML.includes('distribution-track')) throw Error('token distribution failed');
if(!document.querySelector('#model-cost-distribution').innerHTML.includes('其他模型')) throw Error('cost distribution failed');
document.querySelector('#model-distribution').onclick({target:{closest:()=>({dataset:{modelIndex:'0'}})}});
if(!document.querySelector('#model-dialog').open || !document.querySelector('#model-dialog-rows').innerHTML.includes('&lt;unsafe&gt;')) throw Error('model detail failed');
document.querySelector('#model-dialog-close').onclick();
document.querySelector('#model-cost-distribution').onclick({target:{closest:()=>({dataset:{modelIndex:'5'}})}});
if((document.querySelector('#model-dialog-rows').innerHTML.match(/<tr>/g)||[]).length!==101 || document.querySelector('#model-dialog-rows').innerHTML.includes('model105</td>')) throw Error('pie other detail failed');
document.querySelector('#model-dialog-close').onclick();
DATA={...DATA,models:[{prov:'provider-a',model:'shared',total:60,cost:2},{prov:'provider-b',model:'SHARED',total:50,cost:3},{prov:'provider-c',model:'other',total:100,cost:4}]};
renderModelDistribution('#model-distribution','total',n=>nf(n));
if(modelDistributionGroups('total')[0].value!==110 || modelDistributionGroups('cost')[0].value!==5) throw Error('model aggregation failed');
document.querySelector('#model-distribution').onclick({target:{closest:()=>({dataset:{modelIndex:'0'}})}});
if(!document.querySelector('#model-dialog-rows').innerHTML.includes('provider-a') || !document.querySelector('#model-dialog-rows').innerHTML.includes('provider-b')) throw Error('aggregated provider detail failed');
document.querySelector('#model-dialog-close').onclick();
DATA={...DATA,models};
if(modelName('opencode','deepseek-v4.1-flash-free')!=='deepseek-flash' || modelName('other','deepseek-v4.1-flash-free')!=='deepseek-v4.1-flash-free') throw Error('deepseek distribution alias failed');
DATA={...DATA,models:[{prov:'opencode',model:'deepseek-v4.1-flash-free',total:40,cost:0},{prov:'deepseek',model:'deepseek-flash',total:60,cost:1}]};
if(modelDistributionGroups('total').length!==1 || modelDistributionGroups('total')[0].value!==100 || modelDistributionGroups('total')[0].rows.length!==2) throw Error('deepseek provider aggregation failed');
DATA={...DATA,models};
if(compactToken(1500)!=='1.5K' || compactToken(2000000)!=='2M' || compactToken(3000000000)!=='3B') throw Error('token unit formatting failed');
summarySort.mtab={key:'cost',asc:false};tablePages.mtab=0;renderSummaries();
if(!document.querySelector('#mtab tbody').innerHTML.includes('<td>1</td><td>1</td>') || !document.querySelector('#mtab tbody').innerHTML.includes('model105')) throw Error('model ranking sort or numbering failed');
`,context);
assert.ok(elements.has('#page-label'));
console.log('Dashboard script/filter smoke test passed');
