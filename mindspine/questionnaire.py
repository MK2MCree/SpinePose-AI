"""Versioned, plain-language adult intake; not a validated diagnostic scale.

The wording and pain anchors are original explanatory aids. Source domains are
NICE NG59 (assessment/function/self-management) and NHS back-pain safety advice.
No ODI/STarT Back score, disease probability, or medical clearance is computed.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

VERSION = "spine-intake-v3"
SOURCES = [
    {"title": "NICE NG59：腰背痛与坐骨神经痛的评估和管理", "url": "https://www.nice.org.uk/guidance/ng59/chapter/Recommendations"},
    {"title": "NHS：背痛与需要及时就医的症状", "url": "https://www.nhs.uk/conditions/back-pain/"},
]
RED_FLAGS = {
    "new_bowel_or_bladder_change": (3, "新出现排尿、排便控制变化", "例如突然尿不出来、感觉不到膀胱是否充盈，或以前没有的漏尿、失禁；不是单纯便秘。"),
    "saddle_numbness": (3, "臀部内侧、会阴或肛门周围新出现麻木", "指坐在自行车坐垫上接触的区域，例如擦拭时感觉明显变弱。"),
    "progressive_weakness": (3, "腿部力量正在明显变差", "例如近期越来越难抬脚、脚拖地或站立不稳；不是运动后的普通疲劳。"),
    "bilateral_leg_symptoms": (3, "背痛同时伴双腿新出现疼痛、麻木或无力", "这里指两条腿同时出现的新变化，而不只是单侧长期酸痛。"),
    "severe_trauma": (3, "本次不适发生在严重事故或明显外伤之后", "例如车祸、高处坠落或重物撞击。轻微碰撞且不确定时请选择“不确定”。"),
    "fever_or_infection": (2, "背痛同时伴发热、寒战或明显全身不适", "尤其是近期感染之后出现；不要只把它当作肌肉酸痛。"),
    "cancer_history_with_new_pain": (2, "曾患肿瘤，而且这次出现新的或加重的背痛", "若不了解既往诊断，选择“不确定”，不要猜测。"),
    "unexplained_weight_loss": (2, "近期没有主动减重，却明显变瘦", "例如未改变饮食或运动，体重却持续下降；可在补充说明中写变化。"),
    "night_pain": (1, "疼痛经常在夜间加重或休息后仍不缓解", "例如多次被疼醒、换姿势也没有明显缓解。单次睡姿不适不等于此情况。"),
}
TRISTATE = [{"value": "yes", "label": "有"}, {"value": "no", "label": "没有"}, {"value": "unknown", "label": "不确定"}]
FUNCTION_LABELS = {"walking": "步行", "sitting": "坐着工作或学习", "sleep": "睡眠", "dressing": "穿衣、洗漱等自理"}


def _choice(fid: str, label: str, options: list, help_text: str = "", required: bool = True, **extra) -> dict:
    return {"id": fid, "label": label, "type": "choice", "required": required, "help": help_text,
            "options": [{"value": x, "label": x} if isinstance(x, str) else x for x in options], **extra}


def questionnaire_schema() -> dict[str, Any]:
    sections = [
        {"id": "start", "title": "先确认本次填写", "description": "约需 5–8 分钟。按现在的实际情况回答；不确定可以如实选择，不会被当作“没有问题”。", "fields": [
            _choice("adult_confirmed", "你是否已满 18 周岁？", [{"value": "yes", "label": "已满 18 周岁"}, {"value": "no", "label": "未满 18 周岁"}]),
            {"id": "consent_confirmed", "type": "consent", "required": True, "label": "我同意将本次回答保存到当前健康档案，用于资料整理与安全分流。", "help": "本问卷不是诊断。紧急不适时不要等待问卷或 AI 回复；原始资料不会因此发送到外部模型。"},
        ]},
        {"id": "safety", "title": "先排查需要及时就医的情况", "description": "每一项都请明确选择。出现新发大小便控制异常、会阴麻木、腿部无力或严重外伤时，应立即寻求急诊评估，不必等到填写结束。", "fields": [
            _choice("red_flag_answers." + key, label, TRISTATE, detail, severity=severity)
            for key, (severity, label, detail) in RED_FLAGS.items()
        ]},
        {"id": "symptoms", "title": "描述不适，而不是猜诊断", "description": "疼痛分数只记录你的主观感受，不能判断疾病种类或严重程度。", "fields": [
            {"id": "symptom_location", "type": "multi", "label": "主要不适在哪里？", "required": True, "exclusive": "当前无明显不适", "options": [{"value": v, "label": v} for v in ["颈肩部", "上背部", "腰部", "骶臀部", "下肢", "当前无明显不适"]]},
            _choice("duration", "本次不适持续了多久？", ["不足 1 周", "1–4 周", "1–3 个月", "超过 3 个月", "反复发作", "当前无症状，进行检查或随访"]),
            _choice("symptom_change", "与一周前相比，整体感觉怎样？", ["有所减轻", "基本不变", "逐渐加重", "突然明显加重", "症状不足一周", "当前无症状"]),
            _choice("radiation", "不适是否从背部或臀部延伸到腿部？", ["没有", "主要到左腿", "主要到右腿", "两条腿都有", "不确定"], "这叫“放射”，例如疼痛沿臀部到大腿或小腿；它本身不等于确诊神经受压。"),
            {"id": "pain_score", "type": "score", "required": True, "label": "此刻的疼痛大约是多少？", "help": "先看下面的生活例子，再选择最接近的 0–10 分。例子只是帮助表达，不是经过验证的诊断分级。", "min": 0, "max": 10,
             "anchors": [{"label": "0 · 无痛", "detail": "此刻完全不痛。"}, {"label": "1–3 · 能感觉到", "detail": "有酸痛或隐痛，大多数日常事情还能照常做。"}, {"label": "4–6 · 明显影响", "detail": "常被疼痛分散注意力，工作、走路或睡眠需要调整。"}, {"label": "7–9 · 严重干扰", "detail": "很多事情做不了，休息也难受，需要及时专业评估。"}, {"label": "10 · 难以忍受", "detail": "你能想象的最强疼痛；突然发生或伴异常症状时不要等待线上问卷。"}]},
            {"id": "symptom_notes", "type": "text", "label": "什么情况更痛或更舒服？（选填）", "required": False, "help": "例如久坐、弯腰、走动后变化；可以补充具体部位或发生时间。", "max_length": 1200},
        ]},
        {"id": "function", "title": "看看对日常生活的影响", "description": "请回想最近 7 天；只记录与本次颈背腰部问题有关的影响。这四项分别记录，不合成为“脊柱健康总分”。", "fields": [
            _choice("daily_function." + key, title, [{"value": "none", "label": "没有影响，和平时一样"}, {"value": "mild", "label": mild}, {"value": "moderate", "label": moderate}, {"value": "severe", "label": severe}, {"value": "unknown", "label": "不确定 / 近期没有做过"}])
            for key, title, mild, moderate, severe in [
                ("walking", "步行或外出时", "能走，但会不舒服", "要减少距离或中途停下来休息", "很难行走，或需要别人帮助"),
                ("sitting", "坐着工作或学习时", "能完成，需要偶尔换姿势", "需要频繁起身或减少工作时间", "难以继续坐着完成日常事情"),
                ("sleep", "夜间睡觉时", "入睡稍困难或需要换姿势", "会被疼醒，第二天休息不足", "多数时间难以入睡或反复醒来"),
                ("dressing", "穿衣、穿鞋、洗漱时", "能独立完成，但比较慢或不舒服", "部分动作做不了，需要调整方法", "需要别人协助才能完成"),
            ]
        ]},
        {"id": "history", "title": "了解已有检查和处理", "description": "不记得医学名称也没关系，写你知道的情况即可。不填写不是“无病史”。", "fields": [
            _choice("history_status", "是否有与脊柱有关的既往诊断、外伤或手术？", TRISTATE, "例如曾被告知侧弯、骨折、骨质疏松，或做过脊柱手术。"),
            {"id": "history", "type": "text", "label": "既往情况（有的话请简述）", "required": False, "required_when": {"field": "history_status", "value": "yes"}, "max_length": 2000, "help": "选择“有”时请补充。大概时间、检查名称和医生怎么说；不清楚可写“不记得”。"},
            _choice("treatment_status", "目前是否正在用药、接受治疗或做康复训练？", TRISTATE),
            {"id": "treatment", "type": "text", "label": "正在进行的处理（有的话请简述）", "required": False, "required_when": {"field": "treatment_status", "value": "yes"}, "max_length": 2000, "help": "选择“有”时请补充，可以写药名、训练方式及感觉是否有效；系统不会据此要求你停药或改药。"},
            _choice("scoliosis_history", "是否曾由医生确认脊柱侧弯？", [{"value": "yes", "label": "有，医生确认过"}, {"value": "no", "label": "没有被确认过"}, {"value": "unknown", "label": "不确定"}], "外观不对称或 AI 结果不能替代医生诊断。", required=False),
            {"id": "goals", "type": "text", "label": "这次最希望得到什么帮助？", "required": True, "max_length": 1200, "help": "例如：整理就医资料、弄清需要补充什么检查、比较历次变化，或记录疼痛与睡眠。"},
        ]},
    ]
    return {"version": VERSION, "title": "脊柱健康与日常功能问卷", "review_status": "not_clinically_validated",
            "notice": "项目自编成人信息采集表，参考指南的评估领域；未经过临床量表验证，不能诊断、排除疾病或替代医生。", "sources": deepcopy(SOURCES), "sections": sections}


def _get(data: dict, path: str) -> Any:
    parts = path.split(".")
    value = data.get(parts[0])
    return value.get(parts[1]) if len(parts) == 2 and isinstance(value, dict) else (value if len(parts) == 1 else None)


def _required(field: dict, answers: dict) -> bool:
    condition = field.get("required_when", {})
    return bool(field["required"] or (condition and _get(answers, condition["field"]) == condition["value"]))


def normalize_answers(answers: dict[str, Any]) -> dict[str, Any]:
    """Whitelist fields, reject malformed values, preserve unknown/missing facts."""
    if not isinstance(answers, dict):
        raise ValueError("问卷答案格式无效")
    out: dict[str, Any] = {}
    for section in questionnaire_schema()["sections"]:
        for field in section["fields"]:
            value = _get(answers, field["id"])
            if value is None or value == "":
                continue
            if field["id"] == "adult_confirmed":
                value = "yes" if value is True else "no" if value is False else value
            if field["type"] in {"choice", "multi"}:
                allowed = {x["value"] for x in field["options"]}
                if field["type"] == "multi":
                    if not isinstance(value, list) or any(not isinstance(v, str) or v not in allowed for v in value):
                        raise ValueError(f"{field['label']}包含无效选项")
                    value = list(dict.fromkeys(value))
                    if field.get("exclusive") in value and len(value) > 1:
                        raise ValueError("“当前无明显不适”不能与其他部位同时选择")
                elif not isinstance(value, str) or value not in allowed:
                    raise ValueError(f"{field['label']}包含无效选项")
            elif field["type"] == "consent":
                if not isinstance(value, bool):
                    raise ValueError("保存同意应明确选择")
            elif field["type"] == "score":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10 or int(value) != value:
                    raise ValueError("疼痛评分应为 0–10 的整数")
                value = int(value)
            elif field["type"] == "text":
                if not isinstance(value, str) or len(value) > field["max_length"]:
                    raise ValueError(f"{field['label']}内容过长或格式无效")
                value = value.strip()
            key = field["id"].split(".")
            if len(key) == 2:
                out.setdefault(key[0], {})[key[1]] = value
            else:
                out[key[0]] = value
    if out.get("consent_confirmed") is not True:
        raise ValueError("请先同意将本次问卷保存到当前病例")
    missing = [field["label"] for section in questionnaire_schema()["sections"] for field in section["fields"]
               if _required(field, out) and _get(out, field["id"]) in (None, "", [])]
    if missing:
        raise ValueError("请完成必填项：" + "、".join(missing))
    if "adult_confirmed" in out:
        out["adult_confirmed"] = out["adult_confirmed"] == "yes"
    out["red_flags"] = [k for k, v in out.get("red_flag_answers", {}).items() if v == "yes"]
    out["function_impacts"] = [FUNCTION_LABELS[k] for k, v in out.get("daily_function", {}).items() if v not in {"none", "unknown"}]
    return out


def triage_answers(answers: dict[str, Any]) -> dict[str, Any]:
    flags = answers.get("red_flag_answers", {})
    missing: list[str] = []
    for section in questionnaire_schema()["sections"]:
        for field in section["fields"]:
            value = _get(answers, field["id"])
            if _required(field, answers) and (value is None or value == "" or value == [] or value in ("unknown", "不确定")):
                missing.append(field["label"])
    if answers.get("adult_confirmed") is not True:
        missing.append("成人适用范围确认")
    triggers = [{"id": key, "label": RED_FLAGS[key][1], "severity": RED_FLAGS[key][0]} for key, value in flags.items() if value == "yes"]
    pain = answers.get("pain_score")
    if pain and (answers.get("symptom_location") == ["当前无明显不适"] or answers.get("duration") == "当前无症状，进行检查或随访"):
        missing.append("疼痛评分与“当前无症状”的回答不一致，请核对")
    severity = max([x["severity"] for x in triggers] + [0])
    if pain is not None and pain >= 7:
        severity = max(severity, 1)
        triggers.append({"id": "pain_interference", "label": "自报疼痛较强", "severity": 1})
    if answers.get("symptom_change") in {"逐渐加重", "突然明显加重"}:
        worsening_severity = 2 if answers["symptom_change"] == "突然明显加重" else 1
        severity = max(severity, worsening_severity)
        triggers.append({"id": "worsening_symptoms", "label": answers["symptom_change"], "severity": worsening_severity})
    if "severe" in answers.get("daily_function", {}).values():
        severity = max(severity, 1)
        triggers.append({"id": "daily_function_limit", "label": "日常活动明显受限", "severity": 1})
    if answers.get("history_status") == "yes":
        severity = max(severity, 1)
        triggers.append({"id": "relevant_history", "label": "有相关病史，训练前需确认适用范围", "severity": 1})
    if answers.get("treatment_status") == "yes":
        severity = max(severity, 1)
        triggers.append({"id": "ongoing_treatment", "label": "正在治疗或康复，请与原有安排核对", "severity": 1})
    risk = {0: "routine", 1: "attention", 2: "soon", 3: "urgent"}[severity]
    if missing and severity < 2:
        risk = "incomplete"
    messages = {
        "urgent": "你勾选了需要立即线下评估的信号。请尽快前往急诊或联系当地急救服务，不要等待 AI 或自行训练；问卷不能确认原因。",
        "soon": "有需要尽快线下评估的情况，请联系医生；在明确原因前不生成自动训练建议。",
        "attention": "症状、生活影响或既往情况需要进一步了解；建议带着这份记录咨询专业人员，不自动生成训练处方。",
        "routine": "在你已回答的范围内，未触发优先就医规则；这不等于排除疾病，也不是运动安全许可。",
        "incomplete": "仍有未填写或不确定的关键资料，不能按低风险处理。可补充回答或带记录咨询医生，暂不生成自动训练建议。",
    }
    function_summary = "；".join(f"{FUNCTION_LABELS[k]}：" + {"none": "无影响", "mild": "轻度影响", "moderate": "需调整或休息", "severe": "明显受限", "unknown": "不确定"}[v] for k, v in answers.get("daily_function", {}).items())
    analysis = f"本次自报疼痛：{pain if pain is not None else '未填写'} / 10。" + (function_summary + "。" if function_summary else "日常功能尚未记录。") + messages[risk]
    return {"protocol_version": VERSION, "risk_level": risk, "max_rule_severity": severity, "triggers": triggers,
            "pain_score": pain, "activity_limit": None, "data_complete": not missing, "missing_or_uncertain": missing,
            "automation_allowed": risk == "routine" and not missing, "message": messages[risk], "analysis": analysis,
            "function_summary": function_summary, "sources": deepcopy(SOURCES), "not_a_diagnosis": True,
            "review_status": "not_clinically_validated", "limitations": questionnaire_schema()["notice"],
            "next_steps": (["及时线下就医，出示本次问卷与已有影像。"] if severity >= 2 else ["保存这次记录，后续按相同问题比较变化。", "补全不确定资料或邀请医生复核。" if missing else "按需补充已有检查资料，不要仅为问卷结果自行安排影像检查。"])}
