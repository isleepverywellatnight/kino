const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const app = require('../tools/interface_source.js').appSource();
const help = require('../tools/interface_source.js').withoutRegistrations(require('../tools/interface_source.js').scriptText('playback-help.js'));
const recover = 'async function recoverInAppSource(generation)' + app.split('async function recoverInAppSource(generation)')[1].split('function renderInAppAudioTracks')[0];
function setup(overrides = {}) {
  const starts = [], requests = [];
  const recovery = {sourceKey:'a'.repeat(32), params:{imdb_id:'tt1',type:'series',episode:2,season:1}, excluded:new Set(['a'.repeat(32)]), attempts:0, pending:false};
  const ctx = {inAppRecovery:recovery, inAppPlaybackGeneration:3, inAppCurrentMedia:{id:'tt1'},
    document:{getElementById:()=>({currentTime:42})}, inAppAbsoluteTime:v=>v.currentTime,
    showInAppToast:()=>{}, api:async (path,options)=>{requests.push({path,body:JSON.parse(options.body)}); return {stream_url:'https://example.invalid/new',source_key:'b'.repeat(32),resume_sec:99};},
    openInAppPlayer:(...args)=>starts.push(args), ...overrides};
  vm.createContext(ctx); vm.runInContext(recover,ctx); return {ctx,recovery,starts,requests};
}
test('wrong content starts at zero even if server returns an old resume position',async()=>{
  const {ctx,recovery,starts}=setup(); recovery.resetPosition=true; recovery.feedbackRecorded=true;
  await vm.runInContext('recoverInAppSource(3)',ctx); assert.equal(starts[0][4],0);
});
test('automatic failure persists temporary feedback and preserves exact zero',async()=>{
  const {ctx,requests,starts}=setup({document:{getElementById:()=>({currentTime:0})}});
  await vm.runInContext('recoverInAppSource(3)',ctx);
  assert.equal(requests[0].path,'/api/playback-feedback'); assert.equal(requests[0].body.reason,'stalled');
  assert.equal(starts[0][4],0);
});
test('retry budget and pending request prevent infinite or concurrent recovery',async()=>{
  const {ctx,recovery,requests}=setup(); recovery.attempts=5;
  assert.equal(await vm.runInContext('recoverInAppSource(3)',ctx),false);
  recovery.attempts=0; recovery.pending=true;
  assert.equal(await vm.runInContext('recoverInAppSource(3)',ctx),false); assert.equal(requests.length,0);
});
test('stale recovery cannot replace a new playback session',async()=>{
  const {ctx,starts}=setup(); ctx.api=async()=>{ctx.inAppPlaybackGeneration=4;return {};};
  await vm.runInContext('recoverInAppSource(3)',ctx); assert.equal(starts.length,0);
});
test('diagnostic excludes URLs, metadata, credentials and exception strings',()=>{
  const ctx={inAppAbsoluteTime:()=>42,inAppDuration:()=>100,inAppIsRemux:true,
    inAppAudioTracks:[{title:'SECRET',codec:'https://secret.invalid'}],inAppSelectedAudioIndex:2,
    inAppActiveCues:[],inAppSubDelaySec:0,inAppRecovery:{attempts:1,params:{token:'SECRET'}},
    video:{currentSrc:'https://secret.invalid',error:{code:4,message:'SECRET'},volume:0.7,paused:false,muted:false,readyState:2,networkState:1}};
  vm.createContext(ctx); vm.runInContext(help+'; result = playbackDiagnostic(video);',ctx);
  const json=JSON.stringify(ctx.result); assert.doesNotMatch(json,/SECRET|https|token|currentSrc/); assert.equal(ctx.result.mediaError,4);
});
test('subtitle offset uses absolute time after stream seek',()=>{
  const fn='function adjustInAppSubDelay(deltaSec)'+app.split('function adjustInAppSubDelay(deltaSec)')[1].split('function updateInAppSubtitleOverlay')[0];
  let time; const ctx={inAppActiveCues:[{}],inAppSubDelaySec:0,showInAppToast:()=>{},document:{getElementById:()=>({currentTime:2})},inAppAbsoluteTime:v=>120+v.currentTime,updateInAppSubtitleOverlay:t=>time=t};
  vm.runInNewContext(fn+'; adjustInAppSubDelay(0.5);',ctx);assert.equal(time,122);
});
test('a source rejected during probe retains the intended resume position',async()=>{
  const {ctx,recovery,starts}=setup({document:{getElementById:()=>({currentTime:0})}});recovery.resumePosition=120;
  await vm.runInContext('recoverInAppSource(3)',ctx);assert.equal(starts[0][4],120);
});
test('manual feedback locks automatic recovery until its exclusion is saved',async()=>{
  const {ctx,recovery}=setup(); let release;
  ctx.api=()=>new Promise(resolve=>{release=resolve;});
  vm.runInContext(help,ctx);
  vm.runInContext('playbackHelpStatus = () => {}; closePlaybackHelp = () => {};',ctx);
  const pending = vm.runInContext("replacePlaybackSource('wrong_content')",ctx);
  assert.equal(recovery.pending,true);
  assert.equal(await vm.runInContext('recoverInAppSource(3)',ctx),false);
  // Cancel the session while feedback is in flight; no replacement may start.
  ctx.inAppPlaybackGeneration=4; release({ok:true}); await pending;
  assert.equal(recovery.pending,false);
});
