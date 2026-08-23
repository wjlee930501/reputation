// 자료 URL을 등록하기 전, 서버가 거절할 URL을 화면에서 먼저 알려 준다.
//
// 유튜브 채널 홈은 본문이 없어 근거로 쓸 수 없다. 서버(`_is_youtube_channel_home`)는
// 크롤링 요청을 422로 거절하지만, 그전까지 운영자는 제목까지 입력하고 저장을 누른
// 뒤에야 이유를 본다. 판정 규칙은 서버와 같게 유지한다.

const YOUTUBE_HOSTS = new Set(['youtube.com', 'm.youtube.com', 'music.youtube.com'])
const VIDEO_PATH_PREFIXES = ['/watch', '/shorts/', '/embed/', '/live/']
const CHANNEL_PATH_PREFIXES = ['/@', '/channel/', '/c/', '/user/']

/** 개별 영상이 아닌 유튜브 채널 홈·목록 URL인가. */
export function isYoutubeChannelHomeUrl(value: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(value.trim())
  } catch {
    return false
  }
  const host = parsed.hostname.toLowerCase().replace(/^www\./, '')
  if (host === 'youtu.be') return false
  if (!YOUTUBE_HOSTS.has(host)) return false
  if (VIDEO_PATH_PREFIXES.some((prefix) => parsed.pathname.startsWith(prefix))) return false
  if (parsed.searchParams.has('v')) return false
  return CHANNEL_PATH_PREFIXES.some((prefix) => parsed.pathname.startsWith(prefix))
}

export const YOUTUBE_CHANNEL_HOME_WARNING =
  '유튜브 채널 홈은 본문이 없어 근거로 저장되지 않습니다. 개별 영상 URL을 넣어 주세요.'

/** 등록 전에 보여줄 경고. 없으면 null — 저장을 막지는 않는다. */
export function sourceUrlWarning(value: string): string | null {
  return isYoutubeChannelHomeUrl(value) ? YOUTUBE_CHANNEL_HOME_WARNING : null
}
