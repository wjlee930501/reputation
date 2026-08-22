import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isYoutubeChannelHomeUrl,
  sourceUrlWarning,
  YOUTUBE_CHANNEL_HOME_WARNING,
} from './source-url-warnings.ts'

// 판정 규칙은 백엔드 `_is_youtube_channel_home`과 같아야 한다. 한쪽만 바뀌면 화면은
// 통과시키고 서버가 422로 거절하거나, 반대로 쓸 수 있는 URL에 경고가 붙는다.

test('channel home and listing URLs are flagged', () => {
  for (const url of [
    'https://www.youtube.com/@janpyeonhan',
    'https://youtube.com/channel/UCabcdef123',
    'https://www.youtube.com/c/clinicchannel',
    'https://m.youtube.com/user/clinicchannel',
    'https://music.youtube.com/channel/UCabcdef123',
    'https://www.youtube.com/@janpyeonhan/videos',
  ]) {
    assert.equal(isYoutubeChannelHomeUrl(url), true, url)
    assert.equal(sourceUrlWarning(url), YOUTUBE_CHANNEL_HOME_WARNING, url)
  }
})

test('individual videos and non-YouTube URLs stay unflagged', () => {
  for (const url of [
    'https://www.youtube.com/watch?v=abcdef12345',
    'https://youtube.com/shorts/abcdef12345',
    'https://www.youtube.com/embed/abcdef12345',
    'https://www.youtube.com/live/abcdef12345',
    'https://youtu.be/abcdef12345',
    'https://blog.naver.com/clinic/223456789',
    'https://janpyeonhan.co.kr/about',
  ]) {
    assert.equal(isYoutubeChannelHomeUrl(url), false, url)
    assert.equal(sourceUrlWarning(url), null, url)
  }
})

test('a channel path carrying a video id is treated as a video', () => {
  assert.equal(isYoutubeChannelHomeUrl('https://www.youtube.com/@clinic?v=abcdef12345'), false)
})

test('partial or malformed input never throws and never warns', () => {
  for (const value of ['', '   ', 'youtube.com/@clinic', 'https://', 'not a url']) {
    assert.equal(sourceUrlWarning(value), null, JSON.stringify(value))
  }
})
