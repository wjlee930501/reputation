'use client'

import { useParams } from 'next/navigation'
import { useEffect, useState } from 'react'
import type { Hospital } from '@/types'
import { DomainSetupPanel } from '../DomainSetupPanel'
import type { DomainProfile } from '../DomainSetupTypes'
import { HospitalTabFrame } from '../HospitalTabFrame'
import { useHospitalHeader } from '../hospital-context'
import { OwnershipVerificationSection } from '../info/OwnershipVerificationSection'

/**
 * 도메인·검색 탭 — 자기 도메인 연결과 검색엔진 소유 확인(결정 대기 2번 A draft).
 *
 * 도메인 칸의 입력값은 이 화면이 들고 있다. 입력 중(dirty)에는 새 헤더로 덮지 않고, 도메인 패널이
 * 저장·확인 뒤 헤더를 다시 받으면 그 값으로 다시 채운다.
 */
export default function HospitalDomainPage() {
  const params = useParams<{ id: string }>()
  const hospitalId = params.id
  const { hospital, refetch } = useHospitalHeader()
  const [profile, setProfile] = useState<DomainProfile>({})
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!hospital || dirty) return
    setProfile(hospital as unknown as DomainProfile)
  }, [hospital, dirty])

  async function refreshHeader() {
    await refetch()
    setDirty(false)
  }

  return (
    <HospitalTabFrame
      title="도메인·검색"
      description="기본 주소는 자동으로 운영됩니다. 자기 도메인을 쓰는 병원만 연결하고, 검색엔진 소유 확인 값을 넣습니다."
    >
      {(header: Hospital) => (
        <>
          {header.site_built ? (
            <div id="domain-setup" className="scroll-mt-24">
              <DomainSetupPanel
                hospitalId={hospitalId}
                profile={profile}
                activationReadiness={header}
                onProfileChange={(patch) => {
                  setDirty(true)
                  setProfile((prev) => ({ ...prev, ...patch }))
                }}
                onHeaderRefresh={() => void refreshHeader()}
              />
            </div>
          ) : (
            <p id="domain-setup" className="scroll-mt-24 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
              공개 페이지 준비가 끝나면 자기 도메인을 연결할 수 있습니다. 기본 주소는 준비가 끝나면 자동으로 열립니다.
            </p>
          )}
          <OwnershipVerificationSection
            hospital={header}
            hospitalId={hospitalId}
            onSaved={() => void refetch()}
          />
        </>
      )}
    </HospitalTabFrame>
  )
}
