const MARKDOWN_LINK_CHARS = /[\[\]()]/g
const CONTROL_OR_LINE_BREAK = /[\u0000-\u001f\u007f]+/g
const SPACES = /\s+/g

export function llmsTextValue(value: string | null | undefined): string {
  return (value || '')
    .replace(CONTROL_OR_LINE_BREAK, ' ')
    .replace(MARKDOWN_LINK_CHARS, '')
    .replace(SPACES, ' ')
    .trim()
}

export function llmsUrlValue(value: string | null | undefined): string | null {
  const firstLine = (value || '').split(/\r?\n/, 1)[0]?.trim()
  if (!firstLine) return null
  try {
    const url = new URL(firstLine)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}
