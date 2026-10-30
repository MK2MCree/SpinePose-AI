"""Case-scoped, confirmed, immutable report snapshots and authenticated downloads."""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .spine_agent import SpineAgentPermissionError, SpineAgentValidationError


class ReportRequest(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)
    confirmation: bool = False
    request_id: str


def create_router(*, root, store, current_user, workspace_provider, profile_provider):
    router = APIRouter()
    root = Path(root).resolve()
    lock = threading.Lock()

    def access(user, case_id, write=False):
        case = store.get_case(user.user_id, case_id)
        if 'export' not in case.get('scopes', []):
            raise SpineAgentPermissionError('当前授权不允许导出此病例')
        if write:
            store.require_write(user.user_id, case_id)
        return case

    def folder(case_id, report_id):
        try:
            canonical = str(uuid.UUID(report_id))
        except (ValueError, AttributeError):
            raise SpineAgentValidationError('报告编号无效') from None
        if report_id != canonical:
            raise SpineAgentValidationError('报告编号无效')
        target = (root / case_id / canonical).resolve()
        if not target.is_relative_to(root):
            raise SpineAgentValidationError('报告路径无效')
        return target

    def error_response(error):
        status = 403 if isinstance(error, SpineAgentPermissionError) else 400
        return JSONResponse(status_code=status, content={'code': status, 'message': str(error)})

    @router.post('/api/cases/{case_id}/reports')
    def create_report(request: Request, case_id: str, payload: ReportRequest):
        from .agent_reports import build_agent_report, validate_report_options
        user = current_user(request)
        try:
            access(user, case_id, write=True)
            if payload.confirmation is not True:
                raise SpineAgentValidationError('请确认报告内容范围后再生成文件')
            options = validate_report_options(payload.options)
            target = folder(case_id, payload.request_id)
            with lock:
                manifest = target / 'manifest.json'
                if manifest.exists():
                    saved = json.loads(manifest.read_text(encoding='utf-8'))
                    if saved['options'] != options:
                        raise SpineAgentValidationError('本次生成编号已使用，请重新打开报告设置')
                    return {'code': 200, 'report': saved}
                workspace = workspace_provider(user, case_id)
                profile = profile_provider(workspace['case'])
                formats = ['pdf', 'docx'] if options['format'] == 'both' else [options['format']]
                # Produce every format before publishing anything, so errors cannot
                # leave a visible report claiming an absent second file.
                outputs = {fmt: build_agent_report(workspace, profile, options, fmt) for fmt in formats}
                cards = [{'type': 'document', 'title': f"{options['title']} · {fmt.upper()}",
                          'url': f'/api/cases/{case_id}/reports/{payload.request_id}/report.{fmt}'} for fmt in formats]
                saved = {'id': payload.request_id, 'options': options, 'files': cards}
                target.mkdir(parents=True, exist_ok=True)
                for fmt, content in outputs.items():
                    (target / f'report.{fmt}').write_bytes(content)
                # Recheck permission after potentially slow generation.
                access(user, case_id, write=True)
                store.append_agent_message(user.user_id, case_id, 'assistant',
                    '报告已生成，文件保留了本次病例数据快照。你可以下载 PDF 或可编辑的 Word 文件；后续更新病例不会改写这份报告。',
                    audit={'answer_mode': 'workflow', 'report_id': payload.request_id, 'result_cards': cards})
                manifest.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
                return {'code': 200, 'report': saved}
        except (SpineAgentValidationError, SpineAgentPermissionError, ValueError) as error:
            return error_response(error)
        except (ImportError, OSError):
            return JSONResponse(status_code=503, content={'code': 503, 'message': '报告组件或存储暂不可用，请检查 PDF/Word 依赖及本地磁盘后重试。'})

    @router.get('/api/cases/{case_id}/reports/{report_id}/{filename}')
    def download_report(request: Request, case_id: str, report_id: str, filename: str):
        user = current_user(request)
        try:
            access(user, case_id)
            target = folder(case_id, report_id)
            if filename not in {'report.pdf', 'report.docx'} or not (target / 'manifest.json').is_file() or not (target / filename).is_file():
                return JSONResponse(status_code=404, content={'code': 404, 'message': '报告不存在'})
            media = 'application/pdf' if filename.endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            return FileResponse(target / filename, media_type=media, filename=f'SpineInsight-{report_id[:8]}-{filename}',
                                headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
        except (SpineAgentValidationError, SpineAgentPermissionError) as error:
            return error_response(error)

    return router
