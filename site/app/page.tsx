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
  marketSection,
  measuredFigures,
  operationSection,
  operationSteps,
  painPoints,
  painSection,
  platformShareSection,
  platformShares,
  previewSection,
  sceneSection,
} from "@/lib/landing-copy";

import AnswerExplorer from "./_components/AnswerExplorer";
import HeaderScrollState from "./_components/HeaderScrollState";
import QueryMarquee from "./_components/QueryMarquee";
import RollingAiLogo from "./_components/RollingAiLogo";
import SceneSequence from "./_components/SceneSequence";
import PlatformShareChart from "./_components/PlatformShareChart";
import ScrollReveal from "./_components/ScrollReveal";

const DIAGNOSIS_PATH = "/ai-diagnosis";

/** 접지 않고 세워 두는 질문 수. 나머지는 "질문 N개 더 보기" 뒤로 들어간다. */
const FAQ_OPEN_COUNT = 4;

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
export default function Home() {
  return (
    <main id="main-content" className="landing-shell">
      <ScrollReveal />
      <HeaderScrollState />

      <header className="site-header">
        <a className="brand-lockup" href="#top" aria-label="MotionLabs Re:putation 홈">
          <strong>Re:putation</strong>
          <small>by MotionLabs</small>
        </a>

        <nav className="header-nav" aria-label="랜딩 페이지 섹션">
          <a href="#numbers">근거</a>
          <a href="#operation">운영 방식</a>
          <a href="#faq">자주 묻는 질문</a>
        </nav>

        <Link className="header-cta" href={DIAGNOSIS_PATH}>
          무료 진단
        </Link>
      </header>

      {/* ── ① 히어로 — 큰 카피 · 서브 카피 · CTA · 선착순 고지만 ─────
          시각물은 아래 미리보기 섹션으로 분리했다. 히어로에 목업을 붙이면 두 개를
          동시에 읽어야 하고, 정작 팔아야 하는 한 줄이 묻힌다. */}
      <section id="top" className="hero-section">
        {/* `{ai}` 자리에 굴러가는 AI 로고가 들어간다. 문자열을 미리 쪼개 두지 않고
            자리표시자를 쓰는 이유는, 조사 위치("…에 병원을")가 곧 문장이기 때문이다.
            카피와 컴포넌트가 갈라지면 어순이 조용히 깨진다. */}
        <h1>
          <span className="hero-lead">
            {landingHero.titleLead.split("{ai}")[0]}
            <RollingAiLogo />
            {landingHero.titleLead.split("{ai}")[1]}
          </span>
          <strong>{landingHero.titleMain}</strong>
        </h1>

        <div className="hero-actions" aria-label="주요 행동">
          <Link className="btn btn-primary btn-lg" href={DIAGNOSIS_PATH}>
            {landingHero.primaryCta}
          </Link>
        </div>

        {/* 제목이 두 로고를 이미 품고 있으므로 하단 "진단 대상" 줄은 중복이라 뺐다. */}
        <p className="hero-slots-note">{heroScarcity.note}</p>
      </section>

      {/* 환자 질문 띠 — 히어로와 장면 사이. 설명하기 전에 눈으로 읽게 한다. */}
      <QueryMarquee />

      {/* ── ② 환자가 보는 화면 — 히어로 바로 다음 ─────────────────
          리포트를 먼저 보여주면 "우리가 파는 것"부터 말하는 셈이다. 먼저 볼 것은
          환자가 실제로 보는 답변이고, 거기 병원 이름이 서너 개뿐이라는 사실이다. */}
      <section id="scene" className="preview-section" aria-labelledby="scene-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{sceneSection.label}</p>
          <h2 id="scene-heading">{sceneSection.heading}</h2>
        </div>

        <SceneSequence example={answerExamples[0]} disclaimer={answerDemo.disclaimer} />
      </section>

      <section id="numbers" className="market-section" aria-labelledby="market-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{marketSection.label}</p>
          <h2 id="market-heading">{marketSection.heading}</h2>
        </div>

        {/* 외부 조사와 자체 실측을 한 그리드에 둔다. 두 섹션으로 나눠 두면 여섯 개
            숫자를 두 번에 걸쳐 읽게 되고 어느 것도 남지 않는다.
            **색이 출처의 구분이다** — 외부는 검정, 우리가 잰 것은 파랑. */}
        <dl className="figure-grid">
          {measuredFigures.map((figure) => (
            <div key={figure.value} className="figure-card is-measured" data-reveal>
              <dt>
                <span className="figure-value">{figure.value}</span>
              </dt>
              <dd>
                {figure.label}
                <span className="figure-source">{figure.source}</span>
              </dd>
            </div>
          ))}
        </dl>

        {/* 차트를 별도 밴드로 두면 "왜 두 곳만"을 위해 섹션 하나를 통째로 쓰게 된다.
            같은 근거이므로 숫자 옆에 붙인다. 해설은 FAQ가 이미 답한다. */}
        <div className="share-block" data-reveal>
          <p className="share-nudge">{platformShareSection.nudge}</p>
          <PlatformShareChart
            shares={platformShares}
            sourceNote={platformShareSection.sourceNote}
          />
        </div>
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

      {/* ── ③ 운영 방식 ──────────────────────────────────────────── */}
      <section id="operation" className="operation-section" aria-labelledby="operation-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{operationSection.label}</p>
          <h2 id="operation-heading">{operationSection.heading}</h2>
        </div>

        <ol className="process-grid">
          {operationSteps.map((step, index) => (
            <li key={step.label} data-reveal>
              <span className="process-num">{String(index + 1).padStart(2, "0")}</span>
              <p className="process-label">{step.label}</p>
              <h3>{step.title}</h3>
            </li>
          ))}
        </ol>
      </section>

      {/* ── ⑤ 하지 않는 것 ───────────────────────────────────────── */}
      <section className="limits-section" aria-labelledby="limits-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{limitsSection.label}</p>
          <h2 id="limits-heading">{limitsSection.heading}</h2>
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
      <section id="faq" className="faq-section" aria-labelledby="faq-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{faqSection.label}</p>
          <h2 id="faq-heading">{faqSection.heading}</h2>
        </div>

        {/* 앞의 넷만 펼쳐 두고 나머지는 한 줄 뒤로 접는다. 열한 개를 한꺼번에 세워 두면
            읽지도 않을 목록이 화면 하나를 차지한다. 더 알고 싶은 사람만 열면 된다.
            `<details>` 안에 `<details>`는 유효한 마크업이고, JS 없이도 동작한다. */}
        <div className="faq-list">
          {faqItems.slice(0, FAQ_OPEN_COUNT).map((item) => (
            <details key={item.question} data-reveal>
              <summary>{item.question}</summary>
              <p>{item.answer}</p>
            </details>
          ))}

          {faqItems.length > FAQ_OPEN_COUNT && (
            <details className="faq-more" data-reveal>
              <summary>
                {faqSection.moreLabel.replace(
                  "{n}",
                  String(faqItems.length - FAQ_OPEN_COUNT),
                )}
              </summary>
              {faqItems.slice(FAQ_OPEN_COUNT).map((item) => (
                <details key={item.question}>
                  <summary>{item.question}</summary>
                  <p>{item.answer}</p>
                </details>
              ))}
            </details>
          )}
        </div>
      </section>

      {/* ── 받으시는 것 — 신청 직전에 보여준다 ────────────────────── */}
      <section id="preview" className="report-section" aria-labelledby="preview-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{previewSection.label}</p>
          <h2 id="preview-heading">{previewSection.heading}</h2>
        </div>

        <div className="preview-stage" data-reveal>
          <AnswerExplorer examples={answerExamples} disclaimer={answerDemo.disclaimer} />
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
