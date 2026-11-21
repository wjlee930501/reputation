import { Fragment, type ReactNode } from 'react'

import type { ContentSummary, Hospital } from '@/lib/api'
import { buildOpeningHoursSpec, visitHoursHref } from '@/lib/business-hours'
import {
  clinicComposition,
  clinicContentDensity,
  clinicGalleryPolicy,
  resolveClinicAccessMode,
  resolveClinicMediaMode,
} from '@/lib/clinic-design'
import { physicianNodeId, resolveClinicPhysicians } from '@/lib/clinic-physicians'
import { buildPostalAddress } from '@/lib/clinic-schema'
import {
  absoluteClinicImageUrl,
  buildClinicThemeStyle,
  selectClinicDirectorImage,
  selectClinicHeroImage,
} from '@/lib/clinic-theme'
import { resolveClinicLayout } from '@/lib/clinic-layout'
import {
  buildFaqPageJsonLdFromEntries,
  buildPhysicianNode,
  selectClusterContents,
} from '@/lib/schema'
import {
  buildHomeSectionPlan,
  naverBookingUrl,
  physicianPhotoLook,
  resolveCareFlow,
  resolveHeroBlocks,
  resolveHomeFaqEntries,
  type SiteSectionId,
} from '@/lib/clinic-site-config'
import { canonicalBase, canonicalHospitalUrl } from '@/lib/site-url'

import { AnswerClusters } from '../_components/AnswerClusters'
import { buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { CareFlow } from '../_components/CareFlow'
import { CarePrinciples } from '../_components/CarePrinciples'
import { ClinicFaq } from '../_components/ClinicFaq'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicGallery } from '../_components/ClinicGallery'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ClinicHero } from '../_components/ClinicHero'
import { ContactCard } from '../_components/ContactCard'
import { DoctorIntro } from '../_components/DoctorIntro'
import { FeaturedContent } from '../_components/FeaturedContent'
import { HospitalFacts } from '../_components/HospitalFacts'
import { JsonLd } from '../_components/JsonLd'
import { TreatmentGrid } from '../_components/TreatmentGrid'
import { HomeLayoutA } from './HomeLayoutA'
import { HomeLayoutC } from './HomeLayoutC'
import type { ClinicHomeModel } from './model'
import { clinicLayoutShellClass } from './shell'

/**
 * 병원 홈 화면 — `/[slug]`(공개)와 `/preview/[token]`(섹션 설정 초안 미리보기)이 같은 코드로 그린다.
 * 데이터·구조화 데이터는 여기서 한 번만 만들고, 레이아웃은 같은 값을 그리기만 한다.
 */
