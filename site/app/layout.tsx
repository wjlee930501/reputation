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

const OG_IMAGE = "/landing/reputation-product-report-devices.png";
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
        alt: "Re:putation — AI 답변에 우리 병원이 보이는지 진단합니다",
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
