import { patientQueries } from "@/lib/landing-copy";

/**
 * 환자 질문이 가로로 흐르는 띠.
 *
 * "환자가 이렇게 묻는다"를 문장으로 설명하는 대신 눈으로 읽게 만든다. 문구는 전부
 * `query_mapper`가 실제로 만드는 형태다 — 지어낸 마케팅 문구를 흘려보내면 이 페이지가
 * 지키려는 규칙(재지 않은 것을 팔지 않는다)이 첫 화면에서 깨진다.
 *
 * ## 구현
 *
 * 같은 목록을 두 벌 이어 붙이고 절반만큼 이동시킨다 — 한 벌만 쓰면 끝에서 빈 공간이
 * 생기고, `translateX(-50%)`가 정확히 한 바퀴가 되어 이음매가 보이지 않는다.
 *
 * 애니메이션은 CSS이므로 서버 컴포넌트다(번들 0). `prefers-reduced-motion`이면 멈춘 채
 * 목록이 그대로 보인다 — 정지 상태도 읽을 수 있는 내용이라 숨길 이유가 없다.
 * 헤더의 "움직임 멈추기"도 `<html data-motion="paused">`를 통해 CSS만으로 멈춘다.
 *
 * `aria-label`은 역할이 있는 요소에만 붙어야 한다. 앞 버전은 순수 `<div>`에 붙어 있어
 * 상당수 보조기술이 통째로 무시했다 — 라벨이 있다고 믿었는데 실제로는 없던 셈이다.
 */
export default function QueryMarquee() {
  return (
    <section className="marquee" aria-label="환자가 AI에 묻는 질문 예시">
      <div className="marquee-track">
        {[0, 1].map((lane) => (
          <ul key={lane} aria-hidden={lane === 1 ? "true" : undefined}>
            {patientQueries.map((query) => (
              <li key={query}>{query}</li>
            ))}
          </ul>
        ))}
      </div>
    </section>
  );
}
