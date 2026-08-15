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
import { isPlatformAddressBrowsable, missingActivationPrerequisites } from '@/lib/hospital-activation'
import { safeOperatorError } from '@/lib/operations-journey'
import { customDomainLiveUrl } from '@/lib/domain-live-links'

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
  const [platformActivating, setPlatformActivating] = useState(false)
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
  const activationMissing = missingActivationPrerequisites(profile)
  const status = hasUnsavedChange
    ? 'unsaved'
    : profile.site_live
      ? 'live'
      : domainSavedValue
        ? 'waiting'
        : activationMissing.length === 0
          ? 'ready'
          : 'empty'
  const badge = statusBadge(status)
  const platformAddressBrowsable = isPlatformAddressBrowsable(profile)
  const customDomainUrl = customDomainLiveUrl({
    site_live: profile.site_live,
    aeo_domain: currentDomain,
    hasUnsavedChange,
  })
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
      const result = await fetchAPI<{
        verified?: boolean
        dns_verified?: boolean
        certificate_ready?: boolean
        certificate_phase?: string | null
        expected_cname?: string
        message?: string
      }>(
        `/admin/hospitals/${hospitalId}/domain/verify`,
        { method: 'POST' },
      )
      if (result?.verified) {
        onProfileChange({ site_live: true })
        onHeaderRefresh()
        setDomainFeedback({ tone: 'success', message: result.message ?? '도메인 연결이 확인되어 운영 상태로 전환되었습니다.' })
      } else if (result?.dns_verified && !result?.certificate_ready) {
        setDomainFeedback({
          tone: 'info',
          title: result.certificate_phase === 'FAILED' ? 'DNS 확인 완료 · 인증서 점검 필요' : 'DNS 확인 완료 · HTTPS 준비 중',
          message: result?.message ?? '인증서를 준비하고 있습니다. 잠시 후 다시 연결 검증을 실행해 주세요.',
        })
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

  async function handleActivatePlatform() {
    if (platformActivating || activationMissing.length > 0 || currentDomain || domainSavedValue) return
    setPlatformActivating(true)
    setDomainFeedback(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/activate`, { method: 'PATCH' })
      onProfileChange({ site_live: true, status: 'ACTIVE' })
      onHeaderRefresh()
      setDomainFeedback({ tone: 'success', message: '기본 플랫폼 주소로 운영을 시작했습니다.' })
    } catch (error: unknown) {
      if (!(error instanceof ApiError || error instanceof TypeError || error instanceof DOMException)) {
        throw error
      }
      setDomainFeedback({
        tone: 'error',
        message: safeOperatorError('onboarding', '공개 운영 시작을 다시 누르고, 계속 실패하면 운영 센터에서 도메인 작업을 확인하세요.'),
      })
    } finally {
      setPlatformActivating(false)
    }
  }

  return (
    <section id="domain-setup" className="scroll-mt-20 overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="border-b border-slate-100 bg-blue-50 px-4 py-5 sm:px-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-slate-900">자기 도메인 연결 <span className="text-slate-400 font-normal">(선택)</span></h3>
            <p className="text-sm text-slate-700 mt-1">기본 플랫폼 주소는 선행 단계 완료 후 직접 활성화하며, 병원 자기 도메인 연결은 선택입니다.</p>
          </div>
          <span className={`shrink-0 inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-full border ${badge.cls}`}>
            {badge.label}
          </span>
        </div>
      </div>

      <div className="px-6 py-5 space-y-5">
        {subdomainUrl && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3">
            <p className="text-sm font-semibold text-emerald-800">
              기본 주소 · {profile.site_live ? '운영 중' : '명시적 활성화 필요'}
            </p>
            {platformAddressBrowsable ? (
              <a
                href={subdomainUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-0.5 block break-all font-mono text-sm text-emerald-700 underline"
              >
                {subdomainUrl}
              </a>
            ) : (
              <p className="mt-0.5 break-all font-mono text-sm text-emerald-700" aria-label="활성화 전 기본 주소">
                {subdomainUrl}
              </p>
            )}
            {profile.site_live ? (
              <p className="mt-1 text-xs text-emerald-700/80">이 주소로 현재 공개 중입니다. 아래 자기 도메인 연결은 선택입니다.</p>
            ) : (
              <div className="mt-2 space-y-2">
                <p className="text-xs text-emerald-700/80">
                  별도 DNS·인증서 없이 사용할 수 있지만, 운영 전환은 담당자가 직접 실행해야 합니다.
                </p>
                {activationMissing.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {activationMissing.map((item) => (
                      <a
                        key={item.key}
                        href={`/hospitals/${hospitalId}/${item.hrefSuffix}`}
                        className="rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-800 hover:bg-amber-100"
                      >
                        필요: {item.label}
                      </a>
                    ))}
                  </div>
                )}
                <button
                  type="button"
                  onClick={handleActivatePlatform}
                  disabled={platformActivating || activationMissing.length > 0 || Boolean(currentDomain || domainSavedValue)}
                  className="min-h-11 w-full rounded-lg bg-emerald-700 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {platformActivating
                    ? '운영 전환 중...'
                    : activationMissing.length > 0
                      ? '선행 단계를 먼저 완료해 주세요'
                      : currentDomain || domainSavedValue
                        ? '커스텀 도메인 설정을 먼저 정리해 주세요'
                        : '기본 주소로 운영 시작'}
                </button>
              </div>
            )}
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
            className="min-h-11 self-end rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
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

        {customDomainUrl ? (
          <a href={customDomainUrl} target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800">
            병원 정보 허브 운영 중 · {currentDomain}
          </a>
        ) : (
          <button type="button" onClick={handleVerifyDomain} disabled={domainVerifying || !domainSavedValue || hasUnsavedChange} className="min-h-11 w-full rounded-lg bg-emerald-600 px-3 py-2.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50">
            {domainVerifying ? 'DNS 확인 중...' : hasUnsavedChange ? '변경한 도메인을 먼저 저장해 주세요' : !domainSavedValue ? '도메인을 먼저 저장해 주세요' : 'DNS 확인하고 운영 시작'}
          </button>
        )}

        <DomainFeedbackBox feedback={domainFeedback} />
      </div>
    </section>
  )
}
