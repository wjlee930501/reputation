import Link from "next/link";

import { BROCHURE_ENABLED } from "@/lib/brochure-flag";

import MotionToggle from "./MotionToggle";

/**
 * 랜딩과 하위 페이지(도입문의 · 약관 · 처리방침 · 진단 신청 · 404 · 에러)가 함께 쓰는
 * 헤더와 푸터.
 *
 * 앞서는 홈이 헤더·푸터를 자기 안에 적어 두고, /contact가 헤더만 베껴 쓰고, 나머지
 * 하위 페이지는 각자 다른 모양(Tailwind 파랑 버튼, "← 홈으로" 링크)이었다. 홈에서
 * 도입문의로 넘어가면 브랜드가 한 번 바뀌는 셈이라, 크롬을 한 곳에서 그린다.
 *
 * 병원 공개 페이지(`/[slug]` 이하)는 쓰지 않는다 — 그쪽은 병원별 테마를 입는
 * 병원의 얼굴이고, Re:putation 브랜드가 끼어들 자리가 아니다.
 */

/** 콜론이 브랜드 표식이다 — 뉴비짓 로고의 i 위 점처럼 이 자리에만 accent를 준다. */
export function BrandWord() {
  return (
    <>
      Re<span className="brand-colon">:</span>putation
    </>
  );
}

type HeaderProps = {
  /** 홈에서는 같은 페이지의 섹션으로, 하위 페이지에서는 홈의 섹션으로 간다. */
  sectionBase?: "" | "/";
  /** 도입문의 버튼이 가는 곳. 홈은 `#contact`, 하위 페이지는 `/contact`. */
  ctaHref?: string;
  /**
   * 좁은 화면에서도 헤더 CTA를 남길지. 홈은 하단 고정 바가 그 역할을 하므로 숨기고,
   * 고정 바가 없는 하위 페이지는 헤더에 남겨야 신청 경로가 끊기지 않는다.
   */
  keepCtaOnMobile?: boolean;
  /** 도입문의 페이지 자신에서는 CTA를 그리지 않는다(자기 자신을 가리키는 버튼). */
  hideCta?: boolean;
};

export function SiteHeader({
  sectionBase = "/",
  ctaHref = "/contact",
  keepCtaOnMobile = true,
  hideCta = false,
}: HeaderProps) {
  const HomeLink = sectionBase === "" ? "a" : Link;
  return (
    <header className="site-header" data-cta={keepCtaOnMobile ? "always" : undefined}>
      <HomeLink
        className="brand-lockup"
        href={sectionBase === "" ? "#top" : "/"}
        aria-label="MotionLabs Re:putation 홈"
      >
        <strong>
          <BrandWord />
        </strong>
        <small>by MotionLabs</small>
      </HomeLink>

      <nav className="header-nav" aria-label="랜딩 페이지 섹션">
        <a href={`${sectionBase}#preview`}>진단 리포트</a>
        <a href={`${sectionBase}#operation`}>운영 방식</a>
        <a href={`${sectionBase}#faq`}>자주 묻는 질문</a>
      </nav>

      {!hideCta && (
        <a className="header-cta" href={ctaHref}>
          도입문의
        </a>
      )}
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-brand">
        <strong>
          <BrandWord />
        </strong>
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
        {BROCHURE_ENABLED && <Link href="/brochure">서비스 소개서</Link>}
        <Link href="/privacy">개인정보 처리방침</Link>
        <Link href="/terms">이용약관</Link>
        <Link href="/contact">도입문의</Link>
        {/* 헤더 내비에서 내려온 자리다 — 내비에는 섹션 링크만 남기되, 페이지를 멈추는
            수단 자체는 남긴다(WCAG 2.2.2). 자동으로 움직이는 것은 질문 띠와 로고뿐이고
            둘 다 이 토글 하나로 멈춘다. */}
        <MotionToggle />
      </div>
    </footer>
  );
}
