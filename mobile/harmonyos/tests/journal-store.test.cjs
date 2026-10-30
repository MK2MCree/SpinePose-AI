'use strict';

// Exercises the production pure ArkTS store using in-memory platform adapters.
// This is a contract suite, not a replacement for an ArkTS build or device QA.
// Run from the workspace root: node mobile/harmonyos/tests/journal-store.test.cjs
// Uses the workspace's existing site/node_modules/typescript; no extra packages.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const projectRoot = path.resolve(__dirname, '../../..');
const ts = require(path.join(projectRoot, 'site/node_modules/typescript'));
const sourcePath = path.resolve(__dirname, '../entry/src/main/ets/model/JournalStore.ets');
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  fileName: 'JournalStore.ts',
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  reportDiagnostics: true
});
const errors = (compiled.diagnostics || []).filter(d => d.category === ts.DiagnosticCategory.Error);
assert.equal(errors.length, 0, 'The store must be valid TypeScript before running its contract tests.');

const MAX_BYTES = 2 * 1024 * 1024;
const context = {};
const clone = value => JSON.parse(JSON.stringify(value));
const fixture = (overrides = {}) => ({
  id: 'record_1', day: '2026-10-24', category: '步行', minutes: 30,
  note: '本地测试记录', createdAt: 1792800000000, updatedAt: 1792800000000,
  ...overrides
});
const photoFixture = (overrides = {}) => ({
  id: 'photo_1', fileName: 'photo_1792800000000_123456.jpg', source: 'camera',
  view: '正面', originalName: '我的照片.jpg', createdAt: 1792800000000,
  ...overrides
});
const backup = entries => JSON.stringify({
  schemaVersion: 1, exportedAt: '2026-10-24T08:00:00.000Z', entries
});

