const healthHub = {
    demoMode: false,
    el(id) { return document.getElementById(id); },
    async request(url) {
        const response = await fetch(url, { credentials: "same-origin" });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.code !== 200) throw new Error(data.message || data.detail || "病例工作台暂时无法读取。");
        return data;
    },
    formatDate(value) {
        if (!value) return "刚刚";
        if (typeof value === "number") return new Date(value * 1000).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
        const [year, month, day] = String(value).split("-");
        return year && month && day ? `${month}.${day}` : String(value);
    },
    timeValue(value) {
        if (typeof value === "number") return value > 100000000000 ? value : value * 1000;
        const parsed = Date.parse(String(value || ""));
        return Number.isFinite(parsed) ? parsed : 0;
    },
    latest(entries, kind) { return (entries || []).find((entry) => entry.kind === kind) || null; },
    setText(id, value) { const node = this.el(id); if (node) node.textContent = value; },
    pill(label, icon, color) {
        const item = document.createElement("span");
        item.className = "tag";
        item.innerHTML = `<i class="fa-solid ${icon}" style="color:${color}"></i> ${label}`;
        return item;
    },
    evidenceCard(definition) {
        const card = document.createElement("article");
        card.className = "evidence";
        const top = document.createElement("div");
        top.className = "evidence-top";
        const icon = document.createElement("span");
        icon.className = "evidence-icon";
        icon.innerHTML = `<i class="fa-solid ${definition.icon}"></i>`;
        const source = document.createElement("span");
        source.className = "source";
        source.textContent = definition.source;
        top.append(icon, source);
        const title = document.createElement("h3"); title.textContent = definition.title;
        const state = document.createElement("strong"); state.textContent = definition.state;
        const note = document.createElement("p"); note.textContent = definition.note;
        card.append(top, title, state, note);
        if (definition.asset) {
            const media = document.createElement("figure");
            media.className = "evidence-media";
            const image = document.createElement("img");
            image.src = definition.asset;
            image.alt = definition.assetAlt || `${definition.title} 可视化资料`;
            image.loading = "lazy";
            const caption = document.createElement("figcaption");
            caption.textContent = definition.assetLabel || "本地可视化资料";
            media.append(image, caption);
            card.append(media);
        }
        card.append(this.pill(definition.tag, definition.tagIcon, definition.color));
        return card;
    },
    renderEvidence(workspace) {
        const entries = workspace.legacy_timeline || [];
        const questionnaire = workspace.questionnaire?.triage;
        const dr = this.latest(entries, "dr");
        const rgb = this.latest(entries, "rgb");
        const video = (workspace.gait_jobs || [])[0] || this.latest(entries, "video");
        const demoAsset = (key) => workspace.demo ? window.MindSpineDemo?.asset?.(key) || "" : "";
        const advancedTasks = (workspace.assessments || []).filter((item) => ["ctmr", "multicam", "surface", "motion"].some((term) => String(item.kind).includes(term)));
        const advanced = advancedTasks[0];
        const definitions = [
            questionnaire ? {
                title: "安全分流", icon: "fa-clipboard-check", source: workspace.demo ? "演示规则引擎" : "规则引擎", state: this.triageName(questionnaire.risk_level),
                note: questionnaire.message || "仅反映本问卷覆盖的规则。", tag: questionnaire.data_complete ? "资料完整" : "待补全", tagIcon: questionnaire.data_complete ? "fa-check" : "fa-triangle-exclamation", color: questionnaire.data_complete ? "#61d6a2" : "#f8c56a", available: true,
            } : { title: "安全分流", icon: "fa-clipboard-check", source: "规则引擎", state: "尚未建立", note: "需要先完成最小安全分流。", tag: "待开始", tagIcon: "fa-circle", color: "#9299a8", available: false },
            dr ? {
                title: "DR / X 光", icon: "fa-x-ray", source: workspace.demo ? "本地授权资料" : "既有影像链路", state: dr.status === "completed" ? "已有测量结果" : "资料已归档", note: dr.status === "completed" ? "原始 DR 与算法可视化分开保存；LL、SS/SA、LCA、LCI 为独立病例事实。" : dr.summary || "等待可用影像结果。", tag: dr.status === "completed" ? (workspace.demo ? "演示记录 · 非诊断" : "原链路") : "待处理", tagIcon: "fa-ruler-combined", color: "#8f9cff", asset: demoAsset("real_lateral_dr"), assetAlt: "本地原始侧位 DR 图像", assetLabel: "原始侧位 DR / X 光 · 本地", available: true,
            } : { title: "DR / X 光", icon: "fa-x-ray", source: "既有影像链路", state: "尚未添加", note: "可导入已有影像；系统不会自动开检查。", tag: "可选资料", tagIcon: "fa-plus", color: "#9299a8", available: false },
            rgb ? {
                title: "体表 RGB", icon: "fa-person", source: workspace.demo ? "本地授权体表资料" : "既有 RGB 链路", state: rgb.status === "completed" ? "已有分析资料" : "资料已归档", note: rgb.summary || "体表资料与影像测量保持独立。", tag: rgb.status === "completed" ? (workspace.demo ? "演示记录 · 非诊断" : "原链路") : "待处理", tagIcon: "fa-camera", color: "#b6a4ff", asset: demoAsset("surface_rgb"), assetAlt: "本地体表 RGB 图像", assetLabel: "体表 RGB · 本地", available: true,
            } : { title: "体表 RGB", icon: "fa-person", source: "既有 RGB 链路", state: "尚未添加", note: "体表照片不能替代 X 光或临床测量。", tag: "可选资料", tagIcon: "fa-plus", color: "#9299a8", available: false },
            video ? {
                title: "动作 / 步态视频", icon: "fa-person-walking", source: workspace.demo ? "本地视频流程素材" : "独立 Worker", state: workspace.demo ? "协议任务已完成" : ({completed:"分析完成",queued:"等待计算",running:"正在分析",rejected:"需调整素材",failed:"分析失败",cancelled:"已取消",prepared:"素材已保存"}[video.status] || "已归档"), note: workspace.demo ? "采集质量与协议完整性为界面示例。" : (video.message || "打开步态工作区查看轮廓、跟踪与三类模型分数。"), tag: workspace.demo ? "演示任务 · 非诊断" : "ScoNet · V2.0.1", tagIcon: workspace.demo ? "fa-flask" : "fa-clock", color: "#f8c56a", asset: demoAsset("video_pose"), assetAlt: "视频姿态叠加帧", assetLabel: "视频姿态叠加帧 · 静态预览", available: true,
            } : { title: "动作 / 步态视频", icon: "fa-person-walking", source: "独立 Worker", state: "尚未添加", note: "导入行走视频，开始轮廓与跟踪分析。", tag: "可添加", tagIcon: "fa-lock", color: "#9299a8", available: false },
            advanced ? {
                title: "专业 / 三维任务", icon: "fa-cubes", source: workspace.demo ? "本地点云可视化" : "隔离复现工具", state: workspace.demo ? `${advancedTasks.length} 项任务已完成` : this.assessmentState(advanced.state), note: workspace.demo ? "体表点云为视觉档案；非尺度测量，不与原始 X 光或医学分割混同。" : "CT/MR、三维或多相机任务均需独立验收和人工复核。", tag: workspace.demo ? "演示任务 · 非诊断" : (advanced.tool_version || "受控任务"), tagIcon: "fa-shield-halved", color: "#f8c56a", asset: demoAsset("torso_point_cloud"), assetAlt: "体表点云可视化", assetLabel: "体表点云 · 视觉档案", available: true,
            } : { title: "专业 / 三维任务", icon: "fa-cubes", source: "隔离复现工具", state: "尚未申请", note: "未验收工具不能生成患者可用结论。", tag: "人工复核", tagIcon: "fa-user-doctor", color: "#9299a8", available: false },
        ];
        const grid = this.el("hub-evidence-grid");
        grid.replaceChildren(...definitions.map((item) => this.evidenceCard(item)));
        const covered = definitions.filter((item) => item.available).length;
        this.setText("hub-coverage-title", `${covered} / ${definitions.length} 模态`);
        this.setText("hub-coverage-note", covered ? "已记录的资料会保留各自来源与边界。" : "先从安全分流或已有资料开始。" );
        this.setText("hub-quality-state", workspace.demo ? "演示：只比较协议与质量均匹配的数据" : (covered ? "不把不同条件的数据强行比较" : "等待首项病例事实"));
    },
    triageName(risk) {
        return { routine: "常规随访", attention: "谨慎随访", soon: "尽快线下复核", urgent: "优先线下评估", incomplete: "信息不完整" }[risk] || "待完成";
    },
    assessmentState(state) {
        return { draft: "等待确认", queued: "已排队", running: "正在处理", completed: "已完成", needs_review: "等待人工复核", failed: "处理失败", cancelled: "已取消" }[state] || "状态待确认";
    },
    renderTriage(workspace) {
        const triage = workspace.questionnaire?.triage || {};
        const risk = String(triage.risk_level || "unknown");
        const view = {
            urgent: ["优先线下评估", triage.message || "筛查提示需要优先线下评估。", "health-agent.html?prompt=我需要申请人工复核", "查看复核任务", "fa-circle-exclamation", "出现高优先级规则"],
            soon: ["尽快安排线下复核", triage.message || "筛查提示需要尽快咨询线下专业人员。", "health-agent.html?prompt=我需要申请人工复核", "查看复核任务", "fa-triangle-exclamation", "当前不生成自动计划"],
            attention: ["补齐资料后谨慎随访", triage.message || "当前资料提示需要谨慎随访。", "personal-case.html?modal=questionnaire", "更新安全分流", "fa-circle-info", "仅提供资料整理与安全提醒"],
            routine: ["选择下一项有价值的资料", triage.message || "本问卷未触发覆盖规则中的高优先级信号。", "personal-workspace.html", "添加评估资料", "fa-circle-check", "不等于排除疾病"],
            incomplete: ["补全安全分流", triage.message || "必填主观评分尚未完成。", "personal-case.html?modal=questionnaire", "补全问卷", "fa-circle-question", "自动计划保持关闭"],
            unknown: ["先建立安全边界", "在解释资料或提出计划前，需要先填写本次健康问卷。", "personal-case.html?modal=questionnaire", "完成安全分流", "fa-circle-question", "需要两项必填主观评分"],
        }[risk];
        if (workspace.demo && risk === "routine") {
            view[0] = "本轮随访已完成，等待下一次同协议复测";
            view[1] = "演示病例已整合问卷、影像、动作质控、体表档案与康复打卡；下一步是保留可比较条件下的复测。";
            view[2] = "personal-case.html?demo=1";
            view[3] = "查看完整演示档案";
            view[5] = "已完成 · 演示分流";
        }
        this.setText("hub-next-title", view[0]); this.setText("hub-next-note", view[1]);
        const action = this.el("hub-next-action"); action.href = view[2]; action.innerHTML = `${view[3]} <i class="fa-solid fa-arrow-right"></i>`;
        this.setText("hub-triage-title", this.triageName(risk)); this.setText("hub-triage-note", triage.analysis || triage.message || "问卷尚未建立安全分流边界。"); this.setText("hub-triage-state", view[5]);
        this.el("hub-triage-icon").className = `fa-solid ${view[4]} ${risk === "urgent" || risk === "soon" ? "text-[#FA8189]" : risk === "routine" ? "text-[#61D6A2]" : "text-[#AEB7FF]"}`;
    },
    activityItem(item) {
        const row = document.createElement("article"); row.className = "feed-item";
        const icon = document.createElement("span"); icon.className = "feed-icon"; icon.innerHTML = `<i class="fa-solid ${item.icon}"></i>`;
        const body = document.createElement("div"); const title = document.createElement("strong"); title.textContent = item.title; const note = document.createElement("p"); note.textContent = item.note; body.append(title, note);
        const date = document.createElement("time"); date.textContent = this.formatDate(item.date);
        row.append(icon, body, date); return row;
    },
    renderFeed(workspace) {
        const source = [
            ...(workspace.legacy_timeline || []).map((item) => ({ date: item.created_at || item.recorded_at, icon: { dr: "fa-x-ray", rgb: "fa-camera", video: "fa-person-walking", surface: "fa-cubes", clinical: "fa-stethoscope", checkin: "fa-heart-pulse" }[item.kind] || "fa-file", title: item.title || "病例资料", note: item.summary || "已写入个人病例。" })),
            ...(workspace.assessments || []).map((item) => ({ date: item.created_at, icon: "fa-gears", title: item.kind || "受控评估任务", note: `${this.assessmentState(item.state)} · ${item.tool_version || "工具版本待确认"}` })),
            ...(workspace.review_tasks || []).map((item) => ({ date: item.created_at, icon: "fa-user-doctor", title: "人工复核任务", note: item.summary || "等待受邀医生或病例所有者处理。" })),
        ].sort((a, b) => this.timeValue(b.date) - this.timeValue(a.date)).slice(0, 6);
        const feed = this.el("hub-feed"); feed.replaceChildren();
        if (!source.length) {
            const empty = document.createElement("p"); empty.className = "mt-4 text-sm text-[#9299A8]"; empty.textContent = "还没有病例事实。先完成安全分流，或添加一项已有资料。"; feed.append(empty); return;
        }
        feed.append(...source.map((item) => this.activityItem(item)));
    },
    renderInsights(workspace) {
        const insights = workspace.demo_insights || {};
        const trend = insights.trend;
        const empty = this.el("hub-trend-empty");
        const chart = this.el("hub-trend-chart");
        const path = this.el("hub-trend-path");
        const pointsLayer = this.el("hub-trend-points");
        const labels = this.el("hub-trend-labels");
        const items = Array.isArray(trend?.values)
            ? trend.values.map((item) => ({ ...item, value: Number(item.value) })).filter((item) => Number.isFinite(item.value))
            : [];
        this.setText("hub-trend-title", trend?.label || "等待可比较记录");
        this.setText("hub-trend-protocol", workspace.demo ? "演示：同一协议、单位和质量条件已匹配" : "协议、单位与质量相同才绘图");
        const hasTrend = items.length >= 2;
        empty.classList.toggle("hidden", hasTrend);
        chart.classList.toggle("hidden", !hasTrend);
        if (hasTrend) {
            const latest = items.at(-1).value;
            const delta = latest - items.at(-2).value;
            this.setText("hub-trend-latest", `${latest}${trend.unit || ""}`);
            this.setText("hub-trend-delta", `较上次 ${delta > 0 ? "+" : ""}${delta.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}${trend.unit || ""}`);
            const width = 680, height = 188, left = 26, right = 26, top = 18, bottom = 34;
            const values = items.map((item) => item.value);
            const minimum = Math.min(...values), maximum = Math.max(...values), spread = Math.max(maximum - minimum, 1);
            const low = minimum - spread * .25, high = maximum + spread * .25;
            const x = (index) => left + ((width - left - right) * index) / (items.length - 1);
            const y = (value) => top + ((high - value) / (high - low)) * (height - top - bottom);
            path.setAttribute("points", items.map((item, index) => `${x(index)},${y(item.value)}`).join(" "));
            pointsLayer.replaceChildren(); labels.replaceChildren();
            const svg = "http://www.w3.org/2000/svg";
            items.forEach((item, index) => {
                const dot = document.createElementNS(svg, "circle");
                dot.setAttribute("class", "trend-point"); dot.setAttribute("cx", String(x(index))); dot.setAttribute("cy", String(y(item.value))); dot.setAttribute("r", "4.5");
                const title = document.createElementNS(svg, "title"); title.textContent = `${item.date}：${item.value}${trend.unit || ""}`; dot.append(title); pointsLayer.append(dot);
                const label = document.createElementNS(svg, "text");
                label.setAttribute("x", String(x(index))); label.setAttribute("y", "181"); label.setAttribute("text-anchor", index === 0 ? "start" : index === items.length - 1 ? "end" : "middle");
                label.textContent = item.date; labels.append(label);
            });
        } else {
            this.setText("hub-trend-latest", "--"); this.setText("hub-trend-delta", "等待至少两次记录");
        }
        const grid = this.el("hub-analysis-grid");
        grid.replaceChildren();
        const analysis = Array.isArray(insights.analysis)
            ? insights.analysis
            : [{ label: "病例状态", value: "等待资料", note: "完成问卷、资料上传或复查后，这里会显示结构化执行状态。" }];
        analysis.forEach((item) => {
            const card = document.createElement("article"); card.className = "analysis-card";
            const label = document.createElement("span"); label.textContent = item.label || "病例状态";
            const value = document.createElement("strong"); value.textContent = item.value || "--";
            const note = document.createElement("p"); note.textContent = item.note || "";
            card.append(label, value, note); grid.append(card);
        });
        this.setText("hub-analysis-boundary", insights.interpretation || "不同来源的资料会保留各自边界，系统不会把它们合成为单一健康总分。");
    },
    render(workspace) {
        const legacy = workspace.legacy_overview || {};
        const factCount = Number(legacy.total_entries || 0) + Number(workspace.questionnaire ? 1 : 0) + (workspace.assessments || []).length + (workspace.review_tasks || []).length;
        this.setText("hub-case-title", workspace.case?.title || "我的脊柱健康病例"); this.setText("hub-fact-count", `${factCount} 条病例事实`);
        this.renderTriage(workspace); this.renderEvidence(workspace); this.renderInsights(workspace); this.renderFeed(workspace);
        const reviews = (workspace.review_tasks || []).filter((item) => !["completed", "resolved", "approved", "cancelled"].includes(item.state || item.status));
        this.setText("hub-review-title", reviews.length ? `${reviews.length} 项待复核` : "暂无待复核");
        this.setText("hub-review-note", reviews.length ? "高优先级信号和未验收任务将在人工队列中保留。" : (workspace.demo ? "演示病例没有待复核任务；真实病例仍会保留不确定性。" : "出现需复核状态时，这里会形成可追踪任务。"));
        this.setText("hub-consent-state", workspace.demo ? "演示模式 · 未发送数据" : (workspace.consent?.external_ai_allowed ? "AI 病例解读已开启" : "仅本地运行"));
        this.setText("hub-agent-state", workspace.demo ? "本地合成数据；不调用外部模型" : (workspace.consent?.external_ai_allowed ? "结合病例上下文与检索知识进行解读" : "没有向外部模型发送病例资料"));
    },
    async init() {
        try {
            this.demoMode = Boolean(window.MindSpineDemo?.useWorkspace?.());
            const selectedCase = new URLSearchParams(window.location.search).get("case_id");
            const data = this.demoMode ? {} : await this.request(`/api/cases/${selectedCase ? encodeURIComponent(selectedCase) : "default"}/workspace`);
            const workspace = this.demoMode ? window.MindSpineDemo.workspace() : (data.workspace || {});
            if (!this.demoMode) window.MindSpineCaseContext?.setCaseId?.(workspace.case?.id);
            window.MindSpineDemo?.decorateLinks?.();
            if (this.demoMode) window.MindSpineDemo.revealBanner();
            this.render(workspace);
        }
        catch (error) { this.setText("hub-next-title", "病例工作台暂时不可用"); this.setText("hub-next-note", error.message); this.setText("hub-case-title", "请登录后继续"); }
    },
};
document.addEventListener("DOMContentLoaded", () => healthHub.init());
