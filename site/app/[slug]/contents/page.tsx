import { Metadata } from 'next'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, HospitalNotFoundError, TYPE_LABELS, type ContentItem } from '@/lib/api'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ContentCard } from '../_components/ContentCard'
import { JsonLd } from '../_components/JsonLd'

interface Props {
  params: { slug: string }
}

export const revalidate = 3600

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

const PRIORITY_TYPES = ['FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE']

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name} 의료 정보 — 자주 묻는 질문, 질환 정보, 치료 안내, 원장 칼럼.`
    return {
      title: `${hospital.name} 의료 정보`,
      description,
      alternates: { canonical: `/${params.slug}/contents` },
      openGraph: {
        title: `${hospital.name} 의료 정보`,
        description,
        url: `/${params.slug}/contents`,
        type: 'website',
      },
    }
  } catch {
    return { title: '의료 정보' }
  }
}

export default async function ContentsLibraryPage({ params }: Props) {
  let hospital
  let contents
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 500),
    ])
  } catch (e) {
    if (e instanceof HospitalNotFoundError) notFound()
    throw e
  }

  const grouped = new Map<string, ContentItem[]>()
  for (const content of contents) {
    const list = grouped.get(content.content_type) ?? []
    list.push(content)
    grouped.set(content.content_type, list)
  }
  const orderedTypes = [
    ...PRIORITY_TYPES.filter((type) => grouped.has(type)),
    ...Array.from(grouped.keys()).filter((type) => !PRIORITY_TYPES.includes(type)),
  ]

  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '의료 정보' },
  ]

  const collectionJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: `${hospital.name} 의료 정보`,
    about: hospital.specialties,
    isPartOf: {
      '@type': 'WebSite',
      name: hospital.name,
      url: `${SITE_URL}/${params.slug}`,
    },
    hasPart: contents.map((content) => ({
      '@type': 'Article',
      headline: content.title,
      url: `${SITE_URL}/${params.slug}/contents/${content.id}`,
      datePublished: content.published_at || content.scheduled_date,
    })),
  }

  return (
    <>
      <JsonLd data={[collectionJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, SITE_URL)]} />
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
              <span className="clinic-section-label">의료 정보</span>
              <h1 className="clinic-library-hero-title">{hospital.name} 의료 정보</h1>
              <p className="clinic-library-hero-meta">
                <span>{hospital.specialties.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region.join(' ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <strong>{hospital.director_name} 원장</strong>
              </p>
              <p
                className="clinic-section-lede"
                style={{ marginTop: 16, maxWidth: 720, fontSize: 14 }}
              >
                진료실에서 자주 나오는 질문과 치료 전 확인하면 좋은 내용을 유형별로 모았습니다.
                개인의 상태에 따라 판단이 달라질 수 있으니 자세한 내용은 진료 상담에서 확인해 주세요.
              </p>
            </div>
          </section>

          <section className="clinic-section">
            <div className="clinic-section-inner">
              {contents.length === 0 ? (
                <div className="clinic-empty">
                  <span className="clinic-empty-title">아직 발행된 콘텐츠가 없습니다</span>
                  <p>진료 안내와 건강 정보 글을 준비하고 있습니다.</p>
                </div>
              ) : (
                <div>
                  {orderedTypes.map((type) => {
                    const items = grouped.get(type) ?? []
                    return (
                      <section key={type} className="clinic-content-group">
                        <header className="clinic-content-group-header">
                          <h2 className="clinic-content-group-heading">
                            {TYPE_LABELS[type] ?? type}
                          </h2>
                          <span className="clinic-content-group-count">{items.length}편</span>
                        </header>
                        <div className="clinic-content-grid">
                          {items.map((content) => (
                            <ContentCard
                              key={content.id}
                              content={content}
                              hospitalSlug={params.slug}
                              hospitalName={hospital.name}
                            />
                          ))}
                        </div>
                      </section>
                    )
                  })}
                </div>
              )}
            </div>
          </section>
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
