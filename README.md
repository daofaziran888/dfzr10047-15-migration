# 解构工坊 · 异构文件解析与字段抽取编排平台

面向异构文件投递、格式探测、编码归一、解析器路由、抽取模板版本、字段解析、坏行隔离、结构嗅探、嵌套展平、取值转型、单位归一、输出契约、结果投递、解析水位与重解析编排的数据采集接入平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8004/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5177/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8004`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 文件投递 | `file_intake` | 投递批次 | 批次编号、来源渠道、原始文件名 |
| 格式探测 | `format_probe` | 探测结论 | 探测编号、关联批次、魔数特征 |
| 编码归一 | `encoding_norm` | 转码记录 | 转码编号、关联批次、原始编码 |
| 解析路由 | `parser_route` | 路由决策 | 决策编号、关联批次、推断格式 |
| 抽取模板 | `extract_tmpl` | 模板版本 | 模板编号、模板名称、适用格式 |
| 字段解析 | `field_parse` | 解析字段 | 解析编号、关联批次、命中模板 |
| 坏行隔离 | `row_quarantine` | 隔离行 | 隔离编号、关联批次、原始行号 |
| 解析批次 | `parse_batch` | 批次记录 | 批次记录编号、关联投递批次、解析器标识 |
| 结构嗅探 | `schema_sniff` | 结构推断 | 嗅探编号、关联批次、样本行数 |
| 分隔规则 | `delimiter_rule` | 规则条目 | 规则编号、适用格式、分隔符 |
| 嵌套展平 | `nested_flatten` | 展平路径 | 展平编号、关联模板、原始路径 |
| 取值转型 | `value_cast` | 转型规则 | 转型编号、目标类型、来源类型 |
| 单位归一 | `unit_normalize` | 归一系数 | 归一编号、字段名称、原始单位（完整清单页见下文） |
| 输出契约 | `output_contract` | 契约条目 | 契约编号、关联批次、输出列集合 |
| 结果投递 | `result_deliver` | 投递记录 | 投递记录编号、关联契约、目标位置 |
| 解析水位 | `parse_watermark` | 水位读数 | 水位编号、来源渠道、已解析至位点 |
| 重解析任务 | `reparse_task` | 重解析单 | 重解析编号、关联批次、重解析原因 |
| 重复指纹 | `dup_fingerprint` | 指纹记录 | 指纹编号、关联批次、内容指纹 |
| 抽样复核 | `sample_review` | 复核样本 | 样本编号、关联批次、抽样比例 |
| 解析指标 | `parse_metric` | 指标读数 | 指标读数编号、统计窗口、来源渠道 |
| 模板差异 | `tmpl_diff` | 差异条目 | 差异编号、旧模板版本、新模板版本 |
| 投递审计 | `intake_audit` | 审计条目 | 审计编号、关联对象、操作类型 |

## 单位归一的版本化系数清单

`/unit_normalize` 是独立页面（`frontend/src/views/NormalizeView.vue`），后端在
`backend/app/services/normalize_manifest.py`，接口统一挂在 `/api/normalize/*`：

| 接口 | 作用 |
| --- | --- |
| `GET /api/normalize/legacy` | 存量系数；无版本条目照样按原始系数计算 |
| `POST /api/normalize/legacy/{归一编号}/compute` | 用指定系数计算原值 |
| `POST /api/normalize/import/preview` | 预检：影响在途转型的条目、冲突值、兼容约定违规 |
| `POST /api/normalize/import` | 登记版本；同一内容指纹只登记一次，可 `pause_after` 只写一部分 |
| `POST /api/normalize/import/{清单编号}/pause` | 暂停导入 |
| `POST /api/normalize/import/{清单编号}/resume` | 用同一文件恢复，只补未完成/失败条目，指纹不符直接拒绝 |
| `POST /api/normalize/import/{清单编号}/retry-failed` | 只处理失败条目的操作 |
| `POST /api/normalize/import/{清单编号}/rollback` | 回滚版本，停用该清单写入的因子，历史凭据不变 |
| `GET /api/normalize/conflicts` | 冲突页：已生效因子与待补核验条目分开列出 |
| `POST /api/normalize/conflicts/{条目id}/resolve` | 裁决 `保留新` / `保留旧` |
| `POST /api/normalize/handoffs` | 取值转型把准备好的数值交给清单计算并落换算凭据 |
| `GET /api/normalize/conversions?version=` | 换算清单，按消费版本筛选 |
| `GET /api/normalize/conversions/{转型编号}/credential` | 从转型历史打开当次实际换算凭据 |
| `GET /api/normalize/conversions/export?version=` | 导出对应版本的换算清单 |

关键约定：

- 同一来源、同一字段、同一原始单位只允许一条生效因子。已生效时再导入不同系数，
  旧因子保持生效，新条目进入「冲突待核验」，不自动覆盖。
- 导入提交后返回版本回执，含兼容校验结论、条目分组（已生效 / 待核验 / 失败 / 待导入）、
  影响在途转型清单，以及 `可继续处理`、`可回滚` 两个开关。
- 换算凭据保存当次消费的因子快照；回滚清单或替换系数都不改写历史凭据。
- 种子数据里 `UN-006`（A=1000）已迁入 `NM-v1`，`UN-001` 等仍是「无版本存量」。
- 取值转型工作台每行多一个「换算凭据」入口，跳到本页按 `?tab=conversions&cast=VC-xxx`
  直接打开当次实际换算凭据。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据采集、爬虫与RPA
