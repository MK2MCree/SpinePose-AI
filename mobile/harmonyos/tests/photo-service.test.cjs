'use strict';

// Exercises the production PhotoService with an in-memory platform boundary.
// No real device, camera, photo library or user files are accessed.
// Run: node mobile/harmonyos/tests/photo-service.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../../..');
const ts = require(path.join(root, 'site/node_modules/typescript'));
const sourcePath = path.resolve(__dirname, '../entry/src/main/ets/service/PhotoService.ets');
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  fileName: 'PhotoService.ts',
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  reportDiagnostics: true
});
assert.equal((compiled.diagnostics || []).filter(item => item.category === ts.DiagnosticCategory.Error).length, 0);

const PRIVATE_DIR = '/data/app/files';
const context = { filesDir: PRIVATE_DIR };
const clone = value => JSON.parse(JSON.stringify(value));

function platform() {
  const files = new Map();
  const directories = new Set([PRIVATE_DIR]);
  const handles = new Map();
  const state = {
    files, directories, handles, nextFd: 1,
    selected: [], cameraUri: '', cameraResultCode: 0,
    pickerError: undefined, cameraError: undefined,
    deleted: [], readPaths: [], writePaths: [], cameraCalls: [], pickerCalls: [],
    failWriteAt: 0, writeCalls: 0, failFsync: false, failRename: false,
    readLimit: Infinity, writeLimit: Infinity, sizes: new Map(),
    pixelMapsReleased: 0, sourcesReleased: 0, packersReleased: 0
  };
  const resolve = ref => {
    if (typeof ref === 'number') {
      if (!handles.has(ref)) throw new Error('Unknown descriptor');
      return handles.get(ref).path;
    }
    if (typeof ref !== 'string') throw new Error('Expected file path or descriptor');
    return ref.startsWith('file://') ? decodeURIComponent(ref.slice(7)) : ref;
  };
  const requireFile = ref => {
    const name = resolve(ref);
    if (!files.has(name)) throw new Error('Missing test file: ' + name);
    return { name, bytes: files.get(name) };
  };
  const fileIo = {
    OpenMode: { READ_ONLY: 1, WRITE_ONLY: 2, READ_WRITE: 3, CREATE: 4, TRUNC: 8, EXCL: 16 },
    async access(name) { return files.has(resolve(name)) || directories.has(resolve(name)); },
    accessSync(name) { return files.has(resolve(name)) || directories.has(resolve(name)); },
    async mkdir(name) { directories.add(resolve(name)); },
    mkdirSync(name) { directories.add(resolve(name)); },
    async open(ref, mode = 1) {
      const name = resolve(ref);
      if (mode & 2) {
        if ((mode & 16) && files.has(name)) throw new Error('Exclusive file already exists');
        if (!files.has(name) && !(mode & 4)) throw new Error('Missing destination');
        if (!files.has(name) || mode & 8) files.set(name, Buffer.alloc(0));
      } else {
        requireFile(name);
      }
      const fd = state.nextFd++;
      handles.set(fd, { path: name, position: 0 });
      return { fd };
    },
    statSync(ref) {
      const name = resolve(ref);
      const size = directories.has(name) ? 0 : state.sizes.has(name) ? state.sizes.get(name) : requireFile(name).bytes.length;
      return { size, isFile() { return files.has(name); }, isDirectory() { return directories.has(name); } };
    },
    async stat(ref) { return this.statSync(ref); },
    async read(fd, buffer, options = {}) {
      const handle = handles.get(fd);
      const { name, bytes } = requireFile(fd);
      state.readPaths.push(name);
      const offset = options.offset === undefined ? handle.position : options.offset;
      const count = Math.max(0, Math.min(buffer.byteLength, state.readLimit, bytes.length - offset));
      new Uint8Array(buffer).set(bytes.subarray(offset, offset + count));
      handle.position = offset + count;
      return count;
    },
    async write(fd, buffer, options = {}) {
      state.writeCalls++;
      const handle = handles.get(fd);
      const name = resolve(fd);
      state.writePaths.push(name);
      if (state.failWriteAt === state.writeCalls) throw new Error('Simulated partial copy failure');
      const data = Buffer.from(buffer);
      const offset = options.offset === undefined ? handle.position : options.offset;
      const count = Math.min(data.length, state.writeLimit);
      const previous = requireFile(fd).bytes;
      const next = Buffer.alloc(Math.max(previous.length, offset + count));
      previous.copy(next);
      data.copy(next, offset, 0, count);
      files.set(name, next);
      handle.position = offset + count;
      return count;
    },
    async fsync() { if (state.failFsync) throw new Error('Simulated fsync failure'); },
    async close(fd) {
      assert.ok(handles.has(fd), 'Live descriptors should be closed exactly once');
      handles.delete(fd);
    },
    async rename(from, to) {
      if (state.failRename) throw new Error('Simulated rename failure');
      const oldName = resolve(from);
      const newName = resolve(to);
      const bytes = requireFile(oldName).bytes;
      files.set(newName, bytes);
      files.delete(oldName);
    },
    async unlink(ref) {
      const name = resolve(ref);
      state.deleted.push(name);
      assert.ok(name.startsWith(PRIVATE_DIR + '/record-photos/'), 'Never delete a selected source or outside the private photo directory');
      if (!files.delete(name)) throw new Error('Missing file for deletion');
    },
    async listFile(dir) {
      return this.listFileSync(dir);
    },
    listFileSync(dir) {
      const prefix = resolve(dir) + '/';
      return Array.from(files.keys()).filter(name => name.startsWith(prefix) && !name.slice(prefix.length).includes('/'))
        .map(name => name.slice(prefix.length));
    }
  };
  const image = {
    createImageSource(ref) {
      const { bytes } = requireFile(ref);
      return {
        async getImageInfo() {
          if (!bytes.toString('utf8').startsWith('JPEG_SAMPLE') && !bytes.toString('utf8').startsWith('PNG_SAMPLE')) {
            throw new Error('Not an image');
          }
          return { size: { width: 640, height: 960 }, mimeType: bytes.toString('utf8').startsWith('PNG') ? 'image/png' : 'image/jpeg' };
        },
        async createPixelMap() { return { async release() { state.pixelMapsReleased++; } }; },
        async release() { state.sourcesReleased++; }
      };
    },
    createImagePacker() {
      return {
        async packing() {
          const bytes = Buffer.from('JPEG_SAMPLE_THUMBNAIL');
          return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
        },
        async release() { state.packersReleased++; }
      };
    }
  };
  const photoAccessHelper = {
    PhotoViewMIMETypes: { IMAGE_TYPE: 'image/*' },
    PhotoSelectOptions: class {},
    PhotoViewPicker: class {
      async select(options) {
        state.pickerCalls.push(clone(options));
        if (state.pickerError) throw state.pickerError;
        return { photoUris: state.selected.slice() };
      }
    }
  };
  const cameraPicker = {
    PickerMediaType: { PHOTO: 'photo' },
    async pick(_context, types, profile) {
      state.cameraCalls.push({ types, profile });
      if (state.cameraError) throw state.cameraError;
      return { resultCode: state.cameraResultCode, resultUri: state.cameraUri, mediaType: 'photo' };
    }
  };
  const modules = {
    '@kit.AbilityKit': {},
    '@kit.CameraKit': { cameraPicker, camera: { CameraPosition: { CAMERA_POSITION_BACK: 1 } } },
    '@kit.MediaLibraryKit': { photoAccessHelper },
    '@kit.CoreFileKit': { fileIo, fileUri: { getUriFromPath: value => 'file://' + value } },
    '@kit.ImageKit': { image }
  };
  const exports = {};
  vm.runInNewContext(compiled.outputText, {
    exports,
    require(name) {
      assert.ok(Object.hasOwn(modules, name), 'Unexpected service dependency: ' + name);
      return modules[name];
    }
  }, { filename: sourcePath });
  return {
    ...exports, state,
    source(uri = 'photo://camera/source.jpg', text = 'JPEG_SAMPLE_ORIGINAL') {
      files.set(uri, Buffer.from(text));
      return uri;
    },
    privateFiles() { return Array.from(files.keys()).filter(name => name.startsWith(PRIVATE_DIR + '/')); }
  };
}

