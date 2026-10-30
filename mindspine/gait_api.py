"""Authenticated HTTP boundary for GaitService; no standalone ScoNet server."""
from __future__ import annotations

import json
import mimetypes
from typing import Callable

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from mindspine.gait import GaitError, GaitService, validate_identifier


def create_router(service: GaitService, get_user: Callable,
                  authorize_case: Callable | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/gait", tags=["gait"])

    def user_id(request: Request) -> str:
        user = get_user(request)
        identifier = user if isinstance(user, str) else getattr(user, "user_id", None)
        if not identifier:
            raise HTTPException(401, "请先登录")
        return identifier

    def access_case(owner: str, case_id: str) -> None:
        validate_identifier(case_id, "病例标识")
        if authorize_case is not None:
            try:
                authorize_case(owner, case_id)
            except PermissionError:
                raise GaitError("无权访问此病例", 403) from None
            except KeyError:
                raise GaitError("病例不存在", 404) from None

    def owned_job(request: Request, job_id: str) -> tuple[str, dict]:
        owner = user_id(request)
        job = service.get(owner, job_id)
        access_case(owner, job["case_id"])
        return owner, job

    def error_response(error: GaitError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code,
                            content={"code": error.status_code, "message": str(error)})

    @router.get("/health")
    def health(request: Request):
        user_id(request)
        return {"code": 200, "health": service.health()}

    @router.get("/jobs")
    def list_jobs(request: Request, case_id: str | None = None, limit: int = 50):
        try:
            owner = user_id(request)
            if case_id is not None:
                access_case(owner, case_id)
            jobs = service.list(owner, case_id, limit)
            if authorize_case is not None and case_id is None:
                allowed = []
                for job in jobs:
                    try:
                        access_case(owner, job["case_id"])
                        allowed.append(job)
                    except GaitError as error:
                        if error.status_code in {403, 404}:
                            continue
                        raise
                jobs = allowed
            return {"code": 200, "jobs": jobs}
        except GaitError as error:
            return error_response(error)

    @router.post("/jobs")
    def create_job(request: Request, file: UploadFile = File(...),
                   case_id: str = Form(...), options_json: str = Form("{}")):
        try:
            owner = user_id(request)
            access_case(owner, case_id)
            if len(options_json) > 8192:
                raise GaitError("分析选项过长")
            try:
                options = json.loads(options_json)
            except ValueError:
                raise GaitError("分析选项不是有效 JSON") from None
            job = service.create(owner, case_id, file.file, file.filename or "video.mp4", options)
            return JSONResponse(status_code=202, content={"code": 202, "job": job})
        except GaitError as error:
            return error_response(error)

    @router.get("/jobs/{job_id}")
    def get_job(request: Request, job_id: str):
        try:
            _, job = owned_job(request, job_id)
            return {"code": 200, "job": job}
        except GaitError as error:
            return error_response(error)

    @router.delete("/jobs/{job_id}")
    def cancel_job(request: Request, job_id: str):
        try:
            owner, _ = owned_job(request, job_id)
            return {"code": 200, "job": service.cancel(owner, job_id)}
        except GaitError as error:
            return error_response(error)

    @router.get("/jobs/{job_id}/report")
    def report(request: Request, job_id: str):
        try:
            owner, _ = owned_job(request, job_id)
            return {"code": 200, "report": service.report(owner, job_id)}
        except GaitError as error:
            return error_response(error)

    @router.get("/jobs/{job_id}/files/{filename}")
    def artifact(request: Request, job_id: str, filename: str, download: bool = False):
        try:
            owner, _ = owned_job(request, job_id)
            path = service.artifact(owner, job_id, filename)
            media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            # Starlette FileResponse implements byte ranges for browser videos.
            return FileResponse(path, media_type=media_type, filename=filename if download else None,
                                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
        except GaitError as error:
            return error_response(error)

    @router.post("/jobs/{job_id}/rerun")
    def rerun(request: Request, job_id: str, payload: dict = Body(default={})):
        try:
            owner, _ = owned_job(request, job_id)
            if "options" in payload and set(payload) != {"options"}:
                raise GaitError("存在不支持的重新分析选项")
            options = payload.get("options", payload)
            if not isinstance(options, dict):
                raise GaitError("分析选项必须是 JSON 对象")
            return JSONResponse(status_code=202, content={"code": 202, "job": service.rerun(owner, job_id, options)})
        except GaitError as error:
            return error_response(error)

    @router.post("/jobs/{job_id}/review")
    def review(request: Request, job_id: str, payload: dict = Body(...)):
        try:
            owner, _ = owned_job(request, job_id)
            if set(payload) - {"notes", "archive"}:
                raise GaitError("存在不支持的复核选项")
            return {"code": 200, "job": service.review_archive(owner, job_id, payload.get("notes", ""), payload.get("archive"))}
        except GaitError as error:
            return error_response(error)

    return router
