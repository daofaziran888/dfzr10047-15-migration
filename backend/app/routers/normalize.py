"""版本化归一系数清单接口：预检、导入、暂停/恢复、冲突核验与换算凭据导出。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services import normalize_manifest as manifest

router = APIRouter(prefix="/api/normalize", tags=["归一系数清单"])


class ManifestPayload(BaseModel):
    版本号: str = ""
    来源: str = ""
    文件名: str = "manifest.json"
    内容: str = ""
    条目: list[dict[str, Any]] = Field(default_factory=list)
    pause_after: int | None = None
    fail_rows: list[int] = Field(default_factory=list)
    resume_with_payload: bool = False

    def manifest_payload(self) -> dict[str, Any]:
        """只把清单定义传给领域服务，控制参数不混进内容指纹。"""
        return {
            "版本号": self.版本号,
            "来源": self.来源,
            "文件名": self.文件名,
            "内容": self.内容,
            "条目": self.条目,
        }


class ConflictDecision(BaseModel):
    decision: str


class HandoffPayload(BaseModel):
    转型编号: str = ""
    字段名称: str = ""
    原始取值: Any = ""
    原始单位: str = ""
    目标单位: str = ""
    适用来源: str = ""
    命中日期: str = ""


class LegacyComputePayload(BaseModel):
    raw_value: Any = ""


@router.get("/manifests")
def list_manifests() -> dict[str, Any]:
    return {"items": manifest.list_manifests(), "total": len(manifest.list_manifests())}


@router.get("/legacy")
def list_legacy() -> dict[str, Any]:
    items = manifest.list_legacy()
    return {"items": items, "total": len(items)}


@router.post("/legacy/{factor_no}/compute")
def compute_legacy(factor_no: str, payload: LegacyComputePayload) -> dict[str, Any]:
    return manifest.compute_legacy(factor_no, payload.raw_value)


@router.post("/import/preview")
def preview_import(payload: ManifestPayload) -> dict[str, Any]:
    return manifest.preview_import(payload.manifest_payload())


@router.post("/import")
def import_manifest(payload: ManifestPayload) -> dict[str, Any]:
    return manifest.import_manifest(
        payload.manifest_payload(), pause_after=payload.pause_after,
        fail_rows=payload.fail_rows)


@router.get("/import/{manifest_no}")
def get_manifest(manifest_no: str) -> dict[str, Any]:
    data = manifest.get_manifest(manifest_no)
    if data is None:
        raise HTTPException(status_code=404, detail=f"清单 {manifest_no} 不存在")
    return data


@router.post("/import/{manifest_no}/pause")
def pause_import(manifest_no: str) -> dict[str, Any]:
    return manifest.pause_import(manifest_no)


@router.post("/import/{manifest_no}/resume")
def resume_import(manifest_no: str, payload: ManifestPayload | None = None) -> dict[str, Any]:
    body = payload.manifest_payload() if payload is not None else None
    if body is not None and not body.get("内容") and not body.get("条目"):
        body = None
    return manifest.resume_import(manifest_no, body)


@router.post("/import/{manifest_no}/retry-failed")
def retry_failed(manifest_no: str) -> dict[str, Any]:
    return manifest.retry_failed(manifest_no)


@router.post("/import/{manifest_no}/rollback")
def rollback_manifest(manifest_no: str, reason: str = Query(default="人工回滚")) -> dict[str, Any]:
    return manifest.rollback_manifest(manifest_no, reason=reason)


@router.get("/conflicts")
def conflicts() -> dict[str, Any]:
    return manifest.conflicts()


@router.post("/conflicts/{entry_id}/resolve")
def resolve_conflict(entry_id: int, payload: ConflictDecision) -> dict[str, Any]:
    return manifest.resolve_conflict(entry_id, payload.decision)


@router.post("/handoffs")
def submit_handoff(payload: HandoffPayload) -> dict[str, Any]:
    return manifest.submit_handoff(payload.model_dump())


@router.get("/handoffs")
def list_handoffs() -> dict[str, Any]:
    items = manifest.list_handoffs()
    return {"items": items, "total": len(items)}


@router.get("/conversions")
def list_conversions(
    version: str | None = Query(default=None),
) -> dict[str, Any]:
    filters = {"消费版本号": version} if version else None
    items = manifest.list_conversions(filters)
    return {
        "items": items,
        "total": len(items),
        "versions": manifest.conversion_versions(),
    }


@router.get("/conversions/versions")
def conversion_versions() -> dict[str, Any]:
    return {"versions": manifest.conversion_versions()}


@router.get("/conversions/export")
def export_conversions(version: str | None = Query(default=None)) -> StreamingResponse:
    content, label = manifest.export_conversions(version)
    return StreamingResponse(
        iter([content]), media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="normalize-conversions-{label}.csv"',
        },
    )


@router.get("/conversions/{cast_no}/credential")
def conversion_credential(
    cast_no: str,
    receipt: str | None = Query(default=None),
) -> dict[str, Any]:
    data = manifest.conversion_credential(cast_no, receipt)
    if data is None:
        raise HTTPException(status_code=404, detail=f"转型 {cast_no} 没有换算凭据")
    return data


@router.get("/audits")
def list_audits() -> dict[str, Any]:
    items = manifest.list_audits()
    return {"items": items, "total": len(items)}


@router.get("/snapshot")
def snapshot() -> dict[str, Any]:
    return manifest.manifest_snapshot()
