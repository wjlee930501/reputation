import { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, resolveAssetUrl, HospitalNotFoundError, type ContentSummary } from '@/lib/api'
import { buildPhysicianCredentials } from '@/lib/schema'
import { canonicalBase } from '@/lib/site-url'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ContentCard } from '../_components/ContentCard'
import { DoctorIntro } from '../_components/DoctorIntro'
import { JsonLd } from '../_components/JsonLd'

interface Props {
  params: Promise<{ slug: string }>
}

export const revalidate = 3600

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
    const canonicalUrl = `${canonicalBase(hospital)}/${params.slug}/doctor`
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
          const photo = resolveAssetUrl(hospital.director_photo_url)
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

  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '의료진' },
  ]

  const curatedContents = [...contents].sort(sortByCuratorRelevance).slice(0, 6)

  // 자격·학회·전문영역 신뢰축은 랜딩 중첩 Physician과 동일 빌더를 공유한다.
  const physicianCredentials = buildPhysicianCredentials(hospital)

  const base = canonicalBase(hospital)

  const physicianSameAs = [
    hospital.wikidata_qid ? `https://www.wikidata.org/wiki/${hospital.wikidata_qid}` : null,
  ].filter((value): value is string => Boolean(value))

  // 약력(director_career)에 승인·검수된 진료 철학 서사를 덧붙여 Physician description을 보강한다.
  const publicAbout = hospital.public_about?.trim() || null
  const physicianDescription =
    [hospital.director_career?.trim() || null, publicAbout].filter(Boolean).join(' ') || undefined

  const physicianJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Physician',
    '@id': `${base}/${params.slug}/doctor#physician`,
    name: hospital.director_name,
    jobTitle: '원장',
    description: physicianDescription,
    image: resolveAssetUrl(hospital.director_photo_url) || undefined,
    ...physicianCredentials,
    sameAs: physicianSameAs.length > 0 ? physicianSameAs : undefined,
    worksFor: {
      '@type': 'MedicalClinic',
      '@id': `${base}/${params.slug}#clinic`,
      name: hospital.name,
      url: `${base}/${params.slug}`,
      address: { '@type': 'PostalAddress', streetAddress: hospital.address, addressCountry: 'KR' },
      telephone: hospital.phone,
    },
    url: `${base}/${params.slug}/doctor`,
    mainEntityOfPage: `${base}/${params.slug}/doctor`,
  }

  return (
    <>
      <JsonLd data={[physicianJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, base)]} />
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
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <span className="clinic-section-label">의료진</span>
              <h1 className="clinic-library-hero-title">{hospital.name} 의료진</h1>
              <p className="clinic-library-hero-meta">
                <strong>{hospital.director_name} 원장</strong>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.specialties.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region.join(' ')}</span>
              </p>
              <p
                className="clinic-section-lede"
                style={{ marginTop: 16, maxWidth: 720, fontSize: 14 }}
              >
                {hospital.director_name} 원장의 약력과 진료 영역, 환자 안내 글을 모았습니다.
              </p>
            </div>
          </section>

          <DoctorIntro
            directorName={hospital.director_name}
            directorCareer={hospital.director_career}
            directorPhotoUrl={hospital.director_photo_url}
            specialties={hospital.specialties}
            region={hospital.region}
            contentCount={contents.length}
          />

          {curatedContents.length > 0 && (
            <section className="clinic-section">
              <div className="clinic-section-inner">
                <header className="clinic-section-header">
                  <span className="clinic-section-label">원장 노트</span>
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
                      hospitalSlug={params.slug}
                      hospitalName={hospital.name}
                    />
                  ))}
                </div>
                {contents.length > curatedContents.length && (
                  <div style={{ marginTop: 24, textAlign: 'right' }}>
                    <Link
                      href={`/${params.slug}/contents`}
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
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
      </div>
    </>
  )
}
