import type { Metadata } from 'next'
import ContactForm from '../_components/ContactForm'
import { SiteFooter, SiteHeader } from '../_components/SiteChrome'

export const metadata: Metadata = {
  title: '도입문의 | Re:putation',
  description: 'Re:putation 도입 상담을 남겨 주세요. 병원 현황에 맞는 다음 단계를 안내드립니다.',
  robots: { index: true, follow: true },
}

export default function ContactPage() {
  return (
    <main id="main-content" className="landing-shell landing-edge landing-sub">
      {/* 이 페이지 자신이 도입문의이므로 헤더 CTA는 그리지 않는다. */}
      <SiteHeader hideCta />

      <section id="contact" className="cta-section" aria-labelledby="contact-heading">
        <div className="cta-inner" data-reveal>
          <p className="section-label">도입문의</p>
          <h1 id="contact-heading">도입 상담을 남겨 주세요</h1>
          <p className="cta-body">
            병원 현황과 운영 방식을 짧게 여쭙고, 맞는 요금제·일정·다음 단계를 안내드립니다.
          </p>
          <ContactForm />
          <ul className="cta-notes">
            <li>남기신 연락처는 도입 상담 안내에만 사용합니다.</li>
            <li>영업 목적의 반복 연락 없이, 남겨 주신 문의에 답하는 용도로만 사용합니다.</li>
          </ul>
        </div>
      </section>

      <SiteFooter />
    </main>
  )
}
