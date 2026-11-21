import Image from 'next/image'
import Link from 'next/link'
import type { ReactNode } from 'react'
import { KeepTogether } from '../_components/KeepTogether'

import { IconAftercare, IconExam, IconExplain } from '@/components/brand'
import { resolveAssetUrl, TYPE_LABELS } from '@/lib/api'
import { HOLIDAY_LABEL, holidayHours } from '@/lib/business-hours'
import { clinicGalleryPolicy, displayClinicLabels, selectClinicGalleryPhotos } from '@/lib/clinic-design'
import { buildClinicHeroHeadline } from '@/lib/clinic-hero-headline'
import { CLINIC_IMAGE_QUALITY, CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
import {
  dayShortLabel,
  questionSummary,
  questionText,
  seoulToday,
  selectAnswerQuestions,
  splitHoursNote,
  todayHours,
  WEEK_DAY_KEYS,
} from '@/lib/clinic-layout'
import { fullClinicAddress } from '@/lib/clinic-schema'
import { heroLookAttributes, physicianPhotoLook, limitCarePrincipleCards, type SiteSectionId } from '@/lib/clinic-site-config'
import {
  CARE_FLOW_TITLE,
  resolveCarePrinciples,
  DOCTOR_SECTION_TITLE,
  FACTS_SECTION_NOTE,
  FAQ_SECTION_NOTE,
  FAQ_SECTION_TITLE,
  factsSectionTitle,
  GALLERY_SECTION_TITLE,
  QUESTIONS_SECTION_TITLE,
  TREATMENT_SECTION_NOTE,
  TREATMENT_SECTION_TITLE,
} from '@/lib/clinic-template-copy'
import { buildTreatmentSlug } from '@/lib/treatment-slug'

import { SectionLabel } from '../_components/SectionLabel'
import { SectionLook } from '../_components/SectionLook'
import { PhysicianProfile } from './PhysicianProfile'
import type { ClinicHomeModel } from './model'
import s from './layout-a.module.css'
import { isPreviewAssetUrl } from '@/lib/site-preview-assets'

const PRINCIPLE_ICONS = { explain: IconExplain, verified: IconExam, decide: IconAftercare } as const

/**
 * 레이아웃 A — One Medical 결. 흰 바탕, 큰 명조 제목, 가운데 정렬, 짙은 브랜드 띠 하나.
 * 시안: Design 캔버스 `P2.dc.html`(docs/plans/assets/2026-11-13-clinic-layouts/layout-a.dc.html).
 * 첫 화면 사진은 첫 화면용으로 승인된 병원 사진이다(원장 사진은 의료진 신원 용도뿐, 2026-11-13 결정).
 */
export function HomeLayoutA({ model }: { model: ClinicHomeModel }) {
  const { hospital, hospitalRootUrl, physicians, faqEntries, clusterContents, contents, composition, plan, careFlow } = model
  const principles = resolveCarePrinciples(hospital.care_principles)
  const specialtyLabel = displayClinicLabels(hospital.specialties).join(' · ')
  const headline = buildClinicHeroHeadline({
    approvedHeadline: hospital.hero_headline,
    accessMode: model.accessMode,
    specialtyLabel,
    hospitalName: hospital.name,
  })
  const today = seoulToday()
  const todays = todayHours(hospital.business_hours, today.key)
  const holiday = holidayHours(hospital.business_hours)
  const questions = composition.showAnswerClusters ? selectAnswerQuestions(clusterContents, 6) : []
  const gallery = selectClinicGalleryPhotos(
    hospital.photos ?? [],
    clinicGalleryPolicy('home', model.contentDensity).previewLimit,
    { excludeUrl: model.heroPhotoUrl },
  )
  const galleryPolicy = clinicGalleryPolicy('home', model.contentDensity)
  const visibleLinks = model.factLinks.filter((link): link is { url: string; label: string } => Boolean(link.url))
  const address = fullClinicAddress(hospital.address, hospital.address_detail)

  const questionsBlock = questions.length > 0 ? (
    <section className={s.section}>
      <div className={`${s.sectionHead} clinic-look-head clinic-look-row`}>
        {/* 머리 줄(제목·전체 보기)이 가로 배치라 소제목은 제목과 한 묶음으로 둔다 — 제목 위에 온다. */}
        {plan.label('contents') ? (
          <div>
            <SectionLabel text={plan.label('contents') ?? ''} />
            <h2 className={`${s.bigTitle} clinic-look-title`}>{plan.title('contents', QUESTIONS_SECTION_TITLE)}</h2>
          </div>
        ) : (
          <h2 className={`${s.bigTitle} clinic-look-title`}>{plan.title('contents', QUESTIONS_SECTION_TITLE)}</h2>
        )}
        <Link className={s.textLink} href={`${hospitalRootUrl}/contents`}>
          의료 정보 {contents.length}편 모두 보기 ›
        </Link>
      </div>
      {plan.customNote('contents') ? <p className={`${s.note} clinic-look-note`}>{plan.customNote('contents')}</p> : null}
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
    ) : null

  const faqBlock = faqEntries.length > 0 ? (
    <section id="faq" className={s.section}>
      {plan.label('faq') ? <SectionLabel text={plan.label('faq') ?? ''} /> : null}
      <h2 className={`${s.bigTitle} clinic-look-title`}>{plan.title('faq', FAQ_SECTION_TITLE)}</h2>
      <p className={`${s.note} clinic-look-note`}>{plan.note('faq', FAQ_SECTION_NOTE)}</p>
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
      {questions.length === 0 && contents.length > 0 ? (
        <p className={s.centered}>
          <Link className={s.textLink} href={`${hospitalRootUrl}/contents`}>
            의료 정보 {contents.length}편 모두 보기 ›
          </Link>
        </p>
      ) : null}
    </section>
    ) : null

  const faqWithContents = plan.adjacent('contents', 'faq')

  const sections: Partial<Record<SiteSectionId, ReactNode>> = {
    hero: (
      <section className={s.hero} aria-label="첫 화면" {...heroLookAttributes(model.siteConfig?.hero.look)}>
        <div className={s.heroInner}>
          <div className={s.heroCopy}>
            <h1 className={s.heroTitle}>
              {/* 승인 문구가 한 조각뿐이면 앞부분(lead)이 없다 — 그 조각을 큰 제목으로 쓴다. */}
              <span className={s.heroLead}>
                {(headline.lead.length > 0 ? headline.lead : [headline.emphasis]).map((part, index) => (
                  <span key={`${part}-${index}`}><KeepTogether text={part} compounds />{' '}</span>
                ))}
              </span>
              {headline.lead.length > 0 ? (
                <span className={s.heroEmphasis}>
                  <KeepTogether text={headline.emphasis} compounds />
                </span>
              ) : null}
            </h1>
            <a className={s.primaryButton} href={`tel:${hospital.phone}`}>
              전화 상담 {hospital.phone}
            </a>
            {todays ? (
              <p className={s.heroToday}>
                오늘({today.label}) {todays.closed ? '휴진' : <KeepTogether text={todays.time} />} ·{' '}
                <a href="#facts">진료시간 전체 보기</a>
              </p>
            ) : null}
          </div>
          {model.heroPhotoUrl ? (
            <div className={s.heroPhoto}>
              <Image
                src={model.heroPhotoUrl}
                unoptimized={isPreviewAssetUrl(model.heroPhotoUrl)}
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
    ),
    treatments: (
      hospital.treatments.length > 0 ? (
        <section id="treatments" className={s.centerSection}>
          {plan.label('treatments') ? <SectionLabel text={plan.label('treatments') ?? ''} /> : null}
          <h2 className={`${s.bigTitle} clinic-look-title`}>{plan.title('treatments', TREATMENT_SECTION_TITLE)}</h2>
          <p className={`${s.note} clinic-look-note`}>{plan.note('treatments', TREATMENT_SECTION_NOTE)}</p>
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
      ) : null
    ),
    doctor: (
      physicians.length > 0 ? (
        <section id="doctor" className={s.section}>
          {plan.label('doctor') ? <SectionLabel text={plan.label('doctor') ?? ''} className={s.centered} /> : null}
          <h2 className={`${s.bigTitle} ${s.centered} clinic-look-title`}>{plan.title('doctor', DOCTOR_SECTION_TITLE)}</h2>
          {plan.customNote('doctor') ? <p className={`${s.note} ${s.centered} clinic-look-note`}>{plan.customNote('doctor')}</p> : null}
          <div className={s.doctorList}>
            {physicians.map((physician, index) => (
              <PhysicianProfile
                key={physician.id}
                physician={physician}
                region={physicians.length === 1 ? hospital.region : []}
                className={s.doctor}
                photoClassName={s.doctorPhoto}
                bodyClassName={s.doctorBody}
                photoLook={physicianPhotoLook(model.siteConfig)}
                titleBelow={model.siteConfig?.doctor.titleBelow ?? false}
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
      ) : null
    ),
    care_principles: (
      <section className={s.centerSection} aria-label="진료 원칙">
        {plan.label('care_principles') ? <SectionLabel text={plan.label('care_principles') ?? ''} /> : null}
        <h2 className={`${s.bigTitle} clinic-look-title`}>{plan.title('care_principles', principles.title)}</h2>
        <div className={s.principles}>
          {limitCarePrincipleCards(principles.cards, model.siteConfig).map((principle) => {
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
    ),
    hours: (
      todays ? (
        <section className={s.band} aria-label="오늘 진료">
          {plan.label('hours') ? <SectionLabel text={plan.label('hours') ?? ''} /> : null}
          <h2 className={`${s.bandTitle} clinic-look-title`}>
            {plan.customTitle('hours') ?? (todays.closed ? (
              `오늘(${today.label})은 휴진입니다`
            ) : (
              <>
                오늘({today.label}) <KeepTogether text={splitHoursNote(todays.time).main} /> 진료
                {/* 점심·접수 마감 같은 괄호 메모는 둘째 줄로 내려 큰 제목 중간에서 끊기지 않게 한다. */}
                {splitHoursNote(todays.time).note ? (
                  <span className={s.bandTitleNote}> <KeepTogether text={splitHoursNote(todays.time).note ?? ''} /></span>
                ) : null}
              </>
            ))}
          </h2>
          <p className={`${s.bandNote} clinic-look-note`}>요일별 진료시간은 아래 기본 정보에서 확인할 수 있습니다.</p>
          <a className={s.bandButton} href={`tel:${hospital.phone}`}>
            {hospital.phone} 전화하기
          </a>
        </section>
      ) : null
    ),
    // 글이 있으면 #contents는 언제나 있다 — 질문 목록이 꺼져도(글이 적거나 모두 FAQ로 쓰였을 때)
    // 다른 섹션의 링크가 빈 곳을 가리키지 않게, 기본 순서처럼 FAQ가 바로 뒤에 오면 FAQ까지 함께 감싼다.
    // 설정으로 둘이 떨어지면 #contents는 질문 목록 쪽에 남는다.
    contents: (
      <div id={contents.length > 0 ? 'contents' : undefined}>
      {questionsBlock}
      {/* 질문 목록 바로 뒤의 FAQ는 질문 목록과 한 덩어리로 보인다 — FAQ 배경 설정이 없으면 질문 목록의 배경을 따른다
          (질문 목록이 진한 띠면 FAQ도 같은 띠 규칙으로 — 어두운 글자가 진한 바탕에 남지 않게). */}
      {faqWithContents ? (
        <SectionLook
          nested
          align={plan.align('faq')}
          background={plan.background('faq') ?? plan.background('contents')}
          force={plan.align('contents') !== null}
        >
          {faqBlock}
        </SectionLook>
      ) : null}
      </div>
    ),
    faq: faqWithContents ? null : faqBlock,
    care_flow: (
      composition.showCareFlow ? (
        <section className={s.centerSection} aria-label="진료 과정">
          {plan.label('care_flow') ? <SectionLabel text={plan.label('care_flow') ?? ''} /> : null}
          <h2 className={`${s.bigTitle} clinic-look-title`}>{plan.title('care_flow', CARE_FLOW_TITLE)}</h2>
          <p className={`${s.note} clinic-look-note`}>{careFlow.note}</p>
          <ol className={s.flow}>
            {careFlow.steps.map((step) => (
              <li key={step.label} className={s.flowStep}>
                <span className={s.kicker}>{step.label}{step.aux ? <> · {step.aux}</> : null}</span>
                <h3 className={s.flowTitle}>{step.title}</h3>
                <p className={s.flowBody}>{step.body}</p>
              </li>
            ))}
          </ol>
        </section>
      ) : null
    ),
    gallery: (
      gallery.photos.length >= galleryPolicy.minimumPhotoCount ? (
        <section className={s.section} aria-label={plan.title('gallery', GALLERY_SECTION_TITLE)}>
          <div className={`${s.sectionHead} clinic-look-head clinic-look-row`}>
            {/* 머리 줄(제목·전체 보기)이 가로 배치라 소제목은 제목과 한 묶음으로 둔다 — 제목 위에 온다. */}
            {plan.label('gallery') ? (
              <div>
                <SectionLabel text={plan.label('gallery') ?? ''} />
                <h2 className={`${s.bigTitle} clinic-look-title`}>{plan.title('gallery', GALLERY_SECTION_TITLE)}</h2>
              </div>
            ) : (
              <h2 className={`${s.bigTitle} clinic-look-title`}>{plan.title('gallery', GALLERY_SECTION_TITLE)}</h2>
            )}
            <Link className={s.textLink} href={`${hospitalRootUrl}/visit#gallery`}>
              사진 전체 보기 ›
            </Link>
          </div>
          {plan.customNote('gallery') ? <p className={`${s.note} clinic-look-note`}>{plan.customNote('gallery')}</p> : null}
          <div className={s.gallery}>
            {gallery.photos.map((photo) => {
              const url = resolveAssetUrl(photo.url)
              if (!url) return null
              return (
                <figure key={photo.id} className={s.galleryItem}>
                  <span className={s.galleryImage}>
                    <Image src={url} unoptimized={isPreviewAssetUrl(url)} alt={photo.title} fill sizes={CLINIC_IMAGE_SIZES.gallery} loading="lazy" style={{ objectFit: 'cover' }} />
                  </span>
                  <figcaption>{photo.title}</figcaption>
                </figure>
              )
            })}
          </div>
        </section>
      ) : null
    ),
    facts: (
      <div id="contact" className={s.factsWrap}>
        <section id="facts" className={s.section}>
          {plan.label('facts') ? <SectionLabel text={plan.label('facts') ?? ''} /> : null}
          <h2 className={`${s.bigTitle} clinic-look-title`}>{factsSectionTitle(hospital.name)}</h2>
          <p className={`${s.note} clinic-look-note`}>{FACTS_SECTION_NOTE}</p>
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
                      <span>{time ? <KeepTogether text={time} /> : '-'}</span>
                    </li>
                  )
                })}
                {holiday ? (
                  <li>
                    <span>{HOLIDAY_LABEL}</span>
                    <span><KeepTogether text={holiday.value} /></span>
                  </li>
                ) : null}
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
    ),
  }

  return (
    <main id="main-content" className={s.home}>
      {plan.order.map((id) => (
        <SectionLook key={id} align={plan.align(id)} background={plan.background(id)}>
          {sections[id]}
        </SectionLook>
      ))}
    </main>
  )
}
