import { Metadata } from 'next'
import { notFound } from 'next/navigation'

import { fetchHospital, fetchContents, resolveAssetUrl, HospitalNotFoundError } from '@/lib/api'
import { buildOpeningHoursSpec } from '@/lib/business-hours'
import { getApiBase } from '@/lib/config'
import { canonicalBase } from '@/lib/site-url'

import { AnswerClusters } from './_components/AnswerClusters'
import { buildBreadcrumbJsonLd } from './_components/Breadcrumb'
import { CareFlow } from './_components/CareFlow'
import { CarePrinciples } from './_components/CarePrinciples'
import { ClinicFooter } from './_components/ClinicFooter'
import { ClinicGallery } from './_components/ClinicGallery'
import { ClinicHeader } from './_components/ClinicHeader'
import { ClinicHero } from './_components/ClinicHero'
import { ContactCard } from './_components/ContactCard'
import { DoctorIntro } from './_components/DoctorIntro'
import { FeaturedContent } from './_components/FeaturedContent'
import { HospitalFacts } from './_components/HospitalFacts'
import { JsonLd } from './_components/JsonLd'
import { TreatmentGrid } from './_components/TreatmentGrid'

interface Props {
  params: Promise<{ slug: string }>
}

export const revalidate = 3600

export async function generateStaticParams() {
  try {
    const apiBase = getApiBase(false)
    if (!apiBase) return []
    const res = await fetch(`${apiBase}/hospitals`, { next: { revalidate: 3600 } })
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
  const region = hospital.region?.join(' ') || ''
  const specialties = hospital.specialties?.join(', ') || ''
  const locality = [region, specialties].filter(Boolean).join(' · ')
  return locality
    ? `${hospital.name} (${locality}) 진료 안내 — 의료진, 진료 영역, 진료시간, 오시는 길과 건강 정보를 확인할 수 있습니다.`
    : `${hospital.name} 진료 안내 — 의료진, 진료 영역, 진료시간, 오시는 길과 건강 정보를 확인할 수 있습니다.`
}

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const hospital = await fetchHospital(params.slug)
    const title = buildHospitalTitle(hospital)
    const description = buildHospitalDescription(hospital)
    // 백엔드 자산 경로는 상대 URL일 수 있다 — 크롤러는 site origin 기준으로 잘못 해석하므로
    // 절대 URL로 변환해야 OG/구조화 데이터 이미지가 깨지지 않는다.
    const ogImage =
      resolveAssetUrl(hospital.director_photo_url) ?? '/landing/reputation-clinic-trust-interior.png'
    // 커스텀 도메인이 연결된 병원은 해당 도메인이 canonical origin이 된다 (site-url.ts 정책).
    const canonicalUrl = `${canonicalBase(hospital)}/${params.slug}`
    return {
      title,
      description,
      alternates: { canonical: canonicalUrl },
      openGraph: {
        title,
        description,
        url: canonicalUrl,
        type: 'website',
        images: ogImage
          ? [
              {
                url: ogImage,
                alt: hospital.director_name ? `${hospital.director_name} 원장` : hospital.name,
              },
            ]
          : undefined,
      },
      twitter: {
        card: 'summary_large_image',
        title,
        description,
        images: ogImage ? [ogImage] : undefined,
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

  const sameAs = [
    hospital.website_url,
    hospital.blog_url,
    hospital.kakao_channel_url,
    hospital.google_business_profile_url,
    hospital.google_maps_url,
    hospital.naver_place_url,
    hospital.wikidata_qid ? `https://www.wikidata.org/wiki/${hospital.wikidata_qid}` : null,
    hospital.naver_place_id ? `https://map.naver.com/p/entry/place/${hospital.naver_place_id}` : null,
    hospital.kakao_place_id ? `https://place.map.kakao.com/${hospital.kakao_place_id}` : null,
  ].filter((value): value is string => Boolean(value))

  const base = canonicalBase(hospital)

  const clinicJsonLd = {
    '@context': 'https://schema.org',
    '@type': ['MedicalClinic', 'LocalBusiness'],
    '@id': `${base}/${params.slug}#clinic`,
    name: hospital.name,
    url: `${base}/${params.slug}`,
    image: resolveAssetUrl(hospital.director_photo_url) ?? undefined,
    sameAs,
    address: {
      '@type': 'PostalAddress',
      streetAddress: hospital.address,
      addressCountry: 'KR',
    },
    telephone: hospital.phone,
    medicalSpecialty: hospital.specialties,
    openingHoursSpecification: buildOpeningHoursSpec(hospital.business_hours),
    hasMap: hospital.google_maps_url || undefined,
    geo:
      hospital.latitude && hospital.longitude
        ? {
            '@type': 'GeoCoordinates',
            latitude: hospital.latitude,
            longitude: hospital.longitude,
          }
        : undefined,
    physician: {
      '@type': 'Physician',
      '@id': `${base}/${params.slug}/doctor#physician`,
      name: hospital.director_name,
      jobTitle: '원장',
      description: hospital.director_career,
      image: resolveAssetUrl(hospital.director_photo_url) ?? undefined,
      url: `${base}/${params.slug}/doctor`,
    },
    availableService: (hospital.treatments || []).map((treatment) => ({
      '@type': 'MedicalProcedure',
      name: treatment.name,
    })),
  }

  const breadcrumbJsonLd = buildBreadcrumbJsonLd(
    [{ label: '홈', href: `/${params.slug}` }],
    base,
  )

  const externalChannels = [
    { url: hospital.blog_url, label: '병원 블로그' },
    { url: hospital.kakao_channel_url, label: '카카오톡 상담' },
    { url: hospital.naver_place_url, label: '네이버 플레이스' },
    { url: hospital.google_business_profile_url, label: 'Google 비즈니스 프로필' },
  ]

  // AI-readable Hospital Facts 패널용 공식 엔티티 채널 — schema/llms.txt와 동일한 값.
  const factLinks = [
    { url: hospital.website_url, label: '공식 홈페이지' },
    { url: hospital.naver_place_url, label: '네이버 플레이스' },
    { url: hospital.google_business_profile_url, label: 'Google 비즈니스 프로필' },
    { url: hospital.kakao_channel_url, label: '카카오톡 채널' },
  ]

  return (
    <>
      <JsonLd data={[clinicJsonLd, breadcrumbJsonLd]} />
      <div className="clinic-shell">
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalSlug={params.slug}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
        <main id="main-content">
          {/* PRD §7.2 Public Webblog IA 순서:
              Hero → Hospital Facts → Answer Clusters → Featured →
              Care Principles → Treatments → Care Flow → Doctor → Gallery → Contact.
              병원 엔티티 사실과 대표 질문을 최신글 피드보다 먼저 노출한다. */}
          <ClinicHero
            hospitalName={hospital.name}
            hospitalSlug={params.slug}
            region={hospital.region}
            specialties={hospital.specialties}
            phone={hospital.phone}
            directorName={hospital.director_name}
            directorPhotoUrl={hospital.director_photo_url}
            contentCount={contents.length}
            treatmentCount={(hospital.treatments || []).length}
            address={hospital.address}
            businessHours={hospital.business_hours}
            treatments={hospital.treatments || []}
            photos={hospital.photos ?? []}
            boardCertifications={hospital.director_credentials?.board_certifications ?? null}
          />

          <HospitalFacts
            hospitalName={hospital.name}
            address={hospital.address}
            phone={hospital.phone}
            businessHours={hospital.business_hours}
            region={hospital.region}
            specialties={hospital.specialties}
            directorName={hospital.director_name}
            hiraOrgId={hospital.hira_org_id}
            links={factLinks}
          />

          <AnswerClusters
            contents={contents}
            hospitalSlug={params.slug}
            treatments={hospital.treatments || []}
            region={hospital.region}
            specialties={hospital.specialties}
          />

          <FeaturedContent
            contents={contents}
            hospitalSlug={params.slug}
            hospitalName={hospital.name}
            directorName={hospital.director_name}
          />

          <CarePrinciples hospitalSlug={params.slug} specialties={hospital.specialties} />

          <TreatmentGrid treatments={hospital.treatments} />

          <CareFlow hospitalSlug={params.slug} hospitalName={hospital.name} />

          <DoctorIntro
            directorName={hospital.director_name}
            directorCareer={hospital.director_career}
            directorPhotoUrl={hospital.director_photo_url}
            specialties={hospital.specialties}
            region={hospital.region}
            contentCount={contents.length}
          />

          <ClinicGallery photos={hospital.photos ?? []} />

          <ContactCard
            address={hospital.address}
            phone={hospital.phone}
            businessHours={hospital.business_hours}
            googleMapsUrl={hospital.google_maps_url}
            links={externalChannels}
            hospitalName={hospital.name}
            websiteUrl={hospital.website_url}
          />
        </main>
        <ClinicFooter
          hospitalName={hospital.name}
          directorName={hospital.director_name}
          address={hospital.address}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
      </div>
    </>
  )
}
