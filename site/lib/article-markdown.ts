export interface ArticleHeading {
  id: string
  label: string
}

export function stripLeadingMarkdownH1(body: string | null | undefined): string {
  if (!body) return ''
  // Page chrome owns the only H1. Generated legacy bodies may still begin with a
  // duplicate markdown H1, so remove just that leading heading at render time.
  return body.replace(/^\s*#\s+[^\n]+\n+/, '').trimStart()
}

export function articleHeadingId(label: string): string {
  const normalized = label
    .toLowerCase()
    .trim()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
  return `section-${normalized || 'content'}`
}

export function extractArticleHeadings(body: string): ArticleHeading[] {
  return body
    .split('\n')
    .map((line) => line.match(/^#{1,2}\s+(.+?)\s*#*$/)?.[1]?.trim() ?? '')
    .filter(Boolean)
    .slice(0, 8)
    .map((label) => ({ id: articleHeadingId(label), label }))
}
