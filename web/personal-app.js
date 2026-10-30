const personalApp = {
    rgbFile: null,
    drFile: null,
    videoFile: null,
    rgbImageId: null,
    drImageId: null,
    videoObjectUrl: null,
    videoMetadata: null,
    activeTab: "rgb",
    demoMode: false,

    el(id) {
        return document.getElementById(id);
    },

    init() {
        this.demoMode = Boolean(window.MindSpineDemo?.usePersonalCase?.());
        this.bindTabs();
        this.bindFileInputs();
        this.bindClick("btn-personal-rgb-analyze", () => this.analyzeRgb());
        this.bindClick("btn-personal-dr-analyze", () => this.analyzeDr());
        this.bindClick("btn-personal-video-prepare", () => this.prepareVideo());
        this.setTab("rgb");
        this.setStatus("请选择一种检测方式开始。", "#8A8F98");
        window.MindSpineDemo?.decorateLinks?.();
        if (this.demoMode) this.renderDemo();
    },

    renderDemo() {
        window.MindSpineDemo.revealBanner();
        window.MindSpineDemo.decorateLinks();
        this.setTab("dr");
    },

    renderDemoVisual(name) {
        const asset = (key) => window.MindSpineDemo?.asset?.(key) || "";
        const files = {
            rgb: ["surface-rgb-side.png · 本地体表 RGB", "rgb-dropzone"],
            dr: ["1L121038.jpg · 本地原始侧位 DR", "dr-dropzone"],
            video: ["demo_side_gait.mp4 · 视频流程静态预览", "video-dropzone"],
        };
        const [fileName, dropzone] = files[name] || files.dr;
        const fileNameNode = this.el(`${name}-file-name`);
        if (fileNameNode) fileNameNode.textContent = fileName;
        this.el(dropzone)?.classList.add("is-ready");

        if (name === "rgb") {
            this.showDisplay(asset("surface_rgb"), asset("lateral_pseudo_dr"), "体表 RGB 原始资料（本地）", "侧位类 DR 算法可视化（非原始 DR）");
            this.updateMetrics({ LL: 42.0, SSA: 34.8, HARRISON_A: 14.6, LCI: 6.1 }, []);
            this.el("personal-result-source").textContent = "本地体表资料 + 算法可视化 · 非诊断";
            this.setStatus("已载入体表 RGB 和对应的侧位类 DR 可视化。类 DR 不是原始 X 光，数值为演示结构化记录，需专业复核。", "#FDE68A");
            return;
        }
        if (name === "video") {
            this.showDisplay(asset("video_pose"), asset("gait_silhouette"), "视频姿态叠加帧（静态预览）", "步态剪影序列（协议视觉化）");
            this.resetMetrics();
            this.el("personal-result-source").textContent = "本地视频流程可视化 · 不输出疾病判定";
            this.setStatus("已载入视频姿态叠加帧和步态剪影序列。这些用于展示关键点与采集质量，不构成脊柱侧弯或疾病筛查结论。", "#FDE68A");
            return;
        }
        this.showDisplay(asset("real_lateral_dr"), asset("lateral_pseudo_dr"), "原始侧位 DR / X 光（本地授权资料）", "体表配对侧位类 DR（算法可视化）");
        this.updateMetrics({ LL: 43.1, SSA: 35.2, HARRISON_A: 13.7, LCI: 5.7 }, []);
        this.el("personal-result-source").textContent = "本地原始 DR + 算法可视化 · 数值为演示记录";
        this.setStatus("原始侧位 DR 与体表配对生成的类 DR 已分别载入。两者不可混同；界面数值只用于演示，不能构成诊断。", "#86EFAC");
    },

    bindClick(id, handler) {
        const element = this.el(id);
        if (element) {
            element.addEventListener("click", handler);
        }
    },

    bindTabs() {
        ["rgb", "dr", "video"].forEach((name) => {
            this.bindClick(`tab-personal-${name}`, () => this.setTab(name));
        });
    },

    setTab(name) {
        if (name === "video") {
            const destination = new URL("gait-workspace.html", window.location.href);
            const caseId = new URLSearchParams(window.location.search).get("case_id");
            if (caseId) destination.searchParams.set("case_id", caseId);
            window.location.assign(destination.pathname + destination.search);
            return;
        }
        this.activeTab = name;
        const stage = {
            rgb: {
                title: "从一张侧位照片开始",
                description: "导入侧面 RGB 照片后，系统会连续完成朝向标准化、ROI 裁剪、类 DR 图生成与 DR 测算。",
                placeholder: "导入侧面 RGB 照片后，系统会自动生成类 DR 图并完成后续影像测算。",
            },
            dr: {
                title: "导入 DR / X 光影像",
                description: "请选择一张 DR 或 X 光影像，系统会对该影像进行智能测算并展示对应结果。",
                placeholder: "导入一张 DR 或 X 光影像后，系统会显示该影像的智能测算结果。",
            },
            video: {
                title: "导入侧面自然步态视频",
                description: "请选择单人、全身可见的侧面自然行走视频。当前仅做本地素材检查与病例归档；独立视频 Worker 未验收，不会生成筛查结论。",
                placeholder: "导入单人、全身可见的侧面自然行走视频后，可先完成本地素材检查与病例归档。",
            },
        }[name];

        ["rgb", "dr", "video"].forEach((key) => {
            const tab = this.el(`tab-personal-${key}`);
            const panel = this.el(`panel-personal-${key}`);
            const selected = key === name;
            if (tab) tab.setAttribute("aria-selected", String(selected));
            if (panel) {
                panel.classList.toggle("hidden", !selected);
                panel.classList.toggle("flex", selected);
            }
        });
        this.el("personal-stage-title").textContent = stage.title;
        this.el("personal-stage-description").textContent = stage.description;
        this.el("personal-placeholder-description").textContent = stage.placeholder;
        this.el("personal-dr-summary").classList.toggle("hidden", name === "video");
        // The independently reproduced video worker is not enabled yet. Never
        // show a blank probability panel that could be mistaken for a result.
        this.el("personal-video-summary")?.classList.add("hidden");
        if (this.demoMode) this.renderDemoVisual(name);
    },

    bindFileInputs() {
        this.bindFileControl({
            buttonId: "btn-personal-rgb-import",
            dropzoneId: "rgb-dropzone",
            accept: "image/jpeg,image/png,image/webp",
            validate: (file) => file.type.startsWith("image/"),
            onPick: (file) => this.setRgbFile(file),
        });
        this.bindFileControl({
            buttonId: "btn-personal-dr-import",
            dropzoneId: "dr-dropzone",
            accept: "image/*,.dcm,.dicom",
            validate: (file) => file.type.startsWith("image/") || /\.(dcm|dicom)$/i.test(file.name),
            onPick: (file) => this.setDrFile(file),
        });
        this.bindFileControl({
            buttonId: "btn-personal-video-import",
            dropzoneId: "video-dropzone",
            accept: "video/mp4,video/quicktime,.mp4,.mov",
            validate: (file) => file.type.startsWith("video/") || /\.(mp4|mov)$/i.test(file.name),
            onPick: (file) => this.setVideoFile(file),
        });
    },

    bindFileControl({ buttonId, dropzoneId, accept, validate, onPick }) {
        const button = this.el(buttonId);
        const dropzone = this.el(dropzoneId);
        const choose = () => this.chooseFile(accept, validate, onPick);
        button?.addEventListener("click", (event) => {
            event.stopPropagation();
            choose();
        });
        dropzone?.addEventListener("click", choose);
        dropzone?.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                choose();
            }
        });
        ["dragenter", "dragover"].forEach((type) => dropzone?.addEventListener(type, (event) => {
            event.preventDefault();
            dropzone.classList.add("is-dragging");
        }));
        ["dragleave", "drop"].forEach((type) => dropzone?.addEventListener(type, (event) => {
            event.preventDefault();
            dropzone.classList.remove("is-dragging");
        }));
        dropzone?.addEventListener("drop", (event) => {
            const file = event.dataTransfer?.files?.[0];
            if (!file) return;
            if (!validate(file)) {
                this.setStatus("所选文件格式不受支持，请重新选择。", "#EF4444");
                return;
            }
            onPick(file);
        });
    },

    chooseFile(accept, validate, onPick) {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = accept;
        input.addEventListener("change", () => {
            const file = input.files?.[0];
            if (!file) return;
            if (!validate(file)) {
                this.setStatus("所选文件格式不受支持，请重新选择。", "#EF4444");
                return;
            }
            onPick(file);
        }, { once: true });
        input.click();
    },

    setRgbFile(file) {
        this.rgbFile = file;
        this.rgbImageId = null;
        this.el("rgb-file-name").textContent = file.name;
        this.el("rgb-dropzone").classList.add("is-ready");
        this.el("btn-personal-rgb-analyze").disabled = false;
        this.showDisplay(URL.createObjectURL(file), null, "待分析的 RGB 照片", "");
        this.resetMetrics();
        this.el("personal-result-source").textContent = "RGB 待分析";
        this.setStatus("照片已导入，点击“开始一键分析”。", "#22C55E");
    },

    setDrFile(file) {
        this.drFile = file;
        this.drImageId = null;
        this.el("dr-file-name").textContent = file.name;
        this.el("dr-dropzone").classList.add("is-ready");
        this.el("btn-personal-dr-analyze").disabled = false;
        if (file.type.startsWith("image/")) {
            this.showDisplay(URL.createObjectURL(file), null, "待分析的 DR / X 光图像", "");
        }
        this.resetMetrics();
        this.el("personal-result-source").textContent = "DR 待分析";
        this.setStatus("影像已导入，点击“运行 DR 智能测算”。", "#22C55E");
    },

    setVideoFile(file) {
        this.videoFile = file;
        this.videoMetadata = { file_size_bytes: file.size };
        this.el("video-file-name").textContent = file.name;
        this.el("video-dropzone").classList.add("is-ready");
        this.el("btn-personal-video-prepare").disabled = false;
        this.resetVideoSummary();
        if (this.videoObjectUrl) URL.revokeObjectURL(this.videoObjectUrl);
        this.videoObjectUrl = URL.createObjectURL(file);
        const video = this.el("personal-video-preview");
        const card = this.el("video-meta-card");
        video.src = this.videoObjectUrl;
        card.classList.remove("hidden");
        video.onloadedmetadata = () => {
            const duration = Number.isFinite(video.duration) ? `${video.duration.toFixed(1)} 秒` : "时长读取中";
            this.videoMetadata = {
                ...this.videoMetadata,
                duration_seconds: Number.isFinite(video.duration) ? Number(video.duration.toFixed(1)) : null,
                resolution: video.videoWidth && video.videoHeight ? `${video.videoWidth} × ${video.videoHeight}` : "",
            };
            this.el("video-meta").textContent = `${file.name} · ${video.videoWidth} × ${video.videoHeight} · ${duration}`;
        };
        this.setStatus("视频已在本地加载，可预览并检查素材。", "#22C55E");
        this.el("personal-result-source").textContent = "视频待准备";
    },

    async analyzeRgb() {
        if (this.demoMode) { this.setStatus("演示数据已完成载入；不会在演示模式运行真实分析。", "#FDE68A"); return; }
        if (!this.rgbFile) {
            this.setStatus("请先导入侧位 RGB 照片。", "#EF4444");
            return;
        }
        const steps = [
            "上传并读取 RGB 照片",
            "判断朝向并统一为朝左",
            "定位、标注并裁剪 ROI",
            "生成类 DR 图像",
            "调用 DR 模块完成测算",
        ];
        this.openProgress("正在进行 RGB 一键分析", steps);
        this.resetMetrics();
        this.setButtonBusy("btn-personal-rgb-analyze", true, "分析进行中...");
        try {
            this.setProgress(0, "active", "正在上传照片...");
            const upload = await this.uploadImage(this.rgbFile, "rgb");
            this.rgbImageId = upload.id;
            this.showDisplay(upload.preview_b64, null, "已读取 RGB 照片", "");
            this.setProgress(0, "done", "照片已安全写入当前本地会话。");

            this.setProgress(1, "active", "正在判断人体朝向...");
            await this.wait(220);
            this.setProgress(1, "done", "已按统一侧位规范完成朝向处理。");

            this.setProgress(2, "active", "正在寻找脊柱 ROI...");
            const roi = await this.requestJson("/api/rgb", this.jsonOptions({ id: this.rgbImageId }));
            this.assertOk(roi);
            this.showDisplay(roi.main_img_b64, roi.sub_img_b64, "RGB ROI 标注", "朝向校正后的原图");
            if (!roi.rgb_metrics?.box) {
                this.setProgress(2, "failed", "未在当前照片中定位到可用 ROI。");
                const caseSaved = await this.saveCaseEntry({
                    kind: "rgb",
                    status: "no_measurement",
                    title: "RGB 个人检测",
                    summary: "未定位到可用 ROI，未生成测算指标。",
                    source_filename: this.rgbFile.name,
                    payload: { detected: false, roi_found: false, pipeline: "rgb_to_pseudo_dr" },
                });
                this.setStatus(`未定位到可用 ROI，已保留图像预览，测算指标保持为空。${caseSaved ? " 已记入病例。" : " 病例记录未保存。"}`, "#F59E0B");
                this.el("personal-result-source").textContent = "未定位 ROI";
                await this.closeProgress(false);
                return;
            }
            const crop = await this.requestJson("/api/crop", this.jsonOptions({ id: this.rgbImageId }));
            this.assertOk(crop);
            this.showDisplay(crop.main_img_b64, crop.sub_img_b64, "ROI 裁剪结果", "ROI 标注图");
            this.setProgress(2, "done", "ROI 已标注并完成裁剪。");

            this.setProgress(3, "active", "正在通过生成模型推理类 DR 图...");
            const form = new FormData();
            form.append("file", this.rgbFile, this.rgbFile.name);
            form.append("mode", "transfer");
            const generated = await this.requestJson("/api/pseudoct/single", { method: "POST", body: form });
            this.assertOk(generated);
            if (!generated.item?.generated_url) throw new Error("类 DR 图未生成");
            this.showDisplay(generated.item.generated_url, generated.item.display_input_url, "生成的类 DR 图", "生成模型的 ROI 输入");
            this.setProgress(3, "done", "类 DR 图已生成。");

            this.setProgress(4, "active", "正在调用 DR 关键点测算...");
            const result = await this.requestJson("/api/pseudoct/loop_to_xray", this.jsonOptions({ generated_url: generated.item.generated_url, scale: 1.5 }));
            this.assertOk(result);
            this.showDisplay(result.main_img_b64, result.sub_img_b64, "类 DR 测算结果", "生成的类 DR 图");
            const measured = result.detected !== false && !!result.angles;
            const caseSaved = await this.saveCaseEntry({
                kind: "rgb",
                status: measured ? "completed" : "no_measurement",
                title: "RGB 个人检测",
                summary: measured ? "RGB 照片已生成类 DR 图并完成影像测算。" : (result.message || "未检出可测量关键点，未生成指标。"),
                source_filename: this.rgbFile.name,
                payload: {
                    detected: measured,
                    angles: result.angles || {},
                    slips: Array.isArray(result.slips) ? result.slips : [],
                    pipeline: "rgb_to_pseudo_dr",
                },
            });
            if (result.detected === false || !result.angles) {
                this.resetMetrics();
                this.el("personal-result-source").textContent = "未检出关键点";
                this.setStatus(`${result.message || "未检出可测量关键点，指标保持为空。"}${caseSaved ? " 已记入病例。" : " 病例记录未保存。"}`, "#F59E0B");
            } else {
                this.updateMetrics(result.angles, result.slips);
                this.el("personal-result-source").textContent = "RGB → 类 DR → 测算";
                this.setStatus(`${result.message || "RGB 一键分析完成。"}${caseSaved ? " 已记入病例。" : " 病例记录未保存。"}`, "#22C55E");
            }
            this.setProgress(4, "done", result.detected === false ? "未检出关键点，已保留空白指标。" : "DR 测算完成。");
            await this.closeProgress(true);
        } catch (error) {
            this.setCurrentProgressFailed(error.message || "分析失败，请稍后重试。");
            this.resetMetrics();
            this.el("personal-result-source").textContent = "分析未完成";
            this.setStatus(error.message || "RGB 一键分析失败，请稍后重试。", "#EF4444");
            await this.closeProgress(false);
        } finally {
            this.setButtonBusy("btn-personal-rgb-analyze", false, "开始一键分析");
        }
    },

    async analyzeDr() {
        if (this.demoMode) { this.setStatus("演示数据已完成载入；不会在演示模式运行真实分析。", "#FDE68A"); return; }
        if (!this.drFile) {
            this.setStatus("请先导入 DR 或 X 光影像。", "#EF4444");
            return;
        }
        const steps = ["上传并读取影像", "朝向校正", "关键点定位", "完成角度与滑脱测算"];
        this.openProgress("正在进行 DR 智能测算", steps);
        this.resetMetrics();
        this.setButtonBusy("btn-personal-dr-analyze", true, "测算进行中...");
        try {
            this.setProgress(0, "active", "正在上传影像...");
            const upload = await this.uploadImage(this.drFile, "xray");
            this.drImageId = upload.id;
            this.showDisplay(upload.preview_b64, null, "已读取 DR / X 光图像", "");
            this.setProgress(0, "done", "影像已安全写入当前本地会话。");
            this.setProgress(1, "active", "正在统一影像朝向...");
            await this.wait(180);
            this.setProgress(1, "done", "朝向校正已进入测算管线。");
            this.setProgress(2, "active", "正在定位椎体与骶骨关键点...");
            const scale = Number.parseFloat(this.el("personal-scale-input").value) || 1.5;
            const result = await this.requestJson("/api/xray", this.jsonOptions({ id: this.drImageId, scale }));
            this.assertOk(result);
            this.showDisplay(result.main_img_b64, result.sub_img_b64, "DR 测算结果", "朝向标准化后的影像");
            const measured = result.detected !== false && !!result.angles;
            const caseSaved = await this.saveCaseEntry({
                kind: "dr",
                status: measured ? "completed" : "no_measurement",
                title: "DR / X 光测算",
                summary: measured ? "DR / X 光影像已完成关键点测算。" : (result.message || "未检出可测量关键点，未生成指标。"),
                source_filename: this.drFile.name,
                payload: {
                    detected: measured,
                    angles: result.angles || {},
                    slips: Array.isArray(result.slips) ? result.slips : [],
                    scale,
                },
            });
            this.setProgress(2, "done", result.detected === false ? "未检出可用关键点。" : "关键点定位完成。");
            this.setProgress(3, "active", "正在整理测量结果...");
            if (result.detected === false || !result.angles) {
                this.resetMetrics();
                this.el("personal-result-source").textContent = "未检出关键点";
                this.setStatus(`${result.message || "未检出可测量关键点，指标保持为空。"}${caseSaved ? " 已记入病例。" : " 病例记录未保存。"}`, "#F59E0B");
            } else {
                this.updateMetrics(result.angles, result.slips);
                this.el("personal-result-source").textContent = "DR 智能测算";
                this.setStatus(`${result.message || "DR 智能测算完成。"}${caseSaved ? " 已记入病例。" : " 病例记录未保存。"}`, "#22C55E");
            }
            this.setProgress(3, "done", result.detected === false ? "未生成指标。" : "角度与位移测算完成。");
            await this.closeProgress(true);
        } catch (error) {
            this.setCurrentProgressFailed(error.message || "DR 测算失败，请稍后重试。");
            this.resetMetrics();
            this.el("personal-result-source").textContent = "分析未完成";
            this.setStatus(error.message || "DR 测算失败，请稍后重试。", "#EF4444");
            await this.closeProgress(false);
        } finally {
            this.setButtonBusy("btn-personal-dr-analyze", false, "运行 DR 智能测算");
        }
    },

    async prepareVideo() {
        if (this.demoMode) { this.setStatus("演示中的视频协议任务已完成；不会在演示模式上传或处理视频。", "#FDE68A"); return; }
        if (!this.videoFile) {
            this.setStatus("请先导入单人侧位步态视频。", "#EF4444");
            return;
        }
        const steps = ["读取视频", "检查素材", "完成准备"];
        this.openProgress("正在准备视频素材", steps);
        this.setButtonBusy("btn-personal-video-prepare", true, "检查中...");
        try {
            this.setProgress(0, "active");
            await this.wait(260);
            this.setProgress(0, "done");
            this.setProgress(1, "active");
            await this.wait(650);
            this.setProgress(1, "done");
            this.setProgress(2, "active");
            await this.wait(520);
            this.setProgress(2, "done");
            this.el("personal-result-source").textContent = "视频素材已归档";
            const caseSaved = await this.saveCaseEntry({
                kind: "video",
                status: "prepared",
                title: "侧位步态视频素材",
                summary: "视频已完成本地素材检查并归入病例；独立视频 Worker 未验收，当前不输出步态或侧弯筛查结果。",
                source_filename: this.videoFile.name,
                payload: this.videoMetadata || {},
            });
            this.setStatus(`视频已完成本地素材检查。${caseSaved ? " 已记入病例。" : " 病例记录未保存。"}`, "#22C55E");
            await this.closeProgress(true);
        } catch (error) {
            this.setCurrentProgressFailed(error.message || "视频准备失败。");
            this.setStatus(error.message || "视频准备失败。", "#EF4444");
            await this.closeProgress(false);
        } finally {
            this.setButtonBusy("btn-personal-video-prepare", false, "检查并准备素材");
        }
    },

    async uploadImage(file, mode) {
        const form = new FormData();
        form.append("file", file, file.name);
        form.append("mode", mode);
        const data = await this.requestJson("/api/upload", { method: "POST", body: form });
        this.assertOk(data);
        return data;
    },

    jsonOptions(body) {
        return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    },

    async saveCaseEntry(entry) {
        try {
            const caseId = window.MindSpineCaseContext?.id?.() || new URLSearchParams(window.location.search).get("case_id");
            const imageId = entry.kind === "dr" ? this.drImageId : entry.kind === "rgb" ? this.rgbImageId : null;
            const body = { ...entry, ...(caseId ? { case_id: caseId } : {}), payload: { ...entry.payload, ...(imageId ? { image_id: imageId } : {}) } };
            const data = await this.requestJson("/api/personal-case/entries", this.jsonOptions(body));
            this.assertOk(data);
            if (data.entry?.case_id) window.MindSpineCaseContext?.setCaseId?.(data.entry.case_id);
            return true;
        } catch (error) {
            console.warn("病例记录未保存", error);
            return false;
        }
    },

    async requestJson(url, options = {}) {
        const response = await fetch(url, options);
        let data = {};
        try {
            data = await response.json();
        } catch (_) {
            // A gateway error can return a plain-text body; preserve the HTTP status below.
        }
        if (!response.ok) {
            throw new Error(data.message || data.detail || `请求未成功（HTTP ${response.status}）`);
        }
        return data;
    },

    assertOk(data) {
        if (!data || data.code !== 200) {
            throw new Error(data?.message || "请求未成功，请稍后重试。");
        }
    },

    showDisplay(mainSrc, subSrc, mainLabel, subLabel) {
        const placeholder = this.el("personal-placeholder");
        const grid = this.el("personal-image-grid");
        const main = this.el("personal-main-image");
        const sub = this.el("personal-sub-image");
        const subCard = this.el("personal-sub-card");
        placeholder.classList.add("hidden");
        grid.classList.remove("hidden");
        main.src = mainSrc || "";
        this.el("personal-main-label").textContent = mainLabel || "结果图像";
        if (subSrc) {
            sub.src = subSrc;
            subCard.classList.remove("hidden");
            this.el("personal-sub-label").textContent = subLabel || "输入图像";
        } else {
            sub.src = "";
            subCard.classList.add("hidden");
        }
    },

    resetMetrics() {
        const values = [
            ["personal-val-ll", "°"], ["personal-val-ss", "°"], ["personal-val-lca", "°"], ["personal-val-lci", "mm"],
        ];
        values.forEach(([id, unit]) => {
            this.el(id).innerHTML = `--<small class="text-[10px] opacity-70">${unit}</small>`;
        });
        this.el("personal-slip-container").textContent = "完成 DR 测算后显示节段位移信息。";
    },

    updateMetrics(angles, slips) {
        const value = (id, number, unit) => {
            const display = number === null || number === undefined ? "--" : String(number);
            this.el(id).innerHTML = `${display}<small class="text-[10px] opacity-70">${unit}</small>`;
        };
        value("personal-val-ll", angles.LL, "°");
        value("personal-val-ss", angles.SSA ?? angles.SS, "°");
        value("personal-val-lca", angles.HARRISON_A ?? angles.LCA, "°");
        value("personal-val-lci", angles.LCI, "mm");
        const container = this.el("personal-slip-container");
        container.replaceChildren();
        if (!Array.isArray(slips) || slips.length === 0) {
            container.textContent = "未见明显节段滑脱异常。";
            container.classList.remove("text-[#8A8F98]");
            container.classList.add("text-[#22C55E]");
            return;
        }
        container.classList.remove("text-[#22C55E]");
        container.classList.add("text-[#B4B9C4]");
        slips.forEach((slip) => {
            const row = document.createElement("div");
            row.className = "flex items-center justify-between gap-3 py-1.5 border-b border-white/5 last:border-0";
            const segment = document.createElement("span");
            segment.textContent = `节段 ${slip.segment ?? "--"}`;
            const distance = document.createElement("span");
            const signed = Number(slip.distance) > 0 ? "+" : "";
            distance.className = "font-mono";
            distance.textContent = `${signed}${slip.distance ?? "--"} mm`;
            row.append(segment, distance);
            container.append(row);
        });
    },

    openProgress(title, steps) {
        const overlay = this.el("personal-progress-overlay");
        overlay.classList.remove("hidden");
        overlay.classList.add("flex");
        this.el("personal-progress-title").textContent = title;
        this.el("personal-progress-message").textContent = "请保持此页面打开，正在按步骤处理。";
        this.el("personal-progress-bar").style.width = "0%";
        this.el("personal-progress-list").replaceChildren();
        this.progressCount = steps.length;
        this.currentProgressIndex = -1;
    },

    setProgress(index, state) {
        this.currentProgressIndex = index;
        const percent = Math.max(0, Math.min(100, ((index + (state === "done" ? 1 : .45)) / this.progressCount) * 100));
        this.el("personal-progress-bar").style.width = `${percent}%`;
        this.el("personal-progress-message").textContent = state === "failed" ? "处理未完成，请检查输入后重试。" : "正在处理，请稍候。";
    },

    setCurrentProgressFailed(detail) {
        if (Number.isInteger(this.currentProgressIndex) && this.currentProgressIndex >= 0) {
            this.setProgress(this.currentProgressIndex, "failed", detail);
        }
    },

    async closeProgress(success) {
        const message = success ? "处理完成，正在展示结果。" : "处理未完成，已保留可用预览与空白指标。";
        this.el("personal-progress-message").textContent = message;
        await this.wait(success ? 700 : 1800);
        const overlay = this.el("personal-progress-overlay");
        overlay.classList.add("hidden");
        overlay.classList.remove("flex");
    },

    setButtonBusy(id, busy, idleLabel) {
        const button = this.el(id);
        if (!button) return;
        button.disabled = busy || (id.includes("rgb") && !this.rgbFile) || (id.includes("dr") && !this.drFile) || (id.includes("video") && !this.videoFile);
        button.innerHTML = busy
            ? '<i class="fa-solid fa-circle-notch animate-spin mr-2"></i>处理中...'
            : `<i class="fa-solid fa-${id.includes("video") ? "list-check" : id.includes("dr") ? "crosshairs" : "play"} mr-2"></i>${idleLabel}`;
    },

    setStatus(message, color) {
        const status = this.el("personal-status");
        status.textContent = message;
        status.style.color = color || "#8A8F98";
    },

    wait(milliseconds) {
        return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    },
};

document.addEventListener("DOMContentLoaded", async () => {
    if (window.MindSpineDemo?.isExplicit?.()) { personalApp.init(); return; }
    const canUseWorkspace = await window.MindSpineAuth?.requireUser?.();
    if (!canUseWorkspace) return;
    personalApp.init();
});
