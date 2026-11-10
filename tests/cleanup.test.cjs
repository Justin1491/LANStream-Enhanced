const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page() {
  class Element {
    constructor() { this.children=[]; this.dataset={}; this.value=''; this.checked=false; this.hidden=false; this.disabled=false; this.classList={toggle(){}}; this.options=[]; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children=items; }
    setAttribute(key,value) { this[key]=value; }
    removeAttribute(key) { delete this[key]; }
    addEventListener(type,fn) { this['on'+type]=fn; }
    showModal() { this.open=true; }
    close() { this.open=false; }
    focus() {}
  }
  const ids = [...fs.readFileSync(require.resolve('../templates/cleanup.html'),'utf8').matchAll(/id="([^"]+)"/g)].map(x=>x[1]);
  const elements=Object.fromEntries(ids.map(id=>[id,new Element()]));
  elements.cleanup.dataset.writes='true'; elements.cleanupScope.value='all'; elements.cleanupType.value='all';
  const calls=[],events={},timers=[],durationResponses=[];
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/cleanup.js'),'utf8'),{
    document:{getElementById:id=>elements[id],querySelector:()=>({content:'csrf'}),querySelectorAll:()=>[],createElement:()=>new Element(),createDocumentFragment:()=>new Element()},
    window:{addEventListener:(type,fn)=>events[type]=fn}, location:{search:'',pathname:'/cleanup'},
    history:{replaceState(){}}, localStorage:{getItem:()=>null,setItem(){}},
    URLSearchParams,URL,AbortController,crypto:{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},
    IntersectionObserver:class{observe(){}disconnect(){}},
    fetch:async(url,options={})=>{calls.push({url,options});return{ok:true,json:async()=>url.includes('/durations')?{job:durationResponses.shift()||null}:{items:[{name:'clip.mp4',rel:'clip.mp4',root_id:'root_1',size:20,mtime:1,kind:'video',duration:12,selection_token:'signed',thumb_url:'/thumbnail/0/clip.mp4',location:'Media',url:'/video/0/clip.mp4'}],total:1,page:1,pages:1,coverage:[{folders:2,files:7}],writes_enabled:true}}},
  });
  return {elements,calls,events,timers,durationResponses};
}
const flush = () => new Promise(resolve=>setImmediate(resolve));

test('cleanup starts with thumbnails off and no image or probe requests',async()=>{
  const p=page();await flush();
  assert.equal(p.elements.cleanupThumbnails.checked,false);
  assert.equal(p.elements.cleanupRows.children.length>0,true);
  assert.equal(p.calls.some(c=>c.url.startsWith('/thumbnail/')||c.url.includes('image-ready')),false);
  assert.equal(p.calls.some(c=>c.options.method==='POST'),false);
  assert.doesNotMatch(p.elements.cleanupCoverage.textContent,/undefined/);
});

test('completed background duration preparation refreshes saved values',async()=>{
  const p=page();await flush();
  const base=p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).length;
  p.durationResponses.push({running:true,ready:0,failed:0,changed:0,checked:0}, {running:false,ready:1,failed:0,changed:0,checked:1});
  p.elements.prepareDurations.onclick();await p.elements.cleanupConfirmAction.onclick();await flush();
  await p.timers.at(-1)();await flush();
  assert.equal(p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).length,base+1);
  assert.match(p.elements.durationStatus.textContent,/1 saved/);
});

test('selection is page-local and changing scope clears it without mutation',async()=>{
  const p=page();await flush();
  p.elements.selectCleanupPage.checked=true;p.elements.selectCleanupPage.onchange();
  assert.match(p.elements.cleanupSelected.textContent,/1 file/);
  assert.equal(p.elements.moveCleanup.disabled,false);
  p.elements.cleanupScope.value='library';p.elements.cleanupScope.onchange();await flush();
  assert.match(p.elements.cleanupSelected.textContent,/0 files/);
  assert.equal(p.elements.moveCleanup.disabled,true);
  assert.equal(p.calls.some(c=>c.options.method==='POST'),false);
});

test('tab changes clear selection and hide thumbnail controls in Trash',async()=>{
  const p=page();await flush();
  p.elements.selectCleanupPage.checked=true;p.elements.selectCleanupPage.onchange();
  p.elements.trashTab.onclick();await flush();
  assert.match(p.elements.cleanupSelected.textContent,/0 files/);
  assert.equal(p.elements.thumbnailsLabel.hidden,true);
  assert.equal(p.elements.moveCleanup.hidden,true);
  assert.equal(p.calls.some(c=>c.url.startsWith('/api/cleanup/trash')),true);
});

test('changing results cannot retain a selection from the old page while loading',async()=>{
  const p=page();await flush();
  p.elements.cleanupScope.onchange();
  p.elements.selectCleanupPage.checked=true;p.elements.selectCleanupPage.onchange();
  assert.match(p.elements.cleanupSelected.textContent,/0 files/);
  await flush();
  assert.equal(p.elements.moveCleanup.disabled,true);
});
