import { common } from '@kit.AbilityKit';
import { KeyboardAvoidMode } from '@kit.ArkUI';
import { isValidJournalDay, JournalEntry, JournalStore } from '../model/JournalStore';
import { PhotoService, RecordPhoto } from '../service/PhotoService';

const BACKGROUND: string = '#F8F7FC';
const PANEL: string = '#FFFFFF';
const TEXT: string = '#262338';
const MUTED: string = '#6B667E';
const ACCENT: string = '#5E6AD2';
const BLUE: string = '#6F57C8';
const SOFT: string = '#F0ECFC';
const LINE: string = '#E6E1F2';
const DANGER: string = '#AE5162';
const CATEGORIES: string[] = ['图片记录', '步行', '日常活动', '休息'];
const PHOTO_VIEWS: string[] = ['正面', '侧面', '背面', '斜侧面'];

interface PrimaryButtonProps {
  title: string;
  icon: Resource;
  active: boolean;
  action: () => void;
}

interface StatProps {
  value: string;
  label: string;
}

interface PhotoProps {
  photo: RecordPhoto;
}

interface PhotosProps {
  photos: RecordPhoto[];
}

interface EntryProps {
  entry: JournalEntry;
}

interface SettingsRowProps {
  icon: Resource;
  title: string;
  subtitle: string;
  active: boolean;
  action: () => void;
}

interface DayTotal {
  day: string;
  label: string;
  minutes: number;
}

