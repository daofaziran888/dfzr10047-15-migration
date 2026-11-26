"""版本化归一系数清单：迁移、导入回执、冲突核验、换算凭据与回滚的整链路测试。"""
from __future__ import annotations

import pytest

from app.services import SERVICES, normalize_manifest as manifest
from app.store import store


@pytest.fixture(autouse=True)
def fresh_state():
    store.reload_seed()
    copy = store.rows("file_intake")
    yield
    store.reload_seed()
    assert copy is not None


V2_ENTRIES = [
    {"字段名称": "weight", "原始单位": "kg", "目标单位": "g",
     "换算系数": "1024", "适用来源": "sftp-node-03"},
    {"字段名称": "height", "原始单位": "m", "目标单位": "cm",
     "换算系数": "100", "适用来源": "sftp-node-03"},
]


def v2_payload(**overrides):
    payload = {
        "版本号": "NM-v2",
        "来源": "sftp-node-03",
        "文件名": "normalize-v2.json",
        "条目": [dict(item) for item in V2_ENTRIES],
    }
    payload.update(overrides)
    return payload


def handoff(raw: str = "2", cast: str = "VC-002") -> dict:
    return manifest.submit_handoff({
        "转型编号": cast, "字段名称": "weight", "原始取值": raw,
        "原始单位": "kg", "适用来源": "sftp-node-03",
        "命中日期": "2026-09-03",
    })


# ----------------------------------------------------------------------
# 存量迁移与无版本计算
# ----------------------------------------------------------------------
def test_legacy_factor_migrated_into_first_manifest():
    legacy = {row["归一编号"]: row for row in manifest.list_legacy()}
    assert legacy["UN-006"]["版本化"] is True
    assert legacy["UN-006"]["清单版本号"] == "NM-v1"
    assert legacy["UN-006"]["迁移状态"] == "已迁入"
    assert legacy["UN-006"]["换算系数"] == "1000.0"


def test_unversioned_legacy_factor_still_computes_original_value():
    legacy = {row["归一编号"]: row for row in manifest.list_legacy()}
    assert legacy["UN-001"]["版本化"] is False
    assert legacy["UN-001"]["使用方式"].startswith("无版本存量")
    result = manifest.compute_legacy("UN-001", 3)
    assert result["ok"] is True
    assert result["换算值"] == pytest.approx(3072.0)
    assert result["消费版本号"] == ""
    assert result["无版本存量"] is True


def test_disabled_factor_cannot_compute():
    result = manifest.compute_legacy("UN-005", 3)
    assert result["ok"] is False
    assert "无法计算" in result["message"]


# ----------------------------------------------------------------------
# 预检：影响在途转型 + 冲突值
# ----------------------------------------------------------------------
def test_preview_lists_conflicts_and_impacted_inflight_handoffs():
    handoff("2")
    preview = manifest.preview_import(v2_payload())
    assert preview["ok"] is True
    assert preview["可导入"] is True
    assert preview["重复清单"] is False
    assert [item["字段名称"] for item in preview["冲突值"]] == ["weight"]
    assert preview["冲突值"][0]["申报系数"] == "1024"
    assert preview["冲突值"][0]["冲突系数编号"] == "UN-006"
    impacted = {item["交接编号"] for item in preview["影响在途转型"]}
    # 已换算完成的 2kg 交接按当时版本结了凭据，不算在途；种子里的 3kg 仍在途
    assert impacted == {"CH-001"}
    assert "CH-002" not in impacted
    # 文件里无冲突的 B 不进冲突清单
    assert "height" not in [item["字段名称"] for item in preview["冲突值"]]


def test_preview_rejects_violations_and_missing_version():
    bad = v2_payload(版本号="", 条目=[
        {"字段名称": "height", "原始单位": "m", "目标单位": "cm",
         "换算系数": "abc", "适用来源": "sftp-node-03"},
    ])
    preview = manifest.preview_import(bad)
    assert preview["可导入"] is False
    types = {item["类型"] for item in preview["兼容校验"]["违规"]}
    assert "兼容约定违规" in types
    assert "缺少版本号" in types
    result = manifest.import_manifest(bad)
    assert result["ok"] is False
    assert "未登记版本" in result["message"]


