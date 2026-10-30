(function attachSpineDemo(global) {
    "use strict";

    const query = () => new URLSearchParams(global.location.search);
    const clone = (value) => JSON.parse(JSON.stringify(value));
    // Local-only materials explicitly supplied for the product demo. A generated
    // visual is never presented as an original DR image or a clinical conclusion.
    const assets = Object.freeze({
        gait_silhouette: "assets/case-demo/gait-silhouette-sequence.png",
        frontal_pseudo_dr: "assets/case-demo/surface-to-frontal-pseudo-dr.png",
        real_lateral_dr: "assets/case-demo/real-dr-lateral.jpg",
        surface_rgb: "assets/case-demo/surface-rgb-side.png",
        lateral_pseudo_dr: "assets/case-demo/surface-to-lateral-pseudo-dr.png",
        video_pose: "assets/case-demo/video-pose-overlay.png",
        torso_point_cloud: "assets/case-demo/torso-point-cloud.png",
    });
    const evidence = [
        { title: "WHO 成人慢性原发性腰痛指南（示例引用）", source_url: "https://www.who.int/publications/i/item/9789240081789" },
        { title: "NICE NG59：低背痛与坐骨神经痛（示例引用）", source_url: "https://www.nice.org.uk/guidance/ng59" },
    ];

    const legacyEntries = [
        {
            id: "demo-checkin-aug", kind: "checkin", status: "completed", recorded_at: "2026-08-26", created_at: "2026-08-26",
            title: "第 4 周状态打卡", summary: "久坐后的腰部不适为 3/10；完成本周 3 次低负荷训练。",
            payload: { pain_score: 3, activity_limit: 3, exercise_minutes: 78, note: "办公日午后偶有紧绷，步行与休息后缓解。" },
        },
        {
            id: "demo-clinical-aug", kind: "clinical", status: "completed", recorded_at: "2026-08-22", created_at: "2026-08-22",
            title: "临床复查记录（演示）", summary: "同一随访协议下记录的 Cobb 角为 20.6°；仅用于展示趋势，不构成诊断。",
            payload: { cobb_angle: 20.6, atr_angle: 5.1, curve_location: "胸腰段（演示记录）", diagnosis: "演示：建议继续按既定随访节奏复测。", treatment: "保持低负荷、规律活动；出现新症状时线下复核。" },
        },
        {
            id: "demo-surface-cloud-aug", kind: "surface", status: "completed", recorded_at: "2026-08-21", created_at: "2026-08-21",
            title: "体表三维点云视觉档案（演示）", summary: "点云用于呈现体表视觉档案；非尺度测量，不和原始 X 光或医学分割结果混同。",
            source_filename: "torso_point_cloud.png · 本地点云可视化",
            payload: { quality: "采集协议完整（演示）", measurement_boundary: "非尺度测量 / 非诊断" },
            media: [
                { src: assets.torso_point_cloud, alt: "躯干正侧位体表点云", label: "体表三维点云视觉档案", caption: "可用于后续时间点的视觉对照；当前展示为静态图，不提供医学几何测量。", type: "三维视觉档案" },
            ],
        },
        {
            id: "demo-video-aug", kind: "video", status: "completed", recorded_at: "2026-08-20", created_at: "2026-08-20",
            title: "动作 / 步态协议任务（演示）", summary: "采集协议、画面完整性和关键点连续性均通过演示阈值；未输出疾病筛查结论。",
            source_filename: "demo_side_gait.mp4",
            payload: { duration_seconds: 18.4, resolution: "1920 × 1080", screening_label: "仅演示：协议质量通过（非临床结论）", protocol_quality: 0.94 },
            media: [
                { src: assets.video_pose, alt: "视频姿态叠加帧", label: "视频姿态叠加帧", caption: "本地视频流程可视化；关键点绘制不等于脊柱侧弯或任何疾病判断。", type: "视频算法可视化" },
                { src: assets.gait_silhouette, alt: "步态剪影序列", label: "步态剪影序列", caption: "用于展示连续帧与采集协议；不代替人工阅片或临床检查。", type: "视频质控材料" },
            ],
        },
        {
            id: "demo-dr-jun", kind: "dr", status: "completed", recorded_at: "2026-06-18", created_at: "2026-06-18",
            title: "DR / X 光资料与测算（演示）", summary: "原始侧位 DR 与类 DR 算法可视化已分开留存；页面数值仅用于界面演示。",
            source_filename: "1L121038.jpg · 本地原始 DR",
            payload: { detected: true, angles: { LL: 42.3, SSA: 35.1, HARRISON_A: 14.1, LCI: 5.9 }, slips: [] },
            media: [
                { src: assets.real_lateral_dr, alt: "用户提供的侧位 DR 图像", label: "原始侧位 DR / X 光", caption: "本地授权原始资料；不因在此显示而形成自动诊断。", type: "原始资料" },
                { src: assets.lateral_pseudo_dr, alt: "体表生成的侧位类 DR 可视化", label: "侧位类 DR 算法可视化", caption: "由体表配对生成的视觉结果，不是原始 X 光；仅供产品流程展示与人工复核。", type: "算法可视化" },
            ],
        },
        {
            id: "demo-rgb-jun", kind: "rgb", status: "completed", recorded_at: "2026-06-16", created_at: "2026-06-16",
            title: "体表 RGB 与正位类 DR 可视化（演示）", summary: "体表资料和正位类 DR 算法可视化已分开归档；不与原始 X 光做几何叠加或医学测量。",
            source_filename: "surface-rgb-side.png · 本地体表 RGB",
            payload: { detected: true, angles: { LL: 42.0, SSA: 34.8, HARRISON_A: 14.6, LCI: 6.1 }, slips: [] },
            media: [
                { src: assets.surface_rgb, alt: "用户提供的侧位体表 RGB 图像", label: "体表 RGB 原始资料", caption: "本地授权体表照片；用于视觉随访，不替代 X 光或临床测量。", type: "原始资料" },
                { src: assets.frontal_pseudo_dr, alt: "体表配对生成的正位类 DR 与椎体可视化", label: "正位类 DR / Cobb 可视化", caption: "算法生成的展示图，非原始 DR；涉及 Cobb 等项目须由专业人员复核。", type: "算法可视化" },
            ],
        },
        {
            id: "demo-checkin-may", kind: "checkin", status: "completed", recorded_at: "2026-05-20", created_at: "2026-05-20",
            title: "第 1 周状态打卡", summary: "久坐后腰部不适 5/10；活动受限自评 6/10。",
            payload: { pain_score: 5, activity_limit: 6, exercise_minutes: 35, note: "久坐 60 分钟后不适更明显。" },
        },
        {
            id: "demo-dr-feb", kind: "dr", status: "completed", recorded_at: "2026-02-14", created_at: "2026-02-14",
            title: "DR / X 光基线测算（演示）", summary: "建立可比较的演示基线；同一记录条件才允许在趋势中并列。",
            source_filename: "demo_lateral_dr_2026-02.png",
            payload: { detected: true, angles: { LL: 41.6, SSA: 34.0, HARRISON_A: 15.2, LCI: 6.8 }, slips: [] },
        },
    ];

    const workspace = {
        demo: true,
        case: { id: "demo-adult-spine-001", title: "演示病例 · 腰背随访", care_scope: "adult_spine_followup", created_at: "2026-02-10", role: "owner" },
        consent: { external_ai_allowed: true, updated_at: "2026-08-26" },
        questionnaire: {
            id: "demo-intake-001", protocol_version: "adult-spine-intake-v1", created_at: "2026-05-18",
            answers: { adult_confirmed: true, pain_score: 5, activity_limit: 6, red_flags: [] },
            triage: { risk_level: "routine", data_complete: true, message: "演示问卷已完成：未触发本问卷覆盖的高优先级信号；这不等于排除疾病。", rule_version: "adult-spine-triage-demo-v1", analysis: "填写确认、疼痛和活动受限已填写；8 项高优先级信号均未选中。当前可以进行资料整理、随访提醒和受控康复计划展示，但不能据此排除疾病或替代线下诊疗。" },
        },
        legacy_timeline: legacyEntries,
        legacy_overview: { total_entries: legacyEntries.length, latest_entry: legacyEntries[0] },
        assessments: [
            { id: "demo-surface-001", kind: "surface_archive_demo", state: "completed", tool_id: "demo-surface-archive", tool_version: "demo-1.0", created_at: "2026-08-21", updated_at: "2026-08-21", result: { quality: "采集协议完整；非诊断、非尺度测量" } },
            { id: "demo-motion-001", kind: "video_motion_demo", state: "completed", tool_id: "demo-motion-quality", tool_version: "demo-1.0", created_at: "2026-08-20", updated_at: "2026-08-20", result: { quality: "采集质量 0.94；未给出疾病结论" } },
            { id: "demo-ctmr-001", kind: "ctmr_demo", state: "completed", tool_id: "demo-professional-imaging", tool_version: "demo-1.0", created_at: "2026-08-19", updated_at: "2026-08-19", result: { quality: "仅展示专业影像任务卡，不含真实 DICOM 或分割结果" } },
        ],
        review_tasks: [
            { id: "demo-review-001", state: "completed", created_at: "2026-08-24", resolved_at: "2026-08-25", title: "演示病例人工复核", summary: "已完成资料边界、趋势可比性和展示标识的演示复核；不代表真实临床阅片。" },
        ],
        rehab_plans: [{ id: "demo-plan-001", state: "active", created_at: "2026-08-23", plan_json: { completed_sessions: 12, planned_sessions: 16, next_session: "本周第 4 次：呼吸控制 + 髋主导练习（演示）" } }],
        messages: [
            { id: "demo-message-user-intake", role: "user", created_at: "2026-08-23", content: "我已完成健康问卷。请先告诉我这次安全分流覆盖了什么、没有覆盖什么。" },
            { id: "demo-message-agent-intake", role: "assistant", created_at: "2026-08-23", content: "本次演示问卷已确认填写，疼痛为 5/10、活动受限为 6/10，问卷中列出的 8 项高优先级信号均未选中，因此规则分流为“常规随访”。它只覆盖问卷内的安全信号，不能排除结构性病变、也不能替代医生检查；若出现新发进行性无力、大小便改变、会阴麻木、严重外伤或持续加重症状，应停止自我计划并线下评估。", evidence, audit: { risk_level: "routine", tool_trace: ["case_access", "adult_triage_rules", "safety_audit"], external_model_used: false, demo: true } },
            { id: "demo-message-user-summary", role: "user", created_at: "2026-08-26", content: "请整理当前病例的证据、趋势与下一步。" },
            { id: "demo-message-agent-summary", role: "assistant", created_at: "2026-08-26", content: "演示病例已完成问卷、同协议影像记录、体表视觉档案、动作采集质控与康复打卡。可比较趋势显示：疼痛自评由 5/10 变为 3/10，活动受限由 6/10 变为 3/10；影像与临床测量仅在同一协议下并列展示。当前没有自动诊断或总分，建议按既定节奏复测，并在出现新发神经症状、持续夜间痛或功能明显下降时转线下复核。", evidence, audit: { risk_level: "routine", tool_trace: ["case_access", "structured_case_facts", "triage", "retrieve_clinical_evidence", "safety_audit"], external_model_used: false, demo: true } },
        ],
        actions: [
            { id: "demo-action-surface", kind: "build_surface_archive", state: "completed", reason: "近似体表视觉档案已建立（界面演示）。", created_at: "2026-08-21" },
            { id: "demo-action-video", kind: "run_video_screening", state: "completed", reason: "视频采集协议与关键点连续性已完成演示质控；未输出疾病结论。", created_at: "2026-08-20" },
            { id: "demo-action-plan", kind: "save_rehab_plan", state: "completed", reason: "已生成受控动作目录中的演示计划。", created_at: "2026-08-23" },
            { id: "demo-action-review", kind: "request_clinical_review", state: "completed", reason: "人工复核演示任务已关闭；结论仅展示状态与限制。", created_at: "2026-08-25" },
        ],
        third_party_reproduction: [
            { name: "RepDetect", status: "implemented_demo" }, { name: "OpenCap Core", status: "isolated_demo" }, { name: "TotalSegmentator", status: "isolated_demo" },
            { name: "MONAI", status: "implemented_demo" }, { name: "MultimodalTorsoImageReconstruction", status: "reference_demo" }, { name: "MedRAG", status: "implemented_demo" }, { name: "Multi-Agent Medical Assistant", status: "reference_demo" },
        ],
        knowledge: { review_counts: { reference_pending_clinical_review: 0 } },
        demo_insights: {
            trend: { label: "最近 4 次状态打卡 · 疼痛自评", unit: "/10", values: [{ date: "05.20", value: 5 }, { date: "06.06", value: 4 }, { date: "07.12", value: 3.5 }, { date: "08.26", value: 3 }] },
            analysis: [
                { label: "问卷分流", value: "常规随访", note: "填写确认、疼痛和活动受限已填写；问卷内 8 项高优先级信号均未选中。" },
                { label: "资料完整性", value: "5 / 5 模态", note: "问卷、影像、体表、动作质控与康复记录均已载入。" },
                { label: "可比较趋势", value: "3 条", note: "疼痛、活动受限、同协议 LL / Cobb 记录可按时间查看。" },
                { label: "康复执行", value: "12 / 16 次", note: "示例计划处于进行中；每次完成均保留停止条件。" },
            ],
            interpretation: "演示中的变化仅用于呈现病例时间线、质量状态与可比较约束；不表示疗效保证，也不能替代线下诊断。",
        },
    };

    const personalCase = {
        demo: true,
        profile: { display_name: "演示用户", birth_date: "1992-04-18", sex: "female", height_cm: 165, weight_kg: 58, clinician_note: "演示档案：问卷、指标、趋势和任务状态为模拟记录；下方图像为本地授权展示材料，不上传、不外发，不能替代专业判读。", updated_at: "2026-08-26" },
        entries: legacyEntries,
        overview: { total_entries: legacyEntries.length, counts: { rgb: 1, dr: 2, video: 1, surface: 1, clinical: 1, checkin: 2 }, latest_entry: legacyEntries[0] },
        trends: {
            dr_ll: [{ date: "2026-02-14", value: 41.6, entry_id: "demo-dr-feb" }, { date: "2026-06-18", value: 42.3, entry_id: "demo-dr-jun" }, { date: "2026-08-22", value: 43.1, entry_id: "demo-clinical-aug" }],
            clinical_cobb: [{ date: "2026-02-15", value: 22.4, entry_id: "demo-dr-feb" }, { date: "2026-06-19", value: 21.2, entry_id: "demo-dr-jun" }, { date: "2026-08-22", value: 20.6, entry_id: "demo-clinical-aug" }],
            checkin_pain: [{ date: "2026-05-20", value: 5, entry_id: "demo-checkin-may" }, { date: "2026-06-06", value: 4, entry_id: "demo-dr-jun" }, { date: "2026-07-12", value: 3.5, entry_id: "demo-video-aug" }, { date: "2026-08-26", value: 3, entry_id: "demo-checkin-aug" }],
            video_positive: [{ date: "2026-06-16", value: 0.86, entry_id: "demo-rgb-jun" }, { date: "2026-08-20", value: 0.94, entry_id: "demo-video-aug" }],
        },
    };

    function isExplicit() { return query().get("demo") === "1" || query().get("mode") === "demo"; }
    // Real records are the default; a demo must be explicitly requested.
    function isSuppressed() { return !isExplicit(); }
    function useWorkspace() { return isExplicit(); }
    function usePersonalCase() { return isExplicit(); }
    function decorateLinks(root = document) {
        root.querySelectorAll("[data-demo-link]").forEach((link) => {
            const target = new URL(link.getAttribute("href"), global.location.href);
            if (isSuppressed()) {
                target.searchParams.set("mode", "real");
                target.searchParams.delete("demo");
                if (query().get("case_id")) target.searchParams.set("case_id", query().get("case_id"));
            } else {
                target.searchParams.set("demo", "1");
                target.searchParams.delete("mode");
            }
            link.setAttribute("href", `${target.pathname.split("/").pop()}${target.search}`);
        });
    }
    function revealBanner(root = document) {
        root.querySelectorAll("[data-demo-banner]").forEach((node) => { node.classList.remove("hidden"); node.hidden = false; });
    }
    function svg(kind) {
        const title = kind === "surface" ? "合成体表视觉档案" : "合成 DR 测算示意图";
        const accent = kind === "surface" ? "#a393ff" : "#7f8df0";
        const body = kind === "surface"
            ? '<path d="M294 44c-38 12-58 54-53 90l13 67c4 19-7 36-13 55-12 37-1 80 30 102l20 14c6 4 14 4 20 0l20-14c31-22 42-65 30-102-6-19-17-36-13-55l13-67c5-36-15-78-53-90z" fill="none" stroke="%23a393ff" stroke-width="4"/><path d="M314 78c-17 36-10 53 1 70 12 20 11 41 0 61-11 18-12 43 0 80" fill="none" stroke="%23f1f3ff" stroke-width="5" stroke-linecap="round"/>'
            : '<path d="M314 38c-19 39-12 58 0 78 14 22 14 44 0 65-12 19-18 49 0 112" fill="none" stroke="%23f5f6ff" stroke-width="8" stroke-linecap="round"/><g fill="%237f8df0">' + [78, 108, 138, 168, 198, 228, 258].map((y) => `<rect x="286" y="${y}" width="56" height="18" rx="8"/>`).join("") + '</g><path d="M245 114h-42m42 88h-56m98 86h-64" stroke="%23aab4ff" stroke-width="2" stroke-dasharray="5 5"/>';
        return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 420"><rect width="640" height="420" fill="#090d16"/><g opacity=".28" stroke="#7c89d9">${Array.from({ length: 12 }, (_, i) => `<path d="M${i * 58} 0V420M0 ${i * 42}H640"/>`).join("")}</g><text x="36" y="48" fill="${accent}" font-family="Arial, sans-serif" font-size="18" font-weight="700">${title}</text><text x="36" y="76" fill="#aab2c6" font-family="Arial, sans-serif" font-size="13">DEMO · 非真实影像 / 非医疗结论</text>${body}<rect x="36" y="346" width="568" height="42" rx="12" fill="#121827"/><text x="54" y="372" fill="#dbe0ef" font-family="Arial, sans-serif" font-size="14">LL 43.1° · SS/SA 35.2° · LCA 13.7° · LCI 5.7 mm</text></svg>`)}`;
    }
    function agentReply(message) {
        return {
            id: `demo-message-${Date.now()}`, role: "assistant", created_at: "2026-08-26",
            content: `这是演示模式的本地回复：已读取模拟的问卷、影像、动作质控、体表档案和康复记录。对于“${String(message).slice(0, 80)}”，界面会保留来源、质量和限制；不会生成真实诊断或调用外部模型。`,
            evidence, audit: { risk_level: "routine", tool_trace: ["demo_case_read", "demo_evidence_lookup", "demo_safety_audit"], external_model_used: false, demo: true },
        };
    }

    global.MindSpineDemo = {
        isExplicit, isSuppressed, useWorkspace, usePersonalCase, decorateLinks, revealBanner, svg, agentReply,
        asset: (name) => assets[name] || "",
        workspace: () => clone(workspace), personalCase: () => clone(personalCase),
    };
}(window));
