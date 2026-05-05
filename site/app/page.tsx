const processSteps = [
  {
    title: '먼저, 환자 질문을 정합니다',
    body: '“강남에서 허리 통증을 어디서 봐야 할까?”처럼 실제 환자가 AI에게 물을 법한 질문을 병원별로 정리합니다.',
  },
  {
    title: 'AI 답변 속 현재 모습을 확인합니다',
    body: 'ChatGPT·Gemini 답변에서 병원이 언급되는지, 어떤 정보가 부족한지, 주변 병원과 비교해 어떤 빈틈이 있는지 봅니다.',
  },
  {
    title: '병원답게 말할 기준을 세웁니다',
    body: '진료 강점, 원장님의 설명 방식, 병원이 자신 있게 말할 수 있는 근거를 콘텐츠 운영 기준으로 정리합니다.',
  },
  {
    title: '매달 보완하고 다시 확인합니다',
    body: '환자 질문에 답하는 콘텐츠를 근거 기반으로 운영하고, 다음 달 AI 답변에서 무엇이 달라졌는지 원장님께 보고합니다.',
  },
]

const storyPoints = [
  '환자는 이제 “근처 병원”을 검색하는 대신 AI에게 상황을 설명하고 추천을 묻습니다.',
  'AI는 병원의 광고 문구보다 공개된 정보, 일관된 설명, 근거가 연결된 콘텐츠를 바탕으로 답합니다.',
  '좋은 병원이어도 AI가 읽을 수 있는 정보가 부족하면 답변 안에서 빠질 수 있습니다.',
  'Re:putation은 그 빈틈을 진단하고, 병원이 알려져야 할 기준을 매달 쌓아갑니다.',
]

const targetClinics = [
  { name: '정형외과·통증의학과', body: '증상, 치료법, 회복 기간처럼 환자 질문이 반복되는 진료과' },
  { name: '내과·검진센터', body: '검진 항목, 결과 해석, 만성질환 관리 질문이 많은 진료과' },
  { name: '산부인과', body: '민감한 질문을 신뢰 기반 콘텐츠로 풀어야 하는 진료과' },
  { name: '가정의학과·로컬 의원', body: '지역 기반 반복 질문과 생활질환 상담이 많은 의원' },
]

