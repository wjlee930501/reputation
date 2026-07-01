'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, fetchAPI } from '@/lib/api'
import { useDomainProfileReset } from './DomainSetupHooks'
import {
  buildFallbackDomainSetupPlan,
  parseStepsFromMessage,
  readDomainError,
  type DomainDnsStrategy,
  type DomainManagementMode,
  type DomainSetupPlan,
} from '@/lib/domain'
import type { DomainProfile, DomainSetupPanelProps } from './DomainSetupTypes'
import {
  DomainChecklist,
  DomainFeedbackBox,
  DomainModeSelectors,
  DomainRecordTable,
  ManagedDomainFields,
  type DomainFeedback,
} from './DomainSetupPrimitives'
import { DEFAULT_CNAME_TARGET, platformSubdomainUrl, statusBadge, trimmed } from './DomainSetupState'

export function DomainSetupPanel({ hospitalId, profile, onProfileChange, onHeaderRefresh }: DomainSetupPanelProps) {
  const [domainSavedValue, setDomainSavedValue] = useState('')
  const [managementMode, setManagementMode] = useState<DomainManagementMode>('HOSPITAL_MANAGED')
  const [dnsStrategy, setDnsStrategy] = useState<DomainDnsStrategy>('CNAME')
  const [registrar, setRegistrar] = useState('')
  const [dnsProvider, setDnsProvider] = useState('')
  const [purchaseNote, setPurchaseNote] = useState('')
  const [setupPlan, setSetupPlan] = useState<DomainSetupPlan | null>(null)
  const [domainSaving, setDomainSaving] = useState(false)
  const [domainVerifying, setDomainVerifying] = useState(false)
  const [domainFeedback, setDomainFeedback] = useState<DomainFeedback>(null)
  const [cnameCopied, setCnameCopied] = useState(false)

  const fetchSetupPlan = useCallback(
    () => fetchAPI<DomainSetupPlan>(`/admin/hospitals/${hospitalId}/domain/setup`),
    [hospitalId],
  )

  useDomainProfileReset({
    hospitalId,
    profile,
    setDomainSavedValue,
    setManagementMode,
    setDnsStrategy,
    setRegistrar,
    setDnsProvider,
    setPurchaseNote,
  })

  useEffect(() => {
    let cancelled = false
    async function loadSetup() {
      if (!domainSavedValue) {
        setSetupPlan(null)
        return
      }
      try {
        const plan = await fetchSetupPlan()
        if (!cancelled) setSetupPlan(plan)
      } catch (error) {
        if (error instanceof Error) {
          if (!cancelled) setSetupPlan(buildFallbackDomainSetupPlan(domainSavedValue, DEFAULT_CNAME_TARGET))
          return
        }
        throw error
      }
    }
    void loadSetup()
    return () => {
      cancelled = true
    }
  }, [domainSavedValue, fetchSetupPlan])

  const subdomainUrl = platformSubdomainUrl(profile.slug)
  const currentDomain = trimmed(profile.aeo_domain)
  const savedManagementMode = profile.domain_management_mode ?? 'HOSPITAL_MANAGED'
  const savedDnsStrategy = profile.domain_dns_strategy ?? 'CNAME'
  const hasDomainChange = currentDomain !== trimmed(domainSavedValue)
  const hasMetadataChange =
    managementMode !== savedManagementMode ||
    dnsStrategy !== savedDnsStrategy ||
    trimmed(registrar) !== trimmed(profile.domain_registrar) ||
    trimmed(dnsProvider) !== trimmed(profile.domain_dns_provider) ||
    trimmed(purchaseNote) !== trimmed(profile.domain_purchase_note)
  const hasUnsavedChange = hasDomainChange || hasMetadataChange
  const status = hasUnsavedChange ? 'unsaved' : profile.site_live ? 'live' : !domainSavedValue ? 'empty' : 'waiting'
  const badge = statusBadge(status)
  const displayPlan = useMemo(
    () => setupPlan ?? (domainSavedValue ? buildFallbackDomainSetupPlan(domainSavedValue, DEFAULT_CNAME_TARGET) : null),
    [domainSavedValue, setupPlan],
  )

  async function handleCopy(value: string) {
    try {
      await navigator.clipboard.writeText(value)
      setCnameCopied(true)
      window.setTimeout(() => setCnameCopied(false), 2000)
    } catch (error) {
      if (error instanceof Error) {
        setDomainFeedback({ tone: 'error', message: '클립보드 복사에 실패했습니다. 대상값을 직접 선택해 복사해 주세요.' })
        return
      }
      throw error
    }
  }

  async function handleSaveDomain() {
    const domain = trimmed(profile.aeo_domain)
    const resetsLive = hasDomainChange || dnsStrategy !== savedDnsStrategy
    if (!domain) {
      setDomainFeedback({ tone: 'error', message: '도메인을 입력해 주세요.' })
      return
    }
    setDomainSaving(true)
    setDomainFeedback(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/domain`, {
        method: 'PATCH',
        body: JSON.stringify({
          domain,
          management_mode: managementMode,
          dns_strategy: dnsStrategy,
          registrar: trimmed(registrar) || null,
          dns_provider: trimmed(dnsProvider) || null,
          purchase_note: trimmed(purchaseNote) || null,
        }),
      })
      setDomainSavedValue(domain)
      try {
        setSetupPlan(await fetchSetupPlan())
      } catch (error) {
        if (error instanceof Error) setSetupPlan(buildFallbackDomainSetupPlan(domain, DEFAULT_CNAME_TARGET))
        else throw error
      }
      onProfileChange({
        aeo_domain: domain,
        ...(resetsLive ? { site_live: false } : {}),
        domain_management_mode: managementMode,
        domain_dns_strategy: dnsStrategy,
        domain_registrar: trimmed(registrar) || null,
        domain_dns_provider: trimmed(dnsProvider) || null,
        domain_purchase_note: trimmed(purchaseNote) || null,
      })
      onHeaderRefresh()
      setDomainFeedback({ tone: 'success', message: '도메인 설정이 저장되었습니다. DNS 전파 후 연결 검증을 실행하세요.' })
    } catch (e: unknown) {
      const info = readDomainError(e, '도메인 저장에 실패했습니다.')
      setDomainFeedback({
        tone: 'error',
        title: info.kind === 'invalid' ? '도메인 형식 오류' : info.kind === 'conflict' ? '이미 사용 중인 도메인' : undefined,
        message: info.kind === 'conflict' ? `${info.message} 해당 병원의 연결을 먼저 해제해 주세요.` : info.message,
      })
    } finally {
      setDomainSaving(false)
    }
  }

  async function handleVerifyDomain() {
    setDomainVerifying(true)
    setDomainFeedback(null)
    try {
      const result = await fetchAPI<{ verified?: boolean; expected_cname?: string; message?: string }>(
        `/admin/hospitals/${hospitalId}/domain/verify`,
        { method: 'POST' },
      )
      if (result?.verified) {
        onProfileChange({ site_live: true })
        onHeaderRefresh()
        setDomainFeedback({ tone: 'success', message: result.message ?? '도메인 연결이 확인되어 운영 상태로 전환되었습니다.' })
      } else {
        setDomainFeedback({ tone: 'error', message: result?.message ?? 'DNS 설정이 아직 확인되지 않았습니다.' })
      }
    } catch (e: unknown) {
      const info = readDomainError(e, '도메인 검증에 실패했습니다.')
      if (info.kind === 'prerequisite' || (e instanceof ApiError && e.status === 409)) {
        const steps = info.missingSteps.length > 0 ? info.missingSteps : parseStepsFromMessage(info.message)
        setDomainFeedback({ tone: 'info', title: 'DNS 확인 완료 · 운영 전 단계 필요', message: info.message, steps })
      } else {
        setDomainFeedback({ tone: 'error', message: info.message })
      }
    } finally {
      setDomainVerifying(false)
    }
  }

  return (
    <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
      <div className="bg-gradient-to-br from-indigo-50 via-blue-50 to-white px-6 py-5 border-b border-slate-100">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-slate-900">자기 도메인 연결 <span className="text-slate-400 font-normal">(선택)</span></h3>
            <p className="text-sm text-slate-700 mt-1">기본은 아래 플랫폼 주소로 자동 공개되며, 병원 자기 도메인 연결은 선택입니다.</p>
          </div>
          <span className={`shrink-0 inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-full border ${badge.cls}`}>
            {badge.label}
          </span>
        </div>
      </div>

      <div className="px-6 py-5 space-y-5">
        {subdomainUrl && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3">
            <p className="text-sm font-semibold text-emerald-800">기본 주소 · 자동 공개</p>
            <a
              href={subdomainUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-0.5 block font-mono text-sm text-emerald-700 underline break-all"
            >
              {subdomainUrl}
            </a>
            <p className="mt-1 text-xs text-emerald-700/80">
              운영 시작 시 별도 DNS·인증서 설정 없이 이 주소로 공개됩니다. 아래 자기 도메인 연결은 선택입니다.
            </p>
          </div>
        )}

        <DomainModeSelectors
          managementMode={managementMode}
          dnsStrategy={dnsStrategy}
          onManagementModeChange={setManagementMode}
          onDnsStrategyChange={setDnsStrategy}
        />

        <div className="grid gap-3 md:grid-cols-[1fr_auto]">
          <div>
            <label htmlFor="profile-aeo-domain" className="text-sm font-semibold text-slate-800">연결 도메인</label>
            <input
              id="profile-aeo-domain"
              type="text"
              value={profile.aeo_domain ?? ''}
              onChange={(e) => onProfileChange({ aeo_domain: e.target.value })}
              placeholder={dnsStrategy === 'CNAME' ? 'ai.clinicname.co.kr' : 'clinicname.co.kr'}
              className="mt-2 w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <button
            type="button"
            onClick={handleSaveDomain}
            disabled={domainSaving || !currentDomain || !hasUnsavedChange}
            className="self-end px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {domainSaving ? '저장 중...' : '도메인 저장'}
          </button>
        </div>

        {managementMode === 'MOTIONLABS_MANAGED' && (
          <ManagedDomainFields
            registrar={registrar}
            dnsProvider={dnsProvider}
            purchaseNote={purchaseNote}
            onRegistrarChange={setRegistrar}
            onDnsProviderChange={setDnsProvider}
            onPurchaseNoteChange={setPurchaseNote}
          />
        )}

        <DomainRecordTable plan={displayPlan} copied={cnameCopied} onCopy={handleCopy} />

        {(displayPlan?.warnings.length ?? 0) > 0 && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {displayPlan?.warnings.join(' ')}
          </div>
        )}

        {displayPlan && (
          <DomainChecklist plan={displayPlan} />
        )}

        {profile.site_live && !hasUnsavedChange ? (
          <a href={`https://${currentDomain}`} target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800">
            병원 정보 허브 운영중 · {currentDomain}
          </a>
        ) : (
          <button type="button" onClick={handleVerifyDomain} disabled={domainVerifying || !domainSavedValue || hasUnsavedChange} className="w-full py-2.5 bg-emerald-600 text-white text-sm font-medium rounded-lg hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed">
            {domainVerifying ? 'DNS 확인 중...' : hasUnsavedChange ? '변경한 도메인을 먼저 저장해 주세요' : !domainSavedValue ? '도메인을 먼저 저장해 주세요' : 'DNS 확인하고 운영 시작'}
          </button>
        )}

        <DomainFeedbackBox feedback={domainFeedback} />
      </div>
    </section>
  )
}
