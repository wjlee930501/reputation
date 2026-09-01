import { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'

import { fetchContents, fetchHospital, resolveAssetUrl, HospitalNotFoundError, type ContentSummary } from '@/lib/api'
import { getApiBase } from '@/lib/config'
import { buildClinicThemeStyle } from '@/lib/clinic-theme'
import { countLabel } from '@/lib/clinic-counters'
import { REVALIDATE_SECONDS } from '@/lib/fetch-policy'
import { canonicalHospitalUrl } from '@/lib/site-url'
import { buildTreatmentEmptyStatePaths } from '@/lib/treatment-empty-state'
import {
  buildTreatmentSlug,
  findTreatmentBySlug,
  inferPillarTreatment,
  normalizeTreatmentSlug,
} from '@/lib/treatment-slug'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../../_components/Breadcrumb'
import { ClinicFooter } from '../../_components/ClinicFooter'
import { ClinicHeader } from '../../_components/ClinicHeader'
import { ContentCard } from '../../_components/ContentCard'
import { JsonLd } from '../../_components/JsonLd'

interface Props {
  params: Promise<{ slug: string; treatmentSlug: string }>
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

    const params: Array<{ slug: string; treatmentSlug: string }> = []
    for (const h of hospitals) {
      try {
        const detail = await fetchHospital(h.slug)
        for (const treatment of detail.treatments || []) {
          const treatmentSlug = buildTreatmentSlug(treatment.name)
          if (treatmentSlug) params.push({ slug: h.slug, treatmentSlug })
        }
      } catch {
        // hospital fetch 실패는 무시 — 다른 병원은 빌드 계속.
      }
    }
    return params
  } catch {
    return []
  }
}

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const hospital = await fetchHospital(params.slug)
    const treatment = findTreatmentBySlug(hospital.treatments || [], params.treatmentSlug)
    if (!treatment) return { title: '진료 영역' }
    const treatmentSlug = normalizeTreatmentSlug(params.treatmentSlug)
    const region = hospital.region?.join(' ') ?? ''
    const description = `${hospital.name} ${treatment.name} 진료 안내 — 환자가 자주 묻는 질문과 진료 단계, 회복 정보를 ${region} 의료진이 정리합니다.`
    const canonicalUrl = canonicalHospitalUrl(
      hospital,
      params.slug,
      `treatments/${treatmentSlug}`,
    )
    return {
      title: `${treatment.name} | ${hospital.name}`,
      description,
      alternates: {
        canonical: canonicalUrl,
      },
      openGraph: {
        title: `${treatment.name} | ${hospital.name}`,
        description,
        url: canonicalUrl,
        type: 'website',
        images: (() => {
          const photo = resolveAssetUrl(hospital.director_photo_url)
          return photo ? [{ url: photo }] : undefined
        })(),
      },
      twitter: {
        card: 'summary_large_image',
        title: `${treatment.name} | ${hospital.name}`,
        description,
        images: (() => {
          const photo = resolveAssetUrl(hospital.director_photo_url)
          return photo ? [photo] : undefined
        })(),
      },
    }
  } catch {
    return { title: '진료 영역' }
  }
}

