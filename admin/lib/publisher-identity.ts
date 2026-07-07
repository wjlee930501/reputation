// 발행 담당자 이름의 기본값을 관리한다.
//
// 이전에는 localStorage(브라우저에 영구 저장)에 담당자 이름을 전역으로 저장했다 —
// 공용 PC에서 로그아웃 없이 다음 AE가 이어서 쓰면 이전 AE 이름으로 발행될 위험이 있었다.
// 이제는 로그인 세션과 함께 사라지는 sessionStorage를 쓰고, 로그인 응답으로 받은
// 계정 이름을 기본값으로 우선 사용한다 (AE가 화면에서 언제든 직접 수정 가능).
const ACCOUNT_NAME_KEY = 'reputation.currentAccountName'
const PUBLISHER_OVERRIDE_KEY = 'reputation.publisherNameOverride'

/** override가 있으면(빈 문자열로 지운 경우 포함) override를, 없으면 로그인 계정 이름을 기본값으로 쓴다. */
export function resolveDefaultPublisherName(accountName: string | null, override: string | null): string {
  if (override !== null) return override
  return accountName ?? ''
}

export function storeCurrentAccountName(name: string): void {
  if (typeof window === 'undefined') return
  window.sessionStorage.setItem(ACCOUNT_NAME_KEY, name)
}

export function readPublisherIdentity(): string {
  if (typeof window === 'undefined') return ''
  const accountName = window.sessionStorage.getItem(ACCOUNT_NAME_KEY)
  const override = window.sessionStorage.getItem(PUBLISHER_OVERRIDE_KEY)
  return resolveDefaultPublisherName(accountName, override)
}

export function storePublisherNameOverride(value: string): void {
  if (typeof window === 'undefined') return
  window.sessionStorage.setItem(PUBLISHER_OVERRIDE_KEY, value)
}

/** 로그아웃 시 호출 — 다음 로그인 전까지 이전 담당자 이름이 남지 않게 한다. */
export function clearPublisherIdentity(): void {
  if (typeof window === 'undefined') return
  window.sessionStorage.removeItem(ACCOUNT_NAME_KEY)
  window.sessionStorage.removeItem(PUBLISHER_OVERRIDE_KEY)
}
