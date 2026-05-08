import Link from 'next/link'

export interface BreadcrumbItem {
  label: string
  href?: string
}

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

export function buildBreadcrumbJsonLd(items: BreadcrumbItem[], baseUrl: string): Record<string, unknown> {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map((item, idx) => ({
      '@type': 'ListItem',
      position: idx + 1,
      name: item.label,
      ...(item.href ? { item: `${baseUrl}${item.href}` } : {}),
    })),
  }
}
