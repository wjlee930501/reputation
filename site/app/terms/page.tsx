import Link from "next/link";

export const metadata = {
  title: "이용약관 — Re:putation",
  description: "Re:putation 무료 진단 신청·상담 서비스의 이용약관입니다.",
};

const EFFECTIVE_DATE = "2026-05-08";

const SECTIONS = [
  {
    title: "제1조 (목적)",
    body: [
      "본 약관은 주식회사 모션랩스(이하 \"회사\")가 운영하는 Re:putation 서비스의 무료 진단 상담 신청 절차와 회사·이용자 간 권리·의무를 규정합니다.",
    ],
  },
  {
    title: "제2조 (서비스 정의)",
    body: [
      "Re:putation은 의료기관이 ChatGPT·Gemini 등 AI 답변에서 어떻게 보이는지 진단하고, 보완 콘텐츠 운영 방향을 안내하는 서비스입니다.",
      "본 사이트는 무료 진단 상담 신청 채널이며, 유료 서비스 계약은 별도 계약서로 체결됩니다.",
    ],
  },
  {
    title: "제3조 (회사가 보장하지 않는 사항)",
    body: [
      "회사는 AI 답변에서 특정 병원의 노출 순위·환자 유입·치료 효과를 보장하지 않습니다.",
      "회사는 의료기관이 아니며, 의료 행위 또는 진료 안내를 직접 제공하지 않습니다.",
      "이용자(의료기관)는 의료법, 의료광고에 관한 법령, 자율심의 절차를 본인의 책임 하에 준수해야 합니다.",
    ],
  },
  {
    title: "제4조 (이용자의 책임)",
    body: [
      "이용자는 신청 시 본인 또는 본인이 권한을 가진 의료기관의 정보를 정확히 제공해야 합니다.",
      "이용자는 환자의 개인정보를 본 신청 폼에 입력해서는 안 됩니다.",
      "이용자는 의료광고 자율심의 등 법령상 의무를 회사에 위임할 수 없습니다.",
    ],
  },
  {
    title: "제5조 (이용 제한 및 해지)",
    body: [
      "회사는 다음 경우에 신청을 접수하지 않거나 상담을 중단할 수 있습니다.",
      "- 자동화된 봇 또는 허위 정보로 신청한 경우",
      "- 의료기관이 아닌 자가 의료기관을 사칭하여 신청한 경우",
      "- 본 약관 또는 관계 법령을 위반한 경우",
    ],
  },
  {
    title: "제6조 (개인정보 보호)",
    body: [
      "이용자가 제공한 개인정보는 별도의 개인정보 처리방침에 따라 처리됩니다.",
      "이용자는 동의를 언제든지 철회할 수 있으며 철회 시 회사는 지체 없이 정보를 파기합니다.",
    ],
  },
  {
    title: "제7조 (분쟁 해결)",
    body: [
      "본 약관과 관련된 분쟁은 대한민국 법령에 따라 해결합니다.",
      "관할 법원은 회사의 본점 소재지 관할 법원으로 합니다.",
    ],
  },
];

export default function TermsPage() {
  return (
    <main id="main-content" className="legal-shell">
      <header className="legal-header">
        <Link href="/" className="legal-back">← Re:putation 홈으로</Link>
        <span className="motionlabs-chip" style={{ marginTop: 12 }}>
          <strong>MotionLabs</strong> Research Preview
        </span>
        <h1 className="heading1" style={{ marginTop: 12 }}>이용약관</h1>
        <p className="body4">시행일 {EFFECTIVE_DATE}</p>
      </header>
      <article className="legal-body">
        {SECTIONS.map((section) => (
          <section key={section.title}>
            <h2>{section.title}</h2>
            {section.body.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </section>
        ))}
        <p className="legal-effective">본 약관은 {EFFECTIVE_DATE}부터 시행됩니다.</p>
      </article>
    </main>
  );
}
