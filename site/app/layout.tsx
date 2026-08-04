import type { Metadata, Viewport } from "next";
import Script from "next/script";
import { platformSiteUrl } from "@/lib/site-url";
import "./fonts/pretendard-subset.css";
import "./globals.css";

/* Pretendard는 여전히 자체호스팅이다(CDN 의존 없음). 달라진 것은 **한 덩어리로 주지
   않는다**는 것이다.

   앞 버전은 `next/font/local`로 PretendardVariable.woff2 하나를 통째로 실었고, 그
   파일이 2,057,688바이트 — 랜딩 1회 로드 전송량의 78%였다. 지금은 빈도 기반 서브셋
   92개로 쪼개 브라우저가 쓰는 구간만 받는다(랜딩 기준 538,464바이트, 26.2%).

   `--font-pretendard`는 생성된 CSS의 `:root`가 정의하므로 `<html>`에 클래스를 붙이지
   않는다. 서브셋 생성은 `python3 scripts/build_font_subsets.py`. */

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0671e0",
};

/**
 * 공유 카드 이미지 — **실제 발송되는 리포트 지면**이다.
 *
 * 앞 버전(`reputation-product-report-devices.png`)은 AI로 생성한 마케팅 이미지였다.
 * 노트북 화면의 "대시보드"는 라벨이 생성 낙서였고 차트는 아무것도 인코딩하지 않았으며,
 * 벽에 걸린 X-ray도 생성 파노라마였다. 존재하지 않는 제품의 스크린샷인 셈이다.
 * DESIGN.md Non-goals의 "present generated images as real"에 정면으로 걸리고,
 * 랜딩 카피가 스스로 적어 둔 "인상을 파는 순간 근거가 무너진다"에도 걸린다.
 *
 * 이 이미지가 특히 중요한 이유는 **AE가 카톡으로 링크를 보낼 때 원장이 보는 첫 화면**이
 * 이것이기 때문이다. 의료 이미지를 매일 보는 사람에게 생성 티가 나는 X-ray를 첫인상으로
 * 내밀 이유가 없다.
 *
 * 지금 이미지는 `backend/app/templates/lead_report.html`을 익명 payload로 렌더한
 * 진짜 지면이다(`scripts/build_og_report_html.py`). 병원명만 ○○ 플레이스홀더이고
 * 표·라벨·고지 문구·모델명은 프로덕션과 동일하다. 모델이나 규약이 바뀌면 다시 만든다.
 */
const OG_IMAGE = "/landing/reputation-diagnosis-report-og.png";
const googleSiteVerification = process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION?.trim();
const gaMeasurementId = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID?.trim();

export const metadata: Metadata = {
  metadataBase: new URL(platformSiteUrl()),
  title: "Re:putation — AI 답변에 우리 병원이 보이는지 진단합니다 · MotionLabs Research Preview",
  description:
    "MotionLabs가 운영하는 Re:putation Research Preview. ChatGPT·Gemini가 환자 질문에 답할 때 우리 병원이 어떻게 보이는지 진단하고, 빠진 정보와 근거 콘텐츠 운영 순서를 정리합니다.",
  openGraph: {
    title: "Re:putation — MotionLabs Research Preview",
    description:
      "환자는 이제 AI에게 병원을 묻습니다. 우리 병원은 그 답변 안에 제대로 보이고 있을까요?",
    siteName: "Re:putation by MotionLabs",
    locale: "ko_KR",
    type: "website",
    images: [
      {
        url: OG_IMAGE,
        width: 1200,
        height: 630,
        alt: "무료 AI 노출 진단 리포트 예시 — OpenAI API·Google Gemini API 측정 횟수와 언급 횟수가 표로 정리되어 있다",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Re:putation — MotionLabs Research Preview",
    description:
      "환자는 이제 AI에게 병원을 묻습니다. 우리 병원은 그 답변 안에 제대로 보이고 있을까요?",
    images: [OG_IMAGE],
  },
  ...(googleSiteVerification
    ? { verification: { google: googleSiteVerification } }
    : {}),
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body>
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-50 focus:rounded-lg focus:bg-blue-600 focus:px-4 focus:py-3 focus:text-sm focus:font-medium focus:text-white focus:shadow-lg focus:outline-none"
        >
          본문으로 바로가기
        </a>
        {children}
        {gaMeasurementId && /^G-[A-Z0-9]+$/.test(gaMeasurementId) ? (
          <>
            <Script
              src={`https://www.googletagmanager.com/gtag/js?id=${gaMeasurementId}`}
              strategy="afterInteractive"
            />
            <Script id="google-analytics" strategy="afterInteractive">
              {`window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','${gaMeasurementId}',{anonymize_ip:true});`}
            </Script>
          </>
        ) : null}
      </body>
    </html>
  );
}