function platform(initialDurable = []) {
  const durable = new Map(initialDurable.map(([name, values]) => [name, new Map(values)]));
  const handles = new Map();
  const files = new Map();
  const openFiles = new Map();
  const state = {
    durable, files, selectUris: [], saveUris: [], writes: 0, mutations: 0,
    flushes: 0, opens: 0, closes: 0, fsyncs: 0, nextFd: 10,
    failMutation: false, failOpen: false, failFsync: false, failWrite: false,
    zeroWrite: false, failFlushAt: 0, failFlushAfterCommit: false,
    writeLimit: Infinity, readLimit: Infinity, statSize: undefined,
    mutationGate: undefined, flushHistory: []
  };
  class Preferences {
    constructor(name) {
      this.name = name;
      this.pending = new Map(durable.get(name) || []);
    }
    async get(key, defaultValue) { return this.getSync(key, defaultValue); }
    getSync(key, defaultValue) {
      return this.pending.has(key) ? this.pending.get(key) : defaultValue;
    }
    putSync(key, value) {
      state.mutations++;
      if (state.failMutation) throw new Error('Simulated preferences write failure');
      // The real service rejects oversized values. Long journals must not be
      // tested against an adapter that silently accepts a single giant value.
      if (typeof value === 'string' && value.length > 8192) {
        throw new Error('Preferences value exceeds its platform limit');
      }
      this.pending.set(key, value);
    }
    clearSync() {
      state.mutations++;
      if (state.failMutation) throw new Error('Simulated preferences clear failure');
      this.pending.clear();
    }
    async flush() {
      state.flushes++;
      if (state.mutationGate) await state.mutationGate;
      const failed = state.failFlushAt === state.flushes;
      if (failed && !state.failFlushAfterCommit) throw new Error('Simulated flush failure');
      durable.set(this.name, new Map(this.pending));
      state.flushHistory.push(Array.from(durable, ([name, values]) => [name, Array.from(values)]));
      if (failed) throw new Error('Simulated uncertain commit');
    }
  }
  const preferences = {
    async getPreferences(_context, options) {
      if (!handles.has(options.name)) handles.set(options.name, new Preferences(options.name));
      return handles.get(options.name);
    }
  };
  const fileIo = {
    OpenMode: { READ_ONLY: 1, WRITE_ONLY: 2, CREATE: 4, TRUNC: 8 },
    async open(uri, mode) {
      if (state.failOpen) throw new Error('Simulated file open failure');
      if (mode & 2) {
        if ((mode & 8) || !files.has(uri)) files.set(uri, Buffer.alloc(0));
      } else if (!files.has(uri)) {
        throw new Error('File does not exist');
      }
      const fd = state.nextFd++;
      state.opens++;
      openFiles.set(fd, uri);
      return { fd };
    },
    statSync(fd) {
      return { size: state.statSize === undefined ? files.get(openFiles.get(fd)).length : state.statSize };
    },
    async read(fd, buffer, options) {
      const data = files.get(openFiles.get(fd));
      const offset = options.offset || 0;
      const count = Math.max(0, Math.min(buffer.byteLength, state.readLimit, data.length - offset));
      new Uint8Array(buffer).set(data.subarray(offset, offset + count));
      return count;
    },
    async write(fd, buffer, options) {
      if (state.failWrite) throw new Error('Simulated file write failure');
      if (state.zeroWrite) return 0;
      const data = Buffer.from(buffer);
      const uri = openFiles.get(fd);
      const offset = options.offset || 0;
      const count = Math.min(data.length, state.writeLimit);
      const previous = files.get(uri);
      const next = Buffer.alloc(Math.max(previous.length, offset + count));
      previous.copy(next);
      data.copy(next, offset, 0, count);
      files.set(uri, next);
      state.writes++;
      return count;
    },
    async fsync() {
      if (state.failFsync) throw new Error('Simulated fsync failure');
      state.fsyncs++;
    },
    async close(fd) {
      assert.ok(openFiles.has(fd), 'A live file handle should be closed only once.');
      openFiles.delete(fd);
      state.closes++;
    }
  };
  class DocumentViewPicker {
    async save() { return state.saveUris; }
    async select() { return state.selectUris; }
  }
  const util = {
    TextEncoder: class { encodeInto(text) { return new TextEncoder().encode(text); } },
    TextDecoder: {
      create(encoding, options) {
        const decoder = new TextDecoder(encoding, options);
        return { decodeToString: bytes => decoder.decode(bytes) };
      }
    }
  };
  const modules = {
    '@kit.AbilityKit': {}, '@kit.ArkData': { preferences },
    '@kit.CoreFileKit': {
      fileIo, picker: { DocumentViewPicker, DocumentSaveOptions: class {}, DocumentSelectOptions: class {} }
    },
    '@kit.ArkTS': { util }
  };
  const exports = {};
  vm.runInNewContext(compiled.outputText, {
    exports, require(name) {
      assert.ok(Object.hasOwn(modules, name), 'Unexpected external platform dependency: ' + name);
      return modules[name];
    }
  }, { filename: sourcePath });
  return {
    ...exports, state,
    restart() { handles.clear(); return new exports.JournalStore(); },
    importText(text) {
      const uri = 'memory://backup.json';
      files.set(uri, Buffer.isBuffer(text) ? text : Buffer.from(text, 'utf8'));
      state.selectUris = [uri];
    }
  };
}

test('first launch is empty and does not manufacture records or commit data', async () => {
  const env = platform();
  assert.deepEqual(clone(await new env.JournalStore().initialize(context)), []);
  assert.equal(env.state.mutations, 0);
  assert.equal(env.state.flushes, 0);
});

test('save, reload, edit, delete and clear survive process restart', async () => {
  const env = platform();
  let store = new env.JournalStore();
  await store.initialize(context);
  const first = fixture();
  const second = fixture({ id: 'record_2', category: '休息', minutes: 0 });
  await store.saveEntries([first, second]);
  store = env.restart();
  assert.deepEqual(clone(await store.initialize(context)), [first, second]);
  const edited = fixture({ minutes: 1440, note: '已编辑', updatedAt: first.createdAt + 1000 });
  await store.saveEntries([edited]);
  store = env.restart();
  assert.deepEqual(clone(await store.initialize(context)), [edited]);
  await store.saveEntries([]);
  assert.deepEqual(clone(await env.restart().initialize(context)), []);
});

test('saved and returned records are isolated from caller mutation', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  const entries = [fixture()];
  await store.saveEntries(entries);
  entries[0].minutes = 777;
  const loaded = await store.initialize(context);
  assert.equal(loaded[0].minutes, 30);
  loaded[0].minutes = 888;
  assert.equal((await store.initialize(context))[0].minutes, 30);
});

test('2000 long Unicode entries remain durable without oversized preference values', async () => {
  const env = platform();
  const entries = Array.from({ length: 2000 }, (_, i) => fixture({ id: 'item_' + i, note: '汉'.repeat(200) }));
  const store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries(entries);
  assert.deepEqual(clone(await env.restart().initialize(context)), entries);
});

