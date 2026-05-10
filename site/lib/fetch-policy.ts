export function publicFetchInit(revalidateSeconds: number): RequestInit {
  if (process.env.NODE_ENV === 'development') {
    return { cache: 'no-store' }
  }
  return { next: { revalidate: revalidateSeconds } } as RequestInit
}
