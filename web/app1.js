// web/app.js

// ==========================================
// 1. 显式暴露给 Python Eel 调用的前端接口
// ==========================================
eel.expose(updateDisplay);
function updateDisplay(mainB64, subB64) { app.updateDisplay(mainB64, subB64); }

eel.expose(updateMetrics);
function updateMetrics(angles, slipList) { app.updateMetrics(angles, slipList); }

eel.expose(updateRGBMetrics);
function updateRGBMetrics(data) { app.updateRGBMetrics(data); }

eel.expose(updateScaleInput);
function updateScaleInput(val) { document.getElementById('scale-input').value = val.toFixed(4); }

eel.expose(updateDICOMUI);
function updateDICOMUI(wl, ww, min_v, max_v, auto_scale) { app.updateDICOMUI(wl, ww, min_v, max_v, auto_scale); }

eel.expose(setStatus);
function setStatus(msg, colorHex) { app.setStatus(msg, colorHex); }

const app = {
    mode: 'xray',

    classActiveTab: "relative flex-1 py-1.5 px-3 rounded-lg text-[13px] font-medium text-white bg-white/[0.06] shadow-[0_0_15px_rgba(255,255,255,0.05),inset_0_1px_rgba(255,255,255,0.1)] border border-white/10 transition-all duration-300 ease-out btn-glow overflow-hidden z-10",
    classInactiveTab: "relative flex-1 py-1.5 px-3 rounded-lg text-[13px] font-medium text-[#8A8F98] hover:text-white hover:bg-white/[0.03] border border-transparent transition-all duration-300 ease-out btn-glow overflow-hidden z-0",

    init() {
        document.getElementById('tab-xray').onclick = () => this.setMode('xray');
        document.getElementById('tab-rgb').onclick = () => this.setMode('rgb');
        this.initPanZoom();
    },

    setMode(newMode) {
        this.mode = newMode;
        eel.switch_mode(newMode)();

        const tabXray = document.getElementById('tab-xray');
        const tabRgb = document.getElementById('tab-rgb');
        const panelXray = document.getElementById('panel-xray');
        const panelRgb = document.getElementById('panel-rgb');
        const mx = document.getElementById('metrics-xray');
        const mr = document.getElementById('metrics-rgb');
        const dicomPanel = document.getElementById('dicom-settings');

        if(newMode === 'xray') {
            tabXray.className = this.classActiveTab; tabRgb.className = this.classInactiveTab;
            panelXray.classList.remove('hidden'); panelXray.classList.add('flex');
            panelRgb.classList.add('hidden'); panelRgb.classList.remove('flex');
            mx.classList.remove('hidden'); mx.classList.add('block');
            mr.classList.remove('block'); mr.classList.add('hidden');
        } else {
            tabRgb.className = this.classActiveTab; tabXray.className = this.classInactiveTab;
            panelRgb.classList.remove('hidden'); panelRgb.classList.add('flex');
            panelXray.classList.add('hidden'); panelXray.classList.remove('flex');
            mr.classList.remove('hidden'); mr.classList.add('block');
            mx.classList.remove('block'); mx.classList.add('hidden');

            dicomPanel.classList.add('hidden');
            dicomPanel.classList.remove('flex');
        }
        this.clear();
    },

    initPanZoom() {
        const container = document.getElementById('image-container');
        const wrapper = document.getElementById('image-wrapper');
        let scale = 1, panning = false, pointX = 0, pointY = 0, startX = 0, startY = 0;

        const setTransform = () => { wrapper.style.transform = `translate(${pointX}px, ${pointY}px) scale(${scale})`; };

        container.addEventListener('mousedown', (e) => {
            e.preventDefault(); panning = true;
            startX = e.clientX - pointX; startY = e.clientY - pointY;
        });

        window.addEventListener('mousemove', (e) => {
            if (!panning) return;
            pointX = e.clientX - startX; pointY = e.clientY - startY;
            setTransform();
        });

        window.addEventListener('mouseup', () => { panning = false; });
        window.addEventListener('mouseleave', () => { panning = false; });

        container.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.wheelDelta ? e.wheelDelta : -e.deltaY;
            (delta > 0) ? (scale *= 1.1) : (scale /= 1.1);
            scale = Math.max(0.1, Math.min(scale, 10));
            setTransform();
        });

        container.addEventListener('dblclick', () => {
            scale = 1; pointX = 0; pointY = 0; setTransform();
        });

        window.resetPanZoom = () => { scale = 1; pointX = 0; pointY = 0; setTransform(); };
    },

    async importXray(isBatch) { eel.import_data('xray', isBatch)(); },
    async importRgb(isBatch) { eel.import_data('rgb', isBatch)(); },

    async runXray() {
        let scale = parseFloat(document.getElementById('scale-input').value) || 1.5;
        eel.run_xray(scale)();
    },
    async runRgb() { eel.run_rgb()(); },
    async cropRgb() { eel.crop_rgb()(); },

    async mirror() { eel.mirror_image()(); },
    async clear() {
        eel.clear_canvas()();
        this.updateDisplay(null, null);
        this.updateMetrics(null, null);
        this.updateRGBMetrics(null);
        if(window.resetPanZoom) window.resetPanZoom();

        document.getElementById('dicom-settings').classList.add('hidden');
        document.getElementById('dicom-settings').classList.remove('flex');
    },
    async saveImage() { eel.save_image()(); },
    async exportPdf() { eel.export_pdf()(); },
    async swapImages() { eel.swap_screens()(); },

    updateWindowing() {
        let wl = document.getElementById('wl-slider').value;
        let ww = document.getElementById('ww-slider').value;
        document.getElementById('wl-val').innerText = wl;
        document.getElementById('ww-val').innerText = ww;
        eel.apply_windowing(parseFloat(wl), parseFloat(ww))();
    },

    updateDICOMUI(wl, ww, min_v, max_v, auto_scale) {
        if (this.mode === 'rgb') return;

        document.getElementById('dicom-settings').classList.remove('hidden');
        document.getElementById('dicom-settings').classList.add('flex');
        document.getElementById('scale-input').value = auto_scale.toFixed(4);

        const wlSlider = document.getElementById('wl-slider');
        const wwSlider = document.getElementById('ww-slider');

        wlSlider.min = min_v; wlSlider.max = max_v; wlSlider.value = wl;
        wwSlider.min = 1; wwSlider.max = Math.max(max_v - min_v, 1) * 2; wwSlider.value = ww;

        document.getElementById('wl-val').innerText = Math.round(wl);
        document.getElementById('ww-val').innerText = Math.round(ww);
    },

    updateDisplay(mainB64, subB64) {
        const mainImg = document.getElementById('main-image');
        const subImg = document.getElementById('sub-image');
        const placeholder = document.getElementById('placeholder-text');

        if(mainB64) {
            mainImg.src = mainB64; mainImg.classList.remove('hidden');
            placeholder.classList.add('hidden');
        } else {
            mainImg.classList.add('hidden'); mainImg.src = "";
            placeholder.classList.remove('hidden');
        }

        if(subB64) { subImg.src = subB64; subImg.classList.remove('hidden'); }
        else { subImg.classList.add('hidden'); subImg.src = ""; }
    },

    updateRGBMetrics(data) {
        const container = document.getElementById('rgb-result-container');
        if (!data) {
            container.innerHTML = `<p class="text-[#EF4444] text-[11px] text-center mt-1 font-medium tracking-widest">未侦测到目标</p>`;
        } else {
            container.innerHTML = `
                <div class="flex flex-col gap-3">
                    <div class="flex justify-between items-center border-b border-white/5 pb-2">
                        <span class="text-[11px] text-[#8A8F98] uppercase tracking-widest">置信度 (Conf)</span>
                        <span class="text-white font-bold text-glow text-[15px] text-[#22C55E]">${(data.conf * 100).toFixed(1)}%</span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-[11px] text-[#8A8F98] uppercase tracking-widest">坐标 (BBox)</span>
                        <span class="text-white font-mono text-[10px] opacity-80">[${data.box.join(', ')}]</span>
                    </div>
                </div>
            `;
        }
    },

    updateMetrics(angles, slipList) {
        const valLL = document.getElementById('val-ll');
        const valSS = document.getElementById('val-ss');
        const valLCA = document.getElementById('val-lca');
        const valLCI = document.getElementById('val-lci');
        const slipContainer = document.getElementById('slip-container');

        if(!angles) {
            // 初始化/清空状态：分离单位，添加防换行和缩减字距类名
            valLL.innerHTML = `--<span class="text-[12px] ml-0.5 opacity-70">°</span>`;
            valSS.innerHTML = `--<span class="text-[12px] ml-0.5 opacity-70">°</span>`;
            valLCA.innerHTML = `--<span class="text-[12px] ml-0.5 opacity-70">°</span>`;
            valLCI.innerHTML = `--<span class="text-[11px] ml-0.5 opacity-70">mm</span>`;

            valLL.className = valSS.className = valLCA.className = valLCI.className = "text-[18px] font-semibold tracking-tighter text-[#8A8F98] leading-none mt-2 transition-all duration-300 whitespace-nowrap";
            slipContainer.innerHTML = `<p class="opacity-50 uppercase tracking-widest text-[10px] text-center mt-1">等待扫描...</p>`;
            return;
        }

        // 数据渲染状态：改用 innerHTML 区分数字和单位，使用 whitespace-nowrap 防止被挤掉行
        const applyValue = (elem, val, unit="°") => {
            let unitSize = unit === 'mm' ? '11px' : '12px';
            if (val !== null && val !== undefined) {
                elem.innerHTML = `${val}<span class="text-[${unitSize}] font-normal ml-0.5 opacity-70">${unit}</span>`;
                elem.className = "text-[18px] font-semibold tracking-tighter text-white leading-none mt-2 text-glow transition-all duration-300 whitespace-nowrap";
            } else {
                elem.innerHTML = `N/A`;
                elem.className = "text-[18px] font-semibold tracking-tighter text-[#EF4444] leading-none mt-2 shadow-[0_0_10px_rgba(239,68,68,0.5)] transition-all duration-300 whitespace-nowrap";
            }
        };

        applyValue(valLL, angles.LL);
        applyValue(valSS, angles.SS);
        applyValue(valLCA, angles.LCA);
        applyValue(valLCI, angles.LCI, "mm");

        if (!slipList || slipList.length === 0) {
            slipContainer.innerHTML = `<p class="text-[#22C55E] text-glow tracking-wider text-[11px] mt-2">✓ 未见明显结构异常</p>`;
        } else {
            let slipHtml = "";
            slipList.forEach(item => {
                let statusBadge = item.is_ant ? `<span class="relative px-2 py-0.5 rounded text-[10px] font-bold bg-[#EF4444]/10 text-[#EF4444] border border-[#EF4444]/40 shadow-[0_0_12px_rgba(239,68,68,0.25)]">前滑脱</span>` :
                                  item.is_retro ? `<span class="relative px-2 py-0.5 rounded text-[10px] font-bold bg-[#EF4444]/10 text-[#EF4444] border border-[#EF4444]/40 shadow-[0_0_12px_rgba(239,68,68,0.25)]">后滑脱</span>` :
                                  `<span class="relative px-2 py-0.5 rounded text-[10px] font-bold bg-[#22C55E]/10 text-[#22C55E] border border-[#22C55E]/40 shadow-[0_0_12px_rgba(34,197,94,0.25)]">正常</span>`;

                let distVal = parseFloat(item.distance || 0).toFixed(1);
                let sign = distVal > 0 ? '+' : '';

                slipHtml += `
                <div class="flex flex-col gap-2 pb-3 border-b border-white/5 last:border-b-0 last:pb-0 group">
                    <div class="flex items-center justify-between">
                        <span class="text-white font-medium tracking-wide text-[13px]">测算节段[${item.segment}]</span>
                        ${statusBadge}
                    </div>
                    <div class="flex items-center text-[#8A8F98] text-[12px]">
                        <span class="opacity-50 mr-2 tracking-widest text-[11px]">位移距离:</span>
                        <span class="font-mono text-white text-glow">${sign}${distVal} mm</span>
                    </div>
                </div>`;
            });
            slipContainer.innerHTML = slipHtml;
        }
    },

    setStatus(msg, colorHex="#8A8F98") {
        const bar = document.getElementById('status-bar'); const dot = document.getElementById('status-indicator');
        if(bar) bar.innerText = msg;
        if(dot) { dot.style.backgroundColor = colorHex; dot.style.boxShadow = `0 0 12px ${colorHex}`; }
    }
};

