/** 版本化归一系数清单接口封装：预检、导入、冲突核验与换算凭据。 */
import { request } from './client'

export type ManifestEntry = {
  id: number
  清单编号: string
  版本号: string
  行号: number
  字段名称: string
  原始单位: string
  目标单位: string
  换算系数: string
  适用来源: string
  状态: string
  预检分类: string
  影响在途转型: boolean
  影响说明: string
  冲突说明: string
  冲突系数编号: string
  换算系数编号: string
  清单版本号: string
  写入时间: string
  最近错误: string
  已生效: boolean
  待补核验: boolean
}

export type Manifest = {
  id: number
  清单编号: string
  版本号: string
  来源: string
  文件名: string
  内容指纹: string
  状态: string
  条目数: number
  已写入数: number
  冲突数: number
  待核验数: number
  失败数: number
  待导入数: number
  创建时间: string
  生效时间: string
  回滚时间: string
  回执编号: string
  说明: string
  条目明细: ManifestEntry[]
  回执?: Receipt | null
}

export type Receipt = {
  回执编号: string
  清单编号: string
  版本号: string
  状态: string
  兼容校验: { 通过: boolean; 违规: { 行号: string | number; 类型: string; 说明: string }[]; 约定: string[] }
  已写入: number
  冲突: number
  待核验: number
  失败: number
  待导入: number
  可继续处理: boolean
  可回滚: boolean
  内容指纹: string
  条目分组: Record<string, string[]>
  影响在途转型: { 交接编号: string; 转型编号: string; 字段名称: string; 原始取值: string; 原始单位: string; 说明: string }[]
  提示: string
  生成时间: string
  回滚?: boolean
}

export type Preview = {
  ok: boolean
  message?: string
  清单版本号: string
  来源: string
  文件名: string
  内容指纹: string
  条目数: number
  条目: ManifestEntry[]
  影响在途转型: { 交接编号: string; 转型编号: string; 字段名称: string; 原始取值: string; 原始单位: string; 说明: string; 行号: number }[]
  冲突值: {
    行号: number
    字段名称: string
    适用来源: string
    原始单位: string
    目标单位: string
    申报系数: string
    分类: string
    冲突系数编号: string
    说明: string
  }[]
  兼容校验: { 通过: boolean; 违规: { 行号: string | number; 类型: string; 说明: string }[]; 约定: string[] }
  重复清单: boolean
  已存在清单: { 清单编号: string; 版本号: string; 状态: string } | null
  可导入: boolean
}

export type ConflictGroup = {
  键: string
  字段名称: string
  适用来源: string
  原始单位: string
  目标单位: string
  已生效: {
    归一编号: string
    换算系数: string
    目标单位: string
    清单编号: string
    清单版本号: string
    状态: string
  } | null
  待补核验: {
    条目id: number
    清单编号: string
    清单版本号: string
    行号: number
    换算系数: string
    目标单位: string
    状态: string
    冲突说明: string
  }[]
  需补核验: boolean
  说明: string
}

export type ConflictPage = {
  分组: ConflictGroup[]
  待核验条目: ManifestEntry[]
  已生效条目: { 归一编号: string; 字段名称: string; 原始单位: string; 目标单位: string; 换算系数: string; 适用来源: string; 清单版本号: string; 状态: string }[]
  冲突键: string[]
}

export type LegacyRow = {
  id: number
  status: string
  归一编号: string
  字段名称: string
  原始单位: string
  目标单位: string
  换算系数: string
  适用来源: string
  生效起日: string
  最近命中: string
  清单版本号: string
  版本化: boolean
  迁移状态: string
  可计算原值: boolean
  使用方式: string
}

export type ConversionReceipt = {
  id: number
  换算凭据编号: string
  转型编号: string
  交接编号: string
  字段名称: string
  原始取值: string
  原始单位: string
  目标单位: string
  换算系数: string
  换算值: string
  消费版本号: string
  消费系数编号: string
  清单编号: string
  命中日期: string
  无版本存量: boolean
  生成时间: string
  因子快照: Record<string, string>
}

export type Credential = ConversionReceipt & {
  因子当前状态: { 归一编号: string; 状态: string; 换算系数: string; 清单版本号: string }
  历史口径: string
  导出对应版本: string
}