async function initialized() {
  const env = platform();
  env.service = new env.PhotoService();
  await env.service.initialize(context);
  return env;
}

test('cancelled image selection creates no attachments or private files', async () => {
  const env = await initialized();
  assert.deepEqual(clone(await env.service.choosePhotos(context, '其他', 4)), []);
  assert.equal(env.privateFiles().length, 0);
  assert.equal(env.state.writeCalls, 0);
  assert.equal(env.state.handles.size, 0);
});

test('cancelled camera creates no attachment or private files', async () => {
  const env = await initialized();
  env.state.cameraResultCode = -1;
  assert.equal(await env.service.takePhoto(context, '正面'), undefined);
  assert.equal(env.privateFiles().length, 0);
  assert.equal(env.state.writeCalls, 0);
});

test('selected pictures are durable private copies independent of the original URI', async () => {
  const env = await initialized();
  const uri = env.source();
  const original = Buffer.from(env.state.files.get(uri));
  env.state.selected = [uri];
  const photos = await env.service.choosePhotos(context, '侧面', 1);
  assert.equal(photos.length, 1);
  const photo = photos[0];
  assert.equal(photo.source, 'album');
  assert.equal(photo.view, '侧面');
  const displayPath = env.service.displayPath(photo);
  assert.ok(displayPath.startsWith(PRIVATE_DIR + '/record-photos/'));
  assert.notEqual(env.service.imageUri(photo), uri);
  assert.deepEqual(env.state.files.get(displayPath), original);
  assert.ok(env.service.exists(photo));
  assert.ok(env.service.thumbnailUri(photo).startsWith('file://'));
  env.state.files.delete(uri);
  assert.ok(env.service.exists(photo));
  assert.deepEqual(env.state.files.get(displayPath), original);
  assert.equal(env.state.handles.size, 0);
  assert.equal(env.state.sourcesReleased, 1);
  assert.equal(env.state.pixelMapsReleased, 1);
  assert.equal(env.state.packersReleased, 1);
});

