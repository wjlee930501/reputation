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

function _certElapsedMinutes(started: string | null | undefined): number {
  if (!started) return 0
  try {
    const startTime = new Date(started).getTime()
    const now = Date.now()
    return Math.floor((now - startTime) / 60000)
  } catch (error: unknown) {
    return 0
  }
}

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
  const [previewPlan, setPreviewPlan] = useState<DomainSetupPlan | null>(null)

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

  useEffect(() => {
    // DM-U6: 변경 사항이 있으면 미리보기 레코드 생성
    if (hasUnsavedChange && currentDomain) {
      // DM-U6: 전략에 맞는 미리보기 생성
      setPreviewPlan(buildFallbackDomainSetupPlan(currentDomain, DEFAULT_CNAME_TARGET))
    } else {
      setPreviewPlan(null)
    }
  }, [hasUnsavedChange, currentDomain])

  // DM-F1/F2: ISSUING 상태일 때 cert-status를 폴링하여 DONE/FAILED 전환 확인
  useEffect(() => {
    if (profile.domain_cert_job_state !== 'ISSUING') return
    
    let cancelled = false
    const checkCertStatus = async () => {
      try {
        const result = await fetchAPI<{
          cert_job_state?: string | null
          cert_job_started_at?: string | null
          cert_job_elapsed_minutes?: number | null
          certificate_ready?: boolean
          message?: string
        }>(`/admin/hospitals/${hospitalId}/domain/cert-status`)
        
        if (!cancelled && result.cert_job_state && result.cert_job_state !== profile.domain_cert_job_state) {
          onProfileChange({
            domain_cert_job_state: result.cert_job_state,
            domain_cert_job_started_at: result.cert_job_started_at ?? profile.domain_cert_job_started_at,
          })
        }
      } catch (error: unknown) {
        // 폴링 실패는 조용히 무시 (다음 폴링에서 재시도)
      }
    }
    
    // 즉시 한 번 체크
    void checkCertStatus()
    
    // 30초마다 폴링
    const interval = setInterval(() => void checkCertStatus(), 30000)
    
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [hospitalId, profile.domain_cert_job_state, profile.domain_cert_job_started_at, onProfileChange])

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
      // DM-F3: 저장은 site_live를 건드리지 않음. 기본 주소와 커스텀 도메인은 독립적.
      onProfileChange({
        aeo_domain: domain,
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

  async function handleRollbackDomain() {
    if (!confirm('커스텀 도메인 설정을 초기화하시겠습니까? 기본 주소는 유지됩니다.')) return
    setDomainSaving(true)
    setDomainFeedback(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/domain`, { method: 'DELETE' })
      onProfileChange({ 
        aeo_domain: '',
        domain_cert_job_state: null,
        domain_cert_job_started_at: null,
        domain_cert_dns_verified_at: null,
      })
      setDomainSavedValue('')
      setDomainFeedback({ tone: 'success', message: '커스텀 도메인 설정을 초기화했습니다.' })
      onHeaderRefresh()
    } catch (error: unknown) {
      if (!(error instanceof ApiError || error instanceof TypeError || error instanceof DOMException)) {
        throw error
      }
      setDomainFeedback({ tone: 'error', message: '초기화 실패. 다시 시도해 주세요.' })
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
        cert_job_state?: string | null
        cert_job_started_at?: string | null
        cert_job_elapsed_minutes?: number | null
        expected_cname?: string
        message?: string
      }>(
        `/admin/hospitals/${hospitalId}/domain/verify`,
        { method: 'POST' },
      )
      
      // DM-F4: DNS 검증 성공 = 온보딩 5단계 완료, 인증서는 시스템 후속 작업
      if (result?.dns_verified) {
        // 프로파일 업데이트: site_live + 인증서 작업 상태
        onProfileChange({ 
          site_live: true,
          domain_cert_job_state: result.cert_job_state ?? null,
          domain_cert_job_started_at: result.cert_job_started_at ?? null,
        })
        onHeaderRefresh()
        
        if (result.certificate_ready) {
          // 인증서까지 준비 완료
          setDomainFeedback({ 
            tone: 'success', 
            message: result.message ?? 'DNS 확인 완료 · HTTPS 인증서 준비 완료. 운영 상태로 전환되었습니다.' 
          })
        } else if (result.cert_job_state === 'ISSUING') {
          // 인증서 발급 진행 중 (DM-F1: 경과 시간 표시)
          const elapsed = result.cert_job_elapsed_minutes ?? 0
          setDomainFeedback({
            tone: 'success',
            title: `DNS 확인 완료 · HTTPS 인증서 발급 진행 중 (경과 ${elapsed}분)`,
            message: result.message ?? '운영 전환은 완료되었습니다. HTTPS 인증서는 일반적으로 수 분 내에 발급됩니다.',
          })
        } else if (result.cert_job_state === 'FAILED') {
          // 인증서 발급 실패
          setDomainFeedback({
            tone: 'info',
            title: 'DNS 확인 완료 · HTTPS 인증서 발급 실패',
            message: result.message ?? 'DNS 연결 상태를 다시 확인한 뒤 재시도해 주세요.',
          })
        } else {
          // DNS 확인 완료, 인증서 대기 중
          setDomainFeedback({ 
            tone: 'success', 
            message: result.message ?? 'DNS 확인 완료. 운영 전환되었으며 HTTPS 인증서는 백그라운드에서 발급됩니다.' 
          })
        }
      } else {
        // DNS 검증 실패
        setDomainFeedback({ tone: 'error', message: result?.message ?? 'DNS 설정이 아직 확인되지 않았습니다.' })
      }
    } catch (e: unknown) {
      const info = readDomainError(e, '도메인 검증에 실패했습니다.')
      
      // DM-F2 UI: 409는 여러 원인 가능. 인증서 작업 진행 중일 때만 특별 처리.
      if (e instanceof ApiError && e.status === 409) {
        const isCertJob = info.message.includes('인증서 발급이 이미 진행 중')
        if (isCertJob) {
          setDomainFeedback({ 
            tone: 'info', 
            title: 'HTTPS 인증서 발급 진행 중',
            message: info.message 
          })
        } else if (info.kind === 'prerequisite') {
          const steps = info.missingSteps.length > 0 ? info.missingSteps : parseStepsFromMessage(info.message)
          setDomainFeedback({ tone: 'info', title: '운영 전 단계 필요', message: info.message, steps })
        } else {
          // 기타 409는 원본 메시지 표시
          setDomainFeedback({ tone: 'error', message: info.message })
        }
      } else if (info.kind === 'prerequisite') {
        const steps = info.missingSteps.length > 0 ? info.missingSteps : parseStepsFromMessage(info.message)
        setDomainFeedback({ tone: 'info', title: '운영 전 단계 필요', message: info.message, steps })
      } else {
        setDomainFeedback({ tone: 'error', message: info.message })
      }
    } finally {
      setDomainVerifying(false)
    }
  }

  async function handleActivatePlatform() {
    // DM-F3: 기본 주소 활성화는 커스텀 도메인과 독립적으로 가능
    if (platformActivating || activationMissing.length > 0) return
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
                  disabled={platformActivating || activationMissing.length > 0}
                  className="min-h-11 w-full rounded-lg bg-emerald-700 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {platformActivating
                    ? '운영 전환 중...'
                    : activationMissing.length > 0
                      ? '선행 단계를 먼저 완료해 주세요'
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
            {dnsStrategy === 'CNAME' && profile.website_url && (() => {
              try {
                const hostname = new URL(profile.website_url).hostname.replace(/^www\./, '')
                return (
                  <p className="mt-1 text-xs text-slate-500">
                    홈페이지 도메인 {hostname}을 사용하는 경우, 서브도메인을 ai.{hostname}로 설정할 수 있습니다.
                  </p>
                )
              } catch (error: unknown) {
                return null
              }
            })()}
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

        {previewPlan && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
            <p className="text-xs font-semibold text-amber-800">저장 전 DNS 레코드 미리보기</p>
            <div className="mt-2">
              <DomainRecordTable plan={previewPlan} copied={false} onCopy={() => {}} />
            </div>
          </div>
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

        {/* DM-F2: 인증서 준비 완료 여부로 버튼/링크 결정. site_live만으로는 판단하지 않음. */}
        {/* DM-F3: 롤백 버튼은 도메인이 저장되어 있으면 항상 표시 (ISSUING/FAILED도 탈출 가능) */}
        {profile.domain_cert_job_state === 'DONE' && customDomainUrl ? (
          <>
            <a href={customDomainUrl} target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800">
              병원 정보 허브 운영 중 · {currentDomain}
            </a>
            <button
              type="button"
              onClick={handleRollbackDomain}
              disabled={domainSaving}
              className="min-h-9 w-full rounded-lg border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              커스텀 도메인 초기화
            </button>
          </>
        ) : domainSavedValue ? (
          <>
            <button 
              type="button" 
              onClick={handleVerifyDomain} 
              disabled={domainVerifying || !domainSavedValue || hasUnsavedChange || profile.domain_cert_job_state === 'ISSUING'} 
              className="min-h-11 w-full rounded-lg bg-emerald-600 px-3 py-2.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {domainVerifying 
                ? 'DNS 확인 중...' 
                : profile.domain_cert_job_state === 'ISSUING'
                  ? `HTTPS 인증서 발급 진행 중 (경과 ${_certElapsedMinutes(profile.domain_cert_job_started_at)}분)`
                  : hasUnsavedChange 
                    ? '변경한 도메인을 먼저 저장해 주세요' 
                    : !domainSavedValue 
                      ? '도메인을 먼저 저장해 주세요' 
                      : profile.domain_cert_job_state === 'FAILED'
                        ? 'DNS 확인 및 인증서 재시도'
                        : 'DNS 확인하고 운영 시작'}
            </button>
            <button
              type="button"
              onClick={handleRollbackDomain}
              disabled={domainSaving}
              className="min-h-9 w-full rounded-lg border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              커스텀 도메인 초기화
            </button>
          </>
        ) : (
          <button 
            type="button" 
            onClick={handleVerifyDomain} 
            disabled={domainVerifying || !domainSavedValue || hasUnsavedChange || profile.domain_cert_job_state === 'ISSUING'} 
            className="min-h-11 w-full rounded-lg bg-emerald-600 px-3 py-2.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {domainVerifying 
              ? 'DNS 확인 중...' 
              : profile.domain_cert_job_state === 'ISSUING'
                ? `HTTPS 인증서 발급 진행 중 (경과 ${_certElapsedMinutes(profile.domain_cert_job_started_at)}분)`
                : hasUnsavedChange 
                  ? '변경한 도메인을 먼저 저장해 주세요' 
                  : !domainSavedValue 
                    ? '도메인을 먼저 저장해 주세요' 
                    : profile.domain_cert_job_state === 'FAILED'
                      ? 'DNS 확인 및 인증서 재시도'
                      : 'DNS 확인하고 운영 시작'}
          </button>
        )}

        <DomainFeedbackBox feedback={domainFeedback} />
      </div>
    </section>
  )
}
