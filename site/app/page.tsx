import Link from "next/link";

import {
  answerDemo,
  answerExamples,
  ctaSection,
  faqItems,
  faqSection,
  heroScarcity,
  landingHero,
  limitItems,
  limitsSection,
  marketFigures,
  marketSection,
  measuredFigures,
  measuredSection,
  methodItems,
  methodSection,
  operationSection,
  operationSteps,
  painPoints,
  painSection,
  platformShareSection,
  platformShares,
  previewSection,
} from "@/lib/landing-copy";

import { fetchTodaySlots, resolveSlotState } from "@/lib/diagnosis-slots";

import { GeminiLogo, OpenAiLogo } from "./_components/AiLogos";
import AnswerExplorer from "./_components/AnswerExplorer";
import PlatformShareChart from "./_components/PlatformShareChart";
import ScrollReveal from "./_components/ScrollReveal";

const DIAGNOSIS_PATH = "/ai-diagnosis";

/**
 * 랜딩의 논증 순서 — 이 순서 자체가 이 서비스의 주장이다.
 *
 *   ① 환자가 어디서 묻는가 (외부 조사)
 *   ② 그 답변에 자리가 몇 개인가 (자체 실측)
 *   ③ 그 자리를 만들려면 무엇이 필요한가 (운영 방식)
 *   ④ 그걸 어떻게 검증하는가 (측정 규약 공개)
 *   ⑤ 무엇은 못 하는가 (하지 않는 것)
 *   ⑥ 그래서 지금 확인해보라 (무료 진단)
 *
 * ③을 ①② 앞에 두면 기능 소개가 되고, ⑤를 빼면 노출 대행과 구별되지 않는다.
 */