# ----------------------------------------------------------------------
# 导入、暂停、恢复、重复登记
# ----------------------------------------------------------------------
def test_same_file_import_registers_only_once():
    first = manifest.import_manifest(v2_payload())
    assert first["ok"] is True
    assert first["重复登记"] is False
    second = manifest.import_manifest(v2_payload())
    assert second["重复登记"] is True
    assert second["清单编号"] == first["清单编号"]
    assert len([row for row in store.rows("unit_normalize")
                if row.get("清单版本号") == "NM-v2"]) == 1
    manifests = [row for row in manifest.list_manifests()
                 if row["版本号"] == "NM-v2"]
    assert len(manifests) == 1


def test_paused_import_resumes_with_same_file_and_keeps_inflight():
    handoff("2")
    started = manifest.import_manifest(v2_payload(), pause_after=1)
    assert started["回执"]["状态"] == "已暂停"
    assert started["回执"]["待导入"] == 1
    assert started["回执"]["可继续处理"] is True
    # A 冲突待核验；B 还没写
    nm2 = {row["归一编号"]: row for row in manifest.list_legacy()
           if row.get("清单版本号") == "NM-v2"}
    assert nm2 == {}
    resumed = manifest.resume_import(started["清单编号"], v2_payload())
    assert resumed["回执"]["状态"] == "冲突待处理"
    groups = resumed["回执"]["条目分组"]
    assert len(groups["已生效"]) == 1 and "height" in groups["已生效"][0]
    assert len(groups["待核验"]) == 1 and "weight" in groups["待核验"][0]
    # A 在途：旧因子仍生效，A=1024 不抢先生效
    active = [row for row in store.rows("unit_normalize")
              if row.get("字段名称") == "weight" and row.get("status") == "已生效"]
    assert [row["换算系数"] for row in active] == ["1000.0"]
    assert handoff("5")["挂起"] is True


def test_resume_refuses_different_file():
    started = manifest.import_manifest(v2_payload(), pause_after=1)
    other = v2_payload(文件名="other.json", 条目=[
        {"字段名称": "volume", "原始单位": "L", "目标单位": "mL",
         "换算系数": "1000", "适用来源": "sftp-node-03"},
    ])
    result = manifest.resume_import(started["清单编号"], other)
    assert result["ok"] is False
    assert "指纹" in result["message"]


def test_partial_write_failures_only_retry_failed_rows():
    started = manifest.import_manifest(v2_payload(), pause_after=2, fail_rows=[2])
    assert started["回执"]["状态"] == "冲突待处理"
    assert started["回执"]["失败"] == 1
    assert started["回执"]["可继续处理"] is True
    retried = manifest.retry_failed(started["清单编号"])
    assert retried["ok"] is True
    groups = retried["回执"]["条目分组"]
    assert groups["失败"] == []
    assert len(groups["已生效"]) == 1
    # A 的冲突条目不因重试被改写
    assert len(groups["待核验"]) == 1
    assert "weight" in groups["待核验"][0]


# ----------------------------------------------------------------------
# 冲突页与裁决
# ----------------------------------------------------------------------
def test_conflict_page_separates_effective_and_pending():
    manifest.import_manifest(v2_payload())
    page = manifest.conflicts()
    groups = {group["键"]: group for group in page["分组"]}
    weight_key = "weight|sftp-node-03|kg"
    assert weight_key in groups
    group = groups[weight_key]
    assert group["已生效"]["归一编号"] == "UN-006"
    assert group["已生效"]["换算系数"] == "1000.0"
    assert len(group["待补核验"]) == 1
    assert group["待补核验"][0]["换算系数"] == "1024"
    assert group["需补核验"] is True
    assert "height" in {row["字段名称"] for row in page["已生效条目"]}
    assert page["冲突键"] == ["weight|sftp-node-03|kg"]


