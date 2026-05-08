import Image from "next/image";
import Link from "next/link";

const CONSENT_VERSION = "v1.2026-05";

const CONSENT_DETAILS = [
  { label: "수집 목적", value: "AI 노출 진단 범위 확인 및 진단 상담 안내" },
  { label: "수집 항목", value: "병원명, 진료과/지역, 연락처(이메일 또는 전화), 확인하고 싶은 환자 질문" },
  { label: "보유 기간", value: "수집일로부터 180일 이내 자동 파기. 상담 종료 시 즉시 파기 가능" },
  { label: "거부 권리", value: "동의를 거부할 수 있으며, 거부 시 무료 진단 상담만 진행되지 않고 다른 불이익은 없습니다" },
];

const proofItems = [
  {
    title: "ChatGPT·Gemini 확인",
    body: "환자 질문별 AI 답변에서 병원이 어떤 맥락으로 보이는지 점검합니다.",
  },
  {
    title: "의료광고법 리스크 고려",
    body: "표현을 키우기보다 과장·비교·보장 표현 가능성을 함께 확인합니다.",
  },
  {
    title: "진료과별 환자 질문 설계",
    body: "지역, 증상, 치료 선택처럼 실제 상담 전 질문을 기준으로 진단합니다.",
  },
  {
    title: "월간 원장님용 리포트",
    body: "답변 변화, 빠진 정보, 다음 보완 항목을 원장님 보고용으로 정리합니다.",
  },
];

const comparisonItems = [
  {
    label: "기존 방식",
    title: "홈페이지·블로그를 만들고 기다립니다.",
    points: [
      "검색 노출 지표만 보고 AI 답변 안의 맥락은 확인하기 어렵습니다.",
      "병원 강점이 환자 질문 문장과 연결되지 않은 채 흩어져 있습니다.",
      "콘텐츠 발행 후 어떤 정보가 부족한지 다음 운영 기준이 모호합니다.",
    ],
  },
  {
    label: "Re:putation 방식",
    title: "AI 답변을 먼저 진단하고 운영 순서를 정합니다.",
    points: [
      "ChatGPT·Gemini 답변에서 병원명, 설명, 누락 정보를 확인합니다.",
      "진료 강점, 의료진 설명, 근거 자료를 환자 질문 기준으로 재정리합니다.",
      "매달 확인 질문과 보완 콘텐츠를 보고서로 남겨 다음 운영에 연결합니다.",
    ],
  },
];

const processSteps = [
  {
    title: "질문 설정",
    body: "원장님이 잡고 싶은 진료 영역과 실제 환자 문의를 바탕으로 AI 확인 질문을 구성합니다.",
  },
  {
    title: "AI 답변 확인",
    body: "ChatGPT·Gemini가 어떤 병원을 언급하고 어떤 기준으로 설명하는지 확인합니다.",
  },
  {
    title: "빠진 정보 진단",
    body: "진료 강점, 의료진 이력, 장비·검사·시술 설명, 근거 콘텐츠의 빈틈을 찾습니다.",
  },
  {
    title: "보완 콘텐츠 운영",
    body: "광고성 문구보다 환자 질문에 답하는 정보와 출처 기반 콘텐츠를 우선 정리합니다.",
  },
  {
    title: "월간 리포트",
    body: "이번 달 변화와 다음 달 운영 항목을 원장님이 바로 볼 수 있는 형태로 제공합니다.",
  },
];

const specialtyCards = [
  {
    tag: "정형외과·통증",
    question: "어깨 통증 비수술 치료는 어느 병원에서 상담해야 할까?",
    focus: "증상·검사·비수술 치료 설명과 원장님 진료 관점을 연결합니다.",
  },
  {
    tag: "내과·검진",
    question: "위내시경과 대장내시경을 같이 받을 병원은 어디가 좋을까?",
    focus: "검진 프로세스, 장비, 준비 안내, 사후 관리 콘텐츠를 점검합니다.",
  },
  {
    tag: "산부인과",
    question: "생리불순이나 여성검진은 어떤 기준으로 병원을 골라야 할까?",
    focus: "민감한 진료의 상담 흐름과 신뢰 자료가 충분히 설명되는지 봅니다.",
  },
  {
    tag: "가정의학과/로컬 의원",
    question: "동네에서 지속적으로 관리받을 만한 의원을 찾고 싶어.",
    focus: "지역성, 만성질환 관리, 예방접종·수액·검사 안내의 누락을 확인합니다.",
  },
];

const trustItems = [
  {
    title: "개인정보 최소화",
    body: "진단에는 공개 정보와 사용자가 제공한 확인 질문을 중심으로 사용합니다.",
  },
  {
    title: "의료광고 리스크 검토",
    body: "순위 보장, 치료 결과 보장, 과도한 비교 표현을 전제로 운영하지 않습니다.",
  },
  {
    title: "근거 자료 중심",
    body: "AI가 이해할 수 있는 공개 자료, 병원 안내, 진료 설명의 연결성을 봅니다.",
  },
  {
    title: "원장 보고용 리포트",
    body: "실무 체크리스트가 아니라 의사결정에 필요한 요약과 다음 액션을 제공합니다.",
  },
];

