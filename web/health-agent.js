const healthAgent = {
    workspace: null,
    actionTokens: new Map(),
    demoMode: false,
    attachments: [],
    selectedPlugins: new Set(),
    plugins: [],
    busy: false,
    activeRun: null,
    pollTimer: null,
    messageStart: 0,
    pendingRequest: null,
    el(id) { return document.getElementById(id); },
    async request(url, options = {}) {
        const response = await fetch(url, { credentials: "same-origin", ...options });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || ![200, 202].includes(data.code)) {
            const detail = typeof data.detail === "string" ? data.detail : "";
            const error = new Error(data.message || detail || `请求未成功（HTTP ${response.status}）`);
            error.status = response.status; throw error;
        }
        return data;
    },
    flash(message, error = false) {
        const node = this.el("agent-toast");
        node.textContent = message;
        node.style.color = error ? "#b42318" : "#666";
    },
    triageName(risk) {
        return { routine: "常规随访", attention: "谨慎随访", soon: "尽快线下复核", urgent: "优先线下评估", incomplete: "信息不完整" }[risk] || "待完成问卷";
    },
    actionLabel(kind) {
        return { run_video_screening: "申请视频动作 / 步态任务", build_surface_archive: "申请近似体表视觉档案", run_ctmr_segmentation: "创建 CT/MR 专业影像任务", run_multicam_kinematics: "创建多相机运动学任务", save_rehab_plan: "保存受控康复计划", request_clinical_review: "创建人工复核任务", export_case: "导出病例资料" }[kind] || "执行病例动作";
    },
    async load() {
        // Explicit demos use a local, immutable case first; real is the default.
        if (window.MindSpineDemo?.useWorkspace?.()) {
            this.demoMode = true;
            this.workspace = window.MindSpineDemo.workspace();
            window.MindSpineDemo.decorateLinks();
            window.MindSpineDemo.revealBanner();
            this.render(this.workspace);
            return;
        }
        try {
            const selectedCase = new URLSearchParams(window.location.search).get("case_id");
            const data = await this.request(`/api/cases/${selectedCase ? encodeURIComponent(selectedCase) : "default"}/workspace`);
            this.demoMode = false;
            this.workspace = data.workspace || {};
            window.MindSpineCaseContext?.setCaseId?.(this.workspace.case?.id);
            window.MindSpineDemo?.decorateLinks?.();
            this.render(this.workspace);
        } catch (error) {
            if (!this.workspace) this.el("agent-chat-log").textContent = error.message || "Agent 工作区暂时无法读取。";
            this.flash([401, 403].includes(error.status) ? "请先登录并确认当前病例可访问。" : `读取病例失败：${error.message || "请稍后重试"}`, true);
        }
    },
    renderContext(workspace) {
        const triage = workspace.questionnaire?.triage || {};
        const risk = String(triage.risk_level || "unknown");
        const legacyEntries = workspace.legacy_timeline || [];
        const assessments = workspace.assessments || [];
        const reviews = workspace.review_tasks || [];
        const answers = workspace.questionnaire?.answers || {};
        const redFlags = Array.isArray(answers.red_flags) ? answers.red_flags : [];
        this.el("agent-case-label").textContent = workspace.case?.title || "我的脊柱健康病例";
        this.el("agent-triage-status").textContent = workspace.demo ? `已完成 · ${this.triageName(risk)}` : this.triageName(risk);
        this.el("agent-triage-note").textContent = triage.message || "完成问卷后，系统才会开启对应的自动化范围。";
        this.el("agent-context-questionnaire").textContent = workspace.questionnaire ? (triage.data_complete ? "已完成" : "信息不完整") : "尚未开始";
        const assetCount = legacyEntries.length + assessments.length + (workspace.gait_jobs || []).length;
        const completedReviews = reviews.filter((review) => ["completed", "resolved", "approved"].includes(String(review.state || review.status || "").toLowerCase())).length;
        this.el("agent-context-assets").textContent = workspace.demo ? `已完成 · ${assetCount} 项` : `${assetCount} 条`;
        this.el("agent-context-review").textContent = workspace.demo
            ? `已完成 · ${completedReviews} 项`
            : (reviews.length - completedReviews ? `${reviews.length - completedReviews} 项待办` : "暂无待办");
        const externalAllowed = Boolean(workspace.consent?.external_ai_allowed);
        this.el("agent-consent-status").textContent = workspace.demo ? "示例数据" : (externalAllowed ? "AI 病例解读已开启" : "本地知识与病例检索");
        this.el("agent-consent-button-label").textContent = workspace.demo ? "示例模式" : (externalAllowed ? "关闭 AI 病例解读" : "开启 AI 病例解读");
        this.el("agent-questionnaire-summary-title").textContent = workspace.questionnaire
            ? `${workspace.demo ? "已完成 · " : ""}疼痛 ${answers.pain_score ?? "--"}/10${answers.daily_function ? " · 已记录日常活动" : ` · 活动受限 ${answers.activity_limit ?? "--"}/10`}`
            : "尚未填写健康问卷";
        this.el("agent-questionnaire-analysis").textContent = triage.analysis
            || (workspace.questionnaire ? `问卷内高优先级信号：${redFlags.length ? `${redFlags.length} 项命中` : "0 项命中"}。${triage.message || ""}` : "填写后会展示规则范围、已命中信号和安全限制。");
        const safetyLink = this.el("agent-safety-link");
        if (safetyLink) safetyLink.innerHTML = `${workspace.questionnaire ? "查看 / 更新问卷" : "完成安全分流"} <i class="fa-solid fa-arrow-right"></i>`;
    },
    evidenceLink(item) {
        const link = document.createElement("a");
        link.href = String(item.source_url || "#").startsWith("https://") ? item.source_url : "#";
        link.target = "_blank";
        link.rel = "noreferrer";
        link.innerHTML = '<i class="fa-solid fa-book-open"></i>';
        link.append(` ${item.title || "依据来源"}`);
        return link;
    },
    auditTag(icon, text) {
        const tag = document.createElement("span"); tag.className = "audit-tag"; tag.innerHTML = `<i class="fa-solid ${icon}"></i>`; tag.append(` ${text}`); return tag;
    },
    renderMessages(messages) {
        const log = this.el("agent-chat-log");
        const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 120;
        log.replaceChildren();
        messages.slice(this.messageStart).forEach((message) => {
            const isAssistant = message.role === "assistant";
            const card = document.createElement("article"); card.className = `message ${isAssistant ? "assistant" : "user"}`;
            card.setAttribute("aria-label", isAssistant ? "回答" : "你的消息");
            const content = document.createElement("div"); content.className = "message-text";
            content.textContent = this.cleanAnswer(message.content, message.evidence);
            card.append(content);
            const answerMode = message.answer_mode || message.audit?.answer_mode;
            const modeLabel = {model_general: "DeepSeek 通用回答 · 非知识库审核结论", web_evidence: "联网资料辅助回答 · 资料尚未经临床审核", local_evidence: "依据当前病例与本地知识"}[answerMode];
            if (isAssistant && modeLabel) { const source = document.createElement("p"); source.className = "answer-source"; source.textContent = modeLabel; card.append(source); }
            const attachments = message.attachments || message.audit?.attachments || [];
            if (attachments.length) {
                const files = document.createElement("div"); files.className = "message-attachments";
                attachments.forEach((file) => {
                    const label = document.createElement("span"); label.className = "attachment-chip";
                    label.textContent = file.filename || file.name || "附件"; files.append(label);
                });
                card.append(files);
            }
            if (isAssistant && Array.isArray(message.evidence) && message.evidence.length) {
                const references = document.createElement("details"); references.className = "message-evidence references";
                const summary = document.createElement("summary"); summary.textContent = `参考资料 · ${message.evidence.length}`; references.append(summary);
                message.evidence.slice(0, 6).forEach((item) => references.append(this.evidenceLink(item)));
                card.append(references);
            }
            const storedRun = (this.workspace?.agent_tool_runs || []).find((run) => run.id === message.audit?.tool_run_id);
            const resultCards = storedRun?.result_cards?.length ? storedRun.result_cards : Array.isArray(message.result_cards) && message.result_cards.length
                ? message.result_cards
                : (Array.isArray(message.audit?.result_cards) ? message.audit.result_cards : []);
            if (isAssistant && resultCards.length) {
                const results = document.createElement("div"); results.className = "message-evidence";
                resultCards.forEach((result) => {
                    if (["questionnaire", "report_export", "consent_request"].includes(result.type)) {
                        const action = document.createElement("button"); action.type = "button"; action.className = "workflow-action";
                        action.textContent = result.title || {questionnaire: "填写健康问卷", report_export: "设置并生成报告", consent_request: "设置 AI 授权"}[result.type];
                        action.addEventListener("click", () => {
                            if (result.type === "questionnaire") this.openQuestionnaire();
                            else if (result.type === "report_export") window.MindSpineAgentWorkflows?.openReport(this, result.options || {});
                            else this.toggleConsent();
                        });
                        results.append(action); return;
                    }
                    if (result.type === "case_history" && window.MindSpineHistoryCards) {
                        card.append(window.MindSpineHistoryCards.render(result));
                        return;
                    }
                    if (result.images?.length || result.metrics || ["image", "metrics", "video"].includes(result.type)) {
                        card.append(this.resultCard(result)); return;
                    }
                    let url;
                    try { url = new URL(result.url, window.location.href); } catch { return; }
                    if (url.origin !== window.location.origin) return;
                    const link = document.createElement("a"); link.href = url.pathname + url.search;
                    link.textContent = `${result.title || result.label || "查看结果"} ↗`;
                    if (result.type === "document") { link.setAttribute("download", ""); link.className = "workflow-action"; }
                    if (result.summary) link.title = result.summary;
                    results.append(link);
                });
                card.append(results);
            }
            log.append(card);
        });
        if (this.busy && !this.activeRun) {
            const pending = document.createElement("p"); pending.className = "typing-indicator"; pending.textContent = "正在处理…"; log.append(pending);
        }
        if (nearBottom || this.scrollNext) log.scrollTop = log.scrollHeight;
        this.scrollNext = false;
    },
    renderActions(actions, demo = false) {
        const list = this.el("agent-action-list"); list.replaceChildren();
        const drafts = actions.filter((action) => action.state === "draft");
        const completed = actions.filter((action) => action.state !== "draft");
        if (!drafts.length && !completed.length) {
            const empty = document.createElement("p"); empty.className = "mt-3 text-xs text-[#9299A8]"; empty.textContent = "暂无待确认动作。已确认任务会保留在健康档案中。"; list.append(empty); return;
        }
        completed.forEach((action) => {
            const card = document.createElement("article"); card.className = "queue-card";
            const labels = { completed: "已完成", confirmed: "已确认", queued: "排队中", running: "执行中", failed: "执行失败", rejected: "未通过", cancelled: "已取消", review_required: "待复核" };
            const title = document.createElement("strong"); title.textContent = this.actionLabel(action.kind);
            const reason = document.createElement("p"); reason.textContent = action.reason || "已写入演示病例时间线。";
            const state = document.createElement("span"); state.className = "audit-tag"; state.textContent = `${demo ? "示例 · " : ""}${labels[action.state] || action.state}`;
            card.append(title, reason, state); list.append(card);
        });
        drafts.forEach((action) => {
            const card = document.createElement("article"); card.className = "queue-card";
            const title = document.createElement("strong"); title.textContent = this.actionLabel(action.kind);
            const reason = document.createElement("p"); reason.textContent = action.reason;
            const button = document.createElement("button"); button.type = "button"; button.className = "confirm";
            const token = this.actionTokens.get(action.id) || action.confirmation_token;
            if (!token) { button.textContent = "确认执行"; button.addEventListener("click", () => this.refreshAndConfirm(action, button)); }
            else { button.textContent = "确认执行"; button.addEventListener("click", () => this.confirm(action, token)); }
            card.append(title, reason, button); list.append(card);
        });
    },
    render(workspace = this.workspace || {}) {
        this.renderContext(workspace);
        this.renderMessages(workspace.messages || []);
        this.renderActions(workspace.actions || [], Boolean(workspace.demo));
        this.renderToolTasks(workspace.agent_tool_runs || []);
        this.renderGaitTasks(workspace.gait_jobs || []);
        const projects = workspace.third_party_reproduction || [];
        const enabled = projects.filter((project) => String(project.status).includes("implemented")).length;
        this.el("agent-third-party-status").textContent = workspace.demo
            ? "当前使用本地示例病例与对话。"
            : `知识库 ${workspace.knowledge?.source_count || 0} 个来源 · ${enabled} 项已登记能力`;
    },
    renderToolTasks(runs) {
        const list = this.el("agent-action-list");
        if (runs.length && !(this.workspace?.actions || []).length) list.replaceChildren();
        runs.slice(0, 12).forEach((run) => {
            const card = document.createElement("article"); card.className = "queue-card";
            const title = document.createElement("strong"); title.textContent = run.message || "资料分析";
            const detail = document.createElement("p"); detail.textContent = (run.steps || []).map((step) => step.label || step.plugin_id).join(" · ") || "资料归档";
            const status = document.createElement("span"); status.className = "audit-tag";
            status.textContent = this.runStateName(run.state);
            card.append(title, detail, status); list.append(card);
        });
    },
    runStateName(state) {
        return {queued: "等待执行", running: "正在分析", cancelling: "正在停止", completed: "分析完成", failed: "分析未完成", cancelled: "已停止", needs_review: "需要复核", rejected: "未通过"}[state] || "正在处理";
    },
    renderGaitTasks(jobs) {
        const labels = { queued: "排队中", running: "分析中", completed: "已完成", rejected: "需调整素材", failed: "失败", cancelled: "已取消" };
        jobs.slice(0, 6).forEach((job) => {
            const card = document.createElement("article"); card.className = "queue-card";
            const title = document.createElement("strong"); title.textContent = job.filename || "步态评估";
            const status = document.createElement("p"); status.textContent = labels[job.status] || job.status;
            const link = document.createElement("a"); link.className = "confirm";
            link.href = `gait-workspace.html?job_id=${encodeURIComponent(job.id || job.job_id)}&case_id=${encodeURIComponent(job.case_id || this.workspace?.case?.id || "")}`;
            link.textContent = "打开评估 ↗"; card.append(title, status, link); this.el("agent-action-list").append(card);
        });
    },
    async refreshAndConfirm(action, button) {
        button.disabled = true;
        try {
            const result = await this.request(`/api/agent-actions/${encodeURIComponent(action.id)}/refresh-confirmation`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ case_id: this.workspace.case.id }) });
            if (!result.confirmation_token) throw new Error("暂时无法恢复此任务，请刷新后重试。");
            this.actionTokens.set(action.id, result.confirmation_token);
            await this.confirm(action, result.confirmation_token);
        } catch (error) { this.flash(error.message, true); }
        finally { button.disabled = false; }
    },
    async send(event) {
        event.preventDefault();
        const caseId = this.workspace?.case?.id;
        const input = this.el("agent-chat-input");
        const button = this.el("agent-send-button");
        const message = input.value.trim() || (this.attachments.length ? "请分析这些资料。" : "");
        if (this.busy || !message) return;
        if (!caseId) { this.flash("病例尚未加载，请刷新页面或登录后重试。", true); return; }
        if (this.selectedPlugins.size && !this.attachments.length) {
            this.flash("请先用 ＋ 添加要分析的资料。", true); this.setMenu(true); return;
        }
        if (this.demoMode) {
            if (this.attachments.length || this.selectedPlugins.size) {
                this.flash("当前为示例病例。请切换到真实病例后上传资料并运行模型。", true); return;
            }
            this.workspace.messages.push({ id: `demo-user-${Date.now()}`, role: "user", content: message, created_at: "2026-08-26" });
            this.workspace.messages.push(window.MindSpineDemo.agentReply(message));
            input.value = ""; input.style.height = "auto"; this.render(this.workspace);
            this.flash("");
            return;
        }
        const staged = this.attachments.slice();
        this.setBusy(true); this.setMenu(false); this.flash("");
        this.scrollNext = true;
        const optimistic = {id: `pending-${Date.now()}`, role: "user", content: message, attachments: staged.map((item) => ({ filename: item.file.name }))};
        this.workspace.messages ||= []; this.workspace.messages.push(optimistic);
        input.value = ""; input.style.height = "auto"; this.renderMessages(this.workspace.messages);
        try {
            if (staged.length) {
                await this.sendToolRun(message, staged);
                return;
            }
            const data = await this.request(`/api/cases/${encodeURIComponent(caseId)}/agent/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, web_search: Boolean(this.el("agent-web-search")?.checked) }) });
            (data.agent?.proposed_actions || []).forEach((action) => { if (action.confirmation_token) this.actionTokens.set(action.id, action.confirmation_token); });
            this.setBusy(false); this.scrollNext = true; await this.load();
            if (!["urgent", "soon"].includes(data.agent?.safety_status) && data.agent?.result_cards?.some((card) => card.type === "questionnaire")) this.openQuestionnaire();
        } catch (error) {
            this.workspace.messages = this.workspace.messages.filter((item) => item !== optimistic);
            if (!this.activeRun) this.el("agent-run-status").hidden = true;
            input.value = message; this.renderMessages(this.workspace.messages);
            this.flash(error.message || "暂时无法完成请求，请重试。", true);
        } finally { if (!this.activeRun) { this.setBusy(false); this.renderMessages(this.workspace.messages || []); } }
    },
    async confirm(action, token) {
        const caseId = this.workspace?.case?.id;
        if (!caseId || !window.confirm(`确认“${this.actionLabel(action.kind)}”吗？`)) return;
        try {
            const data = await this.request(`/api/agent-actions/${encodeURIComponent(action.id)}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ case_id: caseId, confirmation_token: token }) });
            this.actionTokens.delete(action.id); await this.load();
            this.flash(data.action?.state === "completed" ? "动作已完成。" : "已确认；任务进入受控复核流程。");
        } catch (error) { this.flash(error.message || "动作确认失败。", true); }
    },
    async toggleConsent() {
        const caseId = this.workspace?.case?.id;
        const currentlyAllowed = Boolean(this.workspace?.consent?.external_ai_allowed);
        if (!caseId) return;
        if (this.demoMode) { this.flash("演示病例始终为只读本地数据，不会修改真实同意设置。"); return; }
        const message = currentlyAllowed
            ? "关闭后，Agent 不再调用外部模型，之后完全使用本地规则与内容。是否继续？"
            : "开启后，Agent 会将当前问题、必要对话、结构化检查摘要和检索知识发送给 DeepSeek。原始影像、视频、照片不会发送；问卷自由文本不会自动附带。若你另外勾选“联网”，应用会向 PubMed 发送概括性的公共医学主题词，不发送病例原文。是否开启？";
        if (!window.confirm(message)) return;
        try {
            await this.request(`/api/cases/${encodeURIComponent(caseId)}/consent`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ external_ai_allowed: !currentlyAllowed }) });
            await this.load(); this.flash(!currentlyAllowed ? "AI 病例解读已开启。" : "已关闭外部 AI 调用。");
        } catch (error) { this.flash(error.message || "同意设置更新失败。", true); }
    },
    cleanAnswer(content, evidence = []) {
        const known = new Set(["who_lbp", "nice_ng59", ...((evidence || []).flatMap((item) => [item.id, item.source_id, item.citation_id]).filter(Boolean))]);
        return String(content || "").replace(/\[([^\]\n]+)\](?:\([^\n)]*\))?/g, (whole, body) => {
            const ids = body.split(/[,;，；]/).map((id) => id.trim().replace(/^citation\s*:\s*/i, "")).filter(Boolean);
            return ids.length && ids.every((id) => known.has(id)) ? "" : whole;
        }).replace(/ +([，。；：！？])/g, "$1").trim();
    },
    safeLocalUrl(value) {
        if (!value) return "";
        try {
            const url = new URL(value, window.location.href);
            return url.origin === window.location.origin && ["http:", "https:"].includes(url.protocol) ? url.href : "";
        } catch { return ""; }
    },
    resultCard(result) {
        const card = document.createElement("section"); card.className = "tool-result-card result-card";
        const heading = document.createElement("h3"); heading.textContent = result.title || "分析结果"; card.append(heading);
        if (result.summary) { const summary = document.createElement("p"); summary.textContent = this.cleanAnswer(result.summary); card.append(summary); }
        const images = document.createElement("div"); images.className = "result-images";
        (result.images || []).forEach((item) => {
            const url = this.safeLocalUrl(item.url); if (!url) return;
            const figure = document.createElement("figure");
            const link = document.createElement("a"); link.href = url; link.target = "_blank"; link.rel = "noopener";
            const img = document.createElement("img"); img.src = url; img.alt = item.label || "模型输出"; img.className = "result-preview"; img.loading = "lazy";
            const label = document.createElement("figcaption"); label.textContent = item.label || "查看原图";
            link.append(img); figure.append(link, label); images.append(figure);
        });
        if (images.childElementCount) card.append(images);
        const videoUrl = this.safeLocalUrl(result.video_url);
        if (videoUrl) { const video = document.createElement("video"); video.src = videoUrl; video.controls = true; video.preload = "metadata"; video.className = "result-preview"; card.append(video); }
        const metrics = document.createElement("dl"); metrics.className = "result-metrics";
        const addMetric = (label, raw) => {
            if (raw === null || raw === undefined) return;
            if (typeof raw === "object") {
                if ("value" in raw) addMetric(raw.label || label, `${raw.value ?? "—"}${raw.unit ? ` ${raw.unit}` : ""}`);
                else Object.entries(raw).forEach(([key, value]) => addMetric(key, value));
                return;
            }
            const group = document.createElement("div");
            const name = document.createElement("dt"); name.textContent = label;
            const val = document.createElement("dd"); val.textContent = typeof raw === "number" ? Number(raw.toFixed(3)).toString() : String(raw);
            group.append(name, val); metrics.append(group);
        };
        Object.entries(result.metrics || {}).forEach(([key, value]) => addMetric(key, result.metric_units?.[key] && typeof value === "number" ? `${Number(value.toFixed(3))} ${result.metric_units[key]}` : value));
        if (metrics.childElementCount) card.append(metrics);
        const url = this.safeLocalUrl(result.url || result.report_url);
        if (url) { const link = document.createElement("a"); link.href = url; link.textContent = "打开完整结果 ↗"; card.append(link); }
        return card;
    },
    setBusy(value) {
        this.busy = value;
        this.el("agent-send-button").disabled = value;
        this.el("agent-stop-button").hidden = !value || !this.activeRun;
        this.el("agent-stop-button").disabled = this.activeRun?.state === "cancelling";
        this.el("agent-add-button").disabled = value;
        this.el("agent-chat-input").readOnly = value;
        this.el("agent-chat-form").setAttribute("aria-busy", String(value));
        this.renderAttachments(); this.renderPlugins();
    },
    setMenu(open) {
        this.el("agent-add-menu").hidden = !open;
        this.el("agent-add-button").setAttribute("aria-expanded", String(open));
        if (open) this.el("agent-add-menu").querySelector("button, input")?.focus();
    },
    async loadPlugins() {
        if (this.demoMode) {
            this.plugins = [
                {id: "keypoints", label: "关键点检测", description: "DR / DICOM · 标注与角度测量"},
                {id: "roi", label: "ROI 区域识别", description: "体表照片 · 脊柱区域定位"},
                {id: "gait", label: "步态识别", description: "视频 · 步态跟踪与分析"},
                {id: "pseudo_dr", label: "脊柱伪 DR 生成", description: "Pix2Pix 左半图直推 · 配对图自动取左半"},
            ].map((item) => ({...item, available: true}));
        } else {
            try { this.plugins = (await this.request("/api/agent/plugins")).plugins || []; }
            catch (error) { this.el("agent-plugin-options").textContent = `功能列表暂未读取：${error.message}`; return; }
        }
        this.renderPlugins();
    },
    renderPlugins() {
        const choices = this.el("agent-plugin-options"), chips = this.el("agent-plugin-list");
        choices.replaceChildren(); chips.replaceChildren();
        this.plugins.forEach((plugin) => {
            const label = document.createElement("label"); label.className = "plugin-option";
            const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.dataset.pluginId = plugin.id;
            checkbox.checked = this.selectedPlugins.has(plugin.id); checkbox.disabled = plugin.available === false || this.busy;
            const text = document.createElement("span");
            const title = document.createElement("strong"); title.textContent = plugin.label;
            const detail = document.createElement("small"); detail.textContent = plugin.available === false ? (plugin.reason || "模型尚未就绪") : plugin.description;
            text.append(title, detail); label.append(checkbox, text); choices.append(label);
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) this.selectedPlugins.add(plugin.id); else this.selectedPlugins.delete(plugin.id);
                this.pendingRequest = null; this.syncAttachmentModalities(); this.renderPlugins();
                this.el("agent-plugin-options").querySelector(`[data-plugin-id="${plugin.id}"]`)?.focus();
            });
            if (checkbox.checked) {
                const chip = document.createElement("button"); chip.type = "button"; chip.className = "plugin-chip selected-plugin";
                chip.textContent = `${plugin.label} ×`; chip.disabled = this.busy; chip.setAttribute("aria-label", `移除${plugin.label}`);
                chip.addEventListener("click", () => { this.selectedPlugins.delete(plugin.id); this.pendingRequest = null; this.syncAttachmentModalities(); this.renderPlugins(); }); chips.append(chip);
            }
        });
        chips.hidden = !chips.childElementCount;
    },
    syncAttachmentModalities() {
        const imagePlugin = this.selectedPlugins.has("roi") || this.selectedPlugins.has("pseudo_dr");
        const xrayPlugin = this.selectedPlugins.has("keypoints");
        // Selecting a plugin after attaching its image must work too. Preserve
        // explicit user choices, and do not guess when both modalities are used.
        if (imagePlugin !== xrayPlugin) {
            const modality = imagePlugin ? "rgb" : "xray";
            this.attachments.forEach((item) => {
                if (item.image && !item.modalityExplicit && item.modality !== modality) {
                    item.modality = modality; item.uploaded = null;
                }
            });
        }
        this.renderAttachments();
    },
    addFiles(files) {
        if (this.busy) return;
        for (const file of files) {
            if (this.attachments.length >= 8) { this.flash("一次最多添加 8 个文件。", true); break; }
            if (this.attachments.some((item) => item.file.name === file.name && item.file.size === file.size && item.file.lastModified === file.lastModified)) continue;
            if (!/\.(png|jpe?g|bmp|webp|dcm|dicom|mp4|mov|m4v|avi|mkv|webm|pdf|txt|md|csv|docx)$/i.test(file.name)) { this.flash(`暂不支持 ${file.name} 的文件类型。`, true); continue; }
            const video = /\.(mp4|mov|m4v|avi|mkv|webm)$/i.test(file.name);
            const image = /\.(png|jpe?g|bmp|webp)$/i.test(file.name);
            const dicom = /\.(dcm|dicom)$/i.test(file.name);
            const modality = video ? "video" : dicom ? "xray" : image ? ((this.selectedPlugins.has("roi") || this.selectedPlugins.has("pseudo_dr")) && !this.selectedPlugins.has("keypoints") ? "rgb" : "xray") : "document";
            this.attachments.push({id: crypto.randomUUID(), file, modality, image, preview: image ? URL.createObjectURL(file) : "", uploaded: null});
        }
        this.pendingRequest = null; this.renderAttachments(); this.setMenu(false); this.el("agent-chat-input").focus();
    },
    renderAttachments() {
        const list = this.el("agent-attachment-list"); list.replaceChildren();
        this.attachments.forEach((item) => {
            const card = document.createElement("div"); card.className = "attachment-chip";
            if (item.preview) { const img = document.createElement("img"); img.src = item.preview; img.alt = ""; card.append(img); }
            const info = document.createElement("span"); info.className = "attachment-meta";
            const name = document.createElement("strong"); name.textContent = item.file.name;
            const size = document.createElement("small"); size.textContent = `${(item.file.size / 1024 / (item.file.size > 1024 * 1024 ? 1024 : 1)).toFixed(1)} ${item.file.size > 1024 * 1024 ? "MB" : "KB"}`;
            info.append(name, size); card.append(info);
            if (item.image) {
                const type = document.createElement("select"); type.setAttribute("aria-label", `${item.file.name} 的资料类型`); type.disabled = this.busy;
                [["xray", "DR / X 光"], ["rgb", "体表 / Pix2Pix"]].forEach(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; type.append(option); });
                type.value = item.modality;
                type.addEventListener("change", () => { item.modality = type.value; item.modalityExplicit = true; item.uploaded = null; this.pendingRequest = null; }); card.append(type);
            }
            const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×"; remove.disabled = this.busy; remove.setAttribute("aria-label", `移除 ${item.file.name}`);
            remove.addEventListener("click", () => { if (item.preview) URL.revokeObjectURL(item.preview); this.attachments = this.attachments.filter((entry) => entry !== item); this.pendingRequest = null; this.renderAttachments(); });
            card.append(remove); list.append(card);
        });
        list.hidden = !list.childElementCount;
    },
    async sendToolRun(message, attachments) {
        const caseId = this.workspace.case.id;
        const status = this.el("agent-run-status"); status.hidden = false; status.textContent = "正在上传资料…";
        for (const item of attachments) {
            if (item.uploaded) continue;
            const body = new FormData(); body.append("file", item.file); body.append("modality", item.modality);
            const data = await this.request(`/api/cases/${encodeURIComponent(caseId)}/agent/attachments`, {method: "POST", body});
            item.uploaded = data.attachment;
        }
        const payload = {message, attachment_ids: attachments.map((item) => item.uploaded.id), plugin_ids: [...this.selectedPlugins]};
        const signature = JSON.stringify(payload);
        if (this.pendingRequest?.signature !== signature) this.pendingRequest = {signature, key: crypto.randomUUID()};
        status.textContent = "正在安排分析步骤…";
        const data = await this.request(`/api/cases/${encodeURIComponent(caseId)}/agent/tool-runs`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...payload, idempotency_key: this.pendingRequest.key})});
        this.activeRun = data.run;
        try { sessionStorage.setItem(`mindspine-run:${caseId}`, this.activeRun.id); } catch {}
        this.attachments.forEach((item) => { if (item.preview) URL.revokeObjectURL(item.preview); });
        this.attachments = []; this.pendingRequest = null; this.selectedPlugins.clear();
        this.setBusy(true); this.renderRun(this.activeRun);
        await this.pollRun();
    },
    renderRun(run) {
        const node = this.el("agent-run-status"); node.hidden = false; node.replaceChildren();
        const title = document.createElement("strong"); title.textContent = this.runStateName(run.state);
        node.append(title);
        this.el("agent-stop-button").disabled = run.state === "cancelling";
        const list = document.createElement("ol"); list.className = "step-list";
        (run.steps || []).forEach((step) => {
            const row = document.createElement("li"); row.dataset.state = step.state;
            row.textContent = `${{completed: "✓", running: "◌", failed: "!", cancelled: "−", queued: "○", skipped: "−"}[step.state] || "○"} ${step.label || step.plugin_id}${step.message ? ` · ${step.message}` : ""}`;
            list.append(row);
        });
        node.append(list);
        (run.errors || []).forEach((error) => { const note = document.createElement("p"); note.textContent = typeof error === "string" ? error : error.message || "执行失败"; node.append(note); });
    },
    async pollRun() {
        const run = this.activeRun; if (!run) return;
        clearTimeout(this.pollTimer);
        try {
            const data = await this.request(`/api/cases/${encodeURIComponent(this.workspace.case.id)}/agent/tool-runs/${encodeURIComponent(run.id)}`);
            this.activeRun = data.run; this.renderRun(data.run);
            if (["completed", "failed", "cancelled", "needs_review", "rejected"].includes(data.run.state)) {
                try { sessionStorage.removeItem(`mindspine-run:${this.workspace.case.id}`); } catch {}
                this.activeRun = null; this.setBusy(false); this.scrollNext = true;
                await this.load();
                this.flash(data.run.state === "failed" ? (data.run.message || "部分步骤执行失败，请查看任务详情。") : "", data.run.state === "failed");
                return;
            }
            this.pollTimer = setTimeout(() => this.pollRun(), 1400);
        } catch (error) {
            if ([401, 403, 404].includes(error.status)) {
                try { sessionStorage.removeItem(`mindspine-run:${this.workspace.case.id}`); } catch {}
                this.activeRun = null; this.setBusy(false); this.el("agent-run-status").hidden = true;
                this.flash(error.message, true); return;
            }
            this.flash(`任务状态暂未更新：${error.message}。正在重连。`, true);
            this.pollTimer = setTimeout(() => this.pollRun(), 4500);
        }
    },
    async stopRun() {
        if (!this.activeRun) return;
        const button = this.el("agent-stop-button"); button.disabled = true;
        try {
            const data = await this.request(`/api/cases/${encodeURIComponent(this.workspace.case.id)}/agent/tool-runs/${encodeURIComponent(this.activeRun.id)}`, {method: "DELETE"});
            this.activeRun = data.run; this.renderRun(data.run); await this.pollRun();
        } catch (error) { this.flash(error.message, true); }
        finally { button.disabled = this.activeRun?.state === "cancelling"; }
    },
    openQuestionnaire() {
        if (!this.workspace?.case?.id) { this.flash("请先等待病例加载完成。", true); return; }
        return window.MindSpineQuestionnaire?.open({caseId: this.workspace.case.id,
            answers: this.workspace.questionnaire?.answers, protocolVersion: this.workspace.questionnaire?.protocol_version,
            demoMode: this.demoMode, onSaved: async () => { this.scrollNext = true; await this.load(); }});
    },
    attachComposer() {
        const input = this.el("agent-chat-input");
        input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 120)}px`; });
        input.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); this.el("agent-chat-form").requestSubmit(); }
        });
        input.addEventListener("paste", (event) => {
            const files = [...(event.clipboardData?.files || [])];
            if (files.length) { event.preventDefault(); this.addFiles(files); }
        });
        const form = this.el("agent-chat-form");
        ["dragenter", "dragover"].forEach((type) => form.addEventListener(type, (event) => { event.preventDefault(); form.classList.add("dragging"); }));
        ["dragleave", "drop"].forEach((type) => form.addEventListener(type, (event) => {
            event.preventDefault(); form.classList.remove("dragging");
            if (type === "drop") this.addFiles([...(event.dataTransfer?.files || [])]);
        }));
        this.el("agent-add-button").addEventListener("click", () => this.setMenu(this.el("agent-add-menu").hidden));
        document.querySelectorAll("[data-attach-type]").forEach((button) => button.addEventListener("click", () => {
            const picker = this.el("agent-file-input");
            picker.accept = {image: ".png,.jpg,.jpeg,.bmp,.webp,.dcm,.dicom", video: ".mp4,.mov,.m4v,.avi,.mkv,.webm", file: ".pdf,.txt,.md,.csv,.docx,.dcm,.dicom"}[button.dataset.attachType];
            picker.click();
        }));
        this.el("agent-file-input").addEventListener("change", (event) => { this.addFiles([...event.target.files]); event.target.value = ""; });
        document.addEventListener("click", (event) => {
            if (!this.el("agent-add-menu").contains(event.target) && !this.el("agent-add-button").contains(event.target)) this.setMenu(false);
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !this.el("agent-add-menu").hidden) { this.setMenu(false); this.el("agent-add-button").focus(); }
        });
        this.el("agent-stop-button").addEventListener("click", () => this.stopRun());
        this.el("agent-safety-link")?.addEventListener("click", (event) => { event.preventDefault(); this.openQuestionnaire(); });
        this.el("agent-web-search")?.addEventListener("change", (event) => {
            if (event.target.checked) this.flash(this.workspace?.consent?.external_ai_allowed
                ? "将检索公开脊柱健康文献；仅发送概括主题，不发送病例原文或图片。"
                : "联网与 DeepSeek 需要先在“AI 与资料设置”中授权。未授权不会发送数据。");
            else this.flash("");
        });
        this.el("agent-new-chat").addEventListener("click", () => {
            if (this.busy) return;
            this.messageStart = (this.workspace?.messages || []).length;
            this.renderMessages(this.workspace?.messages || []);
            this.el("agent-run-status").hidden = true; this.flash(""); input.focus();
        });
        this.el("agent-sidebar-toggle").addEventListener("click", () => {
            document.body.classList.toggle("agent-sidebar-open");
            this.el("agent-sidebar-toggle").setAttribute("aria-expanded", String(document.body.classList.contains("agent-sidebar-open")));
        });
        document.querySelectorAll(".suggestion[data-prompt]").forEach((button) => button.addEventListener("click", () => { input.value = button.dataset.prompt || ""; input.focus(); input.dispatchEvent(new Event("input")); }));
        const requested = new URLSearchParams(window.location.search).get("prompt");
        if (requested) {
            input.value = requested.slice(0, 1600); input.dispatchEvent(new Event("input"));
            const cleanUrl = new URL(window.location.href);
            cleanUrl.searchParams.delete("prompt");
            window.history.replaceState({}, "", cleanUrl.pathname + cleanUrl.search);
        }
    },
    async init() {
        this.el("agent-chat-form").addEventListener("submit", (event) => this.send(event));
        this.el("btn-agent-toggle-consent").addEventListener("click", () => this.toggleConsent());
        this.attachComposer(); await this.load(); await this.loadPlugins();
        let runId;
        try { runId = sessionStorage.getItem(`mindspine-run:${this.workspace?.case?.id}`); } catch {}
        runId ||= (this.workspace?.agent_tool_runs || []).find((run) => ["queued", "running", "cancelling"].includes(run.state))?.id;
        if (runId && !this.demoMode && this.workspace?.case?.id) {
            this.activeRun = {id: runId}; this.setBusy(true); this.pollRun();
        }
        window.addEventListener("beforeunload", () => { clearTimeout(this.pollTimer); this.attachments.forEach((item) => { if (item.preview) URL.revokeObjectURL(item.preview); }); });
    },
};
document.addEventListener("DOMContentLoaded", () => healthAgent.init());
