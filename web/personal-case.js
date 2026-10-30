const personalCaseApp = {
    data: null,
    agentWorkspace: null,
    agentActionTokens: new Map(),
    activeTrend: "dr_ll",
    activeFilter: "all",
    demoMode: false,

    el(id) {
        return document.getElementById(id);
    },

    init() {
        this.bindClick("btn-case-edit-profile", () => this.openModal("profile"));
        this.bindClick("btn-case-new-clinical", () => this.openModal("clinical"));
        this.bindClick("btn-case-new-checkin", () => this.openModal("checkin"));
        this.bindClick("btn-agent-questionnaire", () => this.openModal("questionnaire"));
        this.bindClick("btn-case-view-questionnaire", () => this.openModal("questionnaire"));
        this.bindClick("btn-agent-toggle-consent", () => this.toggleAgentConsent());
        this.bindClick("btn-case-print", () => window.print());
        this.bindClick("btn-case-modal-close", () => this.closeModal());

        this.el("case-modal-layer")?.addEventListener("click", (event) => {
            if (event.target === this.el("case-modal-layer")) this.closeModal();
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") this.closeModal();
        });

        this.el("case-trend-tabs")?.addEventListener("click", (event) => {
            const button = event.target.closest("button[data-trend]");
            if (!button) return;
            this.activeTrend = button.dataset.trend;
            this.renderTrend();
        });
        this.el("case-filter-tabs")?.addEventListener("click", (event) => {
            const button = event.target.closest("button[data-filter]");
            if (!button) return;
            this.activeFilter = button.dataset.filter;
            this.renderEntries();
        });

        this.el("case-profile-form")?.addEventListener("submit", (event) => this.saveProfile(event));
        this.el("case-clinical-form")?.addEventListener("submit", (event) => this.saveClinical(event));
        this.el("case-checkin-form")?.addEventListener("submit", (event) => this.saveCheckin(event));
        this.el("case-questionnaire-form")?.addEventListener("submit", (event) => this.saveQuestionnaire(event));
        this.el("agent-chat-form")?.addEventListener("submit", (event) => this.sendAgentMessage(event));
        this.requestedModal = new URLSearchParams(window.location.search).get("modal");
        this.load();
    },

    bindClick(id, handler) {
        this.el(id)?.addEventListener("click", handler);
    },

    readOnlyDemo() {
        if (!this.demoMode) return false;
        this.flash("演示病例为只读数据；不会修改你的真实档案。", "success");
        return true;
    },

    async request(url, options = {}) {
        const response = await fetch(url, { credentials: "same-origin", ...options });
        let data = {};
        try {
            data = await response.json();
        } catch (_) {
            // Let the HTTP error below provide a safe message for non-JSON gateway responses.
        }
        if (!response.ok || data.code !== 200) {
            throw new Error(data.message || data.detail || `请求未成功（HTTP ${response.status}）`);
        }
        return data;
    },

    async load() {
        try {
            this.demoMode = Boolean(window.MindSpineDemo?.usePersonalCase?.());
            const data = this.demoMode ? {} : await this.request("/api/personal-case");
            this.data = this.demoMode ? window.MindSpineDemo.personalCase() : data.case;
            window.MindSpineDemo?.decorateLinks?.();
            if (this.demoMode) window.MindSpineDemo.revealBanner();
            this.render();
        } catch (error) {
            this.flash(error.message || "病例档案暂时无法读取。", "error");
        }
        await this.loadAgentWorkspace();
        if (this.requestedModal && ["profile", "clinical", "checkin", "questionnaire"].includes(this.requestedModal)) {
            this.openModal(this.requestedModal);
            this.requestedModal = null;
            const cleanUrl = new URL(window.location.href);
            cleanUrl.searchParams.delete("modal");
            window.history.replaceState({}, "", cleanUrl.pathname + cleanUrl.search);
        }
    },

    async loadAgentWorkspace() {
        if (this.demoMode) {
            this.agentWorkspace = window.MindSpineDemo.workspace();
            this.renderAgent();
            return;
        }
        try {
            const caseId = new URLSearchParams(window.location.search).get("case_id");
            const data = await this.request(`/api/cases/${caseId ? encodeURIComponent(caseId) : "default"}/workspace`);
            this.agentWorkspace = data.workspace;
            window.MindSpineCaseContext?.setCaseId?.(this.agentWorkspace?.case?.id);
            this.renderAgent();
        } catch (error) {
            const node = this.el("agent-chat-log");
            if (node) node.textContent = error.message || "Agent 工作台暂时无法读取。";
        }
    },

    render() {
        if (!this.data) return;
        this.renderProfile();
        this.renderOverview();
        this.renderTrend();
        this.renderEntries();
        this.renderQuestionnaireSummary();
    },

    renderAgent() {
        const workspace = this.agentWorkspace;
        if (!workspace) return;
        const questionnaire = workspace.questionnaire;
        const triage = questionnaire?.triage || {};
        const riskNames = { routine: "常规随访", attention: "谨慎随访", soon: "尽快线下复核", urgent: "优先线下评估", incomplete: "信息不完整", unknown: "待完成问卷" };
        const risk = triage.risk_level || "unknown";
        this.el("agent-triage-status").textContent = riskNames[risk] || "待完成问卷";
        this.el("agent-triage-note").textContent = triage.message || "完成问卷后才会开启相应的自动化范围。";
        const consentEnabled = Boolean(workspace.consent?.external_ai_allowed);
        this.el("agent-consent-status").textContent = workspace.demo ? "演示本地只读" : (consentEnabled ? "AI 病例解读已开启" : "仅本地");
        this.el("agent-consent-button-label").textContent = workspace.demo ? "演示数据不外发" : (consentEnabled ? "关闭 AI 同意" : "开启 AI 病例解读");
        const reviews = workspace.review_tasks || [];
        this.el("agent-review-count").textContent = `${reviews.length} 项`;
        this.el("agent-kb-status").textContent = `知识库 ${workspace.knowledge?.source_count || 0} 个来源`;
        this.renderAgentMessages(workspace.messages || []);
        this.renderAgentActions(workspace.actions || [], Boolean(workspace.demo));
        this.renderGaitAssessments(workspace.gait_jobs || [], workspace.case?.id);
        this.renderCurrentExam(workspace);
        this.renderThirdPartyStatus(workspace.third_party_reproduction || []);
        this.renderQuestionnaireSummary();
    },

    renderCurrentExam(workspace) {
        const title = this.el("case-current-exam-title");
        if (title) title.textContent = workspace.case?.title || "本次检查资料";
        const list = this.el("case-current-exam-entries");
        if (!list) return;
        list.replaceChildren();
        const entries = workspace.legacy_timeline || [];
        this.el("case-current-exam-count").textContent = String(entries.length);
        entries.forEach((entry) => list.append(this.entryElement(entry)));
        if (!entries.length) { const empty = document.createElement("p"); empty.className = "ms-note"; empty.textContent = "当前检查尚无影像或随访记录。账户完整历史仍保留在下方。"; list.append(empty); }
    },

    renderGaitAssessments(jobs, caseId) {
        const list = this.el("case-gait-list");
        if (!list) return;
        list.replaceChildren();
        const labels = { completed: "已完成", queued: "排队中", running: "分析中", rejected: "需调整素材", failed: "失败", cancelled: "已取消" };
        jobs.forEach((job) => {
            const row = document.createElement("article"); row.className = "ms-panel ms-panel-body";
            const top = document.createElement("div"); top.className = "ms-row between";
            const title = document.createElement("h3"); title.textContent = job.filename || "步态评估";
            const badge = document.createElement("span"); badge.className = `ms-chip ${job.review?.selected ? "good" : ""}`;
            badge.textContent = job.review?.selected ? "本次采用结果" : labels[job.status] || job.status;
            top.append(title, badge);
            const notes = document.createElement("p"); notes.className = "ms-note"; notes.style.marginTop = "8px"; notes.textContent = job.review?.notes || job.message || "";
            const link = document.createElement("a"); link.className = "ms-button"; link.style.marginTop = "12px";
            link.href = `gait-workspace.html?job_id=${encodeURIComponent(job.id || job.job_id)}&case_id=${encodeURIComponent(job.case_id || caseId)}`;
            link.textContent = "查看结果与过程 ↗"; row.append(top, notes, link); list.append(row);
        });
        if (!jobs.length) { const empty = document.createElement("p"); empty.className = "ms-note"; empty.textContent = "当前病例尚无步态评估。"; list.append(empty); }
        const report = this.el("case-gait-report");
        if (report) { report.hidden = !caseId || this.demoMode; report.href = `/api/cases/${encodeURIComponent(caseId)}/report.pdf`; }
    },

    renderQuestionnaireSummary() {
        const questionnaire = this.agentWorkspace?.questionnaire;
        const answers = questionnaire?.answers || {};
        const triage = questionnaire?.triage || {};
        const riskNames = { routine: "常规随访", attention: "谨慎随访", soon: "尽快线下复核", urgent: "优先线下评估", incomplete: "信息不完整" };
        const redFlags = Array.isArray(answers.red_flags) ? answers.red_flags : [];
        const available = Boolean(questionnaire);
        this.el("case-questionnaire-status").textContent = available ? `${riskNames[triage.risk_level] || "已完成"} · ${this.displayDate(questionnaire.created_at)}` : "尚未填写";
        this.el("case-questionnaire-adult-status").textContent = answers.consent_confirmed || answers.adult_confirmed ? "已确认填写" : "待确认";
        const hasPain = answers.pain_score != null && answers.pain_score !== "" && Number.isFinite(Number(answers.pain_score));
        const hasActivityLimit = answers.activity_limit != null && answers.activity_limit !== "" && Number.isFinite(Number(answers.activity_limit));
        this.el("case-questionnaire-pain-status").textContent = hasPain ? `${answers.pain_score} / 10` : "--";
        const functions = answers.daily_function || {};
        const impacted = Object.values(functions).filter(v => !["none", "unknown"].includes(v)).length;
        this.el("case-questionnaire-activity-status").textContent = Object.keys(functions).length ? `${impacted} 项生活活动受影响（逐项记录）` : hasActivityLimit ? `${answers.activity_limit} / 10（旧版自评）` : "--";
        this.el("case-questionnaire-redflag-status").textContent = available ? (redFlags.length ? `${redFlags.length} 项需优先复核` : triage.missing_or_uncertain?.length ? "仍有信息未确认" : "0 项命中（问卷范围内）") : "--";
        this.el("case-questionnaire-analysis").textContent = triage.analysis || triage.message || "尚未建立问卷记录。完成最小安全分流后，这里会展示规则范围、填写情况和后续限制。";
    },

    renderAgentMessages(messages) {
        const log = this.el("agent-chat-log");
        if (!log) return;
        log.replaceChildren();
        if (!messages.length) {
            const empty = document.createElement("p");
            empty.className = "text-sm leading-relaxed text-[#8A8F98]";
            empty.textContent = "可以先完成健康问卷，或询问下一步该补充什么资料。";
            log.append(empty);
            return;
        }
        messages.forEach((message) => {
            const card = document.createElement("article");
            const isAssistant = message.role === "assistant";
            card.className = `rounded-xl border p-3 text-sm leading-relaxed ${isAssistant ? "border-[#5E6AD2]/25 bg-[#5E6AD2]/[.09] text-[#DDE1EB]" : "border-white/8 bg-white/[.035] text-[#BFC7FF]"}`;
            const heading = document.createElement("div");
            heading.className = "mb-1 text-[10px] tracking-widest uppercase opacity-70";
            heading.textContent = isAssistant ? "Spine health agent" : "你";
            const content = document.createElement("p");
            content.textContent = message.content;
            card.append(heading, content);
            if (isAssistant && Array.isArray(message.evidence) && message.evidence.length) {
                const references = document.createElement("div");
                references.className = "mt-3 flex flex-wrap gap-2";
                message.evidence.forEach((evidence) => {
                    const source = document.createElement("a");
                    source.className = "rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[10px] text-[#DDE1EB] hover:border-[#8896F9]";
                    source.textContent = evidence.title || "依据来源";
                    source.target = "_blank";
                    source.rel = "noreferrer";
                    source.href = String(evidence.source_url || "#").startsWith("https://") ? evidence.source_url : "#";
                    references.append(source);
                });
                card.append(references);
            }
            log.append(card);
        });
        log.scrollTop = log.scrollHeight;
    },

    renderAgentActions(actions, demo = false) {
        const list = this.el("agent-action-list");
        if (!list) return;
        list.replaceChildren();
        const draftActions = actions.filter((action) => action.state === "draft");
        const completedActions = demo ? actions.filter((action) => action.state === "completed") : [];
        if (!draftActions.length && !completedActions.length) {
            const empty = document.createElement("p");
            empty.className = "text-sm text-[#8A8F98]";
            empty.textContent = "暂无待确认动作。已确认的任务和复核状态会保留在病例时间线中。";
            list.append(empty);
            return;
        }
        completedActions.forEach((action) => {
            const card = document.createElement("article");
            card.className = "rounded-xl border border-[#22C55E]/25 bg-[#22C55E]/[.06] p-3";
            const title = document.createElement("strong");
            title.className = "block text-xs text-[#D8FBE6]";
            title.textContent = `${this.agentActionLabel(action.kind)} · 已完成`;
            const reason = document.createElement("p");
            reason.className = "mt-1 text-[11px] leading-relaxed text-[#B4D9C2]";
            reason.textContent = action.reason || "已写入演示病例时间线。";
            card.append(title, reason);
            list.append(card);
        });
        draftActions.forEach((action) => {
            const card = document.createElement("article");
            card.className = "rounded-xl border border-[#A393FF]/22 bg-[#A393FF]/[.07] p-3";
            const title = document.createElement("strong");
            title.className = "block text-xs text-[#DDE1EB]";
            title.textContent = this.agentActionLabel(action.kind);
            const reason = document.createElement("p");
            reason.className = "mt-1 text-[11px] leading-relaxed text-[#B4B9C4]";
            reason.textContent = action.reason;
            const button = document.createElement("button");
            button.type = "button";
            button.className = "mt-3 rounded-lg border border-[#8896F9]/35 bg-[#5E6AD2]/20 px-3 py-2 text-xs text-white hover:bg-[#5E6AD2]/35 transition-all";
            const token = this.agentActionTokens.get(action.id);
            if (!token) {
                button.disabled = true;
                button.classList.add("opacity-45", "cursor-not-allowed");
                button.textContent = "请在当前对话中重新发起确认";
            } else {
                button.textContent = "确认并继续";
                button.addEventListener("click", () => this.confirmAgentAction(action, token));
            }
            card.append(title, reason, button);
            list.append(card);
        });
    },

    renderThirdPartyStatus(projects) {
        const node = this.el("agent-third-party-status");
        if (!node) return;
        const enabled = projects.filter((project) => String(project.status).includes("implemented")).length;
        node.textContent = `${projects.length} 个上游项目已登记；${enabled} 个基线能力可用，其余保持隔离复现或人工复核状态。`;
    },

    agentActionLabel(kind) {
        return {
            run_video_screening: "运行视频动作/步态评估",
            build_surface_archive: "建立近似体表视觉档案",
            run_ctmr_segmentation: "创建 CT/MR 专业影像任务",
            run_multicam_kinematics: "创建多相机运动学任务",
            save_rehab_plan: "保存个性化康复计划",
            request_clinical_review: "创建人工复核任务",
            export_case: "导出病例资料",
        }[kind] || "执行病例动作";
    },

    renderProfile() {
        const profile = this.data.profile || {};
        const name = profile.display_name || "我的个人病例";
        this.el("case-title").textContent = name;
        this.el("case-avatar").textContent = name.trim().slice(0, 1) || "我";

        const pieces = [];
        const age = this.ageFromDate(profile.birth_date);
        if (age !== null) pieces.push(`${age} 岁`);
        const sexNames = { female: "女", male: "男", other: "其他" };
        if (sexNames[profile.sex]) pieces.push(sexNames[profile.sex]);
        if (profile.height_cm) pieces.push(`${this.number(profile.height_cm)} cm`);
        if (profile.weight_kg) pieces.push(`${this.number(profile.weight_kg)} kg`);
        this.el("case-profile-meta").textContent = pieces.length
            ? pieces.join(" · ")
            : "完善资料后，可开始建立长期随访记录。";
    },

    renderOverview() {
        const overview = this.data.overview || {};
        const latest = overview.latest_entry;
        this.el("case-total-entries").textContent = String(overview.total_entries || 0);
        this.el("case-latest-date").textContent = latest ? this.displayDate(latest.recorded_at) : "--";
        this.el("case-latest-title").textContent = latest ? latest.title : "尚未建立记录";

        const cobb = this.lastSeriesValue("clinical_cobb");
        this.el("case-latest-cobb").innerHTML = cobb ? `${this.number(cobb.value)}<small class="text-sm opacity-60">°</small>` : "--<small class=\"text-sm opacity-60\">°</small>";
        this.el("case-cobb-change").textContent = this.changeText("clinical_cobb", "°", "等待临床复查记录");

        const pain = this.lastSeriesValue("checkin_pain");
        this.el("case-latest-pain").innerHTML = pain ? `${this.number(pain.value)}<small class="text-sm opacity-60">/10</small>` : "--<small class=\"text-sm opacity-60\">/10</small>";
        this.el("case-pain-date").textContent = pain ? `${this.displayDate(pain.date)} 记录` : "等待状态打卡";
    },

    renderTrend() {
        const definitions = {
            dr_ll: { label: "DR · LL", unit: "°" },
            clinical_cobb: { label: "临床 · Cobb 角", unit: "°" },
            checkin_pain: { label: "状态 · 疼痛评分", unit: "/10" },
            video_positive: { label: this.demoMode ? "视频 · 协议质量" : "视频 · 模型分数", unit: "%", percent: true },
        };
        const definition = definitions[this.activeTrend] || definitions.dr_ll;
        const points = (this.data.trends?.[this.activeTrend] || []).map((point) => ({
            ...point,
            value: definition.percent && Number(point.value) <= 1 ? Number(point.value) * 100 : Number(point.value),
        }));
        this.el("case-trend-label").textContent = definition.label;
        this.el("case-trend-latest").textContent = points.length
            ? `${this.number(points.at(-1).value)}${definition.unit}`
            : "--";
        this.el("case-trend-delta").textContent = this.seriesChange(points, definition.unit, "等待同类记录");

        this.el("case-trend-tabs").querySelectorAll("button[data-trend]").forEach((button) => {
            button.setAttribute("aria-selected", String(button.dataset.trend === this.activeTrend));
        });
        const hasChart = points.length >= 2;
        this.el("case-trend-empty").classList.toggle("hidden", hasChart);
        this.el("case-trend-chart").classList.toggle("hidden", !hasChart);
        if (hasChart) this.drawTrend(points, definition.unit);
    },

    drawTrend(points, unit) {
        const width = 700;
        const height = 220;
        const padding = { left: 52, right: 20, top: 18, bottom: 35 };
        const values = points.map((point) => point.value);
        const minimum = Math.min(...values);
        const maximum = Math.max(...values);
        const spread = Math.max(maximum - minimum, Math.max(Math.abs(maximum) * 0.1, 1));
        const low = minimum - spread * 0.18;
        const high = maximum + spread * 0.18;
        const x = (index) => padding.left + ((width - padding.left - padding.right) * index) / (points.length - 1);
        const y = (value) => padding.top + ((high - value) / (high - low)) * (height - padding.top - padding.bottom);
        const svg = "http://www.w3.org/2000/svg";
        const grid = this.el("case-trend-grid");
        const labels = this.el("case-trend-labels");
        const pointsLayer = this.el("case-trend-points");
        grid.replaceChildren();
        labels.replaceChildren();
        pointsLayer.replaceChildren();

        for (let index = 0; index < 4; index += 1) {
            const ratio = index / 3;
            const value = high - (high - low) * ratio;
            const yy = padding.top + (height - padding.top - padding.bottom) * ratio;
            const line = document.createElementNS(svg, "line");
            line.setAttribute("x1", String(padding.left));
            line.setAttribute("x2", String(width - padding.right));
            line.setAttribute("y1", String(yy));
            line.setAttribute("y2", String(yy));
            line.setAttribute("stroke", "rgba(255,255,255,.10)");
            line.setAttribute("stroke-dasharray", "4 5");
            grid.append(line);
            const label = document.createElementNS(svg, "text");
            label.setAttribute("x", "0");
            label.setAttribute("y", String(yy + 4));
            label.setAttribute("fill", "#8A8F98");
            label.setAttribute("font-size", "10");
            label.textContent = `${this.number(value)}${unit}`;
            labels.append(label);
        }
        this.el("case-trend-line").setAttribute("points", points.map((point, index) => `${x(index)},${y(point.value)}`).join(" "));
        points.forEach((point, index) => {
            const dot = document.createElementNS(svg, "circle");
            dot.setAttribute("class", "trend-point");
            dot.setAttribute("cx", String(x(index)));
            dot.setAttribute("cy", String(y(point.value)));
            dot.setAttribute("r", "5");
            const title = document.createElementNS(svg, "title");
            title.textContent = `${this.displayDate(point.date)}：${this.number(point.value)}${unit}`;
            dot.append(title);
            pointsLayer.append(dot);
        });
        const labelIndexes = [...new Set([0, Math.round((points.length - 1) / 2), points.length - 1])];
        labelIndexes.forEach((index) => {
            const label = document.createElementNS(svg, "text");
            label.setAttribute("x", String(x(index)));
            label.setAttribute("y", String(height - 8));
            label.setAttribute("text-anchor", index === 0 ? "start" : index === points.length - 1 ? "end" : "middle");
            label.setAttribute("fill", "#8A8F98");
            label.setAttribute("font-size", "10");
            label.textContent = this.displayDate(points[index].date);
            labels.append(label);
        });
    },

    renderEntries() {
        const list = this.el("case-entry-list");
        const entries = (this.data.entries || []).filter((entry) => this.matchesFilter(entry));
        list.replaceChildren();
        this.el("case-entry-empty").classList.toggle("hidden", entries.length > 0);
        this.el("case-filter-tabs").querySelectorAll("button[data-filter]").forEach((button) => {
            button.setAttribute("aria-selected", String(button.dataset.filter === this.activeFilter));
        });
        entries.forEach((entry) => list.append(this.entryElement(entry)));
    },

    matchesFilter(entry) {
        return this.activeFilter === "all" || this.activeFilter.split(",").includes(entry.kind);
    },

    entryElement(entry) {
        const styles = {
            rgb: ["fa-person-rays", "text-[#BFC7FF]", "bg-[#5E6AD2]/15 border-[#5E6AD2]/30"],
            dr: ["fa-x-ray", "text-[#DDE1EB]", "bg-white/5 border-white/15"],
            video: ["fa-person-walking", "text-[#86EFAC]", "bg-[#22C55E]/10 border-[#22C55E]/25"],
            surface: ["fa-cubes", "text-[#C4B5FD]", "bg-[#8B5CF6]/10 border-[#8B5CF6]/25"],
            clinical: ["fa-stethoscope", "text-[#FDE68A]", "bg-[#F59E0B]/10 border-[#F59E0B]/25"],
            checkin: ["fa-heart-pulse", "text-[#F9A8D4]", "bg-[#EC4899]/10 border-[#EC4899]/25"],
        };
        const [icon, iconColor, iconSurface] = styles[entry.kind] || styles.checkin;
        const card = document.createElement("article");
        card.className = "timeline-entry grid grid-cols-[36px_1fr] gap-4 rounded-2xl border border-white/8 bg-black/20 p-4 md:p-5";
        const marker = document.createElement("div");
        marker.className = `h-9 w-9 rounded-xl border grid place-items-center ${iconColor} ${iconSurface}`;
        marker.innerHTML = `<i class=\"fa-solid ${icon}\"></i>`;
        const body = document.createElement("div");
        const top = document.createElement("div");
        top.className = "flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3";
        const main = document.createElement("div");
        const title = document.createElement("h3");
        title.className = "font-medium text-sm text-white";
        title.textContent = entry.title;
        const date = document.createElement("p");
        date.className = "mt-1 text-[11px] text-[#8A8F98]";
        date.textContent = this.displayDate(entry.recorded_at) + (entry.source_filename ? ` · ${entry.source_filename}` : "");
        main.append(title, date);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "case-no-print self-start rounded-lg px-2.5 py-1.5 text-[11px] text-[#8A8F98] hover:text-[#FFB6BD] hover:bg-[#EF4444]/10 transition-all";
        if (this.demoMode) {
            remove.disabled = true;
            remove.classList.add("opacity-50", "cursor-not-allowed");
            remove.innerHTML = '<i class="fa-solid fa-flask mr-1"></i>演示数据';
        } else {
            remove.innerHTML = '<i class="fa-solid fa-trash-can mr-1"></i>删除';
            remove.addEventListener("click", () => this.deleteEntry(entry));
        }
        top.append(main, remove);
        const summary = document.createElement("p");
        summary.className = "mt-3 text-sm leading-relaxed text-[#B4B9C4]";
        summary.textContent = entry.summary || this.defaultSummary(entry);
        const media = this.entryMedia(entry);
        const details = this.entryDetails(entry);
        body.append(top, summary);
        if (media) body.append(media);
        body.append(details);
        card.append(marker, body);
        return card;
    },

    entryMedia(entry) {
        const items = Array.isArray(entry.media) ? entry.media.filter((item) => item?.src) : [];
        if (!items.length) return null;
        const wrap = document.createElement("section");
        wrap.className = "mt-4 grid gap-3 sm:grid-cols-2";
        items.forEach((item) => {
            const figure = document.createElement("figure");
            figure.className = "overflow-hidden rounded-xl border border-white/10 bg-[#080b12]";
            const image = document.createElement("img");
            image.src = item.src;
            image.alt = item.alt || item.label || "病例可视化资料";
            image.loading = "lazy";
            image.className = "block h-44 w-full bg-black object-contain";
            const caption = document.createElement("figcaption");
            caption.className = "grid gap-1 border-t border-white/8 px-3 py-2.5";
            const label = document.createElement("strong");
            label.className = "text-[11px] font-medium text-[#E4E8F8]";
            label.textContent = item.label || "病例资料";
            const note = document.createElement("span");
            note.className = "text-[10px] leading-relaxed text-[#9CA6BB]";
            note.textContent = item.caption || "";
            const tag = document.createElement("span");
            tag.className = "mt-1 w-max rounded-full border border-[#A393FF]/25 bg-[#A393FF]/10 px-2 py-0.5 text-[9px] text-[#DDE1EB]";
            tag.textContent = item.type || "可视化资料";
            caption.append(label, note, tag);
            figure.append(image, caption);
            wrap.append(figure);
        });
        return wrap;
    },

    entryDetails(entry) {
        const data = entry.payload || {};
        const values = [];
        const add = (label, value) => {
            if (value !== null && value !== undefined && value !== "") values.push([label, value]);
        };
        if (entry.kind === "rgb" || entry.kind === "dr") {
            const angles = data.angles || {};
            add("LL", this.withUnit(angles.LL, "°"));
            add("SS / SA", this.withUnit(angles.SSA ?? angles.SS, "°"));
            add("LCA", this.withUnit(angles.HARRISON_A ?? angles.LCA, "°"));
            add("LCI", this.withUnit(angles.LCI, " mm"));
            if (data.detected === false) add("状态", "未获得可测量关键点");
        } else if (entry.kind === "clinical") {
            add("Cobb", this.withUnit(data.cobb_angle, "°"));
            add("ATR", this.withUnit(data.atr_angle, "°"));
            add("曲线位置", data.curve_location);
            add("医生结论", data.diagnosis);
            add("处理建议", data.treatment);
        } else if (entry.kind === "checkin") {
            add("疼痛", this.withUnit(data.pain_score, "/10"));
            add("活动受限", this.withUnit(data.activity_limit, "/10"));
            add("运动", this.withUnit(data.exercise_minutes, " 分钟"));
            add("备注", data.note);
        } else if (entry.kind === "video") {
            add("素材检查", entry.status === "prepared" ? "已完成" : "已记录");
            add("时长", this.withUnit(data.duration_seconds, " 秒"));
            add("分辨率", data.resolution);
            add("筛查", data.screening_label);
        } else if (entry.kind === "surface") {
            add("采集质量", data.quality);
            add("使用边界", data.measurement_boundary);
        }
        const wrap = document.createElement("div");
        if (!values.length) return wrap;
        wrap.className = "mt-3 flex flex-wrap gap-2";
        values.forEach(([label, value]) => {
            const chip = document.createElement("span");
            chip.className = "rounded-lg border border-white/8 bg-white/[.035] px-2.5 py-1 text-[11px] leading-relaxed text-[#DDE1EB]";
            chip.textContent = `${label}：${value}`;
            wrap.append(chip);
        });
        return wrap;
    },

    defaultSummary(entry) {
        const defaults = {
            rgb: "已完成 RGB 个人检测。",
            dr: "已完成 DR / X 光测算。",
            video: "已记录侧位步态视频素材。",
            surface: "已保存体表三维视觉档案。",
            clinical: "已保存一次临床复查。",
            checkin: "已保存一次个人状态打卡。",
        };
        return defaults[entry.kind] || "已保存病例记录。";
    },

    openModal(kind) {
        if (!this.data) return;
        if (kind === "questionnaire" && window.MindSpineQuestionnaire) {
            const record = this.agentWorkspace?.questionnaire;
            window.MindSpineQuestionnaire.open({
                caseId: this.agentWorkspace?.case?.id,
                answers: record?.answers,
                protocolVersion: record?.protocol_version,
                demoMode: this.demoMode,
                onSaved: async () => { await this.loadAgentWorkspace(); },
            });
            return;
        }
        const configuration = {
            profile: ["个人资料", "Profile"],
            clinical: ["录入临床复查", "Clinical follow-up"],
            checkin: ["添加状态打卡", "Daily check-in"],
            questionnaire: ["健康与功能问卷", "Health & function intake"],
        }[kind];
        this.el("case-modal-title").textContent = configuration[0];
        this.el("case-modal-eyebrow").textContent = configuration[1];
        ["profile", "clinical", "checkin", "questionnaire"].forEach((name) => this.el(`case-${name}-form`).classList.toggle("hidden", name !== kind));
        if (kind === "profile") this.fillProfileForm();
        if (kind === "clinical") this.el("case-clinical-date").value = this.today();
        if (kind === "checkin") this.el("case-checkin-date").value = this.today();
        if (kind === "questionnaire") this.fillQuestionnaireForm();
        this.el("case-modal-layer").classList.remove("hidden");
        this.el("case-modal-layer").classList.add("flex");
    },

    closeModal() {
        const layer = this.el("case-modal-layer");
        layer.classList.add("hidden");
        layer.classList.remove("flex");
    },

    fillProfileForm() {
        const profile = this.data.profile || {};
        this.el("case-profile-name").value = profile.display_name || "";
        this.el("case-profile-birth-date").value = profile.birth_date || "";
        this.el("case-profile-sex").value = profile.sex || "undisclosed";
        this.el("case-profile-height").value = profile.height_cm ?? "";
        this.el("case-profile-weight").value = profile.weight_kg ?? "";
        this.el("case-profile-note").value = profile.clinician_note || "";
    },

    fillQuestionnaireForm() {
        const form = this.el("case-questionnaire-form");
        if (!form) return;
        form.reset();
        const answers = this.agentWorkspace?.questionnaire?.answers || {};
        this.el("case-questionnaire-adult").checked = Boolean(answers.consent_confirmed || answers.adult_confirmed);
        this.el("case-questionnaire-pain").value = answers.pain_score ?? "";
        this.el("case-questionnaire-activity").value = answers.activity_limit ?? "";
        const flags = new Set(Array.isArray(answers.red_flags) ? answers.red_flags : []);
        form.querySelectorAll("input[data-red-flag]").forEach((input) => { input.checked = flags.has(input.dataset.redFlag); });
        ["symptom_location", "function_impacts"].forEach((name) => {
            const values = new Set(Array.isArray(answers[name]) ? answers[name] : []);
            form.querySelectorAll(`input[data-intake="${name}"]`).forEach((input) => { input.checked = values.has(input.value); });
        });
        ["duration", "history", "treatment", "goals"].forEach((name) => {
            const field = this.el(`case-questionnaire-${name}`);
            if (field) field.value = answers[name] || "";
        });
        const submit = form.querySelector("button[type=submit]");
        if (submit) {
            submit.disabled = this.demoMode;
            submit.classList.toggle("opacity-50", this.demoMode);
            submit.classList.toggle("cursor-not-allowed", this.demoMode);
            submit.textContent = this.demoMode ? "演示问卷已完成（只读）" : (this.agentWorkspace?.questionnaire ? "更新问卷并重新分流" : "提交问卷并建立分流记录");
        }
    },

    async saveProfile(event) {
        event.preventDefault();
        const body = {
            display_name: this.el("case-profile-name").value,
            birth_date: this.el("case-profile-birth-date").value || null,
            sex: this.el("case-profile-sex").value,
            height_cm: this.optionalNumber("case-profile-height"),
            weight_kg: this.optionalNumber("case-profile-weight"),
            clinician_note: this.el("case-profile-note").value,
        };
        await this.save("/api/personal-case/profile", "PUT", body, "个人资料已保存。");
    },

    async saveClinical(event) {
        event.preventDefault();
        const payload = {
            cobb_angle: this.optionalNumber("case-clinical-cobb"),
            atr_angle: this.optionalNumber("case-clinical-atr"),
            curve_location: this.el("case-clinical-location").value.trim(),
            diagnosis: this.el("case-clinical-diagnosis").value.trim(),
            treatment: this.el("case-clinical-treatment").value.trim(),
        };
        const summary = payload.diagnosis || (payload.cobb_angle !== null ? `临床 Cobb 角 ${this.number(payload.cobb_angle)}°` : "已录入临床复查信息。");
        await this.save("/api/personal-case/entries", "POST", {
            kind: "clinical", status: "manual", recorded_at: this.el("case-clinical-date").value || this.today(),
            title: "临床复查记录", summary, payload,
        }, "临床复查记录已保存。");
    },

    async saveCheckin(event) {
        event.preventDefault();
        const payload = {
            pain_score: this.optionalNumber("case-checkin-pain"),
            activity_limit: this.optionalNumber("case-checkin-activity"),
            exercise_minutes: this.optionalNumber("case-checkin-exercise"),
            note: this.el("case-checkin-note").value.trim(),
        };
        const summary = payload.note || (payload.pain_score !== null ? `疼痛评分 ${this.number(payload.pain_score)} / 10` : "已完成一次个人状态打卡。");
        await this.save("/api/personal-case/entries", "POST", {
            kind: "checkin", status: "manual", recorded_at: this.el("case-checkin-date").value || this.today(),
            title: "个人状态打卡", summary, payload,
        }, "状态打卡已保存。");
    },

    async saveQuestionnaire(event) {
        event.preventDefault();
        if (this.readOnlyDemo()) { this.closeModal(); return; }
        if (!this.el("case-questionnaire-form").reportValidity()) return;
        const caseId = this.agentWorkspace?.case?.id;
        if (!caseId) {
            this.flash("病例工作台尚未加载完成。", "error");
            return;
        }
        const redFlags = [...this.el("case-questionnaire-form").querySelectorAll("input[data-red-flag]:checked")]
            .map((input) => input.dataset.redFlag);
        const answers = {
            consent_confirmed: this.el("case-questionnaire-adult").checked,
            pain_score: this.optionalNumber("case-questionnaire-pain"),
            activity_limit: this.optionalNumber("case-questionnaire-activity"),
            red_flags: redFlags,
            symptom_location: [...this.el("case-questionnaire-form").querySelectorAll('input[data-intake="symptom_location"]:checked')].map((input) => input.value),
            function_impacts: [...this.el("case-questionnaire-form").querySelectorAll('input[data-intake="function_impacts"]:checked')].map((input) => input.value),
            duration: this.el("case-questionnaire-duration")?.value || "",
            history: this.el("case-questionnaire-history")?.value.trim() || "",
            treatment: this.el("case-questionnaire-treatment")?.value.trim() || "",
            goals: this.el("case-questionnaire-goals")?.value.trim() || "",
        };
        try {
            const data = await this.request(`/api/cases/${encodeURIComponent(caseId)}/questionnaire`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ answers, protocol_version: "spine-intake-v2" }),
            });
            this.closeModal();
            await this.loadAgentWorkspace();
            const risk = data.questionnaire?.triage?.risk_level;
            const requiresReview = risk === "urgent" || risk === "soon";
            this.flash(requiresReview ? "问卷已保存，系统已停止自动康复并创建复核提示。" : risk === "incomplete" ? "问卷信息不完整，未开启自动计划。" : "问卷已保存，分流状态已更新。", requiresReview ? "error" : "success");
        } catch (error) {
            this.flash(error.message || "问卷提交失败，请检查填写内容。", "error");
        }
    },

    async toggleAgentConsent() {
        if (this.readOnlyDemo()) return;
        const workspace = this.agentWorkspace;
        const caseId = workspace?.case?.id;
        if (!caseId) return this.flash("病例工作台尚未加载完成。", "error");
        const nextValue = !Boolean(workspace.consent?.external_ai_allowed);
        const prompt = nextValue
            ? "开启后，Agent 会将当前问题、近期对话、检查摘要与检索知识发送给 DeepSeek，用于病例解读与问答。原始影像和视频文件不在本次文本请求中。是否开启？"
            : "关闭后，Agent 不再调用 DeepSeek，之后的回复完全使用本地规则与内容。是否继续？";
        if (!window.confirm(prompt)) return;
        try {
            await this.request(`/api/cases/${encodeURIComponent(caseId)}/consent`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ external_ai_allowed: nextValue }),
            });
            await this.loadAgentWorkspace();
            this.flash(nextValue ? "AI 病例解读已开启。" : "已关闭外部 AI 调用。", "success");
        } catch (error) {
            this.flash(error.message || "同意设置更新失败。", "error");
        }
    },

    async sendAgentMessage(event) {
        event.preventDefault();
        const caseId = this.agentWorkspace?.case?.id;
        const input = this.el("agent-chat-input");
        const message = input?.value.trim();
        if (!caseId || !message) return;
        if (this.demoMode) {
            this.agentWorkspace.messages.push({ id: `demo-case-user-${Date.now()}`, role: "user", content: message, created_at: "2026-08-26" });
            this.agentWorkspace.messages.push(window.MindSpineDemo.agentReply(message));
            input.value = "";
            this.renderAgent();
            this.flash("已生成本地演示回复；未调用外部模型。", "success");
            return;
        }
        try {
            const data = await this.request(`/api/cases/${encodeURIComponent(caseId)}/agent/messages`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message }),
            });
            (data.agent?.proposed_actions || []).forEach((action) => {
                if (action.confirmation_token) this.agentActionTokens.set(action.id, action.confirmation_token);
            });
            input.value = "";
            await this.loadAgentWorkspace();
        } catch (error) {
            this.flash(error.message || "Agent 暂时无法回应。", "error");
        }
    },

    async confirmAgentAction(action, confirmationToken) {
        if (this.readOnlyDemo()) return;
        const caseId = this.agentWorkspace?.case?.id;
        if (!caseId || !window.confirm(`确认“${this.agentActionLabel(action.kind)}”吗？确认后将创建可追踪任务；未验收适配器不会伪造分析结果。`)) return;
        try {
            const data = await this.request(`/api/agent-actions/${encodeURIComponent(action.id)}/confirm`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ case_id: caseId, confirmation_token: confirmationToken }),
            });
            this.agentActionTokens.delete(action.id);
            await this.loadAgentWorkspace();
            const state = data.action?.state;
            this.flash(state === "completed" ? "动作已完成。" : "动作已确认，并已进入受控复核流程。", "success");
        } catch (error) {
            this.flash(error.message || "动作确认失败。", "error");
        }
    },

    async save(url, method, body, successMessage) {
        if (this.readOnlyDemo()) { this.closeModal(); return; }
        try {
            if (url === "/api/personal-case/entries") {
                const caseId = this.agentWorkspace?.case?.id || window.MindSpineCaseContext?.id?.();
                if (caseId) body = { ...body, case_id: caseId };
            }
            await this.request(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
            this.closeModal();
            await this.load();
            this.flash(successMessage, "success");
        } catch (error) {
            this.flash(error.message || "保存失败，请稍后重试。", "error");
        }
    },

    async deleteEntry(entry) {
        if (this.readOnlyDemo()) return;
        if (!window.confirm(`确定删除“${entry.title}”吗？此操作仅会移除此条病例记录。`)) return;
        try {
            await this.request(`/api/personal-case/entries/${encodeURIComponent(entry.id)}`, { method: "DELETE" });
            await this.load();
            this.flash("病例记录已删除。", "success");
        } catch (error) {
            this.flash(error.message || "删除失败，请稍后重试。", "error");
        }
    },

    lastSeriesValue(key) {
        const values = this.data.trends?.[key] || [];
        return values.length ? values.at(-1) : null;
    },

    changeText(key, unit, fallback) {
        return this.seriesChange(this.data.trends?.[key] || [], unit, fallback);
    },

    seriesChange(points, unit, fallback) {
        if (!points || points.length < 2) return fallback;
        const previous = Number(points.at(-2).value);
        const current = Number(points.at(-1).value);
        const change = current - previous;
        const sign = change > 0 ? "+" : "";
        return `较上次 ${sign}${this.number(change)}${unit}`;
    },

    optionalNumber(id) {
        const value = this.el(id).value.trim();
        if (value === "") return null;
        const number = Number(value);
        return Number.isFinite(number) ? number : null;
    },

    withUnit(value, unit) {
        const number = Number(value);
        return Number.isFinite(number) ? `${this.number(number)}${unit}` : null;
    },

    number(value) {
        return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
    },

    displayDate(value) {
        if (!value) return "--";
        if (typeof value === "number" || /^\d{10,13}$/.test(String(value))) {
            const number = Number(value);
            const date = new Date(number < 1e12 ? number * 1000 : number);
            return Number.isNaN(date.getTime()) ? "--" : date.toLocaleDateString("zh-CN");
        }
        const [year, month, day] = String(value).slice(0, 10).split("-");
        return year && month && day ? `${year}.${month}.${day}` : String(value);
    },

    ageFromDate(value) {
        if (!value) return null;
        const birth = new Date(`${value}T00:00:00`);
        if (Number.isNaN(birth.getTime())) return null;
        const now = new Date();
        let age = now.getFullYear() - birth.getFullYear();
        const birthdayReached = now.getMonth() > birth.getMonth() || (now.getMonth() === birth.getMonth() && now.getDate() >= birth.getDate());
        if (!birthdayReached) age -= 1;
        return age >= 0 ? age : null;
    },

    today() {
        return new Date().toISOString().slice(0, 10);
    },

    flash(message, type) {
        let notice = this.el("case-flash");
        if (!notice) {
            notice = document.createElement("div");
            notice.id = "case-flash";
            notice.className = "case-no-print fixed bottom-5 left-1/2 z-[70] -translate-x-1/2 rounded-xl border px-4 py-3 text-sm shadow-2xl backdrop-blur-xl transition-all";
            document.body.append(notice);
        }
        notice.textContent = message;
        notice.className = `case-no-print fixed bottom-5 left-1/2 z-[70] -translate-x-1/2 rounded-xl border px-4 py-3 text-sm shadow-2xl backdrop-blur-xl transition-all ${type === "error" ? "border-[#EF4444]/40 bg-[#2B0A0D]/95 text-[#FFB6BD]" : "border-[#22C55E]/35 bg-[#071D10]/95 text-[#BBF7D0]"}`;
        window.clearTimeout(this.flashTimer);
        this.flashTimer = window.setTimeout(() => notice.remove(), 3600);
    },
};

document.addEventListener("DOMContentLoaded", async () => {
    if (window.MindSpineDemo?.isExplicit?.()) { personalCaseApp.init(); return; }
    const canUseCase = await window.MindSpineAuth?.requireUser?.();
    if (!canUseCase) return;
    personalCaseApp.init();
});
