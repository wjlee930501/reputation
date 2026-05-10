import { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, type ContentItem } from '@/lib/api'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ContentCard } from '../_components/ContentCard'
import { DoctorIntro } from '../_components/DoctorIntro'
import { JsonLd } from '../_components/JsonLd'

interface Props {
  params: { slug: string }
}

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'
const COLUMN_FIRST = ['COLUMN', 'FAQ', 'DISEASE', 'TREATMENT', 'HEALTH', 'LOCAL', 'NOTICE']

function sortByCuratorRelevance(a: ContentItem, b: ContentItem): number {
  const ai = COLUMN_FIRST.indexOf(a.content_type)
  const bi = COLUMN_FIRST.indexOf(b.content_type)
  return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi)
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.director_name} 원장 — ${hospital.name}의 의료 콘텐츠 큐레이터. 진료 분야와 약력, 검수한 의료 정보 모음.`
    return {
      title: `${hospital.director_name} 원장 | ${hospital.name}`,
      description,
      alternates: { canonical: `/${params.slug}/doctor` },
      openGraph: {
        title: `${hospital.director_name} 원장 | ${hospital.name}`,
        description,
        url: `/${params.slug}/doctor`,
        type: 'profile',
        images: hospital.director_photo_url ? [{ url: hospital.director_photo_url }] : [],
      },
    }
  } catch {
    return { title: '의료진' }
  }
}

export default async function DoctorPage({ params }: Props) {
  let hospital
  let contents
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 60),
    ])
  } catch {
    notFound()
  }

  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '의료진' },
  ]

  const curatedContents = [...contents].sort(sortByCuratorRelevance).slice(0, 6)

  const physicianJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Physician',
    '@id': `${SITE_URL}/${params.slug}/doctor`,
    name: hospital.director_name,
    description: hospital.director_career || undefined,
    image: hospital.director_photo_url || undefined,
    medicalSpecialty: hospital.specialties,
    worksFor: {
      '@type': 'MedicalClinic',
      name: hospital.name,
      url: `${SITE_URL}/${params.slug}`,
      address: { '@type': 'PostalAddress', streetAddress: hospital.address, addressCountry: 'KR' },
      telephone: hospital.phone,
    },
    url: `${SITE_URL}/${params.slug}/doctor`,
  }

  return (
    <>
      <JsonLd data={[physicianJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, SITE_URL)]} />
      <div className="clinic-shell">
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalSlug={params.slug}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
        <main>
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <span className="clinic-section-eyebrow">Medical Staff · Content Curator</span>
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
                이 콘텐츠 허브의 모든 의료 정보는 {hospital.director_name} 원장의 검수를 거친 자료입니다.
                약력과 진료 영역, 그리고 직접 큐레이션한 콘텐츠를 모았습니다.
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
                  <span className="clinic-section-eyebrow">Curated Content</span>
                  <h2 className="clinic-section-heading">큐레이터가 검수한 콘텐츠</h2>
                  <p className="clinic-section-lede">
                    원장 칼럼·자주 묻는 질문·질환 가이드를 우선 정렬했습니다.
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
                      전체 콘텐츠 {contents.length}편 보기
                    </Link>
                  </div>
                )}
              </div>
            </section>
          )}
        </main>
        <ClinicFooter
          hospitalName={hospital.name}
          address={hospital.address}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
      </div>
    </>
  )
}
