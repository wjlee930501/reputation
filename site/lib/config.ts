const DEV_API_BASE = 'http://localhost:8000/api/v1/public'

function normalizeUrl(value: string): string {
  return value.replace(/\/$/, '')
}

function isLocalApiUrl(value: string): boolean {
  return /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::|\/|$)/i.test(value)
}

interface ResolveBaseUrlOptions {
  /** 오류 메시지에 노출할 환경변수 이름 (예: 'NEXT_PUBLIC_API_URL') */
  envName: string
  /** 비프로덕션에서 env 미설정 시 사용할 기본값 */
  devDefault: string
}

// fail-closed 공통 정책: 프로덕션에서 env가 비어 있거나 localhost를 가리키면
// (required일 때) 빌드/요청 시점에 즉시 throw — 잘못된 배포 구성이 조용히 새지 않는다.
export function resolveBaseUrl(envValue: string | undefined, options: ResolveBaseUrlOptions & { required?: true }): string
export function resolveBaseUrl(envValue: string | undefined, options: ResolveBaseUrlOptions & { required: false }): string | null
export function resolveBaseUrl(
  envValue: string | undefined,
  { envName, devDefault, required = true }: ResolveBaseUrlOptions & { required?: boolean },
): string | null {
  const value = envValue?.trim()
  if (value) {
    const normalized = normalizeUrl(value)

    if (process.env.NODE_ENV === 'production' && isLocalApiUrl(normalized)) {
      if (!required) {
        return null
      }
      throw new Error(`${envName} cannot point to localhost in production`)
    }

    return normalized
  }

  if (process.env.NODE_ENV !== 'production') {
    return devDefault
  }

  if (!required) {
    return null
  }

  throw new Error(`${envName} is required in production`)
}

export function getApiBase(required: true): string
export function getApiBase(required?: false): string | null
export function getApiBase(required = true): string | null {
  const options = { envName: 'NEXT_PUBLIC_API_URL', devDefault: DEV_API_BASE } as const
  return required
    ? resolveBaseUrl(process.env.NEXT_PUBLIC_API_URL, options)
    : resolveBaseUrl(process.env.NEXT_PUBLIC_API_URL, { ...options, required: false })
}
