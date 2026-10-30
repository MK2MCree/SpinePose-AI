import { common } from '@kit.AbilityKit';
import { preferences } from '@kit.ArkData';
import { fileIo, picker } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';
import { RecordPhoto } from '../service/PhotoService';

export interface JournalEntry {
  id: string;
  day: string;
  category: string;
  minutes: number;
  note: string;
  createdAt: number;
  updatedAt: number;
  photos?: RecordPhoto[];
}

export interface FilePayload {
  schemaVersion: number;
  exportedAt: string;
  entries: JournalEntry[];
}

const MAX_ENTRIES: number = 2000;
const MAX_FILE_BYTES: number = 2 * 1024 * 1024;
const MAX_TIMESTAMP: number = 8640000000000000;
const MAX_PHOTOS: number = 4;
const MAX_PREFERENCE_CHARS: number = 8192;
const CONTROL_NAME: string = 'activity_journal_control_v1';
const SLOT_A_NAME: string = 'activity_journal_snapshot_a_v1';
const SLOT_B_NAME: string = 'activity_journal_snapshot_b_v1';

function copyEntry(entry: JournalEntry): JournalEntry {
  const copy: JournalEntry = {
    id: entry.id,
    day: entry.day,
    category: entry.category,
    minutes: entry.minutes,
    note: entry.note,
    createdAt: entry.createdAt,
    updatedAt: entry.updatedAt
  };
  if (entry.photos !== undefined) {
    copy.photos = validateRecordPhotos(entry.photos);
  }
  return copy;
}

export function isValidJournalDay(day: string): boolean {
  if (typeof day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day)) {
    return false;
  }
  const year: number = Number(day.substring(0, 4));
  const month: number = Number(day.substring(5, 7));
  const date: number = Number(day.substring(8, 10));
  if (year < 1 || month < 1 || month > 12 || date < 1) {
    return false;
  }
  const leap: boolean = year % 400 === 0 || (year % 4 === 0 && year % 100 !== 0);
  const days: number[] = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return date <= days[month - 1];
}

function isValidTimestamp(value: number): boolean {
  return typeof value === 'number' && Number.isFinite(value) &&
    Number.isInteger(value) && value >= 0 && value <= MAX_TIMESTAMP;
}

/** Keeps attachments as local metadata, never image bytes or external URIs. */
export function validateRecordPhotos(photos: RecordPhoto[]): RecordPhoto[] {
  if (!Array.isArray(photos) || photos.length > MAX_PHOTOS) {
    throw new Error('每条记录最多保存 4 张图片。');
  }
  const result: RecordPhoto[] = [];
  const ids: Set<string> = new Set<string>();
  const files: Set<string> = new Set<string>();
  for (let index: number = 0; index < photos.length; index++) {
    const photo: RecordPhoto = photos[index];
    if (photo === null || typeof photo !== 'object' || Array.isArray(photo)) {
      throw new Error('图片记录格式不正确。');
    }
    if (typeof photo.id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(photo.id) || ids.has(photo.id)) {
      throw new Error('图片编号无效或重复。');
    }
    // A managed basename cannot escape the app's record-photos directory.
    if (typeof photo.fileName !== 'string' || photo.fileName.length > 128 ||
      !/^[A-Za-z0-9_-]{1,120}\.(jpg|jpeg|png|webp|heic|heif)$/i.test(photo.fileName) ||
      files.has(photo.fileName)) {
      throw new Error('图片本地文件名无效或重复。');
    }
    if (photo.source !== 'camera' && photo.source !== 'album') {
      throw new Error('图片来源不受支持。');
    }
    if (photo.view !== '正面' && photo.view !== '侧面' && photo.view !== '背面' &&
      photo.view !== '斜侧面' && photo.view !== '其他') {
      throw new Error('图片视角不受支持。');
    }
    if (typeof photo.originalName !== 'string' || photo.originalName.length > 120 ||
      /[\\/\x00-\x1F]/.test(photo.originalName)) {
      throw new Error('图片原始文件名无效。');
    }
    if (!isValidTimestamp(photo.createdAt)) {
      throw new Error('图片保存时间无效。');
    }
    ids.add(photo.id);
    files.add(photo.fileName);
    result.push({
      id: photo.id,
      fileName: photo.fileName,
      source: photo.source,
      view: photo.view,
      originalName: photo.originalName,
      createdAt: photo.createdAt
    });
  }
  return result;
}