window.onload = () => app.init();

// ==========================================
// 3. 终极修复：稳健的拖拽拦截与解析逻辑
// ==========================================

// 第一步：彻底接管浏览器的默认拖拽事件（防止变成下载/打开）
const preventDefaults = (e) => {
    e.preventDefault();
    e.stopPropagation();
};

const dragEvents =['dragenter', 'dragover', 'dragleave', 'drop'];
dragEvents.forEach(eventName => {
    document.addEventListener(eventName, preventDefaults, false);
});

// 第二步：处理 UI 的高亮遮罩层
const dragOverlay = document.getElementById('drag-overlay');
let dragTimer;

document.addEventListener('dragenter', (e) => {
    if (dragOverlay) {
        dragOverlay.classList.remove('hidden');
        dragOverlay.classList.add('flex');
    }
}, false);

document.addEventListener('dragover', (e) => {
    clearTimeout(dragTimer);
    if (dragOverlay) {
        dragOverlay.classList.remove('hidden');
        dragOverlay.classList.add('flex');
    }
}, false);

document.addEventListener('dragleave', (e) => {
    // 延迟50ms收起，防止鼠标略过子元素(如屏幕中间的虚线框)导致的闪烁断层
    dragTimer = setTimeout(() => {
        if (dragOverlay) {
            dragOverlay.classList.remove('flex');
            dragOverlay.classList.add('hidden');
        }
    }, 50);
}, false);

