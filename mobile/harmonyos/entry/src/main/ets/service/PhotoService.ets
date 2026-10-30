import { common } from '@kit.AbilityKit';
import { camera, cameraPicker } from '@kit.CameraKit';
import { photoAccessHelper } from '@kit.MediaLibraryKit';
import { fileIo, fileUri } from '@kit.CoreFileKit';
import { image } from '@kit.ImageKit';

export interface RecordPhoto {
  id: string;
  fileName: string;
  source: string;
  view: string;
  originalName: string;
  createdAt: number;
}

const MAX_BYTES: number = 20 * 1024 * 1024;
const MAX_PIXELS: number = 64000000;
const MAX_DIMENSION: number = 16000;
const MAX_PHOTOS: number = 4;
const THUMBNAIL_EDGE: number = 512;
const SAFE_FILE_NAME: RegExp = /^[A-Za-z0-9_-]{1,120}\.(jpg|jpeg|png|webp|heic|heif)$/i;
const MANAGED_FILE: RegExp = /^photo_[0-9]+_[0-9]+\.(jpg|jpeg|png|webp|heic|heif)(\.thumb\.jpg)?$/i;
const TEMP_FILE: RegExp = /^photo_[0-9]+_[0-9]+\.(stage|thumb\.stage)$/;

function validView(view: string): boolean {
  return view === '正面' || view === '侧面' || view === '背面' || view === '斜侧面' || view === '其他';
}

function extensionFor(mime: string): string {
  switch (mime.toLowerCase()) {
    case 'image/jpeg':
      return 'jpg';
    case 'image/png':
      return 'png';
    case 'image/webp':
      return 'webp';
    case 'image/heic':
    case 'image/heif':
      return 'heic';
    default:
      throw new Error('请选择可读取的 JPG、PNG、WebP 或 HEIC 图片。');
  }
}

function sourceName(uri: string, fallback: string): string {
  const name: string = uri.split('?')[0].substring(uri.split('?')[0].lastIndexOf('/') + 1);
  let decoded: string = name;
  try {
    decoded = decodeURIComponent(name);
  } catch (_) {
    decoded = name;
  }
  const cleaned: string = decoded.replace(/[\\/\u0000-\u001f\u007f]/g, '').substring(0, 120);
  return cleaned.length > 0 ? cleaned : fallback;
}

/** System-selected photos are copied into this app's own directory. */
export class PhotoService {
  private directory: string = '';

  async initialize(context: common.UIAbilityContext): Promise<void> {
    if (!context.filesDir || context.filesDir.length === 0) {
      throw new Error('无法打开本机图片目录，请重启应用后重试。');
    }
    const directory: string = `${context.filesDir}/record-photos`;
    if (!fileIo.accessSync(directory)) {
      await fileIo.mkdir(directory, true);
    }
    if (!fileIo.statSync(directory).isDirectory()) {
      throw new Error('本机图片目录不可用，请保留应用数据并联系支持。');
    }
    this.directory = directory;
  }

  private requireReady(): void {
    if (this.directory.length === 0) {
      throw new Error('本机图片目录尚未就绪。');
    }
  }

  private photoPath(photo: RecordPhoto): string {
    this.requireReady();
    if (typeof photo.fileName !== 'string' || !SAFE_FILE_NAME.test(photo.fileName)) {
      throw new Error('图片文件名无效。');
    }
    return `${this.directory}/${photo.fileName}`;
  }

  imageUri(photo: RecordPhoto): string {
    return fileUri.getUriFromPath(this.photoPath(photo));
  }

  thumbnailUri(photo: RecordPhoto): string {
    const path: string = this.photoPath(photo);
    return fileUri.getUriFromPath(fileIo.accessSync(`${path}.thumb.jpg`) ? `${path}.thumb.jpg` : path);
  }

  displayPath(photo: RecordPhoto): string {
    return this.photoPath(photo);
  }

  exists(photo: RecordPhoto): boolean {
    try {
      return fileIo.accessSync(this.photoPath(photo));
    } catch (_) {
      return false;
    }
  }

  async takePhoto(context: common.UIAbilityContext, view: string): Promise<RecordPhoto | undefined> {
    this.requireReady();
    if (!validView(view)) {
      throw new Error('请选择拍摄方向。');
    }
    const profile: cameraPicker.PickerProfile = { cameraPosition: camera.CameraPosition.CAMERA_POSITION_BACK };
    const result: cameraPicker.PickerResult = await cameraPicker.pick(context,
      [cameraPicker.PickerMediaType.PHOTO], profile);
    if (result.resultCode === -1) {
      return undefined;
    }
    if (result.resultCode !== 0) {
      throw new Error('拍摄没有完成，请重新拍摄。');
    }
    if (typeof result.resultUri !== 'string' || result.resultUri.length === 0) {
      return undefined;
    }
    if (result.mediaType !== cameraPicker.PickerMediaType.PHOTO) {
      throw new Error('请选择照片，不支持视频附件。');
    }
    return this.copySelectedPhoto(result.resultUri, 'camera', view);
  }