function dateText(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

@Entry
@Component
struct Index {
  @State private entries: JournalEntry[] = [];
  @State private tab: number = 0;
  @State private ready: boolean = false;
  @State private busy: boolean = false;
  @State private notice: string = '';
  @State private editorOpen: boolean = false;
  @State private editingId: string = '';
  @State private day: string = dateText(new Date());
  @State private category: string = CATEGORIES[0];
  @State private minutes: string = '';
  @State private note: string = '';
  @State private detail: number = 0;
  @State private recordLimit: number = 50;
  @State private storageUncertain: boolean = false;
  @State private draftPhotos: RecordPhoto[] = [];
  @State private guidanceOpen: boolean = false;
  @State private selectedView: string = '正面';
  @State private previewPhoto: RecordPhoto | undefined = undefined;
  @State private recordFilter: number = 0;
  private contentScroller: Scroller = new Scroller();
  private pendingPhotos: RecordPhoto[] = [];
  private photoService: PhotoService = new PhotoService();
  private store: JournalStore = new JournalStore();

  aboutToAppear(): void {
    this.getUIContext().setKeyboardAvoidMode(KeyboardAvoidMode.RESIZE);
    this.load();
  }

  onBackPress(): boolean {
    if (this.busy) {
      return true;
    }
    if (this.previewPhoto !== undefined) {
      this.previewPhoto = undefined;
      return true;
    }
    if (this.guidanceOpen) {
      this.guidanceOpen = false;
      return true;
    }
    if (this.editorOpen) {
      this.askDiscard();
      return true;
    }
    if (this.detail > 0) {
      this.detail = 0;
      return true;
    }
    if (this.tab !== 0) {
      this.tab = 0;
      return true;
    }
    return false;
  }

  private context(): common.UIAbilityContext {
    return this.getUIContext().getHostContext() as common.UIAbilityContext;
  }

  private async load(): Promise<void> {
    try {
      this.entries = await this.store.initialize(this.context());
      await this.photoService.initialize(this.context());
      this.ready = true;
      this.storageUncertain = false;
      this.notice = '';
      await this.cleanPhotoFiles();
    } catch {
      this.ready = false;
      this.notice = '暂时无法打开本地记录，请重启后重试。如仍失败，请联系支持并保留应用数据。';
    }
  }

  private ordered(): JournalEntry[] {
    return this.entries.slice().sort((a: JournalEntry, b: JournalEntry): number => {
      return a.day === b.day ? b.updatedAt - a.updatedAt : b.day.localeCompare(a.day);
    });
  }

  private todayMinutes(): number {
    const today: string = dateText(new Date());
    return this.entries.filter((entry: JournalEntry): boolean => entry.day === today)
      .reduce((sum: number, entry: JournalEntry): number => sum + entry.minutes, 0);
  }

  private week(): DayTotal[] {
    const days: DayTotal[] = [];
    for (let offset: number = 6; offset >= 0; offset--) {
      const date: Date = new Date();
      date.setDate(date.getDate() - offset);
      const key: string = dateText(date);
      const total: number = this.entries.filter((entry: JournalEntry): boolean => entry.day === key)
        .reduce((sum: number, entry: JournalEntry): number => sum + entry.minutes, 0);
      days.push({ day: key, label: `${date.getMonth() + 1}/${date.getDate()}`, minutes: total });
    }
    return days;
  }

  private weekMinutes(): number {
    return this.week().reduce((sum: number, item: DayTotal): number => sum + item.minutes, 0);
  }

  private barHeight(value: number): number {
    const max: number = Math.max(1, ...this.week().map((item: DayTotal): number => item.minutes));
    return value === 0 ? 4 : Math.max(12, value / max * 100);
  }

  private startEntry(entry?: JournalEntry): void {
    this.scrollTop();
    this.editingId = entry ? entry.id : '';
    this.day = entry ? entry.day : dateText(new Date());
    this.category = entry ? entry.category : '日常活动';
    this.minutes = entry ? String(entry.minutes) : '';
    this.note = entry ? entry.note : '';
    this.draftPhotos = entry && entry.photos ? entry.photos.slice() : [];
    this.pendingPhotos = [];
    this.guidanceOpen = false;
    this.previewPhoto = undefined;
    this.notice = '';
    this.editorOpen = true;
  }

  private startPhotoRecord(): void {
    this.startEntry();
    this.category = '图片记录';
    this.minutes = '0';
  }

  private allPhotos(): RecordPhoto[] {
    const photos: RecordPhoto[] = [];
    this.entries.forEach((entry: JournalEntry): void => {
      if (entry.photos) {
        entry.photos.forEach((photo: RecordPhoto): void => { photos.push(photo); });
      }
    });
    return photos;
  }

  private photoCount(): number {
    return this.allPhotos().length;
  }

  private async cleanPhotoFiles(): Promise<void> {
    const wasBusy: boolean = this.busy;
    this.busy = true;
    try {
      await this.photoService.reconcile(this.allPhotos());
    } catch {
      this.notice = '记录可以继续查看，部分未使用图片暂未完成清理。';
    } finally {
      this.busy = wasBusy;
    }
  }

  private nextPhotoView(): string {
    return PHOTO_VIEWS.find((view: string): boolean =>
      !this.draftPhotos.some((photo: RecordPhoto): boolean => photo.view === view)) || PHOTO_VIEWS[0];
  }

  private openGuide(): void {
    if (!this.ready || this.busy) {
      return;
    }
    if (!this.editorOpen) {
      this.startPhotoRecord();
    }
    if (this.draftPhotos.length >= 4) {
      this.notice = '每条记录最多添加 4 张图片，可先移除一张再拍摄。';
      return;
    }
    this.scrollTop();
    this.selectedView = this.nextPhotoView();
    this.guidanceOpen = true;
    this.notice = '';
  }

  private async takePhoto(): Promise<void> {
    if (!this.ready || this.busy || this.draftPhotos.length >= 4) {
      return;
    }
    this.busy = true;
    try {
      const photo: RecordPhoto | undefined = await this.photoService.takePhoto(this.context(), this.selectedView);
      if (photo) {
        this.draftPhotos = [...this.draftPhotos, photo];
        this.pendingPhotos.push(photo);
        this.notice = '照片已加入草稿。可以继续拍摄，或返回记录保存。';
        this.selectedView = this.nextPhotoView();
      } else {
        this.notice = '已取消拍摄，已有图片仍保留在草稿中。';
      }
    } catch {
      this.notice = '拍摄未完成，可重试或改为从本机选择图片。';
    } finally {
      this.busy = false;
    }
  }

  private async choosePhotos(): Promise<void> {
    if (!this.ready || this.busy) {
      return;
    }
    if (!this.editorOpen) {
      this.startPhotoRecord();
    }
    const remaining: number = 4 - this.draftPhotos.length;
    if (remaining <= 0) {
      this.notice = '每条记录最多添加 4 张图片，可先移除一张再选择。';
      return;
    }
    this.busy = true;
    try {
      const photos: RecordPhoto[] = await this.photoService.choosePhotos(this.context(), '其他', remaining);
      this.draftPhotos = [...this.draftPhotos, ...photos];
      photos.forEach((photo: RecordPhoto): void => { this.pendingPhotos.push(photo); });
      this.notice = photos.length > 0 ? `已添加 ${photos.length} 张本机图片，请保存记录。` : '已取消选择图片。';
    } catch {
      this.notice = '图片未能添加，请选择有效图片重试（每张不超过 20 MB）。';
    } finally {
      this.busy = false;
    }
  }

  private changePhotoView(photoId: string): void {
    const views: string[] = [...PHOTO_VIEWS, '其他'];
    this.draftPhotos = this.draftPhotos.map((photo: RecordPhoto): RecordPhoto => {
      if (photo.id !== photoId) {
        return photo;
      }
      return {
        id: photo.id, fileName: photo.fileName, source: photo.source,
        view: views[(views.indexOf(photo.view) + 1) % views.length],
        originalName: photo.originalName, createdAt: photo.createdAt
      };
    });
  }

  private askDiscard(): void {
    AlertDialog.show({
      title: '放弃本次修改？', message: '未保存的文字和新添加图片将从草稿中移除，已保存的记录不受影响。',
      primaryButton: { value: '继续填写', action: (): void => {} },
      secondaryButton: { value: '放弃', action: (): void => { this.discardEditor(); } }
    });
  }

  private async discardEditor(): Promise<void> {
    if (this.busy) {
      return;
    }
    if (this.storageUncertain) {
      // A preferences commit may already reference these files. Only a fresh
      // launch can determine durable references before any file is removed.
      this.pendingPhotos = [];
      this.draftPhotos = [];
      this.guidanceOpen = false;
      this.editorOpen = false;
      this.notice = '请先重启应用核对本地记录，当前图片文件已保留。';
      return;
    }
    this.busy = true;
    this.notice = '';
    try {
      await this.photoService.removePhotos(this.pendingPhotos);
    } catch {
      this.notice = '草稿已关闭，部分临时图片将在下次打开时清理。';
    } finally {
      this.pendingPhotos = [];
      this.draftPhotos = [];
      this.guidanceOpen = false;
      this.editorOpen = false;
      this.busy = false;
    }
  }

  private async persist(next: JournalEntry[], message: string): Promise<boolean> {
    if (!this.ready || this.busy) {
      return false;
    }
    this.busy = true;
    try {
      await this.store.saveEntries(next);
      this.entries = next;
      this.notice = message;
      return true;
    } catch {
      this.storageUncertain = true;
      this.ready = false;
      this.notice = '本次保存结果未确认。请保留当前输入并重启后核对记录，暂勿清除应用数据。';
      return false;
    } finally {
      this.busy = false;
    }
  }

  private async saveEntry(): Promise<void> {
    const numericMinutes: number = this.category === '图片记录' ? 0 : Number(this.minutes);
    if (!isValidJournalDay(this.day)) {
      this.notice = '请填写有效日期，例如 2026-10-24。';
      return;
    }
    if (this.category !== '图片记录' && (!/^\d+$/.test(this.minutes) || !Number.isInteger(numericMinutes) || numericMinutes > 1440)) {
      this.notice = '时长应为 0 到 1440 之间的整数分钟。';
      return;
    }
    if (this.category === '图片记录' && this.draftPhotos.length === 0) {
      this.notice = '请先拍摄或从本机选择至少一张图片。';
      return;
    }
    if (this.entries.length >= 2000 && this.editingId.length === 0) {
      this.notice = '已达到 2000 条记录，请先导出备份并整理记录。';
      return;
    }
    const now: number = Date.now();
    const previous: JournalEntry | undefined = this.entries.find((entry: JournalEntry): boolean => entry.id === this.editingId);
    const nextEntry: JournalEntry = {
      id: previous ? previous.id : `${now}-${Math.floor(Math.random() * 1000000)}`,
      day: this.day,
      category: this.category,
      minutes: numericMinutes,
      note: this.note.trim(),
      createdAt: previous ? previous.createdAt : now,
      updatedAt: now,
      photos: this.draftPhotos.slice()
    };
    const next: JournalEntry[] = this.entries.filter((entry: JournalEntry): boolean => entry.id !== nextEntry.id);
    next.push(nextEntry);
    if (await this.persist(next, previous ? '记录已更新并保存到本机。' : '新记录已保存到本机。')) {
      this.scrollTop();
      this.editorOpen = false;
      this.recordFilter = 0;
      this.pendingPhotos = [];
      this.guidanceOpen = false;
      this.tab = 1;
      await this.cleanPhotoFiles();
    }
  }

  private askDelete(entry: JournalEntry): void {
    AlertDialog.show({
      title: '删除这条记录？',
      message: `${entry.day} · ${entry.category} · ${entry.minutes} 分钟。删除后无法在应用内恢复。`,
      primaryButton: { value: '保留', action: (): void => {} },
      secondaryButton: { value: '删除', fontColor: DANGER, action: (): void => {
        this.deleteRecord(entry.id);
      } }
    });
  }

  private async deleteRecord(id: string): Promise<void> {
    if (await this.persist(this.entries.filter((item: JournalEntry): boolean => item.id !== id), '记录及其本机图片副本已删除。')) {
      await this.cleanPhotoFiles();
    }
  }

  private async replaceRecords(entries: JournalEntry[]): Promise<void> {
    if (await this.persist(entries, '记录表已导入。缺少本机图片文件时，请重新选择图片。')) {
      await this.cleanPhotoFiles();
    }
  }

  private async clearRecords(): Promise<void> {
    if (await this.persist([], '所有本地记录及本机图片副本已清空。')) {
      await this.cleanPhotoFiles();
    }
  }

  private async exportData(): Promise<void> {
    if (this.busy || !this.ready) {
      return;
    }
    this.busy = true;
    try {
      const saved: boolean = await this.store.exportFile(this.context(), this.entries);
      this.notice = saved ? '记录表已导出，包含图片文件信息，不包含图片文件本身。' : '已取消导出。';
    } catch {
      this.notice = '导出未完成，请检查所选位置后重试。';
    } finally {
      this.busy = false;
    }
  }

  private async importData(): Promise<void> {
    if (this.busy || !this.ready) {
      return;
    }
    this.busy = true;
    try {
      const imported: JournalEntry[] | undefined = await this.store.importFile(this.context());
      if (imported === undefined) {
        this.notice = '已取消导入。';
        return;
      }
      this.busy = false;
      AlertDialog.show({
        title: '用备份替换本机记录？',
        message: `文件有 ${imported.length} 条记录，将替换当前 ${this.entries.length} 条及图片关联。JSON 不包含图片文件；缺失图片需要重新选择。不再使用的本机图片副本会被清理。`,
        primaryButton: { value: '取消', action: (): void => {} },
        secondaryButton: { value: '确认替换', action: (): void => {
          this.replaceRecords(imported);
        } }
      });
    } catch {
      this.notice = '无法导入：请选择本应用导出的有效 JSON 备份（不超过 2 MB）。当前记录未更改。';
    } finally {
      this.busy = false;
    }
  }

  private askClear(): void {
    AlertDialog.show({
      title: '清空所有本地记录？',
      message: `共 ${this.entries.length} 条。记录和保存到本应用的图片副本将一并清空；系统相册原图不受影响。JSON 记录表不含图片，无法恢复被清空的图片副本。`,
      primaryButton: { value: '取消', action: (): void => {} },
      secondaryButton: { value: '清空记录', fontColor: DANGER, action: (): void => {
        this.clearRecords();
      } }
    });
  }

  private scrollTop(): void {
    this.contentScroller.scrollTo({ xOffset: 0, yOffset: 0 });
  }

  private switchTab(index: number): void {
    this.scrollTop();
    this.tab = index;
    this.notice = '';
  }

  private goBack(): void {
    if (!this.busy) {
      this.scrollTop();
      this.onBackPress();
    }
  }

  private openDetail(index: number): void {
    this.scrollTop();
    this.detail = index;
    this.notice = '';
  }

  private filteredRecords(): JournalEntry[] {
    return this.ordered().filter((entry: JournalEntry): boolean => {
      if (this.recordFilter === 1) {
        return entry.photos !== undefined && entry.photos.length > 0;
      }
      return this.recordFilter !== 2 || entry.category !== '图片记录';
    });
  }

  private readableDate(day: string): string {
    const date: Date = new Date(`${day}T12:00:00`);
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }

  private todayLabel(): string {
    const date: Date = new Date();
    const weekdays: string[] = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    return `${date.getMonth() + 1}月${date.getDate()}日 · ${weekdays[date.getDay()]}`;
  }

  private subpageTitle(): string {
    if (this.previewPhoto !== undefined) { return '图片详情'; }
    if (this.guidanceOpen) { return '引导拍摄'; }
    if (this.editorOpen) { return this.editingId.length > 0 ? '编辑记录' : '新建记录'; }
    return this.detail === 1 ? '隐私说明' : '关于脊见智诊';
  }

  private compactNumber(value: number): string {
    return value >= 10000 ? `${(value / 10000).toFixed(1)}万` : String(value);
  }

  private guideImage(): Resource {
    if (this.selectedView === '侧面') { return $r('app.media.guide_side'); }
    if (this.selectedView === '背面') { return $r('app.media.guide_back'); }
    if (this.selectedView === '斜侧面') { return $r('app.media.guide_oblique'); }
    return $r('app.media.guide_front');
  }

  private guideText(): string {
    if (this.selectedView === '侧面') { return '让身体一侧朝向镜头，保持自然站姿。'; }
    if (this.selectedView === '背面') { return '背对镜头，可请他人协助或使用系统相机倒计时。'; }
    if (this.selectedView === '斜侧面') { return '身体自然转向斜侧，确认没有明显遮挡。'; }
    return '面向镜头，自然站立，让需要记录的部分完整入镜。';
  }

  @Builder
  private sectionHeading(title: string, subtitle: string) {
    Column({ space: 6 }) {
      Text(title).fontSize(25).fontWeight(FontWeight.Bold).fontColor(TEXT).lineHeight(34)
      Text(subtitle).fontSize(13).fontColor(MUTED).lineHeight(21)
    }.alignItems(HorizontalAlign.Start).width('100%')
  }

  @Builder
  private primaryButton($$: PrimaryButtonProps) {
    Button({ type: ButtonType.Normal }) {
      Row({ space: 10 }) {
        Image($$.icon).width(20).height(20).objectFit(ImageFit.Contain)
        Text($$.title).fontSize(16).fontWeight(FontWeight.Medium).fontColor('#FFFFFF')
      }.justifyContent(FlexAlign.Center).width('100%')
    }.height(52).width('100%').borderRadius(26).backgroundColor(ACCENT)
      .linearGradient({ angle: 135, colors: [['#5E6AD2', 0], ['#7960C8', 1]] })
      .enabled($$.active).onClick($$.action)
  }

  @Builder
  private iconButton(icon: Resource, label: string, action: () => void) {
    Button({ type: ButtonType.Normal }) {
      Image(icon).width(20).height(20).objectFit(ImageFit.Contain)
    }.width(44).height(44).borderRadius(14).backgroundColor(SOFT)
      .enabled(!this.busy).accessibilityText(label).onClick(action)
  }

  @Builder
  private stat($$: StatProps) {
    Column({ space: 5 }) {
      Text($$.value).fontSize(26).fontWeight(FontWeight.Medium).fontColor(TEXT)
        .maxLines(1).minFontSize(18).maxFontSize(26)
      Text($$.label).fontSize(11).fontColor(MUTED)
    }.alignItems(HorizontalAlign.Start).layoutWeight(1)
  }

  @Builder
  private titleRow(title: string, more: string, action: () => void) {
    Row() {
      Text(title).fontSize(19).fontWeight(FontWeight.Bold).fontColor(TEXT).layoutWeight(1)
      Button({ type: ButtonType.Normal }) {
        Row({ space: 4 }) {
          Text(more).fontSize(12).fontColor(BLUE)
          Image($r('app.media.ic_arrow_right')).width(14).height(14)
        }
      }.height(44).padding({ left: 10, right: 0 }).backgroundColor(Color.Transparent).onClick(action)
    }.width('100%')
  }

  @Builder
  private photoTile($$: PhotoProps) {
    Column({ space: 7 }) {
      Stack({ alignContent: Alignment.BottomStart }) {
        if (this.photoService.exists($$.photo)) {
          Image(this.photoService.thumbnailUri($$.photo)).width('100%').height(124).objectFit(ImageFit.Cover)
        } else {
          Column({ space: 8 }) {
            Image($r('app.media.ic_image')).width(25).height(25).opacity(0.5)
            Text('图片不在本机').fontSize(11).fontColor(MUTED)
          }.justifyContent(FlexAlign.Center).width('100%').height(124).backgroundColor(SOFT)
        }
        Text($$.photo.view).fontSize(10).fontColor(TEXT).padding({ left: 9, right: 9, top: 5, bottom: 5 })
          .backgroundColor('#F2FFFFFF').borderRadius(8).margin(8)
      }.width('100%').height(124).borderRadius(14).clip(true)
      Text($$.photo.source === 'camera' ? '手机拍摄' : '本机选择').fontSize(10).fontColor(MUTED)
    }.width('100%').accessibilityText(`${$$.photo.view}图片，查看图片和文件信息`)
      .onClick((): void => { this.scrollTop(); this.previewPhoto = $$.photo; })
  }

  @Builder
  private attachmentGrid($$: PhotosProps) {
    Grid() {
      ForEach($$.photos, (photo: RecordPhoto) => {
        GridItem() { this.photoTile({ photo: photo }) }
      }, (photo: RecordPhoto): string => photo.id)
    }.columnsTemplate('1fr 1fr').columnsGap(10).rowsGap(12)
      .height($$.photos.length > 2 ? 304 : 146).width('100%')
  }

  @Builder
  private draftPhotoCard($$: PhotoProps) {
    Row({ space: 12 }) {
      Stack({ alignContent: Alignment.Center }) {
        if (this.photoService.exists($$.photo)) {
          Image(this.photoService.thumbnailUri($$.photo)).width(76).height(92).objectFit(ImageFit.Cover)
        } else {
          Image($r('app.media.ic_image')).width(26).height(26).opacity(0.5)
        }
      }.width(76).height(92).backgroundColor(SOFT).borderRadius(12).clip(true)
        .accessibilityText('查看图片及本地路径')
        .onClick((): void => { this.scrollTop(); this.previewPhoto = $$.photo; })
      Column({ space: 5 }) {
        Text($$.photo.originalName || $$.photo.fileName).fontSize(13).fontColor(TEXT).fontWeight(FontWeight.Medium)
          .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
        Text($$.photo.source === 'camera' ? '手机拍摄 · 点击缩略图查看详情' : '本机选择 · 点击缩略图查看详情')
          .fontSize(10).fontColor(MUTED).maxLines(2).lineHeight(16)
        Row() {
          Button({ type: ButtonType.Normal }) {
            Row({ space: 6 }) {
              Text($$.photo.view).fontSize(12).fontColor(BLUE)
              Image($r('app.media.ic_edit')).width(12).height(12)
            }
          }.height(44).padding({ left: 10, right: 10 }).backgroundColor(SOFT).borderRadius(10)
            .enabled(this.ready && !this.busy).accessibilityText(`切换图片方向，当前${$$.photo.view}`)
            .onClick((): void => this.changePhotoView($$.photo.id))
          Blank()
          Button({ type: ButtonType.Normal }) {
            Image($r('app.media.ic_close')).width(17).height(17)
          }.height(44).width(44).backgroundColor(Color.Transparent).borderRadius(12)
            .enabled(this.ready && !this.busy).accessibilityText('移除这张图片')
            .onClick((): void => {
              this.draftPhotos = this.draftPhotos.filter((item: RecordPhoto): boolean => item.id !== $$.photo.id);
            })
        }.width('100%')
      }.layoutWeight(1).alignItems(HorizontalAlign.Start)
    }.padding(12).width('100%').border({ width: 1, color: LINE }).borderRadius(17).backgroundColor(PANEL)
  }

  @Builder
  private guidancePage() {
    Column({ space: 22 }) {
      Column({ space: 7 }) {
        Text('CAPTURE GUIDE').fontSize(10).fontColor(BLUE).fontWeight(FontWeight.Medium).letterSpacing(2)
        Text('选好角度，自然入镜。').fontSize(25).fontWeight(FontWeight.Bold).fontColor(TEXT)
        Text(`按需选择拍摄方向 · 已添加 ${this.draftPhotos.length} / 4 张`).fontSize(13).fontColor(MUTED).lineHeight(21)
      }.width('100%').alignItems(HorizontalAlign.Start)
      Row({ space: 4 }) {
        ForEach(PHOTO_VIEWS, (view: string) => {
          Button({ type: ButtonType.Normal }) {
            Text(view).fontSize(13).fontWeight(this.selectedView === view ? FontWeight.Medium : FontWeight.Normal)
              .fontColor(this.selectedView === view ? '#FFFFFF' : MUTED)
          }.layoutWeight(1).height(44).padding(0).borderRadius(12)
            .backgroundColor(this.selectedView === view ? ACCENT : Color.Transparent)
            .enabled(!this.busy).accessibilityText(`${view}拍摄指引`)
            .onClick((): void => { this.selectedView = view; })
        }, (view: string): string => view)
      }.width('100%').padding(5).backgroundColor('#EEEBF8').borderRadius(17)
      Column({ space: 12 }) {
        Row() {
          Text(`${this.selectedView}视角`).fontSize(13).fontColor(BLUE).fontWeight(FontWeight.Medium)
          Blank()
          Text('构图示意').fontSize(11).fontColor(MUTED)
        }.width('100%')
        Image(this.guideImage()).width('100%').height(246).objectFit(ImageFit.Contain)
        Text(this.guideText()).fontSize(14).fontColor(TEXT).lineHeight(24).textAlign(TextAlign.Center)
        Text('示意仅作拍前参考，取景画面由系统相机提供。')
          .fontSize(10).fontColor(MUTED).lineHeight(18).textAlign(TextAlign.Center)
      }.padding(20).width('100%').backgroundColor(PANEL).borderRadius(26).border({ width: 1, color: LINE })
      Row({ space: 10 }) {
        Image($r('app.media.ic_check')).width(17).height(17)
        Text('光线均匀').fontSize(12).fontColor(MUTED).layoutWeight(1)
        Text('手机平稳').fontSize(12).fontColor(MUTED).layoutWeight(1)
        Text('自然站立').fontSize(12).fontColor(MUTED)
      }.width('100%').padding({ left: 4, right: 4 })
      if (this.draftPhotos.length > 0) {
        this.titleRow('已添加的图片', '返回编辑', (): void => { this.scrollTop(); this.guidanceOpen = false; })
        this.attachmentGrid({ photos: this.draftPhotos })
      }
    }.width('100%').alignItems(HorizontalAlign.Start)
  }

  @Builder
  private photoPreviewPage($$: PhotoProps) {
    Column({ space: 20 }) {
      if (this.photoService.exists($$.photo)) {
        Image(this.photoService.imageUri($$.photo)).width('100%').height(360)
          .sourceSize({ width: 1200, height: 1600 }).objectFit(ImageFit.Contain)
          .backgroundColor('#F0EDF6').borderRadius(22)
      } else {
        Column({ space: 13 }) {
          Image($r('app.media.ic_image')).width(42).height(42).opacity(0.5)
          Text('图片文件不在本机').fontSize(17).fontColor(TEXT).fontWeight(FontWeight.Medium)
          Text('编辑这条记录后，可以重新选择图片。').fontSize(13).fontColor(MUTED).lineHeight(22)
        }.justifyContent(FlexAlign.Center).padding(24).width('100%').height(230).backgroundColor(PANEL).borderRadius(22)
      }
      Column({ space: 17 }) {
        Row() {
          Text('文件信息').fontSize(17).fontWeight(FontWeight.Bold).fontColor(TEXT).layoutWeight(1)
          Text($$.photo.view).fontSize(12).fontColor(BLUE).padding({ left: 10, right: 10, top: 6, bottom: 6 })
            .backgroundColor(SOFT).borderRadius(8)
        }.width('100%')
        Text($$.photo.originalName || $$.photo.fileName).fontSize(14).fontColor(TEXT).lineHeight(23).width('100%')
        Row() {
          Text('图片来源').fontSize(12).fontColor(MUTED).layoutWeight(1)
          Text($$.photo.source === 'camera' ? '手机拍摄' : '本机选择').fontSize(12).fontColor(TEXT)
        }.width('100%')
        Divider().color(LINE)
        Row() {
          Text('本地保存路径').fontSize(12).fontColor(MUTED).layoutWeight(1)
          Text('长按复制').fontSize(10).fontColor(BLUE)
        }.width('100%')
        Text(this.photoService.displayPath($$.photo)).fontSize(12).fontColor(BLUE).lineHeight(22)
          .copyOption(CopyOptions.LocalDevice).width('100%')
      }.padding(21).width('100%').backgroundColor(PANEL).borderRadius(22)
      Text('图片仅在本机保存，路径用于查看当前设备中的文件位置。')
        .fontSize(11).fontColor(MUTED).lineHeight(20).width('100%')
    }.width('100%').alignItems(HorizontalAlign.Start)
  }

  @Builder
  private recordCard($$: EntryProps) {
    Column({ space: 14 }) {
      Row({ space: 12 }) {
        Column({ space: 4 }) {
          Text(this.readableDate($$.entry.day)).fontSize(19).fontColor(TEXT).fontWeight(FontWeight.Bold)
          Text($$.entry.day).fontSize(10).fontColor(MUTED).letterSpacing(1)
        }.layoutWeight(1).alignItems(HorizontalAlign.Start)
        Text($$.entry.category).fontSize(11).fontColor(BLUE).padding({ left: 11, right: 11, top: 6, bottom: 6 })
          .backgroundColor(SOFT).borderRadius(9)
      }.width('100%')
      if ($$.entry.note.length > 0) {
        Text($$.entry.note).fontSize(14).fontColor('#5E5771').lineHeight(24).width('100%')
          .maxLines(4).textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      if ($$.entry.photos && $$.entry.photos.length > 0) { this.attachmentGrid({ photos: $$.entry.photos as RecordPhoto[] }) }
      Row({ space: 5 }) {
        if ($$.entry.category === '图片记录') {
          Image($r('app.media.ic_image')).width(14).height(14).opacity(0.6)
          Text(`${$$.entry.photos ? $$.entry.photos.length : 0} 张图片附件`).fontSize(11).fontColor(MUTED)
        } else {
          Text(String($$.entry.minutes)).fontSize(21).fontColor(BLUE).fontWeight(FontWeight.Medium)
          Text('分钟').fontSize(11).fontColor(MUTED).margin({ top: 5 })
        }
        Blank()
        Button({ type: ButtonType.Normal }) {
          Row({ space: 5 }) {
            Image($r('app.media.ic_edit')).width(14).height(14)
            Text('编辑').fontSize(12).fontColor(BLUE)
          }
        }.height(44).padding({ left: 10, right: 10 }).borderRadius(12).backgroundColor(Color.Transparent)
          .enabled(this.ready && !this.busy).onClick((): void => this.startEntry($$.entry))
        Button({ type: ButtonType.Normal }) {
          Image($r('app.media.ic_trash')).width(16).height(16).opacity(0.5)
        }.width(44).height(44).borderRadius(12).backgroundColor(Color.Transparent)
          .enabled(this.ready && !this.busy).accessibilityText('删除这条记录').onClick((): void => this.askDelete($$.entry))
      }.width('100%')
    }.padding(20).backgroundColor(PANEL).borderRadius(23).width('100%').border({ width: 1, color: LINE })
  }

  @Builder
  private emptyState() {
    Column({ space: 13 }) {
      Image($r('app.media.ic_journal')).width(30).height(30).opacity(0.6).margin({ bottom: 2 })
      Text('第一条记录，从今天开始').fontSize(17).fontWeight(FontWeight.Medium).fontColor(TEXT)
      Text('拍张照片，或记下一件日常小事。').fontSize(12).fontColor(MUTED).lineHeight(21)
      Button({ type: ButtonType.Normal }) {
        Row({ space: 6 }) {
          Text('新建记录').fontSize(13).fontColor(BLUE).fontWeight(FontWeight.Medium)
          Image($r('app.media.ic_arrow_right')).width(16).height(16)
        }
      }.height(44).backgroundColor(SOFT).padding({ left: 17, right: 17 }).borderRadius(12)
        .enabled(this.ready && !this.busy).onClick((): void => this.startEntry())
    }.padding({ left: 20, right: 20, top: 28, bottom: 24 }).width('100%')
      .backgroundColor(PANEL).borderRadius(23).border({ width: 1, color: LINE })
  }

  @Builder
  private quickAction(icon: Resource, title: string, subtitle: string, action: () => void) {
    Button({ type: ButtonType.Normal }) {
      Row({ space: 10 }) {
        Image(icon).width(22).height(22)
        Column({ space: 5 }) {
          Text(title).fontSize(13).fontColor(TEXT).fontWeight(FontWeight.Medium)
            .maxLines(1).minFontSize(11).maxFontSize(13)
          Text(subtitle).fontSize(10).fontColor(MUTED).maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
        }.alignItems(HorizontalAlign.Start).layoutWeight(1)
      }.width('100%')
    }.height(78).layoutWeight(1).padding({ left: 15, right: 10 }).backgroundColor(PANEL)
      .borderRadius(18).border({ width: 1, color: LINE }).enabled(this.ready && !this.busy).onClick(action)
  }

  @Builder
  private weekCard() {
    Column({ space: 15 }) {
      Row() {
        Column({ space: 4 }) {
          Text('日常节奏').fontSize(17).fontWeight(FontWeight.Bold).fontColor(TEXT)
          Text('过去七天 · 活动时长').fontSize(10).fontColor(MUTED)
        }.layoutWeight(1).alignItems(HorizontalAlign.Start)
        Text(this.compactNumber(this.weekMinutes())).fontSize(25).fontWeight(FontWeight.Medium).fontColor(ACCENT)
        Text('分钟').fontSize(10).fontColor(MUTED).margin({ left: 5, top: 10 })
      }.width('100%')
      Row({ space: 9 }) {
        ForEach(this.week(), (item: DayTotal) => {
          Column({ space: 8 }) {
            Column() {
              Blank()
              Column().height(item.minutes === 0 ? 4 : Math.max(9, this.barHeight(item.minutes) * 0.58))
                .width(16).borderRadius(5).backgroundColor(item.day === dateText(new Date()) ? ACCENT : '#C4BAE7')
            }.height(60).width('100%')
            Text(item.label).fontSize(9).fontColor(item.day === dateText(new Date()) ? ACCENT : MUTED)
          }.layoutWeight(1)
        }, (item: DayTotal): string => item.day)
      }.width('100%').alignItems(VerticalAlign.Bottom)
      if (this.weekMinutes() === 0) {
        Text('写下活动时长，让日常慢慢有迹可循。').fontSize(10).fontColor(MUTED).width('100%')
      }
    }.padding(20).width('100%').backgroundColor(PANEL).borderRadius(23).border({ width: 1, color: LINE })
  }

  @Builder
  private homePage() {
    Column({ space: 18 }) {
      Column({ space: 7 }) {
        Text(this.todayLabel()).fontSize(12).fontColor(MUTED).letterSpacing(1)
        Text('看见日常，留存细节。').fontSize(27).fontWeight(FontWeight.Bold).fontColor(TEXT).lineHeight(38)
          .maxLines(1).minFontSize(23).maxFontSize(27)
      }.width('100%').alignItems(HorizontalAlign.Start).padding({ top: 2, bottom: 3 })
      Button({ type: ButtonType.Normal }) {
        Row({ space: 3 }) {
          Column({ space: 9 }) {
            Row({ space: 7 }) {
              Column().width(5).height(5).borderRadius(3).backgroundColor('#A393FF')
              Text('留下一张此刻').fontSize(10).fontColor(BLUE).letterSpacing(1)
            }
            Text('引导拍摄').fontSize(25).fontColor(TEXT).fontWeight(FontWeight.Bold)
            Text('四种视角，轻松记录').fontSize(11).fontColor(MUTED)
            Row({ space: 7 }) {
              Image($r('app.media.ic_camera_white')).width(14).height(14)
              Text('开始拍摄').fontSize(12).fontColor('#FFFFFF').fontWeight(FontWeight.Medium)
            }.height(36).padding({ left: 15, right: 15 }).borderRadius(18)
              .linearGradient({ angle: 135, colors: [['#5E6AD2', 0], ['#7960C8', 1]] })
          }.alignItems(HorizontalAlign.Start).layoutWeight(1)
          Stack() {
            Image($r('app.media.hero_capture')).width(122).height(150).objectFit(ImageFit.Contain)
            Image($r('app.media.app_icon')).width(90).height(108).objectFit(ImageFit.Contain)
          }.width(122).height(150)
        }.width('100%')
      }.width('100%').height(174).padding({ left: 22, right: 12 }).borderRadius(25).backgroundColor(PANEL)
        .linearGradient({ angle: 120, colors: [['#F7F4FF', 0], ['#EAE5FC', 1]] })
        .border({ width: 1, color: '#E1D9FA' })
        .enabled(this.ready && !this.busy).accessibilityText('引导拍摄，查看四种方向的拍前提示')
        .onClick((): void => this.openGuide())
      Row({ space: 12 }) {
        this.quickAction($r('app.media.ic_image'), '从本机选图', '整理已有照片', (): void => { this.choosePhotos(); })
        this.quickAction($r('app.media.ic_edit'), '写一条记录', '记下今天的小事', (): void => this.startEntry())
      }.width('100%')
      Row({ space: 15 }) {
        this.stat({ value: String(this.entries.length), label: '全部记录' })
        Column().width(1).height(30).backgroundColor(LINE)
        this.stat({ value: String(this.photoCount()), label: '图片附件' })
        Column().width(1).height(30).backgroundColor(LINE)
        this.stat({ value: this.compactNumber(this.todayMinutes()), label: '今日分钟' })
      }.padding({ left: 21, right: 17, top: 18, bottom: 18 }).width('100%')
        .backgroundColor(PANEL).borderRadius(21).border({ width: 1, color: LINE })
      this.weekCard()
      Column({ space: 10 }) {
        this.titleRow('最近记录', '全部记录', (): void => this.switchTab(1))
        if (this.entries.length === 0) {
          this.emptyState()
        } else {
          ForEach(this.ordered().slice(0, 3), (entry: JournalEntry) => {
            this.recordCard({ entry: entry })
          }, (entry: JournalEntry): string => `${entry.id}-${entry.updatedAt}`)
        }
      }.width('100%')
      Row({ space: 6 }) {
        Image($r('app.media.ic_shield')).width(13).height(13).opacity(0.5)
        Text('无需账号 · 记录保存在本机').fontSize(10).fontColor(MUTED)
      }.justifyContent(FlexAlign.Center).width('100%').padding({ top: 2, bottom: 8 })
    }.width('100%').alignItems(HorizontalAlign.Start)
  }

  @Builder
  private recordsPage() {
    Column({ space: 20 }) {
      Row() {
        Column({ space: 6 }) {
          Text('我的记录').fontSize(29).fontWeight(FontWeight.Bold).fontColor(TEXT)
          Text(`${this.entries.length} 条记录，串起平凡日常。`).fontSize(12).fontColor(MUTED)
        }.layoutWeight(1).alignItems(HorizontalAlign.Start)
        this.iconButton($r('app.media.ic_plus'), '新建记录', (): void => this.startEntry())
      }.width('100%')
      Row({ space: 8 }) {
        ForEach(['全部', '有图', '活动'], (name: string, index: number) => {
          Button({ type: ButtonType.Normal }) {
            Text(name).fontSize(13).fontColor(this.recordFilter === index ? '#FFFFFF' : MUTED)
          }.height(42).padding({ left: 20, right: 20 }).borderRadius(13)
            .backgroundColor(this.recordFilter === index ? ACCENT : PANEL)
            .onClick((): void => { this.recordFilter = index; this.recordLimit = 50; })
        }, (name: string): string => name)
      }.width('100%')
      if (this.entries.length === 0) {
        this.emptyState()
      } else if (this.filteredRecords().length === 0) {
        Text('这里还没有相关记录，试试其他分类。').fontSize(13).fontColor(MUTED).lineHeight(23)
          .padding(25).width('100%').backgroundColor(PANEL).borderRadius(20)
      } else {
        ForEach(this.filteredRecords().slice(0, this.recordLimit), (entry: JournalEntry) => {
          this.recordCard({ entry: entry })
        }, (entry: JournalEntry): string => `${entry.id}-${entry.updatedAt}`)
        if (this.filteredRecords().length > this.recordLimit) {
          Button('查看更多记录').width('100%').height(46).backgroundColor(SOFT).fontColor(BLUE)
            .onClick((): void => { this.recordLimit += 50; })
        }
      }
    }.width('100%').alignItems(HorizontalAlign.Start)
  }

  @Builder
  private settingsRow($$: SettingsRowProps) {
    Button({ type: ButtonType.Normal }) {
      Row({ space: 13 }) {
        Image($$.icon).width(21).height(21)
        Column({ space: 5 }) {
          Text($$.title).fontSize(14).fontColor(TEXT).fontWeight(FontWeight.Medium)
          if ($$.subtitle.length > 0) { Text($$.subtitle).fontSize(11).fontColor(MUTED).lineHeight(18) }
        }.alignItems(HorizontalAlign.Start).layoutWeight(1)
        Image($r('app.media.ic_arrow_right')).width(17).height(17).opacity(0.5)
      }.width('100%')
    }.width('100%').constraintSize({ minHeight: 66 }).padding({ left: 20, right: 20, top: 14, bottom: 14 })
      .backgroundColor(PANEL).borderRadius(14).enabled($$.active).onClick($$.action)
  }

  @Builder
  private settingsPage() {
    Column({ space: 23 }) {
      this.sectionHeading('记录属于你', '整理、备份，也安心地留在自己手里。')
      Row({ space: 15 }) {
        Image($r('app.media.ic_shield')).width(34).height(34)
        Column({ space: 6 }) {
          Text('本机保存，自在记录').fontSize(16).fontColor(ACCENT).fontWeight(FontWeight.Medium)
          Text('无需注册 · 无广告 · 应用不联网').fontSize(11).fontColor('#756B91').lineHeight(20)
        }.alignItems(HorizontalAlign.Start).layoutWeight(1)
      }.padding(22).width('100%').backgroundColor('#F0EBFC').borderRadius(22)
      Column({ space: 10 }) {
        Text('数据管理').fontSize(12).fontColor(MUTED).width('100%').padding({ left: 4 })
        Column() {
          this.settingsRow({ icon: $r('app.media.ic_export'), title: '导出记录表', subtitle: '保存为 JSON 文件',
            active: this.ready && !this.busy, action: (): void => { this.exportData(); } })
          Divider().color(LINE).margin({ left: 20, right: 20 })
          this.settingsRow({ icon: $r('app.media.ic_import'), title: '导入记录表', subtitle: '从 JSON 文件替换当前记录',
            active: this.ready && !this.busy, action: (): void => { this.importData(); } })
        }.backgroundColor(PANEL).borderRadius(21).clip(true).width('100%').border({ width: 1, color: LINE })
        Text('记录表包含文字和图片文件信息，不包含图片文件。卸载或清除应用数据前，图片请另行保管。')
          .fontSize(11).fontColor(MUTED).lineHeight(20).width('100%').padding({ left: 4, right: 4 })
      }.width('100%')
      Column({ space: 10 }) {
        Text('应用与支持').fontSize(12).fontColor(MUTED).width('100%').padding({ left: 4 })
        Column() {
          this.settingsRow({ icon: $r('app.media.ic_shield'), title: '隐私说明', subtitle: '',
            active: !this.busy, action: (): void => this.openDetail(1) })
          Divider().color(LINE).margin({ left: 20, right: 20 })
          this.settingsRow({ icon: $r('app.media.ic_info'), title: '关于脊见智诊', subtitle: '',
            active: !this.busy, action: (): void => this.openDetail(2) })
        }.backgroundColor(PANEL).borderRadius(21).clip(true).width('100%').border({ width: 1, color: LINE })
      }.width('100%')
      Button({ type: ButtonType.Normal }) {
        Row({ space: 10 }) {
          Image($r('app.media.ic_trash')).width(18).height(18).opacity(0.6)
          Text('清空所有本地记录').fontSize(13).fontColor(DANGER).layoutWeight(1)
          Image($r('app.media.ic_arrow_right')).width(16).height(16).opacity(0.4)
        }.width('100%')
      }.width('100%').height(58).padding({ left: 20, right: 20 }).borderRadius(18)
        .backgroundColor('#FAEDF1').enabled(this.ready && !this.busy && this.entries.length > 0)
        .onClick((): void => this.askClear())
      Column({ space: 7 }) {
        Text('SpineInsight').fontSize(15).fontColor('#9A90B4').fontWeight(FontWeight.Medium).letterSpacing(1)
        Text('脊见智诊 · 0.2.0').fontSize(10).fontColor(MUTED)
      }.width('100%').padding({ top: 8, bottom: 12 })
    }.width('100%').alignItems(HorizontalAlign.Start)
  }

  @Builder
  private editorPage() {
    Column({ space: 22 }) {
      Column({ space: 6 }) {
        Text(this.editingId.length > 0 ? '更新这一刻的记录。' : '把此刻，留给以后的你。')
          .fontSize(23).fontWeight(FontWeight.Bold).fontColor(TEXT).lineHeight(33)
        Text('图片、文字与日期，收在同一条记录里。').fontSize(12).fontColor(MUTED).lineHeight(21)
      }.alignItems(HorizontalAlign.Start).width('100%')
      Column({ space: 16 }) {
        Row() {
          Text('记录信息').fontSize(15).fontColor(TEXT).fontWeight(FontWeight.Bold).layoutWeight(1)
          Text('01').fontSize(11).fontColor('#9387AE')
        }.width('100%')
        Row() {
          Text('日期').fontSize(13).fontColor(MUTED).width(55)
          TextInput({ text: this.day, placeholder: 'YYYY-MM-DD' }).maxLength(10).height(46).layoutWeight(1)
            .fontColor(TEXT).placeholderColor(MUTED).fontSize(15).backgroundColor(BACKGROUND).borderRadius(12)
            .enabled(this.ready && !this.busy).onChange((value: string): void => { this.day = value; })
        }.width('100%')
        Row({ space: 5 }) {
          ForEach(CATEGORIES, (name: string) => {
            Button({ type: ButtonType.Normal }) {
              Text(name === '图片记录' ? '图片' : name === '日常活动' ? '活动' : name)
                .fontSize(12).fontColor(this.category === name ? '#FFFFFF' : MUTED)
            }.layoutWeight(1).height(44).padding(0).borderRadius(12)
              .backgroundColor(this.category === name ? ACCENT : BACKGROUND).enabled(this.ready && !this.busy)
              .onClick((): void => { this.category = name; })
          }, (name: string): string => name)
        }.width('100%')
        if (this.category !== '图片记录') {
          Row() {
            Text('时长').fontSize(13).fontColor(MUTED).width(55)
            TextInput({ text: this.minutes, placeholder: '0' }).type(InputType.Number).maxLength(4).height(46)
              .layoutWeight(1).fontColor(TEXT).placeholderColor(MUTED).fontSize(15)
              .backgroundColor(BACKGROUND).borderRadius(12).enabled(this.ready && !this.busy)
              .onChange((value: string): void => { this.minutes = value; })
            Text('分钟').fontSize(12).fontColor(MUTED).margin({ left: 10 })
          }.width('100%')
        }
      }.padding(20).backgroundColor(PANEL).borderRadius(23).width('100%').border({ width: 1, color: LINE })
      Column({ space: 14 }) {
        Row() {
          Text('图片附件').fontSize(15).fontColor(TEXT).fontWeight(FontWeight.Bold).layoutWeight(1)
          Text(`${this.draftPhotos.length} / 4`).fontSize(12).fontColor(BLUE)
        }.width('100%')
        if (this.draftPhotos.length === 0) {
          Column({ space: 9 }) {
            Image($r('app.media.ic_image')).width(30).height(30).opacity(0.5)
            Text('为这条记录添一张照片').fontSize(13).fontColor(TEXT)
            Text('支持拍摄或选择本机图片').fontSize(10).fontColor(MUTED)
          }.width('100%').height(118).justifyContent(FlexAlign.Center).backgroundColor(BACKGROUND).borderRadius(17)
        }
        ForEach(this.draftPhotos, (photo: RecordPhoto) => {
          this.draftPhotoCard({ photo: photo })
        }, (photo: RecordPhoto): string => `${photo.id}-${photo.view}`)
        Row({ space: 10 }) {
          Button({ type: ButtonType.Normal }) {
            Row({ space: 7 }) {
              Image($r('app.media.ic_camera')).width(17).height(17)
              Text('引导拍摄').fontSize(12).fontColor(BLUE)
            }
          }.height(46).layoutWeight(1).backgroundColor(SOFT).borderRadius(13)
            .enabled(this.ready && !this.busy && this.draftPhotos.length < 4).onClick((): void => this.openGuide())
          Button({ type: ButtonType.Normal }) {
            Row({ space: 7 }) {
              Image($r('app.media.ic_image')).width(17).height(17)
              Text('本机选图').fontSize(12).fontColor(BLUE)
            }
          }.height(46).layoutWeight(1).backgroundColor(BACKGROUND).borderRadius(13)
            .enabled(this.ready && !this.busy && this.draftPhotos.length < 4).onClick((): void => { this.choosePhotos(); })
        }.width('100%')
      }.padding(20).backgroundColor(PANEL).borderRadius(23).width('100%').border({ width: 1, color: LINE })
      Column({ space: 13 }) {
        Row() {
          Text('写点什么').fontSize(15).fontColor(TEXT).fontWeight(FontWeight.Bold).layoutWeight(1)
          Text('可选').fontSize(11).fontColor(MUTED)
        }.width('100%')
        TextArea({ text: this.note, placeholder: '今天的活动、感受，或想记住的小事…' }).maxLength(300)
          .height(132).fontSize(14).fontColor(TEXT).placeholderColor(MUTED).backgroundColor(BACKGROUND)
          .borderRadius(14).padding(14).enabled(this.ready && !this.busy)
          .onChange((value: string): void => { this.note = value; })
        Text(`${this.note.length} / 300`).fontSize(10).fontColor(MUTED).width('100%').textAlign(TextAlign.End)
      }.padding(20).backgroundColor(PANEL).borderRadius(23).width('100%').border({ width: 1, color: LINE })
      Text('保存后仍可继续编辑。图片和记录仅保存在本机。')
        .fontSize(11).fontColor(MUTED).lineHeight(20).width('100%').textAlign(TextAlign.Center)
    }.alignItems(HorizontalAlign.Start).width('100%')
  }

  @Builder
  private policyParagraph(title: string, content: string) {
    Column({ space: 9 }) {
      Text(title).fontSize(16).fontColor(TEXT).fontWeight(FontWeight.Bold).width('100%')
      Text(content).fontSize(13).fontColor('#625B75').lineHeight(24).width('100%')
    }.width('100%').alignItems(HorizontalAlign.Start)
  }

  @Builder
  private detailPage() {
    Column({ space: 23 }) {
      if (this.detail === 1) {
        this.sectionHeading('你的记录，由你掌握。', '隐私说明 · 更新于 2026 年 10 月 24 日')
        this.policyParagraph('本机处理', '本应用保存你主动填写的日期、记录类型、时长、备注，以及主动拍摄或选择的图片和文件信息，用于整理个人记录。无需注册，不接入广告、统计分析或云端服务。')
        this.policyParagraph('拍摄与选图', '拍照会打开系统相机，选图通过系统图片选择器完成。应用只复制你主动选中的图片到本机私有目录，不扫描其他相册内容，也不向服务器发送记录或图片。原图片可能保留拍摄时间等附带信息，本版本不专门清除全部图片元数据。')
        this.policyParagraph('导出与删除', 'JSON 为明文记录表，包含图片来源和文件名，不包含图片文件本身。图片文件缺失时需要重新选择。你可以修改、删除或清空记录；卸载或清除应用数据会移除本机记录及图片副本。相册原图和已导出的文件不由应用清空操作删除。系统备份和文件共享受系统及你的设置控制。')
        this.policyParagraph('联系开发者', '开发者：刘思辰。你主动发送支持邮件时，开发者会收到你的邮箱地址和自愿提供的内容，仅在处理问题所需范围使用。普通功能不要求发送邮件。')
      } else {
        Image($r('app.media.brand_logo')).width(180).height(180).objectFit(ImageFit.Contain)
        this.sectionHeading('脊见智诊', 'SpineInsight · 0.2.0')
        this.policyParagraph('留存每个日常细节', '按正面、侧面、背面、斜侧面查看拍前提示，调用系统相机拍照，或从本机选择图片，和日期、备注一起保存。')
        this.policyParagraph('简单，也有条理', '查看图片缩略图、来源、方向标签和本地路径。记录日常活动，并用七天时长回顾感受自己的日常节奏。')
        this.policyParagraph('本机记录工具', '无需账号，无广告，不连接互联网。导出的 JSON 记录表不含图片文件，重要图片请另行保管。')
      }
      Column({ space: 8 }) {
        Text('联系与支持').fontSize(11).fontColor(MUTED).width('100%')
        Text('benjaminmcree@qq.com').fontSize(14).fontColor(BLUE).lineHeight(24)
          .copyOption(CopyOptions.LocalDevice).width('100%')
      }.padding(20).width('100%').backgroundColor(SOFT).borderRadius(18)
    }.alignItems(HorizontalAlign.Start).width('100%')
  }

  @Builder
  private topBar() {
    if (this.editorOpen || this.detail > 0 || this.previewPhoto !== undefined) {
      Row({ space: 13 }) {
        this.iconButton($r('app.media.ic_chevron_left'), '返回上一页', (): void => this.goBack())
        Text(this.subpageTitle()).fontSize(17).fontColor(TEXT).fontWeight(FontWeight.Medium).layoutWeight(1)
        Row({ space: 5 }) {
          Column().width(5).height(5).borderRadius(3).backgroundColor('#A393FF')
          Text(this.busy ? '处理中' : '仅本机').fontSize(10).fontColor(MUTED)
        }
      }.width('100%').padding({ left: 20, right: 23, top: 13, bottom: 13 })
    } else {
      Row({ space: 10 }) {
        Image($r('app.media.app_icon')).width(38).height(38).objectFit(ImageFit.Contain)
        Column({ space: 2 }) {
          Text('脊见智诊').fontSize(17).fontColor(TEXT).fontWeight(FontWeight.Bold)
          Text('SpineInsight').fontSize(10).fontColor(MUTED).letterSpacing(0.3)
        }.alignItems(HorizontalAlign.Start).layoutWeight(1)
        Row({ space: 5 }) {
          Image($r('app.media.ic_shield')).width(12).height(12)
          Text(this.busy ? '处理中' : '本机保存').fontSize(10).fontColor(BLUE)
        }.padding({ left: 10, right: 10, top: 7, bottom: 7 }).backgroundColor(SOFT).borderRadius(10)
      }.width('100%').padding({ left: 22, right: 22, top: 16, bottom: 17 })
    }
  }

  @Builder
  private navItem(icon: Resource, label: string, index: number) {
    Button({ type: ButtonType.Normal }) {
      Column({ space: 5 }) {
        Image(icon).width(21).height(21).opacity(this.tab === index ? 1 : 0.5)
        Text(label).fontSize(10).fontColor(this.tab === index ? ACCENT : MUTED)
          .fontWeight(this.tab === index ? FontWeight.Medium : FontWeight.Normal)
      }.justifyContent(FlexAlign.Center).width('100%')
    }.layoutWeight(1).height(56).borderRadius(16).backgroundColor(this.tab === index ? SOFT : Color.Transparent)
      .enabled(!this.busy).onClick((): void => this.switchTab(index))
  }

  @Builder
  private bottomBar() {
    if (this.previewPhoto !== undefined || this.detail > 0) {
      // Reading pages use the header back button.
    } else if (this.guidanceOpen) {
      Column({ space: 6 }) {
        this.primaryButton({ title: this.busy ? '正在处理…' : `打开相机 · 拍${this.selectedView}`,
          icon: $r('app.media.ic_camera_white'), active: this.ready && !this.busy && this.draftPhotos.length < 4,
          action: (): void => { this.takePhoto(); } })
        Button('完成拍摄，返回记录').fontSize(12).fontColor(BLUE).width('100%').height(40)
          .backgroundColor(Color.Transparent).enabled(!this.busy)
          .onClick((): void => { this.scrollTop(); this.guidanceOpen = false; })
      }.padding({ left: 22, right: 22, top: 12, bottom: 8 }).width('100%').backgroundColor(PANEL)
        .border({ width: { top: 1 }, color: LINE })
    } else if (this.editorOpen) {
      Row({ space: 11 }) {
        this.iconButton($r('app.media.ic_close'), '放弃修改', (): void => this.askDiscard())
        Column() {
          this.primaryButton({ title: this.busy ? '正在保存…' : '保存这条记录', icon: $r('app.media.ic_check_white'),
            active: this.ready && !this.busy, action: (): void => { this.saveEntry(); } })
        }.layoutWeight(1)
      }.padding({ left: 22, right: 22, top: 12, bottom: 12 }).width('100%').backgroundColor(PANEL)
        .border({ width: { top: 1 }, color: LINE })
    } else {
      Row({ space: 9 }) {
        this.navItem($r('app.media.ic_home'), '概览', 0)
        this.navItem($r('app.media.ic_journal'), '记录', 1)
        this.navItem($r('app.media.ic_settings'), '设置', 2)
        Button({ type: ButtonType.Normal }) {
          Image($r('app.media.ic_plus_white')).width(23).height(23)
        }.width(50).height(50).borderRadius(17).backgroundColor(ACCENT).margin({ left: 8 })
          .accessibilityText('新建记录').enabled(this.ready && !this.busy).onClick((): void => this.startEntry())
      }.width('100%').padding({ left: 18, right: 22, top: 10, bottom: 12 }).backgroundColor(PANEL)
        .border({ width: { top: 1 }, color: LINE })
    }
  }

  build() {
    Column() {
      this.topBar()
      Scroll(this.contentScroller) {
        Column({ space: 17 }) {
          if (this.notice.length > 0) {
            Row({ space: 9 }) {
              Image($r('app.media.ic_info')).width(17).height(17)
              Text(this.notice).fontSize(12).fontColor(BLUE).lineHeight(21).layoutWeight(1)
            }.padding(15).width('100%').backgroundColor('#F0EBFC').borderRadius(15)
          }
          if (!this.ready) {
            Button('重新读取本地记录').fontColor(BLUE).backgroundColor(SOFT).onClick((): void => { this.load(); })
          }
          if (this.previewPhoto !== undefined) {
            this.photoPreviewPage({ photo: this.previewPhoto as RecordPhoto })
          } else if (this.guidanceOpen) {
            this.guidancePage()
          } else if (this.editorOpen) {
            this.editorPage()
          } else if (this.detail > 0) {
            this.detailPage()
          } else if (this.tab === 0) {
            this.homePage()
          } else if (this.tab === 1) {
            this.recordsPage()
          } else {
            this.settingsPage()
          }
        }.padding({ left: 22, right: 22, top: 7, bottom: 26 }).width('100%').constraintSize({ maxWidth: 560 })
      }.layoutWeight(1).width('100%').align(Alignment.Top).scrollBar(BarState.Off)
      this.bottomBar()
    }.width('100%').height('100%').backgroundColor(BACKGROUND)
  }
}
