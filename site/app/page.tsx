const processSteps = [
  {
    eyebrow: '01',
    title: '환자가 실제로 묻는 질문을 정합니다',
    body: '증상, 지역, 불안, 치료 선택처럼 AI에게 바로 던질 법한 질문을 병원별로 정리합니다.',
  },
  {
    eyebrow: '02',
    title: 'AI 답변 속 현재 모습을 확인합니다',
    body: 'ChatGPT·Gemini가 우리 병원을 언급하는지, 어떤 정보가 비어 있는지, 경쟁 병원은 어떻게 설명되는지 봅니다.',
  },
  {
    eyebrow: '03',
    title: '병원답게 말할 기준을 세웁니다',
    body: '진료 강점, 원장님의 설명 방식, 병원이 자신 있게 말할 수 있는 근거를 하나의 운영 기준으로 정리합니다.',
  },
  {
    eyebrow: '04',
    title: '매달 보완하고 다시 확인합니다',
    body: '환자 질문에 답하는 콘텐츠를 근거 기반으로 운영하고, 다음 달 AI 답변에서 무엇이 달라졌는지 보고합니다.',
  },
]

const storyPoints = [
  {
    label: '대화의 변화',
    body: '환자는 이제 “근처 병원”을 검색하는 대신 AI에게 자신의 증상과 상황을 설명합니다.',
  },
  {
    label: '판단의 재료',
    body: 'AI는 광고 문구보다 공개된 병원 정보, 일관된 설명, 근거가 연결된 콘텐츠를 바탕으로 답합니다.',
  },
  {
    label: '빠지는 이유',
    body: '좋은 병원이어도 AI가 읽을 수 있는 정보가 흩어져 있으면 답변 안에서 빠지거나 다르게 이해될 수 있습니다.',
  },
  {
    label: '대비의 방식',
    body: 'Re:putation은 그 빈틈을 진단하고, 병원이 알려져야 할 기준을 매달 쌓아갑니다.',
  },
]

const targetClinics = [
  { name: '정형외과·통증의학과', body: '증상, 치료법, 회복 기간처럼 환자 질문이 반복되는 진료과' },
  { name: '내과·검진센터', body: '검진 항목, 결과 해석, 만성질환 관리 질문이 많은 진료과' },
  { name: '산부인과', body: '민감한 질문을 신뢰 기반 설명으로 풀어야 하는 진료과' },
  { name: '가정의학과·로컬 의원', body: '지역 기반 반복 질문과 생활질환 상담이 많은 의원' },
]

const reportRows = [
  { key: '확인한 질문', value: '12개' },
  { key: '우선 보완 주제', value: '4개' },
  { key: '원장 확인 항목', value: '2개' },
]

