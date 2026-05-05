const diagnosisRows = [
  { label: '환자 질문', value: '“강남에서 탈장 수술 잘하는 병원 알려줘”' },
  { label: '현재 AI 답변', value: '주변 병원은 언급되지만 우리 병원 설명은 부족함' },
  { label: '빠진 이유', value: '수술 강점·원장 설명·근거 콘텐츠가 한 흐름으로 정리되지 않음' },
  { label: '다음 보완', value: '환자 질문 기준 콘텐츠 4개와 병원 정보 정리' },
]

const proofPoints = [
  'ChatGPT·Gemini 기준 환자 질문 확인',
  '진료 강점과 근거 자료 정리',
  '의료광고 리스크 검토',
  '원장님용 월간 보고서 제공',
]

const processSteps = [
  {
    title: '질문을 정합니다',
    body: '지역, 증상, 치료 선택처럼 환자가 AI에게 실제로 물어볼 질문을 진료과별로 정리합니다.',
  },
  {
    title: '현재 답변을 확인합니다',
    body: 'AI 답변 안에서 우리 병원이 언급되는지, 어떤 설명이 빠져 있는지 확인합니다.',
  },
  {
    title: '빠진 이유를 찾습니다',
    body: '병원 정보, 진료 강점, 근거 콘텐츠가 AI가 이해할 수 있게 연결되어 있는지 점검합니다.',
  },
  {
    title: '매달 보완합니다',
    body: '필요한 콘텐츠와 병원 정보를 보강하고, 다음 달 다시 AI 답변 변화를 확인합니다.',
  },
]

const clinicTypes = [
  '정형외과·통증의학과',
  '내과·검진센터',
  '산부인과',
  '가정의학과·로컬 의원',
]

