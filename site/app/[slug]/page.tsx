import { Metadata } from 'next'
import { notFound } from 'next/navigation'

import { fetchHospital, fetchContents, HospitalNotFoundError } from '@/lib/api'
import { selectShareImage } from '@/lib/clinic-share-image'
import { getApiBase } from '@/lib/config'
import { REVALIDATE_SECONDS } from '@/lib/fetch-policy'
import { regionSentenceLabel } from '@/lib/clinic-region'
import { canonicalHospitalUrl } from '@/lib/site-url'

import { HospitalHome } from './_layouts/HospitalHome'

interface Props {
  params: Promise<{ slug: string }>
}

// Next.js는 `revalidate`를 정적으로 파싱해야 해서 lib/fetch-policy.ts의
// REVALIDATE_SECONDS를 import해 쓸 수 없다(import된 식별자는 빌드가 거부한다) — 값은
// 그 상수와 반드시 같게 유지한다.
export const revalidate = 1800

export async function generateStaticParams() {
  try {
    const apiBase = getApiBase(false)
    if (!apiBase) return []
    const res = await fetch(`${apiBase}/hospitals`, { next: { revalidate: REVALIDATE_SECONDS } })
    if (!res.ok) return []
    const hospitals = (await res.json()) as Array<{ slug: string }>
    return hospitals.map((h) => ({ slug: h.slug }))
  } catch {
    return []
  }
}

// 진료과목은 병원 프로파일(specialties[])에서만 가져온다 — 하드코딩된 진료과 문구는
// 다른 진료과 병원에서 허위 의료 표시가 되므로 절대 금지.
function buildSpecialtyLabel(specialties: string[] | null | undefined): string {
  return (specialties || []).filter(Boolean).join('·')
}

function buildHospitalTitle(hospital: Awaited<ReturnType<typeof fetchHospital>>): string {
  const specialtyLabel = buildSpecialtyLabel(hospital.specialties)
  return specialtyLabel
    ? `${hospital.name} ${specialtyLabel} 진료 안내`
    : `${hospital.name} 진료 안내`
}

function buildHospitalDescription(hospital: Awaited<ReturnType<typeof fetchHospital>>): string {
  const region = regionSentenceLabel(hospital.region)
  const specialties = hospital.specialties?.join(', ') || ''
  const locality = [region, specialties].filter(Boolean).join(' · ')
  return locality
    ? `${hospital.name} (${locality}) 진료 안내 — 의료진, 진료 영역, 진료시간, 오시는 길과 의료 정보를 확인할 수 있습니다.`
    : `${hospital.name} 진료 안내 — 의료진, 진료 영역, 진료시간, 오시는 길과 의료 정보를 확인할 수 있습니다.`
}

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const hospital = await fetchHospital(params.slug)
    const title = buildHospitalTitle(hospital)
    const description = buildHospitalDescription(hospital)
    // 백엔드 자산 경로는 상대 URL일 수 있다 — 크롤러는 site origin 기준으로 잘못 해석하므로
    // 절대 URL로 변환해야 OG/구조화 데이터 이미지가 깨지지 않는다.
    // 섹션 설정의 공유 이미지·설명은 OG/트위터 메타만 바꾼다(검색 설명·JSON-LD `image`는 그대로, I1).
    const share = hospital.site_config?.share
    const ogImage = selectShareImage(hospital)
    const shareDescription = share?.description ?? description
    // 커스텀 도메인이 연결된 병원은 해당 도메인이 canonical origin이 된다 (site-url.ts 정책).
    const canonicalUrl = canonicalHospitalUrl(hospital, params.slug)
    return {
      title,
      description,
      alternates: { canonical: canonicalUrl },
      openGraph: {
        title,
        description: shareDescription,
        url: canonicalUrl,
        type: 'website',
        images: [{ url: ogImage.url, alt: ogImage.alt }],
      },
      twitter: {
        card: 'summary_large_image',
        title,
        description: shareDescription,
        images: [ogImage.url],
      },
    }
  } catch {
    return { title: '진료 안내' }
  }
}

export default async function HospitalHubPage({ params: paramsPromise }: Props) {
  const params = await paramsPromise
  let hospital
  let contents
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 60),
    ])
  } catch (e) {
    if (e instanceof HospitalNotFoundError) notFound()
    throw e
  }

  return <HospitalHome hospital={hospital} contents={contents} slug={params.slug} />
}
