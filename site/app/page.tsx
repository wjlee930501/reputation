import Link from "next/link";

import {
  answerDemo,
  answerExamples,
  comparisonItems,
  landingHero,
  processSteps,
  proofItems,
  trustItems,
} from "@/lib/landing-copy";

import { GeminiLogo, OpenAiLogo } from "./_components/AiLogos";
import AnswerExplorer from "./_components/AnswerExplorer";
import ScrollReveal from "./_components/ScrollReveal";

const CONSENT_VERSION = "v1.2026-05";

const CONSENT_DETAILS = [
  { label: "수집 목적", value: "AI 노출 진단 범위 확인 및 진단 상담 안내" },
  { label: "수집 항목", value: "병원명, 진료과/지역, 연락처(이메일 또는 전화), 확인하고 싶은 환자 질문" },
  { label: "보유 기간", value: "수집일로부터 180일 이내 자동 파기. 상담 종료 시 즉시 파기 가능" },
  { label: "거부 권리", value: "동의를 거부할 수 있으며, 거부 시 무료 진단 상담만 진행되지 않고 다른 불이익은 없습니다" },
];

export default async function Home({
  searchParams,
}: {
  searchParams?: Promise<{ lead?: string }>;
}) {
  const resolvedSearchParams = await searchParams;
  const leadStatus = resolvedSearchParams?.lead;
  const leadMessage =
    leadStatus === "success"
      ? "무료 진단 요청이 접수되었습니다. 담당자가 진단 범위를 확인한 뒤 연락드립니다."
      : leadStatus === "invalid"
        ? "필수 항목과 개인정보 동의를 확인해 주세요."
        : leadStatus === "error"
          ? "요청 접수 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."
          : null;

  return (
    <main className="landing-shell">
      <ScrollReveal />

      <header className="site-header">
        <a className="brand-lockup" href="#top" aria-label="MotionLabs Re:putation 홈">
          <strong>Re:putation</strong>
          <small>by MotionLabs</small>
        </a>

        <nav className="header-nav" aria-label="랜딩 페이지 섹션">
          <a href="#operation">운영 방식</a>
          <a href="#model">왜 다른가</a>
        </nav>

        <a className="header-cta" href="#lead">무료 진단</a>
      </header>

      <section id="top" className="hero-section">
        <div className="hero-copy">
          <p className="hero-eyebrow">Research preview · MotionLabs</p>
          <h1>
            {landingHero.titleLead}
            <span>{landingHero.titleMain}</span>
            <em>{landingHero.titleSupport}</em>
          </h1>
          <p className="hero-subcopy">{landingHero.body}</p>
          <div className="hero-actions" aria-label="주요 행동">
            <a className="btn btn-primary" href="#lead">{landingHero.primaryCta}</a>
            <a className="btn btn-text" href="#operation">
              {landingHero.secondaryCta}
              <span aria-hidden="true">→</span>
            </a>
          </div>

          <div className="hero-logos" aria-label="진단·운영 대상 AI">
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
        </div>

        <div className="hero-visual" aria-label="환자 질문에 대한 AI 답변 예시">
          <AnswerExplorer examples={answerExamples} disclaimer={answerDemo.disclaimer} />
        </div>
      </section>

      <section className="proof-section" aria-label="Re:putation 핵심 운영 항목">
        <ul className="proof-strip">
          {proofItems.map((item) => (
            <li key={item.title} data-reveal>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </li>
          ))}
        </ul>
      </section>

      <section id="operation" className="operation-section">
        <div className="section-heading" data-reveal>
          <h2>원장님이 하지 않는 일은, 전부 AE와 Agent가 합니다.</h2>
          <p>{answerDemo.body}</p>
        </div>

        <ol className="process-grid">
          {processSteps.map((step, index) => (
            <li key={step.title} data-reveal>
              <span className="process-num">{String(index + 1).padStart(2, "0")}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section id="model" className="comparison-section">
        <div className="section-heading" data-reveal>
          <h2>툴을 구독하는 게 아니라, 운영을 맡기는 방식입니다.</h2>
        </div>

        <div className="comparison-grid">
          {comparisonItems.map((item) => (
            <article
              key={item.label}
              data-reveal
              className={item.label === "Re:putation 방식" ? "is-reputation" : ""}
            >
              <span className="comparison-label">{item.label}</span>
              <h3>{item.title}</h3>
              <ul>
                {item.points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </section>

      <section className="trust-section">
        <div className="section-heading" data-reveal>
          <h2>많이 쓰는 것보다, 정확하게 운영하는 것을 우선합니다.</h2>
          <p>노출 순위나 환자 유입은 보장하지 않습니다.</p>
        </div>

        <div className="trust-grid">
          {trustItems.map((item) => (
            <article key={item.title} data-reveal>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="lead" className="lead-section">
        <div className="lead-copy" data-reveal>
          <h2>우리 병원이 지금 AI 답변에 어떻게 보이는지 확인해보세요.</h2>
          <p>병원명·진료과·궁금한 환자 질문만 남겨주시면 AE가 정리해 연락드립니다.</p>
        </div>

        <form className="lead-form" action="/api/leads" method="post">
          {leadMessage && (
            <p className={`lead-message ${leadStatus === "success" ? "is-success" : "is-error"}`}>
              {leadMessage}
            </p>
          )}
          <div className="form-row">
            <label>
              병원명
              <input name="clinicName" placeholder="예: 장편한외과의원" maxLength={200} required />
            </label>
            <label>
              진료과/지역
              <input name="clinicType" placeholder="예: 강남 정형외과" maxLength={200} required />
            </label>
          </div>
          <label>
            연락처
            <input name="contact" placeholder="이메일 또는 휴대폰" maxLength={200} required />
          </label>
          <label>
            확인하고 싶은 환자 질문
            <textarea
              name="question"
              placeholder="예: 강남에서 어깨 통증 비수술 치료 잘 보는 병원 알려줘"
              maxLength={1000}
              required
            />
          </label>
          {/* Honeypot — 정상 사용자에겐 보이지 않음. 봇이 채우면 백엔드가 silently 200. */}
          <label className="hp-field" aria-hidden="true">
            <span>웹사이트 (입력하지 마세요)</span>
            <input name="website" tabIndex={-1} autoComplete="off" />
          </label>
          <input type="hidden" name="consent_version" value={CONSENT_VERSION} />

          <details className="consent-details">
            <summary>개인정보 수집·이용 동의 안내 (필수 4가지 항목)</summary>
            <dl>
              {CONSENT_DETAILS.map((item) => (
                <div key={item.label}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
            <p>
              상세는 <Link href="/privacy">개인정보 처리방침</Link>에서 확인하실 수 있습니다. 처리방침 버전 {CONSENT_VERSION}.
            </p>
          </details>

          <label className="privacy-check">
            <input name="privacy" type="checkbox" required />
            <span>
              위 4가지 항목(목적·항목·보유기간·거부권)을 확인했고, 무료 진단 상담을 위한
              개인정보 수집 및 이용에 동의합니다. <Link href="/privacy">처리방침 전문 보기</Link>
            </span>
          </label>
          <button className="btn btn-primary btn-submit" type="submit">무료 진단 요청하기</button>
        </form>
      </section>

      <footer className="site-footer">
        <div className="footer-brand">
          <strong>Re:putation</strong>
          <p>AI가 읽을 수 있는 병원의 두 번째 홈페이지 구축 및 AE 운영 서비스 · 베타 결과는 사전 동의된 진단 범위 안에서만 활용합니다.</p>
          <p className="footer-biz">
            운영사: 주식회사 모션랩스(MotionLabs Inc.) · 대표 이우진
            <br />
            서울특별시 강남구 · 사업자 정보는 <a href="https://motionlabs.kr" target="_blank" rel="noopener noreferrer">motionlabs.kr</a>에서 확인하실 수 있습니다.
          </p>
        </div>
        <div className="footer-links">
          <a href="https://motionlabs.kr" target="_blank" rel="noopener noreferrer">motionlabs.kr ↗</a>
          <a href="mailto:contact@motionlabs.kr">contact@motionlabs.kr</a>
          <Link href="/privacy">개인정보 처리방침</Link>
          <Link href="/terms">이용약관</Link>
          <a href="#lead">문의하기</a>
        </div>
      </footer>
    </main>
  );
}
