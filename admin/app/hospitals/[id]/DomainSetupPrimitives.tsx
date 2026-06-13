'use client'

import {
  domainManagementModeLabel,
  domainStrategyLabel,
  type DomainDnsStrategy,
  type DomainManagementMode,
  type DomainSetupPlan,
} from '@/lib/domain'

export type DomainFeedback = {
  tone: 'success' | 'error' | 'info'
  title?: string
  message: string
  steps?: string[]
} | null

interface ModeSelectorProps<T extends string> {
  title: string
  value: T
  options: readonly T[]
  labelFor: (value: T) => string
  activeClass: string
  onChange: (value: T) => void
}

function ModeSelector<T extends string>({
  title,
  value,
  options,
  labelFor,
  activeClass,
  onChange,
}: ModeSelectorProps<T>) {
  return (
    <div>
      <p className="mb-2 text-sm font-semibold text-slate-800">{title}</p>
      <div className="grid grid-cols-2 gap-2">
        {options.map((option) => (
          <button
            type="button"
            key={option}
            onClick={() => onChange(option)}
            className={`rounded-lg border px-3 py-2 text-sm font-medium ${
              value === option ? activeClass : 'border-slate-200 bg-white text-slate-600'
            }`}
          >
            {labelFor(option)}
          </button>
        ))}
      </div>
    </div>
  )
}

export function DomainModeSelectors({
  managementMode,
  dnsStrategy,
  onManagementModeChange,
  onDnsStrategyChange,
}: {
  managementMode: DomainManagementMode
  dnsStrategy: DomainDnsStrategy
  onManagementModeChange: (value: DomainManagementMode) => void
  onDnsStrategyChange: (value: DomainDnsStrategy) => void
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <ModeSelector
        title="관리 방식"
        value={managementMode}
        options={['HOSPITAL_MANAGED', 'MOTIONLABS_MANAGED'] as const}
        labelFor={domainManagementModeLabel}
        activeClass="border-blue-500 bg-blue-50 text-blue-700"
        onChange={onManagementModeChange}
      />
      <ModeSelector
        title="DNS 전략"
        value={dnsStrategy}
        options={['CNAME', 'APEX_ADDRESS'] as const}
        labelFor={domainStrategyLabel}
        activeClass="border-emerald-500 bg-emerald-50 text-emerald-700"
        onChange={onDnsStrategyChange}
      />
    </div>
  )
}

export function ManagedDomainFields({
  registrar,
  dnsProvider,
  purchaseNote,
  onRegistrarChange,
  onDnsProviderChange,
  onPurchaseNoteChange,
}: {
  registrar: string
  dnsProvider: string
  purchaseNote: string
  onRegistrarChange: (value: string) => void
  onDnsProviderChange: (value: string) => void
  onPurchaseNoteChange: (value: string) => void
}) {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      <input value={registrar} onChange={(e) => onRegistrarChange(e.target.value)} placeholder="등록기관 예: Gabia" className="px-3 py-2 border border-slate-300 rounded-lg text-sm" />
      <input value={dnsProvider} onChange={(e) => onDnsProviderChange(e.target.value)} placeholder="DNS 제공자 예: Route53" className="px-3 py-2 border border-slate-300 rounded-lg text-sm" />
      <input value={purchaseNote} onChange={(e) => onPurchaseNoteChange(e.target.value)} placeholder="갱신/구매 메모" className="px-3 py-2 border border-slate-300 rounded-lg text-sm" />
    </div>
  )
}

export function DomainRecordTable({
  plan,
  copied,
  onCopy,
}: {
  plan: DomainSetupPlan | null
  copied: boolean
  onCopy: (value: string) => void
}) {
  return (
    <>
      <div className="rounded-lg border border-slate-200 bg-slate-50 overflow-hidden">
        {plan ? (
          <table className="w-full text-xs">
            <tbody className="divide-y divide-slate-200">
              {plan.records.map((record) => (
                <tr key={`${record.type}-${record.name}-${record.value}`}>
                  <td className="px-3 py-2 w-24 font-mono text-slate-800">{record.type}</td>
                  <td className="px-3 py-2 font-mono text-slate-800">{record.name}</td>
                  <td className="px-3 py-2">
                    <button type="button" onClick={() => onCopy(record.value)} className="font-mono text-blue-700 underline">
                      {record.value}
                    </button>
                  </td>
                  <td className="px-3 py-2 text-slate-500">TTL {record.ttl}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="px-3 py-2.5 text-xs text-slate-500">도메인을 저장하면 DNS 레코드가 표시됩니다.</p>
        )}
      </div>
      {copied && <p className="text-[11px] text-blue-600">대상값을 복사했습니다.</p>}
    </>
  )
}

export function DomainChecklist({ plan }: { plan: DomainSetupPlan }) {
  return (
    <ol className="grid gap-2 md:grid-cols-4">
      {plan.checklist.map((item) => (
        <li key={item.key} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
          <p className="text-xs font-semibold text-slate-800">{item.label}</p>
          <p className="mt-1 text-[11px] text-slate-500">{item.description}</p>
        </li>
      ))}
    </ol>
  )
}

export function DomainFeedbackBox({ feedback }: { feedback: DomainFeedback }) {
  if (!feedback) return null
  const color =
    feedback.tone === 'success'
      ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
      : feedback.tone === 'error'
        ? 'bg-red-50 border-red-200 text-red-700'
        : 'bg-blue-50 border-blue-200 text-blue-700'
  return (
    <div className={`rounded-lg px-3 py-2 text-sm border ${color}`}>
      {feedback.title && <p className="font-semibold">{feedback.title}</p>}
      <p className={feedback.title ? 'mt-0.5 text-xs' : ''}>{feedback.message}</p>
      {(feedback.steps?.length ?? 0) > 0 && (
        <ul className="mt-2 space-y-1.5">
          {(feedback.steps ?? []).map((step) => <li key={step} className="text-xs">{step}</li>)}
        </ul>
      )}
    </div>
  )
}
