const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the shipped composer logic, not a reimplementation. DOM is deliberately
// small; full browser layout and image inference are covered separately.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.style = {}; this.dataset = {};
    this.attributes = {}; this.listeners = {}; this.value = ''; this.hidden = false;
    this.scrollHeight = 200; this.scrollTop = 0; this.clientHeight = 200;
    const classes = new Set();
    this.classList = { add: (v) => classes.add(v), remove: (v) => classes.delete(v),
      contains: (v) => classes.has(v), toggle: (v) => classes.has(v) ? classes.delete(v) : classes.add(v) };
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map((n) => typeof n === 'string' ? n : n.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.text = ''; this.children = nodes; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { this.listeners[key] = fn; }
  querySelector() { return null; }
  focus() { this.focused = true; }
  contains(node) { return node === this || this.children.includes(node); }
  get childElementCount() { return this.children.length; }
}

function setup() {
  const elements = new Map(), calls = [], timers = new Map(), storage = new Map();
  const document = { getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement: (tag) => new Element(tag), addEventListener() {}, querySelectorAll: () => [], body: new Element('body') };
  let serial = 0;
  class LocalURL extends URL {}
  LocalURL.createObjectURL = () => 'blob:local-preview'; LocalURL.revokeObjectURL = () => {};
  class FormDataMock { constructor() { this.values = new Map(); } append(k, v) { this.values.set(k, v); } }
  const context = vm.createContext({ document, URL: LocalURL, URLSearchParams, console,
    crypto: { randomUUID: () => `test-${++serial}` }, FormData: FormDataMock,
    window: { location: { href: 'http://localhost:8000/health-agent.html', origin: 'http://localhost:8000', search: '' }, addEventListener() {} },
    sessionStorage: { setItem: (k, v) => storage.set(k, v), getItem: (k) => storage.get(k), removeItem: (k) => storage.delete(k) },
    setTimeout: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; }, clearTimeout: (id) => timers.delete(id),
    fetch: async (url, options) => { calls.push({url, options}); throw new Error('Unexpected fetch'); },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/health-agent.js'), 'utf8') + '\nglobalThis.subject = healthAgent;', context);
  const agent = context.subject;
  agent.workspace = { case: { id: 'case/a' }, messages: [], actions: [] };
  return { agent, context, elements, calls, timers, storage, el: (id) => document.getElementById(id) };
}
const event = { preventDefault() {} };
const file = (name, size = 1024) => ({ name, size, lastModified: 123 });
const plain = (value) => JSON.parse(JSON.stringify(value));

test('questionnaire reply opens shared case-scoped questionnaire after refreshing facts', async () => {
  const { agent, context, el } = setup();
  let opened, requested;
  context.window.MindSpineQuestionnaire = {open: (options) => { opened = options; }};
  el('agent-chat-input').value = '做一份问卷'; el('agent-web-search').checked = true;
  agent.request = async (url, options) => { requested = JSON.parse(options.body); return {agent: {result_cards: [{type: 'questionnaire'}]}}; };
  agent.load = async () => { agent.workspace.questionnaire = {answers: {pain_score: 2}, protocol_version: 'spine-intake-v3'}; };
  await agent.send(event);
  assert.equal(requested.web_search, true); assert.equal(opened.caseId, 'case/a');
  assert.equal(opened.answers.pain_score, 2); assert.equal(opened.protocolVersion, 'spine-intake-v3');
});

test('workflow cards are buttons and saved reports have download links', () => {
  const {agent, context, el} = setup();
  let options;
  context.window.MindSpineAgentWorkflows = {openReport: (_, value) => { options = value; }};
  agent.renderMessages([{role: 'assistant', content: '已准备', audit: {result_cards: [
    {type: 'report_export', title: '生成Word', options: {format: 'docx'}},
    {type: 'document', title: '下载文件', url: '/api/cases/case-a/reports/report.docx'}]}}]);
  const results = el('agent-chat-log').children[0].children[1];
  assert.equal(results.children[0].tagName, 'button'); results.children[0].listeners.click();
  assert.equal(options.format, 'docx'); assert.equal(results.children[1].attributes.download, '');
});

test('choosing Pix2Pix after adding an image fixes only implicit modality choices', () => {
  const { agent } = setup();
  agent.addFiles([file('left-half.png')]);
  assert.equal(agent.attachments[0].modality, 'xray');
  agent.attachments[0].uploaded = {id: 'old-xray-upload'};
  agent.selectedPlugins.add('pseudo_dr');
  agent.syncAttachmentModalities();
  assert.equal(agent.attachments[0].modality, 'rgb');
  assert.equal(agent.attachments[0].uploaded, null);
  agent.attachments[0].modality = 'xray';
  agent.attachments[0].modalityExplicit = true;
  agent.syncAttachmentModalities();
  assert.equal(agent.attachments[0].modality, 'xray');
  agent.attachments[0].modalityExplicit = false;
  agent.selectedPlugins.add('keypoints');
  agent.syncAttachmentModalities();
  assert.equal(agent.attachments[0].modality, 'xray');
});

test('source identifiers disappear but clinical brackets and ordinary links survive', () => {
  const { agent } = setup();
  assert.equal(agent.cleanAnswer('观察 [who_lbp]，参考 [citation:nice_ng59](https://x.test)。'), '观察，参考。');
  assert.equal(agent.cleanAnswer('[who_lbp; nice_ng59] [0,10] [L1-L5] [正常链接](https://example.org)'), '[0,10] [L1-L5] [正常链接](https://example.org)');
  assert.equal(agent.cleanAnswer('结论 [source_9]', [{ source_id: 'source_9' }]), '结论');
  assert.equal(agent.cleanAnswer('范围 [unknown]'), '范围 [unknown]');
});

test('artifact URLs are same-origin HTTP(S), never javascript or remote', () => {
  const { agent } = setup();
  assert.equal(agent.safeLocalUrl('/api/cases/a/files/x.png'), 'http://localhost:8000/api/cases/a/files/x.png');
  for (const url of ['javascript:alert(1)', 'data:image/png;base64,x', '//external.test/image.png', 'https://localhost:8000/file', '', null]) assert.equal(agent.safeLocalUrl(url), '');
});

test('JSON API accepts 202 and exposes useful HTTP status on failures', async () => {
  const { agent, context } = setup();
  context.fetch = async () => ({ ok: true, status: 202, json: async () => ({ code: 202, run: { id: 'r1' } }) });
  assert.equal((await agent.request('/run')).run.id, 'r1');
  context.fetch = async () => ({ ok: false, status: 422, json: async () => ({ detail: [{ type: 'validation' }] }) });
  await assert.rejects(agent.request('/run'), (err) => err.status === 422 && err.message.includes('HTTP 422') && !err.message.includes('[object Object]'));
});

test('text send renders user immediately, posts to selected case and reloads answer', async () => {
  const { agent, el } = setup();
  el('agent-chat-input').value = '  请解释记录  ';
  let release, requested;
  agent.request = (url, options) => { requested = {url, options}; return new Promise((resolve) => { release = resolve; }); };
  let loads = 0; agent.load = async () => { loads++; };
  const pending = agent.send(event);
  assert.equal(agent.busy, true); assert.equal(el('agent-chat-input').value, '');
  assert.equal(agent.workspace.messages[0].content, '请解释记录');
  assert.match(el('agent-chat-log').textContent, /请解释记录/);
  assert.equal(requested.url, '/api/cases/case%2Fa/agent/messages');
  assert.deepEqual(JSON.parse(requested.options.body), {message: '请解释记录', web_search: false});
  release({agent: {proposed_actions: [{id: 'action1', confirmation_token: 'single-use'}]}});
  await pending;
  assert.equal(agent.busy, false); assert.equal(loads, 1);
  assert.equal(agent.actionTokens.get('action1'), 'single-use');
});

test('failed send restores input and clears stale progress without losing previous messages', async () => {
  const { agent, el } = setup();
  agent.workspace.messages.push({role: 'assistant', content: '既有回复'});
  el('agent-chat-input').value = '重试请求'; el('agent-run-status').hidden = false;
  agent.request = async () => { throw new Error('network unavailable'); };
  await agent.send(event);
  assert.equal(agent.workspace.messages.length, 1); assert.equal(agent.busy, false);
  assert.equal(el('agent-chat-input').value, '重试请求'); assert.equal(el('agent-run-status').hidden, true);
  assert.match(el('agent-toast').textContent, /network unavailable/);
});

test('demo cannot upload or execute models and missing attachments prompt the plus menu', async () => {
  const { agent, el } = setup();
  agent.demoMode = true; agent.attachments = [{file: file('dr.jpg')}];
  el('agent-chat-input').value = '分析';
  await agent.send(event);
  assert.equal(agent.workspace.messages.length, 0); assert.equal(agent.busy, false);
  assert.match(el('agent-toast').textContent, /切换到真实病例/);
  agent.attachments = []; agent.selectedPlugins.add('keypoints');
  await agent.send(event);
  assert.equal(el('agent-add-menu').hidden, false); assert.match(el('agent-toast').textContent, /添加要分析的资料/);
});

test('attachment staging infers modalities, rejects unsupported files and deduplicates', () => {
  const { agent } = setup();
  agent.selectedPlugins.add('roi');
  agent.addFiles([file('surface.jpg'), file('surface.jpg'), file('scan.dicom'), file('walk.mp4'), file('notes.pdf'), file('archive.zip')]);
  assert.deepEqual(plain(agent.attachments.map((item) => item.modality)), ['rgb', 'xray', 'video', 'document']);
  assert.equal(agent.attachments[0].preview, 'blob:local-preview');
  agent.addFiles(Array.from({length: 10}, (_, i) => file(`image-${i}.png`)));
  assert.equal(agent.attachments.length, 8);
});

test('upload then whitelisted plugins produces case-scoped run and returns real results', async () => {
  const { agent, el, storage } = setup();
  agent.selectedPlugins.add('keypoints'); agent.addFiles([file('scan.dcm')]);
  const requests = []; let loaded = 0;
  agent.request = async (url, options = {}) => {
    requests.push({url, options});
    if (url.endsWith('/attachments')) {
      assert.equal(options.body.values.get('modality'), 'xray');
      return {attachment: {id: 'attachment1'}};
    }
    if (options.method === 'POST') return {run: {id: 'run1', state: 'queued', steps: []}};
    return {run: {id: 'run1', state: 'completed', steps: [], result_cards: [{type: 'metrics'}]}};
  };
  agent.load = async () => { loaded++; };
  el('agent-chat-input').value = '标注并分析';
  await agent.send(event);
  assert.equal(requests[0].url, '/api/cases/case%2Fa/agent/attachments');
  const payload = JSON.parse(requests[1].options.body);
  assert.deepEqual(payload.attachment_ids, ['attachment1']); assert.deepEqual(payload.plugin_ids, ['keypoints']);
  assert.ok(payload.idempotency_key); assert.equal(payload.message, '标注并分析');
  assert.equal(agent.attachments.length, 0); assert.equal(agent.selectedPlugins.size, 0);
  assert.equal(agent.activeRun, null); assert.equal(agent.busy, false); assert.equal(loaded, 1); assert.equal(storage.size, 0);
});

test('network retry preserves uploaded attachment and run idempotency key', async () => {
  const { agent } = setup();
  agent.selectedPlugins.add('keypoints'); agent.addFiles([file('scan.dcm')]);
  let uploads = 0; const keys = [];
  agent.request = async (url, options) => {
    if (url.endsWith('/attachments')) { uploads++; return {attachment: {id: 'a1'}}; }
    keys.push(JSON.parse(options.body).idempotency_key); throw new Error('lost response');
  };
  await assert.rejects(agent.sendToolRun('分析', agent.attachments), /lost response/);
  await assert.rejects(agent.sendToolRun('分析', agent.attachments), /lost response/);
  assert.equal(uploads, 1); assert.equal(keys.length, 2); assert.equal(keys[0], keys[1]);
});

test('transient polling retries but denied/missing runs unlock the composer', async () => {
  const { agent, timers, storage, el } = setup();
  agent.activeRun = {id: 'run1'}; agent.setBusy(true); storage.set('mindspine-run:case/a', 'run1');
  agent.request = async () => { throw new Error('temporarily offline'); };
  await agent.pollRun();
  assert.equal(agent.busy, true); assert.equal([...timers.values()][0].ms, 4500);
  agent.request = async () => { const e = new Error('no permission'); e.status = 403; throw e; };
  await agent.pollRun();
  assert.equal(timers.size, 0); assert.equal(agent.activeRun, null); assert.equal(agent.busy, false);
  assert.equal(el('agent-run-status').hidden, true); assert.equal(storage.size, 0);
});

test('cancelling keeps the composer locked until worker terminal result has been reloaded', async () => {
  const { agent, timers, el } = setup();
  agent.activeRun = {id: 'run1', state: 'running'}; agent.setBusy(true);
  let terminal = false, loads = 0;
  agent.request = async () => ({run: {id: 'run1', state: terminal ? 'cancelled' : 'cancelling', steps: []}});
  agent.load = async () => { loads++; };
  await agent.stopRun();
  assert.equal(agent.busy, true); assert.equal(el('agent-stop-button').disabled, true); assert.equal(loads, 0);
  assert.equal([...timers.values()][0].ms, 1400);
  terminal = true; await agent.pollRun();
  assert.equal(agent.busy, false); assert.equal(agent.activeRun, null); assert.equal(loads, 1); assert.equal(timers.size, 0);
});

test('result cards retain units and reject external image/video resources', () => {
  const { agent } = setup();
  const card = agent.resultCard({title: '关键点测量', metrics: {LL: 40.12645}, metric_units: {LL: '°'},
    images: [{url: '/api/cases/a/image.png', label: '标注图'}, {url: 'https://bad.test/tracker.png'}], video_url: 'javascript:evil()'});
  assert.match(card.textContent, /40\.126 °/);
  const images = card.children.find((node) => node.className === 'result-images');
  assert.equal(images.children.length, 1);
  assert.equal(card.children.some((node) => node.tagName === 'video'), false);
});

test('persisted full tool result takes priority over a bounded message audit snapshot', () => {
  const { agent, el } = setup();
  agent.workspace.agent_tool_runs = [{id: 'run1', result_cards: [{type: 'metrics', title: '完整结果', metrics: {LL: 38}, metric_units: {LL: '°'}}]}];
  agent.renderMessages([{role: 'assistant', content: '分析完成', audit: {tool_run_id: 'run1', result_cards: [{type: 'metrics', title: '截断摘要', metrics: {}}]}}]);
  assert.match(el('agent-chat-log').textContent, /完整结果/);
  assert.doesNotMatch(el('agent-chat-log').textContent, /截断摘要/);
});
