const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Execute the shipped functions against a minimal DOM, without installing packages.
class Element {
  constructor(tag='div') {this.tagName=tag;this.children=[];this.style={};this.dataset={};this.attributes={};this.listeners={};this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.readyState=0;this.paused=true;}
  set textContent(value) {this.text=String(value);this.children=[];}
  get textContent() {return (this.text||'')+this.children.map(n=>typeof n==='string'?n:n.textContent).join('');}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.text='';this.children=nodes;}
  add(node) {this.children.push(node);}
  get options() {return this.children;}
  setAttribute(k,v) {this.attributes[k]=v;}
  getAttribute(k) {return this.attributes[k]||null;}
  removeAttribute(k) {delete this.attributes[k];}
  addEventListener(k,fn) {this.listeners[k]=fn;}
  pause() {this.paused=true;}
  load() {}
  focus() {}
}
function setup() {
  const elements=new Map(),calls=[];
  const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:tag=>new Element(tag),querySelectorAll:()=>[]};
  function Option(text,value) {const el=new Element('option');el.textContent=text;el.value=value;return el;}
  const location={href:'http://localhost:8000/gait-workspace.html',origin:'http://localhost:8000',search:''};
  const context=vm.createContext({document,Option,location,URL,URLSearchParams,console,
    history:{replaceState(){}},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options)=>{calls.push({url,options});throw Error('Unexpected network request');}});
  const source=fs.readFileSync(path.join(__dirname,'../web/gait-workspace.js'),'utf8');
  vm.runInContext(source.replace('  init();','  globalThis.subject={state,options,renderSubjects,renderScores,renderTracks,renderRuntime,renderReport,renderJob,review,resetResult,updateArchiveState,qualitySummary,clearDerivedVideos};'),context);
  const el=id=>document.getElementById(id);
  el('gait-start-seconds').value='0';el('gait-end-seconds').value='';el('gait-device').value='auto';el('gait-analysis-mode').value='single';el('gait-track-id').value='';
  return {agent:context.subject,el,calls};
}
const plain=value=>JSON.parse(JSON.stringify(value));

