const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(result = {status:'renamed',name:'Summer.final.mp4'}, options = {}) {
  const elements = {};
  for (const id of ['renameDialog','renameForm','renameCurrent','renameName','renameExtension','renameStatus','renameCancel','renameSave']) {
    elements[id] = {value:'',disabled:false,isConnected:true,focus(){},select(){},
      showModal(){this.open=true;},close(){this.open=false;this.onclose?.();},
      addEventListener(type,callback){this['on'+type]=callback;}};
  }
  const calls=[],navigation=[],window={};
  const location={href:'http://lanstream/video/0/sub%20folder/clip%20one.mp4?from=details',assign:url=>navigation.push(url)};
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/rename.js'),'utf8'), {
    document:{getElementById:id=>elements[id],querySelector:()=>({content:'csrf'}),querySelectorAll:()=>[],activeElement:{isConnected:true,focus(){}}},
    window,location,URL,Uint8Array,setTimeout:fn=>setImmediate(fn),
    crypto:{getRandomValues:bytes=>require('node:crypto').randomFillSync(bytes)},
    fetch:async(url,init={})=>{calls.push({url,init});if(options.fetchHook)return options.fetchHook(url,init);
      return {ok:true,json:async()=>({state:'complete',results:[result]})};}
  });
  return {elements,calls,navigation,open:window.LANStreamRename};
}
const submit = p => p.elements.renameForm.onsubmit({preventDefault(){}});

test('rename preselects only the stem, fixes extension, and Cancel never writes', () => {
  const p=page();p.open({name:'clip.final.MP4',selection_token:'signed'});
  assert.equal(p.elements.renameName.value,'clip.final');assert.equal(p.elements.renameExtension.textContent,'.MP4');
  p.elements.renameCancel.onclick();assert.equal(p.elements.renameDialog.open,false);assert.equal(p.calls.length,0);
});

test('save sends only the edited stem and signed selection using LAN-compatible UUID generation', async () => {
  const p=page();let refresh=0;p.open({name:'clip.mp4',selection_token:'signed'},()=>{refresh++;});
  p.elements.renameName.value='Summer.final';await submit(p);
  const body=JSON.parse(p.calls[0].init.body);
  assert.equal(body.action,'rename');assert.deepEqual(body.items,[{selection_token:'signed',name:'Summer.final'}]);
  assert.match(body.operation_id,/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  assert.equal(p.calls[0].init.headers['X-LANStream-CSRF'],'csrf');assert.equal(refresh,1);
  assert.equal(p.elements.renameDialog.open,false);assert.equal(p.elements.renameSave.disabled,false);
});

test('server errors keep the name and dialog available for correction', async () => {
  const p=page({status:'error',error:'Destination already exists'});p.open({name:'clip.mp4',selection_token:'signed'});
  p.elements.renameName.value='existing';await submit(p);
  assert.equal(p.elements.renameDialog.open,true);assert.equal(p.elements.renameName.value,'existing');
  assert.equal(p.elements.renameStatus.textContent,'Destination already exists');assert.equal(p.navigation.length,0);
  assert.equal(p.elements.renameSave.disabled,false);
});

test('detail success navigates to an encoded new filename inside the original folder', async () => {
  const p=page({status:'renamed',name:'Summer #1 你好.mp4'});p.open({name:'clip one.mp4',selection_token:'signed'});
  p.elements.renameName.value='Summer #1 你好';await submit(p);
  assert.equal(p.navigation[0],'http://lanstream/video/0/sub%20folder/Summer%20%231%20%E4%BD%A0%E5%A5%BD.mp4?from=details');
});

test('queued rename waits for the worker and disables duplicate submits and dismissal', async () => {
  let finish;const pending=new Promise(resolve=>finish=resolve);
  const p=page(undefined,{fetchHook:async(url)=>({ok:true,json:async()=>url.endsWith('/actions')?{state:'queued',job_id:'job'}:pending})});
  let refreshed=false;p.open({name:'clip.mp4',selection_token:'signed'},()=>{refreshed=true;});
  const saving=submit(p);await new Promise(resolve=>setImmediate(resolve));
  assert.equal(p.elements.renameSave.disabled,true);assert.equal(p.elements.renameCancel.disabled,true);
  let prevented=false;p.elements.renameDialog.oncancel({preventDefault(){prevented=true;}});assert.equal(prevented,true);
  await submit(p);assert.equal(p.calls.filter(c=>c.init.method==='POST').length,1);
  finish({state:'complete',results:[{status:'renamed',name:'clip.mp4'}]});await saving;
  assert.equal(refreshed,true);assert.equal(p.elements.renameDialog.open,false);
});

test('completed rename distinguishes slow results refresh from the file operation', async () => {
  let finish;
  const refresh = new Promise(resolve => finish = resolve);
  const p = page();
  p.open({name:'clip.mp4',selection_token:'signed'}, () => refresh);
  const saving = submit(p);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.elements.renameStatus.textContent, 'Renamed. Refreshing results…');
  assert.equal(p.elements.renameDialog.open, true);
  assert.equal(p.elements.renameSave.disabled, true);
  finish();
  await saving;
  assert.equal(p.elements.renameDialog.open, false);
});