test('strict calendar dates include leap years and reject normalized impossible dates', () => {
  const env = platform();
  for (const day of ['0001-01-01', '2000-02-29', '2024-02-29', '9999-12-31']) {
    assert.equal(env.isValidJournalDay(day), true, day);
  }
  for (const day of ['0000-01-01', '1900-02-29', '2026-02-29', '2026-04-31', '2026-13-01',
    '2026-00-01', '2026-01-00', '2026-1-01', ' 2026-01-01', 20260101, null]) {
    assert.equal(env.isValidJournalDay(day), false, String(day));
  }
});

test('import rejects malformed envelopes and unsupported schema before any writes', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries([fixture()]);
  const mutationsBefore = env.state.mutations;
  const invalid = ['', '{', 'null', '[]', '{}', JSON.stringify({ schemaVersion: 2, entries: [] }),
    JSON.stringify({ schemaVersion: '1', exportedAt: '2026-10-24T08:00:00.000Z', entries: [] }),
    JSON.stringify({ schemaVersion: 1, exportedAt: '2026-02-30T08:00:00.000Z', entries: [] }),
    JSON.stringify({ schemaVersion: 1, exportedAt: '2026-10-24', entries: [] }),
    JSON.stringify({ schemaVersion: 1, exportedAt: '2026-10-24T08:00:00.000Z', entries: {} })];
  for (const text of invalid) {
    env.importText(text);
    await assert.rejects(() => store.importFile(context), 'Invalid backup should be rejected: ' + text);
  }
  assert.equal(env.state.mutations, mutationsBefore);
  assert.deepEqual(clone(await store.initialize(context)), [fixture()]);
  assert.equal(env.state.opens, env.state.closes);
});

test('invalid fields, duplicate identifiers and record limit are rejected', () => {
  const env = platform();
  const invalidEntries = [null, [], {}, fixture({ id: '' }), fixture({ id: 'a/b' }),
    fixture({ id: 'x'.repeat(129) }), fixture({ day: '2026-02-30' }), fixture({ category: '跑步' }),
    fixture({ minutes: '30' }), fixture({ minutes: 0.5 }), fixture({ minutes: -1 }),
    fixture({ minutes: 1441 }), fixture({ minutes: NaN }), fixture({ note: null }),
    fixture({ note: 'x'.repeat(301) }), fixture({ createdAt: -1 }), fixture({ createdAt: 0.5 }),
    fixture({ updatedAt: fixture().createdAt - 1 }), fixture({ updatedAt: Infinity }),
    fixture({ updatedAt: 8640000000000001 })];
  for (const entry of invalidEntries) {
    assert.throws(() => env.validateJournalEntries([entry]), 'Invalid record: ' + JSON.stringify(entry));
  }
  assert.throws(() => env.parseJournalFile(backup([fixture(), fixture()])));
  assert.throws(() => env.parseJournalFile(backup(Array.from({ length: 2001 }, (_, i) => fixture({ id: 'item_' + i })))));
});

test('unknown imported fields are discarded and cannot leak to later exports', () => {
  const env = platform();
  const entries = env.parseJournalFile(backup([{ ...fixture(), surprise: 'discard me' }]));
  assert.deepEqual(clone(entries), [fixture()]);
  assert.equal(env.createJournalFile(entries).includes('discard me'), false);
});

test('legacy version 1 backups without attachment fields remain unchanged', async () => {
  const env = platform();
  const legacy = [fixture(), fixture({ id: 'old_2', minutes: 0 })];
  const parsed = env.parseJournalFile(backup(legacy));
  assert.deepEqual(clone(parsed), legacy);
  assert.equal(Object.hasOwn(parsed[0], 'photos'), false);
  const store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries(parsed);
  assert.deepEqual(clone(await env.restart().initialize(context)), legacy);
});

test('photo records preserve local metadata across restart and isolate nested mutation', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  const record = fixture({ category: '图片记录', minutes: 0, photos: [photoFixture()] });
  const expected = clone(record);
  await store.saveEntries([record]);
  record.photos[0].view = '背面';
  record.photos.push(photoFixture({ id: 'photo_2', fileName: 'another.png' }));
  const reloaded = await env.restart().initialize(context);
  assert.deepEqual(clone(reloaded), [expected]);
  reloaded[0].photos[0].originalName = 'modified.jpg';
  assert.deepEqual(clone(await store.initialize(context)), [expected]);
});