def test_resolve_conflict_keep_new_replaces_old_factor():
    manifest.import_manifest(v2_payload())
    page = manifest.conflicts()
    entry_id = page["分组"][0]["待补核验"][0]["条目id"]
    resolved = manifest.resolve_conflict(entry_id, "保留新")
    assert resolved["ok"] is True
    assert resolved["停用因子"] == ["UN-006"]
    active = [row for row in store.rows("unit_normalize")
              if row.get("字段名称") == "weight" and row.get("status") == "已生效"]
    assert [row["换算系数"] for row in active] == ["1024"]
    assert manifest.conflicts()["分组"] == []


def test_resolve_conflict_keep_old_skips_new_entry():
    manifest.import_manifest(v2_payload())
    entry_id = manifest.conflicts()["分组"][0]["待补核验"][0]["条目id"]
    resolved = manifest.resolve_conflict(entry_id, "保留旧")
    assert resolved["ok"] is True
    assert resolved["条目"]["状态"] == "已跳过"
    active = [row for row in store.rows("unit_normalize")
              if row.get("字段名称") == "weight" and row.get("status") == "已生效"]
    assert [row["换算系数"] for row in active] == ["1000.0"]


def test_handoff_settles_only_after_conflict_resolved():
    handoff("2")
    manifest.import_manifest(v2_payload())
    assert manifest.settle_handoff("CH-001")["ok"] is False
    entry_id = manifest.conflicts()["分组"][0]["待补核验"][0]["条目id"]
    resolved = manifest.resolve_conflict(entry_id, "保留新")
    settled = {item["交接编号"]: item for item in resolved["结算交接"]}
    assert settled["CH-001"]["归一取值"] == pytest.approx(3072.0)
    assert settled["CH-001"]["消费版本号"] == "NM-v2"


# ----------------------------------------------------------------------
# 换算凭据 / 导出 / 回滚历史
# ----------------------------------------------------------------------
def test_handoff_consumes_effective_version_and_records_credential():
    result = handoff("2")
    assert result["ok"] is True
    assert result["归一取值"] == pytest.approx(2000.0)
    receipt = result["换算凭据"]
    assert receipt["消费版本号"] == "NM-v1"
    assert receipt["消费系数编号"] == "UN-006"
    assert receipt["因子快照"]["换算系数"] == "1000.0"


def test_history_credential_keeps_original_factor_after_rollback():
    handoff("2")
    started = manifest.import_manifest(v2_payload())
    # 导入产生冲突期间，同一转型新的交接保持挂起
    pending = handoff("5")
    assert pending["挂起"] is True
    rolled = manifest.rollback_manifest(started["清单编号"])
    # 回滚只解挂，不覆盖已落凭据；挂起动作的交接按回滚后的生效因子补算
    settled = {item["交接编号"]: item for item in rolled["结算交接"]}
    assert settled[pending["交接"]["交接编号"]]["归一取值"] == pytest.approx(5000.0)
    cred = manifest.conversion_credential("VC-002", "CV-001")
    assert cred is not None
    assert cred["换算值"] == "2000.0"
    assert cred["消费版本号"] == "NM-v1"
    assert cred["因子当前状态"]["归一编号"] == "UN-006"
    assert "不回写" in cred["历史口径"]


def test_export_follows_selected_version():
    handoff("2")
    started = manifest.import_manifest(v2_payload())
    assert started["清单编号"] == "NM-002"
    content, label = manifest.export_conversions("NM-v1")
    assert label == "NM-v1"
    assert "CV-001" in content
    assert "NM-v1" in content
    empty, _ = manifest.export_conversions("NM-v2")
    assert "CV-001" not in empty
    assert manifest.conversion_versions() == ["NM-v1"]


def test_rollback_disables_manifest_factors_and_keeps_migrated_baseline():
    handoff("2")
    started = manifest.import_manifest(v2_payload())
    rolled = manifest.rollback_manifest(started["清单编号"])
    assert rolled["ok"] is True
    assert rolled["停用因子"]
    statuses = {row["归一编号"]: row["status"] for row in manifest.list_legacy()}
    assert statuses["UN-006"] == "已生效"
    for factor_no in rolled["停用因子"]:
        assert statuses[factor_no] == "已停用"
    assert manifest.get_manifest(started["清单编号"])["状态"] == "已回滚"