test('successful camera attachment records the chosen view and camera source', async () => {
  const env = await initialized();
  env.state.cameraUri = env.source('photo://camera/captured.jpg');
  const photo = await env.service.takePhoto(context, '背面');
  assert.ok(photo);
  assert.equal(photo.source, 'camera');
  assert.equal(photo.view, '背面');
  assert.ok(env.service.exists(photo));
  assert.equal(env.state.cameraCalls.length, 1);
  assert.ok(env.state.files.has(env.state.cameraUri), 'The system camera result is not deleted');
});

test('partial reads and writes preserve the complete original contents', async () => {
  const env = await initialized();
  const uri = env.source('photo://album/long.jpg', 'JPEG_SAMPLE_' + '原图数据'.repeat(8000));
  env.state.readLimit = 17;
  env.state.writeLimit = 7;
  env.state.selected = [uri];
  const [photo] = await env.service.choosePhotos(context, '其他', 1);
  assert.deepEqual(env.state.files.get(env.service.displayPath(photo)), env.state.files.get(uri));
  assert.equal(env.state.handles.size, 0);
});

test('a failed file copy removes its incomplete private files and preserves the source', async () => {
  const env = await initialized();
  const uri = env.source('photo://album/copy-fails.jpg', 'JPEG_SAMPLE_' + 'x'.repeat(200));
  env.state.selected = [uri];
  env.state.readLimit = 17;
  env.state.failWriteAt = 2;
  await assert.rejects(env.service.choosePhotos(context, '其他', 1));
  assert.equal(env.privateFiles().length, 0);
  assert.ok(env.state.files.has(uri));
  assert.equal(env.state.handles.size, 0);
});

test('non-images are rejected without a durable attachment', async () => {
  const env = await initialized();
  const uri = env.source('photo://album/fake.jpg', 'NOT_AN_IMAGE');
  env.state.selected = [uri];
  await assert.rejects(env.service.choosePhotos(context, '其他', 1));
  assert.equal(env.privateFiles().length, 0);
  assert.ok(env.state.files.has(uri));
  assert.equal(env.state.handles.size, 0);
});

test('oversized source images are rejected before being persisted', async () => {
  const env = await initialized();
  const uri = env.source('photo://album/oversized.jpg');
  env.state.sizes.set(uri, 20 * 1024 * 1024 + 1);
  env.state.selected = [uri];
  await assert.rejects(env.service.choosePhotos(context, '其他', 1));
  assert.equal(env.privateFiles().length, 0);
  assert.ok(env.state.files.has(uri));
});