export default function Home() {
  return (
    <main className="landing-shell">
      <header className="top-bar">
        <a className="brand" href="#top" aria-label="Re:putation 홈">
          <span aria-hidden />
          <strong>Re:putation</strong>
          <small>병원 AI 노출 진단</small>
        </a>
        <nav className="nav-links" aria-label="페이지 섹션">
          <a href="#diagnosis">진단 항목</a>
          <a href="#process">월간 운영</a>
          <a href="#lead">무료 진단</a>
        </nav>
        <a className="nav-button" href="#lead">AI 노출 진단받기</a>
      </header>

      <section id="top" className="hero-section">
        <div className="hero-copy">
          <p className="eyebrow">로컬 병원을 위한 AI 노출 진단</p>
          <h1>
            환자는 이제 AI에게 병원을 묻습니다.
            <span>우리 병원은 그 답변 안에 제대로 보이고 있을까요?</span>
          </h1>
          <p className="hero-subcopy">
            Re:putation은 ChatGPT·Gemini가 환자 질문에 답할 때 우리 병원이 언급되는지,
            어떤 설명으로 보이는지 확인하고 다음 보완 주제를 정리합니다.
          </p>
          <div className="hero-actions">
            <a className="button primary" href="#lead">우리 병원 AI 노출 진단받기</a>
            <a className="button secondary" href="#diagnosis">AI 노출 진단 샘플 보기</a>
          </div>
          <div className="trust-strip" aria-label="서비스 신뢰 요소">
            {proofPoints.map((point) => (
              <span key={point}>{point === '의료광고 리스크 검토' ? '의료광고법 리스크를 고려해 점검' : point}</span>
            ))}
          </div>
        </div>

        <aside className="diagnosis-card" aria-label="AI 노출 진단 리포트 예시">
          <div className="card-topline">
            <div>
              <p>AI 노출 1차 진단</p>
              <h2>우리 병원 AI 답변 진단 예시</h2>
            </div>
            <div className="card-badges" aria-label="확인 기준">
              <strong>ChatGPT</strong>
              <strong>Gemini</strong>
            </div>
          </div>
          <div className="question-box">
            <span>확인 질문</span>
            <strong>“우리 지역에서 이 증상으로 어느 병원을 가야 할까?”</strong>
          </div>
          <dl className="diagnosis-list">
            {diagnosisRows.map((row) => (
              <div key={row.label}>
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
          <div className="card-note">
            과장된 약속 대신, AI가 병원을 이해할 수 있는 정보와 근거를 매달 점검합니다.
          </div>
        </aside>
      </section>

      <section id="diagnosis" className="section diagnosis-section">
        <div className="section-heading">
          <p className="eyebrow">WHAT WE CHECK</p>
          <h2>좋은 병원인데 AI 답변에서 빠지는 이유를 찾습니다.</h2>
          <p>
            AI는 광고 문구보다 공개된 병원 정보, 환자 질문에 맞는 설명, 근거가 연결된 콘텐츠를 바탕으로 답합니다.
          </p>
        </div>
        <div className="check-grid">
          <article>
            <span>01</span>
            <h3>환자 질문 기준</h3>
            <p>원장님이 하고 싶은 말이 아니라 환자가 실제로 묻는 문장부터 정리합니다.</p>
          </article>
          <article>
            <span>02</span>
            <h3>현재 AI 답변</h3>
            <p>우리 병원이 언급되는지, 어떤 병원과 함께 설명되는지 확인합니다.</p>
          </article>
          <article>
            <span>03</span>
            <h3>근거와 설명의 빈틈</h3>
            <p>진료 강점, 원장님 설명, 병원 자료가 한 흐름으로 연결되어 있는지 봅니다.</p>
          </article>
        </div>
      </section>

      <section id="process" className="section process-section">
        <div className="section-heading compact">
          <p className="eyebrow">MONTHLY OPERATION</p>
          <h2>진단으로 끝내지 않고, 매달 보완합니다.</h2>
        </div>
        <div className="process-list">
          {processSteps.map((step, index) => (
            <article key={step.title}>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <div>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="section fit-section">
        <div>
          <p className="eyebrow">CLINIC FIT</p>
          <h2>이런 병원부터 먼저 점검해볼 만합니다.</h2>
          <p>이미 진료 경쟁력은 있지만, 환자 질문 기준으로 강점과 근거가 정리되어 있지 않은 로컬 병원에 적합합니다.</p>
        </div>
        <ul>
          {clinicTypes.map((clinic) => (
            <li key={clinic}>{clinic}</li>
          ))}
        </ul>
      </section>

      <section id="lead" className="lead-section">
        <div className="lead-copy">
          <p className="eyebrow">FREE DIAGNOSIS REQUEST</p>
          <h2>우리 병원이 AI 답변에서 어떻게 보이는지 먼저 확인해보세요.</h2>
          <p>
            병원명과 확인하고 싶은 환자 질문을 남겨주시면 1차 진단 범위를 정리해 연락드립니다.
            진단은 무료이며, 입력하신 정보는 상담 안내와 진단 범위 확인에만 사용됩니다.
          </p>
        </div>
        <form className="lead-form" action="#" method="post">
          <label>
            병원명
            <input placeholder="예: 장편한외과의원" required />
          </label>
          <label>
            진료과/지역
            <input placeholder="예: 강남 외과" required />
          </label>
          <label>
            연락처
            <input placeholder="이메일 또는 휴대폰" required />
          </label>
          <label>
            확인하고 싶은 환자 질문
            <textarea placeholder="예: 강남에서 탈장 수술 잘하는 병원 알려줘" required />
          </label>
          <label className="privacy-check">
            <input type="checkbox" required />
            <span>개인정보 수집 및 이용에 동의합니다. 입력하신 정보는 AI 노출 진단 및 상담 안내 목적으로만 사용됩니다.</span>
          </label>
          <button className="button primary submit-button" type="submit">무료 진단 요청하기</button>
        </form>
      </section>

      <footer className="site-footer">
        <div>
          <strong>Re:putation</strong>
          <p>AI 노출 진단 및 병원 콘텐츠 운영 컨설팅</p>
        </div>
        <div>
          <span>MotionLabs Inc.</span>
          <a href="mailto:contact@motionlabs.kr">contact@motionlabs.kr</a>
          <a href="#lead">개인정보처리방침</a>
        </div>
      </footer>
    </main>
  )
}
