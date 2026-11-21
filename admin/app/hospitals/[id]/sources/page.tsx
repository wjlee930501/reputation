'use client'

import { useParams } from 'next/navigation'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { HospitalTabFrame } from '../HospitalTabFrame'
import { SourcesSection } from '../info/SourcesSection'

/** 근거 자료 탭 — 원장 인터뷰 설문·문서·블로그·자료 목록(결정 대기 2번 A draft). */
export default function HospitalSourcesPage() {
  const params = useParams<{ id: string }>()
  return (
    <HospitalTabFrame
      title={ADMIN_COPY.evidence}
      description="원장 인터뷰 설문과 병원 자료를 올리고, 처리 상태와 근거 노트를 확인합니다. 공식 채널은 병원 정보를 저장하면 자동으로 등록됩니다."
    >
      {() => <SourcesSection hospitalId={params.id} />}
    </HospitalTabFrame>
  )
}