test('four long photo metadata records fit a preference value without storing image bytes', async () => {
  const env = platform();
  const views = ['正面', '侧面', '背面', '斜侧面'];
  const photos = views.map((view, i) => photoFixture({
    id: ('photo_' + i).padEnd(128, '_'),
    fileName: ('photo_' + i).padEnd(120, '_') + '.heic',
    source: i % 2 ? 'album' : 'camera', view,
    originalName: '照'.repeat(116) + '.jpg'
  }));
  const record = fixture({ id: 'r'.repeat(128), category: '图片记录', minutes: 0,
    note: '记'.repeat(300), photos });
  const clean = env.validateJournalEntries([record]);
  assert.ok(JSON.stringify(clean[0]).length <= 8192);
  const store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries(clean);
  assert.deepEqual(clone(await env.restart().initialize(context)), [record]);
});

test('photo metadata validates sources, views, limits and managed file paths before writes', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries([fixture()]);
  const mutationCheckpoint = env.state.mutations;
  const invalidPhotos = [null, [], {}, photoFixture({ id: '' }), photoFixture({ id: 'a/b' }),
    photoFixture({ fileName: '../outside.jpg' }), photoFixture({ fileName: '/absolute.jpg' }),
    photoFixture({ fileName: 'C:\\outside.jpg' }), photoFixture({ fileName: 'file://private.jpg' }),
    photoFixture({ fileName: '%2e%2e%2foutside.jpg' }), photoFixture({ fileName: 'a.jpg?x=1' }),
    photoFixture({ fileName: 'not-image.dcm' }), photoFixture({ fileName: 'x'.repeat(129) + '.jpg' }),
    photoFixture({ source: 'network' }), photoFixture({ view: '自动识别' }),
    photoFixture({ originalName: 'dir/photo.jpg' }), photoFixture({ originalName: 'dir\\photo.jpg' }),
    photoFixture({ originalName: 'bad\nname.jpg' }), photoFixture({ originalName: 'x'.repeat(121) }),
    photoFixture({ createdAt: -1 }), photoFixture({ createdAt: Infinity })];
  for (const photo of invalidPhotos) {
    await assert.rejects(() => store.saveEntries([fixture({ photos: [photo] })]));
  }
  for (const photos of [null, {}, 'a.jpg', [photoFixture(), photoFixture()],
    [photoFixture(), photoFixture({ id: 'different' })],
    Array.from({ length: 5 }, (_, i) => photoFixture({ id: 'photo_' + i, fileName: 'file_' + i + '.png' }))]) {
    await assert.rejects(() => store.saveEntries([fixture({ photos })]));
  }
  assert.equal(env.state.mutations, mutationCheckpoint);
  assert.deepEqual(clone(await store.initialize(context)), [fixture()]);
});

test('attachment backup roundtrip includes only validated metadata and no image payloads', () => {
  const env = platform();
  const record = fixture({ category: '图片记录', minutes: 0, photos: [photoFixture({
    unknown: 'SHOULD_NOT_EXPORT', sourceUri: 'external://private/photo.jpg', imageBase64: 'RAW_IMAGE_BYTES'
  })] });
  const exported = env.createJournalFile([record]);
  const envelope = JSON.parse(exported);
  assert.equal(envelope.schemaVersion, 1);
  assert.equal(envelope.entries[0].photos[0].fileName, photoFixture().fileName);
  assert.equal(exported.includes('SHOULD_NOT_EXPORT'), false);
  assert.equal(exported.includes('external://'), false);
  assert.equal(exported.includes('RAW_IMAGE_BYTES'), false);
  assert.deepEqual(clone(env.parseJournalFile(exported)), [fixture({
    category: '图片记录', minutes: 0, photos: [photoFixture()]
  })]);
});

test('empty attachments and supported extra view remain explicit and valid', () => {
  const env = platform();
  assert.deepEqual(clone(env.validateJournalEntries([fixture({ photos: [] })])), [fixture({ photos: [] })]);
  const photo = photoFixture({ source: 'album', view: '其他', originalName: '' });
  assert.deepEqual(clone(env.validateRecordPhotos([photo])), [photo]);
});

