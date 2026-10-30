import io
import zipfile
from pathlib import Path

import pytest

from mindspine.agent_reports import parse_report_request, validate_report_options, build_agent_report, report_capabilities, _facts


def sample_workspace():
    return {"case": {"id": "test-case", "title": "健康随访"},
            "questionnaire": {"protocol_version": "spine-intake-v3", "created_at": 1790726400,
                "answers": {"pain_score": 3, "symptom_location": ["腰部"], "duration": "超过 3 个月", "symptom_change": "有所减轻", "radiation": "没有", "daily_function": {"walking": "none", "sitting": "mild", "sleep": "none", "dressing": "none"}, "history_status": "no", "treatment_status": "no", "goals": "整理资料与比较变化"},
                "risk_level": "incomplete", "triage": {"risk_level": "incomplete", "analysis": "自报疼痛为 3 / 10，久坐时不适。安全问题未全部填写，不能完成分流。"}},
            "agent_tool_runs": [{"id": "run-1", "state": "completed", "created_at": 1790726400, "result_cards": [{"title": "DR 关键点与角度分析", "summary": "角度已记录，等待专业复核", "metrics": {"LL": 41.2}, "metric_units": {"LL": "°"}, "limitations": ["辅助测量，不是诊断"]}]}],
            "legacy_timeline": [{"kind": "checkin", "payload": {"pain_score": 3, "notes": "休息后缓解"}}, {"kind": "rgb", "title": "体表资料", "created_at": 1790726400, "payload": {"summary": "已关联体表照片"}}],
            "review_tasks": [{"state": "open", "summary": "请医生复核影像测量"}]}


@pytest.mark.parametrize("message", ["请生成一份健康报告", "帮我做份健康报告", "给我做个PDF报告", "帮我出份Word档案", "帮我导出健康档案"])
def test_positive_intents(message):
    assert parse_report_request(message)


@pytest.mark.parametrize("message", ["解释一下这份报告", "我的报告说明什么", "不要生成报告", "不用做一份报告", "我不想导出报告", "不需要导出PDF", "取消生成报告", "这张片子能看出什么"])
def test_non_creation_intents(message):
    assert parse_report_request(message) is None


def test_customization():
    options = parse_report_request('请生成只包含问卷的医生简版Word报告，标题为“我的就医资料”')
    assert options["audience"] == "clinician"
    assert options["detail"] == "brief"
    assert options["format"] == "docx"
    assert options["sections"] == ["questionnaire"]
    assert options["title"] == "我的就医资料"


def test_declined_format_not_reenabled():
    assert parse_report_request("不用导出PDF，请生成Word报告")["format"] == "docx"


@pytest.mark.parametrize("options", [{"url": "https://example.org"}, {"sections": ["files"]}, {"sections": []}, {"audience": "any"}, {"format": "html"}, {"title": "\n"}, {"date_from": "2026-02-30"}, {"date_from": "2026-10-01", "date_to": "2026-09-01"}])
def test_reject_unsupported_options(options):
    with pytest.raises(ValueError): validate_report_options(options)


def test_scoped_content_and_no_fake_values():
    options = validate_report_options({"sections": ["questionnaire"]})
    text = str(_facts(sample_workspace(), {"display_name": "private-profile"}, options))
    assert "private-profile" not in text and "41.2" not in text
    assert "sitting" not in text and "能完成，需要偶尔换姿势" in text
    assert "未记录" in text


def test_date_scope_retains_undated():
    options = validate_report_options({"date_from": "2027-01-01"})
    text = str(_facts(sample_workspace(), {}, options))
    assert "41.2" not in text
    assert "休息后缓解" in text and "未记录日期" in text


def test_clinical_record_date_wins_over_upload_date():
    workspace = {"case": {"id": "date-test"}, "legacy_timeline": [{"kind": "clinical", "recorded_at": "2020-01-01", "created_at": 1790726400, "payload": {"cobb_angle": 15}}]}
    text = str(_facts(workspace, {}, validate_report_options({"date_from": "2026-01-01"})))
    assert "15" not in text


def test_profile_uses_birth_date_and_known_sex_labels():
    text = str(_facts({"case": {"id": "profile-test"}}, {"birth_date": "1995-01-01", "sex": "female"}, validate_report_options({"sections": ["profile"]})))
    assert "性别：女" in text and "年龄：未记录" not in text


def test_real_pdf_and_docx():
    workspace = sample_workspace()
    assert set(report_capabilities()["formats"]) == {"pdf", "docx"}
    pdf = build_agent_report(workspace, {"display_name": "演示用户"}, {"detail": "brief"}, "pdf")
    assert pdf.startswith(b"%PDF-") and len(pdf) > 5000
    docx = build_agent_report(workspace, {"display_name": "演示用户"}, {"detail": "brief"}, "docx")
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        xml = archive.read("word/document.xml").decode()
        assert "演示用户" in xml and "41.2°" in xml
        assert "Title" in xml and "Heading1" in xml
        assert "未记录" in xml


def test_user_markup_is_plain_text():
    options = {"title": "我的 <b>报告</b>"}
    docx = build_agent_report({"case": {"id": "test"}}, {}, options, "docx")
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        assert "&lt;b&gt;" in archive.read("word/document.xml").decode()


if __name__ == "__main__":
    # Local synthetic fixtures only, deliberately outside user-facing exports.
    output = Path(__file__).resolve().parents[1] / ".analysis_tmp" / "agent-report-qa"
    output.mkdir(parents=True, exist_ok=True)
    for format in ("pdf", "docx"):
        (output / ("sample." + format)).write_bytes(build_agent_report(sample_workspace(), {"display_name": "演示用户", "age": 32}, {"detail": "brief"}, format))
    print(output)
