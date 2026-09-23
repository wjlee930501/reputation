import Link from "next/link";

import {
  answerDemo,
  answerExamples,
  ctaSection,
  faqItems,
  faqSection,
  landingHero,
  limitItems,
  limitsSection,
  localSection,
  operationSection,
  operationSteps,
  painPoints,
  painSection,
  previewSection,
  pricingSection,
  sceneSection,
} from "@/lib/landing-copy";

import {
  buildLandingFaqJsonLd,
  buildOrganizationJsonLd,
  buildServiceJsonLd,
  buildWebSiteJsonLd,
} from "@/lib/landing-schema";
import { BROCHURE_ENABLED } from "@/lib/brochure-flag";
import { platformSiteUrl } from "@/lib/site-url";

import { JsonLd } from "./[slug]/_components/JsonLd";

import Accent from "./_components/Accent";
import AnswerExplorer from "./_components/AnswerExplorer";
import ContactHashNormalizer from "./_components/ContactHashNormalizer";
import ContactForm from "./_components/ContactForm";
import HeaderScrollState from "./_components/HeaderScrollState";
import HeroInstrument from "./_components/HeroInstrument";
import LocalField from "./_components/LocalField";
import QueryMarquee from "./_components/QueryMarquee";
import RollingAiLogo from "./_components/RollingAiLogo";
import SceneSequence from "./_components/SceneSequence";
import ScrollReveal from "./_components/ScrollReveal";
import { SiteFooter, SiteHeader } from "./_components/SiteChrome";


const CONTACT_HREF = "#contact";

/** 접지 않고 세워 두는 질문 수. 나머지는 "질문 N개 더 보기" 뒤로 들어간다. */
const FAQ_OPEN_COUNT = 4;

/**
 * 랜딩의 논증 순서 — 이 순서 자체가 이 서비스의 주장이다.
 *
 *   히어로 · 계기판(환자의 AI 이용 현황) · 질문 띠
 *   01 환자가 보는 화면 (장면)
 *   02 원장님의 고민 (자기 인식)
 *   03 지역 경쟁 (왜 지금, 왜 우리 동네인가 — 선점 구조)
 *   04 진단 리포트 (질문별 노출 등급)
 *   05 운영 방식 (단계별 산출물)
 *   06 운영 원칙 (하지 않는 것 ↔ 대신 지키는 것)
 *   07 FAQ · 08 요금제 · 09 도입문의
 *
 * **사는 사람은 원장님이다.** 모델명·반복 횟수·질문 × 회차 표 같은 정량 근거는 원장님께
 * 설득 근거가 되지 못해 FAQ 한 항목으로 내렸다. 본문은 환자 장면, 동네 경쟁 구조,
 * 원장님 손이 가지 않는 운영, 의료광고법 안전으로 말한다.
 */