export default async function TreatmentPillarPage({ params: paramsPromise }: Props) {
  const params = await paramsPromise
  let hospital
  let contents: ContentSummary[]
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 500),
    ])
  } catch (e) {
    if (e instanceof HospitalNotFoundError) notFound()
    throw e
  }

  const treatments = hospital.treatments || []
  const treatment = findTreatmentBySlug(treatments, params.treatmentSlug)
  if (!treatment) notFound()

  const canonicalTreatmentSlug = buildTreatmentSlug(treatment.name)
  const treatmentName = treatment.name
  const lowerName = treatmentName.toLowerCase()
  const relatedContents = contents.filter((content) => {
    const inferred = inferPillarTreatment(treatments, content)
    if (inferred && buildTreatmentSlug(inferred.name) === canonicalTreatmentSlug) return true
    const haystack = `${content.title ?? ''} ${content.meta_description ?? ''} ${content.faq_question ?? ''}`.toLowerCase()
    return haystack.includes(lowerName)
  })

  const hospitalRootUrl = canonicalHospitalUrl(hospital, params.slug)
  const breadcrumbItems = [
    { label: '홈', href: hospitalRootUrl },
    { label: '진료 영역', href: `${hospitalRootUrl}/treatments` },
    { label: treatmentName },
  ]

  const pageUrl = `${hospitalRootUrl}/treatments/${canonicalTreatmentSlug}`

  // 관련 글이 아직 없을 때 이 페이지가 빈 문서로 끝나지 않게 할 경로들 (P-A-2).
  const emptyStatePaths = buildTreatmentEmptyStatePaths({
    treatments,
    currentTreatmentName: treatmentName,
    contents,
  })

  const collectionJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: `${hospital.name} ${treatmentName}`,
    url: pageUrl,
    isPartOf: {
      '@type': 'MedicalClinic',
      '@id': `${hospitalRootUrl}#clinic`,
      name: hospital.name,
      url: hospitalRootUrl,
    },
    about: {
      '@type': 'MedicalProcedure',
      name: treatmentName,
      description: treatment.description || undefined,
      performer: {
        '@type': 'MedicalClinic',
        '@id': `${hospitalRootUrl}#clinic`,
        name: hospital.name,
      },
    },
    mainEntity: {
      '@type': 'ItemList',
      itemListElement: relatedContents.map((content, idx) => ({
        '@type': 'ListItem',
        position: idx + 1,
        url: `${hospitalRootUrl}/contents/${content.id}`,
        name: content.title,
      })),
    },
  }

  return (
    <>
      <JsonLd data={[collectionJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, hospitalRootUrl)]} />
      <div className="clinic-shell clinic-shell--editorial" style={buildClinicThemeStyle(hospital)}>
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalRootUrl={hospitalRootUrl}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
          logoUrl={hospital.logo_url}
          currentSection="treatments"
          googleMapsUrl={hospital.google_maps_url}
        />
        <main id="main-content">
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <span className="clinic-section-label">진료 영역</span>
              <h1 className="clinic-library-hero-title">{treatmentName}</h1>
              <p className="clinic-library-hero-meta">
                <strong>{hospital.name}</strong>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.specialties?.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region?.join(' ')}</span>
              </p>
              {treatment.description && (
                <p className="clinic-section-lede" style={{ marginTop: 16, maxWidth: 720, fontSize: 14 }}>
                  {treatment.description}
                </p>
              )}
            </div>
          </section>

          <section className="clinic-section">
            <div className="clinic-section-inner">
              {relatedContents.length === 0 ? (
                <div className="clinic-treatment-empty">
                  <header className="clinic-section-head">
                    <h2 className="clinic-section-title">
                      {treatmentName} 안내 글은 준비 중입니다
                    </h2>
                    <p className="clinic-section-note">
                      이 영역의 환자 안내 글은 아직 발행되지 않았습니다. 진료 범위와 방문
                      방법은 아래에서 바로 확인할 수 있고, 개인별 판단은 진료 상담에서
                      확인합니다.
                    </p>
                  </header>

                  <div className="clinic-treatment-empty-actions">
                    <a className="clinic-btn clinic-btn-cta" href={`tel:${hospital.phone}`}>
                      전화 상담 {hospital.phone}
                    </a>
                    <Link className="clinic-btn clinic-btn-secondary" href={`${hospitalRootUrl}/visit`}>
                      진료시간·오시는 길
                    </Link>
                  </div>

                  {emptyStatePaths.siblings.length > 0 ? (
                    <div className="clinic-treatment-empty-block">
                      <h3 className="clinic-treatment-empty-heading">이 병원의 다른 진료 영역</h3>
                      <div className="clinic-treatment-empty-links">
                        {emptyStatePaths.siblings.map((sibling) => (
                          <Link
                            key={sibling.name}
                            href={`${hospitalRootUrl}/treatments/${buildTreatmentSlug(sibling.name)}`}
                          >
                            {sibling.name}
                          </Link>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {emptyStatePaths.recentContents.length > 0 ? (
                    <div className="clinic-treatment-empty-block">
                      <h3 className="clinic-treatment-empty-heading">
                        이미 발행된 의료 정보 {countLabel(contents.length, '편')}
                      </h3>
                      <div className="clinic-content-grid">
                        {emptyStatePaths.recentContents.map((content) => (
                          <ContentCard
                            key={content.id}
                            content={content}
                            hospitalRootUrl={hospitalRootUrl}
                            hospitalName={hospital.name}
                          />
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <Link
                    href={`${hospitalRootUrl}/contents`}
                    className="clinic-btn clinic-btn-secondary clinic-treatment-empty-all"
                  >
                    의료 정보 전체 보기
                  </Link>
                </div>
              ) : (
                <>
                  <header className="clinic-section-header">
                    <span className="clinic-section-label">
                      관련 콘텐츠 {countLabel(relatedContents.length, '편')}
                    </span>
                    <h2 className="clinic-section-heading">{treatmentName} 진료 안내 글 모음</h2>
                    <p className="clinic-section-lede">
                      {treatmentName}와 관련해 환자가 자주 묻는 질문, 질환 정보, 진료 단계를 모았습니다.
                    </p>
                  </header>
                  <div className="clinic-content-grid">
                    {relatedContents.map((content) => (
                      <ContentCard
                        key={content.id}
                        content={content}
                        hospitalRootUrl={hospitalRootUrl}
                        hospitalName={hospital.name}
                      />
                    ))}
                  </div>
                  <div style={{ marginTop: 32, textAlign: 'right' }}>
                    <Link
                      href={`${hospitalRootUrl}/contents`}
                      className="clinic-btn clinic-btn-secondary"
                    >
                      의료 정보 전체 보기
                    </Link>
                  </div>
                </>
              )}
            </div>
          </section>
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