test('an unavailable camera reports failure without creating files', async () => {
  const env = await initialized();
  env.state.cameraError = new Error('Simulated unavailable system camera');
  await assert.rejects(env.service.takePhoto(context, '斜侧面'));
  assert.equal(env.privateFiles().length, 0);
  assert.equal(env.state.handles.size, 0);
});

test('a failing multi-selection removes prior new attachments but keeps all selected sources', async () => {
  const env = await initialized();
  const first = env.source('photo://album/accepted-first.jpg');
  const second = env.source('photo://album/rejected-second.jpg', 'NOT_AN_IMAGE');
  env.state.selected = [first, second];
  await assert.rejects(env.service.choosePhotos(context, '其他', 2));
  assert.equal(env.privateFiles().length, 0);
  assert.ok(env.state.files.has(first));
  assert.ok(env.state.files.has(second));
  assert.equal(env.state.handles.size, 0);
});

test('invalid view, selection limit and excess picker results cannot add files', async () => {
  const env = await initialized();
  await assert.rejects(env.service.takePhoto(context, 'not-a-view'));
  assert.equal(env.state.cameraCalls.length, 0);
  for (const count of [0, 5, -1, 1.5]) {
    await assert.rejects(env.service.choosePhotos(context, '其他', count));
  }
  assert.equal(env.state.pickerCalls.length, 0);
  env.state.selected = [env.source('photo://album/a.jpg'), env.source('photo://album/b.jpg')];
  await assert.rejects(env.service.choosePhotos(context, '其他', 1));
  assert.equal(env.privateFiles().length, 0);
});

test('path traversal and absolute file names cannot escape the private directory', async () => {
  const env = await initialized();
  for (const fileName of ['../outside.jpg', '/outside.jpg', '..\\outside.jpg', 'a/../outside.jpg', 'file:///outside.jpg']) {
    const photo = { id: 'fake', fileName, source: 'album', view: '其他', originalName: '', createdAt: 1 };
    assert.throws(() => env.service.displayPath(photo));
    assert.equal(env.service.exists(photo), false);
    await assert.rejects(env.service.removePhotos([photo]));
  }
  assert.equal(env.state.deleted.length, 0);
});

test('removal deletes private copies and thumbnails without deleting the selected originals', async () => {
  const env = await initialized();
  const uri = env.source('photo://album/keep-original.jpg');
  env.state.selected = [uri];
  const photos = await env.service.choosePhotos(context, '其他', 1);
  await env.service.removePhotos(photos);
  assert.equal(env.privateFiles().length, 0);
  assert.ok(env.state.files.has(uri));
  assert.ok(env.state.deleted.every(name => name.startsWith(PRIVATE_DIR + '/record-photos/')));
});

test('reconcile retains shared references and removes only unreferenced private pictures', async () => {
  const env = await initialized();
  const first = env.source('photo://album/first.jpg');
  const second = env.source('photo://album/second.jpg');
  env.state.selected = [first, second];
  const photos = await env.service.choosePhotos(context, '其他', 2);
  assert.equal(photos.length, 2);
  await env.service.reconcile([photos[0], photos[0]]);
  assert.ok(env.service.exists(photos[0]));
  assert.equal(env.service.exists(photos[1]), false);
  assert.ok(env.state.files.has(first));
  assert.ok(env.state.files.has(second));
  assert.ok(env.state.deleted.every(name => name.startsWith(PRIVATE_DIR + '/record-photos/')));
});

test('reconcile clears abandoned staging files while leaving unrelated app files alone', async () => {
  const env = await initialized();
  const pending = PRIVATE_DIR + '/record-photos/photo_123_456.stage';
  const unrelated = PRIVATE_DIR + '/record-photos/notes.txt';
  const outside = PRIVATE_DIR + '/other-module/photo_123_456.jpg';
  env.state.files.set(pending, Buffer.from('partial copy'));
  env.state.files.set(unrelated, Buffer.from('unrelated data'));
  env.state.files.set(outside, Buffer.from('other module data'));
  await env.service.reconcile([]);
  assert.equal(env.state.files.has(pending), false);
  assert.ok(env.state.files.has(unrelated));
  assert.ok(env.state.files.has(outside));
});
