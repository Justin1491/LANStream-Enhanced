const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/vr180.js'), 'utf8');
const tick = () => new Promise(setImmediate);

function embedded({supported=true, request, frameError=false}={}) {
  let requests=0, loads=0, plays=0, fullscreen=0;
  const video=Object.assign(new EventTarget(), {
    dataset:{src:'/media/original.mp4', frameRate:'60000/1001'}, readyState:0, currentTime:0,
    hasAttribute(name) {return name==='src' && !!this.src;},
    removeAttribute(name) {delete this[name];},
    load() {loads++;this.readyState=0;},
    pause() {this.paused=true;},
    async play() {plays++;this.paused=false;},
    webkitEnterFullscreen() {fullscreen++;},
  });
  const elements={vrVideo:video,vrVideoStage:{hidden:true},enterVr:Object.assign(new EventTarget(),{disabled:true}),
    vrHands:{},vrHandChoice:{hidden:true},vrStatus:{textContent:'Native fullscreen hint'}};
  let frame;
  const active=Object.assign(new EventTarget(),{
    async end() {this.dispatchEvent(new Event('end'));},
    requestReferenceSpace() {return frameError ? Promise.resolve({}) : new Promise(()=>{});},
    updateRenderState(state) {this.renderState=state;},
    requestAnimationFrame(callback) {frame=callback;},
  });
  const context={document:Object.assign(new EventTarget(),{
    getElementById:id=>elements[id],
    createElement:()=>({getContext:kind=>kind==='2d'?{
      scale(){}, clearRect(){}, createLinearGradient(){return {addColorStop(){}};}, beginPath(){}, roundRect(){}, fill(){}, stroke(){},
      fillText(){}, moveTo(){}, lineTo(){}, arc(){}, save(){}, restore(){}, translate(){}, rotate(){},
    }:{}}),
  }),
    navigator:{xr:{isSessionSupported:async()=>supported,requestSession(){requests++;return request?request(active):Promise.resolve(active);}}},
    XRMediaBinding:class {createEquirectLayer(){return {}; }},XRWebGLBinding:class {},Path2D:class {},
    createVrControllerLayer:()=>({layer:{},update(){return false;},draw(){},close(){},uploadPanel(){}})};
  vm.runInNewContext(script,context);
  return {...elements,video,active,api:context.lanstreamVr,
    get requests(){return requests;},get loads(){return loads;},get plays(){return plays;},get fullscreen(){return fullscreen;},
    frame(){frame(0,{getViewerPose(){throw new Error('frame draw failure');}});}};
}

test('normal-player entry requests XR synchronously, uses the original at the saved position, and releases its decoder on exit',async()=>{
  const p=embedded();await tick();
  let stopped=0,result;
  assert.equal(p.video.src,undefined);
  const pending=p.api.start({time:37,volume:0.4,beforeStart(){stopped++;},onEnd(...args){result=args;}});
  assert.equal(stopped,1);
  assert.equal(p.requests,1,'requestSession must execute inside the original click before an await');
  await tick();
  assert.equal(p.video.src,'/media/original.mp4');
  assert.equal(p.video.volume,0.4);
  p.video.readyState=1;p.video.dispatchEvent(new Event('loadedmetadata'));
  assert.equal(p.video.currentTime,37);
  p.video.currentTime=52;
  await p.active.end();
  assert.equal(result[0],52);
  assert.equal(p.video.src,undefined);
  assert.equal(p.video.paused,true);
  assert.equal(p.loads,1);
});

test('unsupported embedded VR never loads background media and enters native fullscreen from the same click',async()=>{
  const p=embedded({supported:false});await tick();
  let result;
  assert.equal(p.video.src,undefined);
  p.api.start({time:11,volume:1,beforeStart(){},onEnd(...args){result=args;}});
  assert.equal(p.fullscreen,1);
  assert.equal(p.requests,0);
  assert.equal(p.plays,1);
  assert.equal(p.vrVideoStage.hidden,false);
  assert.equal(p.video.controls,true);
  p.video.readyState=1;p.video.dispatchEvent(new Event('loadedmetadata'));
  p.video.currentTime=19;
  p.video.dispatchEvent(new Event('webkitendfullscreen'));
  assert.equal(result[0],19);
  assert.equal(p.video.src,undefined);
});

test('declined XR restores 2D at the entry position without loading another decoder',async()=>{
  const p=embedded({request:()=>Promise.reject(new Error('Permission denied'))});await tick();
  let result;
  await p.api.start({time:31,volume:1,beforeStart(){},onEnd(...args){result=args;}});
  assert.equal(p.requests,1);
  assert.equal(p.plays,0);
  assert.equal(result[0],31);
  assert.match(result[1],/Unable to start VR/);
  assert.equal(p.video.src,undefined);
  assert.equal(p.enterVr.disabled,false);
});

test('an embedded equirect render failure returns the saved playback position and reason',async()=>{
  const p=embedded({frameError:true});await tick();
  let result;
  await p.api.start({time:31,volume:1,beforeStart(){},onEnd(...args){result=args;}});
  p.video.readyState=1;p.video.currentTime=47;
  p.frame();
  assert.equal(result[0],47);
  assert.match(result[1],/Unable to render VR180/);
  assert.equal(p.video.src,undefined);
});

test('returning while XR permission is pending ends a late session without loading media',async()=>{
  let resolve;
  const p=embedded({request:()=>new Promise(done=>{resolve=done;})});await tick();
  let returns=0,ended=0;
  p.active.end=async()=>{ended++;};
  const pending=p.api.start({time:7,volume:1,beforeStart(){},onEnd(){returns++;}});
  p.api.stop();
  resolve(p.active);await pending;
  assert.equal(returns,1);
  assert.equal(ended,1);
  assert.equal(p.plays,0);
  assert.equal(p.video.src,undefined);
});