export default function Home({
  searchParams,
}: {
  searchParams?: { lead?: string };
}) {
  const leadStatus = searchParams?.lead;
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
      <header className="site-header">
        <a className="brand-lockup" href="#top" aria-label="MotionLabs Re:putation 홈">
          <span className="brand-mark" aria-hidden="true">R</span>
          <span>
            <strong>Re:putation</strong>
            <small>by MotionLabs</small>
          </span>
        </a>

        <nav className="header-nav" aria-label="랜딩 페이지 섹션">
          <a href="#diagnosis">진단 방식</a>
          <a href="#operation">월간 운영</a>
          <a href="#specialty">진료과</a>
          <a href="#lead">상담 신청</a>
        </nav>

        <a className="header-cta" href="#lead">무료 진단 요청</a>
      </header>

      <section id="top" className="hero-section">
        <div className="hero-copy">
          <p className="section-eyebrow">AI EXPOSURE DIAGNOSIS FOR CLINICS</p>
          <h1>
            환자는 이제 AI에게
            <span>병원을 묻습니다.</span>
            <em>우리 병원은 그 답변 안에 제대로 보이고 있을까요?</em>
          </h1>
          <p className="hero-subcopy">
            Re:putation은 홈페이지 제작이나 일반 블로그 대행이 아닙니다. ChatGPT·Gemini가
            환자 질문에 답할 때 우리 병원이 어떻게 보이는지 진단하고, 빠진 정보와 근거
            콘텐츠 운영 순서를 정리합니다.
          </p>
          <div className="hero-actions" aria-label="주요 행동">
            <a className="btn btn-primary" href="#lead">우리 병원 AI 노출 진단받기</a>
            <a className="btn btn-secondary" href="#diagnosis">진단 방식 보기</a>
          </div>
          <div className="hero-note">
            <strong>순위 보장 대신</strong>
            <span>AI가 참고할 수 있는 정보의 빈틈을 확인하고 매달 보완합니다.</span>
          </div>
        </div>

        <div className="product-visual" aria-label="Re:putation AI 노출 진단 화면 예시">
          <div className="visual-frame">
            <Image
              src="/landing/reputation-clinic-ai-dashboard.png"
              alt="병원 AI 노출 진단 대시보드와 진료 콘텐츠 운영 화면 예시"
              width={1536}
              height={1024}
              priority
              sizes="(max-width: 960px) 100vw, 48vw"
            />
            <div className="visual-chip visual-chip-left">
              <span>누락 정보</span>
              <strong>의료진 설명 · 검사 흐름</strong>
            </div>
            <div className="visual-chip visual-chip-right">
              <span>답변 표면</span>
              <strong>ChatGPT · Gemini</strong>
            </div>
            <div className="diagnosis-overlay" aria-label="AI 노출 진단 리포트 예시">
              <div className="overlay-head">
                <span>AI 답변 노출 진단서</span>
                <strong>월간 리포트</strong>
              </div>
              <dl>
                <div>
                  <dt>확인 질문</dt>
                  <dd>압구정 어깨 통증 비수술 치료 병원</dd>
                </div>
                <div>
                  <dt>현재 답변</dt>
                  <dd>주변 병원은 언급되지만 우리 병원 설명은 부족</dd>
                </div>
                <div>
                  <dt>다음 보완</dt>
                  <dd>진료 철학, 검사 흐름, 치료 기준 콘텐츠 정리</dd>
                </div>
              </dl>
            </div>
          </div>
        </div>
      </section>

      <section className="proof-strip" aria-label="Re:putation 핵심 확인 항목">
        {proofItems.map((item) => (
          <article key={item.title}>
            <h2>{item.title}</h2>
            <p>{item.body}</p>
          </article>
        ))}
      </section>

      <section id="diagnosis" className="section comparison-section">
        <div className="section-heading">
          <p className="section-eyebrow">DIAGNOSIS MODEL</p>
          <h2>콘텐츠를 더 쓰기 전에, AI 답변에서 빠지는 이유부터 봅니다.</h2>
          <p>
            병원 홍보 문구를 늘리는 방식으로는 AI 답변의 빈틈을 확인하기 어렵습니다.
            Re:putation은 환자 질문을 기준으로 현재 답변과 부족한 근거를 먼저 진단합니다.
          </p>
        </div>

        <div className="comparison-grid">
          {comparisonItems.map((item) => (
            <article key={item.label} className={item.label === "Re:putation 방식" ? "is-reputation" : ""}>
              <span>{item.label}</span>
              <h3>{item.title}</h3>
              <ul>
                {item.points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </article>
          ))}
        </div>

        <div className="photo-insight-card">
          <div className="photo-copy">
            <p className="section-eyebrow">REPORT-LED OPERATION</p>
            <h3>병원 공간보다 중요한 건, AI가 참고할 수 있는 진료 정보의 구조입니다.</h3>
            <p>
              진단 리포트, 질문별 답변 변화, 보완 콘텐츠 체크리스트를 한 화면에서 보며
              다음 달 운영 우선순위를 정합니다.
            </p>
          </div>
          <div className="photo-frame">
            <Image
              src="/landing/reputation-product-report-devices.png"
              alt="사람 없는 병원 SaaS 리포트 대시보드 기기 실사 이미지"
              width={1536}
              height={1024}
              sizes="(max-width: 960px) 100vw, 44vw"
            />
            <div className="photo-badge">
              <strong>월간 리포트</strong>
              <span>답변 변화 · 누락 정보 · 다음 액션</span>
            </div>
          </div>
        </div>
      </section>

      <section id="operation" className="operation-section">
        <div className="section-heading">
          <p className="section-eyebrow">MONTHLY OPERATION</p>
          <h2>AI 노출 진단은 한 번의 점검이 아니라 월간 운영 기준입니다.</h2>
          <p>
            질문 설정부터 리포트까지 같은 흐름으로 반복해 우리 병원의 정보가 환자 질문에
            더 잘 대응하도록 정리합니다.
          </p>
        </div>

        <div className="process-grid">
          {processSteps.map((step, index) => (
            <article key={step.title}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="specialty" className="section specialty-section">
        <div className="section-heading">
          <p className="section-eyebrow">SPECIALTY QUESTION SET</p>
          <h2>진료과마다 환자가 AI에게 묻는 문장이 다릅니다.</h2>
          <p>
            정형외과, 내과, 산부인과, 로컬 의원은 같은 “좋은 병원”이라도 AI가 참고해야 할
            정보 구조가 다릅니다.
          </p>
        </div>

        <div className="specialty-tabs" aria-label="진료과 탭 예시">
          {specialtyCards.map((card) => (
            <span key={card.tag}>{card.tag}</span>
          ))}
        </div>

        <div className="specialty-grid">
          {specialtyCards.map((card) => (
            <article key={card.tag}>
              <span>{card.tag}</span>
              <h3>{card.question}</h3>
              <p>{card.focus}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="trust-section">
        <div className="trust-copy">
          <p className="section-eyebrow">TRUST & RISK CHECK</p>
          <h2>의료 서비스답게, 보이는 것보다 지켜야 할 기준을 먼저 봅니다.</h2>
          <p>
            Re:putation은 AI 답변 노출을 진단하지만 노출 순위나 환자 유입을 보장하지
            않습니다. 공개 정보의 정합성, 의료광고 리스크, 원장님 보고에 필요한 판단
            자료를 차분하게 정리합니다.
          </p>
          <div className="trust-photo">
            <Image
              src="/landing/reputation-clinic-trust-interior.png"
              alt="사람 없는 프리미엄 병원 리셉션과 데이터 리포트 화면 실사 이미지"
              width={1536}
              height={1024}
              sizes="(max-width: 960px) 100vw, 36vw"
            />
            <div className="photo-badge trust-badge">
              <strong>리스크 체크</strong>
              <span>공개 정보 · 표현 기준 · 보고 자료</span>
            </div>
          </div>
        </div>
        <div className="trust-grid">
          {trustItems.map((item) => (
            <article key={item.title}>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="lead" className="lead-section">
        <div className="lead-panel">
          <div className="lead-copy">
            <p className="section-eyebrow">FREE DIAGNOSIS REQUEST</p>
            <h2>우리 병원이 AI 답변에서 어떻게 보이는지 먼저 확인해보세요.</h2>
            <p>
              병원명과 확인하고 싶은 환자 질문을 남겨주시면 1차 진단 범위를 정리해
              연락드립니다. 입력하신 정보는 진단 범위 확인과 상담 안내 목적으로만 사용됩니다.
            </p>
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
            <button className="btn btn-submit" type="submit">무료 진단 요청하기</button>
          </form>
        </div>
      </section>

      <footer className="site-footer">
        <div>
          <strong>Re:putation</strong>
          <p>AI 노출 진단 및 병원 콘텐츠 운영 서비스</p>
          <p className="footer-biz">
            운영사: 주식회사 모션랩스(MotionLabs Inc.) · 대표 이우진
            <br />
            서울특별시 강남구 테헤란로 · 사업자등록번호 등록 예정
          </p>
        </div>
        <div className="footer-links">
          <a href="mailto:contact@motionlabs.kr">contact@motionlabs.kr</a>
          <Link href="/privacy">개인정보 처리방침</Link>
          <Link href="/terms">이용약관</Link>
          <a href="#lead">문의하기</a>
        </div>
      </footer>
    </main>
  );
}
