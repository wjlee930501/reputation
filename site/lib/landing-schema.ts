/**
 * 랜딩의 구조화 데이터.
 *
 * ## 왜 이게 없으면 안 되는가
 *
 * 이 페이지는 "병원 정보를 AI가 읽는 형태로 정리해 드립니다"를 판다. 그런데 정작
 * 이 페이지에는 JSON-LD가 **한 줄도 없었다.** 경쟁사(얼라인)의 공개 진단 도구에
 * 우리 랜딩을 넣으면 B등급 62점이 나왔고, 감점 사유가 정확히 우리가 파는 항목이었다:
 *
 *   구조화 데이터(JSON-LD) 없음        0/6
 *   스키마 다양성 0종                   0/3
 *   FAQ 이름표(FAQPage) 없음            0/4   ← FAQ를 11개 렌더링하면서
 *   정규 주소(canonical) 없음           0/3
 *
 * 파는 것을 스스로 지키지 않는 페이지는, 그 자체가 반증이다. 게다가 이 항목들은
 * 경쟁사 도구로 **누구나 검증할 수 있다** — 고치면 그 검증 가능성이 그대로 근거가 된다.
 *
 * ## 지어내지 않는다
 *
 * 사업자 정보는 운영사 공식 사이트(motionlabs.kr)에 공개된 값만 쓴다. 확인되지 않는
 * 값을 구조화 데이터에 넣으면 그건 기계가 읽는 거짓말이 되고, 사람이 읽는 거짓말보다
 * 오래 남는다.
 */

export const MOTIONLABS = {
  legalName: "주식회사 모션랩스",
  alternateName: "MotionLabs Inc.",
  founder: "이우진",
  /** 사업자등록번호 — motionlabs.kr 푸터에 공개된 값. */
  taxId: "466-88-01551",
  streetAddress: "아차산로 38, 406호",
  addressLocality: "성동구",
  addressRegion: "서울특별시",
  addressCountry: "KR",
  telephone: "+82-70-8671-0100",
  email: "support@motionlabs.kr",
  url: "https://motionlabs.kr",
} as const

export type LandingFaq = { question: string; answer: string }

/** 조직 — 이 서비스를 누가 운영하는가. AI가 "누구냐"에 답할 때 쓰는 노드다. */
export function buildOrganizationJsonLd(siteUrl: string): Record<string, unknown> {
  const base = siteUrl.replace(/\/$/, "")
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    "@id": `${base}/#organization`,
    name: MOTIONLABS.legalName,
    alternateName: MOTIONLABS.alternateName,
    url: MOTIONLABS.url,
    // 한국 사업자등록번호. taxID가 표준 필드이고, 사람이 읽을 라벨은 identifier에 둔다.
    taxID: MOTIONLABS.taxId,
    identifier: {
      "@type": "PropertyValue",
      name: "사업자등록번호",
      value: MOTIONLABS.taxId,
    },
    founder: { "@type": "Person", name: MOTIONLABS.founder },
    address: {
      "@type": "PostalAddress",
      streetAddress: MOTIONLABS.streetAddress,
      addressLocality: MOTIONLABS.addressLocality,
      addressRegion: MOTIONLABS.addressRegion,
      addressCountry: MOTIONLABS.addressCountry,
    },
    contactPoint: {
      "@type": "ContactPoint",
      contactType: "customer support",
      telephone: MOTIONLABS.telephone,
      email: MOTIONLABS.email,
      areaServed: "KR",
      availableLanguage: ["ko"],
    },
    sameAs: [MOTIONLABS.url],
  }
}

/**
 * 서비스 — 무엇을 파는가.
 *
 * `Service`를 쓰고 `Product`를 쓰지 않는 이유: 이건 재고가 있는 물건이 아니라 운영
 * 대행이다. `offers`에 가격을 넣지 않는 이유: 랜딩에 가격을 공개하지 않으므로,
 * 넣으면 화면에 없는 값을 기계에만 말하는 셈이 된다.
 */
export function buildServiceJsonLd(siteUrl: string): Record<string, unknown> {
  const base = siteUrl.replace(/\/$/, "")
  return {
    "@context": "https://schema.org",
    "@type": "Service",
    "@id": `${base}/#service`,
    name: "Re:putation",
    serviceType: "AI 답변 노출 진단 및 근거 콘텐츠 운영",
    description:
      "ChatGPT와 Gemini에 환자 질문을 실제로 보내 병원이 답변에 몇 번 언급되는지 측정하고, " +
      "병원 정보를 AI가 읽는 형태로 정리해 근거 기반 콘텐츠를 매달 발행합니다.",
    provider: { "@id": `${base}/#organization` },
    areaServed: { "@type": "Country", name: "대한민국" },
    audience: { "@type": "Audience", audienceType: "의원·병원" },
    url: base,
  }
}

/** 사이트 자체. */
export function buildWebSiteJsonLd(siteUrl: string): Record<string, unknown> {
  const base = siteUrl.replace(/\/$/, "")
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "@id": `${base}/#website`,
    name: "Re:putation",
    url: base,
    inLanguage: "ko-KR",
    publisher: { "@id": `${base}/#organization` },
  }
}

/**
 * FAQ — 화면에 있는 질문과 **같은 것만** 넣는다.
 *
 * 접힌 질문(`<details>` 안)도 DOM에 있고 사람이 열 수 있으므로 포함한다. 화면에 없는
 * 질문을 여기에만 넣으면 구조화 데이터와 본문이 어긋나고, 그건 스팸 신호다.
 */
export function buildLandingFaqJsonLd(
  faqs: readonly LandingFaq[],
  siteUrl: string,
): Record<string, unknown> | null {
  const usable = faqs.filter((f) => f.question.trim() && f.answer.trim())
  if (usable.length === 0) return null

  const base = siteUrl.replace(/\/$/, "")
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "@id": `${base}/#faq`,
    inLanguage: "ko-KR",
    mainEntity: usable.map((f) => ({
      "@type": "Question",
      name: f.question,
      acceptedAnswer: { "@type": "Answer", text: f.answer },
    })),
  }
}