  async choosePhotos(_context: common.UIAbilityContext, view: string, maxCount: number = 4): Promise<RecordPhoto[]> {
    this.requireReady();
    if (!validView(view) || !Number.isInteger(maxCount) || maxCount < 1 || maxCount > MAX_PHOTOS) {
      throw new Error('本条记录最多添加 4 张图片。');
    }
    const options: photoAccessHelper.PhotoSelectOptions = new photoAccessHelper.PhotoSelectOptions();
    options.MIMEType = photoAccessHelper.PhotoViewMIMETypes.IMAGE_TYPE;
    options.maxSelectNumber = maxCount;
    const picker: photoAccessHelper.PhotoViewPicker = new photoAccessHelper.PhotoViewPicker();
    const result: photoAccessHelper.PhotoSelectResult = await picker.select(options);
    if (result.photoUris.length === 0) {
      return [];
    }
    if (result.photoUris.length > maxCount) {
      throw new Error('选择的图片数量超过本条记录的剩余位置。');
    }
    const photos: RecordPhoto[] = [];
    try {
      for (let index: number = 0; index < result.photoUris.length; index++) {
        photos.push(await this.copySelectedPhoto(result.photoUris[index], 'album', view));
      }
      return photos;
    } catch (_) {
      await this.removePhotos(photos);
      throw new Error('未能添加所选图片，请检查图片格式、文件大小及本机存储空间后重试。');
    }
  }

  private async writeBytes(fd: number, buffer: ArrayBuffer): Promise<void> {
    let offset: number = 0;
    while (offset < buffer.byteLength) {
      const written: number = await fileIo.write(fd, buffer.slice(offset) as ArrayBuffer);
      if (!Number.isInteger(written) || written <= 0 || written > buffer.byteLength - offset) {
        throw new Error('图片未能完整写入，请检查存储空间后重试。');
      }
      offset += written;
    }
  }

  private async copySelectedPhoto(uri: string, source: string, view: string): Promise<RecordPhoto> {
    if (typeof uri !== 'string' || uri.length === 0) {
      throw new Error('没有读取到所选图片。');
    }
    const createdAt: number = Date.now();
    let id: string = '';
    for (let attempt: number = 0; attempt < 10; attempt++) {
      const candidate: string = `photo_${createdAt}_${Math.floor(Math.random() * 1000000000)}`;
      const names: string[] = fileIo.listFileSync(this.directory, { recursion: false });
      if (!names.some((name: string): boolean => name.startsWith(`${candidate}.`))) {
        id = candidate;
        break;
      }
    }
    if (id.length === 0) {
      throw new Error('图片暂时无法保存，请稍后重试。');
    }
    const temporary: string = `${this.directory}/${id}.stage`;
    const thumbnailTemporary: string = `${this.directory}/${id}.thumb.stage`;
    let originalPath: string = '';
    let thumbnailPath: string = '';
    let accepted: boolean = false;
    try {
      const input: fileIo.File = await fileIo.open(uri, fileIo.OpenMode.READ_ONLY);
      try {
        const expectedSize: number = fileIo.statSync(input.fd).size;
        if (expectedSize <= 0 || expectedSize > MAX_BYTES) {
          throw new Error('单张图片应大于 0 字节且不超过 20 MB。');
        }
        const output: fileIo.File = await fileIo.open(temporary,
          fileIo.OpenMode.WRITE_ONLY | fileIo.OpenMode.CREATE | fileIo.OpenMode.TRUNC);
        try {
          let total: number = 0;
          while (true) {
            const buffer: ArrayBuffer = new ArrayBuffer(65536);
            const count: number = await fileIo.read(input.fd, buffer);
            if (count === 0) {
              break;
            }
            if (!Number.isInteger(count) || count < 0 || count > buffer.byteLength || total + count > MAX_BYTES) {
              throw new Error('图片超过 20 MB 或读取异常，请重新选择。');
            }
            await this.writeBytes(output.fd, buffer.slice(0, count) as ArrayBuffer);
            total += count;
          }
          if (total !== expectedSize) {
            throw new Error('所选图片在读取时发生变化，请重新选择。');
          }
          await fileIo.fsync(output.fd);
        } finally {
          await fileIo.close(output.fd);
        }
      } finally {
        await fileIo.close(input.fd);
      }

      const staged: fileIo.File = await fileIo.open(temporary, fileIo.OpenMode.READ_ONLY);
      let extension: string = '';
      try {
        const imageSource: image.ImageSource = image.createImageSource(staged.fd);
        try {
          const info: image.ImageInfo = await imageSource.getImageInfo();
          extension = extensionFor(info.mimeType);
          const width: number = info.size.width;
          const height: number = info.size.height;
          if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0 ||
            width > MAX_DIMENSION || height > MAX_DIMENSION || width * height > MAX_PIXELS) {
            throw new Error('图片分辨率过大，请选择普通分辨率的图片。');
          }
          const scale: number = Math.min(1, THUMBNAIL_EDGE / Math.max(width, height));
          const pixelMap: image.PixelMap = await imageSource.createPixelMap({
            desiredSize: { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) },
            editable: false
          });
          try {
            const packer: image.ImagePacker = image.createImagePacker();
            try {
              const thumbnail: ArrayBuffer = await packer.packing(pixelMap, { format: 'image/jpeg', quality: 82 });
              const output: fileIo.File = await fileIo.open(thumbnailTemporary,
                fileIo.OpenMode.WRITE_ONLY | fileIo.OpenMode.CREATE | fileIo.OpenMode.TRUNC);
              try {
                await this.writeBytes(output.fd, thumbnail);
                await fileIo.fsync(output.fd);
              } finally {
                await fileIo.close(output.fd);
              }
            } finally {
              await packer.release();
            }
          } finally {
            await pixelMap.release();
          }
        } finally {
          await imageSource.release();
        }
      } finally {
        await fileIo.close(staged.fd);
      }