export default async function Home() {
  const slotState = resolveSlotState(await fetchTodaySlots(), heroScarcity);

  return (
    <main id="main-content" className="landing-shell">
      <ScrollReveal />

      <header className="site-header">
        <a className="brand-lockup" href="#top" aria-label="MotionLabs Re:putation 홈">
          <strong>Re:putation</strong>
          <small>by MotionLabs</small>
        </a>

        <nav className="header-nav" aria-label="랜딩 페이지 섹션">
          <a href="#measured">실측 결과</a>
          <a href="#operation">운영 방식</a>
          <a href="#method">측정 규약</a>
        </nav>

        <Link className="header-cta" href={DIAGNOSIS_PATH}>
          무료 진단
        </Link>
      </header>

      {/* ── ① 히어로 — 큰 카피 · 서브 카피 · CTA · 선착순 고지만 ─────
          시각물은 아래 미리보기 섹션으로 분리했다. 히어로에 목업을 붙이면 두 개를
          동시에 읽어야 하고, 정작 팔아야 하는 한 줄이 묻힌다. */}
      <section id="top" className="hero-section">
        {/* 선착순을 제목보다 먼저 읽히게 둔다 — 리드마그넷의 장치는 희소성이고,
            CTA 아래 각주로 내려가면 아무도 읽지 않는다. 숫자는 실제 카운터에서 온다. */}
        <p className={`hero-pill ${slotState.tone}`}>
          <span className="hero-pill-dot" aria-hidden="true" />
          {slotState.text}
        </p>

        {/* 두 줄뿐이고, 무게가 다르다 — 시선은 titleMain(사실)에 앉는다. */}
        <h1>
          <span className="hero-lead">{landingHero.titleLead}</span>
          <strong>{landingHero.titleMain}</strong>
        </h1>
        <p className="hero-subcopy">{landingHero.body}</p>

        <div className="hero-actions" aria-label="주요 행동">
          <Link className="btn btn-primary btn-lg" href={DIAGNOSIS_PATH}>
            {landingHero.primaryCta}
          </Link>
          <a className="btn btn-text" href="#preview">
            {landingHero.secondaryCta}
            <span aria-hidden="true">→</span>
          </a>
        </div>

        {/* 희소성에 이유를 붙인다 — 이유 없는 선착순은 마케팅 장치로만 읽힌다. */}
        <p className="hero-slots-note">{heroScarcity.note}</p>

        <div className="hero-logos" aria-label="무료 진단으로 확인하는 AI 서비스">
          <span className="hero-logos-label">진단 대상</span>
          <span className="ai-logo">
            <OpenAiLogo className="ai-logo-mark" />
            ChatGPT
          </span>
          <span className="ai-logo">
            <GeminiLogo className="ai-logo-mark" />
            Gemini
          </span>
        </div>
      </section>

      {/* ── ①-b 리포트 미리보기 — 히어로에서 분리한 시각물 ────────── */}
      <section id="preview" className="preview-section" aria-labelledby="preview-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{previewSection.label}</p>
          <h2 id="preview-heading">{previewSection.heading}</h2>
          <p>{previewSection.body}</p>
        </div>

        <div className="preview-stage" data-reveal>
          <AnswerExplorer examples={answerExamples} disclaimer={answerDemo.disclaimer} />
        </div>
      </section>

      <section className="market-section" aria-labelledby="market-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{marketSection.label}</p>
          <h2 id="market-heading">{marketSection.heading}</h2>
          <p>{marketSection.body}</p>
        </div>

        <dl className="figure-grid">
          {marketFigures.map((figure) => (
            <div key={figure.value + figure.label} className="figure-card" data-reveal>
              <dt>{figure.value}</dt>
              <dd>
                {figure.label}
                {/* 출처를 숫자에서 떼어놓지 않는다 — 떨어지면 검증할 수 없는 주장이 된다. */}
                <span className="figure-source">{figure.source}</span>
              </dd>
            </div>
          ))}
        </dl>
      </section>

      {/* ── ①-b 원장님이 하시는 말 ────────────────────────────────
          3인칭 선언문만으로는 읽는 사람이 자기 문제로 인식하지 않는다.
          통증은 당사자의 문장으로 적는다. */}
      <section className="pain-section" aria-labelledby="pain-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{painSection.label}</p>
          <h2 id="pain-heading">{painSection.heading}</h2>
        </div>

        <ul className="pain-list">
          {painPoints.map((item) => (
            <li key={item.quote} data-reveal>
              <blockquote>{item.quote}</blockquote>
              <p>{item.answer}</p>
            </li>
          ))}
        </ul>
      </section>

      {/* ── ② 실측 결과 ──────────────────────────────────────────── */}
      <section id="measured" className="measured-section" aria-labelledby="measured-heading">
        <div className="measured-inner">
          <div className="section-heading" data-reveal>
            <p className="section-label">{measuredSection.label}</p>
            <h2 id="measured-heading">{measuredSection.heading}</h2>
            <p>{measuredSection.body}</p>
          </div>

          <dl className="measured-figures">
            {measuredFigures.map((figure) => (
              <div key={figure.value} data-reveal>
                <dt>{figure.value}</dt>
                <dd>
                  {figure.label}
                  <span className="figure-source">{figure.source}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* ── ③ 운영 방식 ──────────────────────────────────────────── */}
      <section id="operation" className="operation-section" aria-labelledby="operation-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{operationSection.label}</p>
          <h2 id="operation-heading">{operationSection.heading}</h2>
          <p>{operationSection.body}</p>
        </div>

        <ol className="process-grid">
          {operationSteps.map((step, index) => (
            <li key={step.label} data-reveal>
              <span className="process-num">{String(index + 1).padStart(2, "0")}</span>
              <p className="process-label">{step.label}</p>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* ── ③-b 측정 범위 — "왜 두 곳만" 에 대한 답 ────────────────
          커버리지의 한계가 아니라 비용 판단이다. 그래서 차트로 먼저 보여준다. */}
      <section className="share-section" aria-labelledby="share-heading">
        <div className="share-inner">
          <div className="section-heading" data-reveal>
            <p className="section-label">{platformShareSection.label}</p>
            <h2 id="share-heading">{platformShareSection.heading}</h2>
            <p>{platformShareSection.body}</p>
            <p className="share-nudge">{platformShareSection.nudge}</p>
          </div>

          <div data-reveal>
            <PlatformShareChart
              shares={platformShares}
              sourceNote={platformShareSection.sourceNote}
            />
          </div>
        </div>
      </section>

      {/* ── ④ 측정 규약 — 노출 대행과 갈라지는 지점 ───────────────── */}
      <section id="method" className="method-section" aria-labelledby="method-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{methodSection.label}</p>
          <h2 id="method-heading">{methodSection.heading}</h2>
          <p>{methodSection.body}</p>
        </div>

        <dl className="method-list">
          {methodItems.map((item) => (
            <div key={item.term} data-reveal>
              <dt>{item.term}</dt>
              <dd>{item.detail}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* ── ⑤ 하지 않는 것 ───────────────────────────────────────── */}
      <section className="limits-section" aria-labelledby="limits-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{limitsSection.label}</p>
          <h2 id="limits-heading">{limitsSection.heading}</h2>
          <p>{limitsSection.body}</p>
        </div>

        <ul className="limits-grid">
          {limitItems.map((item) => (
            <li key={item.title} data-reveal>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </li>
          ))}
        </ul>
      </section>

      {/* ── ⑤-b 자주 받는 질문 ────────────────────────────────────
          반론을 피하지 않는다. 여기서 답하지 않으면 상담에서 같은 질문을 다시 받는다. */}
      <section className="faq-section" aria-labelledby="faq-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{faqSection.label}</p>
          <h2 id="faq-heading">{faqSection.heading}</h2>
        </div>

        <div className="faq-list">
          {faqItems.map((item) => (
            <details key={item.question} data-reveal>
              <summary>{item.question}</summary>
              <p>{item.answer}</p>
            </details>
          ))}
        </div>
      </section>

      {/* ── ⑥ 무료 진단 ──────────────────────────────────────────── */}
      <section id="lead" className="cta-section" aria-labelledby="cta-heading">
        <div className="cta-inner" data-reveal>
          <p className="section-label">{ctaSection.label}</p>
          <h2 id="cta-heading">{ctaSection.heading}</h2>
          <p className="cta-body">{ctaSection.body}</p>

          <Link className="btn btn-primary btn-lg" href={DIAGNOSIS_PATH}>
            {ctaSection.primaryCta}
          </Link>

          <ul className="cta-notes">
            {ctaSection.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      </section>

      <footer className="site-footer">
        <div className="footer-brand">
          <strong>Re:putation</strong>
          <p>
            병원 정보를 AI가 읽을 수 있는 형태로 정리하고, 근거 기반 콘텐츠를 매달 발행하는
            AI 노출 컨설팅·콘텐츠 운영 서비스입니다.
          </p>
          <p className="footer-biz">
            운영사: 주식회사 모션랩스(MotionLabs Inc.) · 대표 이우진
            <br />
            서울특별시 강남구 · 사업자 정보는{" "}
            <a href="https://motionlabs.kr" target="_blank" rel="noopener noreferrer">
              motionlabs.kr
            </a>
            에서 확인하실 수 있습니다.
          </p>
        </div>
        <div className="footer-links">
          <a href="https://motionlabs.kr" target="_blank" rel="noopener noreferrer">
            motionlabs.kr ↗
          </a>
          <a href="mailto:contact@motionlabs.kr">contact@motionlabs.kr</a>
          <Link href="/privacy">개인정보 처리방침</Link>
          <Link href="/terms">이용약관</Link>
          <Link href={DIAGNOSIS_PATH}>무료 진단</Link>
        </div>
      </footer>
    </main>
  );
}
