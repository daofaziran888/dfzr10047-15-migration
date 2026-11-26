<template>
  <section class="page" data-testid="normalize-view">
    <header class="page-head">
      <div>
        <h2>单位归一 · 版本化系数清单</h2>
        <p class="page-desc">
          把存量无版本系数迁入版本化清单；导入先预检影响在途转型的条目与冲突值，
          提交后由服务端按兼容约定给出可继续处理或回滚的版本回执。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="refresh" type="button" @click="reloadAll">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>
    <p v-if="flashMessage" class="flash-text" data-testid="flash">{{ flashMessage }}</p>

    <nav class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        :data-testid="`tab-${tab.key}`"
        type="button"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <!-- ================= 存量系数 ================= -->
    <section v-if="activeTab === 'legacy'" class="panel" data-testid="panel-legacy">
      <div class="panel-head">
        <h3>存量归一系数</h3>
        <span class="hint">无版本存量仍可计算原值；版本化系数展示迁入的清单版本</span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>归一编号</th><th>字段名称</th><th>原始单位→目标单位</th>
            <th>换算系数</th><th>适用来源</th><th>清单版本</th>
            <th>迁移状态</th><th>状态</th><th>原值试算</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in legacyRows"
            :key="row.归一编号"
            :data-testid="`legacy-${row.归一编号}`"
            :data-versioned="String(row.版本化)"
          >
            <td>{{ row.归一编号 }}</td>
            <td>{{ row.字段名称 }}</td>
            <td>{{ row.原始单位 }} → {{ row.目标单位 }}</td>
            <td>{{ row.换算系数 }}</td>
            <td>{{ row.适用来源 }}</td>
            <td>
              <span :class="row.版本化 ? 'tag versioned' : 'tag legacy'">
                {{ row.版本化 ? row.清单版本号 : '无版本存量' }}
              </span>
            </td>
            <td>{{ row.迁移状态 }}</td>
            <td>{{ row.status }}</td>
            <td class="inline-actions">
              <input
                v-model="legacyInputs[row.归一编号]"
                class="mini-input"
                :data-testid="`legacy-input-${row.归一编号}`"
                placeholder="原始取值"
              />
              <button
                class="link"
                type="button"
                :data-testid="`legacy-compute-${row.归一编号}`"
                @click="computeLegacy(row.归一编号)"
              >
                计算
              </button>
              <span v-if="legacyResults[row.归一编号]" class="compute-result">
                {{ legacyResults[row.归一编号] }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- ================= 清单 / 导入 ================= -->
    <section v-if="activeTab === 'manifests'" data-testid="panel-manifests">
      <div class="panel">
        <div class="panel-head">
          <h3>导入归一系数清单</h3>
          <span class="hint">先预检后提交；相同内容指纹的清单只登记一次</span>
        </div>
        <div class="form-grid">
          <label class="create-field">
            <span>清单版本号</span>
            <input v-model="form.版本号" data-testid="import-version" placeholder="例如 NM-v2" />
          </label>
          <label class="create-field">
            <span>来源</span>
            <input v-model="form.来源" data-testid="import-source" placeholder="例如 sftp-node-03" />
          </label>
          <label class="create-field">
            <span>文件名</span>
            <input v-model="form.文件名" data-testid="import-file" />
          </label>
          <label class="create-field">
            <span>只写前 N 条后暂停（留空则全部写入）</span>
            <input v-model="form.pauseAfter" data-testid="import-pause-after" placeholder="例如 1" />
          </label>
        </div>
        <label class="create-field">
          <span>清单内容（JSON 条目数组，或 CSV：字段名称,原始单位,目标单位,换算系数,适用来源）</span>
          <textarea
            v-model="form.内容"
            rows="8"
            data-testid="import-content"
            class="manifest-content"
          ></textarea>
        </label>
        <div class="page-actions">
          <button class="btn" data-testid="preview-import" type="button" @click="doPreview">
            {{ previewing ? '预检中…' : '预检清单' }}
          </button>
          <button
            class="btn primary"
            data-testid="submit-import"
            type="button"
            :disabled="!preview || !preview.可导入 || importing"
            @click="doImport"
          >
            {{ importing ? '导入中…' : '提交导入' }}
          </button>
          <button class="btn ghost" type="button" @click="fillDemo">填入示例清单</button>
        </div>
      </div>

      <div v-if="preview" class="panel" data-testid="preview-panel">
        <div class="panel-head">
          <h3>预检结果 · {{ preview.清单版本号 || '未命名版本' }}</h3>
          <span :class="preview.可导入 ? 'tag versioned' : 'tag legacy'">
            {{ preview.可导入 ? '兼容约定通过，可导入' : '兼容约定未通过' }}
          </span>
        </div>
        <p class="hint" data-testid="preview-fingerprint">内容指纹：{{ preview.内容指纹 }}</p>
        <p v-if="preview.重复清单 && preview.已存在清单" class="warning-text" data-testid="preview-duplicate">
          同一份清单已登记为 {{ preview.已存在清单.清单编号 }}
          （{{ preview.已存在清单.状态 }}），提交只会登记一次。
        </p>

        <h4>会影响正在取值转型的条目</h4>
        <table class="data-table" data-testid="preview-impact">
          <thead>
            <tr><th>行号</th><th>字段</th><th>原始取值</th><th>单位</th><th>转型编号</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in preview.影响在途转型" :key="item.交接编号" :data-testid="`impact-${item.交接编号}`">
              <td>{{ item.行号 }}</td>
              <td>{{ item.字段名称 }}</td>
              <td>{{ item.原始取值 }}</td>
              <td>{{ item.原始单位 }}</td>
              <td>{{ item.转型编号 }}</td>
              <td>{{ item.说明 }}</td>
            </tr>
            <tr v-if="!preview.影响在途转型.length">
              <td colspan="6" class="empty-state">没有正在进行的取值转型受影响</td>
            </tr>
          </tbody>
        </table>

        <h4>冲突值</h4>
        <table class="data-table" data-testid="preview-conflicts">
          <thead>
            <tr>
              <th>行号</th><th>字段</th><th>来源</th><th>单位</th>
              <th>申报系数</th><th>分类</th><th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in preview.冲突值" :key="`${item.行号}-${item.字段名称}`" :data-testid="`preview-conflict-${item.行号}`">
              <td>{{ item.行号 }}</td>
              <td>{{ item.字段名称 }}</td>
              <td>{{ item.适用来源 }}</td>
              <td>{{ item.原始单位 }} → {{ item.目标单位 }}</td>
              <td>{{ item.申报系数 }}</td>
              <td>{{ item.分类 }}</td>
              <td>{{ item.说明 }}</td>
            </tr>
            <tr v-if="!preview.冲突值.length">
              <td colspan="7" class="empty-state">没有冲突系数</td>
            </tr>
          </tbody>
        </table>

        <h4>兼容约定</h4>
        <ul class="convention-list" data-testid="preview-rules">
          <li v-for="rule in preview.兼容校验.约定" :key="rule">{{ rule }}</li>
        </ul>
        <p v-if="preview.兼容校验.违规.length" class="error-text">
          <span v-for="item in preview.兼容校验.违规" :key="`${item.行号}-${item.类型}`">
            [行{{ item.行号 }}·{{ item.类型 }}] {{ item.说明 }}；
          </span>
        </p>
      </div>

      <div v-if="lastResult" class="panel" data-testid="import-receipt">
        <div class="panel-head">
          <h3>版本回执 · {{ lastResult.回执?.回执编号 || '—' }}</h3>
          <span :class="lastResult.回执?.可回滚 ? 'tag versioned' : 'tag legacy'">
            {{ lastResult.回执?.状态 }}
          </span>
        </div>
        <p data-testid="import-message">{{ lastResult.message }}</p>
        <p v-if="lastResult.重复登记" class="warning-text" data-testid="duplicate-notice">
          重复导入只登记一次：{{ lastResult.清单编号 }}
        </p>
        <div class="receipt-grid">
          <div>已写入：{{ lastResult.回执?.已写入 }}</div>
          <div>冲突待核验：{{ lastResult.回执?.待核验 }}</div>
          <div>失败：{{ lastResult.回执?.失败 }}</div>
          <div>待导入：{{ lastResult.回执?.待导入 }}</div>
        </div>
        <details class="receipt-groups" open>
          <summary>条目分组</summary>
          <div v-for="(items, group) in lastResult.回执?.条目分组 || {}" :key="group" class="receipt-group">
            <strong>{{ group }}（{{ items.length }}）</strong>
            <span v-if="items.length">{{ items.join('；') }}</span>
            <span v-else class="hint">无</span>
          </div>
        </details>
        <p v-if="lastResult.回执?.影响在途转型?.length" class="warning-text">
          影响在途转型：{{ lastResult.回执?.影响在途转型.map((item) => `${item.转型编号} 的 ${item.原始取值}${item.原始单位}`).join('、') }}
        </p>
        <div class="page-actions">
          <button
            v-if="lastResult.清单编号"
            class="btn"
            data-testid="receipt-pause"
            type="button"
            @click="doPause(lastResult.清单编号)"
          >
            暂停导入
          </button>
          <button
            v-if="lastResult.清单编号"
            class="btn primary"
            data-testid="receipt-resume"
            type="button"
            @click="doResume(lastResult.清单编号, true)"
          >
            用同一文件恢复
          </button>
          <button
            v-if="lastResult.清单编号"
            class="btn"
            data-testid="receipt-retry-failed"
            type="button"
            @click="doRetryFailed(lastResult.清单编号)"
          >
            只处理失败条目
          </button>
          <button
            v-if="lastResult.清单编号"
            class="btn ghost"
            data-testid="receipt-rollback"
            type="button"
            @click="doRollback(lastResult.清单编号)"
          >
            回滚该版本
          </button>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>版本清单</h3>
          <span class="hint">首份清单 NM-v1 已生效，旧系数 A=1000 由存量迁入</span>
        </div>
        <table class="data-table" data-testid="manifest-table">
          <thead>
            <tr>
              <th>清单编号</th><th>版本号</th><th>来源</th><th>状态</th>
              <th>条目</th><th>已写入</th><th>冲突待核验</th><th>失败</th>
              <th>待导入</th><th>回执</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in manifests" :key="item.清单编号" :data-testid="`manifest-${item.清单编号}`">
              <td>{{ item.清单编号 }}</td>
              <td>{{ item.版本号 }}</td>
              <td>{{ item.来源 }}</td>
              <td>{{ item.状态 }}</td>
              <td>{{ item.条目数 }}</td>
              <td>{{ item.已写入数 }}</td>
              <td>{{ item.待核验数 }}</td>
              <td>{{ item.失败数 }}</td>
              <td>{{ item.待导入数 }}</td>
              <td>{{ item.回执编号 }}</td>
              <td class="row-actions">
                <button class="link" type="button" :data-testid="`manifest-resume-${item.清单编号}`" @click="doResume(item.清单编号, false)">
                  续传未完成条目
                </button>
                <button class="link" type="button" :data-testid="`manifest-retry-${item.清单编号}`" @click="doRetryFailed(item.清单编号)">
                  只处理失败
                </button>
                <button class="link" type="button" :data-testid="`manifest-rollback-${item.清单编号}`" @click="doRollback(item.清单编号)">
                  回滚
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ================= 冲突核验 ================= -->
    <section v-if="activeTab === 'conflicts'" class="panel" data-testid="panel-conflicts">
      <div class="panel-head">
        <h3>系数冲突核验</h3>
        <span class="hint">同一来源同一单位只允许一条生效因子；冲突条目需补核验后才能替换</span>
      </div>
      <table class="data-table" data-testid="conflict-table">
        <thead>
          <tr>
            <th>字段</th><th>来源</th><th>单位</th><th>已生效</th>
            <th>待补核验</th><th>说明</th><th>裁决</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="group in conflicts.分组" :key="group.键" :data-testid="`conflict-${group.键}`">
            <td>{{ group.字段名称 }}</td>
            <td>{{ group.适用来源 }}</td>
            <td>{{ group.原始单位 }} → {{ group.目标单位 }}</td>
            <td>
              <div v-if="group.已生效" class="factor-cell" data-testid="conflict-active">
                <span class="tag versioned">已生效</span>
                {{ group.已生效.归一编号 }} ×{{ group.已生效.换算系数 }}
                <span class="hint">（{{ group.已生效.清单版本号 }}）</span>
              </div>
              <span v-else class="empty-state">没有生效因子</span>
            </td>
            <td>
              <div
                v-for="pending in group.待补核验"
                :key="pending.条目id"
                class="factor-cell"
                data-testid="conflict-pending"
              >
                <span class="tag legacy">待补核验</span>
                ×{{ pending.换算系数 }}
                <span class="hint">（{{ pending.清单版本号 }} 第{{ pending.行号 }}行）</span>
              </div>
            </td>
            <td>{{ group.说明 }}</td>
            <td class="row-actions">
              <button
                v-for="pending in group.待补核验"
                :key="`resolve-${pending.条目id}`"
                class="link"
                type="button"
                :data-testid="`resolve-new-${pending.条目id}`"
                @click="doResolve(pending.条目id, '保留新')"
              >
                保留新 ×{{ pending.换算系数 }}
              </button>
              <button
                v-for="pending in group.待补核验"
                :key="`keep-old-${pending.条目id}`"
                class="link"
                type="button"
                :data-testid="`resolve-old-${pending.条目id}`"
                @click="doResolve(pending.条目id, '保留旧')"
              >
                保留旧
              </button>
            </td>
          </tr>
          <tr v-if="!conflicts.分组.length">
            <td colspan="7" class="empty-state">当前没有系数冲突</td>
          </tr>
        </tbody>
      </table>

      <div v-if="conflicts.分组.length" class="page-actions">
        <button
          v-for="item in attentionManifests"
          :key="`retry-${item.清单编号}`"
          class="btn"
          type="button"
          :data-testid="`conflict-retry-${item.清单编号}`"
          @click="doRetryFailed(item.清单编号)"
        >
          只处理 {{ item.清单编号 }} 的失败条目（{{ item.失败数 }}）
        </button>
      </div>

      <h4>已生效条目对照</h4>
      <table class="data-table" data-testid="conflict-active-table">
        <thead><tr><th>归一编号</th><th>字段</th><th>来源</th><th>单位</th><th>系数</th><th>清单版本</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="row in conflicts.已生效条目" :key="row.归一编号">
            <td>{{ row.归一编号 }}</td>
            <td>{{ row.字段名称 }}</td>
            <td>{{ row.适用来源 }}</td>
            <td>{{ row.原始单位 }} → {{ row.目标单位 }}</td>
            <td>{{ row.换算系数 }}</td>
            <td>{{ row.清单版本号 }}</td>
            <td>{{ row.状态 }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- ================= 换算凭据 ================= -->
    <section v-if="activeTab === 'conversions'" data-testid="panel-conversions">
      <div class="panel">
        <div class="panel-head">
          <h3>换算凭据 · 从转型历史打开旧系数</h3>
          <span class="hint">每条凭据保存当次实际消费的因子快照，回滚或替换系数都不改写历史</span>
        </div>
        <div class="form-grid">
          <label class="create-field">
            <span>按版本筛选</span>
            <select v-model="conversionVersion" data-testid="conversion-version" @change="loadConversions">
              <option value="">全部版本</option>
              <option v-for="version in conversionVersions" :key="version" :value="version">
                {{ version }}
              </option>
            </select>
          </label>
          <label class="create-field">
            <span>从转型历史查凭据</span>
            <input v-model="castLookup" data-testid="credential-cast" placeholder="转型编号，例如 VC-001" />
          </label>
        </div>
        <div class="page-actions">
          <button class="btn" data-testid="load-credential" type="button" @click="openCredential(castLookup)">
            打开当次换算凭据
          </button>
          <button class="btn" data-testid="export-conversions" type="button" @click="doExport">
            导出{{ conversionVersion || '全部版本' }}换算清单
          </button>
        </div>
      </div>

      <div v-if="credential" class="panel credential-panel" data-testid="credential-panel">
        <div class="panel-head">
          <h3>换算凭据 {{ credential.换算凭据编号 }}</h3>
          <span class="tag versioned">消费版本 {{ credential.导出对应版本 }}</span>
        </div>
        <dl class="credential-grid">
          <div><dt>转型编号</dt><dd>{{ credential.转型编号 }}</dd></div>
          <div><dt>交接编号</dt><dd>{{ credential.交接编号 }}</dd></div>
          <div><dt>字段</dt><dd>{{ credential.字段名称 }}</dd></div>
          <div><dt>实际消费因子</dt><dd>{{ credential.消费系数编号 }} ×{{ credential.换算系数 }}</dd></div>
          <div><dt>换算结果</dt><dd>{{ credential.原始取值 }} {{ credential.原始单位 }} → {{ credential.换算值 }} {{ credential.目标单位 }}</dd></div>
          <div><dt>命中日期</dt><dd>{{ credential.命中日期 }}</dd></div>
          <div><dt>因子当前状态</dt>
            <dd>
              {{ credential.因子当前状态.归一编号 }} · {{ credential.因子当前状态.状态 }}
              · ×{{ credential.因子当前状态.换算系数 }}
            </dd>
          </div>
          <div><dt>生成时间</dt><dd>{{ credential.生成时间 }}</dd></div>
        </dl>
        <p class="hint" data-testid="credential-history">{{ credential.历史口径 }}</p>
        <pre class="snapshot" data-testid="credential-snapshot">{{ JSON.stringify(credential.因子快照, null, 2) }}</pre>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>换算清单（{{ conversionVersion || '全部版本' }}）</h3>
          <span class="hint">最近命中也标出当次实际消费的版本</span>
        </div>
        <table class="data-table" data-testid="conversion-table">
          <thead>
            <tr>
              <th>凭据编号</th><th>转型编号</th><th>字段</th><th>原始取值</th>
              <th>系数</th><th>换算值</th><th>目标单位</th><th>消费版本</th>
              <th>消费因子</th><th>生成时间</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in conversions" :key="row.换算凭据编号" :data-testid="`conversion-${row.换算凭据编号}`">
              <td>{{ row.换算凭据编号 }}</td>
              <td>{{ row.转型编号 }}</td>
              <td>{{ row.字段名称 }}</td>
              <td>{{ row.原始取值 }} {{ row.原始单位 }}</td>
              <td>{{ row.换算系数 }}</td>
              <td>{{ row.换算值 }}</td>
              <td>{{ row.目标单位 }}</td>
              <td>{{ row.消费版本号 || '无版本存量' }}</td>
              <td>{{ row.消费系数编号 }}</td>
              <td>{{ row.生成时间 }}</td>
              <td>
                <button class="link" type="button" :data-testid="`credential-open-${row.换算凭据编号}`" @click="openCredential(row.转型编号, row.换算凭据编号)">
                  重看旧系数
                </button>
              </td>
            </tr>
            <tr v-if="!conversions.length">
              <td colspan="11" class="empty-state">该版本还没有换算凭据</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ================= 转型交接 ================= -->
    <section v-if="activeTab === 'handoffs'" class="panel" data-testid="panel-handoffs">
      <div class="panel-head">
        <h3>取值转型交付与在途交接</h3>
        <span class="hint">转型准备好的数值交给清单计算；冲突未裁决时保持在途</span>
      </div>
      <div class="form-grid">
        <label class="create-field"><span>转型编号</span><input v-model="handoffForm.转型编号" data-testid="handoff-cast" /></label>
        <label class="create-field"><span>字段名称</span><input v-model="handoffForm.字段名称" data-testid="handoff-field" /></label>
        <label class="create-field"><span>原始取值</span><input v-model="handoffForm.原始取值" data-testid="handoff-value" /></label>
        <label class="create-field"><span>原始单位</span><input v-model="handoffForm.原始单位" data-testid="handoff-unit" /></label>
        <label class="create-field"><span>适用来源</span><input v-model="handoffForm.适用来源" data-testid="handoff-source" /></label>
        <label class="create-field"><span>命中日期</span><input v-model="handoffForm.命中日期" data-testid="handoff-date" /></label>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="submit-handoff" type="button" @click="doHandoff">
          交给清单计算归一字段
        </button>
      </div>
      <table class="data-table" data-testid="handoff-table">
        <thead>
          <tr><th>交接编号</th><th>转型编号</th><th>字段</th><th>原始取值</th><th>状态</th><th>消费版本</th><th>凭据</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in handoffs" :key="row.交接编号">
            <td>{{ row.交接编号 }}</td>
            <td>{{ row.转型编号 }}</td>
            <td>{{ row.字段名称 }}</td>
            <td>{{ row.原始取值 }} {{ row.原始单位 }}</td>
            <td>{{ row.状态 }}</td>
            <td>{{ row.消费版本号 || '—' }}</td>
            <td>{{ row.换算凭据编号 || '—' }}</td>
            <td>{{ row.说明 }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  computeLegacy as computeLegacyApi,
  downloadConversions,
  importManifest,
  listConflicts,
  listConversions,
  listLegacy,
  listManifests,
  loadCredential,
  pauseImport,
  previewImport,
  resolveConflict,
  resumeImport,
  retryFailed,
  rollbackManifest,
  submitHandoff,
} from '@/api/normalize'
import { request } from '@/api/client'
import type {
  ConflictPage,
  ConversionReceipt,
  Credential,
  ImportResult,
  LegacyRow,
  Manifest,
  Preview,
} from '@/api/normalize'

const route = useRoute()
const tabs = [
  { key: 'legacy', label: '存量系数' },
  { key: 'manifests', label: '版本清单与导入' },
  { key: 'conflicts', label: '冲突核验' },
  { key: 'conversions', label: '换算凭据' },
  { key: 'handoffs', label: '转型交接' },
]
const activeTab = ref('manifests')
const errorMessage = ref('')
const flashMessage = ref('')

const legacyRows = ref<LegacyRow[]>([])
const legacyInputs = reactive<Record<string, string>>({})
const legacyResults = reactive<Record<string, string>>({})

const manifests = ref<Manifest[]>([])
const preview = ref<Preview | null>(null)
const lastResult = ref<ImportResult | null>(null)
const previewing = ref(false)
const importing = ref(false)

const form = reactive({
  版本号: 'NM-v2',
  来源: 'sftp-node-03',
  文件名: 'normalize-v2.json',
  内容: '',
  pauseAfter: '',
})

const conflicts = ref<ConflictPage>({
  分组: [], 待核验条目: [], 已生效条目: [], 冲突键: [],
})
const attentionManifests = computed(() =>
  manifests.value.filter((item) => item.失败数 > 0 || item.待导入数 > 0),
)

const conversions = ref<ConversionReceipt[]>([])
const conversionVersion = ref('')
const conversionVersions = ref<string[]>([])
const castLookup = ref('')
const credential = ref<Credential | null>(null)

const handoffs = ref<Record<string, string>[]>([])
const handoffForm = reactive({
  转型编号: 'VC-002',
  字段名称: 'weight',
  原始取值: '2',
  原始单位: 'kg',
  适用来源: 'sftp-node-03',
  命中日期: '2026-09-03',
})

const demoEntries = [
  { 字段名称: 'weight', 原始单位: 'kg', 目标单位: 'g', 换算系数: '1024', 适用来源: 'sftp-node-03' },
  { 字段名称: 'height', 原始单位: 'm', 目标单位: 'cm', 换算系数: '100', 适用来源: 'sftp-node-03' },
]

function fillDemo() {
  form.内容 = JSON.stringify(demoEntries, null, 2)
}

function payloadFor() {
  const payload: Record<string, unknown> = {
    版本号: form.版本号,
    来源: form.来源,
    文件名: form.文件名,
    内容: form.内容,
  }
  const pauseAfter = form.pauseAfter.trim()
  if (pauseAfter && /^\d+$/.test(pauseAfter)) payload.pause_after = Number(pauseAfter)
  return payload
}

function setFlash(message: string) {
  flashMessage.value = message
  errorMessage.value = ''
}

function setError(message: string) {
  errorMessage.value = message
  flashMessage.value = ''
}

async function reloadAll() {
  errorMessage.value = ''
  try {
    legacyRows.value = await listLegacy()
    manifests.value = await listManifests()
    conflicts.value = await listConflicts()
    await loadConversions()
    await loadHandoffs()
  } catch (error) {
    setError(error instanceof Error ? error.message : '归一清单读取失败')
  }
}

async function loadHandoffs() {
  const response = await request('/api/normalize/handoffs')
  if (!response.ok) return
  const payload = (await response.json()) as { items: Record<string, string>[] }
  handoffs.value = payload.items
}

async function loadConversions() {
  const payload = await listConversions(conversionVersion.value)
  conversions.value = payload.items
  conversionVersions.value = payload.versions
}

async function computeLegacy(factorNo: string) {
  const raw = legacyInputs[factorNo] || '1'
  try {
    const result = await computeLegacyApi(factorNo, raw)
    legacyResults[factorNo] = result.ok
      ? `${raw} → ${result.换算值} ${result.无版本存量 ? '（无版本存量按原系数）' : `（${result.消费版本号}）`}`
      : result.message
  } catch (error) {
    setError(error instanceof Error ? error.message : '原值计算失败')
  }
}

async function doPreview() {
  previewing.value = true
  errorMessage.value = ''
  try {
    const result = await previewImport(payloadFor())
    if (!result.ok) {
      setError(result.message || '预检失败')
      preview.value = null
      return
    }
    preview.value = result
    setFlash(`预检完成：${result.条目数} 条，冲突 ${result.冲突值.length} 条`)
  } catch (error) {
    setError(error instanceof Error ? error.message : '预检失败')
  } finally {
    previewing.value = false
  }
}

async function doImport() {
  importing.value = true
  errorMessage.value = ''
  try {
    const result = await importManifest(payloadFor())
    lastResult.value = result
    if (!result.ok) {
      setError(result.message)
      return
    }
    setFlash(result.message)
    preview.value = null
    await reloadAll()
  } catch (error) {
    setError(error instanceof Error ? error.message : '导入失败')
  } finally {
    importing.value = false
  }
}

function applyResult(result: ImportResult, label: string) {
  lastResult.value = result
  if (!result.ok) {
    setError(result.message)
    return
  }
  setFlash(`${label}：${result.message}`)
}

async function doPause(manifestNo: string) {
  try {
    applyResult(await pauseImport(manifestNo), '暂停导入')
    await reloadAll()
  } catch (error) {
    setError(error instanceof Error ? error.message : '暂停失败')
  }
}

async function doResume(manifestNo: string, withFile = false) {
  try {
    const result = await resumeImport(manifestNo, withFile ? payloadFor() : undefined)
    applyResult(result, withFile ? '用同一文件恢复' : '续传未完成条目')
    await reloadAll()
  } catch (error) {
    setError(error instanceof Error ? error.message : '恢复失败')
  }
}

async function doRetryFailed(manifestNo: string) {
  try {
    applyResult(await retryFailed(manifestNo), '只处理失败条目')
    await reloadAll()
  } catch (error) {
    setError(error instanceof Error ? error.message : '失败条目处理失败')
  }
}

async function doRollback(manifestNo: string) {
  try {
    const result = await rollbackManifest(manifestNo)
    applyResult(result, '回滚版本')
    await reloadAll()
  } catch (error) {
    setError(error instanceof Error ? error.message : '回滚失败')
  }
}

async function doResolve(entryId: number, decision: string) {
  try {
    const result = await resolveConflict(entryId, decision)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setFlash(result.message)
    await reloadAll()
  } catch (error) {
    setError(error instanceof Error ? error.message : '冲突裁决失败')
  }
}

async function openCredential(castNo: string, receiptNo = '') {
  if (!castNo.trim()) {
    setError('请填写转型编号')
    return
  }
  try {
    credential.value = await loadCredential(castNo.trim(), receiptNo)
    setFlash(`已打开转型 ${castNo} 的当次换算凭据`)
  } catch (error) {
    setError(error instanceof Error ? error.message : '换算凭据读取失败')
  }
}

async function doExport() {
  try {
    await downloadConversions(conversionVersion.value)
    setFlash(`已导出 ${conversionVersion.value || '全部版本'} 的换算清单`)
  } catch (error) {
    setError(error instanceof Error ? error.message : '导出失败')
  }
}

async function doHandoff() {
  try {
    const result = await submitHandoff({ ...handoffForm })
    if (!result.ok) {
      setError(result.message)
    } else {
      setFlash(result.message)
    }
    await reloadAll()
  } catch (error) {
    setError(error instanceof Error ? error.message : '交接失败')
  }
}

async function applyRouteQuery() {
  const tab = String(route.query.tab ?? '')
  if (tabs.some((item) => item.key === tab)) activeTab.value = tab
  const cast = String(route.query.cast ?? '')
  if (cast) {
    castLookup.value = cast
    await openCredential(cast)
  }
}

watch(
  () => [route.query.tab, route.query.cast],
  () => { void applyRouteQuery() },
)

onMounted(async () => {
  fillDemo()
  await reloadAll()
  await applyRouteQuery()
})
</script>
