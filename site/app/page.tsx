import Link from "next/link";

import {
  answerDemo,
  answerExamples,
  ctaSection,
  faqItems,
  faqSection,
  funnelSection,
  landingHero,
  limitItems,
  limitsSection,
  marketSection,
  operationSection,
  operationSteps,
  painPoints,
  painSection,
  platformShareSection,
  platformShares,
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
import { platformSiteUrl } from "@/lib/site-url";

import { JsonLd } from "./[slug]/_components/JsonLd";

import AnswerExplorer from "./_components/AnswerExplorer";
import LiveDiagnosisQuota from "./_components/DiagnosisQuota";
import HeaderScrollState from "./_components/HeaderScrollState";
import HeroInstrument from "./_components/HeroInstrument";
import MotionToggle from "./_components/MotionToggle";
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
 *   ① 환자가 보는 화면은 이렇다 (장면)
 *   ② 그게 내 얘기다 (원장의 말 — 자기 인식)
 *   ③ 그래서 무엇을 받는가 (리포트 실물 + 담기는 것)
 *   ④ 그 답변은 어디서 일어나는가 (점유율 · 비용 판단)
 *   ⑤ 그래서 언제 시작해야 하는가 (자리는 서너 곳 · 시점)
 *   ⑥ 그 자리를 만들려면 무엇이 필요한가 (운영 방식)
 *   ⑦ 무엇은 못 하는가 (하지 않는 것 → FAQ → 요금제)
 *   ⑧ 그래서 지금 확인해보라 (무료 진단)
 *
 * **③은 원래 ⑥이었다**(운영 방식 뒤, 문서 한가운데 y≈4,000). "신청 직전에 보여준다"는
 * 배치였는데, 외부 검토에서 "이름을 세어 숫자만 주는 것 같다"는 말이 나왔다 — 산출물이
 * 절반을 지나야 나오니 그 전에 판단이 끝난 것이다. 받는 것을 통증 바로 뒤로 올리고,
 * 신청 직전 신뢰 다지기는 ⑦(하지 않는 것 → FAQ)이 그대로 맡는다.
 *
 * ⑤를 ①②③ 앞에 두면 기능 소개가 되고, ⑦을 빼면 노출 대행과 구별되지 않는다.
 *
 * **②는 원래 ③ 뒤에 있었다.** 근거(점유율)를 먼저 깔고 통증을 나중에 꺼내는 순서였는데,
 * 그러면 원장이 자기 문제로 인식하기 전에 남의 숫자부터 읽게 된다. 장면을 본 직후가
 * "이거 우리 얘기네"가 가장 크게 울리는 자리이므로, 자기 인식을 근거 앞으로 올린다.
 *
 * ④는 원래 FAQ **뒤**에 있었다("신청 직전에 보여준다"). 그 원칙은 독자가 거기까지
 * 온다는 전제에 기대는데, 모바일 8,000px에서 리포트 카드가 y≈6,300이었고 그 앞을
 * FAQ 열한 개가 막고 있었다. 반론(⑤)은 갖고 싶은 마음이 생긴 뒤에 나오므로,
 * 받는 것을 먼저 보여주고 못 하는 것과 FAQ를 신청 직전 신뢰 다지기로 쓴다.
 */
export default function Home() {
  const siteUrl = platformSiteUrl();

  return (
    <main id="main-content" className="landing-shell">
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

        {/* 내비 밖에 둔다 — `.header-nav`는 600px 이하에서 통째로 숨는데, 좁은 화면에서
            멈춤 수단이 사라지면 이 버튼의 존재 이유가 없어진다. */}
        <MotionToggle />

        <Link className="header-cta" href={DIAGNOSIS_PATH}>
          무료 진단
        </Link>
      </header>

      {/* ── ① 히어로 — 큰 카피 · 서브 카피 · CTA · 선착순 고지만 ─────
          시각물은 아래 미리보기 섹션으로 분리했다. 히어로에 목업을 붙이면 두 개를
          동시에 읽어야 하고, 정작 팔아야 하는 한 줄이 묻힌다. */}
      <section id="top" className="hero-section">


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
            <strong>{landingHero.titleMain}</strong>
          </h1>

          {/* 제목이 던진 질문에 답하는 한 줄. 이 줄이 없으면 카테고리를 모르는 원장에게
              첫 화면은 "AI 마케팅 대행"으로도 "블로그 외주"로도 읽힌다. 톤이 조용할수록
              무엇을 파는지는 분명해야 한다. */}
          <p className="hero-subcopy">{landingHero.subcopy}</p>

          <div className="hero-actions" aria-label="주요 행동">
            <Link className="btn btn-primary btn-lg" href={DIAGNOSIS_PATH}>
              {landingHero.primaryCta}
            </Link>
          </div>

          <LiveDiagnosisQuota variant="hero" />
        </div>
      </section>

      {/* 접힘 위에 데이터를 둔다 — 첫 화면이 문장과 버튼뿐이면 카테고리가 안 보인다. */}
      <HeroInstrument />

      {/* 환자 질문 띠 — 히어로와 장면 사이. 설명하기 전에 눈으로 읽게 한다. */}
      <QueryMarquee />

      {/* ── ② 환자가 보는 화면 — 히어로 바로 다음 ─────────────────
          리포트를 먼저 보여주면 "우리가 파는 것"부터 말하는 셈이다. 먼저 볼 것은
          환자가 실제로 보는 답변이고, 거기 병원 이름이 서너 개뿐이라는 사실이다. */}
      <section id="scene" className="scene-section" aria-labelledby="scene-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{sceneSection.label}</p>
          <h2 id="scene-heading">{sceneSection.heading}</h2>
        </div>

        <SceneSequence example={answerExamples[0]} disclaimer={answerDemo.disclaimer} />
      </section>

      {/* ── ② 원장님이 하시는 말 — 장면 바로 뒤 ────────────────────
          3인칭 선언문만으로는 읽는 사람이 자기 문제로 인식하지 않는다.
          통증은 당사자의 문장으로 적고, 장면을 본 직후에 둔다. */}
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

      {/* ── ④ 받으시는 것 — 운영 방식 바로 뒤 ─────────────────────────
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
            <h2 id="preview-heading">{previewSection.heading}</h2>
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

      <section id="numbers" className="market-section" aria-labelledby="market-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{marketSection.label}</p>
          <h2 id="market-heading">{marketSection.heading}</h2>
        </div>

        {/* 이 섹션은 이제 "왜 두 곳만 재는가"에만 답한다. 차트가 본문이다. */}
        <div className="share-block" data-reveal>
          <p className="share-nudge">{platformShareSection.nudge}</p>
          <PlatformShareChart
            shares={platformShares}
            sourceNote={platformShareSection.sourceNote}
          />
        </div>
      </section>

      {/* ── ②-b 우리가 서 있는 자리 ───────────────────────────────
          원장은 이미 블로그·플레이스에 돈을 쓰고 있고, 그 시장은 "노출은 성과가 아니다"라는
          자기비판을 이미 끝냈다. 그 한가운데에 노출보다 더 상류인 지표를 들고 가면
          "순위 올려준다던 곳이랑 뭐가 다르냐"로 먼저 읽힌다.
          대체재가 아니라 상류 보완재라는 것을 그림으로 먼저 못 박는다. */}
      <section className="funnel-section" aria-labelledby="funnel-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{funnelSection.label}</p>
          <h2 id="funnel-heading">{funnelSection.heading}</h2>
          <p className="section-note">{funnelSection.body}</p>
        </div>

        <div className="slot-chart" data-reveal>
          {/* 실측값을 그대로 그린 그림 — 답변 한 건에 병원이 서너 곳 적힌다.
              마지막 칸은 비어 있다고 쓰지 않고 물음표를 둔다. 자리가 남아 있다는 것은
              우리가 재지 않은 사실이고, 물음표는 이 페이지가 내내 던진 질문이다. */}
          <ol className="slot-row">
            {funnelSection.slots.map((slot, index) => (
              <li key={slot.name} data-ours={slot.ours ? "yes" : "no"}>
                <span className="slot-index">{index + 1}</span>
                <span className="slot-name">{slot.name}</span>
              </li>
            ))}
          </ol>

          <p className="slot-caption">{funnelSection.slotsCaption}</p>

          <div className="slot-legend">
            <p className="slot-legend-ours">{funnelSection.oursNote}</p>
            <p className="slot-legend-rest">{funnelSection.restNote}</p>
          </div>

          {/* 그림이 성과 약속으로 읽히지 않게 잠그는 줄. */}
          <p className="slot-caveat">{funnelSection.caveat}</p>
        </div>
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
              <p>{step.body}</p>
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

      {/* ── 요금제 ────────────────────────────────────────────────
          같은 카테고리 국내 16곳 중 가격을 공개하는 곳은 SaaS형 둘뿐이고, 대행 형태는
          전부 "무료 상담 후 견적"이다. 원장은 가격을 알려면 매번 영업 통화를 해야 하고
          그 마찰이 비교 자체를 막는다. 표로 적어 두면 혼자 판단할 수 있다 —
          이 페이지가 내내 하려던 그 일이다. FAQ 앞에 두어 반론보다 먼저 답한다. */}
      <section id="pricing" className="pricing-section" aria-labelledby="pricing-heading">
        <div className="section-heading" data-reveal>
          <p className="section-label">{pricingSection.label}</p>
          <h2 id="pricing-heading">{pricingSection.heading}</h2>
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
              <p className="pricing-terms">{plan.vatExcluded ? "부가세 별도" : "부가세 포함"}</p>
              <p className="pricing-management">{plan.management}</p>
              {/* 고를 근거. 편수가 아니라 우선순위로 나눈다 — 원장이 이미 알고 있는
                  자기 상태가 곧 선택 기준이 된다. */}
              <p className="pricing-note">{plan.note}</p>
            </li>
          ))}
        </ul>
      </section>

      {/* ── ⑥ 무료 진단 ──────────────────────────────────────────── */}
      <section id="lead" className="cta-section" aria-labelledby="cta-heading">
        <div className="cta-inner" data-reveal>
          <p className="section-label">{ctaSection.label}</p>
          <h2 id="cta-heading">{ctaSection.heading}</h2>
          <p className="cta-body">{ctaSection.body}</p>

          <LiveDiagnosisQuota variant="cta" />

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
        <Link className="btn btn-primary" href={DIAGNOSIS_PATH}>
          {ctaSection.primaryCta}
        </Link>
      </div>

      <footer className="site-footer">
        <div className="footer-brand">
          <strong>Re:putation</strong>
          <p>
            병원 정보를 AI가 읽을 수 있는 형태로 정리하고, 근거 기반 콘텐츠를 매달 발행하는
            AI 노출 컨설팅·콘텐츠 운영 서비스입니다.
          </p>
          {/* 사업자 정보는 링크로 미루지 않고 여기 적는다. 앞 버전은 "사업자 정보는
              motionlabs.kr에서 확인하실 수 있습니다"로 넘겼는데, 그러면 사람도 한 번 더
              눌러야 하고 기계는 아예 못 읽는다(E-E-A-T 신호 누락).
              주소도 틀려 있었다 — "강남구"로 적혀 있었지만 운영사 등기 주소는 성동구다. */}
          <p className="footer-biz">
            운영사: 주식회사 모션랩스(MotionLabs Inc.) · 대표 이우진
            <br />
            사업자등록번호 466-88-01551 · 서울특별시 성동구 아차산로 38, 406호
            <br />
            <a href="https://motionlabs.kr" target="_blank" rel="noopener noreferrer">
              motionlabs.kr
            </a>
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