export type ImportResult = {
  ok: boolean
  message: string
  重复登记?: boolean
  可继续处理?: boolean
  清单编号?: string
  版本号?: string
  清单?: Manifest
  回执?: Receipt
  兼容校验?: Preview['兼容校验']
  冲突值?: Preview['冲突值']
  影响在途转型?: Preview['影响在途转型']
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  return (await response.json()) as T
}

export async function listManifests(): Promise<Manifest[]> {
  const response = await request('/api/normalize/manifests')
  if (!response.ok) throw new Error('版本清单读取失败')
  const payload = (await response.json()) as { items: Manifest[] }
  return payload.items
}

export async function listLegacy(): Promise<LegacyRow[]> {
  const response = await request('/api/normalize/legacy')
  if (!response.ok) throw new Error('存量系数读取失败')
  const payload = (await response.json()) as { items: LegacyRow[] }
  return payload.items
}

export function computeLegacy(factorNo: string, rawValue: string) {
  return postJson<{ ok: boolean; message: string; 换算值?: number; 消费版本号?: string; 无版本存量?: boolean }>(
    `/api/normalize/legacy/${encodeURIComponent(factorNo)}/compute`,
    { raw_value: rawValue },
  )
}

export function previewImport(payload: Record<string, unknown>) {
  return postJson<Preview>('/api/normalize/import/preview', payload)
}

export function importManifest(payload: Record<string, unknown>) {
  return postJson<ImportResult>('/api/normalize/import', payload)
}

export function getManifest(manifestNo: string) {
  return request(`/api/normalize/import/${manifestNo}`).then((res) => res.json() as Promise<Manifest>)
}

export function pauseImport(manifestNo: string) {
  return postJson<ImportResult>(`/api/normalize/import/${manifestNo}/pause`, {})
}

export function resumeImport(manifestNo: string, payload?: Record<string, unknown>) {
  return postJson<ImportResult>(`/api/normalize/import/${manifestNo}/resume`, payload ?? {})
}

export function retryFailed(manifestNo: string) {
  return postJson<ImportResult>(`/api/normalize/import/${manifestNo}/retry-failed`, {})
}

export function rollbackManifest(manifestNo: string, reason = '人工回滚') {
  return postJson<ImportResult & { 停用因子: string[]; 历史换算凭据: number }>(
    `/api/normalize/import/${manifestNo}/rollback?reason=${encodeURIComponent(reason)}`, {},
  )
}

export async function listConflicts(): Promise<ConflictPage> {
  const response = await request('/api/normalize/conflicts')
  if (!response.ok) throw new Error('冲突清单读取失败')
  return (await response.json()) as ConflictPage
}

export function resolveConflict(entryId: number, decision: string) {
  return postJson<{ ok: boolean; message: string; 结算交接?: unknown[] }>(
    `/api/normalize/conflicts/${entryId}/resolve`, { decision },
  )
}

export async function listConversions(version = ''): Promise<{
  items: ConversionReceipt[]
  total: number
  versions: string[]
}> {
  const query = version ? `?version=${encodeURIComponent(version)}` : ''
  const response = await request(`/api/normalize/conversions${query}`)
  if (!response.ok) throw new Error('换算凭据读取失败')
  return await response.json()
}

export async function loadCredential(castNo: string, receiptNo = ''): Promise<Credential> {
  const query = receiptNo ? `?receipt=${encodeURIComponent(receiptNo)}` : ''
  const response = await request(
    `/api/normalize/conversions/${encodeURIComponent(castNo)}/credential${query}`)
  if (!response.ok) throw new Error('换算凭据不存在')
  return (await response.json()) as Credential
}

export async function downloadConversions(version = ''): Promise<void> {
  const query = version ? `?version=${encodeURIComponent(version)}` : ''
  const response = await request(`/api/normalize/conversions/export${query}`)
  if (!response.ok) throw new Error('换算清单导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `normalize-conversions-${version || 'all'}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export function submitHandoff(values: Record<string, string>) {
  return postJson<{ ok: boolean; message: string; 归一取值?: number; 消费版本号?: string }>(
    '/api/normalize/handoffs', values,
  )
}
