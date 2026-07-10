const PSEUDOCT_MODE_TRANSFER = "transfer";
const PSEUDOCT_MODE_SOURCE = "source_direct";

const app = {
    mode: "xray",
    currentImageId: null,
    staticDemo: window.location.hostname.endsWith(".github.io"),

    pseudoctItems: [],
    selectedPseudoCTIndex: -1,
    pseudoctInferenceMode: PSEUDOCT_MODE_TRANSFER,

    reportAudience: "doctor",
    reportAiEnabled: false,
    reportAiReady: false,
    reportAiModel: "--",

    xrayBatchJob: null,
    xrayBatchFiles: [],
    xrayBatchInputFolderName: "",
    rgbBatchJob: null,
    rgbBatchFiles: [],
    rgbBatchInputFolderName: "",
    rgbOutputDirectoryHandle: null,
    rgbOutputDirectoryName: "",
    selectedXrayBatchIndex: -1,
    selectedRgbBatchIndex: -1,
    batchPollers: {
        xray: null,
        rgb: null,
    },
    activeProgressKind: "",
    progressDismissed: false,
    mirrorX: 1,
    saveDirectoryHandle: null,
    saveDirectoryName: "",

    classActiveTab: "relative flex-1 py-1.5 px-3 rounded-lg text-[13px] font-medium text-white bg-white/[0.06] shadow-[0_0_15px_rgba(255,255,255,0.05),inset_0_1px_rgba(255,255,255,0.1)] border border-white/10 transition-all duration-300 ease-out btn-glow overflow-hidden z-10",
    classInactiveTab: "relative flex-1 py-1.5 px-3 rounded-lg text-[13px] font-medium text-[#8A8F98] hover:text-white hover:bg-white/[0.03] border border-transparent transition-all duration-300 ease-out btn-glow overflow-hidden z-0",
    classActiveMiniTab: "flex-1 py-2 rounded-lg text-[12px] font-medium text-white bg-white/[0.08] border border-white/10 shadow-[0_0_18px_rgba(94,106,210,0.18)]",
    classInactiveMiniTab: "flex-1 py-2 rounded-lg text-[12px] font-medium text-[#8A8F98] hover:text-white hover:bg-white/[0.03] border border-transparent",

    init() {
        this.configureRuntimeMode();
        this.bindUIActions();
        this.updateReportAudienceButtons();
        this.updatePseudoCTModeButtons();
        this.initPanZoom();
        this.initDragAndDrop();
        this.initWindowingSliders();
        this.fetchReportAIStatus();
        this.fetchPseudoCTStatus(false);
        this.renderXrayBatchList();
        this.renderRGBBatchList();
        this.renderBatchProgress(null, null);
    },

    el(id) {
        return document.getElementById(id);
    },

    configureRuntimeMode() {
        if (!this.staticDemo) {
            return;
        }
        const banner = this.el("static-demo-banner");
        if (banner) {
            banner.classList.remove("hidden");
        }
    },

    async fetchJson(url, options = {}) {
        if (this.staticDemo && String(url).startsWith("/api/")) {
            throw new Error("GitHub Pages 为静态展示版；完整 AI 功能请下载源码后在本机运行");
        }
        const controller = new AbortController();
        const timeoutMs = Number(options.timeoutMs || 300000);
        const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
        const requestOptions = {
            ...options,
            credentials: "same-origin",
            signal: options.signal || controller.signal,
        };
        delete requestOptions.timeoutMs;

        try {
            const response = await fetch(url, requestOptions);
            const contentType = response.headers.get("content-type") || "";
            if (!contentType.includes("application/json")) {
                throw new Error(`服务返回了无法识别的响应（HTTP ${response.status}）`);
            }
            const payload = await response.json();
            if (!response.ok && payload.code === undefined) {
                payload.code = response.status;
            }
            return payload;
        } catch (error) {
            if (error && error.name === "AbortError") {
                throw new Error("请求超时，请稍后重试");
            }
            throw error;
        } finally {
            window.clearTimeout(timeoutId);
        }
    },

    bindClick(id, handler) {
        const element = this.el(id);
        if (!element) {
            return;
        }
        element.addEventListener("click", handler);
    },

    bindUIActions() {
        this.bindClick("tab-xray", () => this.setMode("xray"));
        this.bindClick("tab-rgb", () => this.setMode("rgb"));
        this.bindClick("tab-pseudoct", () => this.setMode("pseudoct"));

        this.bindClick("btn-import-xray-single", () => this.importXray(false));
        this.bindClick("btn-import-xray-batch", () => this.importXray(true));
        this.bindClick("btn-run-xray", () => this.runXray());
        this.bindClick("btn-run-xray-batch-left", () => this.runSelectedXrayBatch());

        this.bindClick("btn-import-rgb-single", () => this.importRgb(false));
        this.bindClick("btn-import-rgb-batch", () => this.importRgb(true));
        this.bindClick("btn-run-rgb", () => this.runRgb());
        this.bindClick("btn-crop-rgb", () => this.cropRgb());
        this.bindClick("btn-run-rgb-batch-pipeline", () => this.runSelectedRgbBatch());
        this.bindClick("btn-select-rgb-output-folder", () => this.selectRgbOutputDirectory());
        this.bindClick("btn-save-rgb-batch-selected", () => this.saveRgbBatchSelected());

        this.bindClick("btn-save-xray-batch-left", () => this.saveXrayBatchLeft());
        this.bindClick("btn-hide-batch-progress", () => this.hideBatchProgress());

        this.bindClick("btn-report-doctor", () => this.setReportAudience("doctor"));
        this.bindClick("btn-report-personal", () => this.setReportAudience("personal"));
        this.bindClick("btn-report-ai", () => this.toggleReportAI());
        this.bindClick("btn-export-pdf", () => this.exportPdf());

        this.bindClick("btn-import-pseudoct-single", () => this.importPseudoCTSingle());
        this.bindClick("btn-import-pseudoct-batch", () => this.importPseudoCTBatch());
        this.bindClick("btn-refresh-pseudoct", () => this.fetchPseudoCTStatus(true));
        this.bindClick("btn-preview-pseudoct-strip", () => this.previewPseudoCTStrip());
        this.bindClick("btn-pseudoct-mode-transfer", () => this.setPseudoCTInferenceMode(PSEUDOCT_MODE_TRANSFER));
        this.bindClick("btn-pseudoct-mode-source", () => this.setPseudoCTInferenceMode(PSEUDOCT_MODE_SOURCE));
        this.bindClick("btn-pseudoct-show-compare", () => this.previewPseudoCTCompare());
        this.bindClick("btn-pseudoct-show-input", () => this.previewPseudoCTInputOnly());
        this.bindClick("btn-pseudoct-show-output", () => this.previewPseudoCTOutputOnly());
        this.bindClick("btn-pseudoct-save-input", () => this.savePseudoCTInput());
        this.bindClick("btn-pseudoct-save-generated", () => this.savePseudoCTOutput());
        this.bindClick("btn-pseudoct-loop-to-xray", () => this.loopPseudoCTToXray());

        this.bindClick("btn-mirror", () => this.mirror());
        this.bindClick("btn-swap-images", () => this.swapImages());
        this.bindClick("btn-save-image", () => this.saveImage());
        this.bindClick("btn-clear", () => this.clear());

        const pseudoctBatchList = this.el("pseudoct-batch-list");
        if (pseudoctBatchList) {
            pseudoctBatchList.addEventListener("click", (event) => {
                const target = event.target.closest("[data-pseudoct-index]");
                if (!target) {
                    return;
                }
                const index = Number(target.dataset.pseudoctIndex);
                if (Number.isInteger(index)) {
                    this.previewPseudoCTItem(index);
                }
            });
        }

        const xrayBatchList = this.el("xray-batch-result-list");
        if (xrayBatchList) {
            xrayBatchList.addEventListener("click", (event) => {
                const target = event.target.closest("[data-xray-batch-index]");
                if (!target) {
                    return;
                }
                const index = Number(target.dataset.xrayBatchIndex);
                if (Number.isInteger(index)) {
                    this.previewXrayBatchItem(index);
                }
            });
        }

        const rgbBatchList = this.el("rgb-batch-result-list");
        if (rgbBatchList) {
            rgbBatchList.addEventListener("click", (event) => {
                const target = event.target.closest("[data-rgb-batch-index]");
                if (!target) {
                    return;
                }
                const index = Number(target.dataset.rgbBatchIndex);
                if (Number.isInteger(index)) {
                    this.previewRGBBatchItem(index);
                }
            });
        }
    },

    initWindowingSliders() {
        const wlSlider = this.el("wl-slider");
        const wwSlider = this.el("ww-slider");
        const wlVal = this.el("wl-val");
        const wwVal = this.el("ww-val");

        if (!wlSlider || !wwSlider || !wlVal || !wwVal) {
            return;
        }

        const debounce = (func, wait) => {
            let timeout = null;
            return (...args) => {
                clearTimeout(timeout);
                timeout = setTimeout(() => func.apply(this, args), wait);
            };
        };

        const handleSlider = debounce(() => this.updateWindowing(), 180);

        wlSlider.addEventListener("input", () => {
            wlVal.innerText = wlSlider.value;
            handleSlider();
        });

        wwSlider.addEventListener("input", () => {
            wwVal.innerText = wwSlider.value;
            handleSlider();
        });
    },

    setMode(newMode, options = {}) {
        const preservePseudoCT = !!options.preservePseudoCT;
        const savedPseudoCT = preservePseudoCT
            ? {
                items: [...this.pseudoctItems],
                selected: this.selectedPseudoCTIndex,
            }
            : null;

        this.mode = newMode;

        const mapping = {
            xray: { tab: "tab-xray", panel: "panel-xray", metrics: "metrics-xray" },
            rgb: { tab: "tab-rgb", panel: "panel-rgb", metrics: "metrics-rgb" },
            pseudoct: { tab: "tab-pseudoct", panel: "panel-pseudoct", metrics: "metrics-pseudoct" },
        };

        Object.keys(mapping).forEach((key) => {
            const { tab, panel, metrics } = mapping[key];
            const tabEl = this.el(tab);
            const panelEl = this.el(panel);
            const metricsEl = this.el(metrics);

            if (tabEl) {
                tabEl.className = key === newMode ? this.classActiveTab : this.classInactiveTab;
                tabEl.setAttribute("aria-selected", key === newMode ? "true" : "false");
            }
            if (panelEl) {
                if (key === newMode) {
                    panelEl.classList.remove("hidden");
                    panelEl.classList.add("flex");
                } else {
                    panelEl.classList.add("hidden");
                    panelEl.classList.remove("flex");
                }
            }
            if (metricsEl) {
                if (key === newMode) {
                    metricsEl.classList.remove("hidden");
                    metricsEl.classList.add("block");
                } else {
                    metricsEl.classList.add("hidden");
                    metricsEl.classList.remove("block");
                }
            }
        });

        this.updateBatchCardVisibility();

        if (savedPseudoCT) {
            this.pseudoctItems = savedPseudoCT.items;
            this.selectedPseudoCTIndex = savedPseudoCT.selected;
            this.renderPseudoCTBatchList();
            this.updatePseudoCTMetrics(this.getSelectedPseudoCTItem());
        }

        if (newMode === "xray") {
            this.fetchReportAIStatus();
            this.updateReportAudienceButtons();
            this.renderXrayBatchList();
        }

        if (newMode === "rgb") {
            this.renderRGBBatchList();
        }

        if (newMode === "pseudoct") {
            this.updatePseudoCTModeButtons();
            this.fetchPseudoCTStatus(false);
            this.renderPseudoCTBatchList();
            this.updatePseudoCTMetrics(this.getSelectedPseudoCTItem());
        }
    },

    getReportAudienceLabel(audience) {
        return audience === "personal" ? "个人版报告" : "医生版报告";
    },

    getReportAudienceHint(audience) {
        if (audience === "personal") {
            if (this.reportAiEnabled && this.reportAiReady) {
                return "当前会在个人版 PDF 后追加一页 DeepSeek 增强解读。";
            }
            return "个人版会把结果翻译成更易懂的语言，便于患者和家属查看。";
        }
        return "医生版保留更专业的参数、角度和滑脱信息。";
    },

    setReportAudience(audience) {
        this.reportAudience = audience === "personal" ? "personal" : "doctor";
        this.updateReportAudienceButtons();
        this.setStatus(`报告版本已切换为${this.getReportAudienceLabel(this.reportAudience)}`, "#8A8F98");
    },

    async fetchReportAIStatus() {
        try {
            const data = await this.fetchJson("/api/report_ai/status");
            if (data.code === 200 && data.service) {
                this.reportAiReady = !!data.service.ready;
                this.reportAiModel = data.service.model || "--";
                if (!this.reportAiReady) {
                    this.reportAiEnabled = false;
                }
            } else {
                this.reportAiReady = false;
                this.reportAiEnabled = false;
                this.reportAiModel = "--";
            }
        } catch {
            this.reportAiReady = false;
            this.reportAiEnabled = false;
            this.reportAiModel = "--";
        }
        this.updateReportAudienceButtons();
    },

    toggleReportAI() {
        if (!this.reportAiReady) {
            this.setStatus("未检测到 DeepSeek 配置，请先完成后端配置", "#EF4444");
            return;
        }
        if (!this.reportAiEnabled) {
            const accepted = window.confirm(
                "开启后，系统会把当前病例的测量角度、滑脱结果和分析模式发送给 DeepSeek 生成个人版解读；不会发送原始影像或文件名。是否同意？"
            );
            if (!accepted) {
                this.setStatus("已取消 DeepSeek 增强", "#8A8F98");
                return;
            }
        }
        this.reportAiEnabled = !this.reportAiEnabled;
        this.updateReportAudienceButtons();
        this.setStatus(this.reportAiEnabled ? "DeepSeek 增强已开启" : "DeepSeek 增强已关闭", "#8A8F98");
    },

    updateReportAudienceButtons() {
        const doctorBtn = this.el("btn-report-doctor");
        const personalBtn = this.el("btn-report-personal");
        const hintEl = this.el("report-audience-hint");
        const exportLabelEl = this.el("btn-export-pdf-label");
        const aiBtn = this.el("btn-report-ai");
        const aiStatusEl = this.el("report-ai-status");
        const aiHintEl = this.el("report-ai-hint");

        if (doctorBtn) {
            doctorBtn.className = this.reportAudience === "doctor" ? this.classActiveMiniTab : this.classInactiveMiniTab;
        }
        if (personalBtn) {
            personalBtn.className = this.reportAudience === "personal" ? this.classActiveMiniTab : this.classInactiveMiniTab;
        }
        if (hintEl) {
            hintEl.innerText = this.getReportAudienceHint(this.reportAudience);
        }

        if (aiStatusEl) {
            aiStatusEl.innerText = this.reportAiReady ? `已连接 ${this.reportAiModel}` : "未配置 DeepSeek";
            aiStatusEl.className = this.reportAiReady ? "text-[11px] text-[#22C55E]" : "text-[11px] text-[#EF4444]";
        }

        if (aiBtn) {
            aiBtn.disabled = !this.reportAiReady;
            if (this.reportAiReady && this.reportAiEnabled) {
                aiBtn.className = "w-full py-2.5 bg-[#5E6AD2] hover:bg-[#6B79E4] text-white rounded-xl text-sm font-medium transition-all shadow-[0_0_15px_rgba(94,106,210,0.25)]";
                aiBtn.innerHTML = `<i class="fa-solid fa-sparkles mr-2"></i>DeepSeek 增强：已开启`;
            } else if (this.reportAiReady) {
                aiBtn.className = "w-full py-2.5 bg-white/5 hover:bg-white/10 border border-white/10 rounded-xl text-sm font-medium transition-all text-[#B4B9C4] hover:text-white";
                aiBtn.innerHTML = `<i class="fa-solid fa-sparkles mr-2"></i>DeepSeek 增强：已关闭`;
            } else {
                aiBtn.className = "w-full py-2.5 bg-white/5 border border-white/10 rounded-xl text-sm font-medium transition-all text-[#8A8F98] cursor-not-allowed";
                aiBtn.innerHTML = `<i class="fa-solid fa-key mr-2"></i>DeepSeek 未配置`;
            }
        }

        if (aiHintEl) {
            if (!this.reportAiReady) {
                aiHintEl.innerText = "请先配置 DEEPSEEK_API_KEY。";
            } else if (this.reportAudience === "doctor") {
                aiHintEl.innerText = "DeepSeek 增强仅作用于个人版报告。";
            } else if (this.reportAiEnabled) {
                aiHintEl.innerText = `已同意发送测量指标（不含原始影像和文件名），当前模型：${this.reportAiModel}。`;
            } else {
                aiHintEl.innerText = "开启前会说明发送范围并征得同意；仅发送测量指标，不发送原始影像或文件名。";
            }
        }

        if (exportLabelEl) {
            if (this.reportAudience === "doctor") {
                exportLabelEl.innerText = "导出医生版报告";
            } else if (this.reportAiEnabled && this.reportAiReady) {
                exportLabelEl.innerText = "导出个人 AI 增强报告";
            } else {
                exportLabelEl.innerText = "导出个人版报告";
            }
        }
    },

    setPseudoCTInferenceMode(nextMode) {
        this.pseudoctInferenceMode = nextMode === PSEUDOCT_MODE_SOURCE ? PSEUDOCT_MODE_SOURCE : PSEUDOCT_MODE_TRANSFER;
        this.updatePseudoCTModeButtons();
        this.clearPseudoCTResults(true);
        this.updateDisplay(null, null);
        this.fetchPseudoCTStatus(false);
        this.setStatus(`X光图 已切换为${this.getPseudoCTModeLabel(this.pseudoctInferenceMode)}`, "#8A8F98");
    },

    updatePseudoCTModeButtons() {
        const transferBtn = this.el("btn-pseudoct-mode-transfer");
        const sourceBtn = this.el("btn-pseudoct-mode-source");

        if (transferBtn) {
            transferBtn.className = this.pseudoctInferenceMode === PSEUDOCT_MODE_TRANSFER ? this.classActiveMiniTab : this.classInactiveMiniTab;
        }
        if (sourceBtn) {
            sourceBtn.className = this.pseudoctInferenceMode === PSEUDOCT_MODE_SOURCE ? this.classActiveMiniTab : this.classInactiveMiniTab;
        }
    },

    getPseudoCTModeLabel(mode) {
        return mode === PSEUDOCT_MODE_SOURCE ? "Pix2Pix 左半图直推" : "真实 RGB 迁移模式";
    },

    getPseudoCTLayoutLabel(layout) {
        if (layout === "pair_auto_split") {
            return "整张配对图自动切左半";
        }
        if (layout === "left_half") {
            return "左半图直推";
        }
        if (layout === "roi_crop_soft_mask") {
            return "ROI 裁剪 + soft mask";
        }
        return layout || "--";
    },

    getPseudoCTDisplayInputUrl(item) {
        if (!item) {
            return "";
        }
        return item.display_input_url || item.crop_url || item.overlay_url || item.masked_url || "";
    },

    getPseudoCTDisplayInputLabel(item) {
        if (!item) {
            return "输入图";
        }
        return item.display_input_label || (item.inference_mode === PSEUDOCT_MODE_SOURCE ? "左半输入图" : "ROI 输入图");
    },

    getSelectedPseudoCTItem() {
        if (this.selectedPseudoCTIndex < 0 || this.selectedPseudoCTIndex >= this.pseudoctItems.length) {
            return null;
        }
        return this.pseudoctItems[this.selectedPseudoCTIndex];
    },

    buildXrayInputLabel(payload) {
        if (payload && payload.was_flipped) {
            return "X光模型输入（已镜像为朝左）";
        }
        if (payload && payload.orientation_label) {
            return `X光模型输入（${payload.orientation_label}）`;
        }
        return "X光模型输入";
    },

    formatPercent(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return "--";
        }
        return `${(Number(value) * 100).toFixed(1)}%`;
    },

    formatSize(size) {
        if (!Array.isArray(size) || size.length !== 2) {
            return "--";
        }
        return `${size[0]} × ${size[1]}`;
    },

    sanitizeFilename(value) {
        return String(value || "image")
            .replace(/[<>:"/\\|?*\u0000-\u001F]/g, "_")
            .replace(/\s+/g, "_")
            .replace(/_+/g, "_")
            .replace(/^_+|_+$/g, "");
    },

    buildDownloadName(originalName, suffix) {
        const safe = this.sanitizeFilename(String(originalName || "image").replace(/\.[^.]+$/, "")) || "image";
        return `${safe}_${suffix}.png`;
    },

    escapeHtml(value) {
        return String(value || "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll("\"", "&quot;")
            .replaceAll("'", "&#39;");
    },

    openFileDialog({ accept, multiple = false, folder = false }, callback) {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = accept || "image/*";
        input.multiple = folder ? true : !!multiple;
        if (folder) {
            input.setAttribute("webkitdirectory", "");
            input.setAttribute("directory", "");
        }
        input.onchange = (event) => {
            callback(Array.from(event.target.files || []));
        };
        input.click();
    },

    filterSupportedFiles(files, allowDicom = false) {
        const imagePattern = /\.(png|jpg|jpeg|bmp|webp)$/i;
        const xrayPattern = /\.(png|jpg|jpeg|bmp|webp|dcm)$/i;
        const pattern = allowDicom ? xrayPattern : imagePattern;
        return (files || []).filter((file) => pattern.test(file.name || ""));
    },

    buildRelativePathList(files) {
        return files.map((file) => file.relativePath || file.webkitRelativePath || file.name || "image");
    },

    getFolderNameFromFiles(files) {
        const first = (files || [])[0];
        const relPath = first ? (first.relativePath || first.webkitRelativePath || first.name || "") : "";
        return relPath.includes("/") ? relPath.split("/")[0] : "已选择文件夹";
    },

    setFileRelativePath(file, relativePath) {
        try {
            Object.defineProperty(file, "relativePath", {
                value: relativePath,
                configurable: true,
            });
        } catch {
            file.relativePath = relativePath;
        }
        return file;
    },

    async collectFilesFromDirectoryHandle(directoryHandle, prefix = "") {
        const files = [];
        for await (const [name, handle] of directoryHandle.entries()) {
            const relativePath = prefix ? `${prefix}/${name}` : name;
            if (handle.kind === "file") {
                const file = await handle.getFile();
                files.push(this.setFileRelativePath(file, relativePath));
            } else if (handle.kind === "directory") {
                const childFiles = await this.collectFilesFromDirectoryHandle(handle, relativePath);
                files.push(...childFiles);
            }
        }
        return files;
    },

    async pickRgbInputFolderFiles() {
        if (window.showDirectoryPicker) {
            const directoryHandle = await window.showDirectoryPicker({ mode: "read" });
            const files = await this.collectFilesFromDirectoryHandle(directoryHandle, directoryHandle.name || "");
            return {
                files,
                folderName: directoryHandle.name || "已选择文件夹",
            };
        }

        return await new Promise((resolve) => {
            this.openFileDialog({ accept: "image/*", folder: true }, (files) => {
                resolve({
                    files,
                    folderName: this.getFolderNameFromFiles(files),
                });
            });
        });
    },

    updateRgbBatchFolderUI() {
        const summaryEl = this.el("rgb-batch-summary");
        const hintEl = this.el("rgb-batch-folder-hint");
        const count = this.rgbBatchFiles.length;
        if (summaryEl) {
            summaryEl.innerText = count > 0 ? `${this.rgbBatchInputFolderName || "已选择文件夹"} / ${count} 张` : "未选择文件夹";
        }
        if (hintEl) {
            hintEl.innerText = count > 0
                ? `已选择 ${count} 张 RGB 图片。点击“一键批量朝左 + ROI识别 + 自动裁剪”后会处理完整队列。`
                : "先选择输入文件夹，再选择输出文件夹。若输出前未选择目录，保存时会自动弹出目录选择窗口。";
        }
    },

    updateXrayBatchFolderUI() {
        const summaryEl = this.el("xray-batch-summary");
        const hintEl = this.el("xray-batch-folder-hint");
        const count = this.xrayBatchFiles.length;
        if (summaryEl) {
            summaryEl.innerText = count > 0
                ? `${this.xrayBatchInputFolderName || "已选择文件夹"} / ${count} 张`
                : "未选择文件夹";
        }
        if (hintEl) {
            hintEl.innerText = count > 0
                ? `已选择 ${count} 个 X 光 / DICOM 文件。点击“批量统一朝左”开始处理。`
                : "导入一个文件夹后，可批量把 X 光图片统一为患者面向左侧的结果，并可保存到自选目录。";
        }
    },

    importXray(isBatch) {
        if (!isBatch) {
            this.openFileDialog({ accept: "image/*,.dcm", multiple: false }, (files) => {
                if (files.length > 0) {
                    this.triggerUpload(files[0]);
                }
            });
            return;
        }

        this.openFileDialog({ accept: "image/*,.dcm", folder: true }, (files) => {
            const validFiles = this.filterSupportedFiles(files, true);
            if (!validFiles.length) {
                this.setStatus("文件夹中未找到可用的 X 光 / DICOM 文件", "#EF4444");
                return;
            }
            this.xrayBatchFiles = validFiles;
            this.xrayBatchInputFolderName = this.getFolderNameFromFiles(validFiles);
            this.xrayBatchJob = null;
            this.selectedXrayBatchIndex = -1;
            this.renderXrayBatchList();
            this.updateXrayBatchFolderUI();
            this.setStatus(
                `已选择 X 光输入文件夹：${this.xrayBatchInputFolderName}，共 ${validFiles.length} 张`,
                "#22C55E"
            );
        });
    },

    async runSelectedXrayBatch() {
        if (!this.xrayBatchFiles.length) {
            this.importXray(true);
            return;
        }
        await this.startXrayBatch(this.xrayBatchFiles);
    },

    async importRgb(isBatch) {
        if (!isBatch) {
            this.openFileDialog({ accept: "image/*", multiple: false }, (files) => {
                if (files.length > 0) {
                    this.triggerUpload(files[0]);
                }
            });
            return;
        }

        try {
            const selection = await this.pickRgbInputFolderFiles();
            const validFiles = this.filterSupportedFiles(selection.files, false)
                .sort((a, b) => this.buildRelativePathList([a])[0].localeCompare(this.buildRelativePathList([b])[0], "zh-Hans-CN"));
            if (!validFiles.length) {
                this.setStatus("文件夹中未找到可用的 RGB 图片", "#EF4444");
                return;
            }

            this.rgbBatchFiles = validFiles;
            this.rgbBatchInputFolderName = selection.folderName || this.getFolderNameFromFiles(validFiles);
            this.rgbBatchJob = null;
            this.selectedRgbBatchIndex = -1;
            this.renderRGBBatchList();
            this.updateRgbBatchFolderUI();
            this.setStatus(`已选择 RGB 输入文件夹：${this.rgbBatchInputFolderName}，共 ${validFiles.length} 张`, "#22C55E");
        } catch (error) {
            if (error && error.name === "AbortError") {
                this.setStatus("已取消 RGB 输入文件夹选择", "#8A8F98");
                return;
            }
            console.error(error);
            this.setStatus(`RGB 输入文件夹选择失败: ${error.message || "未知错误"}`, "#EF4444");
        }
    },

    async runSelectedRgbBatch() {
        if (!this.rgbBatchFiles.length) {
            await this.importRgb(true);
            if (!this.rgbBatchFiles.length) {
                return;
            }
        }
        await this.startRgbBatch(this.rgbBatchFiles);
    },

    async startXrayBatch(files) {
        this.setMode("xray");
        this.progressDismissed = false;
        this.xrayBatchJob = null;
        this.selectedXrayBatchIndex = -1;
        this.renderXrayBatchList();
        this.renderBatchProgress("xray", {
            title: "X 光文件夹批处理",
            progress: 0,
            status: "uploading",
            total: files.length,
            done: 0,
            message: "正在上传文件夹并创建批处理任务...",
            current_label: "",
        });

        const formData = new FormData();
        const scale = parseFloat(this.el("scale-input")?.value || "1.5") || 1.5;
        formData.append("scale", String(scale));
        files.forEach((file) => formData.append("files", file, file.name));
        this.buildRelativePathList(files).forEach((relPath) => formData.append("relative_paths", relPath));

        try {
            const data = await this.fetchJson("/api/xray/batch/start", {
                method: "POST",
                body: formData,
            });

            if (data.code !== 200 || !data.job) {
                this.renderBatchProgress(null, null);
                this.setStatus(`X 光批处理启动失败: ${data.message || "未知错误"}`, "#EF4444");
                return;
            }

            this.handleBatchJob("xray", data.job);
            this.startBatchPolling("xray", data.job.job_id);
            this.setStatus(`X 光文件夹已导入，共 ${data.job.total} 张，正在统一朝左`, "#5E6AD2");
        } catch (error) {
            console.error(error);
            this.renderBatchProgress(null, null);
            this.setStatus("X 光批处理请求失败", "#EF4444");
        }
    },

    async startRgbBatch(files) {
        this.setMode("rgb");
        this.progressDismissed = false;
        this.rgbBatchJob = null;
        this.selectedRgbBatchIndex = -1;
        this.renderRGBBatchList();
        this.renderBatchProgress("rgb", {
            title: "RGB 文件夹批处理",
            progress: 0,
            status: "uploading",
            total: files.length,
            done: 0,
            message: "正在上传文件夹并创建批处理任务...",
            current_label: "",
        });

        const formData = new FormData();
        files.forEach((file) => formData.append("files", file, file.name));
        this.buildRelativePathList(files).forEach((relPath) => formData.append("relative_paths", relPath));

        try {
            const data = await this.fetchJson("/api/rgb/batch/start", {
                method: "POST",
                body: formData,
            });

            if (data.code !== 200 || !data.job) {
                this.renderBatchProgress(null, null);
                this.setStatus(`RGB 批处理启动失败: ${data.message || "未知错误"}`, "#EF4444");
                return;
            }

            this.handleBatchJob("rgb", data.job);
            this.startBatchPolling("rgb", data.job.job_id);
            this.setStatus(`RGB 文件夹已导入，共 ${data.job.total} 张，正在朝左化 + ROI 识别 + 自动裁剪`, "#5E6AD2");
        } catch (error) {
            console.error(error);
            this.renderBatchProgress(null, null);
            this.setStatus("RGB 批处理请求失败", "#EF4444");
        }
    },

    startBatchPolling(kind, jobId) {
        this.stopBatchPolling(kind);

        const poll = async () => {
            let finished = false;
            try {
                const data = await this.fetchJson(`/api/batch_jobs/${jobId}`, { timeoutMs: 15000 });
                if (data.code !== 200 || !data.job) {
                    finished = data.code === 404;
                } else {
                    this.handleBatchJob(kind, data.job);

                    if (data.job.status === "completed" || data.job.status === "failed") {
                        finished = true;
                        const summary = data.job.summary || { success: 0, failed: 0 };
                        const tone = summary.success > 0 ? "#22C55E" : "#EF4444";
                        this.setStatus(
                            `${data.job.title || "批处理"}已结束：成功 ${summary.success}，失败 ${summary.failed}`,
                            tone
                        );
                    }
                }
            } catch (error) {
                console.error(error);
            }

            if (finished) {
                this.stopBatchPolling(kind);
            } else {
                this.batchPollers[kind] = window.setTimeout(poll, 1000);
            }
        };

        this.batchPollers[kind] = window.setTimeout(poll, 0);
    },

    stopBatchPolling(kind) {
        if (this.batchPollers[kind]) {
            window.clearTimeout(this.batchPollers[kind]);
            this.batchPollers[kind] = null;
        }
    },

    hideBatchProgress() {
        this.progressDismissed = true;
        const card = this.el("batch-progress-card") || this.el("batch-progress-panel");
        card?.classList.add("hidden");
        card?.classList.remove("block");
    },

    handleBatchJob(kind, job) {
        this.activeProgressKind = kind;

        if (kind === "xray") {
            this.xrayBatchJob = job;
            this.renderXrayBatchList();

            if (this.selectedXrayBatchIndex < 0 && Array.isArray(job.items) && job.items.length > 0) {
                const firstSuccessIndex = job.items.findIndex((item) => item.status === "success");
                if (firstSuccessIndex >= 0) {
                    this.selectedXrayBatchIndex = firstSuccessIndex;
                    if (this.mode === "xray") {
                        this.previewXrayBatchItem(firstSuccessIndex);
                    }
                }
            }
        } else {
            this.rgbBatchJob = job;
            this.renderRGBBatchList();

            if (this.selectedRgbBatchIndex < 0 && Array.isArray(job.items) && job.items.length > 0) {
                const firstSuccessIndex = job.items.findIndex((item) => item.status === "success");
                if (firstSuccessIndex >= 0) {
                    this.selectedRgbBatchIndex = firstSuccessIndex;
                    if (this.mode === "rgb") {
                        this.previewRGBBatchItem(firstSuccessIndex);
                    }
                }
            }
        }

        this.renderBatchProgress(kind, job);
        this.updateBatchCardVisibility();
    },

    renderBatchProgress(kind, job) {
        const card = this.el("batch-progress-card") || this.el("batch-progress-panel");
        const titleEl = this.el("batch-progress-title");
        const fillEl = this.el("batch-progress-fill") || this.el("batch-progress-bar");
        const percentEl = this.el("batch-progress-percent") || this.el("batch-progress-text");
        const textEl = this.el("batch-progress-result") || this.el("batch-progress-subtitle");
        const countEl = this.el("batch-progress-count");
        const currentEl = this.el("batch-progress-current");
        const subtitleEl = this.el("batch-progress-subtitle");

        if (!card || !titleEl || !fillEl || !percentEl || !textEl || !currentEl) {
            return;
        }

        if (!job || this.progressDismissed) {
            card.classList.add("hidden");
            card.classList.remove("block");
            return;
        }

        const progress = Number(job.progress || 0);
        const total = Number(job.total || 0);
        const done = Number(job.done || 0);
        const title = job.title || (kind === "xray" ? "X 光批处理" : "RGB 批处理");

        card.classList.remove("hidden");
        card.classList.add("block");

        titleEl.innerText = title;
        fillEl.style.width = `${Math.max(0, Math.min(100, progress))}%`;
        const progressTrack = this.el("batch-progress-track");
        progressTrack?.setAttribute("aria-valuenow", String(Math.round(Math.max(0, Math.min(100, progress)))));
        percentEl.innerText = `${Math.round(progress)}%`;
        if (countEl) {
            countEl.innerText = `${done} / ${total}`;
        }
        if (subtitleEl) {
            subtitleEl.innerText = `${done} / ${total}`;
        }

        if (job.status === "uploading") {
            textEl.innerText = "正在上传文件夹...";
        } else if (job.status === "running") {
            textEl.innerText = job.message || "正在处理...";
        } else if (job.status === "completed") {
            const summary = job.summary || { success: 0, failed: 0 };
            textEl.innerText = `处理完成：成功 ${summary.success}，失败 ${summary.failed}`;
        } else if (job.status === "failed") {
            textEl.innerText = job.message || "批处理失败";
        } else {
            textEl.innerText = job.message || "等待开始...";
        }

        currentEl.innerText = job.current_label ? `当前文件：${job.current_label}` : "当前文件：--";
    },

    updateBatchCardVisibility() {
        const xrayList = this.el("xray-batch-result-list");
        const rgbList = this.el("rgb-batch-result-list");

        if (xrayList && this.mode === "xray") {
            xrayList.parentElement?.classList.remove("hidden");
        }
        if (rgbList && this.mode === "rgb") {
            rgbList.parentElement?.classList.remove("hidden");
        }
    },

    renderXrayBatchList() {
        const list = this.el("xray-batch-result-list");
        if (!list) {
            return;
        }

        const job = this.xrayBatchJob;
        if (!job) {
            list.innerHTML = `<p class="opacity-50 uppercase tracking-widest text-[10px] text-center mt-1">等待文件夹导入...</p>`;
            return;
        }

        if (!Array.isArray(job.items) || job.items.length === 0) {
            list.innerHTML = `<p class="opacity-50 uppercase tracking-widest text-[10px] text-center mt-1">${this.escapeHtml(job.message || "正在处理...")}</p>`;
            return;
        }

        list.innerHTML = job.items.map((item, index) => {
            const active = index === this.selectedXrayBatchIndex;
            const activeClass = active
                ? "border-[#5E6AD2]/60 bg-white/[0.06] shadow-[0_0_18px_rgba(94,106,210,0.25)]"
                : "border-white/5 bg-white/[0.02] hover:bg-white/[0.05]";
            const thumb = item.oriented_url || item.preview_url || "";
            const note = item.was_flipped ? "已镜像为朝左" : (item.orientation_label || "朝向未知");
            const statusText = item.status === "success" ? "成功" : "失败";
            const statusColor = item.status === "success" ? "text-[#22C55E]" : "text-[#EF4444]";

            return `
                <button
                    type="button"
                    data-xray-batch-index="${index}"
                    class="w-full text-left rounded-xl border ${activeClass} transition-all p-3 mb-2 last:mb-0"
                >
                    <div class="flex gap-3">
                        <div class="w-16 h-20 rounded-lg border border-white/10 bg-black/40 overflow-hidden shrink-0">
                            ${thumb ? `<img src="${thumb}" class="w-full h-full object-cover" alt="Xray Batch Preview">` : `<div class="w-full h-full flex items-center justify-center text-[10px] text-[#8A8F98]">N/A</div>`}
                        </div>
                        <div class="min-w-0 flex-1">
                            <div class="text-[12px] text-white font-medium truncate">${this.escapeHtml(item.filename)}</div>
                            <div class="text-[10px] ${statusColor} mt-1">${statusText}</div>
                            <div class="text-[10px] text-[#8A8F98] mt-1">${this.escapeHtml(note)}</div>
                            <div class="text-[10px] text-[#8A8F98] mt-1">${this.escapeHtml(item.note || "--")}</div>
                        </div>
                    </div>
                </button>
            `;
        }).join("");
    },

    renderRGBBatchList() {
        const list = this.el("rgb-batch-result-list");
        if (!list) {
            return;
        }

        const job = this.rgbBatchJob;
        if (!job) {
            list.innerHTML = `<p class="opacity-50 uppercase tracking-widest text-[10px] text-center mt-1">等待文件夹导入...</p>`;
            return;
        }

        if (!Array.isArray(job.items) || job.items.length === 0) {
            list.innerHTML = `<p class="opacity-50 uppercase tracking-widest text-[10px] text-center mt-1">${this.escapeHtml(job.message || "正在处理...")}</p>`;
            return;
        }

        list.innerHTML = job.items.map((item, index) => {
            const active = index === this.selectedRgbBatchIndex;
            const activeClass = active
                ? "border-[#5E6AD2]/60 bg-white/[0.06] shadow-[0_0_18px_rgba(94,106,210,0.25)]"
                : "border-white/5 bg-white/[0.02] hover:bg-white/[0.05]";
            const thumb = item.crop_url || item.detected_url || item.left_url || "";
            const note = item.crop_url ? "已自动裁剪" : "未检测到 ROI";
            const confText = item.bbox_confidence !== null && item.bbox_confidence !== undefined
                ? `ROI ${this.formatPercent(item.bbox_confidence)}`
                : "ROI --";
            const orientText = item.was_flipped ? "已镜像为朝左" : (item.orientation_label || "朝向未知");
            const statusText = item.status === "success" ? "成功" : "失败";
            const statusColor = item.status === "success" ? "text-[#22C55E]" : "text-[#EF4444]";

            return `
                <button
                    type="button"
                    data-rgb-batch-index="${index}"
                    class="w-full text-left rounded-xl border ${activeClass} transition-all p-3 mb-2 last:mb-0"
                >
                    <div class="flex gap-3">
                        <div class="w-16 h-20 rounded-lg border border-white/10 bg-black/40 overflow-hidden shrink-0">
                            ${thumb ? `<img src="${thumb}" class="w-full h-full object-cover" alt="RGB Batch Preview">` : `<div class="w-full h-full flex items-center justify-center text-[10px] text-[#8A8F98]">N/A</div>`}
                        </div>
                        <div class="min-w-0 flex-1">
                            <div class="text-[12px] text-white font-medium truncate">${this.escapeHtml(item.filename)}</div>
                            <div class="text-[10px] ${statusColor} mt-1">${statusText}</div>
                            <div class="text-[10px] text-[#8A8F98] mt-1">${this.escapeHtml(orientText)}</div>
                            <div class="text-[10px] text-[#8A8F98] mt-1">${this.escapeHtml(confText)}</div>
                            <div class="text-[10px] text-[#8A8F98] mt-1">${this.escapeHtml(note)}</div>
                        </div>
                    </div>
                </button>
            `;
        }).join("");
    },

    previewXrayBatchItem(index) {
        const job = this.xrayBatchJob;
        if (!job || !Array.isArray(job.items) || index < 0 || index >= job.items.length) {
            return;
        }

        const item = job.items[index];
        this.selectedXrayBatchIndex = index;
        this.renderXrayBatchList();

        if (item.status !== "success") {
            this.setStatus(`该批处理项失败：${item.message || "未知错误"}`, "#EF4444");
            return;
        }

        this.setMode("xray");
        this.updateDisplay(item.oriented_url || item.preview_url || "", null, "批量朝左结果", "");
        this.updateXrayMetrics(null, null);
        this.setStatus(`当前预览：${item.filename}`, "#22C55E");
    },

    previewRGBBatchItem(index) {
        const job = this.rgbBatchJob;
        if (!job || !Array.isArray(job.items) || index < 0 || index >= job.items.length) {
            return;
        }

        const item = job.items[index];
        this.selectedRgbBatchIndex = index;
        this.renderRGBBatchList();

        if (item.status !== "success") {
            this.setStatus(`该批处理项失败：${item.message || "未知错误"}`, "#EF4444");
            return;
        }

        this.setMode("rgb");

        const mainSrc = item.crop_url || item.detected_url || item.left_url || "";
        let subSrc = "";
        let mainLabel = "批量检测结果";
        let subLabel = "";

        if (item.crop_url) {
            mainLabel = "自动裁剪结果";
            subSrc = item.detected_url || item.left_url || "";
            subLabel = item.detected_url ? "ROI 标注图" : "朝左标准图";
        } else {
            mainLabel = item.detected_url ? "ROI 标注图" : "朝左标准图";
            subSrc = item.left_url || "";
            subLabel = item.left_url ? "朝左标准图" : "";
        }

        if (subSrc === mainSrc) {
            subSrc = "";
            subLabel = "";
        }

        this.updateDisplay(mainSrc, subSrc, mainLabel, subLabel);

        if (Array.isArray(item.bbox) && item.bbox.length === 4 && item.bbox_confidence !== null && item.bbox_confidence !== undefined) {
            this.updateRGBMetrics({
                conf: item.bbox_confidence,
                box: item.bbox,
            });
        } else {
            this.updateRGBMetrics(null);
        }

        this.setStatus(`当前预览：${item.filename}`, "#22C55E");
    },

    async saveXrayBatchLeft() {
        const entries = this.collectBatchSaveEntries("xray", "left");
        if (!entries.length) {
            this.setStatus("当前没有可保存的 X 光批处理结果", "#EF4444");
            return;
        }
        await this.saveEntriesToDirectory(entries, `MindSpine_Xray_Left_${Date.now()}`);
    },

    async saveRgbBatchVariant(variant) {
        const entries = this.collectBatchSaveEntries("rgb", variant);
        if (!entries.length) {
            this.setStatus("当前没有可保存的 RGB 批处理结果", "#EF4444");
            return;
        }

        const labelMap = {
            left: "Left",
            detected: "Detected",
            crop: "Cropped",
        };
        await this.saveEntriesToDirectory(entries, `MindSpine_RGB_${labelMap[variant] || "Batch"}_${Date.now()}`);
    },

    updateRgbOutputDirectoryUI() {
        const outputEl = this.el("rgb-output-folder-name");
        if (outputEl) {
            outputEl.innerText = this.rgbOutputDirectoryName
                ? `输出目录：${this.rgbOutputDirectoryName}`
                : "输出目录：未选择";
        }
    },

    async selectRgbOutputDirectory() {
        if (!window.showDirectoryPicker) {
            this.setStatus("当前浏览器不支持选择输出文件夹，保存时将回退为浏览器下载", "#F59E0B");
            return false;
        }

        try {
            const handle = await window.showDirectoryPicker({ mode: "readwrite" });
            this.rgbOutputDirectoryHandle = handle;
            this.rgbOutputDirectoryName = handle.name || "已选择目录";
            this.updateRgbOutputDirectoryUI();
            this.setStatus(`RGB 输出目录已选择：${this.rgbOutputDirectoryName}`, "#22C55E");
            return true;
        } catch (error) {
            if (error && error.name === "AbortError") {
                this.setStatus("已取消 RGB 输出文件夹选择", "#8A8F98");
                return false;
            }
            console.error(error);
            this.setStatus(`RGB 输出文件夹选择失败: ${error.message || "未知错误"}`, "#EF4444");
            return false;
        }
    },

    getSelectedRgbSaveVariants() {
        const variants = [];
        if (this.el("chk-save-rgb-left")?.checked) {
            variants.push("left");
        }
        if (this.el("chk-save-rgb-detected")?.checked) {
            variants.push("detected");
        }
        if (this.el("chk-save-rgb-crop")?.checked) {
            variants.push("crop");
        }
        return variants;
    },

    collectRgbSelectedSaveEntries() {
        const folderMap = {
            left: "left",
            detected: "detected",
            crop: "crop",
        };
        const entries = [];
        this.getSelectedRgbSaveVariants().forEach((variant) => {
            this.collectBatchSaveEntries("rgb", variant).forEach((entry) => {
                entries.push({
                    ...entry,
                    folderName: folderMap[variant] || "rgb",
                });
            });
        });
        return entries;
    },

    async ensureRgbOutputDirectory() {
        if (this.rgbOutputDirectoryHandle) {
            return true;
        }
        return await this.selectRgbOutputDirectory();
    },

    async saveRgbBatchSelected() {
        if (!this.rgbBatchJob) {
            this.setStatus("请先完成 RGB 批处理后再保存结果", "#EF4444");
            return;
        }
        if (this.rgbBatchJob.status !== "completed") {
            this.setStatus("RGB 批处理仍在进行中，请等待全部图像处理完成后再保存", "#F59E0B");
            return;
        }

        const variants = this.getSelectedRgbSaveVariants();
        if (!variants.length) {
            this.setStatus("请至少勾选一种 RGB 输出结果", "#EF4444");
            return;
        }

        const entries = this.collectRgbSelectedSaveEntries();
        if (!entries.length) {
            this.setStatus("当前没有可保存的 RGB 批处理结果", "#EF4444");
            return;
        }

        if (!window.showDirectoryPicker) {
            entries.forEach((entry) => this.downloadUrl(entry.url, entry.filename));
            this.setStatus("当前环境不支持选择目录，已回退为逐个下载", "#F59E0B");
            return;
        }

        const ready = await this.ensureRgbOutputDirectory();
        if (!ready || !this.rgbOutputDirectoryHandle) {
            return;
        }

        try {
            for (let i = 0; i < entries.length; i += 1) {
                const entry = entries[i];
                const targetHandle = await this.rgbOutputDirectoryHandle.getDirectoryHandle(entry.folderName, { create: true });
                const response = await fetch(entry.url);
                if (!response.ok) {
                    throw new Error(`下载失败: ${entry.filename}`);
                }
                const blob = await response.blob();
                const fileHandle = await targetHandle.getFileHandle(entry.filename, { create: true });
                const writable = await fileHandle.createWritable();
                await writable.write(blob);
                await writable.close();
                this.setStatus(`正在保存 RGB 结果 ${i + 1}/${entries.length}...`, "#5E6AD2");
            }

            this.setStatus(`RGB 勾选结果已全部保存到：${this.rgbOutputDirectoryName}`, "#22C55E");
        } catch (error) {
            console.error(error);
            this.setStatus(`RGB 结果保存失败: ${error.message || "未知错误"}`, "#EF4444");
        }
    },

    collectBatchSaveEntries(kind, variant) {
        const job = kind === "xray" ? this.xrayBatchJob : this.rgbBatchJob;
        if (!job || !Array.isArray(job.items)) {
            return [];
        }

        return job.items
            .filter((item) => item.status === "success")
            .map((item) => {
                if (kind === "xray") {
                    if (!item.oriented_url) {
                        return null;
                    }
                    return {
                        url: item.oriented_url,
                        filename: this.buildDownloadName(item.filename, "xray_left"),
                    };
                }

                if (variant === "left" && item.left_url) {
                    return {
                        url: item.left_url,
                        filename: this.buildDownloadName(item.filename, "rgb_left"),
                    };
                }

                if (variant === "detected" && item.detected_url) {
                    return {
                        url: item.detected_url,
                        filename: this.buildDownloadName(item.filename, "rgb_detected"),
                    };
                }

                if (variant === "crop" && item.crop_url) {
                    return {
                        url: item.crop_url,
                        filename: this.buildDownloadName(item.filename, "rgb_crop"),
                    };
                }

                return null;
            })
            .filter(Boolean);
    },

    async saveEntriesToDirectory(entries, suggestedFolderName) {
        if (!entries.length) {
            return;
        }

        if (!window.showDirectoryPicker) {
            entries.forEach((entry) => this.downloadUrl(entry.url, entry.filename));
            this.setStatus("当前环境不支持选择目录，已回退为逐个下载", "#F59E0B");
            return;
        }

        try {
            const rootHandle = await window.showDirectoryPicker({ mode: "readwrite" });
            const targetHandle = await rootHandle.getDirectoryHandle(suggestedFolderName, { create: true });

            this.saveDirectoryHandle = rootHandle;
            this.saveDirectoryName = rootHandle.name || "已选择目录";

            for (let i = 0; i < entries.length; i += 1) {
                const entry = entries[i];
                const response = await fetch(entry.url);
                if (!response.ok) {
                    throw new Error(`下载失败: ${entry.filename}`);
                }
                const blob = await response.blob();
                const fileHandle = await targetHandle.getFileHandle(entry.filename, { create: true });
                const writable = await fileHandle.createWritable();
                await writable.write(blob);
                await writable.close();

                this.setStatus(`正在保存 ${i + 1}/${entries.length}...`, "#5E6AD2");
            }

            this.setStatus(`已保存到所选目录：${suggestedFolderName}`, "#22C55E");
        } catch (error) {
            if (error && error.name === "AbortError") {
                this.setStatus("已取消保存目录选择", "#8A8F98");
                return;
            }
            console.error(error);
            this.setStatus(`保存失败: ${error.message || "未知错误"}`, "#EF4444");
        }
    },

    importPseudoCTSingle() {
        this.openFileDialog({ accept: "image/*", multiple: false }, (files) => {
            if (files.length > 0) {
                this.runPseudoCTSingle(files[0]);
            }
        });
    },

    importPseudoCTBatch() {
        this.openFileDialog({ accept: "image/*", multiple: true }, (files) => {
            if (!files.length) {
                return;
            }
            if (files.length === 1) {
                this.runPseudoCTSingle(files[0]);
                return;
            }
            this.runPseudoCTBatch(files);
        });
    },

    async triggerUpload(file) {
        if (!file) {
            return;
        }

        this.setStatus("正在上传并解析图像...", "#5E6AD2");

        const formData = new FormData();
        formData.append("file", file);
        formData.append("mode", this.mode);

        try {
            const data = await this.fetchJson("/api/upload", {
                method: "POST",
                body: formData,
            });

            if (data.code !== 200) {
                this.setStatus(`解析失败: ${data.message}`, "#EF4444");
                return;
            }

            this.currentImageId = data.id;
            this.updateDisplay(data.preview_b64, null, "原始图像", "");

            if (data.dicom_info && data.dicom_info.ww) {
                this.updateDICOMUI(
                    data.dicom_info.wl,
                    data.dicom_info.ww,
                    data.dicom_info.min_v,
                    data.dicom_info.max_v,
                    data.dicom_info.auto_scale
                );
            }

            this.setStatus("图像加载成功，等待分析", "#22C55E");
        } catch (error) {
            console.error(error);
            this.setStatus("网络请求异常", "#EF4444");
        }
    },

    async runXray() {
        if (!this.currentImageId) {
            this.setStatus("请先导入影像", "#EF4444");
            return;
        }

        this.setStatus("正在进行 X 光左右判断与关键点测算...", "#5E6AD2");
        const scale = parseFloat(this.el("scale-input")?.value || "1.5") || 1.5;

        try {
            const data = await this.fetchJson("/api/xray", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: this.currentImageId, scale }),
            });

            if (data.code !== 200) {
                this.setStatus(`测算失败: ${data.message}`, "#EF4444");
                return;
            }

            this.updateDisplay(
                data.main_img_b64,
                data.sub_img_b64,
                "X光测算结果",
                this.buildXrayInputLabel(data)
            );
            this.updateXrayMetrics(data.angles, data.slips);

            if (data.detected === false) {
                this.setStatus(data.message || "当前图像未检出可测量关键点", "#F59E0B");
            } else {
                this.setStatus(data.message || "测算完成", "#22C55E");
            }
        } catch (error) {
            console.error(error);
            this.setStatus("网络请求异常", "#EF4444");
        }
    },

    async runRgb() {
        if (!this.currentImageId) {
            this.setStatus("请先导入图像", "#EF4444");
            return;
        }

        this.setStatus("正在检测脊柱 ROI...", "#5E6AD2");

        try {
            const data = await this.fetchJson("/api/rgb", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: this.currentImageId }),
            });

            if (data.code !== 200) {
                this.setStatus(`检测失败: ${data.message}`, "#EF4444");
                return;
            }

            this.updateDisplay(data.main_img_b64, data.sub_img_b64, "RGB 检测结果", "原始图像");
            this.updateRGBMetrics(data.rgb_metrics);
            this.setStatus("检测完成", "#22C55E");
        } catch (error) {
            console.error(error);
            this.setStatus("网络请求异常", "#EF4444");
        }
    },

    async cropRgb() {
        if (!this.currentImageId) {
            this.setStatus("请先完成 RGB 检测", "#EF4444");
            return;
        }

        try {
            const data = await this.fetchJson("/api/crop", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: this.currentImageId }),
            });
            if (data.code !== 200) {
                this.setStatus("当前没有可裁剪的 ROI", "#EF4444");
                return;
            }

            this.updateDisplay(data.main_img_b64, data.sub_img_b64, "裁剪后图像", "检测结果");
            this.setStatus("裁剪成功", "#22C55E");
        } catch (error) {
            console.error(error);
            this.setStatus(error.message || "网络请求异常", "#EF4444");
        }
    },

    async updateWindowing() {
        if (!this.currentImageId) {
            return;
        }

        const wl = parseFloat(this.el("wl-slider")?.value || "0");
        const ww = parseFloat(this.el("ww-slider")?.value || "0");

        try {
            const data = await this.fetchJson("/api/windowing", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: this.currentImageId, wl, ww }),
            });

            if (data.code === 200) {
                this.updateDisplay(data.main_img_b64, null, "窗宽窗位调整结果", "");
            }
        } catch (error) {
            console.error(error);
        }
    },

    async exportPdf() {
        if (this.staticDemo) {
            this.setStatus("GitHub Pages 为静态展示版；PDF 报告请在本机完整版中生成", "#F59E0B");
            return;
        }
        if (!this.currentImageId || this.mode !== "xray") {
            this.setStatus("请先完成 X 光测算", "#EF4444");
            return;
        }

        const params = new URLSearchParams();
        params.set("audience", this.reportAudience);
        params.set("id", this.currentImageId);

        let label = this.getReportAudienceLabel(this.reportAudience);
        if (this.reportAudience === "personal" && this.reportAiEnabled && this.reportAiReady) {
            params.set("use_ai", "1");
            label = "个人 AI 增强报告";
        }

        this.setStatus(`正在生成${label}...`, "#5E6AD2");
        try {
            const response = await fetch(`/api/export_pdf?${params.toString()}`, {
                credentials: "same-origin",
            });
            if (!response.ok) {
                const contentType = response.headers.get("content-type") || "";
                const payload = contentType.includes("application/json") ? await response.json() : null;
                throw new Error(payload?.message || `报告生成失败（HTTP ${response.status}）`);
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            this.downloadUrl(url, `${label.replaceAll(" ", "_")}_${Date.now()}.pdf`);
            window.setTimeout(() => URL.revokeObjectURL(url), 30000);
            this.setStatus(`${label}已导出`, "#22C55E");
        } catch (error) {
            console.error(error);
            this.setStatus(error.message || "报告生成失败", "#EF4444");
        }
    },

    async fetchPseudoCTStatus(forceReload = false) {
        const query = new URLSearchParams({ mode: this.pseudoctInferenceMode }).toString();
        const url = forceReload ? `/api/pseudoct/reload?${query}` : `/api/pseudoct/status?${query}`;
        const method = forceReload ? "POST" : "GET";

        try {
            const data = await this.fetchJson(url, { method });
            if (data.code === 200) {
                this.updatePseudoCTService(data.service);
                if (forceReload) {
                    this.setStatus("X光图模型状态已刷新", "#22C55E");
                }
            }
        } catch (error) {
            console.error(error);
            this.updatePseudoCTService(null);
            this.setStatus("X光图服务不可用", "#EF4444");
        }
    },

    async runPseudoCTSingle(file) {
        if (!file) {
            return;
        }

        this.setStatus("正在生成X光图...", "#5E6AD2");

        const formData = new FormData();
        formData.append("file", file);
        formData.append("mode", this.pseudoctInferenceMode);

        try {
            const data = await this.fetchJson("/api/pseudoct/single", {
                method: "POST",
                body: formData,
            });

            if (data.code !== 200 || !data.item) {
                this.updatePseudoCTService(data.service || null);
                this.setStatus(`生成失败: ${data.message || "未知错误"}`, "#EF4444");
                return;
            }

            this.pseudoctItems = [data.item];
            this.selectedPseudoCTIndex = 0;
            this.updatePseudoCTService(data.service);
            this.renderPseudoCTBatchList();
            this.previewPseudoCTItem(0);
            this.setStatus("X光图生成完成", "#22C55E");
        } catch (error) {
            console.error(error);
            this.setStatus("X光图生成请求失败", "#EF4444");
        }
    },

    async runPseudoCTBatch(files) {
        if (!files || files.length === 0) {
            return;
        }

        this.setStatus(`正在批量生成X光图（${files.length} 张）...`, "#5E6AD2");

        const formData = new FormData();
        formData.append("mode", this.pseudoctInferenceMode);
        files.forEach((file) => formData.append("files", file, file.name));

        try {
            const data = await this.fetchJson("/api/pseudoct/batch", {
                method: "POST",
                body: formData,
            });

            if (data.code !== 200 || !Array.isArray(data.items) || data.items.length === 0) {
                this.updatePseudoCTService(data.service || null);
                this.setStatus(`批量生成失败: ${data.message || "未知错误"}`, "#EF4444");
                return;
            }

            this.pseudoctItems = data.items;
            this.selectedPseudoCTIndex = 0;
            this.updatePseudoCTService(data.service);
            this.renderPseudoCTBatchList();
            this.previewPseudoCTItem(0);

            const failed = data.summary ? data.summary.failed : 0;
            if (failed > 0) {
                this.setStatus(`批量完成：成功 ${data.summary.success}，失败 ${failed}`, "#F59E0B");
            } else {
                this.setStatus(`批量完成，共生成 ${data.items.length} 张`, "#22C55E");
            }
        } catch (error) {
            console.error(error);
            this.setStatus("批量生成请求失败", "#EF4444");
        }
    },

    previewPseudoCTItem(index) {
        if (index < 0 || index >= this.pseudoctItems.length) {
            return;
        }
        this.selectedPseudoCTIndex = index;
        this.previewPseudoCTCompare();
        this.renderPseudoCTBatchList();

        const item = this.pseudoctItems[index];
        this.setStatus(`当前预览：${item.filename}`, "#22C55E");
    },

    previewPseudoCTCompare() {
        const item = this.getSelectedPseudoCTItem();
        if (!item) {
            this.setStatus("请先选择一张X光图结果", "#EF4444");
            return;
        }

        this.updateDisplay(
            item.generated_url,
            this.getPseudoCTDisplayInputUrl(item),
            "X光图输出",
            this.getPseudoCTDisplayInputLabel(item)
        );
        this.updatePseudoCTMetrics(item);
    },

    previewPseudoCTInputOnly() {
        const item = this.getSelectedPseudoCTItem();
        if (!item) {
            this.setStatus("请先选择一张X光图结果", "#EF4444");
            return;
        }

        this.updateDisplay(this.getPseudoCTDisplayInputUrl(item), null, this.getPseudoCTDisplayInputLabel(item), "");
        this.updatePseudoCTMetrics(item);
        this.setStatus("已切换为输入图单独查看", "#8A8F98");
    },

    previewPseudoCTOutputOnly() {
        const item = this.getSelectedPseudoCTItem();
        if (!item) {
            this.setStatus("请先选择一张X光图结果", "#EF4444");
            return;
        }

        this.updateDisplay(item.generated_url, null, "X光图输出", "");
        this.updatePseudoCTMetrics(item);
        this.setStatus("已切换为X光图输出单独查看", "#8A8F98");
    },

    previewPseudoCTStrip() {
        const item = this.getSelectedPseudoCTItem();
        if (!item) {
            this.setStatus("请先选择一张X光图结果", "#EF4444");
            return;
        }

        this.updateDisplay(item.preview_url, item.generated_url, "流程预览", "X光图输出");
        this.updatePseudoCTMetrics(item);
        this.setStatus("已切换为流程拼接预览", "#8A8F98");
    },

    savePseudoCTInput() {
        const item = this.getSelectedPseudoCTItem();
        if (!item) {
            this.setStatus("请先选择一张X光图结果", "#EF4444");
            return;
        }
        this.downloadUrl(this.getPseudoCTDisplayInputUrl(item), this.buildDownloadName(item.filename, "xray_input"));
        this.setStatus("输入图已开始下载", "#22C55E");
    },

    savePseudoCTOutput() {
        const item = this.getSelectedPseudoCTItem();
        if (!item) {
            this.setStatus("请先选择一张X光图结果", "#EF4444");
            return;
        }
        this.downloadUrl(item.generated_url, this.buildDownloadName(item.filename, "xray_output"));
        this.setStatus("X光图输出已开始下载", "#22C55E");
    },

    async loopPseudoCTToXray() {
        const item = this.getSelectedPseudoCTItem();
        if (!item) {
            this.setStatus("请先选择一张X光图结果", "#EF4444");
            return;
        }

        const scale = parseFloat(this.el("scale-input")?.value || "1.5") || 1.5;
        this.setStatus("正在将当前X光图导入 X 光检测闭环...", "#5E6AD2");

        try {
            const data = await this.fetchJson("/api/pseudoct/loop_to_xray", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    generated_url: item.generated_url,
                    scale,
                }),
            });

            if (data.code !== 200) {
                this.setStatus(`闭环分析失败: ${data.message || "未知错误"}`, "#EF4444");
                return;
            }

            this.setMode("xray", { preservePseudoCT: true });
            this.currentImageId = data.image_id || `pseudoct_loop_${Date.now()}`;

            const scaleInput = this.el("scale-input");
            if (scaleInput && data.scale) {
                scaleInput.value = Number(data.scale).toFixed(4);
            }

            this.updateDisplay(
                data.main_img_b64,
                data.sub_img_b64,
                "X光测算结果",
                this.buildXrayInputLabel(data)
            );
            this.updateXrayMetrics(data.angles, data.slips);

            if (data.detected === false) {
                this.setStatus(data.message || "闭环图像未检出可测量关键点", "#F59E0B");
            } else {
                this.setStatus(data.message || "已完成X光图检测闭环", "#22C55E");
            }
        } catch (error) {
            console.error(error);
            this.setStatus("闭环分析请求失败", "#EF4444");
        }
    },

    clearPseudoCTResults(keepService = false) {
        this.pseudoctItems = [];
        this.selectedPseudoCTIndex = -1;
        this.updatePseudoCTMetrics(null);
        this.renderPseudoCTBatchList();

        if (!keepService) {
            this.updatePseudoCTService(null);
        }
    },

    clear() {
        this.stopBatchPolling("xray");
        this.stopBatchPolling("rgb");
        this.currentImageId = null;
        this.xrayBatchJob = null;
        this.rgbBatchJob = null;
        this.xrayBatchFiles = [];
        this.rgbBatchFiles = [];
        this.xrayBatchInputFolderName = "";
        this.rgbBatchInputFolderName = "";
        this.selectedXrayBatchIndex = -1;
        this.selectedRgbBatchIndex = -1;
        this.progressDismissed = false;
        this.mirrorX = 1;
        this.updateDisplay(null, null);
        this.updateXrayMetrics(null, null);
        this.updateRGBMetrics(null);
        this.clearPseudoCTResults(true);
        this.renderXrayBatchList();
        this.renderRGBBatchList();
        this.updateXrayBatchFolderUI();
        this.updateRgbBatchFolderUI();
        this.renderBatchProgress(null, null);

        const dicomPanel = this.el("dicom-settings");
        if (dicomPanel) {
            dicomPanel.classList.add("hidden");
            dicomPanel.classList.remove("flex");
        }

        if (window.resetPanZoom) {
            window.resetPanZoom();
        }

        this.setStatus("工作区已清空", "#8A8F98");
    },

    saveImage() {
        const mainImg = this.el("main-image");
        const mainCard = this.el("main-image-card");
        if (!mainImg || !mainCard || !mainImg.src || mainCard.classList.contains("hidden")) {
            this.setStatus("当前没有可保存的图像", "#EF4444");
            return;
        }
        this.downloadUrl(mainImg.src, `MindSpine_${Date.now()}.png`);
        this.setStatus("当前主视图已开始下载", "#22C55E");
    },

    downloadUrl(url, filename) {
        const link = document.createElement("a");
        link.href = url;
        link.download = filename || `MindSpine_${Date.now()}.png`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    },

    updateDisplay(mainSrc, subSrc, mainLabel = "", subLabel = "") {
        const mainImg = this.el("main-image");
        const subImg = this.el("sub-image");
        const mainCard = this.el("main-image-card");
        const subCard = this.el("sub-image-card");
        const mainLabelEl = this.el("main-image-label");
        const subLabelEl = this.el("sub-image-label");
        const placeholder = this.el("placeholder-text");

        if (mainImg && mainCard) {
            if (mainSrc) {
                mainImg.src = mainSrc;
                mainCard.classList.remove("hidden");
            } else {
                mainImg.src = "";
                mainCard.classList.add("hidden");
            }
        }

        if (subImg && subCard) {
            if (subSrc) {
                subImg.src = subSrc;
                subCard.classList.remove("hidden");
            } else {
                subImg.src = "";
                subCard.classList.add("hidden");
            }
        }

        if (placeholder) {
            if (mainSrc || subSrc) {
                placeholder.classList.add("hidden");
            } else {
                placeholder.classList.remove("hidden");
            }
        }

        if (mainLabelEl) {
            if (mainLabel) {
                mainLabelEl.innerText = mainLabel;
                mainLabelEl.classList.remove("hidden");
            } else {
                mainLabelEl.innerText = "";
                mainLabelEl.classList.add("hidden");
            }
        }

        if (subLabelEl) {
            if (subLabel && subSrc) {
                subLabelEl.innerText = subLabel;
                subLabelEl.classList.remove("hidden");
            } else {
                subLabelEl.innerText = "";
                subLabelEl.classList.add("hidden");
            }
        }
    },

    updateDICOMUI(wl, ww, minV, maxV, autoScale) {
        if (this.mode !== "xray") {
            return;
        }

        const panel = this.el("dicom-settings");
        if (!panel) {
            return;
        }

        panel.classList.remove("hidden");
        panel.classList.add("flex");

        const scaleInput = this.el("scale-input");
        const wlSlider = this.el("wl-slider");
        const wwSlider = this.el("ww-slider");
        const wlVal = this.el("wl-val");
        const wwVal = this.el("ww-val");

        if (scaleInput) {
            scaleInput.value = Number(autoScale || 1.5).toFixed(4);
        }

        if (wlSlider) {
            wlSlider.min = minV;
            wlSlider.max = maxV;
            wlSlider.value = wl;
        }
        if (wwSlider) {
            wwSlider.min = 1;
            wwSlider.max = Math.max(maxV - minV, 1) * 2;
            wwSlider.value = ww;
        }
        if (wlVal) {
            wlVal.innerText = Math.round(wl);
        }
        if (wwVal) {
            wwVal.innerText = Math.round(ww);
        }
    },

    updateXrayMetrics(angles, slipList) {
        const valLL = this.el("val-ll");
        const valSSA = this.el("val-ss");
        const valHarrisonA = this.el("val-lca");
        const valLCI = this.el("val-lci");
        const valLLNear = this.el("val-ll-near");
        const valLLFar = this.el("val-ll-far");
        const slipContainer = this.el("slip-container");

        if (!valLL || !valSSA || !valHarrisonA || !valLCI || !valLLNear || !valLLFar || !slipContainer) {
            return;
        }

        if (!angles) {
            valLL.innerHTML = `--<span class="text-[12px] ml-0.5 opacity-70">°</span>`;
            valSSA.innerHTML = `--<span class="text-[12px] ml-0.5 opacity-70">°</span>`;
            valHarrisonA.innerHTML = `--<span class="text-[12px] ml-0.5 opacity-70">°</span>`;
            valLCI.innerHTML = `--<span class="text-[11px] ml-0.5 opacity-70">mm</span>`;
            valLLNear.innerHTML = `--<span class="text-[11px] ml-0.5 opacity-70">°</span>`;
            valLLFar.innerHTML = `--<span class="text-[11px] ml-0.5 opacity-70">°</span>`;
            slipContainer.innerHTML = `<p class="opacity-50 uppercase tracking-widest text-[10px] text-center mt-1">等待扫描...</p>`;
            return;
        }

        const applyValue = (element, value, unit = "°") => {
            if (value !== null && value !== undefined) {
                element.innerHTML = `${value}<span class="text-[12px] ml-0.5 opacity-70">${unit}</span>`;
            } else if (unit === "mm") {
                element.innerHTML = `--<span class="text-[11px] ml-0.5 opacity-70">${unit}</span>`;
            } else {
                element.innerHTML = `--<span class="text-[12px] ml-0.5 opacity-70">${unit}</span>`;
            }
        };

        applyValue(valLL, angles.LL, "°");
        applyValue(valSSA, angles.SSA ?? angles.SS, "°");
        applyValue(valHarrisonA, angles.HARRISON_A ?? angles.LCA, "°");
        applyValue(valLCI, angles.LCI, "mm");
        applyValue(valLLNear, angles.LL_NEAR, "°");
        applyValue(valLLFar, angles.LL_FAR, "°");

        if (!slipList || slipList.length === 0) {
            slipContainer.innerHTML = `<p class="text-[#22C55E] tracking-wider text-[11px] mt-2 text-center">未见明显滑脱异常</p>`;
            return;
        }

        let html = "";
        slipList.forEach((item) => {
            const badge = item.is_ant
                ? `<span class="px-2 py-0.5 rounded text-[10px] bg-[#EF4444]/10 text-[#EF4444] border border-[#EF4444]/40">前滑脱</span>`
                : item.is_retro
                    ? `<span class="px-2 py-0.5 rounded text-[10px] bg-[#EF4444]/10 text-[#EF4444] border border-[#EF4444]/40">后滑脱</span>`
                    : `<span class="px-2 py-0.5 rounded text-[10px] bg-[#22C55E]/10 text-[#22C55E] border border-[#22C55E]/40">正常</span>`;

            const sign = item.distance > 0 ? "+" : "";
            html += `
                <div class="flex justify-between items-center pb-2 mb-2 border-b border-white/5 last:border-0 last:mb-0 last:pb-0">
                    <span class="text-white text-[12px]">节段 [${item.segment}]</span>
                    <span class="text-white text-[12px] font-mono">${sign}${item.distance} mm</span>
                    ${badge}
                </div>
            `;
        });

        slipContainer.innerHTML = html;
    },

    updateRGBMetrics(data) {
        const container = this.el("rgb-result-container");
        if (!container) {
            return;
        }

        if (!data) {
            container.innerHTML = `<p class="text-[#EF4444] text-[11px] text-center mt-1 font-medium tracking-widest">未检测到目标</p>`;
            return;
        }

        container.innerHTML = `
            <div class="flex justify-between items-center border-b border-white/5 pb-2">
                <span class="text-[11px] text-[#8A8F98] uppercase tracking-widest">置信度</span>
                <span class="text-[#22C55E] font-bold text-[15px]">${(Number(data.conf || 0) * 100).toFixed(1)}%</span>
            </div>
            <div class="flex justify-between items-start mt-2 gap-3">
                <span class="text-[11px] text-[#8A8F98] uppercase tracking-widest">坐标</span>
                <span class="text-white font-mono text-[10px] opacity-80 break-all">[${(data.box || []).join(", ")}]</span>
            </div>
        `;
    },

    updatePseudoCTService(service) {
        const statusEl = this.el("pseudoct-service-status");
        const modeEl = this.el("pseudoct-active-mode");
        const checkpointEl = this.el("pseudoct-checkpoint");
        const detectorEl = this.el("pseudoct-detector");
        const noteEl = this.el("pseudoct-service-note");

        if (!statusEl || !modeEl || !checkpointEl || !detectorEl || !noteEl) {
            return;
        }

        if (!service) {
            statusEl.innerText = "--";
            statusEl.className = "text-[12px] font-semibold text-white text-right";
            modeEl.innerText = this.getPseudoCTModeLabel(this.pseudoctInferenceMode);
            checkpointEl.innerText = "--";
            detectorEl.innerText = "--";
            noteEl.innerText = "等待后端服务...";
            return;
        }

        statusEl.innerText = service.ready ? "已就绪" : "未就绪";
        statusEl.className = service.ready
            ? "text-[12px] font-semibold text-[#22C55E] text-right"
            : "text-[12px] font-semibold text-[#EF4444] text-right";

        modeEl.innerText = service.mode_label || this.getPseudoCTModeLabel(this.pseudoctInferenceMode);
        checkpointEl.innerText = service.checkpoint_name || "--";
        detectorEl.innerText = service.detector || "--";
        noteEl.innerText = service.status || "--";
    },

    updatePseudoCTMetrics(item) {
        const nameEl = this.el("pseudoct-current-name");
        const modeEl = this.el("pseudoct-current-mode");
        const bboxLabelEl = this.el("pseudoct-current-bbox-label");
        const bboxEl = this.el("pseudoct-current-bbox");
        const networkEl = this.el("pseudoct-current-network");
        const maskEl = this.el("pseudoct-current-mask");
        const sizeEl = this.el("pseudoct-current-size");
        const noteEl = this.el("pseudoct-current-note");

        if (!nameEl || !modeEl || !bboxLabelEl || !bboxEl || !networkEl || !maskEl || !sizeEl || !noteEl) {
            return;
        }

        if (!item) {
            nameEl.innerText = "--";
            modeEl.innerText = "--";
            bboxLabelEl.innerText = "ROI 置信度";
            bboxEl.innerText = "--";
            networkEl.innerText = "--";
            maskEl.innerText = "--";
            sizeEl.innerText = "--";
            noteEl.innerText = "--";
            return;
        }

        nameEl.innerText = item.filename || "--";
        modeEl.innerText = item.mode_label || this.getPseudoCTModeLabel(item.inference_mode);

        if (item.inference_mode === PSEUDOCT_MODE_SOURCE) {
            bboxLabelEl.innerText = "输入布局";
            bboxEl.innerText = this.getPseudoCTLayoutLabel(item.source_layout);
        } else {
            bboxLabelEl.innerText = "ROI 置信度";
            bboxEl.innerText = this.formatPercent(item.bbox_confidence);
        }

        networkEl.innerText = this.formatSize(item.network_input_size);
        maskEl.innerText = this.formatPercent(item.mask_fill_ratio);
        sizeEl.innerText = this.formatSize(item.image_size);
        noteEl.innerText = item.note || "--";
    },

    renderPseudoCTBatchList() {
        const list = this.el("pseudoct-batch-list");
        if (!list) {
            return;
        }

        if (!this.pseudoctItems || this.pseudoctItems.length === 0) {
            list.innerHTML = `<p class="opacity-50 uppercase tracking-widest text-[10px] text-center mt-1">等待导入...</p>`;
            return;
        }

        list.innerHTML = this.pseudoctItems.map((item, index) => {
            const active = index === this.selectedPseudoCTIndex;
            const activeClass = active
                ? "border-[#5E6AD2]/60 bg-white/[0.06] shadow-[0_0_18px_rgba(94,106,210,0.25)]"
                : "border-white/5 bg-white/[0.02] hover:bg-white/[0.05]";

            const secondLine = item.inference_mode === PSEUDOCT_MODE_SOURCE
                ? this.getPseudoCTLayoutLabel(item.source_layout)
                : `ROI ${this.formatPercent(item.bbox_confidence)}`;

            return `
                <button
                    type="button"
                    data-pseudoct-index="${index}"
                    class="w-full text-left rounded-xl border ${activeClass} transition-all p-3 mb-2 last:mb-0"
                >
                    <div class="flex gap-3">
                        <img
                            src="${item.generated_url}"
                            class="w-16 h-20 object-cover rounded-lg border border-white/10 bg-black/40"
                            alt="X光图预览"
                        >
                        <div class="min-w-0 flex-1">
                            <div class="text-[12px] text-white font-medium truncate">${this.escapeHtml(item.filename)}</div>
                            <div class="text-[10px] text-[#8A8F98] mt-1">${this.escapeHtml(item.mode_label || this.getPseudoCTModeLabel(item.inference_mode))}</div>
                            <div class="text-[10px] text-[#8A8F98] mt-1">${this.escapeHtml(secondLine)}</div>
                            <div class="text-[10px] text-[#8A8F98] mt-1">${this.escapeHtml(this.getPseudoCTDisplayInputLabel(item))}</div>
                        </div>
                    </div>
                </button>
            `;
        }).join("");
    },

    mirror() {
        if (!this.el("image-wrapper")) {
            return;
        }
        this.mirrorX *= -1;
        window.refreshPanZoomTransform?.();
        this.setStatus("当前视图已镜像", "#8A8F98");
    },

    swapImages() {
        const mainImg = this.el("main-image");
        const subImg = this.el("sub-image");
        const subCard = this.el("sub-image-card");
        const mainLabelEl = this.el("main-image-label");
        const subLabelEl = this.el("sub-image-label");

        if (!mainImg || !subImg || !subCard || !mainLabelEl || !subLabelEl) {
            return;
        }

        if (!subImg.src || subCard.classList.contains("hidden")) {
            return;
        }

        const tempSrc = mainImg.src;
        const tempLabel = mainLabelEl.innerText;

        mainImg.src = subImg.src;
        subImg.src = tempSrc;

        mainLabelEl.innerText = subLabelEl.innerText;
        subLabelEl.innerText = tempLabel;

        this.setStatus("主副视图已交换", "#8A8F98");
    },

    setStatus(message, colorHex = "#8A8F98") {
        const statusBar = this.el("status-bar");
        const dot = this.el("status-indicator");

        if (statusBar) {
            statusBar.innerText = message;
        }
        if (dot) {
            dot.style.backgroundColor = colorHex;
            dot.style.boxShadow = `0 0 12px ${colorHex}`;
        }
    },

    initPanZoom() {
        const container = this.el("image-container");
        const wrapper = this.el("image-wrapper");

        if (!container || !wrapper) {
            return;
        }

        let scale = 1;
        let panning = false;
        let pointX = 0;
        let pointY = 0;
        let startX = 0;
        let startY = 0;

        const setTransform = () => {
            wrapper.style.transform = `translate(${pointX}px, ${pointY}px) scale(${scale}) scaleX(${this.mirrorX})`;
        };

        window.refreshPanZoomTransform = setTransform;

        window.resetPanZoom = () => {
            scale = 1;
            pointX = 0;
            pointY = 0;
            setTransform();
        };

        container.addEventListener("mousedown", (event) => {
            event.preventDefault();
            panning = true;
            startX = event.clientX - pointX;
            startY = event.clientY - pointY;
        });

        window.addEventListener("mousemove", (event) => {
            if (!panning) {
                return;
            }
            pointX = event.clientX - startX;
            pointY = event.clientY - startY;
            setTransform();
        });

        window.addEventListener("mouseup", () => {
            panning = false;
        });
        container.addEventListener("mouseleave", () => {
            panning = false;
        });

        container.addEventListener("wheel", (event) => {
            event.preventDefault();
            const delta = event.wheelDelta ? event.wheelDelta : -event.deltaY;
            scale = delta > 0 ? scale * 1.1 : scale / 1.1;
            scale = Math.max(0.1, Math.min(scale, 10));
            setTransform();
        });

        container.addEventListener("dblclick", () => {
            window.resetPanZoom();
        });
    },

    initDragAndDrop() {
        const dragOverlay = this.el("drag-overlay");

        ["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
            document.addEventListener(eventName, (event) => event.preventDefault(), false);
        });

        document.addEventListener("dragenter", () => {
            if (!dragOverlay) {
                return;
            }
            dragOverlay.classList.remove("hidden");
            dragOverlay.classList.add("flex");
        });

        document.addEventListener("dragleave", () => {
            if (!dragOverlay) {
                return;
            }
            setTimeout(() => {
                dragOverlay.classList.add("hidden");
                dragOverlay.classList.remove("flex");
            }, 50);
        });

        document.addEventListener("drop", (event) => {
            if (dragOverlay) {
                dragOverlay.classList.add("hidden");
                dragOverlay.classList.remove("flex");
            }

            const files = Array.from(event.dataTransfer.files || []);
            if (!files.length) {
                return;
            }

            if (this.mode === "pseudoct") {
                const supportedFiles = this.filterSupportedFiles(files, false);
                if (!supportedFiles.length) {
                    this.setStatus("未找到支持的图片文件", "#EF4444");
                } else if (supportedFiles.length === 1) {
                    this.runPseudoCTSingle(supportedFiles[0]);
                } else {
                    this.runPseudoCTBatch(supportedFiles);
                }
                return;
            }

            if (this.mode === "xray") {
                if (files.length === 1) {
                    this.triggerUpload(files[0]);
                } else {
                    const supportedFiles = this.filterSupportedFiles(files, true);
                    if (supportedFiles.length) {
                        this.startXrayBatch(supportedFiles);
                    }
                }
                return;
            }

            if (this.mode === "rgb") {
                if (files.length === 1) {
                    this.triggerUpload(files[0]);
                } else {
                    const supportedFiles = this.filterSupportedFiles(files, false);
                    if (supportedFiles.length) {
                        this.startRgbBatch(supportedFiles);
                    }
                }
            }
        });
    },
};

document.addEventListener("DOMContentLoaded", () => {
    app.init();
});