/** Validates untrusted values and reconstructs only the supported fields. */
export function validateJournalEntries(entries: JournalEntry[]): JournalEntry[] {
  if (!Array.isArray(entries) || entries.length > MAX_ENTRIES) {
    throw new Error('记录必须是数组，且不能超过 2000 条。');
  }
  const result: JournalEntry[] = [];
  const ids: Set<string> = new Set<string>();
  for (let index: number = 0; index < entries.length; index++) {
    const entry: JournalEntry = entries[index];
    if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) {
      throw new Error(`第 ${index + 1} 条记录格式不正确。`);
    }
    if (typeof entry.id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(entry.id) || ids.has(entry.id)) {
      throw new Error(`第 ${index + 1} 条记录的编号无效或重复。`);
    }
    if (!isValidJournalDay(entry.day)) {
      throw new Error(`第 ${index + 1} 条记录的日期无效，请使用 YYYY-MM-DD。`);
    }
    if (typeof entry.category !== 'string' ||
      (entry.category !== '步行' && entry.category !== '日常活动' && entry.category !== '休息' &&
      entry.category !== '图片记录')) {
      throw new Error(`第 ${index + 1} 条记录的活动类别不受支持。`);
    }
    if (typeof entry.minutes !== 'number' || !Number.isInteger(entry.minutes) ||
      entry.minutes < 0 || entry.minutes > 1440) {
      throw new Error(`第 ${index + 1} 条记录的时长必须是 0 至 1440 的整数。`);
    }
    if (typeof entry.note !== 'string' || entry.note.length > 300) {
      throw new Error(`第 ${index + 1} 条记录的备注不能超过 300 个字符。`);
    }
    if (!isValidTimestamp(entry.createdAt) || !isValidTimestamp(entry.updatedAt) ||
      entry.updatedAt < entry.createdAt) {
      throw new Error(`第 ${index + 1} 条记录的创建或修改时间无效。`);
    }
    const clean: JournalEntry = copyEntry(entry);
    if (JSON.stringify(clean).length > MAX_PREFERENCE_CHARS) {
      throw new Error(`第 ${index + 1} 条记录内容过长，请减少备注或图片文件名长度。`);
    }
    ids.add(entry.id);
    result.push(clean);
  }
  return result;
}

function assertFileSize(bytes: number): void {
  if (bytes > MAX_FILE_BYTES) {
    throw new Error('记录文件超过 2 MB，请减少记录或备注内容。');
  }
}

export function createJournalFile(entries: JournalEntry[]): string {
  const payload: FilePayload = {
    schemaVersion: 1,
    exportedAt: new Date().toISOString(),
    entries: validateJournalEntries(entries)
  };
  const text: string = JSON.stringify(payload);
  assertFileSize(new util.TextEncoder().encodeInto(text).byteLength);
  return text;
}

/** No value read from JSON is trusted merely because it has a TypeScript type. */
export function parseJournalFile(text: string): JournalEntry[] {
  assertFileSize(new util.TextEncoder().encodeInto(text).byteLength);
  let raw: Object;
  try {
    raw = JSON.parse(text) as Object;
  } catch (_) {
    throw new Error('文件不是有效的 JSON 记录备份。');
  }
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('备份文件的顶层格式不正确。');
  }
  const payload: FilePayload = raw as FilePayload;
  if (payload.schemaVersion !== 1) {
    throw new Error('不支持此备份版本，仅支持 schemaVersion 为 1 的文件。');
  }
  if (typeof payload.exportedAt !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(payload.exportedAt)) {
    throw new Error('备份文件的导出时间格式无效。');
  }
  const exportedDate: Date = new Date(payload.exportedAt);
  if (!Number.isFinite(exportedDate.getTime()) || exportedDate.toISOString() !== payload.exportedAt) {
    throw new Error('备份文件的导出时间无效。');
  }
  return validateJournalEntries(payload.entries);
}

