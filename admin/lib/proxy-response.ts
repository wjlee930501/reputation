const FORWARDED_RESPONSE_HEADERS = [
  'content-disposition',
  'content-type',
  'location',
]

export function buildProxyResponse(upstream: Response): Response {
  const headers = new Headers()
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name)
    if (value) headers.set(name, value)
  }
  headers.set('cache-control', 'no-store, private')

  const body = upstream.status === 204 || upstream.status === 304 ? null : upstream.body
  return new Response(body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  })
}