      const fileName: string = `${id}.${extension}`;
      originalPath = `${this.directory}/${fileName}`;
      thumbnailPath = `${originalPath}.thumb.jpg`;
      await fileIo.rename(temporary, originalPath);
      await fileIo.rename(thumbnailTemporary, thumbnailPath);
      const photo: RecordPhoto = {
        id: id,
        fileName: fileName,
        source: source,
        view: view,
        originalName: sourceName(uri, source === 'camera' ? '拍摄照片' : '所选图片'),
        createdAt: createdAt
      };
      accepted = true;
      return photo;
    } finally {
      const cleanup: string[] = [temporary, thumbnailTemporary];
      if (!accepted) {
        if (originalPath.length > 0) {
          cleanup.push(originalPath);
        }
        if (thumbnailPath.length > 0) {
          cleanup.push(thumbnailPath);
        }
      }
      for (let index: number = 0; index < cleanup.length; index++) {
        if (fileIo.accessSync(cleanup[index])) {
          await fileIo.unlink(cleanup[index]);
        }
      }
    }
  }

  async removePhotos(photos: RecordPhoto[]): Promise<void> {
    const paths: string[] = [];
    for (let index: number = 0; index < photos.length; index++) {
      const path: string = this.photoPath(photos[index]);
      paths.push(path, `${path}.thumb.jpg`);
    }
    let failed: boolean = false;
    for (let index: number = 0; index < paths.length; index++) {
      try {
        if (fileIo.accessSync(paths[index])) {
          await fileIo.unlink(paths[index]);
        }
      } catch (_) {
        failed = true;
      }
    }
    if (failed) {
      throw new Error('部分本地图片尚未清理，请重启应用后重试。');
    }
  }

  /** Call only after the record index was successfully read or committed. */
  async reconcile(photosToKeep: RecordPhoto[]): Promise<void> {
    this.requireReady();
    const keep: Set<string> = new Set<string>();
    for (let index: number = 0; index < photosToKeep.length; index++) {
      this.photoPath(photosToKeep[index]);
      keep.add(photosToKeep[index].fileName);
      keep.add(`${photosToKeep[index].fileName}.thumb.jpg`);
    }
    const names: string[] = await fileIo.listFile(this.directory, { recursion: false });
    let failed: boolean = false;
    for (let index: number = 0; index < names.length; index++) {
      const name: string = names[index];
      if (keep.has(name) || (!MANAGED_FILE.test(name) && !TEMP_FILE.test(name))) {
        continue;
      }
      try {
        await fileIo.unlink(`${this.directory}/${name}`);
      } catch (_) {
        failed = true;
      }
    }
    if (failed) {
      throw new Error('部分未使用图片尚未清理，请稍后重试。');
    }
  }
}