test('default GPU preference does not authorize CPU fallback and clears inherited target/range',()=>{
  const {agent}=setup();
  assert.deepEqual(plain(agent.options()),{start_seconds:0,end_seconds:null,device:'auto',analysis_mode:'single',target_track_id:null,allow_cpu_fallback:false});
});
test('all-person mode clears target; CPU fallback requires explicit opt in',()=>{
  const {agent,el}=setup();el('gait-track-id').value='2';el('gait-analysis-mode').value='all';el('gait-allow-cpu').checked=true;
  assert.equal(agent.options().target_track_id,null);assert.equal(agent.options().allow_cpu_fallback,true);
});
test('invalid target and time range are rejected before upload',()=>{
  const {agent,el}=setup();el('gait-track-id').value='foo';assert.throws(()=>agent.options(),/有效/);
  el('gait-track-id').value='';el('gait-start-seconds').value='5';el('gait-end-seconds').value='2';assert.throws(()=>agent.options(),/结束时间/);
});
test('ambiguous target displays immediate selection and all-person actions',()=>{
  const {agent,el}=setup();agent.state.job={status:'rejected',options:{}};agent.state.report={requires_target_selection:true};
  agent.renderTracks({tracks:[{track_id:1,frame_count:100},{track_id:2,frame_count:110}]});
  assert.equal(el('gait-target-prompt').hidden,false);assert.equal(el('gait-analyze-all').disabled,false);
  assert.match(el('gait-result-label').textContent,/请选择被测者/);assert.equal(el('gait-target-choice').options.length,3);
});
test('auto selection is restored when opening a different automatic job',()=>{
  const {agent,el}=setup();agent.state.job={status:'completed',options:{target_track_id:null}};agent.state.report={};el('gait-track-id').value='2';
  agent.renderTracks({tracks:[{track_id:2}]});assert.equal(el('gait-track-id').value,'');
});
test('four people produce four independent cards including failed-quality reasons',()=>{
  const {agent,el}=setup();const report={multi_subject:true,subject_results:[1,2,3,4].map(track_id=>({track_id,status:track_id===4?'rejected':'completed',prediction:track_id===4?null:{label:'模型类别 A',scores:{A:.7,B:.2,C:.1}},quality:{passed:track_id!==4,valid_frames:track_id===4?10:100,issues:track_id===4?[{message:'有效帧不足'}]:[]}}))};
  agent.renderSubjects(report);const cards=el('gait-subject-results').children;
  assert.equal(cards.length,4);assert.match(cards[0].textContent,/人物 1/);assert.match(cards[0].textContent,/70%/);
  assert.match(cards[3].textContent,/有效帧不足/);assert.doesNotMatch(cards[3].textContent,/70%/);
  assert.match(el('gait-result-note').textContent,/不代表诊断/);
});
test('multi-person completed jobs cannot be archived even through direct action',async()=>{
  const {agent,el,calls}=setup();agent.state.job={id:'job-a',status:'completed',options:{analysis_mode:'all'}};agent.state.report={multi_subject:true};
  agent.updateArchiveState();assert.equal(el('gait-archive').disabled,true);await agent.review(true);assert.equal(calls.length,0);
  assert.match(el('gait-review-message').textContent,/不能合并归档/);
});
test('completed single-person report can be archived only after loading its report',()=>{
  const {agent,el}=setup();agent.state.job={status:'completed',options:{analysis_mode:'single'}};agent.updateArchiveState();assert.equal(el('gait-archive').disabled,true);
  agent.state.report={prediction:{label:'A'}};agent.updateArchiveState();assert.equal(el('gait-archive').disabled,false);
});
test('runtime identifies actual GPU/CPU, fallback and stage duration without using innerHTML',()=>{
  const {agent,el}=setup();agent.renderRuntime({actual_device:'cuda',gpu_name:'RTX 4050',tracking_cache_reused:true,stage_timings:{tracking:12.5,inference:0.7}});
  assert.match(el('gait-runtime').textContent,/GPU \/ CUDA · RTX 4050/);assert.match(el('gait-runtime').textContent,/12.5 秒/);assert.match(el('gait-runtime').textContent,/缓存/);
  agent.renderRuntime({actual_device:'cpu',fallback_reason:'<img src=x onerror=alert(1)>'});
  assert.match(el('gait-runtime').textContent,/CPU/);assert.equal(el('gait-runtime').children.length,0);
});
test('reset removes prior people and runtime instead of showing stale results for a new video',()=>{
  const {agent,el}=setup();el('gait-subject-results').append(new Element());el('gait-target-prompt').hidden=false;
  agent.resetResult();assert.equal(el('gait-subject-results').children.length,0);assert.equal(el('gait-target-prompt').hidden,true);assert.equal(el('gait-analyze-all').disabled,true);
});
test('research per-person probabilities are visible and explicitly labeled as research',()=>{
  const {agent,el}=setup();agent.renderSubjects({multi_subject:true,subject_results:[{track_id:2,status:'completed',research_demo:true,prediction:null,raw_model_prediction:{class_name:'A',probabilities:{A:.8,B:.15,C:.05}},quality:{passed:true,valid_frames:118}}]});
  assert.match(el('gait-subject-results').textContent,/研究模式原始输出/);assert.match(el('gait-subject-results').textContent,/80%/);
});
test('target-selection phase does not present unextracted silhouettes as failed data',()=>{
  const {agent,el}=setup();agent.qualitySummary({requires_target_selection:true,quality:{passed:false,valid_frames:0,sampled_frames:118}});
  assert.equal(el('gait-quality-state').textContent,'等待选择人物');assert.match(el('gait-quality-summary').textContent,/选择人物后提取/);
});
test('GPU error offers explicit CPU retry and live backend timing aliases render correctly',()=>{
  const {agent,el}=setup();agent.state.job={status:'failed'};agent.renderRuntime({actual_device:null,error_code:'cuda_preflight_failed',fallback_reason:'GPU 自检失败'});
  assert.equal(el('gait-retry-cpu').hidden,false);
  agent.state.job.status='running';agent.renderRuntime({device_effective:'cuda',stage_timings_seconds:{classification:1.2},segmenter_device:'cpu'});
  assert.equal(el('gait-retry-cpu').hidden,true);assert.match(el('gait-runtime').textContent,/分类推理：1.2 秒/);assert.match(el('gait-runtime').textContent,/轮廓提取：CPU/);
});
test('rerunning a job clears prior target mask/overlay sources',()=>{
  const {agent,el}=setup();agent.state.view='masks';el('gait-video-masks').setAttribute('src','/old-target-mask.mp4');el('gait-video-overlay').setAttribute('src','/old-overlay.mp4');
  agent.clearDerivedVideos();assert.equal(el('gait-video-masks').getAttribute('src'),null);assert.equal(el('gait-video-overlay').getAttribute('src'),null);assert.equal(agent.state.view,'original');
});
test('single and per-person classifier keys show localized model labels',()=>{
  const {agent,el}=setup();const prediction={predicted_class:'positive',probabilities:{negative:.1,neutral:.2,positive:.7}};
  agent.renderScores({prediction});assert.equal(el('gait-result-label').textContent,'阳性类');assert.match(el('gait-scores').textContent,/阴性类/);assert.match(el('gait-scores').textContent,/中间类/);assert.doesNotMatch(el('gait-scores').textContent,/negative|neutral|positive/);
  agent.renderSubjects({multi_subject:true,subject_results:[{track_id:1,prediction,quality:{passed:true}}]});assert.match(el('gait-subject-results').textContent,/模型输出：阳性类/);
  agent.renderScores({prediction:{predicted_class_id:0,class_order:['negative','neutral','positive'],probabilities:[.7,.2,.1]}});assert.equal(el('gait-result-label').textContent,'阴性类');
});