// 第三步：核心文件掉落解析与分片上传
document.addEventListener('drop', async (e) => {
    // 文件松手时立刻隐藏遮罩层
    if (dragOverlay) {
        dragOverlay.classList.remove('flex');
        dragOverlay.classList.add('hidden');
    }

    if (e.dataTransfer && e.dataTransfer.files.length > 0) {
        let file = e.dataTransfer.files[0];
        if (app && app.setStatus) {
            app.setStatus("正在本地读取影像文件...", "#5E6AD2");
        }

        let reader = new FileReader();
        reader.onload = async function(event) {
            let b64 = event.target.result;
            let chunkSize = 512 * 1024; // 每片 512KB
            let totalChunks = Math.ceil(b64.length / chunkSize);
            let fileId = Date.now().toString();

            // 循环使用 await 确保严格顺序发送，绝不丢包或堵塞 websocket
            for (let i = 0; i < totalChunks; i++) {
                let progress = Math.round(((i + 1) / totalChunks) * 100);
                if (app && app.setStatus) {
                    app.setStatus(`正在传输数据引擎中... (${progress}%)`, "#5E6AD2");
                }

                let chunk = b64.substring(i * chunkSize, (i + 1) * chunkSize);
                await eel.receive_file_chunk(fileId, chunk, i, totalChunks, file.name, app.mode)();
            }
            if (app && app.setStatus) {
                app.setStatus(`数据合并完成，引擎解析中...`, "#5E6AD2");
            }
        };
        reader.readAsDataURL(file);
    }
}, false);