export function HospitalHome({
  hospital,
  contents,
  slug,
}: {
  hospital: Hospital
  contents: ContentSummary[]
  slug: string
}) {
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

  const hospitalRootUrl = canonicalHospitalUrl(hospital, slug)

  // 승인된 운영 기준에서 의료광고 검수를 통과한 about 서사 (없으면 null) — description/slogan에 사용.
  const publicAbout = hospital.public_about?.trim() || null

  // 화면(DoctorIntro)·구조화 데이터·llms.txt가 같은 목록을 본다.
  const canonicalOrigin = canonicalBase(hospital, slug)
  const physicians = resolveClinicPhysicians(hospital)
  const employeeJsonLd = physicians.map((physician, index) =>
    buildPhysicianNode({
      hospital,
      physician,
      hospitalRootUrl,
      nodeId: physicianNodeId(hospitalRootUrl, physician, index),
      imageUrl: absoluteClinicImageUrl(physician.photoUrl, canonicalOrigin),
    }),
  )

  const clinicJsonLd = {
    '@context': 'https://schema.org',
    '@type': ['MedicalClinic', 'LocalBusiness'],
    '@id': `${hospitalRootUrl}#clinic`,
    name: hospital.name,
    url: hospitalRootUrl,
    // Organization.logo — 답변 엔진과 검색이 병원 엔티티를 식별할 때 표준으로 읽는
    // 필드다. 승인된 로고가 있는데 비워 두면 이 제품의 핵심 지표(AI 답변 언급)에서
    // 그만큼 손해다. 값은 업로드된 자산의 절대 주소로 정규화돼 있다.
    logo: hospital.logo_url ?? undefined,
    image: selectClinicDirectorImage(hospital) ?? undefined,
    description: publicAbout ?? undefined,
    slogan: publicAbout ?? undefined,
    sameAs,
    address: buildPostalAddress(hospital.address, hospital.address_detail),
    telephone: hospital.phone,
    medicalSpecialty: hospital.specialties,
    openingHoursSpecification: buildOpeningHoursSpec(hospital.business_hours),
    hasMap: hospital.google_maps_url || undefined,
    geo:
      hospital.latitude !== null && hospital.longitude !== null
        ? {
            '@type': 'GeoCoordinates',
            latitude: hospital.latitude,
            longitude: hospital.longitude,
          }
        : undefined,
    // 의료진은 한 명이 아닐 수 있다. 자격·학회·전문영역 신뢰축을 최우선순위
    // URL(랜딩)에도 사람마다 실어 /doctor에만 의존하지 않게 한다.
    employee: employeeJsonLd.length > 0 ? employeeJsonLd : undefined,
    availableService: (hospital.treatments || []).map((treatment) => ({
      '@type': 'MedicalProcedure',
      name: treatment.name,
    })),
  }

  const breadcrumbJsonLd = buildBreadcrumbJsonLd(
    [{ label: '홈', href: hospitalRootUrl }],
    hospitalRootUrl,
  )

  // 마케터가 고른 레이아웃(A/B/C)과 공개된 섹션 설정(레이아웃 편집기). 설정이 없으면 지금 화면 그대로다
  // (`clinic-site-config.ts`). 설정은 표현만 바꾼다 — 위의 병원 JSON-LD는 설정을 읽지 않는다(I1).
  const layout = resolveClinicLayout(hospital.site_layout)
  const siteConfig = hospital.site_config
  const plan = buildHomeSectionPlan(layout, siteConfig)

  // 발행된 FAQ를 병원 단위 FAQPage로 집계 (개별 FAQ 페이지의 FAQPage와 별개).
  // 화면 FAQ 섹션과 같은 배열 하나를 쓴다 — 구조화 데이터에만 있는 Q&A를 만들지 않는다. FAQ 섹션을 끄면
  // 빈 배열이라 FAQPage도 없다(화면 = FAQPage, I2).
  const faqEntries = resolveHomeFaqEntries(contents, hospitalRootUrl, siteConfig, plan)
  const faqJsonLd = buildFaqPageJsonLdFromEntries(faqEntries, hospitalRootUrl)

  // 승인된 Q&A의 주인은 FAQ 섹션 하나다. 질문 목록(AnswerClusters)은 FAQ를
  // 우선순위 맨 앞에 놓고 뽑기 때문에, 같은 항목을 두 섹션이 나란히 반복해서
  // 보여주게 된다. FAQ 섹션이 질문과 답변을 모두 싣는 쪽이므로 그쪽에 남기고,
  // 질문 목록은 아직 답이 붙지 않은 나머지 읽을 거리를 안내한다.
  // 같은 질문 문장의 글도 뺀다 — 중복이라 FAQ 섹션에서 빠진 글이 질문 목록에 다시 나오지 않게.
  const clusterContents = selectClusterContents(contents, faqEntries)
  const pageJsonLd = [clinicJsonLd, breadcrumbJsonLd, ...(faqJsonLd ? [faqJsonLd] : [])]

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

  const contentDensity = clinicContentDensity(contents.length)
  const composition = clinicComposition(contentDensity)
  const heroPhotoUrl = selectClinicHeroImage(hospital)
  const accessMode = resolveClinicAccessMode({
    configuredMode: hospital.site_access_mode,
    specialties: hospital.specialties,
    businessHours: hospital.business_hours,
    boardCertifications: hospital.director_credentials?.board_certifications ?? [],
  })
  const mediaMode = resolveClinicMediaMode({
    hasVerifiedFacilityPhoto: Boolean(heroPhotoUrl) && hospital.hero_media_kind !== 'BRAND_GRAPHIC',
    hasBrandGraphic: Boolean(heroPhotoUrl) && hospital.hero_media_kind === 'BRAND_GRAPHIC',
    hasLogo: Boolean(hospital.logo_url),
  })

  // 레이아웃이 비어 있으면 레이아웃 도입 전 화면이다 — 배포만으로 운영 중인 병원의 화면이 바뀌지
  // 않는다. 데이터·구조화 데이터는 위에서 한 번만 만들고, 레이아웃은 같은 값을 섹션 계획의 순서대로
  // 그리기만 한다(FAQ 항목 = FAQPage, 질문 목록 = FAQ를 뺀 글).
  const careFlow = resolveCareFlow(siteConfig)
  // B안 모양 칸(정보 띠 칸·진료 항목 모양). 설정이 없거나 기본값이면 null·기본 — 지금 마크업 그대로다.
  const bookingUrl = naverBookingUrl(hospital)
  const heroBlocks = resolveHeroBlocks(siteConfig, bookingUrl)
  const homeModel: ClinicHomeModel = {
    hospital,
    hospitalRootUrl,
    physicians,
    faqEntries,
    clusterContents,
    contents,
    contentDensity,
    composition,
    heroPhotoUrl,
    mediaMode,
    accessMode,
    // 레이아웃 도입 전 화면은 블로그를 사실 패널이 아닌 연락 카드에 보였다 — A·C는 연락 카드가
    // 없으므로 공식 채널 목록에 블로그를 더해 화면에서 빠지지 않게 한다(sameAs에도 있다).
    factLinks: [...factLinks, { url: hospital.blog_url, label: '병원 블로그' }],
    plan,
    siteConfig,
    careFlow,
  }

  // 레이아웃 도입 전 화면이자 레이아웃 B(As-is 발전형)의 섹션 — 섹션 계획(`plan.order`)의 순서대로 그린다.
  // 설정이 없으면 계획은 지금 마크업 순서 그대로다. 섹션 안의 데이터 조건(진료 항목·글 수 등)은 그대로다.
  const sectionsB: Partial<Record<SiteSectionId, ReactNode>> = {
    hero: (
      <ClinicHero
        hospitalName={hospital.name}
        hospitalRootUrl={hospitalRootUrl}
        region={hospital.region}
        specialties={hospital.specialties}
        phone={hospital.phone}
        directorName={hospital.director_name}
        heroPhotoUrl={heroPhotoUrl}
        address={hospital.address}
        addressDetail={hospital.address_detail}
        businessHours={hospital.business_hours}
        accessMode={accessMode}
        mediaMode={mediaMode}
        heroHeadline={hospital.hero_headline}
        heroDescription={hospital.hero_description}
        blocks={heroBlocks}
        bookingUrl={bookingUrl}
      />
    ),
    // 첫 화면 아래 바로가기 메뉴(.clinic-section-index)는 2026-11-20 결정 12a로 뺐다 — 상단 메뉴와
    // 내용이 겹쳤고, 휴대폰에서는 상단 메뉴·바로가기·하단 바 세 겹 메뉴였다. 편집기에서 다시 켜는
    // 'section_index'는 모양 설정 PR에서 그린다(스타일은 globals.css에 남아 있다).
    treatments: (
      <div className="clinic-anchor-target">
        <TreatmentGrid
          treatments={hospital.treatments}
          hospitalRootUrl={hospitalRootUrl}
          title={plan.customTitle('treatments')}
          note={plan.customNote('treatments')}
          variant={siteConfig?.treatments.style}
          showDescription={siteConfig?.treatments.showDescription}
        />
      </div>
    ),
    doctor: (
      <div id="doctor" className="clinic-anchor-target">
        <DoctorIntro
          physicians={physicians}
          specialties={hospital.specialties}
          region={hospital.region}
          contentCount={contents.length}
          title={plan.customTitle('doctor')}
          note={plan.customNote('doctor')}
          photoLook={physicianPhotoLook(siteConfig)}
          titleBelow={siteConfig?.doctor.titleBelow ?? false}
        />
      </div>
    ),
    care_principles: (
      <CarePrinciples
        hospitalRootUrl={hospitalRootUrl}
        hospitalName={hospital.name}
        specialties={hospital.specialties}
        region={hospital.region}
        publicAbout={publicAbout}
        custom={hospital.care_principles}
        title={plan.customTitle('care_principles')}
        cardCount={siteConfig?.carePrinciples.cardCount}
        // '공식 채널 보기'는 오시는 길(#contact)로 간다 — 그 섹션이 꺼지면 하위 페이지로.
        channelsHref={plan.enabled('visit') ? `${hospitalRootUrl}#contact` : `${hospitalRootUrl}/visit`}
      />
    ),
    contents: (
      <div id="contents" className="clinic-anchor-target">
        <FeaturedContent
          contents={contents}
          hospitalRootUrl={hospitalRootUrl}
          hospitalName={hospital.name}
          directorName={hospital.director_name}
          secondaryLimit={composition.featuredSecondaryLimit}
          title={plan.customTitle('contents')}
          note={plan.customNote('contents')}
        />
      </div>
    ),
    questions: composition.showAnswerClusters ? (
      <AnswerClusters
        contents={clusterContents}
        hospitalRootUrl={hospitalRootUrl}
        treatments={hospital.treatments || []}
        region={hospital.region}
        specialties={hospital.specialties}
        title={plan.customTitle('questions')}
        note={plan.customNote('questions')}
      />
    ) : null,
    faq: (
      <ClinicFaq
        entries={faqEntries}
        hospitalRootUrl={hospitalRootUrl}
        title={plan.customTitle('faq')}
        note={plan.customNote('faq')}
      />
    ),
    care_flow: composition.showCareFlow ? (
      <CareFlow
        hospitalRootUrl={hospitalRootUrl}
        hospitalName={hospital.name}
        title={plan.customTitle('care_flow')}
        note={careFlow.note}
        steps={careFlow.steps}
      />
    ) : null,
    gallery: (
      <ClinicGallery
        photos={hospital.photos ?? []}
        policy={clinicGalleryPolicy('home', contentDensity)}
        excludeUrl={heroPhotoUrl}
        allPhotosHref={`${hospitalRootUrl}/visit#gallery`}
        title={plan.customTitle('gallery')}
        note={plan.customNote('gallery')}
      />
    ),
    facts: (
      <div id="facts" className="clinic-anchor-target">
        <HospitalFacts
          hospitalName={hospital.name}
          address={hospital.address}
          addressDetail={hospital.address_detail}
          phone={hospital.phone}
          businessHours={hospital.business_hours}
          region={hospital.region}
          specialties={hospital.specialties}
          directorName={hospital.director_name}
          hiraOrgId={hospital.hira_org_id}
          links={factLinks}
          googleMapsUrl={hospital.google_maps_url}
        />
      </div>
    ),
    visit: (
      <div className="clinic-anchor-target">
        <ContactCard
          address={hospital.address}
          addressDetail={hospital.address_detail}
          phone={hospital.phone}
          googleMapsUrl={hospital.google_maps_url}
          links={externalChannels}
          hospitalName={hospital.name}
          hospitalRootUrl={hospitalRootUrl}
          region={hospital.region}
          parkingNote={hospital.parking_note}
          transitNote={hospital.transit_note}
          websiteUrl={hospital.website_url}
          hoursHref={visitHoursHref(hospitalRootUrl, false)}
          title={plan.customTitle('visit')}
          note={plan.customNote('visit')}
        />
      </div>
    ),
  }

  return (
    <>
      <JsonLd data={pageJsonLd} />
      <div
        className={`clinic-shell clinic-shell--editorial clinic-shell--density-${contentDensity} ${clinicLayoutShellClass(layout)}`}
        style={buildClinicThemeStyle(hospital)}
      >
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalRootUrl={hospitalRootUrl}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
          logoUrl={hospital.logo_url}
          currentSection="home"
          googleMapsUrl={hospital.google_maps_url}
          siteConfig={hospital.site_config}
          faqAvailable={faqEntries.length > 0}
        />
        {layout === 'A' ? (
          <HomeLayoutA model={homeModel} />
        ) : layout === 'C' ? (
          <HomeLayoutC model={homeModel} />
        ) : (
        <main id="main-content">
          {/* 레이아웃 도입 전 화면이자 레이아웃 B(As-is 발전형)의 구성. B는 같은 섹션·순서를
              쓰고 생김새만 바꾼다(_layouts/layout-b.module.css). 섹션은 `sectionsB` 표를 섹션 계획 순서로
              그린다 — 선택 섹션은 승인된 데이터가 있을 때만 보이고, 빈 자리를 지어내지 않는다. */}
          {plan.order.map((id) => (
            <Fragment key={id}>{sectionsB[id]}</Fragment>
          ))}
        </main>
        )}
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
