import { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, type ContentItem } from '@/lib/api'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ContentCard } from '../_components/ContentCard'
import { JsonLd } from '../_components/JsonLd'
import { pickIconForTreatment } from '../_components/MedicalIcons'

interface Props {
  params: { slug: string }
}

export const revalidate = 3600

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

function findRelatedContents(treatmentName: string, contents: ContentItem[]): ContentItem[] {
  const stem = treatmentName.replace(/[수술치료시술검사진료]/g, '').trim()
  if (!stem) return []
  const lowerStem = stem.toLowerCase()
  return contents
    .filter((c) => {
      const haystack = `${c.title} ${c.meta_description ?? ''} ${c.faq_question ?? ''}`.toLowerCase()
      return haystack.includes(lowerStem)
    })
    .slice(0, 3)
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name}의 진료 영역 — ${(hospital.treatments || []).map((t) => t.name).join(', ')}. 환자가 확인하면 좋은 진료 안내를 함께 제공합니다.`
    return {
      title: `진료 영역 | ${hospital.name}`,
      description,
      alternates: { canonical: `/${params.slug}/treatments` },
      openGraph: {
        title: `진료 영역 | ${hospital.name}`,
        description,
        url: `/${params.slug}/treatments`,
        type: 'website',
      },
    }
  } catch {
    return { title: '진료 영역' }
  }
}

export default async function TreatmentsPage({ params }: Props) {
  let hospital
  let contents
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 200),
    ])
  } catch {
    notFound()
  }

  const treatments = hospital.treatments || []
  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '진료 영역' },
  ]

  const treatmentsWithRelated = treatments.map((t) => ({
    treatment: t,
    related: findRelatedContents(t.name, contents),
    visual: pickIconForTreatment(t.name),
  }))

  const itemListJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: `${hospital.name} 진료 영역`,
    itemListElement: treatments.map((t, idx) => ({
      '@type': 'ListItem',
      position: idx + 1,
      item: {
        '@type': 'MedicalProcedure',
        name: t.name,
        performer: { '@type': 'MedicalClinic', name: hospital.name },
      },
    })),
  }

  return (
    <>
      <JsonLd data={[itemListJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, SITE_URL)]} />
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
              <span className="clinic-section-eyebrow">진료 영역</span>
              <h1 className="clinic-library-hero-title">{hospital.name} 진료 영역</h1>
              <p className="clinic-library-hero-meta">
                <strong>{treatments.length}개 진료 영역</strong>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.specialties.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region.join(' ')}</span>
              </p>
              <p
                className="clinic-section-lede"
                style={{ marginTop: 16, maxWidth: 720, fontSize: 14 }}
              >
                각 진료 영역에서 다루는 항목과 환자가 자주 묻는 내용을 함께 정리했습니다.
                정확한 진단과 치료 계획은 진료 상담을 통해 확인해 주세요.
              </p>
            </div>
          </section>

          {treatmentsWithRelated.length === 0 ? (
            <section className="clinic-section">
              <div className="clinic-section-inner">
                <div className="clinic-empty">
                  <span className="clinic-empty-title">아직 등록된 진료 영역이 없습니다</span>
                  <p>진료 영역 정보를 준비하고 있습니다.</p>
                </div>
              </div>
            </section>
          ) : (
            <section className="clinic-section">
              <div className="clinic-section-inner">
                <ul className="clinic-treatment-grid" aria-label="진료 영역 목록">
                  {treatmentsWithRelated.map(({ treatment, visual }) => {
                    const { Icon, hue } = visual
                    return (
                      <li key={treatment.name} className="clinic-treatment-card">
                        <span className={`clinic-treatment-card-icon hue-${hue}`} aria-hidden="true">
                          <Icon />
                        </span>
                        <span className="clinic-treatment-card-name">{treatment.name}</span>
                      </li>
                    )
                  })}
                </ul>
              </div>
            </section>
          )}

          {treatmentsWithRelated.some((t) => t.related.length > 0) && (
            <section className="clinic-section clinic-section--alt">
              <div className="clinic-section-inner">
                <header className="clinic-section-header">
                  <span className="clinic-section-eyebrow">관련 글</span>
                  <h2 className="clinic-section-heading">진료 영역별 블로그 글</h2>
                  <p className="clinic-section-lede">
                    진료 전 궁금해할 만한 질문과 안내를 진료 영역별로 모았습니다.
                  </p>
                </header>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 48 }}>
                  {treatmentsWithRelated
                    .filter((t) => t.related.length > 0)
                    .map(({ treatment, related, visual }) => {
                      const { Icon, hue } = visual
                      return (
                        <div key={`group-${treatment.name}`}>
                          <div className="clinic-content-group-header">
                            <h3
                              className="clinic-content-group-heading"
                              style={{ display: 'flex', alignItems: 'center', gap: 12 }}
                            >
                              <span
                                className={`clinic-treatment-card-icon hue-${hue}`}
                                style={{ width: 36, height: 36 }}
                                aria-hidden="true"
                              >
                                <Icon />
                              </span>
                              {treatment.name}
                            </h3>
                            <span className="clinic-content-group-count">{related.length}편</span>
                          </div>
                          <div className="clinic-content-grid">
                            {related.map((content) => (
                              <ContentCard
                                key={content.id}
                                content={content}
                                hospitalSlug={params.slug}
                                hospitalName={hospital.name}
                              />
                            ))}
                          </div>
                        </div>
                      )
                    })}
                </div>

                <div style={{ marginTop: 32, textAlign: 'right' }}>
                  <Link
                    href={`/${params.slug}/contents`}
                    className="clinic-btn clinic-btn-secondary"
                  >
                    블로그 글 전체 보기
                  </Link>
                </div>
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
