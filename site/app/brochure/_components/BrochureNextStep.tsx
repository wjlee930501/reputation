import Link from 'next/link'

/**
 * 소개서 아래의 다음 단계 — 도입문의.
 *
 * 소개서는 일반론이라 읽고 나면 "그래서 우리 병원은?"이 남는다. 그 질문의 답이 진단
 * 리포트이고, 리포트는 도입문의를 남긴 병원에 담당자가 만들어 들고 간다. 이 페이지에서
 * 가장 무거운 버튼은 여기다.
 */
export default function BrochureNextStep() {
  return (
    <section className="brochure-next" aria-labelledby="brochure-next-heading">
      <div className="brochure-inner">
        <p className="section-label">다음 단계</p>
        <h2 id="brochure-next-heading">
          소개서 다음은
          <br />
          <em className="accent">우리 병원 진단 리포트</em>입니다
        </h2>
        <p className="brochure-next-body">
          도입문의를 남기시면 ChatGPT와 Gemini에 우리 병원 기준으로 질문한 결과를 리포트로 만들어,
          담당 마케터가 직접 설명드립니다.
        </p>
        <Link className="btn btn-primary btn-lg" href="/contact">
          도입문의 남기기
        </Link>
      </div>
    </section>
  )
}
