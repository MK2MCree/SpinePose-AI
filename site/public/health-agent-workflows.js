(() => {
    "use strict";
    const node = (tag, text, cls) => { const n = document.createElement(tag); if (text) n.textContent = text; if (cls) n.className = cls; return n; };
    function openReport(agent, defaults = {}) {
        if (agent.demoMode) { agent.flash("请切换到真实病例生成报告，示例数据不会写入个人档案。", true); return; }
        if (!agent.workspace?.case?.id || document.getElementById("agent-report-dialog")) return;
        const dialog = node("dialog", null, "agent-report-dialog"); dialog.id = "agent-report-dialog";
        const form = node("form"), header = node("header");
        const heading = node("h2", "生成健康报告"); heading.id = "report-dialog-title"; dialog.setAttribute("aria-labelledby", heading.id);
        const close = node("button", "关闭", "workflow-action"); close.type = "button"; close.addEventListener("click", () => dialog.close()); header.append(heading, close);
        form.append(header, node("p", "选择文件格式和内容范围。报告只整理当前病例已有记录，不会补造缺失数据或产生诊断。", "report-intro"));
        const field = (title, control) => { const label = node("label", title, "report-field"); label.append(control); form.append(label); return control; };
        const title = node("input"); title.value = defaults.title || "脊柱健康评估报告"; title.required = true; title.maxLength = 60; field("报告标题", title);
        const select = (name, choices, value) => { const el = node("select"); choices.forEach(([v, text]) => { const option = node("option", text); option.value = v; el.append(option); }); el.value = value; return field(name, el); };
        const format = select("文件格式", [["both", "PDF 和 Word"], ["pdf", "PDF"], ["docx", "Word（DOCX）"]], defaults.format || "both");
        const audience = select("阅读对象", [["patient", "个人阅读版"], ["clinician", "就诊沟通版"]], defaults.audience || "patient");
        const detail = select("内容详细程度", [["brief", "简版摘要"], ["full", "完整记录版"]], defaults.detail || "full");
        const range = node("fieldset"); range.append(node("legend", "包含内容"));
        const sections = [["profile", "基本资料"], ["questionnaire", "问卷与分析"], ["assessments", "影像与模型结果"], ["followup", "随访与状态打卡"]].map(([value, text]) => {
            const label = node("label", null, "report-check"), input = node("input"); input.type = "checkbox"; input.value = value;
            input.checked = (defaults.sections || ["profile", "questionnaire", "assessments", "followup"]).includes(value); label.append(input, document.createTextNode(text)); range.append(label); return input;
        }); form.append(range);
        const start = node("input"); start.type = "date"; start.value = defaults.date_from || ""; field("记录开始日期（可留空）", start);
        const end = node("input"); end.type = "date"; end.value = defaults.date_to || ""; field("记录结束日期（可留空）", end);
        const status = node("p", "", "report-status"); status.setAttribute("role", "status"); form.append(status);
        const submit = node("button", "确认生成文件", "report-submit"); submit.type = "submit"; form.append(submit);
        let busy = false, requestId = crypto.randomUUID(), attemptedOptions = "";
        dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
        dialog.addEventListener("close", () => dialog.remove());
        form.addEventListener("submit", async (event) => {
            event.preventDefault(); if (busy) return;
            const options = {title: title.value.trim(), format: format.value, audience: audience.value, detail: detail.value,
                sections: sections.filter(x => x.checked).map(x => x.value), date_from: start.value || null, date_to: end.value || null};
            if (!options.sections.length) { status.textContent = "至少选择一项报告内容。"; return; }
            if (start.value && end.value && start.value > end.value) { status.textContent = "开始日期不能晚于结束日期。"; return; }
            const serialized = JSON.stringify(options);
            if (attemptedOptions && attemptedOptions !== serialized) requestId = crypto.randomUUID();
            attemptedOptions = serialized; busy = true; submit.disabled = true; close.disabled = true; status.textContent = "正在生成，请稍候…";
            try {
                const result = await agent.request(`/api/cases/${encodeURIComponent(agent.workspace.case.id)}/reports`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({options, confirmation: true, request_id: requestId})});
                const files = node("div", null, "report-downloads");
                (result.report?.files || []).forEach(file => { const url = agent.safeLocalUrl(file.url); if (!url) return; const link = node("a", file.title, "workflow-action"); link.href = url; link.setAttribute("download", ""); files.append(link); });
                status.textContent = "文件已生成。下载链接也已保存在本次对话中。";
                form.replaceChildren(header, node("p", options.title, "report-intro"), status, files);
                dialog.scrollTop = 0;
                try { agent.scrollNext = true; await agent.load(); } catch (_) { /* Files remain directly downloadable. */ }
            } catch (error) { status.textContent = error.message || "报告生成失败，请重试。"; }
            finally { busy = false; submit.disabled = false; close.disabled = false; }
        });
        dialog.append(form); document.body.append(dialog); dialog.showModal(); title.focus();
    }
    window.MindSpineAgentWorkflows = {openReport};
})();
