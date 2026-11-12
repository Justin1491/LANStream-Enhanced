const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page({search='', roots=1, fetchHook}={}) {
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
  elements.cleanup.dataset.writes='true'; elements.cleanupRecursive.checked=true; elements.cleanupType.value='all';
  elements.cleanupRoot.options=[{value:'',textContent:'Choose a library',dataset:{}},{value:'all',textContent:'All accessible libraries',dataset:{}}, ...Array.from({length:roots},(_,i)=>({value:'root_'+(i+1),textContent:'Media '+(i+1),dataset:{index:String(i)}}))];
  const calls=[],events={},timers=[],durationResponses=[],historyCalls=[];
  const location={search,pathname:'/cleanup'};
  const response=(url)=>url.includes('/durations')?{job:durationResponses.shift()||null}:url.startsWith('/api/cleanup/groups')?groupResult():url.startsWith('/api/cleanup/folders')?{items:[{name:'Clips',rel:'Clips'}],total:1,page:1,pages:1}:fileResult();
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/cleanup.js'),'utf8'),{
    document:{getElementById:id=>elements[id],querySelector:()=>({content:'csrf'}),querySelectorAll:()=>[],createElement:()=>new Element(),createDocumentFragment:()=>new Element()},
    window:{addEventListener:(type,fn)=>events[type]=fn}, location,
    history:Object.fromEntries(['replaceState','pushState'].map(method=>[method,(_state,_title,url)=>{historyCalls.push({method,url});location.search=url.slice(url.indexOf('?'));}])), localStorage:{getItem:()=>null,setItem(){}},
    URLSearchParams,URL,AbortController,crypto:{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},
    IntersectionObserver:class{observe(){}disconnect(){}},
    fetch:async(url,options={})=>{calls.push({url,options});const result=fetchHook?await fetchHook(url,options,()=>response(url)):response(url);return{ok:true,json:async()=>result};},
  });
  return {elements,calls,events,timers,durationResponses,historyCalls,location};
}
function groupResult(overrides={}) { return {items:[{group_key:'40',value:40,count:2,total_size:80},{group_key:'20',value:20,count:2,total_size:40}],total:2,page:1,pages:1,missing_durations:2,coverage:[{folders:2}],writes_enabled:true,...overrides}; }
function fileResult(name='clip.mp4') { return {items:[{name,rel:name,root_id:'root_1',size:20,mtime:1,kind:'video',duration:12,selection_token:'signed',thumb_url:'/thumbnail/0/clip.mp4',location:'Media',url:'/video/0/clip.mp4',folder_url:'/browse/0/'}],total:1,page:1,pages:1,coverage:[{folders:2,files:7}],writes_enabled:true}; }
function deferred() { let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject}; }
const query = call => new URLSearchParams(call.url.split('?')[1]);
const flush = () => new Promise(resolve=>setImmediate(resolve));

test('folder-first cleanup starts with indexed size groups and recursive scope',async()=>{
  const p=page();await flush();
  const request=p.calls.find(c=>c.url.startsWith('/api/cleanup/groups'));
  assert.ok(request,'default view requests groups');
  const query=new URLSearchParams(request.url.split('?')[1]);
  assert.equal(query.get('scope'),'subtree');assert.equal(query.get('root'),'root_1');
  assert.equal(query.get('group_by'),'size');
  assert.equal(p.calls.some(c=>c.options.method==='POST'),false);
});

test('legacy matching-size links become group views and explicit folder-only scope is retained',async()=>{
  const p=page({search:'?root=0&relative=Clips&scope=folder&matching_sizes=1'});await flush();
  const request=p.calls.find(c=>c.url.startsWith('/api/cleanup/groups'));
  assert.ok(request);assert.equal(new URLSearchParams(request.url.split('?')[1]).get('scope'),'folder');
});

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
  p.elements.cleanupRecursive.checked=false;p.elements.cleanupRecursive.onchange();await flush();
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
  p.elements.cleanupRecursive.onchange();
  p.elements.selectCleanupPage.checked=true;p.elements.selectCleanupPage.onchange();
  assert.match(p.elements.cleanupSelected.textContent,/0 files/);
  await flush();
  assert.equal(p.elements.moveCleanup.disabled,true);
});


test('multiple libraries require a location choice without automatically querying all roots',async()=>{
  const p=page({roots:2});await flush();
  assert.equal(p.calls.length,0);assert.equal(p.elements.cleanupChooseLocation.hidden,false);
  p.elements.cleanupChooseLocationButton.onclick();await flush();
  assert.equal(p.elements.cleanupFolderDialog.open,true);assert.equal(p.calls.length,0);
  p.elements.cleanupRoot.value='root_2';p.elements.cleanupRoot.onchange();await flush();
  assert.equal(query(p.calls.at(-1)).get('root'),'root_2');
  p.elements.folderPickerChoose.onclick();await flush();
  const groups=p.calls.find(c=>c.url.startsWith('/api/cleanup/groups'));
  assert.equal(query(groups).get('root'),'root_2');assert.equal(query(groups).get('scope'),'subtree');
});