export default function Home() {
  return (
    <main className="reputation-canvas">
      <section className="hero-section">
        <nav className="site-nav" aria-label="주요 메뉴">
          <a className="brand-mark" href="#top" aria-label="Re:putation 홈">
            <span className="brand-dot" aria-hidden />
            Re:putation
          </a>
          <div className="nav-links" aria-label="페이지 섹션">
            <a href="#why">왜 지금인가</a>
            <a href="#system">운영 방식</a>
            <a href="#lead">진단 문의</a>
          </div>
          <a className="btn btn-primary nav-cta" href="#lead">AI 대비 상태 확인</a>
        </nav>

        <div id="top" className="hero-grid">
          <div className="hero-copy">
            <p className="badge">로컬 병원을 위한 AI 노출 컨설팅·콘텐츠 운영</p>
            <h1>
              환자들이 AI로 병원을 찾는 시대,
              <br />
              <span>우리 병원은 대비되어 있을까요?</span>
            </h1>
            <p className="hero-subcopy">
              환자가 AI에게 증상과 지역을 묻는 순간, AI는 이미 공개된 병원 정보를 바탕으로 답을 만듭니다.
              Re:putation은 우리 병원이 그 답변 안에서 더 정확히 이해되도록 진료 강점, 근거, 콘텐츠 운영을 연결합니다.
            </p>
            <div className="hero-actions">
              <a className="btn btn-primary" href="#lead">우리 병원 AI 대비 상태 확인하기</a>
              <a className="btn btn-ghost" href="#system">어떻게 대비하는지 보기</a>
            </div>
          </div>

          <aside className="report-card" aria-label="원장 보고 리포트 예시">
            <div className="report-header">
              <div>
                <p className="mono-label">OWNER READY REPORT</p>
                <h2>우리 병원 AI 대비 현황</h2>
              </div>
              <span className="status-pill">검토 완료</span>
            </div>
            <div className="report-main">
              <p>원장님께 설명할 핵심</p>
              <strong>AI가 우리 병원을 어떻게 이해하고 있는지 확인하고, 부족한 정보를 병원답게 채워갑니다.</strong>
            </div>
            <div className="report-metrics">
              {reportRows.map((row) => (
                <div key={row.key}>
                  <span>{row.key}</span>
                  <strong>{row.value}</strong>
                </div>
              ))}
            </div>
            <div className="report-note">
              <span aria-hidden />
              환자 질문별 현재 답변과 다음 보완 주제를 한 장으로 정리합니다.
            </div>
          </aside>
        </div>
      </section>

      <section id="why" className="section two-column">
        <div className="section-heading sticky-heading">
          <p className="mono-label accent">WHY NOW</p>
          <h2>병원 선택의 첫 대화가 바뀌고 있습니다.</h2>
          <p>검색 결과를 기다리는 대신, 환자는 AI에게 바로 묻습니다. 문제는 AI가 병원을 이해하는 방식이 기존 홍보물과 다르다는 점입니다.</p>
        </div>
        <div className="story-list">
          {storyPoints.map((item, index) => (
            <article className="story-card" key={item.label}>
              <span className="story-number">0{index + 1}</span>
              <div>
                <h3>{item.label}</h3>
                <p>{item.body}</p>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section id="system" className="section system-section">
        <div className="section-heading centered">
          <p className="mono-label accent">OPERATING SYSTEM</p>
          <h2>대비는 한 번의 제작물이 아니라, 매달 쌓이는 운영입니다.</h2>
          <p>질문을 정하고, 현재 답변을 확인하고, 병원답게 말할 기준을 세운 뒤 다시 측정합니다.</p>
        </div>
        <div className="process-grid">
          {processSteps.map((step) => (
            <article className="process-card" key={step.title}>
              <span>{step.eyebrow}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section specialty-panel">
        <div className="specialty-copy">
          <p className="mono-label accent">CLINIC FIT</p>
          <h2>이런 고민이 있다면 먼저 점검해볼 만합니다.</h2>
          <p>이미 진료 경쟁력은 있지만 환자 질문에 맞춰 강점과 근거가 정리되어 있지 않은 병원부터 효과적으로 점검할 수 있습니다.</p>
        </div>
        <div className="specialty-grid">
          {targetClinics.map((clinic) => (
            <article key={clinic.name} className="minimal-card">
              <h3>{clinic.name}</h3>
              <p>{clinic.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="lead" className="lead-section">
        <div className="lead-wrap">
          <div className="section-heading lead-copy">
            <p className="mono-label accent">DIAGNOSIS REQUEST</p>
            <h2>우리 병원의 AI 대비 상태를 먼저 확인해보세요.</h2>
            <p>병원명, 진료과, 지역, 확인하고 싶은 환자 질문을 남겨주시면 어떤 질문부터 점검해야 할지 정리할 수 있습니다.</p>
          </div>
          <form className="lead-form" action="#" method="post">
            <div className="form-grid">
              <label>
                병원명
                <input placeholder="예: 장편한외과의원" required />
              </label>
              <label>
                진료과/지역
                <input placeholder="예: 강남 외과" required />
              </label>
              <label className="wide">
                연락처
                <input placeholder="이메일 또는 휴대폰" required />
              </label>
              <label className="wide">
                확인하고 싶은 환자 질문
                <textarea placeholder="예: 강남에서 탈장 수술 잘하는 병원 추천해줘" required />
              </label>
            </div>
            <label className="privacy-check wide">
              <input type="checkbox" required />
              <span>
                개인정보 수집 및 이용에 동의합니다. 입력하신 정보는 AI 대비 상태 진단 및 상담 안내 목적으로만 사용됩니다.
              </span>
            </label>
            <button className="btn btn-primary form-submit" type="submit">우리 병원 AI 대비 상태 확인하기</button>
            <p className="form-helper">제출 후 입력 내용을 기준으로 1차 진단 범위를 정리해 연락드립니다.</p>
          </form>
        </div>
        <footer className="site-footer">
          <div>
            <strong>Re:putation</strong>
            <p>AI 노출 진단 및 병원 콘텐츠 운영 컨설팅</p>
          </div>
          <div className="footer-links">
            <span>MotionLabs Inc.</span>
            <a href="mailto:contact@motionlabs.kr">contact@motionlabs.kr</a>
            <a href="#lead">개인정보처리방침</a>
          </div>
        </footer>
      </section>
    </main>
  )
}