/**
 * Each entry is below the Preferences 8192-character value limit. Two separate
 * snapshots keep a partially written replacement away from the active data.
 * The control pointer is committed only after the new snapshot is flushed.
 */
export class JournalStore {
  private control: preferences.Preferences | undefined = undefined;
  private slotA: preferences.Preferences | undefined = undefined;
  private slotB: preferences.Preferences | undefined = undefined;
  private activeSlot: string = '';
  private entries: JournalEntry[] = [];
  private ready: boolean = false;
  private saving: boolean = false;
  private storageFailed: boolean = false;

  async initialize(context: common.UIAbilityContext): Promise<JournalEntry[]> {
    if (this.storageFailed) {
      throw new Error('本地存储发生错误，请重启应用并核对记录后重试。');
    }
    if (this.ready) {
      return validateJournalEntries(this.entries);
    }
    const control: preferences.Preferences = await preferences.getPreferences(context, { name: CONTROL_NAME });
    const slotA: preferences.Preferences = await preferences.getPreferences(context, { name: SLOT_A_NAME });
    const slotB: preferences.Preferences = await preferences.getPreferences(context, { name: SLOT_B_NAME });
    const active: preferences.ValueType = await control.get('active', '');
    if (active !== '' && active !== 'a' && active !== 'b') {
      throw new Error('本地记录索引格式异常；请保留应用数据并联系开发者。');
    }
    let loaded: JournalEntry[] = [];
    if (active === 'a' || active === 'b') {
      const snapshot: preferences.Preferences = active === 'a' ? slotA : slotB;
      const version: preferences.ValueType = await snapshot.get('schemaVersion', 0);
      const count: preferences.ValueType = await snapshot.get('entryCount', -1);
      if (version !== 1 || typeof count !== 'number' || !Number.isInteger(count) ||
        count < 0 || count > MAX_ENTRIES) {
        throw new Error('本地记录快照不完整；请保留应用数据并联系开发者。');
      }
      for (let index: number = 0; index < count; index++) {
        const value: preferences.ValueType = snapshot.getSync(`entry_${index}`, '');
        if (typeof value !== 'string' || value.length === 0) {
          throw new Error('本地记录快照不完整；请保留应用数据并联系开发者。');
        }
        try {
          loaded.push(JSON.parse(value) as JournalEntry);
        } catch (_) {
          throw new Error('本地记录格式异常；请保留应用数据并联系开发者。');
        }
      }
      loaded = validateJournalEntries(loaded);
    }
    this.control = control;
    this.slotA = slotA;
    this.slotB = slotB;
    this.activeSlot = active as string;
    this.entries = validateJournalEntries(loaded);
    this.ready = true;
    return validateJournalEntries(this.entries);
  }

  async saveEntries(entries: JournalEntry[]): Promise<void> {
    if (!this.ready || this.storageFailed || !this.control || !this.slotA || !this.slotB) {
      throw new Error('本地存储尚未就绪，请重启应用并核对记录后重试。');
    }
    if (this.saving) {
      throw new Error('正在保存上一项更改，请稍后重试。');
    }
    const clean: JournalEntry[] = validateJournalEntries(entries);
    // Keeping stored data exportable also prevents an oversized future backup.
    createJournalFile(clean);
    const nextSlot: string = this.activeSlot === 'a' ? 'b' : 'a';
    const target: preferences.Preferences = nextSlot === 'a' ? this.slotA : this.slotB;
    const previous: preferences.Preferences | undefined = this.activeSlot === 'a' ?
      this.slotA : (this.activeSlot === 'b' ? this.slotB : undefined);
    this.saving = true;
    let committed: boolean = false;
    try {
      target.clearSync();
      for (let index: number = 0; index < clean.length; index++) {
        target.putSync(`entry_${index}`, JSON.stringify(clean[index]));
      }
      target.putSync('schemaVersion', 1);
      target.putSync('entryCount', clean.length);
      await target.flush();
      this.control.putSync('active', nextSlot);
      await this.control.flush();
      committed = true;
      this.activeSlot = nextSlot;
      this.entries = validateJournalEntries(clean);
      // Once the pointer is durable the former snapshot is no longer needed.
      // Remove it so deleted/cleared records are not retained as a hidden copy.
      if (previous) {
        previous.clearSync();
        await previous.flush();
      }
    } catch (_) {
      // A failed commit has an uncertain durable result. Block further writes
      // until a new process reloads the committed pointer instead of guessing.
      this.storageFailed = true;
      this.ready = false;
      throw new Error(committed ? '本次更改已保存，但旧记录副本清理未完成。请重启应用，核对记录后重新保存或清空。' :
        '本次保存结果未确认，请重启应用并核对记录后重试。');
    } finally {
      this.saving = false;
    }
  }