test('folder picker keeps an explicit nonrecursive choice when changing library and folder',async()=>{
  const p=page({search:'?root=0&scope=folder',roots:2});await flush();
  p.elements.chooseFolder.onclick();await flush();
  p.elements.cleanupRoot.value='root_2';p.elements.cleanupRoot.onchange();await flush();
  p.elements.folderPickerFolders.children[0].children[0].onclick();await flush();
  p.elements.folderPickerChoose.onclick();await flush();
  const latest=p.calls.filter(c=>c.url.startsWith('/api/cleanup/groups')).at(-1);
  assert.equal(query(latest).get('scope'),'folder');assert.equal(query(latest).get('relative'),'Clips');
  assert.equal(query(latest).get('root'),'root_2');assert.equal(p.elements.cleanupRecursive.checked,false);
});

test('switching group clears signed selections immediately and requests the exact group key',async()=>{
  const p=page();await flush();p.elements.selectCleanupPage.checked=true;p.elements.selectCleanupPage.onchange();
  p.elements.cleanupGroupList.children[1].onclick();
  assert.match(p.elements.cleanupSelected.textContent,/0 files/);assert.equal(p.elements.moveCleanup.disabled,true);
  await flush();assert.equal(query(p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).at(-1)).get('group_key'),'20');
});

test('duration groups explain missing saved values and only explicit preparation sends a POST',async()=>{
  const p=page();await flush();p.elements.cleanupByDuration.onclick();await flush();
  assert.equal(p.elements.cleanupMissingDurations.hidden,false);assert.match(p.elements.cleanupMissingDurations.textContent,/2 videos.*excluded/);
  assert.equal(query(p.calls.filter(c=>c.url.startsWith('/api/cleanup/groups')).at(-1)).get('group_by'),'duration');
  assert.equal(p.calls.some(c=>c.options.method==='POST'),false);
  p.elements.prepareDurations.onclick();assert.match(p.elements.cleanupConfirmDescription.textContent,/filters do not limit/);
});

test('filename search is forwarded to both summaries and whole group member reads',async()=>{
  const p=page();await flush();p.elements.cleanupQuery.value='Alpine';p.elements.cleanupQuery.oninput();await p.timers.at(-1)();await flush();
  const groups=p.calls.filter(c=>c.url.startsWith('/api/cleanup/groups')).at(-1), files=p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).at(-1);
  assert.equal(query(groups).get('q'),'Alpine');assert.equal(query(files).get('q'),'Alpine');assert.equal(query(files).get('group_key'),'40');
  assert.match(p.elements.cleanupCompareSubtitle.textContent,/All group members shown/);
});

test('Trash has its own search and returning to the library restores the group query',async()=>{
  const p=page({search:'?q=Alpine'});await flush();p.elements.trashTab.onclick();await flush();
  assert.equal(p.elements.cleanupQuery.value,'');assert.equal(query(p.calls.filter(c=>c.url.startsWith('/api/cleanup/trash')).at(-1)).get('q'),'');
  p.elements.cleanupQuery.value='renamed';p.elements.cleanupQuery.oninput();await p.timers.at(-1)();await flush();
  p.elements.libraryTab.onclick();await flush();assert.equal(p.elements.cleanupQuery.value,'Alpine');
  p.elements.trashTab.onclick();await flush();assert.equal(p.elements.cleanupQuery.value,'renamed');
});

test('legacy library and all-scope URLs remain available',async()=>{
  const p=page({search:'?scope=all&tab=library',roots:2});await flush();
  assert.equal(p.calls.some(c=>c.url.startsWith('/api/cleanup/groups')),false);
  assert.equal(query(p.calls.find(c=>c.url.startsWith('/api/cleanup/files'))).get('scope'),'all');
  assert.equal(p.elements.allTab['aria-pressed'],'true');
});

test('navigation pushes history; normalized results and thumbnails do not; popstate reloads scope',async()=>{
  const p=page();await flush();p.elements.cleanupRecursive.checked=false;p.elements.cleanupRecursive.onchange();await flush();
  assert.equal(p.historyCalls.filter(c=>c.method==='pushState').length,1);
  p.elements.cleanupThumbnails.checked=true;p.elements.cleanupThumbnails.onchange();await flush();
  assert.equal(p.historyCalls.filter(c=>c.method==='pushState').length,1);
  p.location.search='?root=root_1&relative=Trips&scope=subtree&group_by=duration';p.events.popstate();await flush();
  const latest=p.calls.filter(c=>c.url.startsWith('/api/cleanup/groups')).at(-1);
  assert.equal(query(latest).get('relative'),'Trips');assert.equal(query(latest).get('group_by'),'duration');assert.equal(p.elements.cleanupRecursive.checked,true);
});

test('late group summary cannot replace newer groups or issue member reads',async()=>{
  const delayed=deferred();let count=0;
  const p=page({fetchHook:(url,_options,next)=>url.startsWith('/api/cleanup/groups')&&++count===1?delayed.promise:next()});
  p.elements.cleanupByDuration.onclick();await flush();const before=p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).length;
  delayed.resolve(groupResult({items:[{group_key:'old',value:999,count:4,total_size:400}]}));await flush();
  assert.equal(p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).length,before);
  assert.doesNotMatch(p.elements.cleanupCompareTitle.textContent,/999/);assert.match(p.elements.cleanupCompareTitle.textContent,/Same duration/);
});