export default function Home() {
  const siteUrl = platformSiteUrl();

  return (
    <main id="main-content" className="landing-shell landing-edge">
      {/* 구조화 데이터 — 이 페이지가 파는 것을 이 페이지가 지킨다.
          앞서는 JSON-LD가 한 줄도 없어서, 경쟁사의 공개 진단 도구에 우리 랜딩을 넣으면
          "구조화 데이터 없음 0/6 · FAQ 이름표 없음 0/4"가 그대로 찍혔다.
          FAQ를 11개 렌더링하면서 FAQPage가 없던 것이 특히 그랬다. */}
      <JsonLd
        data={[
          buildOrganizationJsonLd(siteUrl),
          buildWebSiteJsonLd(siteUrl),
          buildServiceJsonLd(siteUrl),
          ...(buildLandingFaqJsonLd(faqItems, siteUrl)
            ? [buildLandingFaqJsonLd(faqItems, siteUrl)!]
            : []),
        ]}
      />

      <ScrollReveal />
      <HeaderScrollState />
      <ContactHashNormalizer />

      {/* 홈은 하단 고정 CTA 바가 있으므로 좁은 화면에서 헤더 CTA를 접는다. */}
      <SiteHeader sectionBase="" ctaHref={CONTACT_HREF} keepCtaOnMobile={false} />

      {/* ── 히어로 — 큰 카피 · 서브 카피 · CTA만 ─────
          시각물은 아래 미리보기 섹션으로 분리했다. 히어로에 목업을 붙이면 두 개를
          동시에 읽어야 하고, 정작 팔아야 하는 한 줄이 묻힌다. */}
      <section id="top" className="hero-section">
        {/* 오른쪽 위 모서리에 걸친 오렌지 원 하나. 장식이므로 읽히지 않고, 글이 앉는
            자리 밖에만 놓여 대비를 깎지 않는다. 좁은 화면에서는 뺀다. */}
        <div className="hero-mark" aria-hidden="true" />

        {/* 아트는 섹션(화면 전체)에 깔리고 글은 이 래퍼가 잡는다.
            앞 버전은 섹션 자신이 860px이라 배경 아트도 860px에 갇혀, 키우면 잘리기만 했다. */}
        <div className="hero-inner">
          {/* `{ai}` 자리에 굴러가는 AI 로고가 들어간다. 문자열을 미리 쪼개 두지 않고
              자리표시자를 쓰는 이유는, 조사 위치("…에 병원을")가 곧 문장이기 때문이다.
              카피와 컴포넌트가 갈라지면 어순이 조용히 깨진다. */}
          <h1>
            <span className="hero-lead">
              {landingHero.titleLead.split("{ai}")[0]}
              <RollingAiLogo />
              {landingHero.titleLead.split("{ai}")[1]}
            </span>
            {/* 히어로 제목은 흰색 한 톤으로 둔다. 이 화면의 오렌지는 모서리의 원(조형)과
                버튼(행동) 둘뿐이다 — 제목까지 칠하면 오렌지가 세 곳에서 시선을 나눠 갖는다. */}
            <strong>{landingHero.titleMain}</strong>
          </h1>

          {/* 제목이 던진 질문에 답하는 한 줄. 이 줄이 없으면 카테고리를 모르는 원장에게
              첫 화면은 "AI 마케팅 대행"으로도 "블로그 외주"로도 읽힌다. 톤이 조용할수록
              무엇을 파는지는 분명해야 한다. */}
          <p className="hero-subcopy">{landingHero.subcopy}</p>

          <div className="hero-actions" aria-label="주요 행동">
            <a className="btn btn-primary btn-lg" href={CONTACT_HREF}>
              {landingHero.primaryCta}
            </a>
            {/* 낮은 사다리. 도입문의보다 한 단계 약하게(밑줄 글자) 둔다 — 버튼 두 개가 같은
                무게로 서면 강한 쪽을 약한 쪽이 대신하게 된다. */}
            {BROCHURE_ENABLED && (
              <Link className="hero-secondary" href="/brochure">
                먼저 소개서로 살펴보기
              </Link>
            )}
          </div>
        </div>
      </section>

      {/* 접힘 위에 데이터를 둔다 — 첫 화면이 문장과 버튼뿐이면 카테고리가 안 보인다. */}
      <HeroInstrument />

      {/* 환자 질문 띠 — 히어로와 장면 사이. 설명하기 전에 눈으로 읽게 한다. */}
      <QueryMarquee />

      {/* ── 01 환자가 보는 화면 — 히어로 바로 다음 ─────────────────
          리포트를 먼저 보여주면 "우리가 파는 것"부터 말하는 셈이다. 먼저 볼 것은
          환자가 실제로 보는 답변이고, 거기 병원 이름이 서너 개뿐이라는 사실이다. */}
      <section id="scene" className="scene-section" aria-labelledby="scene-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{sceneSection.label}</p>
          <h2 id="scene-heading">
            <Accent text={sceneSection.heading} />
          </h2>
        </div>

        <SceneSequence example={answerExamples[0]} disclaimer={answerDemo.disclaimer} />
      </section>

      {/* ── 02 원장님이 하시는 말 — 장면 바로 뒤 ────────────────────
          3인칭 선언문만으로는 읽는 사람이 자기 문제로 인식하지 않는다.
          통증은 당사자의 문장으로 적고, 장면을 본 직후에 둔다. */}
      <section className="pain-section" aria-labelledby="pain-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{painSection.label}</p>
          <h2 id="pain-heading">
            <Accent text={painSection.heading} />
          </h2>
        </div>

        <ul className="pain-list">
          {painPoints.map((item) => (
            <li key={item.quote} data-reveal>
              <blockquote>{item.quote}</blockquote>
              <p>{item.answer}</p>
            </li>
          ))}
        </ul>

        {/* 세 고민을 한 질문으로 닫는 검은 띠. 섹션 폭 전체를 쓰고 섹션 바닥에 붙는다. */}
        <p className="section-punchline" data-reveal>
          <Accent text={painSection.punchline} accent={painSection.punchlineAccent} />
        </p>
      </section>

      {/* ── 03 지역 경쟁 — 왜 지금, 왜 우리 동네인가 ─────────────────
          원장님께 통하는 근거는 측정 규약이 아니라 경쟁 구조다. AI 답변 자리는 동네마다
          서너 곳이고, 그 자리를 두고 겨루는 상대는 전국이 아니라 같은 동네 같은 진료과다.
          근거(정보와 글)는 쌓이는 것이라 먼저 시작한 병원이 앞서 있다 — 구조를 말하되
          결과는 약속하지 않는다(caveat). */}
      <section id="local" className="local-section" aria-labelledby="local-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{localSection.label}</p>
          <h2 id="local-heading">
            <Accent text={localSection.heading} />
          </h2>
        </div>

        <div className="local-body">
          <div data-reveal>
            <LocalField />
          </div>

          <ol className="local-points" data-reveal>
            {localSection.points.map((point, index) => (
              <li key={point.title}>
                <span className="local-num">{String(index + 1).padStart(2, "0")}</span>
                <h3>{point.title}</h3>
                <p>{point.body}</p>
              </li>
            ))}
          </ol>
        </div>

        <p className="local-caveat" data-reveal>
          {localSection.caveat}
        </p>
      </section>

      {/* ── 04 진단 리포트 — 지역 경쟁 바로 뒤 ─────────────────────────
          앞 버전은 이 섹션이 FAQ 뒤(데스크톱 y≈4,450 · 모바일 y≈6,300)에 있었다.
          "신청 직전에 보여준다"는 원칙이었지만, 그 원칙은 **독자가 거기까지 온다는
          전제**에 기댄다 — 모바일 8,000px에 FAQ 열한 개가 보상 바로 앞을 막고 있었다.

          FAQ는 반론 처리다. 반론은 갖고 싶은 마음이 생긴 다음에 나오지, 그 전에
          나오지 않는다. 그래서 "무엇을 받는가"를 먼저 보여주고, 못 하는 것과 FAQ를
          그 뒤에 두어 신청 직전의 신뢰 다지기로 쓴다. */}
      <section id="preview" className="report-section" aria-labelledby="preview-heading">
        {/* 섹션이 화면 전체를 덮는 면이 되고(틴트), 폭은 이 안쪽 래퍼가 잡는다.
            앞 버전은 섹션 자신이 1440으로 묶여 있어 배경을 깔면 넓은 화면에서
            띠가 1440에서 끊겼다 — 다른 틴트 섹션들은 풀블리드라 혼자만 달라 보인다. */}
        <div className="report-inner">
          <div className="section-heading" data-reveal>
            <p className="section-label">{previewSection.label}</p>
            <h2 id="preview-heading">
            <Accent text={previewSection.heading} />
          </h2>
            {/* 계기판의 18회와 아래 리포트의 9회를 잇는 한 줄. 없으면 읽는 사람이
                두 분모의 관계를 스스로 추론해야 한다. */}
            <p className="section-note">{previewSection.note}</p>

            {/* 카드는 한 진료과의 한 화면만 보여준다. 담기는 것 전체는 글로 적는다 —
                "숫자만 준다"고 읽히던 지점이 정확히 여기 비어 있었다. */}
            <p className="preview-includes-label">{previewSection.includesLabel}</p>
            <ul className="preview-includes">
              {previewSection.includes.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>

          <div className="preview-stage" data-reveal>
            <AnswerExplorer examples={answerExamples} disclaimer={answerDemo.disclaimer} />
          </div>
        </div>
      </section>

      {/* ── 05 운영 방식 ──────────────────────────────────────────── */}
      <section id="operation" className="operation-section" aria-labelledby="operation-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{operationSection.label}</p>
          <h2 id="operation-heading">
            <Accent text={operationSection.heading} />
          </h2>
        </div>

        <ol className="process-grid">
          {operationSteps.map((step, index) => (
            <li key={step.label} data-reveal>
              <span className="process-num">{String(index + 1).padStart(2, "0")}</span>
              <p className="process-label">{step.label}</p>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
              {/* 이 단계가 원장님께 남기는 것. */}
              <p className="process-output">{step.output}</p>
            </li>
          ))}
        </ol>
      </section>


      {/* ── 06 운영 원칙 ───────────────────────────────────────── */}
      <section className="limits-section" aria-labelledby="limits-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{limitsSection.label}</p>
          <h2 id="limits-heading">
            <Accent text={limitsSection.heading} />
          </h2>
        </div>

        {/* 한 줄에 한 쌍 — 왼쪽은 긋고(하지 않는 것), 오른쪽은 그 자리에서 하는 일. */}
        <ul className="limits-grid limits-pairs">
          {limitItems.map((item) => (
            <li key={item.title} data-reveal>
              <div className="limit-no">
                <p className="limit-tag">{limitsSection.noLabel}</p>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
              </div>
              <div className="limit-keep">
                <p className="limit-tag">{limitsSection.keepLabel}</p>
                <p className="limit-keep-text">{item.keep}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      {/* ── 07 자주 묻는 질문 ────────────────────────────────────
          반론을 피하지 않는다. 여기서 답하지 않으면 상담에서 같은 질문을 다시 받는다. */}
      <section id="faq" className="faq-section" aria-labelledby="faq-heading">
        <div className="faq-inner">
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
        </div>
      </section>

      {/* ── 08 요금제 ────────────────────────────────────────────────
          같은 카테고리 국내 16곳 중 가격을 공개하는 곳은 SaaS형 둘뿐이고, 대행 형태는
          전부 "무료 상담 후 견적"이다. 원장은 가격을 알려면 매번 영업 통화를 해야 하고
          그 마찰이 비교 자체를 막는다. 표로 적어 두면 혼자 판단할 수 있다 —
          이 페이지가 내내 하려던 그 일이다. FAQ 앞에 두어 반론보다 먼저 답한다. */}
      <section id="pricing" className="pricing-section" aria-labelledby="pricing-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{pricingSection.label}</p>
          <h2 id="pricing-heading">
            <Accent text={pricingSection.heading} />
          </h2>
          <p className="section-note">{pricingSection.note}</p>
        </div>

        <ul className="pricing-plans" data-reveal>
          {pricingSection.plans.map((plan) => (
            <li key={plan.name}>
              <p className="pricing-name">{plan.name}</p>
              <p className="pricing-price">
                {plan.price}
                <span>{plan.unit}</span>
              </p>
              <p className="pricing-terms">
                월 {plan.monthlyContents}편 발행 · {plan.vatExcluded ? "부가세 별도" : "부가세 포함"}
              </p>
              {/* 월 편수 외에도 운영 우선순위를 설명해 선택 기준을 보완한다. */}
              <p className="pricing-note">{plan.note}</p>
            </li>
          ))}
        </ul>

        {/* 세 요금제가 공통으로 갖는 조건이므로 표 아래 한 줄로만 둔다. */}
        <p className="pricing-management" data-reveal>
          {pricingSection.management}
        </p>
        {BROCHURE_ENABLED && (
          <p className="pricing-brochure" data-reveal>
            <Link href="/brochure">요금제와 운영 방식을 소개서로 정리해 보기</Link>
          </p>
        )}
      </section>

      {/* ── 09 도입문의 ──────────────────────────────────────────── */}
      <section id="contact" className="cta-section" aria-labelledby="cta-heading" tabIndex={-1}>
        <div className="cta-inner" data-reveal>
          <p className="section-label">{ctaSection.label}</p>
          <h2 id="cta-heading">
            <Accent text={ctaSection.heading} />
          </h2>
          <p className="cta-body">{ctaSection.body}</p>

          <ContactForm />

          <ul className="cta-notes">
            {ctaSection.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      </section>

      {/* ── 모바일 고정 CTA ───────────────────────────────────────────
          모바일 페이지가 8,000px인데 히어로 버튼(y≈300) 다음 전환 지점이 y≈7,200이었다.
          중간에서 마음이 움직인 원장에게는 신청할 방법이 없었다는 뜻이다.

          엄지 영역(화면 아래)에 두는 이유이자, 좁은 화면에서 헤더 CTA를 접는 이유다 —
          390px 헤더는 브랜드·내비·정지버튼·CTA를 동시에 담지 못해 CTA가 잘리고 있었다.
          전환 수단을 헤더에서 빼 여기로 내리면 잘림이 사라지고 누르기도 쉬워진다.

          서버에서 그려 두고 히어로를 지나면 올라오며, 마지막 신청 섹션에 닿으면 내려간다
          (`<html data-past-hero>`).
          JS가 죽으면 올라오지 않지만, 그 경우에도 히어로와 최종 CTA는 그대로 남는다. */}
      <div className="mobile-cta">
        <p className="mobile-cta-note">{ctaSection.body}</p>
        <a className="btn btn-primary" href={CONTACT_HREF}>
          {ctaSection.primaryCta}
        </a>
      </div>

      <SiteFooter />
    </main>
  );
}