export default function Home() {
  return (
    <main className="min-h-screen bg-[#f7f4ee] text-[#14110f]">
      <section className="relative overflow-hidden border-b border-[#ded6c9] bg-[#17130f] text-white">
        <div className="absolute inset-0 opacity-30" aria-hidden>
          <div className="absolute left-[-10%] top-[-20%] h-96 w-96 rounded-full bg-[#f3c46b] blur-3xl" />
          <div className="absolute bottom-[-30%] right-[-10%] h-[28rem] w-[28rem] rounded-full bg-[#6aa6ff] blur-3xl" />
        </div>
        <div className="relative mx-auto grid max-w-7xl gap-12 px-6 py-10 md:grid-cols-[1.05fr_0.95fr] md:px-10 md:py-16 lg:py-20">
          <nav className="flex items-center justify-between text-sm text-white/70 md:col-span-2">
            <div className="font-semibold tracking-tight text-white">Re:putation</div>
            <a href="#lead" className="rounded-full border border-white/20 px-4 py-2 text-white transition hover:bg-white hover:text-[#17130f]">
              진단 문의
            </a>
          </nav>
          <div>
            <p className="mb-5 inline-flex rounded-full border border-white/15 bg-white/10 px-4 py-2 text-sm text-[#f4d69b]">
              로컬 병원을 위한 AI 노출 컨설팅·콘텐츠 운영
            </p>
            <h1 className="max-w-4xl text-4xl font-semibold leading-[1.08] tracking-[-0.04em] md:text-6xl lg:text-7xl">
              환자들이 AI로 병원을 찾는 시대,
              우리 병원은 대비되어 있을까요?
            </h1>
            <p className="mt-7 max-w-2xl text-lg leading-8 text-white/72 md:text-xl">
              환자가 AI에게 증상과 지역을 묻는 순간, AI는 이미 공개된 병원 정보를 바탕으로 답을 만듭니다.
              Re:putation은 우리 병원이 그 답변 안에서 더 정확히 이해되도록 진료 강점, 근거, 콘텐츠 운영을 연결합니다.
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <a href="#lead" className="rounded-full bg-[#f3c46b] px-6 py-4 text-center font-semibold text-[#17130f] transition hover:bg-[#ffd886]">
                우리 병원 대비 상태 확인하기
              </a>
              <a href="#system" className="rounded-full border border-white/20 px-6 py-4 text-center font-semibold text-white transition hover:bg-white/10">
                어떻게 대비하는지 보기
              </a>
            </div>
          </div>

          <div className="self-end rounded-[2rem] border border-white/12 bg-white/[0.08] p-4 shadow-2xl backdrop-blur">
            <div className="rounded-[1.5rem] bg-[#f8f5ef] p-5 text-[#17130f]">
              <div className="mb-5 flex items-center justify-between border-b border-[#e7dece] pb-4">
                <div>
                  <p className="text-xs font-semibold tracking-[0.22em] text-[#8b6a2f]">원장 보고 리포트</p>
                  <h2 className="mt-1 text-xl font-semibold">우리 병원 AI 대비 현황</h2>
                </div>
                <span className="rounded-full bg-[#dff2df] px-3 py-1 text-xs font-medium text-[#2d6b34]">검토 완료</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl bg-white p-4 shadow-sm">
                  <p className="text-sm text-[#776c61]">AI 답변 속 현재 상태</p>
                  <p className="mt-2 text-3xl font-semibold">확인 필요</p>
                  <p className="mt-2 text-xs text-[#776c61]">환자 질문별로 점검</p>
                </div>
                <div className="rounded-2xl bg-white p-4 shadow-sm">
                  <p className="text-sm text-[#776c61]">먼저 정리할 부분</p>
                  <p className="mt-2 text-lg font-semibold">환자가 AI에 묻는 질문</p>
                  <p className="mt-2 text-xs text-[#776c61]">병원 기준과 근거 연결</p>
                </div>
              </div>
              <div className="mt-4 rounded-2xl bg-[#17130f] p-5 text-white">
                <p className="text-sm text-white/60">원장님께 설명할 핵심</p>
                <p className="mt-2 leading-7 text-white/88">
                  “AI가 우리 병원을 어떻게 이해하고 있는지 확인하고, 부족한 정보를 병원답게 채워갑니다.”
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-6 py-16 md:px-10 md:py-24">
        <div className="grid gap-10 md:grid-cols-[0.8fr_1.2fr]">
          <div>
            <p className="text-sm font-semibold tracking-[0.22em] text-[#9a6a21]">지금 필요한 이유</p>
            <h2 className="mt-4 text-3xl font-semibold tracking-[-0.03em] md:text-5xl">
              병원 선택의 첫 대화가 바뀌고 있습니다.
            </h2>
          </div>
          <div className="space-y-4">
            {storyPoints.map((item, index) => (
              <div key={item} className="rounded-3xl border border-[#dfd6c9] bg-white/70 p-6 shadow-sm">
                <p className="mb-4 text-sm font-semibold text-[#9a6a21]">0{index + 1}</p>
                <p className="text-lg leading-8 text-[#29231d]">{item}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="system" className="bg-white py-16 md:py-24">
        <div className="mx-auto max-w-7xl px-6 md:px-10">
          <div className="grid gap-10 md:grid-cols-[0.8fr_1.2fr]">
            <div>
              <p className="text-sm font-semibold tracking-[0.22em] text-[#9a6a21]">운영 시스템</p>
              <h2 className="mt-4 max-w-xl text-3xl font-semibold tracking-[-0.03em] md:text-5xl">대비는 한 번의 제작물이 아니라, 매달 쌓이는 운영입니다.</h2>
            </div>
            <div className="grid gap-4">
              {processSteps.map((step, index) => (
                <article key={step.title} className="grid gap-5 rounded-3xl border border-[#e5ded4] bg-[#fbf8f3] p-6 md:grid-cols-[4rem_1fr]">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#17130f] text-lg font-semibold text-white">
                    {index + 1}
                  </div>
                  <div>
                    <h3 className="text-xl font-semibold">{step.title}</h3>
                    <p className="mt-2 leading-7 text-[#665f56]">{step.body}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-6 py-16 md:px-10 md:py-24">
        <div className="rounded-[2rem] bg-[#17130f] p-8 text-white md:p-12">
          <div className="grid gap-10 md:grid-cols-[1fr_1fr]">
            <div>
              <p className="text-sm font-semibold tracking-[0.22em] text-[#f3c46b]">추천 진료과</p>
              <h2 className="mt-4 text-3xl font-semibold tracking-[-0.03em] md:text-5xl">이런 고민이 있다면 먼저 점검해볼 만합니다.</h2>
              <p className="mt-5 leading-8 text-white/68">
                이미 진료 경쟁력은 있지만 환자 질문에 맞춰 강점과 근거가 정리되어 있지 않은 병원부터 효과적으로 점검할 수 있습니다.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {targetClinics.map((clinic) => (
                <div key={clinic.name} className="rounded-2xl border border-white/12 bg-white/[0.07] p-5">
                  <p className="font-semibold">{clinic.name}</p>
                  <p className="mt-3 text-sm leading-6 text-white/58">{clinic.body}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section id="lead" className="border-t border-[#ded6c9] bg-[#eee7dc] py-16 md:py-24">
        <div className="mx-auto grid max-w-7xl gap-10 px-6 md:grid-cols-[0.95fr_1.05fr] md:px-10">
          <div>
            <p className="text-sm font-semibold tracking-[0.22em] text-[#9a6a21]">진단 문의</p>
            <h2 className="mt-4 max-w-xl text-3xl font-semibold tracking-[-0.03em] md:text-5xl">우리 병원의 AI 대비 상태를 먼저 확인해보세요.</h2>
            <p className="mt-5 leading-8 text-[#665f56]">
              병원명, 진료과, 지역, 확인하고 싶은 환자 질문을 남겨주시면 어떤 질문부터 점검해야 할지 정리할 수 있습니다.
            </p>
          </div>
          <form className="rounded-[2rem] bg-white p-6 shadow-xl shadow-[#91724d]/10" action="#" method="post">
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="grid gap-2 text-sm font-medium">
                병원명
                <input className="rounded-2xl border border-[#d9cec1] px-4 py-3 outline-none transition focus:border-[#9a6a21]" placeholder="예: 장편한외과의원" />
              </label>
              <label className="grid gap-2 text-sm font-medium">
                진료과/지역
                <input className="rounded-2xl border border-[#d9cec1] px-4 py-3 outline-none transition focus:border-[#9a6a21]" placeholder="예: 강남 외과" />
              </label>
              <label className="grid gap-2 text-sm font-medium sm:col-span-2">
                연락처
                <input className="rounded-2xl border border-[#d9cec1] px-4 py-3 outline-none transition focus:border-[#9a6a21]" placeholder="이메일 또는 휴대폰" />
              </label>
              <label className="grid gap-2 text-sm font-medium sm:col-span-2">
                확인하고 싶은 환자 질문
                <textarea className="min-h-28 rounded-2xl border border-[#d9cec1] px-4 py-3 outline-none transition focus:border-[#9a6a21]" placeholder="예: 강남에서 탈장 수술 잘하는 병원 추천해줘" />
              </label>
            </div>
            <button className="mt-5 w-full rounded-full bg-[#17130f] px-6 py-4 font-semibold text-white transition hover:bg-[#2b241e]" type="submit">
              AI 대비 상태 문의하기
            </button>
            <p className="mt-3 text-center text-xs leading-5 text-[#776c61]">
              입력 항목은 1차 진단 범위를 정리하기 위한 최소 정보입니다.
            </p>
          </form>
        </div>
      </section>
    </main>
  )
}
