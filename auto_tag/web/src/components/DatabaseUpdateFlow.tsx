import type { ReactNode } from 'react'
import type { StatsResponse } from '../api/client'

type AnnoMode = 'full' | 'incremental'

interface DatabaseUpdateFlowProps {
  stats: StatsResponse
  annoMode: AnnoMode
  setAnnoMode: (m: AnnoMode) => void
  centersOnly: boolean
  setCentersOnly: (v: boolean) => void
  busy: boolean
  onRebuild: () => void | Promise<void>
  onRecompute: () => void | Promise<void>
  onReannotate: () => void | Promise<void>
}

function DecisionNode({
  title,
  satisfied,
  satisfiedLabel = '条件满足',
  unsatisfiedLabel = '条件未满足',
  hint,
}: {
  title: string
  satisfied: boolean
  satisfiedLabel?: string
  unsatisfiedLabel?: string
  hint?: string
}) {
  return (
    <div
      className={`rounded-lg border-2 px-3 py-2.5 text-center text-sm transition-colors ${
        satisfied
          ? 'border-emerald-500/80 bg-emerald-50 dark:bg-emerald-950/25'
          : 'border-amber-400/70 bg-amber-50/80 dark:bg-amber-950/20'
      }`}
    >
      <div className="font-medium text-gray-800 dark:text-gray-100">{title}</div>
      <div
        className={`mt-1 text-xs font-medium ${
          satisfied
            ? 'text-emerald-700 dark:text-emerald-400'
            : 'text-amber-700 dark:text-amber-400'
        }`}
      >
        {satisfied ? `✓ ${satisfiedLabel}` : `○ ${unsatisfiedLabel}`}
      </div>
      {hint && (
        <p className="mt-2 text-xs leading-relaxed text-gray-500 dark:text-gray-400">{hint}</p>
      )}
    </div>
  )
}

function ActionNode({
  title,
  subtitle,
  enabled,
  disabledHint,
  tone,
  onClick,
  busy,
  children,
}: {
  title: string
  subtitle?: string
  enabled: boolean
  disabledHint?: string
  tone: 'orange' | 'yellow' | 'blue' | 'indigo'
  onClick?: () => void
  busy?: boolean
  children?: ReactNode
}) {
  const toneCls = {
    orange: 'bg-orange-500 hover:bg-orange-600',
    yellow: 'bg-yellow-500 hover:bg-yellow-600 text-gray-900',
    blue: 'bg-blue-600 hover:bg-blue-700',
    indigo: 'bg-indigo-600 hover:bg-indigo-700',
  }[tone]

  return (
    <div
      className={`flex flex-1 flex-col rounded-xl border p-3 transition-opacity ${
        enabled
          ? 'border-gray-200 dark:border-gray-600 shadow-sm'
          : 'border-gray-200/80 dark:border-gray-700 opacity-75'
      }`}
    >
      <div className="mb-2 text-center">
        <div className="text-sm font-semibold text-gray-800 dark:text-gray-100">{title}</div>
        {subtitle && (
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{subtitle}</p>
        )}
      </div>
      {children}
      {onClick && (
        <button
          type="button"
          onClick={onClick}
          disabled={!enabled || busy}
          className={`mt-auto w-full rounded-lg px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-45 ${toneCls}`}
        >
          {busy ? '执行中…' : title}
        </button>
      )}
      {!enabled && disabledHint && (
        <p className="mt-2 text-center text-xs text-gray-500 dark:text-gray-400">{disabledHint}</p>
      )}
    </div>
  )
}

function FlowColumn({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-3 rounded-lg border border-gray-100 bg-gray-50/50 p-3 dark:border-gray-700 dark:bg-gray-900/30">
      {children}
    </div>
  )
}