def test_value_cast_service_hands_real_value_to_manifest():
    svc = SERVICES["value_cast"]
    # VC-001 是 float64 规则，转型后的数值才交给清单计算
    result = svc.handoff_to_normalize(
        "VC-001", "weight", "2", "kg", "sftp-node-03", "2026-09-03",
        "2026-09-03T12:00:00")
    assert result["ok"] is True
    assert result["转型取值"] == pytest.approx(2.0)
    assert result["归一取值"] == pytest.approx(2000.0)
    assert result["消费版本号"] == "NM-v1"
    cred = manifest.conversion_credential("VC-001")
    assert cred["换算凭据编号"] == result["换算凭据"]["换算凭据编号"]


def test_value_cast_rejects_non_numeric_before_handoff():
    svc = SERVICES["value_cast"]
    result = svc.handoff_to_normalize(
        "VC-001", "weight", "abc", "kg", "sftp-node-03", "2026-09-03",
        "2026-09-03T12:00:00")
    assert result["ok"] is False
    assert "未通过" in result["message"]


def test_duplicate_manifest_does_not_reapply_conflicts():
    first = manifest.import_manifest(v2_payload())
    before = len(store.rows("unit_normalize"))
    manifest.import_manifest(v2_payload())
    assert len(store.rows("unit_normalize")) == before
    assert first["回执"]["回执编号"] == manifest.import_manifest(
        v2_payload())["回执"]["回执编号"]


# ----------------------------------------------------------------------
# 验收链路：A=1000 已生效 → 2kg 转换 → 上传 A=1024 + B → 暂停/恢复/回滚
# ----------------------------------------------------------------------
def test_acceptance_pause_resume_conflict_and_rollback_keeps_credential():
    # 1. A=1000 已生效，提交 2 kg 转换，落到 NM-v1 凭据 2000 g
    first = handoff("2")
    assert first["归一取值"] == pytest.approx(2000.0)
    assert first["消费版本号"] == "NM-v1"

    # 2. 上传同一来源第二份清单：A=1024 冲突 + B 无冲突，只写第一条后暂停
    started = manifest.import_manifest(v2_payload(), pause_after=1)
    manifest_no = started["清单编号"]
    assert started["回执"]["状态"] == "已暂停"

    # 3. 用同一文件恢复：B 生效，A 停在待补核验；旧 A 仍生效
    resumed = manifest.resume_import(manifest_no, v2_payload())
    assert resumed["回执"]["状态"] == "冲突待处理"
    assert any("height" in label for label in resumed["回执"]["条目分组"]["已生效"])
    assert any("weight" in label for label in resumed["回执"]["条目分组"]["待核验"])
    active_weight = [row for row in store.rows("unit_normalize")
                     if row.get("字段名称") == "weight" and row.get("status") == "已生效"]
    assert [row["换算系数"] for row in active_weight] == ["1000.0"]

    # 4. A 在途：新的 2 kg 交接不抢先用 1024，也不改写已有 2000 g 凭据
    inflight = handoff("2")
    assert inflight["ok"] is False and inflight["挂起"] is True
    cred = manifest.conversion_credential("VC-002")
    assert cred["换算值"] == "2000.0"
    assert cred["消费版本号"] == "NM-v1"

    # 5. 回滚第二份清单：B 停用，旧凭据仍可重看 2000 g
    rolled = manifest.rollback_manifest(manifest_no)
    assert rolled["ok"] is True
    height = [row for row in manifest.list_legacy() if row.get("字段名称") == "height"]
    assert [row["status"] for row in height] == ["已停用"]
    cred_after = manifest.conversion_credential("VC-002")
    assert cred_after["换算值"] == "2000.0"
    assert cred_after["消费版本号"] == "NM-v1"
    assert cred_after["因子当前状态"]["换算系数"] == "1000.0"
    exported, _label = manifest.export_conversions("NM-v1")
    assert "2000.0" in exported
