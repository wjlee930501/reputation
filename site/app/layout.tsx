import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "https://reputation.co.kr"),
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
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
