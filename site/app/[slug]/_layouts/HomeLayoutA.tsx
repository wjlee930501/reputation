import Image from 'next/image'
import Link from 'next/link'

import { IconAftercare, IconExam, IconExplain } from '@/components/brand'
import { resolveAssetUrl, TYPE_LABELS } from '@/lib/api'
import { clinicGalleryPolicy, displayClinicLabels, selectClinicGalleryPhotos } from '@/lib/clinic-design'
import { buildClinicHeroHeadline } from '@/lib/clinic-hero-headline'
import { CLINIC_IMAGE_QUALITY, CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
import {
  dayShortLabel,
  questionSummary,
  questionText,
  seoulToday,
  selectAnswerQuestions,
  todayHours,
  WEEK_DAY_KEYS,
} from '@/lib/clinic-layout'
import { fullClinicAddress } from '@/lib/clinic-schema'
import {
  CARE_FLOW_NOTE,
  CARE_FLOW_TITLE,
  CARE_PRINCIPLES,
  CARE_PRINCIPLES_TITLE,
  CARE_STEPS,
} from '@/lib/clinic-template-copy'
import { buildTreatmentSlug } from '@/lib/treatment-slug'

import { PhysicianProfile } from './PhysicianProfile'
import type { ClinicHomeModel } from './model'
import s from './layout-a.module.css'

const PRINCIPLE_ICONS = { explain: IconExplain, verified: IconExam, decide: IconAftercare } as const

/**
 * 레이아웃 A — One Medical 결. 흰 바탕, 큰 명조 제목, 가운데 정렬, 짙은 브랜드 띠 하나.
 * 시안: Design 캔버스 `P2.dc.html`(docs/plans/assets/2026-11-13-clinic-layouts/layout-a.dc.html).
 * 첫 화면 사진은 첫 화면용으로 승인된 병원 사진이다(원장 사진은 의료진 신원 용도뿐, 2026-11-13 결정).
 */
export function HomeLayoutA({ model }: { model: ClinicHomeModel }) {
  const { hospital, hospitalRootUrl, physicians, faqEntries, clusterContents, contents, composition } = model
  const specialtyLabel = displayClinicLabels(hospital.specialties).join(' · ')
  const headline = buildClinicHeroHeadline({
    approvedHeadline: hospital.hero_headline,
    accessMode: model.accessMode,
    specialtyLabel,
    hospitalName: hospital.name,
  })
  const today = seoulToday()
  const todays = todayHours(hospital.business_hours, today.key)
  const questions = composition.showAnswerClusters ? selectAnswerQuestions(clusterContents, 6) : []
  const gallery = selectClinicGalleryPhotos(
    hospital.photos ?? [],
    clinicGalleryPolicy('home', model.contentDensity).previewLimit,
    { excludeUrl: model.heroPhotoUrl },
  )
  const galleryPolicy = clinicGalleryPolicy('home', model.contentDensity)
  const visibleLinks = model.factLinks.filter((link): link is { url: string; label: string } => Boolean(link.url))
  const address = fullClinicAddress(hospital.address, hospital.address_detail)

  return (
    <main id="main-content" className={s.home}>
      <section className={s.hero} aria-label="첫 화면">
        <div className={s.heroInner}>
          <div className={s.heroCopy}>
            <h1 className={s.heroTitle}>
              {/* 승인 문구가 한 조각뿐이면 앞부분(lead)이 없다 — 그 조각을 큰 제목으로 쓴다. */}
              <span className={s.heroLead}>
                {(headline.lead.length > 0 ? headline.lead : [headline.emphasis]).map((part, index) => (
                  <span key={`${part}-${index}`}>{part}{' '}</span>
                ))}
              </span>
              {headline.lead.length > 0 ? <span className={s.heroEmphasis}>{headline.emphasis}</span> : null}
            </h1>
            <a className={s.primaryButton} href={`tel:${hospital.phone}`}>
              전화 상담 {hospital.phone}
            </a>
            {todays ? (
              <p className={s.heroToday}>
                오늘({today.label}) {todays.closed ? '휴진' : todays.time} ·{' '}
                <a href="#facts">진료시간 전체 보기</a>
              </p>
            ) : null}
          </div>
          {model.heroPhotoUrl ? (
            <div className={s.heroPhoto}>
              <Image
                src={model.heroPhotoUrl}
                alt={model.mediaMode === 'brand-graphic' ? `${hospital.name} 브랜드 그래픽` : `${hospital.name} 진료 공간`}
                fill
                preload
                quality={CLINIC_IMAGE_QUALITY}
                sizes={CLINIC_IMAGE_SIZES.hero}
                style={{ objectFit: 'cover' }}
              />
            </div>
          ) : null}
        </div>
      </section>

      {hospital.treatments.length > 0 ? (
        <section id="treatments" className={s.centerSection}>
          <h2 className={s.bigTitle}>진료 항목 {hospital.treatments.length}가지</h2>
          <p className={s.note}>항목을 누르면 진료 내용과 진행 방식을 볼 수 있습니다.</p>
          <ul className={s.pillList}>
            {hospital.treatments.map((treatment) => {
              const slug = buildTreatmentSlug(treatment.name)
              return (
                <li key={treatment.name}>
                  {slug ? (
                    <Link className={s.pill} href={`${hospitalRootUrl}/treatments/${slug}`}>
                      {treatment.name}
                    </Link>
                  ) : (
                    <span className={s.pill}>{treatment.name}</span>
                  )}
                </li>
              )
            })}
          </ul>
          <Link className={s.textLink} href={`${hospitalRootUrl}/treatments`}>
            진료 항목 자세히 보기 ›
          </Link>
        </section>
      ) : null}

      {physicians.length > 0 ? (
        <section id="doctor" className={s.section}>
          <h2 className={`${s.bigTitle} ${s.centered}`}>진료를 담당하는 의료진</h2>
          <div className={s.doctorList}>
            {physicians.map((physician, index) => (
              <PhysicianProfile
                key={physician.id}
                physician={physician}
                region={physicians.length === 1 ? hospital.region : []}
                className={s.doctor}
                photoClassName={s.doctorPhoto}
                bodyClassName={s.doctorBody}
                priority={index === 0}
              />
            ))}
          </div>
          <p className={s.centered}>
            <Link className={s.textLink} href={`${hospitalRootUrl}/doctor`}>
              의료진 소개 전체 보기 ›
            </Link>
          </p>
        </section>
      ) : null}

      <section className={s.centerSection} aria-label="진료 원칙">
        <h2 className={s.bigTitle}>{CARE_PRINCIPLES_TITLE}</h2>
        <div className={s.principles}>
          {CARE_PRINCIPLES.map((principle) => {
            const Icon = PRINCIPLE_ICONS[principle.key]
            return (
              <div key={principle.key} className={s.principle}>
                <span className={s.principleIcon} aria-hidden="true"><Icon /></span>
                <h3 className={s.principleTitle}>{principle.title}</h3>
                <p className={s.principleBody}>{principle.body}</p>
              </div>
            )
          })}
        </div>
      </section>

      {todays ? (
        <section className={s.band} aria-label="오늘 진료">
          <h2 className={s.bandTitle}>
            {todays.closed ? `오늘(${today.label})은 휴진입니다` : `오늘(${today.label}) ${todays.time} 진료`}
          </h2>
          <p className={s.bandNote}>요일별 진료시간은 아래 기본 정보에서 확인할 수 있습니다.</p>
          <a className={s.bandButton} href={`tel:${hospital.phone}`}>
            {hospital.phone} 전화하기
          </a>
        </section>
      ) : null}

      {questions.length > 0 ? (
        <section id="contents" className={s.section}>
          <div className={s.sectionHead}>
            <h2 className={s.bigTitle}>자주 확인하는 질문</h2>
            <Link className={s.textLink} href={`${hospitalRootUrl}/contents`}>
              건강 정보 {contents.length}편 모두 보기 ›
            </Link>
          </div>
          <div className={s.questionCards}>
            {questions.slice(0, 3).map((content) => (
              <Link key={content.id} className={s.questionCard} href={`${hospitalRootUrl}/contents/${content.id}`}>
                <span className={s.kicker}>{TYPE_LABELS[content.content_type] ?? content.content_type}</span>
                <h3 className={s.questionTitle}>{questionText(content)}</h3>
                {questionSummary(content) ? <p className={s.questionSummary}>{questionSummary(content)}</p> : null}
                <span className={s.more}>답변 읽기 ›</span>
              </Link>
            ))}
          </div>
          {questions.length > 3 ? (
            <ol className={s.questionRows} aria-label="추가 질문">
              {questions.slice(3).map((content) => (
                <li key={content.id}>
                  <Link href={`${hospitalRootUrl}/contents/${content.id}`}>
                    <span>
                      <span className={s.kicker}>{TYPE_LABELS[content.content_type] ?? content.content_type}</span>{' '}
                      {questionText(content)}
                    </span>
                    <span aria-hidden="true">›</span>
                  </Link>
                </li>
              ))}
            </ol>
          ) : null}
        </section>
      ) : null}

      {faqEntries.length > 0 ? (
        <section id="faq" className={s.section}>
          <h2 className={s.bigTitle}>자주 묻는 질문</h2>
          <dl className={s.faq}>
            {faqEntries.map((entry) => (
              <div key={entry.id} className={s.faqItem}>
                <dt className={s.faqQuestion}>{entry.question}</dt>
                <dd className={s.faqAnswer}>
                  <p>{entry.answer}</p>
                  <Link className={s.textLink} href={entry.url}>자세히 보기 ›</Link>
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {composition.showCareFlow ? (
        <section className={s.centerSection} aria-label="진료 과정">
          <h2 className={s.bigTitle}>{CARE_FLOW_TITLE}</h2>
          <p className={s.note}>{CARE_FLOW_NOTE}</p>
          <ol className={s.flow}>
            {CARE_STEPS.map((step) => (
              <li key={step.label} className={s.flowStep}>
                <span className={s.kicker}>{step.label} · {step.aux}</span>
                <h3 className={s.flowTitle}>{step.title}</h3>
                <p className={s.flowBody}>{step.body}</p>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {gallery.photos.length >= galleryPolicy.minimumPhotoCount ? (
        <section className={s.section} aria-label="병원 공간">
          <div className={s.sectionHead}>
            <h2 className={s.bigTitle}>병원 공간</h2>
            <Link className={s.textLink} href={`${hospitalRootUrl}/visit#gallery`}>
              사진 전체 보기 ›
            </Link>
          </div>
          <div className={s.gallery}>
            {gallery.photos.map((photo) => {
              const url = resolveAssetUrl(photo.url)
              if (!url) return null
              return (
                <figure key={photo.id} className={s.galleryItem}>
                  <span className={s.galleryImage}>
                    <Image src={url} alt={photo.title} fill sizes={CLINIC_IMAGE_SIZES.gallery} loading="lazy" style={{ objectFit: 'cover' }} />
                  </span>
                  <figcaption>{photo.title}</figcaption>
                </figure>
              )
            })}
          </div>
        </section>
      ) : null}

      <div id="contact" className={s.factsWrap}>
        <section id="facts" className={s.section}>
          <h2 className={s.bigTitle}>{hospital.name} 기본 정보</h2>
          <div className={s.factsGrid}>
            <div>
              <dl className={s.factList}>
                <div><dt>병원명</dt><dd>{hospital.name}</dd></div>
                {hospital.director_name ? <div><dt>대표원장</dt><dd>{hospital.director_name}</dd></div> : null}
                {hospital.specialties.length > 0 ? <div><dt>진료과</dt><dd>{hospital.specialties.join(', ')}</dd></div> : null}
                {hospital.region.length > 0 ? <div><dt>진료 지역</dt><dd>{hospital.region.join(' · ')}</dd></div> : null}
                <div><dt>주소</dt><dd>{address}</dd></div>
                <div><dt>전화</dt><dd><a href={`tel:${hospital.phone}`}>{hospital.phone}</a></dd></div>
                {hospital.hira_org_id ? <div><dt>HIRA</dt><dd>공공기관 식별정보 {hospital.hira_org_id}</dd></div> : null}
              </dl>
              {hospital.hero_description?.trim() ? <p className={s.factNote}>{hospital.hero_description}</p> : null}
            </div>
            <div>
              <h3 className={s.smallTitle}>주간 진료시간</h3>
              <ul className={s.hours} aria-label="주간 진료시간">
                {WEEK_DAY_KEYS.map((key) => {
                  const time = hospital.business_hours?.[key]
                  return (
                    <li key={key} aria-current={key === today.key ? 'date' : undefined}>
                      <span>{dayShortLabel(key)}요일</span>
                      <span>{time || '-'}</span>
                    </li>
                  )
                })}
              </ul>
              {visibleLinks.length > 0 ? (
                <>
                  <h3 className={s.smallTitle}>병원 공식 채널</h3>
                  <div className={s.channels} aria-label="병원 공식 채널">
                    {visibleLinks.map((link) => (
                      <a key={link.url} href={link.url} target="_blank" rel="noopener">{link.label}</a>
                    ))}
                  </div>
                </>
              ) : null}
              <div className={s.mapButtons}>
                {hospital.naver_place_url ? (
                  <a className={s.primaryButton} href={hospital.naver_place_url} target="_blank" rel="noopener noreferrer">네이버 지도로 길찾기</a>
                ) : null}
                {hospital.google_maps_url ? (
                  <a className={s.secondaryButton} href={hospital.google_maps_url} target="_blank" rel="noopener noreferrer">Google 지도</a>
                ) : null}
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  )
}
