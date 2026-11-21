'use client'

import { useParams } from 'next/navigation'
import { useEffect, useState } from 'react'
import { fetchPhysicians, seedPhysicians } from '@/lib/physicians'
import type { Physician } from '@/types'
import { HospitalTabFrame } from '../HospitalTabFrame'
import { BrandSection } from '../info/BrandSection'
import { PhotosSection } from '../info/PhotosSection'

/** 공개 페이지 탭 — 레이아웃·페이지 문구·로고·색·첫 화면과 사진·사용 권리(결정 대기 2번 A draft). */
export default function HospitalPublicPagePage() {
  const params = useParams<{ id: string }>()
  const hospitalId = params.id
  // 사진 줄이 「어느 의료진이 이 사진을 쓰는지」 말하려면 의료진 목록이 필요하다 — 읽기만 한다.
  const [physicians, setPhysicians] = useState<Physician[]>([])

  useEffect(() => {
    let cancelled = false
    void fetchPhysicians(hospitalId)
      .then((rows) => {
        if (!cancelled) setPhysicians(seedPhysicians(rows))
      })
      .catch(() => {
        // 의료진 표시는 보조 정보다 — 못 읽어도 사진 관리는 그대로 한다.
      })
    return () => { cancelled = true }
  }, [hospitalId])

  return (
    <HospitalTabFrame
      title="공개 페이지"
      description="공개 페이지의 틀·문구·로고·색·첫 화면과 사진을 관리합니다. 각 칸은 그 칸의 저장 버튼으로 저장합니다."
    >
      {(hospital, refetch) => (
        <>
          <BrandSection hospital={hospital} hospitalId={hospitalId} onSaved={() => void refetch()} />
          <PhotosSection
            hospitalId={hospitalId}
            hospitalName={hospital.name}
            hospitalSlug={hospital.slug}
            heroImageUrl={hospital.hero_image_url ?? null}
            physicians={physicians}
            onHeroChanged={() => void refetch()}
          />
        </>
      )}
    </HospitalTabFrame>
  )
}