  async exportFile(context: common.UIAbilityContext, entries: JournalEntry[]): Promise<boolean> {
    const text: string = createJournalFile(entries);
    const bytes: Uint8Array = new util.TextEncoder().encodeInto(text);
    const documentPicker: picker.DocumentViewPicker = new picker.DocumentViewPicker(context);
    const options: picker.DocumentSaveOptions = new picker.DocumentSaveOptions();
    options.newFileNames = [`SpineInsight-records-${new Date().toISOString().substring(0, 10)}.json`];
    options.fileSuffixChoices = ['JSON 记录备份|.json'];
    const uris: string[] = await documentPicker.save(options);
    if (uris.length === 0) {
      return false;
    }
    const file: fileIo.File = await fileIo.open(uris[0],
      fileIo.OpenMode.WRITE_ONLY | fileIo.OpenMode.CREATE | fileIo.OpenMode.TRUNC);
    try {
      let offset: number = 0;
      while (offset < bytes.byteLength) {
        const chunk: ArrayBuffer = bytes.buffer.slice(offset) as ArrayBuffer;
        const written: number = await fileIo.write(file.fd, chunk, { offset: offset });
        if (written <= 0) {
          throw new Error('备份文件未能完整写入，请重新导出。');
        }
        offset += written;
      }
      await fileIo.fsync(file.fd);
    } finally {
      await fileIo.close(file.fd);
    }
    return true;
  }

  /** Returns a validated candidate only; callers must confirm before saving it. */
  async importFile(context: common.UIAbilityContext): Promise<JournalEntry[] | undefined> {
    const documentPicker: picker.DocumentViewPicker = new picker.DocumentViewPicker(context);
    const options: picker.DocumentSelectOptions = new picker.DocumentSelectOptions();
    options.maxSelectNumber = 1;
    options.fileSuffixFilters = ['JSON 记录备份|.json'];
    const uris: string[] = await documentPicker.select(options);
    if (uris.length === 0) {
      return undefined;
    }
    const file: fileIo.File = await fileIo.open(uris[0], fileIo.OpenMode.READ_ONLY);
    try {
      assertFileSize(fileIo.statSync(file.fd).size);
      const bytes: Uint8Array = new Uint8Array(MAX_FILE_BYTES + 1);
      let total: number = 0;
      while (total < bytes.byteLength) {
        const chunk: ArrayBuffer = new ArrayBuffer(Math.min(65536, bytes.byteLength - total));
        const count: number = await fileIo.read(file.fd, chunk, { offset: total });
        if (count === 0) {
          break;
        }
        bytes.set(new Uint8Array(chunk, 0, count), total);
        total += count;
      }
      assertFileSize(total);
      if (total === 0) {
        throw new Error('所选文件为空，无法导入。');
      }
      const decoder: util.TextDecoder = util.TextDecoder.create('utf-8', { fatal: true });
      const text: string = decoder.decodeToString(bytes.subarray(0, total));
      return parseJournalFile(text);
    } finally {
      await fileIo.close(file.fd);
    }
  }
}