test('invalid save does not overwrite existing data or lock a healthy store', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries([fixture()]);
  const mutationsBefore = env.state.mutations;
  await assert.rejects(() => store.saveEntries([fixture({ minutes: -1 })]));
  assert.equal(env.state.mutations, mutationsBefore);
  const changed = fixture({ minutes: 42 });
  await store.saveEntries([changed]);
  assert.deepEqual(clone(await env.restart().initialize(context)), [changed]);
});

test('cancelled file pickers leave current data and the filesystem untouched', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries([fixture()]);
  const mutationsBefore = env.state.mutations;
  assert.equal(await store.importFile(context), undefined);
  assert.equal(await store.exportFile(context, [fixture()]), false);
  assert.equal(env.state.opens, 0);
  assert.equal(env.state.writes, 0);
  assert.equal(env.state.mutations, mutationsBefore);
  assert.deepEqual(clone(await store.initialize(context)), [fixture()]);
});

test('export/import roundtrip handles partial Unicode reads/writes and requires a separate save', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  const entries = [fixture({ note: '中文与 emoji 🌱\n第二行' }), fixture({ id: 'other', category: '日常活动' })];
  env.state.saveUris = ['memory://roundtrip.json'];
  env.state.writeLimit = 7;
  env.state.readLimit = 5;
  assert.equal(await store.exportFile(context, entries), true);
  assert.equal(env.state.fsyncs, 1);
  env.state.selectUris = env.state.saveUris;
  const mutationsBefore = env.state.mutations;
  const imported = await store.importFile(context);
  assert.deepEqual(clone(imported), entries);
  assert.equal(env.state.mutations, mutationsBefore, 'Import only returns a candidate before confirmation.');
  assert.deepEqual(clone(await store.initialize(context)), []);
  await store.saveEntries(imported);
  assert.deepEqual(clone(await env.restart().initialize(context)), entries);
  assert.equal(env.state.opens, env.state.closes);
});

test('malformed UTF-8 is rejected instead of silently replacing bytes', async () => {
  const env = platform();
  env.importText(Buffer.from([0x7b, 0xc3, 0x28, 0x7d]));
  await assert.rejects(() => new env.JournalStore().importFile(context));
  assert.equal(env.state.opens, env.state.closes);
});

test('file size guards count UTF-8 bytes and detect growth after stat', async () => {
  const env = platform();
  const oversizedText = '汉'.repeat(Math.floor(MAX_BYTES / 3) + 1);
  assert.ok(oversizedText.length < MAX_BYTES);
  assert.throws(() => env.parseJournalFile(oversizedText));
  // This is otherwise valid JSON, so the growing-file assertion would fail if
  // the implementation relied only on stat() and omitted the read-size guard.
  env.importText(backup([]) + ' '.repeat(MAX_BYTES));
  await assert.rejects(() => new env.JournalStore().importFile(context));
  env.state.statSize = 1; // The selected file grows between stat and read.
  await assert.rejects(() => new env.JournalStore().importFile(context));
  assert.equal(env.state.mutations, 0);
  assert.equal(env.state.opens, env.state.closes);
});

test('an otherwise valid oversized journal cannot become unexportable persisted data', async () => {
  const env = platform();
  const entries = Array.from({ length: 2000 }, (_, i) => fixture({
    id: ('item_' + i).padEnd(128, '_'), note: '汉'.repeat(300)
  }));
  assert.ok(Buffer.byteLength(backup(entries), 'utf8') > MAX_BYTES);
  assert.throws(() => env.createJournalFile(entries));
  const store = new env.JournalStore();
  await store.initialize(context);
  await assert.rejects(() => store.saveEntries(entries));
  assert.equal(env.state.mutations, 0);
  // A rejected over-limit request is a validation error, not a disk failure.
  await store.saveEntries([fixture()]);
  assert.deepEqual(clone(await env.restart().initialize(context)), [fixture()]);
});

test('export reports write/fsync errors and closes handles rather than claiming success', async () => {
  for (const flag of ['failWrite', 'zeroWrite', 'failFsync']) {
    const env = platform();
    env.state.saveUris = ['memory://failure.json'];
    env.state[flag] = true;
    await assert.rejects(() => new env.JournalStore().exportFile(context, [fixture()]), flag);
    assert.equal(env.state.opens, env.state.closes, flag);
  }
});

