import { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, HospitalNotFoundError, type ContentSummary } from '@/lib/api'
import {
  absoluteClinicImageUrl,
  buildClinicThemeStyle,
  selectClinicDirectorImage,
} from '@/lib/clinic-theme'
import { physicianNodeId, resolveClinicPhysicians } from '@/lib/clinic-physicians'
import { buildPhysicianNode } from '@/lib/schema'
import { canonicalBase, canonicalHospitalUrl } from '@/lib/site-url'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ContentCard } from '../_components/ContentCard'
import { DoctorIntro } from '../_components/DoctorIntro'
import { JsonLd } from '../_components/JsonLd'

interface Props {
  params: Promise<{ slug: string }>
}

// Next.js는 `revalidate`를 정적으로 파싱해야 해서 lib/fetch-policy.ts의
// REVALIDATE_SECONDS를 import해 쓸 수 없다(import된 식별자는 빌드가 거부한다) — 값은
// 그 상수와 반드시 같게 유지한다.
export const revalidate = 1800

const COLUMN_FIRST = ['COLUMN', 'FAQ', 'DISEASE', 'TREATMENT', 'HEALTH', 'LOCAL', 'NOTICE']

function sortByCuratorRelevance(a: ContentSummary, b: ContentSummary): number {
  const ai = COLUMN_FIRST.indexOf(a.content_type)
  const bi = COLUMN_FIRST.indexOf(b.content_type)
  return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi)
}

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.director_name} 원장 — ${hospital.name}의 진료 분야, 약력, 환자 안내 글 모음.`
    const canonicalUrl = canonicalHospitalUrl(hospital, params.slug, 'doctor')
    return {
      title: `${hospital.director_name} 원장 | ${hospital.name}`,
      description,
      alternates: { canonical: canonicalUrl },
      openGraph: {
        title: `${hospital.director_name} 원장 | ${hospital.name}`,
        description,
        url: canonicalUrl,
        type: 'profile',
        images: (() => {
          const photo = absoluteClinicImageUrl(
            selectClinicDirectorImage(hospital),
            canonicalBase(hospital, params.slug),
          )
          return photo ? [{ url: photo }] : []
        })(),
      },
    }
  } catch {
    return { title: '의료진' }
  }
}

export default async function DoctorPage({ params: paramsPromise }: Props) {
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

  const hospitalRootUrl = canonicalHospitalUrl(hospital, params.slug)
  const breadcrumbItems = [
    { label: '홈', href: hospitalRootUrl },
    { label: '의료진' },
  ]

  const curatedContents = [...contents].sort(sortByCuratorRelevance).slice(0, 6)

  const base = canonicalBase(hospital, params.slug)
  const physicians = resolveClinicPhysicians(hospital)

  const physicianSameAs = [
    hospital.wikidata_qid ? `https://www.wikidata.org/wiki/${hospital.wikidata_qid}` : null,
  ].filter((value): value is string => Boolean(value))

  // 승인·검수된 진료 철학 서사를 약력 뒤에 덧붙여 Physician description을 보강한다.
  const publicAbout = hospital.public_about?.trim() || null

  // 의료진마다 독립 Physician 노드를 낸다. 병원은 인라인 복제가 아니라 허브가 소유한
  // `#clinic` @id를 참조한다 — 같은 엔티티를 두 벌로 주장하면 병합이 깨진다.
  const physicianJsonLd = physicians.map((physician, index) => ({
    '@context': 'https://schema.org',
    ...buildPhysicianNode({
      hospital,
      physician,
      hospitalRootUrl,
      nodeId: physicianNodeId(hospitalRootUrl, physician, index),
      imageUrl: absoluteClinicImageUrl(physician.photoUrl, base),
      // 병원 단위 서사는 대표 의료진 한 명에게만 붙인다 — 모든 카드에 같은 문단을
      // 복제하면 답변 엔진이 서로 다른 사람의 설명으로 인용한다.
      extraDescription: index === 0 ? publicAbout : null,
      sameAs: index === 0 ? physicianSameAs : [],
    }),
    worksFor: { '@id': `${hospitalRootUrl}#clinic` },
    mainEntityOfPage: `${hospitalRootUrl}/doctor`,
  }))

  const physicianNames = physicians.map((physician) => physician.name).join(' · ')

  return (
    <>
      <JsonLd data={[...physicianJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, hospitalRootUrl)]} />
      <div className="clinic-shell clinic-shell--editorial" style={buildClinicThemeStyle(hospital)}>
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalRootUrl={hospitalRootUrl}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
          logoUrl={hospital.logo_url}
          currentSection="doctor"
          googleMapsUrl={hospital.google_maps_url}
        />
        <main id="main-content">
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <h1 className="clinic-library-hero-title">{hospital.name} 의료진</h1>
              <p className="clinic-library-hero-meta">
                <strong>{physicianNames || hospital.director_name}</strong>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.specialties.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region.join(' ')}</span>
              </p>
            </div>
          </section>

          <DoctorIntro
            physicians={physicians}
            specialties={hospital.specialties}
            region={hospital.region}
            contentCount={contents.length}
            priorityPhoto
          />

          {curatedContents.length > 0 && (
            <section className="clinic-section">
              <div className="clinic-section-inner">
                <header className="clinic-section-header">
                  <h2 className="clinic-section-heading">원장이 전하는 진료 이야기</h2>
                  <p className="clinic-section-lede">
                    원장 칼럼·자주 묻는 질문·질환 정보를 우선 모았습니다.
                  </p>
                </header>
                <div className="clinic-content-grid">
                  {curatedContents.map((c) => (
                    <ContentCard
                      key={c.id}
                      content={c}
                      hospitalRootUrl={hospitalRootUrl}
                      hospitalName={hospital.name}
                    />
                  ))}
                </div>
                {contents.length > curatedContents.length && (
                  <div className="clinic-section-actions">
                    <Link
                      href={`${hospitalRootUrl}/contents`}
                      className="clinic-btn clinic-btn-secondary"
                    >
                      의료 정보 전체 보기
                    </Link>
                  </div>
                )}
              </div>
            </section>
          )}
        </main>
        <ClinicFooter
          hospitalName={hospital.name}
          directorName={hospital.director_name}
          address={hospital.address}
          addressDetail={hospital.address_detail}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
      </div>
    </>
  )
}
