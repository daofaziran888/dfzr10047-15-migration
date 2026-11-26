"""版本化归一系数清单：存量无版本系数迁入、导入回执、冲突核验与换算凭据。

核心约定：
- 同一来源、同一字段、同一原始单位只允许一条生效因子，冲突条目不得自动生效；
- 同一份清单按内容指纹去重，重复导入只登记一次，暂停后可用同一文件恢复；
- 导入可以只写完一部分，已写入的条目保留，恢复时只补未完成与失败条目；
- 每次换算都落一份凭据快照，回滚清单不会改写历史凭据；
- 无版本存量系数仍可计算原值，历史已确认字段保留当时的系数和单位。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

from app.store import store

MANIFEST_MODULE = "normalize_manifest"
ENTRY_MODULE = "normalize_import_entry"
RECEIPT_MODULE = "normalize_receipt"
CONVERSION_MODULE = "normalize_conversion_receipt"
HANDOFF_MODULE = "normalize_cast_handoff"

UNVERSIONED = "无版本存量"

# 清单状态
MANIFEST_DRAFT = "草稿"
MANIFEST_IMPORTING = "导入中"
MANIFEST_PAUSED = "已暂停"
MANIFEST_PARTIAL = "部分导入"
MANIFEST_CONFLICT = "冲突待处理"
MANIFEST_ACTIVE = "已生效"
MANIFEST_ROLLED_BACK = "已回滚"

# 条目状态
ENTRY_PENDING = "待导入"
ENTRY_ACTIVE = "已生效"
ENTRY_CONFLICT = "冲突待核验"
ENTRY_FAILED = "失败"
ENTRY_SKIPPED = "已跳过"
ENTRY_ROLLED_BACK = "已回滚"

COMPAT_CONVENTIONS = [
    "字段名称、原始单位、目标单位与适用来源必须非空",
    "换算系数必须是大于 0 的有限数值",
    "同一来源、同一字段、同一原始单位只允许一条生效因子",
    "目标单位必须与该键已生效因子的目标单位一致",
    "冲突系数不得自动覆盖已生效因子，只能进入待补核验",
]

_state: dict[str, Any] | None = None


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def _float(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _label(entry: dict[str, Any]) -> str:
    return (
        f"第{entry.get('行号')}行 {entry.get('字段名称')} "
        f"{entry.get('原始单位')}→{entry.get('目标单位')} ×{entry.get('换算系数')}"
    )


def _fingerprint(version: str, source: str, entries: list[dict[str, Any]]) -> str:
    canonical = json.dumps({
        "version": version,
        "source": source,
        "entries": [
            {
                "field": str(item.get("字段名称") or "").strip(),
                "from": str(item.get("原始单位") or "").strip(),
                "to": str(item.get("目标单位") or "").strip(),
                "factor": _float(item.get("换算系数")),
                "source": str(item.get("适用来源") or source).strip(),
            }
            for item in entries
        ],
    }, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _parse_entries(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """解析清单内容：支持条目数组、JSON 文本和 CSV 文本。"""
    raw_entries = payload.get("条目")
    if isinstance(raw_entries, list) and raw_entries:
        return [dict(item) for item in raw_entries], ""
    content = str(payload.get("内容") or "").strip()
    if not content:
        return [], "清单内容为空"
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            parsed = parsed.get("条目") or parsed.get("entries") or []
        if not isinstance(parsed, list):
            return [], "清单 JSON 必须是条目数组或包含 entries 的对象"
        return [dict(item) for item in parsed], ""
    except json.JSONDecodeError:
        pass
    rows: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(content))
    for index, line in enumerate(reader):
        if not line or all(not cell.strip() for cell in line):
            continue
        if index == 0 and any(cell.strip() in ("字段名称", "field") for cell in line):
            continue
        if len(line) < 4:
            return [], f"CSV 第 {index + 1} 行少于 4 列"
        rows.append({
            "行号": index + 1,
            "字段名称": line[0].strip(),
            "原始单位": line[1].strip(),
            "目标单位": line[2].strip(),
            "换算系数": line[3].strip(),
            "适用来源": line[4].strip() if len(line) > 4 else "",
        })
    return rows, "" if rows else "清单内容为空"


def _next_no(prefix: str, key: str, collection: str, width: int = 3) -> str:
    numbers: list[int] = []
    for row in _get_state()[collection]:
        tail = str(row.get(key) or "").rsplit("-", 1)[-1]
        if tail.isdigit():
            numbers.append(int(tail))
    return f"{prefix}-{max(numbers, default=0) + 1:0{width}d}"


def _next_factor_no(prefix: str) -> str:
    numbers: list[int] = []
    for row in store.rows("unit_normalize"):
        text = str(row.get("归一编号") or "")
        if not text.startswith(prefix + "-"):
            continue
        tail = text.rsplit("-", 1)[-1]
        if tail.isdigit():
            numbers.append(int(tail))
    return f"{prefix}-{max(numbers, default=0) + 1:03d}"


def _factor_version(row: dict[str, Any]) -> str:
    return str(row.get("清单版本号") or row.get("迁入清单版本") or UNVERSIONED)


def _active_factors(key: tuple[str, str, str]) -> list[dict[str, Any]]:
    field, source, source_unit = key
    return [
        row for row in store.rows("unit_normalize")
        if str(row.get("status")) == "已生效"
        and str(row.get("字段名称")) == field
        and str(row.get("适用来源")) == source
        and str(row.get("原始单位")) == source_unit
    ]


def _winning_factor(key: tuple[str, str, str]) -> dict[str, Any] | None:
    active = _active_factors(key)
    return max(active, key=lambda row: str(row.get("生效起日") or "")) if active else None


def _impacted_handoffs(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """找出会被该条目影响的在途转型交接。"""
    impacts: list[dict[str, Any]] = []
    for handoff in _get_state()["handoffs"]:
        if handoff.get("状态") != "在途":
            continue
        if (str(handoff.get("字段名称")) == str(entry.get("字段名称"))
                and str(handoff.get("原始单位")) == str(entry.get("原始单位"))
                and str(handoff.get("适用来源")) == str(entry.get("适用来源"))):
            impacts.append({
                "交接编号": handoff.get("交接编号"),
                "转型编号": handoff.get("转型编号"),
                "字段名称": handoff.get("字段名称"),
                "原始取值": handoff.get("原始取值"),
                "原始单位": handoff.get("原始单位"),
                "适用来源": handoff.get("适用来源"),
                "状态": "在途",
                "说明": (
                    f"转型 {handoff.get('转型编号')} 的 "
                    f"{handoff.get('原始取值')} {handoff.get('原始单位')} 仍按当前生效因子计算，"
                    f"导入新系数不会改写这条在途交接"
                ),
            })
    return impacts


def _classify(entry: dict[str, Any],
              siblings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """对单条清单条目做兼容预检，给出分类与说明。"""
    field = str(entry.get("字段名称") or "").strip()
    source_unit = str(entry.get("原始单位") or "").strip()
    target_unit = str(entry.get("目标单位") or "").strip()
    source = str(entry.get("适用来源") or "").strip()
    multiplier = _float(entry.get("换算系数"))

    view = {
        "字段名称": field,
        "原始单位": source_unit,
        "目标单位": target_unit,
        "适用来源": source,
        "换算系数": str(entry.get("换算系数") or "").strip(),
        "冲突说明": "",
        "冲突系数编号": "",
        "最近错误": "",
    }

    missing = [
        name for name, value in (
            ("字段名称", field), ("原始单位", source_unit),
            ("目标单位", target_unit), ("适用来源", source))
        if not value
    ]
    if missing:
        view["预检分类"] = ENTRY_FAILED
        view["最近错误"] = f"缺少必填项：{'、'.join(missing)}"
        return view
    if multiplier is None or multiplier <= 0:
        view["预检分类"] = ENTRY_FAILED
        view["最近错误"] = "换算系数必须是大于 0 的有限数值"
        return view

    key = (field, source, source_unit)
    for sibling in siblings or []:
        if (str(sibling.get("字段名称")) != field
                or str(sibling.get("适用来源")) != source
                or str(sibling.get("原始单位")) != source_unit):
            continue
        sibling_factor = _float(sibling.get("换算系数"))
        if sibling_factor is not None and abs(sibling_factor - multiplier) <= 1e-9:
            view["预检分类"] = "重复条目"
            view["最近错误"] = "同一清单内存在完全相同的条目，只登记一次"
            return view
        view["预检分类"] = "文件内冲突"
        view["冲突说明"] = (
            f"同一清单内 {field} {source_unit} 同时申报 "
            f"×{sibling.get('换算系数')} 与 ×{multiplier}，需补核验"
        )
        return view

    winner = _winning_factor(key)
    if winner is None:
        view["预检分类"] = "新增"
        return view
    winner_factor = _float(winner.get("换算系数"))
    if winner_factor is not None and abs(winner_factor - multiplier) <= 1e-9:
        view["预检分类"] = "已存在"
        view["冲突系数编号"] = winner.get("归一编号")
        return view
    view["预检分类"] = "冲突"
    view["冲突系数编号"] = winner.get("归一编号")
    if str(winner.get("目标单位")) != target_unit:
        view["冲突说明"] = (
            f"已生效因子 {winner.get('归一编号')} 为 {field} {source_unit}→"
            f"{winner.get('目标单位')} ×{winner.get('换算系数')}，新条目目标单位 "
            f"{target_unit} 与系数 ×{multiplier} 均不同，需补核验"
        )
    else:
        view["冲突说明"] = (
            f"字段 {field} 来源 {source} 原始单位 {source_unit} 已有生效因子 "
            f"{winner.get('归一编号')} ×{winner_factor}（版本 {_factor_version(winner)}），"
            f"新条目申报 ×{multiplier}，同一来源同一单位只允许一条生效因子，需补核验"
        )
    return view


# ----------------------------------------------------------------------
# 初始状态与重置
# ----------------------------------------------------------------------
def _initial_state() -> dict[str, Any]:
    source = "sftp-node-03"
    entries_seed = [
        {"字段名称": "weight", "原始单位": "kg", "目标单位": "g",
         "换算系数": "1000.0", "适用来源": source},
    ]
    fingerprint = _fingerprint("NM-v1", source, entries_seed)
    return {
        "manifests": [{
            "id": 1,
            "清单编号": "NM-001",
            "版本号": "NM-v1",
            "来源": source,
            "文件名": "normalize-factors-v1.json",
            "内容指纹": fingerprint,
            "状态": MANIFEST_ACTIVE,
            "条目数": 1,
            "已写入数": 1,
            "冲突数": 0,
            "待核验数": 0,
            "失败数": 0,
            "待导入数": 0,
            "创建时间": "2026-09-02T09:00:00",
            "生效时间": "2026-09-02T09:00:00",
            "生效日期": "2026-09-02",
            "回滚时间": "",
            "回执编号": "VR-001",
            "基线系数编号": "UN-006",
            "说明": "首份版本化清单，由无版本存量系数 UN-006 迁入旧系数 A=1000",
        }],
        "entries": [{
            "id": 1,
            "清单编号": "NM-001",
            "版本号": "NM-v1",
            "行号": 1,
            "字段名称": "weight",
            "原始单位": "kg",
            "目标单位": "g",
            "换算系数": "1000.0",
            "适用来源": source,
            "状态": ENTRY_ACTIVE,
            "预检分类": "新增",
            "影响在途转型": False,
            "影响说明": "",
            "冲突说明": "",
            "冲突系数编号": "UN-006",
            "换算系数编号": "UN-006",
            "清单版本号": "NM-v1",
            "写入时间": "2026-09-02T09:00:00",
            "最近错误": "",
        }],
        "receipts": [{
            "id": 1,
            "回执编号": "VR-001",
            "清单编号": "NM-001",
            "版本号": "NM-v1",
            "状态": MANIFEST_ACTIVE,
            "兼容校验": {"通过": True, "违规": [], "约定": list(COMPAT_CONVENTIONS)},
            "已写入": 1,
            "冲突": 0,
            "待核验": 0,
            "失败": 0,
            "待导入": 0,
            "可继续处理": False,
            "可回滚": True,
            "内容指纹": fingerprint,
            "条目分组": {
                "已生效": ["第1行 weight kg→g ×1000.0"],
                "待核验": [], "失败": [], "待导入": [], "已跳过": [], "已回滚": [],
            },
            "影响在途转型": [],
            "提示": "版本 NM-v1 已生效，旧系数 A=1000 可直接用于换算",
            "生成时间": "2026-09-02T09:00:00",
        }],
        "conversions": [],
        "handoffs": [{
            "id": 1,
            "交接编号": "CH-001",
            "转型编号": "VC-002",
            "字段名称": "weight",
            "原始取值": "3.0",
            "原始单位": "kg",
            "目标单位": "g",
            "适用来源": source,
            "命中日期": "2026-09-02",
            "状态": "在途",
            "消费版本号": "",
            "换算凭据编号": "",
            "创建时间": "2026-09-02T09:30:00",
            "说明": "取值转型已交付、等待按生效清单计算归一字段",
        }],
        "audits": [],
    }


def reset_state() -> None:
    """重建初始状态，供内存仓库重置与测试隔离使用。"""
    global _state
    _state = None


def _get_state() -> dict[str, Any]:
    global _state
    if _state is None:
        _state = _initial_state()
    return _state


def _append(collection: str, values: dict[str, Any]) -> dict[str, Any]:
    rows = _get_state()[collection]
    values = dict(values)
    values.setdefault("id", max((int(r.get("id", 0)) for r in rows), default=0) + 1)
    rows.append(values)
    return values


def _find_manifest(manifest_no: str) -> dict[str, Any] | None:
    for row in _get_state()["manifests"]:
        if str(row.get("清单编号")) == str(manifest_no):
            return row
    return None


def _find_by_fingerprint(fingerprint: str) -> dict[str, Any] | None:
    for row in _get_state()["manifests"]:
        if str(row.get("内容指纹")) == fingerprint and row.get("状态") != MANIFEST_ROLLED_BACK:
            return row
    return None


def _entries_of(manifest_no: str) -> list[dict[str, Any]]:
    return [
        row for row in _get_state()["entries"]
        if str(row.get("清单编号")) == str(manifest_no)
    ]


def _current_receipt(manifest_no: str) -> dict[str, Any] | None:
    rows = [
        row for row in _get_state()["receipts"]
        if str(row.get("清单编号")) == str(manifest_no)
    ]
    return rows[-1] if rows else None


def _entry_view(entry: dict[str, Any]) -> dict[str, Any]:
    state = entry.get("状态")
    return {
        **entry,
        "版本化": bool(entry.get("清单版本号")),
        "已生效": state == ENTRY_ACTIVE,
        "待补核验": state == ENTRY_CONFLICT,
        "失败": state == ENTRY_FAILED,
        "待导入": state == ENTRY_PENDING,
    }


# ----------------------------------------------------------------------
# 查询
# ----------------------------------------------------------------------
def list_manifests() -> list[dict[str, Any]]:
    return [
        {**row, "条目明细": [_entry_view(item) for item in _entries_of(row["清单编号"])]}
        for row in _get_state()["manifests"]
    ]


def get_manifest(manifest_no: str) -> dict[str, Any] | None:
    manifest = _find_manifest(manifest_no)
    if manifest is None:
        return None
    return {
        **manifest,
        "条目明细": [_entry_view(item) for item in _entries_of(manifest_no)],
        "回执": _current_receipt(manifest_no),
    }


def list_legacy() -> list[dict[str, Any]]:
    """存量系数：无清单版本的仍按原始系数计算原值。"""
    result = []
    for row in store.rows("unit_normalize"):
        version = str(row.get("清单版本号") or row.get("迁入清单版本") or "")
        result.append({
            **row,
            "清单版本号": version,
            "版本化": bool(version),
            "迁移状态": row.get("迁移状态") or ("已迁入" if version else "未迁移"),
            "可计算原值": row.get("status") == "已生效",
            "使用方式": (
                f"版本化清单 {version}" if version else "无版本存量：仍按原始系数计算"
            ),
        })
    return result


def compute_legacy(factor_no: str, raw_value: Any) -> dict[str, Any]:
    """无版本存量系数仍可计算原值；版本化系数返回其当前系数快照。"""
    target = next(
        (row for row in store.rows("unit_normalize")
         if str(row.get("归一编号")) == str(factor_no)), None)
    if target is None:
        return {"ok": False, "message": f"归一系数 {factor_no} 不存在"}
    raw = _float(raw_value)
    if raw is None:
        return {"ok": False, "message": f"原始取值 {raw_value!r} 不是数值"}
    multiplier = _float(target.get("换算系数"))
    if multiplier is None or target.get("status") != "已生效":
        return {
            "ok": False,
            "message": f"归一系数 {factor_no} 当前状态为 {target.get('status')}，无法计算",
        }
    version = _factor_version(target)
    computed = round(raw * multiplier, 6)
    return {
        "ok": True,
        "归一编号": factor_no,
        "原始取值": raw,
        "换算系数": multiplier,
        "原始单位": target.get("原始单位"),
        "目标单位": target.get("目标单位"),
        "换算值": computed,
        "消费版本号": "" if version == UNVERSIONED else version,
        "无版本存量": version == UNVERSIONED,
        "message": (
            f"无版本存量系数 {factor_no} 仍按原始系数 ×{multiplier} 计算原值 "
            f"{computed} {target.get('目标单位')}"
            if version == UNVERSIONED else
            f"系数 {factor_no}（版本 {version}）×{multiplier} 计算得 "
            f"{computed} {target.get('目标单位')}"
        ),
    }


# ----------------------------------------------------------------------
# 预检
# ----------------------------------------------------------------------
def preview_import(payload: dict[str, Any]) -> dict[str, Any]:
    """预检导入清单：列出影响在途转型的条目、冲突值与兼容约定违规。"""
    version = str(payload.get("版本号") or payload.get("清单版本号") or "").strip()
    source = str(payload.get("来源") or payload.get("适用来源") or "").strip()
    file_name = str(payload.get("文件名") or "manifest.json").strip()
    entries, parse_error = _parse_entries(payload)
    if parse_error:
        return {"ok": False, "message": parse_error}

    classified: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    for index, raw in enumerate(entries, start=1):
        item = dict(raw)
        item.setdefault("行号", index)
        item["适用来源"] = str(item.get("适用来源") or source).strip()
        view = _classify(item, siblings=accepted)
        view["行号"] = item.get("行号")
        view["影响在途转型"] = bool(_impacted_handoffs(item))
        view["影响说明"] = "; ".join(
            impact["说明"] for impact in _impacted_handoffs(item)
        ) if view["影响在途转型"] else ""
        if view["预检分类"] == ENTRY_FAILED:
            violations.append({
                "行号": view["行号"],
                "类型": "兼容约定违规",
                "说明": view["最近错误"],
            })
        elif view["预检分类"] == "文件内冲突":
            violations.append({
                "行号": view["行号"],
                "类型": "文件内条目冲突",
                "说明": view["冲突说明"],
            })
        else:
            accepted.append(view)
        classified.append(view)

    if not version:
        violations.append({
            "行号": "-",
            "类型": "缺少版本号",
            "说明": "版本化清单必须声明版本号，无版本系数仍留在存量区",
        })
    if not source:
        violations.append({
            "行号": "-",
            "类型": "缺少来源",
            "说明": "清单必须声明来源，同一来源才可判定同键唯一",
        })

    fingerprint = _fingerprint(version, source, entries)
    existing = _find_by_fingerprint(fingerprint)
    impact_items: list[dict[str, Any]] = []
    seen_handoff: set[str] = set()
    for item in classified:
        if not item["影响在途转型"]:
            continue
        for impact in _impacted_handoffs(item):
            if impact["交接编号"] in seen_handoff:
                continue
            seen_handoff.add(impact["交接编号"])
            impact_items.append({**impact, "行号": item["行号"]})
    conflict_values = [
        {
            "行号": item["行号"],
            "字段名称": item["字段名称"],
            "适用来源": item["适用来源"],
            "原始单位": item["原始单位"],
            "目标单位": item["目标单位"],
            "申报系数": item["换算系数"],
            "分类": item["预检分类"],
            "冲突系数编号": item.get("冲突系数编号"),
            "说明": item.get("冲突说明"),
        }
        for item in classified
        if item["预检分类"] in ("冲突", "文件内冲突")
    ]
    return {
        "ok": True,
        "清单版本号": version,
        "来源": source,
        "文件名": file_name,
        "内容指纹": fingerprint,
        "条目数": len(classified),
        "条目": classified,
        "影响在途转型": impact_items,
        "冲突值": conflict_values,
        "兼容校验": {
            "通过": not violations,
            "违规": violations,
            "约定": list(COMPAT_CONVENTIONS),
        },
        "重复清单": existing is not None,
        "已存在清单": (
            {"清单编号": existing.get("清单编号"), "版本号": existing.get("版本号"),
             "状态": existing.get("状态"), "回执编号": existing.get("回执编号")}
            if existing else None
        ),
        "可导入": bool(version) and bool(source) and not violations,
    }


# ----------------------------------------------------------------------
# 写入与回执
# ----------------------------------------------------------------------
def _write_entry(manifest: dict[str, Any], entry: dict[str, Any],
                 at: str) -> None:
    """写入单条清单条目：新增即生效，冲突只登记待补核验，失败留原因。"""
    if entry.get("状态") not in (ENTRY_PENDING, ENTRY_FAILED):
        return
    manifest_no = str(manifest.get("清单编号"))
    siblings = [
        row for row in _entries_of(manifest_no)
        if row is not entry and row.get("状态") == ENTRY_ACTIVE
    ]
    classified = _classify(entry, siblings=siblings)
    category = classified["预检分类"]
    entry["预检分类"] = category
    entry["冲突说明"] = classified.get("冲突说明", "")
    entry["冲突系数编号"] = classified.get("冲突系数编号", "")
    entry["最近写入"] = at

    if category == ENTRY_FAILED:
        entry["状态"] = ENTRY_FAILED
        entry["最近错误"] = classified["最近错误"]
        return

    if category == "重复条目":
        entry["状态"] = ENTRY_SKIPPED
        entry["最近错误"] = classified["最近错误"]
        return

    if category == "新增":
        new_row = {
            "id": max(
                (int(row.get("id", 0)) for row in store.rows("unit_normalize")),
                default=0) + 1,
            "status": "已生效",
            "pending": False,
            "abnormal": False,
            "归一编号": _next_factor_no("UN-M"),
            "字段名称": entry.get("字段名称"),
            "原始单位": entry.get("原始单位"),
            "目标单位": entry.get("目标单位"),
            "换算系数": str(entry.get("换算系数")),
            "适用来源": entry.get("适用来源"),
            "生效起日": str(manifest.get("生效日期") or at[:10]),
            "最近命中": "",
            "清单编号": manifest_no,
            "清单版本号": manifest.get("版本号"),
            "迁移来源": "版本化清单导入",
        }
        store.rows("unit_normalize").append(new_row)
        entry["状态"] = ENTRY_ACTIVE
        entry["换算系数编号"] = new_row["归一编号"]
        entry["清单版本号"] = manifest.get("版本号")
        entry["写入时间"] = at
        entry["最近错误"] = ""
        return

    if category == "已存在":
        entry["状态"] = ENTRY_ACTIVE
        entry["换算系数编号"] = classified.get("冲突系数编号", "")
        entry["清单版本号"] = manifest.get("版本号")
        entry["写入时间"] = at
        entry["最近错误"] = ""
        return

    # 冲突 / 文件内冲突：不自动生效，保留原生效因子
    entry["状态"] = ENTRY_CONFLICT
    entry["换算系数编号"] = ""
    entry["写入时间"] = at
    entry["最近错误"] = ""


def _entry_groups(manifest_no: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {
        "已生效": [], "待核验": [], "失败": [], "待导入": [], "已跳过": [], "已回滚": [],
    }
    mapping = {
        ENTRY_ACTIVE: "已生效", ENTRY_CONFLICT: "待核验",
        ENTRY_FAILED: "失败", ENTRY_PENDING: "待导入",
        ENTRY_SKIPPED: "已跳过", ENTRY_ROLLED_BACK: "已回滚",
    }
    for entry in _entries_of(manifest_no):
        groups[mapping.get(str(entry.get("状态")), "待导入")].append(_label(entry))
    return groups


def _refresh_manifest(manifest: dict[str, Any]) -> None:
    if not manifest:
        return
    entries = _entries_of(str(manifest.get("清单编号")))
    manifest["条目数"] = len(entries)
    manifest["已写入数"] = sum(
        1 for item in entries if item.get("状态") in (ENTRY_ACTIVE, ENTRY_SKIPPED))
    manifest["冲突数"] = sum(1 for item in entries if item.get("状态") == ENTRY_CONFLICT)
    manifest["待核验数"] = manifest["冲突数"]
    manifest["失败数"] = sum(1 for item in entries if item.get("状态") == ENTRY_FAILED)
    manifest["待导入数"] = sum(1 for item in entries if item.get("状态") == ENTRY_PENDING)


def _settle_manifest(manifest: dict[str, Any]) -> None:
    if not manifest:
        return
    _refresh_manifest(manifest)
    if manifest.get("状态") == MANIFEST_ROLLED_BACK:
        return
    if manifest["待导入数"]:
        manifest["状态"] = MANIFEST_IMPORTING if manifest.get("状态") == MANIFEST_IMPORTING else MANIFEST_PAUSED
    elif manifest["冲突数"]:
        manifest["状态"] = MANIFEST_CONFLICT
    elif manifest["失败数"]:
        manifest["状态"] = MANIFEST_PARTIAL
    else:
        manifest["状态"] = MANIFEST_ACTIVE
        manifest["生效时间"] = manifest.get("生效时间") or manifest.get("创建时间", "")
        manifest["生效日期"] = manifest.get("生效日期") or manifest.get("创建时间", "")[:10]


def _compat_report(manifest_no: str) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    for entry in _entries_of(manifest_no):
        if entry.get("状态") == ENTRY_FAILED:
            violations.append({
                "行号": entry.get("行号"),
                "类型": "兼容约定违规",
                "说明": entry.get("最近错误") or "写入失败",
            })
        elif entry.get("状态") == ENTRY_CONFLICT:
            violations.append({
                "行号": entry.get("行号"),
                "类型": "同键冲突",
                "说明": entry.get("冲突说明") or "同一来源同一单位已存在生效因子",
            })
    return {"通过": not violations, "违规": violations, "约定": list(COMPAT_CONVENTIONS)}


def _settle_message(manifest: dict[str, Any]) -> str:
    state = manifest.get("状态")
    if state == MANIFEST_ACTIVE:
        return f"清单 {manifest.get('清单编号')} 全部条目已生效"
    if state == MANIFEST_CONFLICT:
        return (
            f"清单 {manifest.get('清单编号')} 有 {manifest.get('冲突数')} 条冲突待补核验，"
            f"已生效条目不受影响"
        )
    if state == MANIFEST_PAUSED:
        return (
            f"清单 {manifest.get('清单编号')} 已暂停，剩余 "
            f"{manifest.get('待导入数')} 条待导入，可用同一文件恢复"
        )
    return (
        f"清单 {manifest.get('清单编号')} 部分导入："
        f"{manifest.get('失败数')} 条失败、{manifest.get('冲突数')} 条待核验"
    )


def _new_receipt(manifest: dict[str, Any], *, message: str, at: str,
                 extra: dict[str, Any] | None = None) -> dict[str, Any]:
    _refresh_manifest(manifest)
    manifest_no = str(manifest.get("清单编号"))
    impacted: list[dict[str, Any]] = []
    seen_handoff: set[str] = set()
    for entry in _entries_of(manifest_no):
        for item in _impacted_handoffs(entry):
            if item["交接编号"] in seen_handoff:
                continue
            seen_handoff.add(item["交接编号"])
            impacted.append(item)
    receipt = {
        "回执编号": _next_no("VR", "回执编号", "receipts"),
        "清单编号": manifest_no,
        "版本号": manifest.get("版本号"),
        "状态": manifest.get("状态"),
        "兼容校验": _compat_report(manifest_no),
        "已写入": manifest.get("已写入数", 0),
        "冲突": manifest.get("冲突数", 0),
        "待核验": manifest.get("待核验数", 0),
        "失败": manifest.get("失败数", 0),
        "待导入": manifest.get("待导入数", 0),
        "可继续处理": bool(manifest.get("待导入数") or manifest.get("失败数")),
        "可回滚": manifest.get("状态") in (
            MANIFEST_ACTIVE, MANIFEST_CONFLICT, MANIFEST_PARTIAL),
        "内容指纹": manifest.get("内容指纹"),
        "条目分组": _entry_groups(manifest_no),
        "影响在途转型": impacted,
        "提示": message,
        "生成时间": at,
    }
    if extra:
        receipt.update(extra)
    _append("receipts", receipt)
    manifest["回执编号"] = receipt["回执编号"]
    return receipt


def _audit(manifest_no: str, action: str, detail: str, at: str) -> None:
    _append("audits", {
        "清单编号": manifest_no, "动作": action, "说明": detail, "时间": at,
    })


# ----------------------------------------------------------------------
# 导入 / 暂停 / 恢复 / 回滚
# ----------------------------------------------------------------------
def import_manifest(payload: dict[str, Any], *,
                    pause_after: int | None = None,
                    fail_rows: list[int] | None = None,
                    at: str = "2026-09-03T10:00:00") -> dict[str, Any]:
    """登记并导入清单：同一指纹只登记一次；可只写一部分后暂停。

    fail_rows 用于模拟写到一半断掉：这些行号登记为失败，恢复时只补失败条目。
    """
    preview = preview_import(payload)
    if not preview["ok"]:
        return {"ok": False, "message": preview["message"]}
    fail_rows = set(fail_rows or [])

    fingerprint = preview["内容指纹"]
    existing = _find_by_fingerprint(fingerprint)
    if existing is not None:
        manifest_no = str(existing.get("清单编号"))
        receipt = _current_receipt(manifest_no)
        resumable = existing.get("状态") in (
            MANIFEST_PAUSED, MANIFEST_PARTIAL, MANIFEST_CONFLICT, MANIFEST_DRAFT,
            MANIFEST_IMPORTING)
        suffix = (
            f"当前状态 {existing.get('状态')}，可继续处理未完成条目"
            if resumable else "重复导入只登记一次，本次不产生新版本"
        )
        return {
            "ok": True,
            "重复登记": True,
            "可继续处理": resumable,
            "清单编号": manifest_no,
            "版本号": existing.get("版本号"),
            "清单": get_manifest(manifest_no),
            "回执": receipt,
            "message": (
                f"同一份清单（指纹 {fingerprint}）已登记为 {manifest_no}，"
                f"不重复建单；{suffix}"
            ),
        }

    if not preview["可导入"]:
        return {
            "ok": False,
            "message": "清单兼容约定校验未通过，未登记版本",
            "兼容校验": preview["兼容校验"],
            "冲突值": preview["冲突值"],
            "影响在途转型": preview["影响在途转型"],
        }

    manifest_no = _next_no("NM", "清单编号", "manifests")
    manifest = _append("manifests", {
        "清单编号": manifest_no,
        "版本号": preview["清单版本号"],
        "来源": preview["来源"],
        "文件名": preview["文件名"],
        "内容指纹": fingerprint,
        "状态": MANIFEST_IMPORTING,
        "条目数": preview["条目数"],
        "已写入数": 0,
        "冲突数": 0,
        "待核验数": 0,
        "失败数": 0,
        "待导入数": preview["条目数"],
        "创建时间": at,
        "生效时间": "",
        "生效日期": at[:10],
        "回滚时间": "",
        "回执编号": "",
        "基线系数编号": "",
        "说明": "由归一系数清单导入登记",
    })
    for index, item in enumerate(preview["条目"], start=1):
        _append("entries", {
            "清单编号": manifest_no,
            "版本号": manifest["版本号"],
            "行号": item.get("行号") or index,
            "字段名称": item.get("字段名称"),
            "原始单位": item.get("原始单位"),
            "目标单位": item.get("目标单位"),
            "换算系数": item.get("换算系数"),
            "适用来源": item.get("适用来源"),
            "状态": ENTRY_PENDING,
            "预检分类": item.get("预检分类"),
            "影响在途转型": item.get("影响在途转型"),
            "影响说明": item.get("影响说明"),
            "冲突说明": item.get("冲突说明"),
            "冲突系数编号": item.get("冲突系数编号"),
            "换算系数编号": "",
            "清单版本号": "",
            "写入时间": "",
            "最近错误": "",
        })

    processed = 0
    for entry in _entries_of(manifest_no):
        if pause_after is not None and processed >= pause_after:
            break
        if entry.get("行号") in fail_rows:
            entry["状态"] = ENTRY_FAILED
            entry["最近错误"] = "写入中断：该行尚未写完，恢复时只补失败条目"
            entry["最近写入"] = at
            processed += 1
            continue
        _write_entry(manifest, entry, at)
        processed += 1

    if pause_after is not None and processed < preview["条目数"]:
        manifest["状态"] = MANIFEST_PAUSED
        _refresh_manifest(manifest)
        _audit(manifest_no, "暂停导入",
               f"已写入 {processed}/{preview['条目数']} 条后暂停", at)
        receipt = _new_receipt(manifest, message=(
            f"清单 {manifest_no} 已暂停：已处理 {processed}/{preview['条目数']} 条，"
            f"剩余条目保持待导入，可用同一文件恢复"), at=at)
    else:
        _settle_manifest(manifest)
        receipt = _new_receipt(manifest, message=_settle_message(manifest), at=at)
    _audit(manifest_no, "导入清单",
           f"{manifest_no} {manifest.get('版本号')} 状态 {manifest.get('状态')}", at)
    return {
        "ok": True,
        "重复登记": False,
        "可继续处理": receipt["可继续处理"],
        "清单编号": manifest_no,
        "版本号": manifest["版本号"],
        "清单": get_manifest(manifest_no),
        "回执": receipt,
        "message": receipt["提示"],
    }


def pause_import(manifest_no: str, at: str = "2026-09-03T10:05:00") -> dict[str, Any]:
    manifest = _find_manifest(manifest_no)
    if manifest is None:
        return {"ok": False, "message": f"清单 {manifest_no} 不存在"}
    if manifest.get("状态") not in (MANIFEST_IMPORTING, MANIFEST_PAUSED):
        return {"ok": False,
                "message": f"清单 {manifest_no} 当前状态 {manifest.get('状态')} 不可暂停"}
    manifest["状态"] = MANIFEST_PAUSED
    _refresh_manifest(manifest)
    _audit(manifest_no, "暂停导入", "人工暂停导入", at)
    receipt = _new_receipt(manifest, message=f"清单 {manifest_no} 已暂停", at=at)
    return {"ok": True, "清单编号": manifest_no, "回执": receipt,
            "清单": get_manifest(manifest_no), "message": receipt["提示"]}


def resume_import(manifest_no: str, payload: dict[str, Any] | None = None,
                  *, only_failed: bool = False,
                  at: str = "2026-09-03T10:10:00") -> dict[str, Any]:
    """用同一文件恢复：校验指纹未变，只处理未完成/失败条目。"""
    manifest = _find_manifest(manifest_no)
    if manifest is None:
        return {"ok": False, "message": f"清单 {manifest_no} 不存在"}
    if manifest.get("状态") == MANIFEST_ROLLED_BACK:
        return {"ok": False, "message": f"清单 {manifest_no} 已回滚，不能恢复"}
    if payload is not None:
        preview = preview_import(payload)
        if not preview["ok"]:
            return {"ok": False, "message": preview["message"]}
        if preview["内容指纹"] != manifest.get("内容指纹"):
            return {
                "ok": False,
                "message": (
                    f"恢复文件指纹与清单 {manifest_no} 的登记指纹不一致，"
                    f"不允许用不同文件续传"
                ),
            }
    states = (ENTRY_FAILED,) if only_failed else (ENTRY_FAILED, ENTRY_PENDING)
    pending = [entry for entry in _entries_of(manifest_no) if entry.get("状态") in states]
    if not pending:
        return {
            "ok": True,
            "清单编号": manifest_no,
            "回执": _current_receipt(manifest_no),
            "清单": get_manifest(manifest_no),
            "message": "没有需要继续处理的条目",
        }
    manifest["状态"] = MANIFEST_IMPORTING
    for entry in pending:
        entry["最近错误"] = ""
        _write_entry(manifest, entry, at)
    _settle_manifest(manifest)
    settled_handoffs = _settle_pending_handoffs(at)
    receipt = _new_receipt(manifest, message=(
        f"清单 {manifest_no} 已用同一文件恢复，处理 {len(pending)} 条未完成/失败条目；"
        + _settle_message(manifest)), at=at)
    _audit(manifest_no, "恢复导入",
           f"处理 {len(pending)} 条，状态 {manifest.get('状态')}", at)
    return {"ok": True, "清单编号": manifest_no, "回执": receipt,
            "结算交接": settled_handoffs,
            "清单": get_manifest(manifest_no), "message": receipt["提示"]}


def _settle_pending_handoffs(at: str) -> list[dict[str, Any]]:
    """冲突解决或回滚后，把同键不再有冲突的在途交接补算并落凭据。"""
    settled: list[dict[str, Any]] = []
    for handoff in _get_state()["handoffs"]:
        if handoff.get("状态") != "在途":
            continue
        field = str(handoff.get("字段名称"))
        source = str(handoff.get("适用来源"))
        source_unit = str(handoff.get("原始单位"))
        if _pending_conflicts(field, source, source_unit):
            continue
        factor = _active_for(field, source, source_unit)
        if factor is None:
            continue
        result = _settle_handoff_record(handoff, factor, at)
        if result["ok"]:
            _audit(str(factor.get("清单编号") or ""), "结算在途交接",
                   handoff["说明"], at)
            settled.append({
                "交接编号": handoff.get("交接编号"),
                "消费版本号": result["receipt"]["消费版本号"],
                "换算凭据编号": result["receipt"]["换算凭据编号"],
                "归一取值": result["normalized"],
                "目标单位": result["receipt"]["目标单位"],
                "说明": handoff["说明"],
            })
    return settled


def retry_failed(manifest_no: str, at: str = "2026-09-03T10:15:00") -> dict[str, Any]:
    """只处理失败条目的操作：不重写已生效、待核验和已跳过条目。"""
    return resume_import(manifest_no, None, only_failed=True, at=at)


def rollback_manifest(manifest_no: str, at: str = "2026-09-03T10:20:00",
                      reason: str = "人工回滚") -> dict[str, Any]:
    """回滚清单：停用本清单写入的因子，历史换算凭据保持原样。"""
    manifest = _find_manifest(manifest_no)
    if manifest is None:
        return {"ok": False, "message": f"清单 {manifest_no} 不存在"}
    if manifest.get("状态") == MANIFEST_ROLLED_BACK:
        return {"ok": False, "message": f"清单 {manifest_no} 已回滚"}
    disabled: list[str] = []
    for row in store.rows("unit_normalize"):
        if str(row.get("清单编号") or "") != manifest_no:
            continue
        if row.get("status") == "已停用":
            continue
        row["status"] = "已停用"
        row["pending"] = False
        row["abnormal"] = False
        row["停用原因"] = f"清单 {manifest_no} 回滚：{reason}"
        row["停用时间"] = at
        disabled.append(str(row.get("归一编号")))
    manifest["状态"] = MANIFEST_ROLLED_BACK
    manifest["回滚时间"] = at
    for entry in _entries_of(manifest_no):
        if entry.get("状态") == ENTRY_ACTIVE and str(entry.get("换算系数编号")) in disabled:
            entry["状态"] = ENTRY_ROLLED_BACK
        elif entry.get("状态") == ENTRY_CONFLICT:
            # 回滚后这条申报不再待裁决，不能再阻塞同键在途交接
            entry["状态"] = ENTRY_ROLLED_BACK
            entry["最近错误"] = f"清单 {manifest_no} 回滚，不再核验"
        elif entry.get("状态") == ENTRY_PENDING:
            entry["状态"] = ENTRY_ROLLED_BACK
            entry["最近错误"] = f"清单 {manifest_no} 回滚，未写入"
    _refresh_manifest(manifest)
    manifest["待导入数"] = 0
    settled_handoffs = _settle_pending_handoffs(at)
    conversions = [
        row for row in _get_state()["conversions"]
        if str(row.get("消费版本号")) == str(manifest.get("版本号"))
    ]
    _audit(manifest_no, "回滚清单",
           f"停用因子 {disabled}，历史换算凭据 {len(conversions)} 份保持不变", at)
    receipt = _new_receipt(
        manifest,
        message=(
            f"清单 {manifest_no} 已回滚，停用 {len(disabled)} 条版本化因子；"
            f"{len(conversions)} 份历史换算凭据仍可重看"
        ),
        at=at, extra={"回滚": True})
    return {"ok": True, "清单编号": manifest_no, "回执": receipt,
            "清单": get_manifest(manifest_no), "停用因子": disabled,
            "结算交接": settled_handoffs,
            "历史换算凭据": len(conversions), "message": receipt["提示"]}

# ----------------------------------------------------------------------
# 冲突核验
# ----------------------------------------------------------------------
def _conflict_groups() -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in _get_state()["entries"]:
        if entry.get("状态") != ENTRY_CONFLICT:
            continue
        key = (str(entry.get("字段名称")), str(entry.get("适用来源")),
               str(entry.get("原始单位")))
        group = groups.setdefault(key, {
            "键": "|".join(key),
            "字段名称": key[0],
            "适用来源": key[1],
            "原始单位": key[2],
            "目标单位": entry.get("目标单位"),
            "已生效": None,
            "待补核验": [],
            "需补核验": True,
            "说明": "",
        })
        winner = _winning_factor(key)
        if winner is not None and group["已生效"] is None:
            group["已生效"] = {
                "归一编号": winner.get("归一编号"),
                "换算系数": winner.get("换算系数"),
                "目标单位": winner.get("目标单位"),
                "清单编号": winner.get("清单编号", ""),
                "清单版本号": _factor_version(winner),
                "状态": "已生效",
            }
        group["待补核验"].append({
            "条目id": entry.get("id"),
            "清单编号": entry.get("清单编号"),
            "清单版本号": entry.get("版本号"),
            "行号": entry.get("行号"),
            "换算系数": entry.get("换算系数"),
            "目标单位": entry.get("目标单位"),
            "状态": "待补核验",
            "冲突说明": entry.get("冲突说明"),
        })
    for group in groups.values():
        pending_text = "、".join(
            f"×{item['换算系数']}（条目 {item['条目id']}）" for item in group["待补核验"])
        if group["已生效"]:
            active = group["已生效"]
            group["说明"] = (
                f"字段 {group['字段名称']} 来源 {group['适用来源']} 原始单位 "
                f"{group['原始单位']} 已生效因子 {active['归一编号']} "
                f"×{active['换算系数']}（版本 {active['清单版本号']}）；"
                f"待补核验申报 {pending_text}，不会自动覆盖"
            )
        else:
            group["说明"] = (
                f"字段 {group['字段名称']} 来源 {group['适用来源']} 原始单位 "
                f"{group['原始单位']} 存在互相冲突的申报 {pending_text}，需补核验"
            )
    return sorted(groups.values(), key=lambda item: item["键"])


def conflicts() -> dict[str, Any]:
    groups = _conflict_groups()
    return {
        "分组": groups,
        "待核验条目": [
            _entry_view(entry) for entry in _get_state()["entries"]
            if entry.get("状态") == ENTRY_CONFLICT
        ],
        "已生效条目": [
            {
                "归一编号": row.get("归一编号"),
                "字段名称": row.get("字段名称"),
                "原始单位": row.get("原始单位"),
                "目标单位": row.get("目标单位"),
                "换算系数": row.get("换算系数"),
                "适用来源": row.get("适用来源"),
                "清单版本号": _factor_version(row),
                "清单编号": row.get("清单编号", ""),
                "版本化": bool(row.get("清单编号") or row.get("清单版本号")
                               or row.get("迁入清单版本")),
                "状态": row.get("status"),
            }
            for row in store.rows("unit_normalize")
            if row.get("status") == "已生效"
        ],
        "冲突键": [group["键"] for group in groups],
    }


def resolve_conflict(entry_id: int, decision: str,
                     at: str = "2026-09-03T10:30:00") -> dict[str, Any]:
    """裁决冲突：保留新申报或保留旧生效因子，二选一。"""
    entry = next(
        (item for item in _get_state()["entries"]
         if int(item.get("id", 0)) == int(entry_id)), None)
    if entry is None:
        return {"ok": False, "message": f"清单条目 {entry_id} 不存在"}
    if entry.get("状态") != ENTRY_CONFLICT:
        return {"ok": False, "message": f"清单条目 {entry_id} 不处于待补核验状态"}
    key = (str(entry.get("字段名称")), str(entry.get("适用来源")),
           str(entry.get("原始单位")))
    winner = _winning_factor(key)
    manifest_no = str(entry.get("清单编号"))

    if decision in ("保留旧", "keep_old"):
        entry["状态"] = ENTRY_SKIPPED
        entry["最近错误"] = (
            f"人工裁决保留 {winner.get('归一编号') if winner else '原生效因子'}")
        manifest = _find_manifest(manifest_no)
        settled: list[dict[str, Any]] = []
        if manifest is not None:
            _settle_manifest(manifest)
            settled = _settle_pending_handoffs(at)
            _new_receipt(manifest, message=(
                f"冲突已裁决：保留 {winner.get('归一编号') if winner else '原生效因子'}，"
                f"新申报 ×{entry.get('换算系数')} 不生效"), at=at)
            _audit(manifest_no, "冲突核验",
                   f"保留旧因子 {winner.get('归一编号') if winner else ''}", at)
        return {
            "ok": True,
            "条目": _entry_view(entry),
            "清单": get_manifest(manifest_no),
            "结算交接": settled,
            "message": (
                f"已保留 {winner.get('归一编号') if winner else '原生效因子'} 生效，"
                f"新申报 ×{entry.get('换算系数')} 不生效"
            ),
        }

    if decision not in ("保留新", "keep_new"):
        return {"ok": False, "message": "裁决只能选择「保留新」或「保留旧」"}

    rows = store.rows("unit_normalize")
    new_row = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "归一编号": _next_factor_no("UN-C"),
        "字段名称": entry.get("字段名称"),
        "原始单位": entry.get("原始单位"),
        "目标单位": entry.get("目标单位"),
        "换算系数": str(entry.get("换算系数")),
        "适用来源": entry.get("适用来源"),
        "生效起日": str(entry.get("命中日期") or at[:10]),
        "最近命中": "",
        "清单编号": manifest_no,
        "清单版本号": entry.get("版本号"),
        "迁移来源": "冲突裁决导入",
    }
    rows.append(new_row)
    disabled: list[str] = []
    if winner is not None:
        winner["status"] = "已停用"
        winner["pending"] = False
        winner["abnormal"] = False
        winner["停用原因"] = (
            f"清单 {manifest_no} 冲突裁决，由 {new_row['归一编号']} 接替")
        winner["停用时间"] = at
        disabled.append(str(winner.get("归一编号")))
    entry["状态"] = ENTRY_ACTIVE
    entry["换算系数编号"] = new_row["归一编号"]
    entry["清单版本号"] = entry.get("版本号")
    entry["写入时间"] = at
    entry["最近错误"] = ""
    manifest = _find_manifest(manifest_no)
    settled: list[dict[str, Any]] = []
    if manifest is not None:
        _settle_manifest(manifest)
        settled = _settle_pending_handoffs(at)
        _new_receipt(manifest, message=(
            f"冲突已裁决：{entry.get('字段名称')} {entry.get('原始单位')} 采用 "
            f"×{entry.get('换算系数')}（{new_row['归一编号']}）"), at=at)
        _audit(manifest_no, "冲突核验",
               f"保留新申报 {new_row['归一编号']}，停用 {disabled}", at)
    return {
        "ok": True,
        "条目": _entry_view(entry),
        "新因子": new_row,
        "停用因子": disabled,
        "清单": get_manifest(manifest_no),
        "结算交接": settled,
        "message": (
            f"冲突已裁决：保留新申报 ×{entry.get('换算系数')}，停用旧因子 {disabled}"
        ),
    }


# ----------------------------------------------------------------------
# 换算凭据
# ----------------------------------------------------------------------
def _active_for(field: str, source: str, source_unit: str) -> dict[str, Any] | None:
    candidates = [
        row for row in store.rows("unit_normalize")
        if row.get("status") == "已生效"
        and str(row.get("字段名称")) == field
        and str(row.get("原始单位")) == source_unit
        and str(row.get("适用来源")) in (source, "*")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: str(row.get("生效起日") or ""))


def _pending_conflicts(field: str, source: str,
                       source_unit: str) -> list[dict[str, Any]]:
    return [
        entry for entry in _get_state()["entries"]
        if entry.get("状态") == ENTRY_CONFLICT
        and str(entry.get("字段名称")) == field
        and str(entry.get("适用来源")) == source
        and str(entry.get("原始单位")) == source_unit
    ]


def _settle_handoff_record(handoff: dict[str, Any], factor: dict[str, Any],
                           at: str) -> dict[str, Any]:
    multiplier = _float(factor.get("换算系数"))
    raw = _float(handoff.get("原始取值")) or 0.0
    if multiplier is None:
        return {"ok": False, "message": "生效因子换算系数不可用"}
    version = _factor_version(factor)
    normalized = round(raw * multiplier, 6)
    receipt = _append("conversions", {
        "换算凭据编号": _next_no("CV", "换算凭据编号", "conversions"),
        "转型编号": handoff.get("转型编号"),
        "交接编号": handoff.get("交接编号"),
        "字段名称": handoff.get("字段名称"),
        "原始取值": str(raw),
        "原始单位": handoff.get("原始单位"),
        "目标单位": factor.get("目标单位"),
        "换算系数": str(multiplier),
        "换算值": str(normalized),
        "消费版本号": "" if version == UNVERSIONED else version,
        "消费系数编号": factor.get("归一编号"),
        "清单编号": factor.get("清单编号", ""),
        "命中日期": handoff.get("命中日期"),
        "无版本存量": version == UNVERSIONED,
        "生成时间": at,
        "因子快照": {
            "归一编号": factor.get("归一编号"),
            "字段名称": factor.get("字段名称"),
            "原始单位": factor.get("原始单位"),
            "目标单位": factor.get("目标单位"),
            "换算系数": factor.get("换算系数"),
            "适用来源": factor.get("适用来源"),
            "清单版本号": _factor_version(factor),
            "清单编号": factor.get("清单编号", ""),
        },
    })
    handoff["状态"] = "已换算"
    handoff["目标单位"] = factor.get("目标单位")
    handoff["消费版本号"] = receipt["消费版本号"]
    handoff["换算凭据编号"] = receipt["换算凭据编号"]
    handoff["说明"] = (
        f"按 {receipt['消费版本号'] or UNVERSIONED} 的 {factor.get('归一编号')} "
        f"×{multiplier} 归一为 {normalized} {factor.get('目标单位')}"
    )
    return {"ok": True, "receipt": receipt, "normalized": normalized,
            "multiplier": multiplier, "factor": factor, "version": version}


def submit_handoff(payload: dict[str, Any],
                   at: str = "2026-09-03T11:00:00") -> dict[str, Any]:
    """取值转型交付数值：按当前生效清单计算归一并落换算凭据。

    同键存在待补核验条目时，换算保持在途，不抢先用可能被替换的系数计算。
    """
    cast_no = str(payload.get("转型编号") or "").strip()
    field = str(payload.get("字段名称") or "").strip()
    source = str(payload.get("适用来源") or "").strip()
    source_unit = str(payload.get("原始单位") or "").strip()
    raw_value = _float(payload.get("原始取值"))
    hit_date = str(payload.get("命中日期") or at[:10])
    if not cast_no or not field or not source or not source_unit:
        return {"ok": False, "message": "转型编号、字段名称、适用来源、原始单位不能为空"}
    if raw_value is None:
        return {"ok": False, "message": f"原始取值 {payload.get('原始取值')!r} 不是数值"}
    if not any(row.get("转型编号") == cast_no for row in store.rows("value_cast")):
        return {"ok": False, "message": f"转型规则 {cast_no} 不存在"}

    handoff = _append("handoffs", {
        "交接编号": _next_no("CH", "交接编号", "handoffs"),
        "转型编号": cast_no,
        "字段名称": field,
        "原始取值": str(raw_value),
        "原始单位": source_unit,
        "目标单位": "",
        "适用来源": source,
        "命中日期": hit_date,
        "状态": "在途",
        "消费版本号": "",
        "换算凭据编号": "",
        "创建时间": at,
        "说明": "取值转型已交付、等待按生效清单计算归一字段",
    })

    pending = _pending_conflicts(field, source, source_unit)
    if pending:
        handoff["说明"] = (
            f"字段 {field} 来源 {source} {source_unit} 有 "
            f"{len(pending)} 条冲突待补核验（"
            f"{', '.join('×' + str(item.get('换算系数')) for item in pending)}），"
            f"换算保持在途，待核验后再结算"
        )
        _audit(str(pending[0].get("清单编号")), "换算待核验", handoff["说明"], at)
        return {
            "ok": False,
            "挂起": True,
            "交接": handoff,
            "待核验条目": [
                {"条目id": item.get("id"), "换算系数": item.get("换算系数"),
                 "清单编号": item.get("清单编号")} for item in pending
            ],
            "message": handoff["说明"],
        }

    factor = _active_for(field, source, source_unit)
    if factor is None:
        handoff["状态"] = "未命中"
        handoff["说明"] = (
            f"字段 {field} 来源 {source} {source_unit} 没有生效因子，保留原值")
        _audit("", "换算拒绝", handoff["说明"], at)
        return {"ok": False, "message": handoff["说明"], "交接": handoff}

    settled = _settle_handoff_record(handoff, factor, at)
    if not settled["ok"]:
        handoff["状态"] = "未命中"
        handoff["说明"] = "生效因子换算系数不可用，保留原值"
        return {"ok": False, "message": handoff["说明"], "交接": handoff}

    receipt = settled["receipt"]
    _audit(str(receipt["清单编号"]), "换算", handoff["说明"], at)
    return {
        "ok": True,
        "交接": handoff,
        "换算凭据": receipt,
        "归一取值": settled["normalized"],
        "目标单位": factor.get("目标单位"),
        "消费版本号": receipt["消费版本号"],
        "message": (
            f"转型 {cast_no} 的 {raw_value} {source_unit} 按版本 "
            f"{receipt['消费版本号'] or UNVERSIONED} 换算为 {settled['normalized']} "
            f"{factor.get('目标单位')}（凭据 {receipt['换算凭据编号']}）"
        ),
    }


def settle_handoff(handoff_no: str,
                   at: str = "2026-09-03T11:30:00") -> dict[str, Any]:
    """结算在途交接：冲突裁决或回滚后，用当前生效因子补算并落凭据。"""
    handoff = next(
        (item for item in _get_state()["handoffs"]
         if str(item.get("交接编号")) == str(handoff_no)), None)
    if handoff is None:
        return {"ok": False, "message": f"交接 {handoff_no} 不存在"}
    if handoff.get("状态") != "在途":
        return {"ok": False,
                "message": f"交接 {handoff_no} 当前状态 {handoff.get('状态')}，无需结算"}
    field = str(handoff.get("字段名称"))
    source = str(handoff.get("适用来源"))
    source_unit = str(handoff.get("原始单位"))
    pending = _pending_conflicts(field, source, source_unit)
    if pending:
        return {
            "ok": False,
            "挂起": True,
            "交接": handoff,
            "message": (
                f"字段 {field} {source_unit} 仍有 {len(pending)} 条冲突待补核验，"
                f"交接继续保持在途"
            ),
        }
    factor = _active_for(field, source, source_unit)
    if factor is None:
        handoff["状态"] = "未命中"
        handoff["说明"] = f"字段 {field} 没有生效因子，保留原值"
        return {"ok": False, "message": handoff["说明"], "交接": handoff}
    settled = _settle_handoff_record(handoff, factor, at)
    _audit(str(factor.get("清单编号") or ""), "结算在途交接", handoff["说明"], at)
    return {
        "ok": True,
        "交接": handoff,
        "换算凭据": settled["receipt"],
        "归一取值": settled["normalized"],
        "消费版本号": settled["receipt"]["消费版本号"],
        "message": (
            f"交接 {handoff_no} 已结算：" + handoff["说明"]
        ),
    }


def list_handoffs() -> list[dict[str, Any]]:
    return list(_get_state()["handoffs"])


def list_conversions(filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    filters = {k: v for k, v in (filters or {}).items() if str(v).strip()}
    rows = list(_get_state()["conversions"])
    for field, expected in filters.items():
        if field in ("转型编号", "消费版本号", "换算凭据编号", "清单编号",
                     "字段名称", "消费系数编号", "交接编号"):
            rows = [row for row in rows if str(row.get(field)) == str(expected)]
    return rows


def conversion_credential(cast_no: str,
                          receipt_no: str | None = None) -> dict[str, Any] | None:
    """从转型历史打开旧系数：返回当次实际消费的因子快照。"""
    rows = [
        row for row in _get_state()["conversions"]
        if str(row.get("转型编号")) == str(cast_no)
    ]
    if receipt_no:
        rows = [
            row for row in rows
            if str(row.get("换算凭据编号")) == str(receipt_no)
        ]
    if not rows:
        return None
    receipt = rows[-1]
    factor_no = str(receipt.get("消费系数编号") or "")
    current = next(
        (row for row in store.rows("unit_normalize")
         if str(row.get("归一编号")) == factor_no), None)
    receipt_no = str(receipt.get("换算凭据编号") or "")
    related = [
        row for row in _get_state()["conversions"]
        if str(row.get("交接编号")) == str(receipt.get("交接编号"))
    ]
    return {
        **receipt,
        "同批换算": [
            {
                "换算凭据编号": row.get("换算凭据编号"),
                "换算系数": row.get("换算系数"),
                "换算值": row.get("换算值"),
                "消费版本号": row.get("消费版本号") or UNVERSIONED,
            }
            for row in related if row.get("换算凭据编号") != receipt_no
        ],
        "因子当前状态": {
            "归一编号": factor_no,
            "状态": current.get("status") if current else "已不存在",
            "换算系数": current.get("换算系数") if current else "",
            "清单版本号": _factor_version(current) if current else "",
        },
        "历史口径": (
            f"当次按 {receipt.get('消费版本号') or UNVERSIONED} 的 "
            f"{factor_no} ×{receipt.get('换算系数')} 将 "
            f"{receipt.get('原始取值')} {receipt.get('原始单位')} 换算为 "
            f"{receipt.get('换算值')} {receipt.get('目标单位')}，"
            f"之后的清单变更不回写该凭据"
        ),
        "导出对应版本": receipt.get("消费版本号") or UNVERSIONED,
    }


def conversion_versions() -> list[str]:
    versions = sorted({
        str(row.get("消费版本号") or UNVERSIONED)
        for row in _get_state()["conversions"]
    })
    return versions


def export_conversions(version: str | None = None) -> tuple[str, str]:
    rows = list(_get_state()["conversions"])
    if version:
        rows = [
            row for row in rows
            if str(row.get("消费版本号") or UNVERSIONED) == version
        ]
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "换算凭据编号", "转型编号", "字段名称", "原始取值", "原始单位",
        "换算系数", "换算值", "目标单位", "消费版本号", "消费系数编号",
        "清单编号", "命中日期", "生成时间",
    ])
    for row in rows:
        writer.writerow([
            row.get("换算凭据编号"), row.get("转型编号"), row.get("字段名称"),
            row.get("原始取值"), row.get("原始单位"), row.get("换算系数"),
            row.get("换算值"), row.get("目标单位"),
            row.get("消费版本号") or UNVERSIONED, row.get("消费系数编号"),
            row.get("清单编号"), row.get("命中日期"), row.get("生成时间"),
        ])
    return buffer.getvalue(), (version or "全部版本")


def list_audits() -> list[dict[str, Any]]:
    return list(_get_state()["audits"])


def manifest_snapshot() -> dict[str, Any]:
    return {
        "manifests": list_manifests(),
        "conflicts": conflicts(),
        "conversions": list_conversions(),
        "handoffs": list_handoffs(),
        "legacy": list_legacy(),
    }
