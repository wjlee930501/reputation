// BreadcrumbList JSON-LD 빌더 (순수 함수).
//
// 컴포넌트(app/[slug]/_components/Breadcrumb.tsx)에 두면 테스트 러너(lib/*.test.ts, JSX 미지원)가
// 검증할 수 없어 로직만 여기로 분리한다 — 컴포넌트는 이 빌더를 다시 export 한다.

export interface BreadcrumbItem {
  label: string
  href?: string
}

/**
 * BreadcrumbList JSON-LD.
 *
 * Google은 마지막 항목을 제외한 모든 ListItem에 `item`이 없으면 breadcrumb rich result 자체를
 * 폐기한다. 화면용 breadcrumb에는 링크 없는 중간 라벨(예: 콘텐츠 유형)이 있을 수 있으므로,
 * 구조화 데이터에서는 그런 항목을 빼고 position을 다시 매긴다(빠진 자리로 번호가 끊기면
 * 그 역시 무효 판정 대상).
 */
export function buildBreadcrumbJsonLd(
  items: BreadcrumbItem[],
  baseUrl: string,
): Record<string, unknown> {
  const lastIndex = items.length - 1
  const linkedItems = items.filter((item, idx) => Boolean(item.href) || idx === lastIndex)
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: linkedItems.map((item, idx) => ({
      '@type': 'ListItem',
      position: idx + 1,
      name: item.label,
      ...(item.href
        ? { item: new URL(item.href, `${baseUrl.replace(/\/$/, '')}/`).toString() }
        : {}),
    })),
  }
}