test('late member response cannot replace a newer group or reactivate old selections',async()=>{
  const delayed=deferred();let count=0;
  const p=page({fetchHook:(url,_options,next)=>url.startsWith('/api/cleanup/files')&&++count===1?delayed.promise:next()});await flush();
  p.elements.cleanupGroupList.children[1].onclick();await flush();
  delayed.resolve(fileResult('stale.mp4'));await flush();
  const row=p.elements.cleanupRows.children[0].children[0];assert.equal(row.children[1].children[0].textContent,'clip.mp4');
  assert.equal(p.elements.moveCleanup.disabled,true);assert.match(p.elements.cleanupSelected.textContent,/0 files/);
});

test('late folder response and errors cannot replace the current picker results',async()=>{
  const delayed=deferred();let count=0;
  const p=page({roots:2,fetchHook:(url,_options,next)=>url.startsWith('/api/cleanup/folders')&&++count===1?delayed.promise:next()});
  p.elements.chooseFolder.onclick();p.elements.cleanupRoot.value='root_1';p.elements.cleanupRoot.onchange();
  p.elements.cleanupRoot.value='root_2';p.elements.cleanupRoot.onchange();await flush();
  delayed.reject(Error('Stale folder error'));await flush();
  assert.match(p.elements.folderPickerStatus.textContent,/1 indexed folders/);assert.equal(p.elements.folderPickerFolders.children.length,1);
});

test('all accessible libraries is an explicit picker choice and never the multiple-root default',async()=>{
  const p=page({roots:2});await flush();assert.equal(p.calls.length,0);
  p.elements.chooseFolder.onclick();p.elements.cleanupRoot.value='all';p.elements.cleanupRoot.onchange();await flush();
  assert.equal(p.calls.length,0);assert.equal(p.elements.folderPickerChoose.disabled,false);
  p.elements.folderPickerChoose.onclick();await flush();
  const call=p.calls.find(c=>c.url.startsWith('/api/cleanup/groups'));assert.equal(query(call).get('scope'),'all');assert.equal(query(call).get('root'),'');
});

test('All files supports location sorting and a direction toggle after combining name and location',async()=>{
  const p=page();await flush();p.elements.allTab.onclick();await flush();
  p.elements.cleanupSort.value='location';p.elements.cleanupSort.onchange();await flush();p.elements.cleanupDirection.onclick();await flush();
  const call=p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).at(-1);assert.equal(query(call).get('sort'),'location');assert.equal(query(call).get('direction'),'desc');
});

test('moving signed selections previews and confirms before mutation then advances past a vanished group',async()=>{
  let moved=false;const actions=[];
  const p=page({fetchHook:(url,options,next)=>{
    if(url==='/api/cleanup/actions') {const body=JSON.parse(options.body);actions.push(body);if(body.action==='move')moved=true;return {job_id:'job',state:'complete',results:[{status:body.action==='preview_move'?'ready':'moved',root_id:'root_1',rel:'clip.mp4',name:'clip.mp4',confirmation_token:'confirmation'}]};}
    if(url.startsWith('/api/cleanup/groups')&&moved)return groupResult({items:[{group_key:'20',value:20,count:2,total_size:40}],total:1});
    return next();
  }});await flush();
  p.elements.selectCleanupPage.checked=true;p.elements.selectCleanupPage.onchange();await p.elements.moveCleanup.onclick();
  assert.equal(actions.length,1);assert.equal(actions[0].action,'preview_move');assert.deepEqual(actions[0].items,[{selection_token:'signed'}]);assert.equal(moved,false);
  assert.equal(p.elements.cleanupConfirm.open,true);await p.elements.cleanupConfirmAction.onclick();await flush();
  assert.equal(actions[1].action,'move');assert.deepEqual(actions[1].items,[{confirmation_token:'confirmation'}]);
  assert.equal(query(p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).at(-1)).get('group_key'),'20');assert.equal(p.elements.moveCleanup.disabled,true);
});

test('member and group pagination use separate page values and clear selected files',async()=>{
  const p=page();await flush();p.elements.selectCleanupPage.checked=true;p.elements.selectCleanupPage.onchange();
  p.elements.cleanupNext.onclick();assert.match(p.elements.cleanupSelected.textContent,/0 files/);await flush();
  assert.equal(query(p.calls.filter(c=>c.url.startsWith('/api/cleanup/groups')).at(-1)).get('page'),'1');
  assert.equal(query(p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).at(-1)).get('page'),'2');
  p.elements.cleanupGroupNext.onclick();await flush();
  assert.equal(query(p.calls.filter(c=>c.url.startsWith('/api/cleanup/groups')).at(-1)).get('page'),'2');
  assert.equal(query(p.calls.filter(c=>c.url.startsWith('/api/cleanup/files')).at(-1)).get('page'),'1');
});
