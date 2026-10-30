/* Shared, local-only adult intake. No model generates questions or scores. */
(() => {
    "use strict";
    let current = null;
    const node = (tag, text, className) => {
        const el = document.createElement(tag);
        if (text != null) el.textContent = text;
        if (className) el.className = className;
        return el;
    };
    const get = (data, path) => path.split(".").reduce((value, key) => value?.[key], data);
    const put = (data, path, value) => {
        const keys = path.split(".");
        if (keys.length === 1) data[keys[0]] = value;
        else (data[keys[0]] ||= {})[keys[1]] = value;
    };
    const present = (field, value) => {
        if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) return "未填写";
        if (field.type === "consent") return value ? "已同意保存" : "未同意";
        const list = Array.isArray(value) ? value : [value === true ? "yes" : value === false ? "no" : value];
        return list.map(v => field.options?.find(o => o.value === v)?.label || String(v)).join("、");
    };
    const isRequired = (field, answers) => Boolean(field.required || (field.required_when && get(answers, field.required_when.field) === field.required_when.value));
    const isValid = (field, value, answers) => {
        if (typeof value === "string") value = value.trim();
        if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) return !isRequired(field, answers);
        if (field.type === "consent") return value === true;
        if (field.type === "choice") return field.options.some(o => o.value === (value === true ? "yes" : value === false ? "no" : value));
        if (field.type === "multi") return Array.isArray(value) && value.every(v => field.options.some(o => o.value === v));
        if (field.type === "score") return Number.isInteger(value) && value >= field.min && value <= field.max;
        return field.type !== "text" || typeof value === "string" && value.length <= field.max_length;
    };
    async function open(options = {}) {
        if (current && !current.close()) return;
        const previousFocus = document.activeElement;
        const dialog = node("dialog", null, "sq-dialog");
        dialog.setAttribute("aria-labelledby", "sq-title");
        const shell = node("div", null, "sq-shell");
        const head = node("header", null, "sq-header");
        const title = node("h2", "脊柱健康与日常功能问卷"); title.id = "sq-title";
        const closeButton = node("button", "×", "sq-close"); closeButton.type = "button"; closeButton.setAttribute("aria-label", "关闭问卷");
        head.append(title, closeButton);
        const content = node("div", null, "sq-content"); content.append(node("p", "正在读取问卷…", "sq-muted"));
        const footer = node("footer", null, "sq-footer");
        shell.append(head, content, footer); dialog.append(shell); document.body.append(dialog); dialog.showModal();
        let busy = false, dirty = false, schema, step = 0;
        const draft = JSON.parse(JSON.stringify(options.answers || {}));
        const isLegacy = options.protocolVersion && options.protocolVersion !== "spine-intake-v3";
        if (!options.demoMode) delete draft.consent_confirmed;
        if (isLegacy && !draft.red_flag_answers) {
            draft.red_flag_answers = {};
            (Array.isArray(draft.red_flags) ? draft.red_flags : []).forEach(id => { draft.red_flag_answers[id] = "yes"; });
        }
        const close = () => {
            if (busy) return false;
            if (dirty && !window.confirm("本次修改还没有保存，确定关闭问卷吗？")) return false;
            dialog.close(); dialog.remove();
            if (current?.dialog === dialog) current = null;
            previousFocus?.focus?.();
            return true;
        };
        current = { close, dialog };
        closeButton.addEventListener("click", close);
        dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
        const errorBox = () => { const box = node("p", "", "sq-error"); box.setAttribute("role", "alert"); return box; };
        const button = (label, fn, primary = false) => { const b = node("button", label, primary ? "sq-primary" : "sq-secondary"); b.type = "button"; b.addEventListener("click", fn); return b; };
        function activeWarning(host) {
            host.replaceChildren();
            const adult = get(draft, "adult_confirmed");
            if (adult === "no" || adult === false) host.append(node("p", "当前问卷适用于成人。未满 18 周岁不能据此建立自动康复计划，请由监护人与专业人员协助评估。", "sq-warning"));
            const red = schema.sections.flatMap(s => s.fields).some(f => f.severity >= 3 && get(draft, f.id) === "yes");
            if (red) host.append(node("p", "你选择了需要立即线下评估的信号。请前往急诊或联系当地急救服务，不要等问卷完成或 AI 回复。记录可以保留，但系统不会生成自动训练处方。", "sq-warning"));
        }
        function fieldView(field, warning) {
            const group = node("fieldset", null, "sq-field"); group.dataset.field = field.id;
            group.append(node("legend", field.label + (isRequired(field, draft) ? " *" : "")));
            if (field.help) group.append(node("p", field.help, "sq-help"));
            let selected = get(draft, field.id);
            if (typeof selected === "boolean" && field.type === "choice") selected = selected ? "yes" : "no";
            const update = value => {
                put(draft, field.id, value); dirty = true;
                group.removeAttribute("aria-invalid"); group.querySelector(".sq-field-error")?.remove();
                activeWarning(warning);
            };
            if (field.type === "choice" || field.type === "multi" || field.type === "score") {
                if (field.anchors) {
                    const anchors = node("div", null, "sq-anchors");
                    field.anchors.forEach(a => { const item = node("div"); item.append(node("strong", a.label), node("span", a.detail)); anchors.append(item); });
                    group.append(anchors);
                }
                const choices = field.type === "score" ? Array.from({length: 11}, (_, i) => ({value: i, label: String(i)})) : field.options;
                const list = node("div", null, field.type === "score" ? "sq-options sq-score" : "sq-options");
                choices.forEach((option, i) => {
                    const label = node("label", null, "sq-option");
                    const input = node("input"); input.type = field.type === "multi" ? "checkbox" : "radio";
                    input.name = "sq-" + field.id; input.value = String(option.value);
                    input.id = "sq-" + field.id.replaceAll(".", "-") + "-" + i;
                    input.checked = field.type === "multi" ? (Array.isArray(selected) && selected.includes(option.value)) : selected === option.value;
                    input.disabled = Boolean(options.demoMode);
                    input.addEventListener("change", () => {
                        if (field.type !== "multi") update(option.value);
                        else {
                            if (input.checked && field.exclusive) list.querySelectorAll("input").forEach(other => {
                                if (other !== input && (option.value === field.exclusive || other.value === field.exclusive)) other.checked = false;
                            });
                            update([...list.querySelectorAll("input:checked")].map(i => i.value));
                        }
                    });
                    label.append(input, node("span", option.label)); list.append(label);
                });
                group.append(list);
            } else if (field.type === "text") {
                const area = node("textarea"); area.value = selected || ""; area.maxLength = field.max_length; area.rows = 3;
                area.setAttribute("aria-label", field.label); area.disabled = Boolean(options.demoMode);
                const count = node("span", `${area.value.length} / ${field.max_length}`, "sq-counter");
                area.addEventListener("input", () => { update(area.value.trim()); count.textContent = `${area.value.length} / ${field.max_length}`; });
                group.append(area, count);
            } else if (field.type === "consent") {
                const label = node("label", null, "sq-option"); const input = node("input"); input.type = "checkbox"; input.checked = selected === true; input.disabled = Boolean(options.demoMode);
                input.addEventListener("change", () => update(input.checked)); label.append(input, node("span", "同意保存本次回答")); group.append(label);
            }
            return group;
        }
        function validSection(index) {
            if (options.demoMode) return true;
            let first = null;
            schema.sections[index].fields.forEach(f => {
                const value = get(draft, f.id);
                const invalid = !isValid(f, value, draft);
                if (!invalid) return;
                const el = [...content.querySelectorAll("[data-field]")].find(e => e.dataset.field === f.id);
                if (!el) return;
                el.setAttribute("aria-invalid", "true");
                if (!el.querySelector(".sq-field-error")) el.append(node("p", f.type === "consent" ? "需要同意保存后才能提交。" : "请明确选择或填写；不知道的项目可选“不确定”。", "sq-field-error"));
                first ||= el;
            });
            if (first) { first.scrollIntoView({block: "center", behavior: "smooth"}); first.querySelector("input,textarea")?.focus(); }
            return !first;
        }
        function render() {
            if (!dialog.isConnected) return;
            content.replaceChildren(); footer.replaceChildren();
            const progress = node("nav", null, "sq-progress"); progress.setAttribute("aria-label", "问卷填写进度");
            schema.sections.forEach((s, index) => { const b = button(`${index + 1} ${s.title}`, () => { if (index < step || step === schema.sections.length || validSection(step)) { step = index; render(); } }); b.classList.toggle("sq-active", index === step); b.disabled = index > step; progress.append(b); });
            content.append(progress);
            if (options.demoMode) content.append(node("p", "演示病例 · 只读。可查看示例记录，不会保存到真实档案。", "sq-notice"));
            if (isLegacy) {
                const old = node("details", null, "sq-legacy"); old.append(node("summary", "上次是旧版问卷：查看原始填写记录"));
                const labels = {pain_score: "原疼痛自评（0–10）", activity_limit: "原活动受限自评（0–10）", duration: "持续时间", symptom_location: "不适部位", history: "既往情况", treatment: "处理措施", goals: "本次目标", red_flags: "当时勾选的安全信号", function_impacts: "受影响活动"};
                Object.entries(labels).forEach(([key, label]) => { const v = options.answers?.[key]; if (v !== undefined) old.append(node("p", `${label}：${Array.isArray(v) ? v.join("、") || "无勾选项（旧版未逐项确认）" : String(v)}`)); });
                content.append(old, node("p", "旧版未勾选的安全信号不会自动变成“没有”。新版请逐项确认。", "sq-muted"));
            } else if (options.answers && !dirty) content.append(node("p", "已载入上次答复。请按本次情况逐项检查，保存会新增记录，保留历史版本。", "sq-muted"));
            const warning = node("div"); warning.setAttribute("aria-live", "polite"); content.append(warning); activeWarning(warning);
            if (step < schema.sections.length) {
                const section = schema.sections[step]; content.append(node("h3", section.title), node("p", section.description, "sq-muted"));
                section.fields.forEach(field => content.append(fieldView(field, warning)));
                if (step === 0) {
                    content.append(node("p", schema.notice, "sq-notice"));
                    const sources = node("div", null, "sq-sources"); sources.append(node("span", "参考领域："));
                    schema.sources.forEach(source => { const link = node("a", source.title); link.href = source.url; link.target = "_blank"; link.rel = "noopener noreferrer"; sources.append(link); }); content.append(sources);
                }
                footer.append(node("span", `第 ${step + 1} / ${schema.sections.length} 步`, "sq-muted"));
                if (step) footer.append(button("上一步", () => { step--; render(); }));
                footer.append(button(step === schema.sections.length - 1 ? "核对本次回答" : "下一步", () => { if (validSection(step)) { step++; render(); } }, true));
            } else {
                content.append(node("h3", "核对并保存本次记录"), node("p", "保存后将展示规则分析、需要补充的资料与后续建议；不会产生诊断或虚构“健康总分”。", "sq-muted"));
                schema.sections.forEach((s, index) => {
                    const block = node("section", null, "sq-review"); block.append(node("h4", s.title), button("修改", () => { step = index; render(); }));
                    s.fields.forEach(f => { const row = node("div", null, "sq-review-row"); row.append(node("span", f.label), node("strong", present(f, get(draft, f.id)))); block.append(row); }); content.append(block);
                });
                footer.append(button("返回修改", () => { step--; render(); }));
                const save = button(options.demoMode ? "演示记录（只读）" : "保存问卷并查看分析", () => saveAnswers(save), true); save.disabled = Boolean(options.demoMode); footer.append(save);
            }
            content.scrollTop = 0;
        }
        async function saveAnswers(save) {
            if (busy || options.demoMode) return;
            const error = errorBox(); content.append(error);
            if (!options.caseId) { error.textContent = "当前病例尚未载入，请关闭问卷并刷新后重试。"; return; }
            const invalidStep = schema.sections.findIndex(section => section.fields.some(f => !isValid(f, get(draft, f.id), draft)));
            if (invalidStep >= 0) { step = invalidStep; render(); validSection(step); return; }
            busy = true; save.disabled = true; closeButton.disabled = true; save.textContent = "正在保存…";
            footer.querySelectorAll("button").forEach(b => { b.disabled = true; });
            try {
                const response = await fetch(`/api/cases/${encodeURIComponent(options.caseId)}/questionnaire`, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify({answers: draft, protocol_version: schema.version})});
                const data = await response.json().catch(() => ({}));
                if (!response.ok || data.code !== 200 || !data.questionnaire) throw new Error(typeof data.detail === "string" ? data.detail : data.message || "问卷未保存，请检查登录状态或稍后重试。");
                const result = data.questionnaire, triage = result.triage || {};
                dirty = false;
                content.replaceChildren(node("h3", "问卷已保存"), node("p", "本次回答已记录到当前病例，重复填写会保留独立记录。", "sq-muted"), node("p", triage.message || "安全分流已更新。", ["urgent", "soon"].includes(triage.risk_level) ? "sq-warning" : "sq-notice"));
                content.append(node("h4", "本次记录与分析"), node("p", triage.analysis || "本次回答已保存。", "sq-analysis"));
                if (triage.missing_or_uncertain?.length) content.append(node("p", "仍需确认：" + triage.missing_or_uncertain.join("、"), "sq-warning"));
                const next = node("ul"); (triage.next_steps || []).forEach(item => next.append(node("li", item))); content.append(next, node("p", schema.notice, "sq-muted"));
                footer.replaceChildren(button("完成，返回档案 / 对话", close, true));
                try { await options.onSaved?.(result); } catch (_) { content.append(node("p", "问卷已成功保存，但页面刷新未完成；关闭后刷新即可查看。", "sq-muted")); }
            } catch (err) {
                error.textContent = err.message || "网络未连接，回答仍保留在本窗口，请重试。";
                error.scrollIntoView({block: "nearest"});
                save.textContent = "重新保存";
                footer.querySelectorAll("button").forEach(b => { b.disabled = false; });
            } finally { busy = false; save.disabled = false; closeButton.disabled = false; }
        }
        try {
            const response = await fetch("/api/questionnaire/schema", {credentials: "same-origin"});
            const data = await response.json();
            if (!response.ok || !data.schema?.sections?.length) throw new Error("问卷定义暂时无法读取，请确认已重启最新服务。");
            schema = data.schema; render();
        } catch (error) { content.replaceChildren(node("p", error.message || "问卷加载失败，请稍后重试。", "sq-error")); footer.append(button("关闭", close)); }
        return {close};
    }
    window.MindSpineQuestionnaire = {open};
})();
