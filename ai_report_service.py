import json
import os
import sys
from urllib import error, request

from mindspine.runtime import get_resource_root


def get_runtime_base_dir():
    return get_resource_root()


class DeepSeekReportService:
    def __init__(self, base_dir=None):
        self.base_dir = base_dir or get_runtime_base_dir()
        self.settings_path = os.path.join(self.base_dir, "app_settings.json")
        self.api_key = ""
        self.base_url = "https://api.deepseek.com"
        # V4-Pro is the strongest current DeepSeek Chat Completions model. It
        # is still constrained to de-identified wording after local policy has
        # made every safety and tool decision.
        self.model = "deepseek-v4-pro"
        self.model_migrated_from = ""
        self.timeout_sec = 45.0
        self.refresh_config()

    def _load_settings_file(self):
        if not os.path.exists(self.settings_path):
            return {}

        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def refresh_config(self):
        settings = self._load_settings_file()
        production = os.getenv("MINDSPINE_ENV", "development").strip().lower() in {"production", "prod"}

        env_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        file_key = "" if production else str(settings.get("deepseek_api_key", "")).strip()
        self.api_key = env_key or file_key

        env_base = os.getenv("DEEPSEEK_BASE_URL", "").strip()
        file_base = str(settings.get("deepseek_base_url", "")).strip()
        self.base_url = (env_base or file_base or "https://api.deepseek.com").rstrip("/")

        env_model = os.getenv("DEEPSEEK_MODEL", "").strip()
        file_model = str(settings.get("deepseek_model", "")).strip()
        requested_model = env_model or file_model or "deepseek-v4-pro"
        # Keep legacy local settings from silently selecting a retired model.
        # The setting file itself is left untouched so secrets/config ownership
        # remain with the operator.
        if requested_model in {"deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash"}:
            self.model_migrated_from = requested_model
            self.model = "deepseek-v4-pro"
        else:
            self.model_migrated_from = ""
            self.model = requested_model

        env_timeout = os.getenv("DEEPSEEK_TIMEOUT_SEC", "").strip()
        file_timeout = str(settings.get("deepseek_timeout_sec", "")).strip()
        timeout_raw = env_timeout or file_timeout or "45"

        try:
            self.timeout_sec = max(10.0, float(timeout_raw))
        except ValueError:
            self.timeout_sec = 45.0

    def is_ready(self):
        self.refresh_config()
        return bool(self.api_key)

    def describe(self):
        self.refresh_config()
        return {
            "ready": bool(self.api_key),
            "provider": "DeepSeek",
            "model": self.model,
            "model_migrated_from": self.model_migrated_from or None,
            "base_url": self.base_url,
            "settings_path": "",
            "status": (
                f"已配置 DeepSeek，当前模型：{self.model}"
                + (f"（已从 {self.model_migrated_from} 迁移）" if self.model_migrated_from else "")
                if self.api_key
                else "未配置 DeepSeek API，个人报告将使用本地基础解读"
            ),
        }

    @staticmethod
    def _sanitize_text(value, default="--"):
        text = str(value or "").replace("\r", "\n").strip()
        if not text:
            return default
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return " ".join(lines) if lines else default

    def _normalize_list(self, value, default_items, limit=4):
        if isinstance(value, list):
            items = [self._sanitize_text(item, "") for item in value]
            items = [item for item in items if item]
        elif isinstance(value, str):
            raw = value.replace("；", "\n").replace(";", "\n").split("\n")
            items = [self._sanitize_text(item, "") for item in raw]
            items = [item for item in items if item]
        else:
            items = []

        if not items:
            items = default_items[:]

        return items[:limit]

    def _fallback_sections(self, reason):
        return {
            "used": False,
            "provider": "DeepSeek",
            "model": self.model,
            "status_message": reason,
            "summary_title": "当前未生成 DeepSeek 增强解读",
            "summary_for_patient": "系统这次没有成功获取 DeepSeek 增强分析，本报告已自动回退为基础个人版解释。若你近期有明显疼痛、麻木、活动受限或体态变化，请尽快到医院线下复查。",
            "urgency_level": "attention",
            "recommended_departments": [
                "骨科门诊",
                "脊柱外科门诊",
                "康复医学科门诊",
            ],
            "visit_steps": [
                "先携带本次报告和原始影像到骨科或脊柱专科就诊。",
                "向医生说明目前症状、持续时间和是否影响日常活动。",
                "如医生认为有必要，再进一步完善 DR、CT、MRI 等检查。",
            ],
            "lifestyle_recommendations": [
                "避免久坐久站和频繁弯腰搬重物。",
                "保持规律作息和体重管理，减少腰背部长期负担。",
                "日常坐姿、站姿尽量保持对称和稳定。",
            ],
            "rehab_recommendations": [
                "如果已有腰背不适，先降低高强度运动和扭转动作。",
                "是否进行针对性康复训练，请以康复科或专科医生意见为准。",
                "训练过程中若疼痛明显加重，应立即停止并就医。",
            ],
            "red_flags": [
                "腰腿痛突然明显加重",
                "下肢麻木无力或步态异常",
                "大小便功能异常或夜间痛明显",
            ],
            "medical_disclaimer": "本页为辅助建议，不构成最终诊断、用药或手术决策依据。",
        }

    def _normalize_sections(self, parsed, success_message):
        urgency = str(parsed.get("urgency_level", "routine")).strip().lower()
        if urgency not in ("routine", "attention", "soon", "urgent"):
            urgency = "routine"

        return {
            "used": True,
            "provider": "DeepSeek",
            "model": self.model,
            "status_message": success_message,
            "summary_title": self._sanitize_text(parsed.get("summary_title"), "AI 个人增强解读"),
            "summary_for_patient": self._sanitize_text(
                parsed.get("summary_for_patient"),
                "本次 AI 分析结果更适合作为个人健康参考，请结合线下医生意见综合判断。"
            ),
            "urgency_level": urgency,
            "recommended_departments": self._normalize_list(
                parsed.get("recommended_departments"),
                ["骨科门诊", "脊柱外科门诊"]
            ),
            "visit_steps": self._normalize_list(
                parsed.get("visit_steps"),
                ["携带本报告和原始影像就诊", "向医生描述症状和持续时间", "按医生意见完善检查"]
            ),
            "lifestyle_recommendations": self._normalize_list(
                parsed.get("lifestyle_recommendations"),
                ["避免久坐久站", "减少弯腰搬重物", "保持规律作息"]
            ),
            "rehab_recommendations": self._normalize_list(
                parsed.get("rehab_recommendations"),
                ["不要自行进行高强度矫正训练", "训练前咨询康复科或专科医生", "不适加重时立即停止训练"]
            ),
            "red_flags": self._normalize_list(
                parsed.get("red_flags"),
                ["疼痛明显加重", "下肢麻木无力", "大小便功能异常"]
            ),
            "medical_disclaimer": self._sanitize_text(
                parsed.get("medical_disclaimer"),
                "本页内容由 AI 根据当前筛查结果生成，仅供辅助参考，不替代医生诊断。"
            ),
        }

    def generate_agent_copy(self, deidentified_summary, draft_message):
        """Improve wording only; no identifiers or raw medical assets are sent.

        The case agent has already made the deterministic safety and tool-policy
        decision before this method is reached.  This method cannot propose a
        tool, prescribe treatment, or alter the triage level.
        """

        self.refresh_config()
        safe_draft = self._sanitize_text(draft_message, "")[:900]
        if not self.api_key or not safe_draft:
            return {"used": False, "message": safe_draft}

        safe_summary = {
            "case_scope": str((deidentified_summary or {}).get("case_scope", "adult_spine_followup"))[:64],
            "triage": (deidentified_summary or {}).get("triage", {}),
            "symptom_scores": (deidentified_summary or {}).get("symptom_scores", {}),
            "assessment_states": (deidentified_summary or {}).get("assessment_states", []),
            "structured_case_facts": (deidentified_summary or {}).get("structured_case_facts", {}),
        }
        system_prompt = """
You are the wording component of a cautious spine-health assistant.
Call return_safe_copy exactly once with the revised message.
Write concise Simplified Chinese for adults.
You may improve the supplied draft's clarity but must preserve its safety boundaries.
Do not diagnose, prescribe medication, recommend surgery, invent results, mention hidden reasoning,
or add exercises/dosage. Do not ask for names, addresses, identifiers, raw images, DICOM, photos, or video.
"""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": "去标识化摘要：\n"
                    + json.dumps(safe_summary, ensure_ascii=False, separators=(",", ":"))
                    + "\n\n必须保持的草稿：\n"
                    + safe_draft,
                },
            ],
            # This is a schema-only function call.  The returned arguments are
            # validated locally and never grant the model an application tool.
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "return_safe_copy",
                        "description": "Return only the patient-facing wording while preserving the supplied safety boundary.",
                        "strict": True,
                        "parameters": {
                            "type": "object",
                            "properties": {"message": {"type": "string", "minLength": 1, "maxLength": 1000}},
                            "required": ["message"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "return_safe_copy"}},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
            "max_tokens": 420,
        }
        req = request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
            response_message = (((payload.get("choices") or [{}])[0].get("message")) or {})
            calls = response_message.get("tool_calls") or []
            if calls:
                function = (calls[0] or {}).get("function") or {}
                if function.get("name") != "return_safe_copy":
                    return {"used": False, "message": safe_draft}
                parsed = json.loads(function.get("arguments") or "{}")
            else:
                # Some compatible gateways return JSON content despite a forced
                # tool call.  Support it only as a non-authoritative fallback.
                content = str(response_message.get("content") or "").strip()
                parsed = json.loads(content)
            message = self._sanitize_text(parsed.get("message"), safe_draft)
            prohibited = ("确诊", "诊断为", "处方", "服用", "手术", "剂量")
            if any(token in message for token in prohibited):
                return {"used": False, "message": safe_draft}
            return {"used": bool(message), "message": message[:1000]}
        except Exception:
            # The case agent already has a safe local response.  Avoid leaking
            # provider details into a patient-facing workbench.
            return {"used": False, "message": safe_draft}

    def generate_personal_sections(self, analysis_context):
        self.refresh_config()

        if not self.api_key:
            return self._fallback_sections("未配置 DeepSeek API，已回退为本地基础解读。")

        payload_for_model = {
            "angles": analysis_context.get("angles", {}),
            "slips": analysis_context.get("slips", []),
            "current_mode": analysis_context.get("current_mode", "xray"),
            "has_measurement": analysis_context.get("has_measurement", False),
        }

        system_prompt = """
You are a cautious spinal screening assistant.
You must output json only.
Write in Simplified Chinese for patients.
Do not provide a definitive diagnosis.
Do not prescribe drugs.
Do not recommend surgery directly.
Only provide conservative guidance based on the given screening data.

The json schema must be:
{
  "summary_title": "一句简短标题，18字内",
  "summary_for_patient": "80到140字，面向个人用户解释当前结果",
  "urgency_level": "routine|attention|soon|urgent",
  "recommended_departments": ["字符串1", "字符串2", "字符串3"],
  "visit_steps": ["字符串1", "字符串2", "字符串3"],
  "lifestyle_recommendations": ["字符串1", "字符串2", "字符串3"],
  "rehab_recommendations": ["字符串1", "字符串2", "字符串3"],
  "red_flags": ["字符串1", "字符串2", "字符串3"],
  "medical_disclaimer": "一句边界声明"
}
"""

        user_prompt = (
            "请基于以下脊柱辅助筛查数据，生成给个人用户看的增强报告内容，并严格输出 json。\n\n"
            f"{json.dumps(payload_for_model, ensure_ascii=False, indent=2)}"
        )

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
            "max_tokens": 1200,
        }

        url = f"{self.base_url}/chat/completions"
        req = request.Request(
            url=url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
            payload = json.loads(raw)
            content = ((((payload.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()

            if not content:
                return self._fallback_sections("DeepSeek 返回为空，已回退为基础个人版建议。")

            parsed = json.loads(content)
            return self._normalize_sections(parsed, f"DeepSeek 增强解读已生成，模型：{self.model}")
        except error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = str(exc)
            return self._fallback_sections(f"DeepSeek 请求失败（HTTP {exc.code}），已回退为基础个人版建议。{detail[:180]}")
        except Exception as exc:
            return self._fallback_sections(f"DeepSeek 请求异常，已回退为基础个人版建议。{str(exc)[:180]}")
