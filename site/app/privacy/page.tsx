import Link from "next/link";

export const metadata = {
  title: "개인정보 처리방침 — Re:putation",
  description: "주식회사 모션랩스가 운영하는 Re:putation의 개인정보 처리방침입니다.",
};

const CONSENT_VERSION = "v1.2026-05";
const EFFECTIVE_DATE = "2026-05-08";

const SECTIONS = [
  {
    title: "1. 수집하는 개인정보 항목",
    body: [
      "Re:putation은 무료 진단 상담을 위해 다음 항목을 수집합니다.",
      "필수: 병원명, 진료과/지역, 연락처(이메일 또는 휴대전화), 확인하고 싶은 환자 질문",
      "자동 수집: 동의 시각, 동의 시점 IP, 처리방침 동의 버전",
    ],
  },
  {
    title: "2. 수집·이용 목적",
    body: [
      "AI 노출 진단 범위 확인 및 진단 결과 안내",
      "유료 서비스 도입 의향이 있는 경우 후속 상담 진행",
      "민감정보(건강·진료기록) 및 환자 개인정보는 수집하지 않습니다.",
    ],
  },
  {
    title: "3. 보유 및 이용 기간",
    body: [
      "수집일로부터 180일 이내에 자동 파기합니다.",
      "상담 종료, 동의 철회, 이용자 본인의 삭제 요청 시 지체 없이 즉시 파기합니다.",
      "법령에 따라 보존이 필요한 경우 해당 법령이 정한 기간 동안만 보존합니다.",
    ],
  },
  {
    title: "4. 동의 거부 권리 및 거부 시 영향",
    body: [
      "이용자는 위 동의를 거부할 수 있습니다.",
      "거부 시 무료 진단 상담만 진행되지 않으며, 다른 불이익은 없습니다.",
      "이미 동의한 항목에 대해 언제든지 동의를 철회할 수 있습니다 (privacy@motionlabs.kr).",
    ],
  },
  {
    title: "5. 제3자 제공 / 처리 위탁",
    body: [
      "Re:putation은 수집한 개인정보를 제3자에게 제공하지 않습니다.",
      "다음 처리 위탁 사실이 있을 수 있으며, 위탁 시에는 이용자에게 별도 고지합니다.",
      "- 인프라: Amazon Web Services (서울 리전 우선)",
      "- 이메일 발송: 미사용 (현재 모든 상담은 위 연락처를 통해 직접 회신)",
      "- 사내 협업 도구: Slack (PII는 마스킹 후에만 채널로 송출됨)",
    ],
  },
  {
    title: "6. 개인정보 보호 안전조치",
    body: [
      "전송 구간 TLS 암호화, 데이터베이스 접근 권한 분리, 관리자 작업 감사 로그.",
      "모든 관리자 액션은 누가 / 언제 / 무엇을 했는지 audit_log에 기록됩니다.",
      "Slack 등 외부 채널로 PII가 노출될 수 있는 경로는 모두 마스킹 처리합니다.",
    ],
  },
  {
    title: "7. 정보주체의 권리 행사",
    body: [
      "이용자는 본인의 개인정보에 대해 열람·정정·삭제·처리정지를 요청할 수 있습니다.",
      "요청 채널: privacy@motionlabs.kr",
      "회사는 요청 접수 후 10일 이내에 처리 결과를 회신합니다.",
    ],
  },
  {
    title: "8. 처리방침 버전 / 변경 안내",
    body: [
      `현재 처리방침 버전: ${CONSENT_VERSION} (시행일 ${EFFECTIVE_DATE}).`,
      "처리방침이 변경될 경우 시행일 7일 전 홈페이지 공지 또는 직접 통지합니다.",
      "이용자가 동의 시점에 적용된 처리방침 버전은 별도로 보관됩니다.",
    ],
  },
  {
    title: "9. 개인정보 보호책임자",
    body: [
      "주식회사 모션랩스 개인정보 보호책임자",
      "이메일: privacy@motionlabs.kr",
      "한국인터넷진흥원 개인정보침해 신고센터(국번없이 118)에 신고할 수 있습니다.",
    ],
  },
];

export default function PrivacyPage() {
  return (
    <main className="legal-shell">
      <header className="legal-header">
        <Link href="/" className="legal-back">← Re:putation 홈으로</Link>
        <h1>개인정보 처리방침</h1>
        <p>버전 {CONSENT_VERSION} · 시행일 {EFFECTIVE_DATE}</p>
      </header>
      <article className="legal-body">
        <p>
          주식회사 모션랩스(이하 &ldquo;회사&rdquo;)는 Re:putation 서비스(이하 &ldquo;서비스&rdquo;)
          를 제공하면서 이용자의 개인정보를 다음과 같이 처리합니다.
        </p>
        {SECTIONS.map((section) => (
          <section key={section.title}>
            <h2>{section.title}</h2>
            {section.body.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </section>
        ))}
        <p className="legal-effective">
          본 처리방침은 {EFFECTIVE_DATE}부터 시행됩니다.
        </p>
      </article>
    </main>
  );
}
