const processSteps = [
  {
    title: 'AI 답변 현황 진단',
    body: '환자가 실제로 물어볼 질문을 정리하고 ChatGPT·Gemini 답변에서 병원이 어떻게 언급되는지 확인합니다.',
  },
  {
    title: '진료 강점과 기준 정리',
    body: '진료 강점, 원장님의 설명 방식, 병원이 자신 있게 말할 수 있는 근거를 운영 기준으로 정리합니다.',
  },
  {
    title: '근거 기반 콘텐츠 운영',
    body: '환자 질문에 맞춘 콘텐츠 가이드를 만들고 의료광고 리스크와 병원 운영 기준을 확인한 뒤 발행합니다.',
  },
  {
    title: '월간 리포트와 다음 조치',
    body: 'AI에서 보인 변화, 아직 잡히지 않는 질문, 다음 달 집중할 주제를 원장님이 이해하기 쉽게 보고합니다.',
  },
]

const proofItems = [
  'AI 답변 안에서 우리 병원이 언급되는 비율 측정',
  '환자 질문별 부족한 정보와 보완 작업 정리',
  '병원 자료·공식 채널·진료 기준 기반의 콘텐츠 가이드',
  '의료광고 리스크 검수 및 발행 전 승인 절차',
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
              환자와 AI가 병원을 더 정확히 이해하도록 만듭니다.
            </h1>
            <p className="mt-7 max-w-2xl text-lg leading-8 text-white/72 md:text-xl">
              병원의 강점과 근거를 정리하고, ChatGPT·Gemini 답변에서 빠지는 정보를 매달 콘텐츠로 보완합니다.
              원장님이 설명할 수 있는 기준과 환자가 실제로 묻는 질문을 연결하는 운영 시스템입니다.
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <a href="#lead" className="rounded-full bg-[#f3c46b] px-6 py-4 text-center font-semibold text-[#17130f] transition hover:bg-[#ffd886]">
                무료 AI 노출 진단 문의
              </a>
              <a href="#system" className="rounded-full border border-white/20 px-6 py-4 text-center font-semibold text-white transition hover:bg-white/10">
                진단 과정 보기
              </a>
            </div>
          </div>

          <div className="self-end rounded-[2rem] border border-white/12 bg-white/[0.08] p-4 shadow-2xl backdrop-blur">
            <div className="rounded-[1.5rem] bg-[#f8f5ef] p-5 text-[#17130f]">
              <div className="mb-5 flex items-center justify-between border-b border-[#e7dece] pb-4">
                <div>
                  <p className="text-xs font-semibold tracking-[0.22em] text-[#8b6a2f]">원장 보고 리포트</p>
                  <h2 className="mt-1 text-xl font-semibold">이번 달 AI 노출 요약</h2>
                </div>
                <span className="rounded-full bg-[#dff2df] px-3 py-1 text-xs font-medium text-[#2d6b34]">검토 완료</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl bg-white p-4 shadow-sm">
                  <p className="text-sm text-[#776c61]">현재 AI 언급률</p>
                  <p className="mt-2 text-3xl font-semibold">진단 중</p>
                  <p className="mt-2 text-xs text-[#776c61]">질문별 확인 결과 기반</p>
                </div>
                <div className="rounded-2xl bg-white p-4 shadow-sm">
                  <p className="text-sm text-[#776c61]">다음 보완 주제</p>
                  <p className="mt-2 text-lg font-semibold">환자가 묻는 핵심 질문</p>
                  <p className="mt-2 text-xs text-[#776c61]">진료 강점과 근거 연결</p>
                </div>
              </div>
              <div className="mt-4 rounded-2xl bg-[#17130f] p-5 text-white">
                <p className="text-sm text-white/60">원장님께 설명할 핵심</p>
                <p className="mt-2 leading-7 text-white/88">
                  “AI 답변에서 병원이 빠지는 이유를 찾고, 병원이 잘 알려져야 할 진료 기준을 콘텐츠로 보강합니다.”
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-6 py-16 md:px-10 md:py-24">
        <div className="max-w-3xl">
          <p className="text-sm font-semibold tracking-[0.22em] text-[#9a6a21]">지금 필요한 이유</p>
          <h2 className="mt-4 text-3xl font-semibold tracking-[-0.03em] md:text-5xl">
            이제 환자는 검색창뿐 아니라 AI에게 병원을 묻습니다.
          </h2>
          <p className="mt-5 text-lg leading-8 text-[#665f56]">
            문제는 AI가 병원을 추천하지 않는 이유가 단순히 “홍보가 부족해서”가 아닐 수 있다는 점입니다.
            병원 정보가 흩어져 있거나, 진료 강점이 근거와 연결되지 않았거나, 환자 질문에 답할 공개 콘텐츠가 부족할 수 있습니다.
          </p>
        </div>
        <div className="mt-10 grid gap-4 md:grid-cols-4">
          {proofItems.map((item) => (
            <div key={item} className="rounded-3xl border border-[#dfd6c9] bg-white/70 p-5 shadow-sm">
              <div className="mb-5 h-1.5 w-10 rounded-full bg-[#c58b2b]" />
              <p className="leading-7 text-[#29231d]">{item}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="system" className="bg-white py-16 md:py-24">
        <div className="mx-auto max-w-7xl px-6 md:px-10">
          <div className="grid gap-10 md:grid-cols-[0.8fr_1.2fr]">
            <div>
              <p className="text-sm font-semibold tracking-[0.22em] text-[#9a6a21]">운영 시스템</p>
              <h2 className="mt-4 max-w-xl text-3xl font-semibold tracking-[-0.03em] md:text-5xl">한 번 만들고 끝나는 페이지가 아니라, 매달 좋아지는 운영 흐름입니다.</h2>
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
              <h2 className="mt-4 text-3xl font-semibold tracking-[-0.03em] md:text-5xl">이런 병원에 먼저 맞습니다.</h2>
              <p className="mt-5 leading-8 text-white/68">
                이미 진료 경쟁력은 있지만, 온라인에 그 강점이 충분히 정리되어 있지 않거나 AI 답변에서 주변 경쟁 병원보다 덜 보이는 병원에 적합합니다.
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
            <h2 className="mt-4 max-w-xl text-3xl font-semibold tracking-[-0.03em] md:text-5xl">우리 병원이 AI 답변에서 어떻게 보이는지 먼저 확인해보세요.</h2>
            <p className="mt-5 leading-8 text-[#665f56]">
              병원명, 진료과, 지역, 확인하고 싶은 환자 질문을 남기면 1차 진단 범위를 정리할 수 있습니다.
              다음 단계에서 저장·알림 연동을 붙이면 바로 리드 수집 흐름으로 사용할 수 있습니다.
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
              AI 노출 진단 요청하기
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
