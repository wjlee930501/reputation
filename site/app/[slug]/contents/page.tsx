import { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { Suspense } from 'react'

import { fetchContents, fetchHospital, HospitalNotFoundError } from '@/lib/api'
import { buildClinicThemeStyle } from '@/lib/clinic-theme'
import { buildFaqPageJsonLd } from '@/lib/schema'
import { canonicalHospitalUrl } from '@/lib/site-url'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { JsonLd } from '../_components/JsonLd'
import { ContentsFeed } from './_components/ContentsFeed'
import { ContentsFeedView } from './_components/ContentsFeedView'

interface Props {
  params: Promise<{ slug: string }>
}

// Next.js는 `revalidate`를 정적으로 파싱해야 해서 lib/fetch-policy.ts의
// REVALIDATE_SECONDS를 import해 쓸 수 없다(import된 식별자는 빌드가 거부한다) — 값은
// 그 상수와 반드시 같게 유지한다.
export const revalidate = 1800

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name} 의료 정보 — 자주 묻는 질문, 질환 정보, 치료 안내, 원장 칼럼.`
    const canonicalUrl = canonicalHospitalUrl(hospital, params.slug, 'contents')
    return {
      title: `${hospital.name} 의료 정보`,
      description,
      alternates: { canonical: canonicalUrl },
      openGraph: {
        title: `${hospital.name} 의료 정보`,
        description,
        url: canonicalUrl,
        type: 'website',
      },
    }
  } catch {
    return { title: '의료 정보' }
  }
}

export default async function ContentsLibraryPage({ params: paramsPromise }: Props) {
  const params = await paramsPromise
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

  const hospitalRootUrl = canonicalHospitalUrl(hospital, params.slug)
  const breadcrumbItems = [
    { label: '홈', href: hospitalRootUrl },
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
      url: hospitalRootUrl,
    },
    hasPart: contents.map((content) => ({
      '@type': 'Article',
      headline: content.title,
      url: `${hospitalRootUrl}/contents/${content.id}`,
      datePublished: content.published_at || content.scheduled_date,
    })),
  }

  const faqJsonLd = buildFaqPageJsonLd(contents, hospitalRootUrl)
  const pageJsonLd = [
    collectionJsonLd,
    buildBreadcrumbJsonLd(breadcrumbItems, hospitalRootUrl),
    ...(faqJsonLd ? [faqJsonLd] : []),
  ]

  return (
    <>
      <JsonLd data={pageJsonLd} />
      <div className="clinic-shell clinic-shell--editorial" style={buildClinicThemeStyle(hospital)}>
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalRootUrl={hospitalRootUrl}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
          logoUrl={hospital.logo_url}
          currentSection="contents"
          googleMapsUrl={hospital.google_maps_url}
        />
        <main id="main-content">
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <h1 className="clinic-library-hero-title">{hospital.name} 의료 정보</h1>
              <p className="clinic-library-hero-meta">
                <span>{hospital.specialties.join(' · ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <span>{hospital.region.join(' ')}</span>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <strong>{hospital.director_name} 원장</strong>
              </p>
              <p className="clinic-library-hero-note">
                진료실에서 자주 나오는 질문과 치료 전 확인하면 좋은 내용을 모았습니다.
                개인의 상태에 따라 판단이 달라질 수 있으니 자세한 내용은 진료 상담에서 확인해 주세요.
              </p>
            </div>
          </section>

          <section className="clinic-section clinic-section--tight">
            <div className="clinic-section-inner">
              {/* ?type= 필터는 클라이언트에서 읽는다(ContentsFeed) — 이 서버 컴포넌트가
                  searchParams를 읽지 않아야 페이지가 쿼리와 무관하게 ISR로 정적 생성된다.
                  Suspense 폴백은 필터 없음(activeType=null) 상태와 동일한 마크업이라
                  기본 진입(?type= 없음)에서는 하이드레이션 전후로 화면이 바뀌지 않는다. */}
              <Suspense
                fallback={
                  <ContentsFeedView
                    contents={contents}
                    hospitalRootUrl={hospitalRootUrl}
                    directorName={hospital.director_name}
                    activeType={null}
                  />
                }
              >
                <ContentsFeed
                  contents={contents}
                  hospitalRootUrl={hospitalRootUrl}
                  directorName={hospital.director_name}
                />
              </Suspense>
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
