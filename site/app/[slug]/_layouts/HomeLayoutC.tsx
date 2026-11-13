import Image from 'next/image'
import Link from 'next/link'

import { resolveAssetUrl, TYPE_LABELS } from '@/lib/api'
import { clinicGalleryPolicy, displayClinicLabels, selectClinicGalleryPhotos } from '@/lib/clinic-design'
import { buildClinicHeroHeadline } from '@/lib/clinic-hero-headline'
import { CLINIC_IMAGE_QUALITY, CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
import {
  buildSessionGrid,
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
import s from './layout-c.module.css'

const NEWS_LIMIT = 5

function publishedDate(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const parts = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date)
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? ''
  return `${get('year')}.${get('month')}.${get('day')}`
}

/**
 * 레이아웃 C — 일본 동네 내과 결(しおや内科・内視鏡クリニック 참고).
 * 꽉 찬 병원 사진 + 오늘 진료 위젯, 최근 글과 요일×시간 ●진료표, 고딕·영문 머리표.
 * 시안: Design 캔버스 `SetC.dc.html`(docs/plans/assets/2026-11-13-clinic-layouts/layout-c.dc.html).
 */
export function HomeLayoutC({ model }: { model: ClinicHomeModel }) {
  const { hospital, hospitalRootUrl, physicians, faqEntries, clusterContents, contents, composition } = model
  const specialtyLabel = displayClinicLabels(hospital.specialties).join(' · ')
  const locationLabel = displayClinicLabels(hospital.region).join(' ')
  const headline = buildClinicHeroHeadline({
    approvedHeadline: hospital.hero_headline,
    accessMode: model.accessMode,
    specialtyLabel,
    hospitalName: hospital.name,
  })
  const today = seoulToday()
  const todays = todayHours(hospital.business_hours, today.key)
  const todaysTime = todays && !todays.closed ? splitHoursNote(todays.time) : null
  const grid = buildSessionGrid(hospital.business_hours)
  const news = contents.slice(0, NEWS_LIMIT)
  const questions = composition.showAnswerClusters ? selectAnswerQuestions(clusterContents, 6) : []
  const galleryPolicy = clinicGalleryPolicy('home', model.contentDensity)
  const gallery = selectClinicGalleryPhotos(hospital.photos ?? [], galleryPolicy.previewLimit, {
    excludeUrl: model.heroPhotoUrl,
  })
  const visibleLinks = model.factLinks.filter((link): link is { url: string; label: string } => Boolean(link.url))
  const address = fullClinicAddress(hospital.address, hospital.address_detail)
  const hasHours = WEEK_DAY_KEYS.some((key) => hospital.business_hours?.[key])

  return (
    <main id="main-content" className={s.home}>
      <section className={`${s.hero}${model.heroPhotoUrl ? '' : ` ${s.heroPlain}`}`} aria-label="첫 화면">
        {model.heroPhotoUrl ? (
          <>
            <Image
              src={model.heroPhotoUrl}
              alt={model.mediaMode === 'brand-graphic' ? `${hospital.name} 브랜드 그래픽` : `${hospital.name} 진료 공간`}
              fill
              preload
              quality={CLINIC_IMAGE_QUALITY}
              sizes="100vw"
              style={{ objectFit: 'cover' }}
            />
            <span className={s.heroVeil} aria-hidden="true" />
          </>
        ) : null}
        <div className={s.heroInner}>
          <div className={s.heroCopy}>
            {[locationLabel, specialtyLabel].filter(Boolean).length > 0 ? (
              <p className={s.heroKicker}>{[locationLabel, specialtyLabel].filter(Boolean).join(' · ')}</p>
            ) : null}
            <h1 className={s.heroTitle}>
              {/* 승인 문구가 한 조각뿐이면 앞부분(lead)이 없다 — 그 조각을 큰 제목으로 쓴다. */}
              <span className={s.heroLead}>
                {(headline.lead.length > 0 ? headline.lead : [headline.emphasis]).map((part, index) => (
                  <span key={`${part}-${index}`}>{part}{' '}</span>
                ))}
              </span>
              {headline.lead.length > 0 ? <span className={s.heroEmphasis}>{headline.emphasis}</span> : null}
            </h1>
          </div>
          <div className={s.todayWidget}>
            <div>
              <p className={s.widgetLabel}>{today.month}월 {today.day}일 ({today.label})</p>
              <p className={s.widgetValue}>오늘 진료</p>
            </div>
            <div>
              <p className={s.widgetLabel}>{todays && !todays.closed ? '진료시간' : '안내'}</p>
              <p className={s.widgetValue}>
                {todaysTime ? todaysTime.main : todays ? '휴진' : '전화로 확인'}
                {todaysTime?.note ? <small className={s.widgetNote}>{todaysTime.note}</small> : null}
              </p>
            </div>
            <a className={s.widgetCall} href={`tel:${hospital.phone}`}>
              <span>전화 상담</span>
              {hospital.phone}
            </a>
          </div>
        </div>
      </section>

      <div className={s.infoRow}>
        {news.length > 0 ? (
          <section id="contents" className={s.newsBlock}>
            <div className={s.blockHead}>
              <h2 className={s.title}><span className={s.label}>NEWS</span>최근 건강 정보</h2>
              <Link className={s.moreLink} href={`${hospitalRootUrl}/contents`}>
                {contents.length}편 모두 보기 →
              </Link>
            </div>
            <ul className={s.newsList}>
              {news.map((content) => (
                <li key={content.id}>
                  <Link href={`${hospitalRootUrl}/contents/${content.id}`}>
                    <span className={s.newsDate}>{publishedDate(content.published_at)}</span>
                    <span className={s.badge}>{TYPE_LABELS[content.content_type] ?? content.content_type}</span>
                    <span className={s.newsTitle}>{content.title}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {hasHours ? (
          <section className={s.scheduleBlock} aria-label="진료시간">
            <h2 className={s.title}><span className={s.label}>INFORMATION</span>진료시간</h2>
            {grid ? (
              <div className={s.tableScroll}>
                <table className={s.schedule}>
                  <thead>
                    <tr>
                      <th scope="col">진료시간</th>
                      {WEEK_DAY_KEYS.map((key) => (
                        <th key={key} scope="col" aria-current={key === today.key ? 'date' : undefined}>
                          {dayShortLabel(key)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {grid.rows.map((row) => (
                      <tr key={row.label}>
                        <th scope="row">{row.label}</th>
                        {row.cells.map((cell, index) => (
                          <td key={WEEK_DAY_KEYS[index]} data-state={cell.state}>
                            {cell.state === 'open' ? '●' : cell.state === 'partial' ? '▲' : '—'}
                            <span className="sr-only">
                              {cell.state === 'open' ? ' 진료' : cell.state === 'partial' ? ' 일부 진료' : ' 진료 없음'}
                            </span>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <ul className={s.hourList}>
                {WEEK_DAY_KEYS.map((key) => (
                  <li key={key} aria-current={key === today.key ? 'date' : undefined}>
                    <span>{dayShortLabel(key)}</span>
                    <span>{hospital.business_hours?.[key] || '-'}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className={s.scheduleNote}>
              {grid?.hasPartial ? '▲ 표시는 시간이 다른 날입니다. ' : ''}요일별 정확한 시간은 아래 기본 정보에서 확인하세요.
            </p>
            <div className={s.scheduleActions}>
              <a className={s.primaryButton} href={`tel:${hospital.phone}`}>전화 상담</a>
              {hospital.naver_place_url ? (
                <a className={s.secondaryButton} href={hospital.naver_place_url} target="_blank" rel="noopener noreferrer">길찾기</a>
              ) : null}
            </div>
          </section>
        ) : null}
      </div>

      {hospital.treatments.length > 0 ? (
        <section id="treatments" className={s.tintSection}>
          <div className={s.inner}>
            <div className={s.centerHead}>
              <h2 className={s.title}><span className={s.label}>MEDICAL</span>진료 항목</h2>
              <p className={s.note}>병원에서 진료하는 항목 {hospital.treatments.length}가지입니다.</p>
            </div>
            <ul className={s.tiles}>
              {hospital.treatments.map((treatment, index) => {
                const slug = buildTreatmentSlug(treatment.name)
                const inner = (
                  <>
                    <span className={s.tileIndex} aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                    <span className={s.tileName}>{treatment.name}</span>
                  </>
                )
                return (
                  <li key={treatment.name}>
                    {slug ? (
                      <Link className={s.tile} href={`${hospitalRootUrl}/treatments/${slug}`}>{inner}</Link>
                    ) : (
                      <span className={s.tile}>{inner}</span>
                    )}
                  </li>
                )
              })}
            </ul>
            <p className={s.centerHead}>
              <Link className={s.primaryButton} href={`${hospitalRootUrl}/treatments`}>진료 항목 자세히 보기</Link>
            </p>
          </div>
        </section>
      ) : null}

      {physicians.length > 0 ? (
        <section id="doctor" className={s.section}>
          <h2 className={s.title}><span className={s.label}>DOCTOR</span>의료진 소개</h2>
          <div className={s.doctorList}>
            {physicians.map((physician) => (
              <PhysicianProfile
                key={physician.id}
                physician={physician}
                region={[]}
                className={s.doctor}
                photoClassName={s.doctorPhoto}
                bodyClassName={s.doctorBody}
                asTable
                tableClassName={s.doctorTable}
              />
            ))}
          </div>
          <Link className={s.moreLink} href={`${hospitalRootUrl}/doctor`}>의료진 소개 전체 보기 →</Link>
        </section>
      ) : null}

      <section className={s.darkSection} aria-label="진료 원칙과 진료 과정">
        <div className={s.inner}>
          <h2 className={s.title}><span className={s.label}>POLICY</span>{CARE_PRINCIPLES_TITLE}</h2>
          <div className={s.policyGrid}>
            {CARE_PRINCIPLES.map((principle, index) => (
              <div key={principle.key} className={s.policyCard}>
                <span className={s.policyIndex} aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                <h3>{principle.title}</h3>
                <p>{principle.body}</p>
              </div>
            ))}
          </div>
          {composition.showCareFlow ? (
            <>
              <h2 className={`${s.title} ${s.flowTitle}`}><span className={s.label}>FLOW</span>{CARE_FLOW_TITLE}</h2>
              <p className={s.note}>{CARE_FLOW_NOTE}</p>
              <ol className={s.flow}>
                {CARE_STEPS.map((step) => (
                  <li key={step.label}>
                    <span className={s.stepBadge}>STEP {step.label} · {step.aux}</span>
                    <h3>{step.title}</h3>
                    <p>{step.body}</p>
                  </li>
                ))}
              </ol>
            </>
          ) : null}
        </div>
      </section>

      {questions.length > 0 ? (
        <section className={s.section} aria-label="자주 확인하는 질문">
          <div className={s.blockHead}>
            <h2 className={s.title}><span className={s.label}>Q&amp;A</span>자주 확인하는 질문</h2>
            <Link className={s.moreLink} href={`${hospitalRootUrl}/contents`}>건강 정보 {contents.length}편 →</Link>
          </div>
          <div className={s.qaGrid}>
            {questions.map((content) => (
              <Link key={content.id} className={s.qaCard} href={`${hospitalRootUrl}/contents/${content.id}`}>
                <span className={s.qMark} aria-hidden="true">Q</span>
                <span className={s.qaBody}>
                  <span className={s.qaKind}>{TYPE_LABELS[content.content_type] ?? content.content_type}</span>
                  <h3 className={s.qaTitle}>{questionText(content)}</h3>
                  {questionSummary(content) ? <span className={s.qaSummary}>{questionSummary(content)}</span> : null}
                </span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      {faqEntries.length > 0 ? (
        <section id="faq" className={s.section}>
          <h2 className={s.title}><span className={s.label}>FAQ</span>자주 묻는 질문</h2>
          <dl className={s.faq}>
            {faqEntries.map((entry) => (
              <div key={entry.id} className={s.faqItem}>
                <dt>
                  <span className={s.qMark} aria-hidden="true">Q</span>
                  {entry.question}
                </dt>
                <dd>
                  <span className={s.aMark} aria-hidden="true">A</span>
                  <span>
                    {entry.answer}{' '}
                    <Link href={entry.url}>자세히 보기</Link>
                  </span>
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {gallery.photos.length >= galleryPolicy.minimumPhotoCount ? (
        <section className={s.section} aria-label="병원 공간">
          <div className={s.blockHead}>
            <h2 className={s.title}><span className={s.label}>CLINIC</span>병원 공간</h2>
            <Link className={s.moreLink} href={`${hospitalRootUrl}/visit#gallery`}>사진 전체 보기 →</Link>
          </div>
          <div className={s.gallery}>
            {gallery.photos.map((photo) => {
              const url = resolveAssetUrl(photo.url)
              if (!url) return null
              return (
                <figure key={photo.id}>
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

      <div id="contact">
        <section id="facts" className={`${s.section} ${s.factsSection}`}>
          <h2 className={s.title}><span className={s.label}>ACCESS</span>{hospital.name} 기본 정보</h2>
          <div className={s.factsRow}>
            <table className={s.factsTable}>
              <tbody>
                <tr><th scope="row">병원명</th><td>{hospital.name}</td></tr>
                {hospital.director_name ? <tr><th scope="row">대표원장</th><td>{hospital.director_name}</td></tr> : null}
                {hospital.specialties.length > 0 ? <tr><th scope="row">진료과</th><td>{hospital.specialties.join(', ')}</td></tr> : null}
                {hospital.region.length > 0 ? <tr><th scope="row">진료 지역</th><td>{hospital.region.join(' · ')}</td></tr> : null}
                <tr><th scope="row">주소</th><td>{address}</td></tr>
                <tr><th scope="row">전화</th><td><a href={`tel:${hospital.phone}`}>{hospital.phone}</a></td></tr>
                {hasHours ? (
                  <tr>
                    <th scope="row">진료시간</th>
                    <td>
                      <ul className={s.plainList}>
                        {WEEK_DAY_KEYS.map((key) => (
                          <li key={key}>{dayShortLabel(key)} {hospital.business_hours?.[key] || '-'}</li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ) : null}
                {hospital.hira_org_id ? <tr><th scope="row">HIRA</th><td>공공기관 식별정보 {hospital.hira_org_id}</td></tr> : null}
              </tbody>
            </table>
            <div className={s.factsSide}>
              {hospital.hero_description?.trim() ? <p className={s.factsNote}>{hospital.hero_description}</p> : null}
              {visibleLinks.length > 0 ? (
                <>
                  <p className={s.sideLabel}>병원 공식 채널</p>
                  <div className={s.channelGrid} aria-label="병원 공식 채널">
                    {visibleLinks.map((link) => (
                      <a key={link.url} href={link.url} target="_blank" rel="noopener">{link.label}</a>
                    ))}
                  </div>
                </>
              ) : null}
              <div className={s.channelGrid}>
                {hospital.naver_place_url ? (
                  <a className={s.primaryButton} href={hospital.naver_place_url} target="_blank" rel="noopener noreferrer">네이버 지도</a>
                ) : null}
                {hospital.google_maps_url ? (
                  <a className={s.primaryButton} href={hospital.google_maps_url} target="_blank" rel="noopener noreferrer">Google 지도</a>
                ) : null}
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  )
}