export default function DatabaseUpdateFlow({
  stats,
  annoMode,
  setAnnoMode,
  centersOnly,
  setCentersOnly,
  busy,
  onRebuild,
  onRecompute,
  onReannotate,
}: DatabaseUpdateFlowProps) {
  const hasSnapshot = Boolean(stats.has_snapshot)
  const hasRecords = (stats.embedding_record_count || stats.chroma_document_count || 0) > 0
  const inputDirs = (stats.snapshot?.input_dirs as string[] | undefined) || []
  const canRebuild = hasSnapshot && inputDirs.length > 0
  const canRecompute = Boolean(stats.enable_recompute_relations)
  // 与上方「索引内是否有记录」条件节点一致，不单独依赖 enable_reannotate（避免与 stats 计数展示不一致）
  const canReannotate = hasSnapshot && hasRecords

  const selectCls =
    'w-full border border-gray-300 dark:border-gray-600 rounded px-2 py-1.5 text-sm bg-white dark:bg-gray-900 dark:text-gray-200 disabled:opacity-50'

  return (
    <section className="mb-0 rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
      <h4 className="mb-1 text-sm font-medium text-gray-700 dark:text-gray-300">更新维护流程</h4>
      <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
        三条维护分支并列展示（随窗口宽度自动 1～3 列排布）。绿色条件节点表示可走通。
        比对配置读取磁盘 config（与设置页一致）；实际执行仍使用后端进程内配置，修改后请重启后端。
      </p>

      <div>
        <DecisionNode
          title="是否存在构建快照？"
          satisfied={hasSnapshot}
          satisfiedLabel="已有 auto_tag_db_build_snapshot.json"
          unsatisfiedLabel="尚无快照"
          hint={
            hasSnapshot
              ? undefined
              : '请先成功完成一次标注任务，或执行完全重建后才会写入快照。'
          }
        />

        {hasSnapshot && (
          <div className="mt-4 grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,15rem),1fr))]">
            <FlowColumn>
              <DecisionNode
                title="快照含 input_dirs？"
                satisfied={canRebuild}
                unsatisfiedLabel="缺少输入目录"
                hint={canRebuild ? undefined : '快照中无 input_dirs，无法按原输入重建。'}
              />
              <ActionNode
                title="完全重建索引"
                subtitle="清空索引与侧车，按快照 input 重跑 CLIP+VLM"
                enabled={canRebuild}
                disabledHint="需要快照中含非空 input_dirs"
                tone="orange"
                onClick={onRebuild}
                busy={busy}
              />
            </FlowColumn>

            <FlowColumn>
              <DecisionNode
                title="关系参数与快照不同？"
                satisfied={canRecompute}
                unsatisfiedLabel="τ 等与快照一致"
                hint={
                  canRecompute
                    ? '检测到 tau_dup / tau_cls 等与上次任务不一致'
                    : '在设置中修改 tau_dup、tau_cls 等并保存 config 后，重新进入本页或按 F5 刷新。'
                }
              />
              <ActionNode
                title="仅重算关系"
                subtitle="复用向量与 labels，按当前 τ 重算簇（不调 VLM）"
                enabled={canRecompute}
                disabledHint="关系参数与快照一致时无需重算"
                tone="yellow"
                onClick={onRecompute}
                busy={busy}
              />
            </FlowColumn>

            <FlowColumn>
              <DecisionNode
                title="索引内是否有记录？"
                satisfied={hasRecords}
                unsatisfiedLabel="索引为空"
                hint={hasRecords ? '可对全部或簇中心重新调 VLM 写回 labels' : undefined}
              />
              <ActionNode
                title="更新标注"
                subtitle="按当前 questions 对已入库记录重新调 VLM"
                enabled={canReannotate}
                disabledHint="索引无记录时无法更新标注"
                tone="blue"
                busy={busy}
              >
                <div className="space-y-2">
                  <select
                    value={annoMode}
                    onChange={e => setAnnoMode(e.target.value as AnnoMode)}
                    className={selectCls}
                    disabled={!canReannotate || busy}
                  >
                    <option value="full">全量：按当前 questions 整图重标</option>
                    <option value="incremental">增量：仅为缺失的键补充</option>
                  </select>
                  <label className="flex items-start gap-2 text-xs text-gray-600 dark:text-gray-300">
                    <input
                      type="checkbox"
                      checked={centersOnly}
                      onChange={e => setCentersOnly(e.target.checked)}
                      disabled={!canReannotate || busy}
                      className="mt-0.5"
                    />
                    <span>
                      仅簇中心调 VLM（可与全量/增量组合；全量 + 勾选 = 只对簇中心整图重标）
                    </span>
                  </label>
                  <button
                    type="button"
                    onClick={onReannotate}
                    disabled={!canReannotate || busy}
                    className="w-full rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    {busy ? '执行中…' : '执行更新标注'}
                  </button>
                </div>
              </ActionNode>
            </FlowColumn>
          </div>
        )}
      </div>
    </section>
  )
}