test('save before initialization is rejected without any disk changes', async () => {
  const env = platform();
  await assert.rejects(() => new env.JournalStore().saveEntries([fixture()]));
  assert.equal(env.state.mutations, 0);
  assert.equal(env.state.flushes, 0);
});

test('write failure preserves the old durable records and blocks further writes until restart', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries([fixture()]);
  env.state.failMutation = true;
  await assert.rejects(() => store.saveEntries([fixture({ minutes: 99 })]));
  env.state.failMutation = false;
  await assert.rejects(() => store.saveEntries([]));
  assert.deepEqual(clone(await env.restart().initialize(context)), [fixture()]);
});

for (const phase of [1, 2]) {
  test('durability failure at commit stage ' + phase + ' never replaces data with a partial journal', async () => {
    const env = platform();
    const store = new env.JournalStore();
    await store.initialize(context);
    await store.saveEntries([fixture()]);
    env.state.failFlushAt = env.state.flushes + phase;
    await assert.rejects(() => store.saveEntries([fixture({ minutes: 99 })]));
    env.state.failFlushAt = 0;
    await assert.rejects(() => store.saveEntries([]));
    assert.deepEqual(clone(await env.restart().initialize(context)), [fixture()]);
  });
}

test('a commit that persists then errors stays uncertain until restart and reports no false success', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries([fixture()]);
  const replacement = [fixture({ minutes: 99 }), fixture({ id: 'record_2' })];
  env.state.failFlushAt = env.state.flushes + 2;
  env.state.failFlushAfterCommit = true;
  await assert.rejects(() => store.saveEntries(replacement));
  env.state.failFlushAt = 0;
  await assert.rejects(() => store.saveEntries([]));
  assert.deepEqual(clone(await env.restart().initialize(context)), replacement);
});

test('overlapping saves are rejected instead of interleaving journal snapshots', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  let release;
  env.state.mutationGate = new Promise(resolve => { release = resolve; });
  const pending = store.saveEntries([fixture()]);
  await assert.rejects(() => store.saveEntries([]));
  release();
  await pending;
  assert.deepEqual(clone(await env.restart().initialize(context)), [fixture()]);
});

test('deletion commits the replacement before removing old data, with no hidden old copy after success', async () => {
  const env = platform();
  const store = new env.JournalStore();
  await store.initialize(context);
  const removed = fixture({ note: 'UNIQUE_DELETED_CONTENT' });
  const retained = fixture({ id: 'retained', note: 'KEEP_CONTENT' });
  await store.saveEntries([removed, retained]);
  const firstCheckpoint = env.state.flushHistory.length;
  await store.saveEntries([retained]);
  const checkpoints = env.state.flushHistory.slice(firstCheckpoint);
  assert.ok(checkpoints.length >= 3, 'Persist replacement, commit index, then remove the obsolete copy.');
  const beforeCommit = platform(checkpoints[0]);
  assert.deepEqual(clone(await new beforeCommit.JournalStore().initialize(context)), [removed, retained]);
  const afterCommit = platform(checkpoints[1]);
  assert.deepEqual(clone(await new afterCommit.JournalStore().initialize(context)), [retained]);
  assert.equal(JSON.stringify(Array.from(env.state.durable, ([name, data]) => [name, Array.from(data)]))
    .includes('UNIQUE_DELETED_CONTENT'), false);
  await store.saveEntries([]);
  assert.equal(JSON.stringify(Array.from(env.state.durable, ([name, data]) => [name, Array.from(data)]))
    .includes('KEEP_CONTENT'), false);
  assert.deepEqual(clone(await env.restart().initialize(context)), []);
});

test('old-copy cleanup failure reports an error while keeping the new committed records authoritative', async () => {
  const env = platform();
  let store = new env.JournalStore();
  await store.initialize(context);
  await store.saveEntries([fixture({ note: 'OLD_COPY_PENDING_CLEANUP' })]);
  env.state.failFlushAt = env.state.flushes + 3;
  await assert.rejects(() => store.saveEntries([]), /已保存/);
  env.state.failFlushAt = 0;
  await assert.rejects(() => store.saveEntries([fixture()]));
  store = env.restart();
  assert.deepEqual(clone(await store.initialize(context)), []);
  await store.saveEntries([]);
  assert.equal(JSON.stringify(Array.from(env.state.durable, ([name, data]) => [name, Array.from(data)]))
    .includes('OLD_COPY_PENDING_CLEANUP'), false);
});
