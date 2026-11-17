const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');

function setup({preview = 'ready', move = 'moved', pending = false} = {}) {
  const nodes = {};
  for (const id of ['deleteFile','deleteDialog','deleteName','deleteStatus','deleteDialogStatus','deleteCancel','deleteConfirm']) {
    nodes[id] = Object.assign(new EventTarget(), {dataset:{}, textContent:'', disabled:false,
      showModal() { this.open = true; }, close() { this.open = false; this.dispatchEvent(new Event('close')); },
      focus() { if (!this.disabled) this.focused = true; }});
  }
  nodes.deleteFile.dataset = {name:'A <clip>.mp4', token:'signed-selection', folder:'/browse/0/Folder/'};
  const calls = [], navigations = [];
  const context = {document:{getElementById:id=>nodes[id], querySelector:()=>({content:'csrf'})},
    crypto:webcrypto, Uint8Array, encodeURIComponent, setTimeout:fn=>{fn();}, clearTimeout, AbortController,
    location:{assign:url=>navigations.push(url)},
    fetch:async (url, options) => {
      const data = options?.body ? JSON.parse(options.body) : null;
      calls.push({url, options, data});
      if (!data) return {ok:true,json:async()=>({state:'complete',results:[{status:move}]})};
      const result = data.action === 'preview_move' ? {status:preview,confirmation_token:'signed-confirmation',error:preview==='ready'?undefined:'File changed'} : {status:move,error:move==='moved'?undefined:'File is busy'};
      return {ok:true,json:async()=>data.action==='move' && pending ? {state:'running',job_id:'job-1'} : {state:'complete',results:[result]}};
    }};
  vm.runInNewContext(fs.readFileSync('static/detail-delete.js','utf8'), context);
  return {nodes,calls,navigations};
}

test('preview is non-destructive, shows the filename safely, focuses Cancel and cancellation sends no move', async () => {
  const p = setup(); await p.nodes.deleteFile.onclick();
  assert.equal(p.nodes.deleteDialog.open,true);
  assert.equal(p.nodes.deleteName.textContent,'A <clip>.mp4');
  assert.equal(p.nodes.deleteCancel.focused,true);
  assert.equal(p.calls[0].data.action,'preview_move');
  assert.equal(p.calls[0].data.items[0].selection_token,'signed-selection');
  p.nodes.deleteCancel.onclick();
  assert.equal(p.nodes.deleteDialog.open,false);
  assert.equal(p.calls.length,1);
  assert.deepEqual(p.navigations,[]);
});

test('confirmation sends only the preview token with CSRF and waits for a successful move before navigating', async () => {
  const p = setup({pending:true}); await p.nodes.deleteFile.onclick();
  await p.nodes.deleteConfirm.onclick();
  assert.equal(p.calls[1].data.action,'move');
  assert.deepEqual(p.calls[1].data.items,[{confirmation_token:'signed-confirmation'}]);
  assert.equal(p.calls[1].options.headers['X-LANStream-CSRF'],'csrf');
  assert.match(p.calls[1].data.operation_id,/^[a-f0-9-]{36}$/);
  assert.equal(p.calls[2].url,'/api/cleanup/jobs/job-1');
  assert.deepEqual(p.navigations,['/browse/0/Folder/']);
});

test('preview errors leave the file in place and do not open confirmation', async () => {
  const p = setup({preview:'error'}); await p.nodes.deleteFile.onclick();
  assert.equal(p.nodes.deleteDialog.open,undefined);
  assert.match(p.nodes.deleteStatus.textContent,/File changed/);
  assert.equal(p.calls.length,1);
});

test('move errors stay visible without navigating and require a fresh preview', async () => {
  const p = setup({move:'error'}); await p.nodes.deleteFile.onclick();
  await p.nodes.deleteConfirm.onclick();
  assert.match(p.nodes.deleteDialogStatus.textContent,/File is busy/);
  assert.deepEqual(p.navigations,[]);
  await p.nodes.deleteConfirm.onclick();
  assert.equal(p.calls.length,2);
});
