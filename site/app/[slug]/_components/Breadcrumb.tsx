import Link from 'next/link'

import type { BreadcrumbItem } from '@/lib/breadcrumb'

// JSON-LD 빌더는 lib에 있고(테스트 러너가 lib/*.test.ts만 실행) 여기서는 그대로 재노출한다 —
// 페이지들이 화면용 Breadcrumb와 같은 자리에서 가져다 쓰던 기존 import 경로를 유지하기 위함.
export { buildBreadcrumbJsonLd } from '@/lib/breadcrumb'
export type { BreadcrumbItem } from '@/lib/breadcrumb'

interface Props {
  items: BreadcrumbItem[]
}

export function Breadcrumb({ items }: Props) {
  if (items.length === 0) return null
  return (
    <nav className="clinic-breadcrumb" aria-label="경로">
      {items.map((item, index) => {
        const isLast = index === items.length - 1
        return (
          <span key={`${item.label}-${index}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {item.href && !isLast ? (
              <Link href={item.href}>{item.label}</Link>
            ) : (
              <span className="clinic-breadcrumb-current">{item.label}</span>
            )}
            {!isLast && <span className="clinic-breadcrumb-separator" aria-hidden="true">›</span>}
          </span>
        )
      })}
    </nav>
  )
}
