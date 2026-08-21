# 노원탑365의원 공개 표면 시각 QA

- 실행 시각: 2026-08-21 13:04 KST
- 범위: `https://ai.no1top365.co.kr/` 및 `/contents`, 실제 첫 콘텐츠 상세, `/doctor`, `/treatments`, `/visit`
- 캡처: desktop 1440×900 6장 + mobile 390×844 6장
- 도구: Aside REPL. 모든 캡처는 이번 실행에서 `snapshot()` 선행 후 저장·`view_image` 검수했다.
- 산출물 디렉터리: `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/`

## 종합 판정

모든 지정 route와 viewport가 새 캡처로 렌더링되었고, 페이지-level 가로 overflow는 발견되지 않았다. 홈의 전화/오시는 길 행동, 의료진 사진, 8개 진료행은 명확하다. 다만 콘텐츠가 1건뿐인 상태에서 홈·방문 표면에는 사진 DOM 인스턴스가 24/21개로 훨씬 많고, 콘텐츠 상세에는 이미지 아래 빈 예약 밴드가 보인다. 의료기관의 첫 행동(전화·길찾기)과 신뢰 정보(읽기 시간·브랜드)를 route 간 일관되게 우선할 필요가 있다.

## 이번 실행의 정확한 캡처 호출

Desktop 호출은 각 route마다 `const page = await openTab(url); await page._sendToTarget('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false,screenWidth:1440,screenHeight:900}); await snapshot(page,{interactive:true}); await page.screenshot({path:'./<route>-desktop-1440x900.png'});` 순서로 수행했다.

Mobile 호출은 각 route마다 `const page = await openTab(url); await page._sendToTarget('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true,screenWidth:390,screenHeight:844}); await snapshot(page,{interactive:true}); await page.screenshot({clip:{x:0,y:0,width:390,height:844},path:'./<route>-mobile-390x844.png'});` 순서로 수행했다. Aside 세션 파일을 evidence 디렉터리로 복사한 뒤 `file`, SHA-256, `view_image`로 서명·크기·상태를 확인했다.

## 핵심 5 findings

### F1 · P1 — 콘텐츠 상세의 이미지 아래 예약된 빈 밴드

`/contents/596a17ac-f4c8-4da3-8cc7-1e169006c184`에서 이미지와 콘텐츠 타입 사이에 연한 파란색 빈 영역이 남는다. desktop에서는 약 140px, mobile에서도 약 30px로 보이며 이미지가 잘린 뒤 빈 hero 슬롯이 남은 것처럼 읽힌다. 첫 화면에서 제목이 아래로 밀리고, 이미지 실패/로딩처럼 오해될 수 있다. 이미지 컨테이너의 aspect-ratio·object-fit·placeholder 높이를 실제 이미지 비율에 맞춰 정리해야 한다. Evidence: `art03`, `art09`.

### F2 · P1 — 희소 콘텐츠와 사진량의 불균형

`/contents`에는 실제 콘텐츠 카드가 1건뿐인데, 홈은 스크롤 후 image DOM 24개(고유 URL 23개), `/visit`은 21개다. 콘텐츠 목록은 큰 대표 이미지와 넓은 여백을 먼저 보여주므로 “읽을 정보가 적다”는 인상보다 “사진이 많은 병원” 인상이 앞선다. 카드 수/상태를 첫 화면에서 명시하고, 정보 카드와 핵심 FAQ를 사진 갤러리보다 앞에 배치하는 편이 AI 정보 허브 목적에 맞다. Evidence: `art01`, `art02`, `art06`, `art07`, `metrics.json`.

### F3 · P1 — route별 첫 화면 전화 CTA 우선순위가 다름

홈은 desktop·mobile 모두 `전화 상담`과 `오시는 길`을 영웅 영역에 두고 mobile 하단 고정 바도 제공한다. 반면 `/contents`, 상세, `/doctor`, `/treatments`의 mobile 첫 화면은 헤더의 작은 `전화 상담` 링크가 주 CTA이고, 상세는 본문 첫 화면에 전화/길찾기 행동이 없다. 응급·외상 진료 표면에서는 route가 달라도 전화·길찾기 CTA를 동일한 우선순위로 유지하는 것이 안전하다. `/visit`의 전화하기·길찾기·진료시간 카드는 좋은 기준점이다. Evidence: `art01`, `art03`, `art07`, `art09`, `art10`, `art11`, `art12`.

### F4 · P2 — 텍스트 이름은 보이지만 구별되는 브랜드 mark가 없음

헤더는 `노원탑365의원` 텍스트와 일반적인 navy/blue CTA만 노출한다. 캡처 범위에서 별도 로고 mark·브랜드 lockup은 보이지 않는다. 현재 route 간 식별은 가능하지만, 사진과 콘텐츠가 많은 표면에서 병원 브랜드 기억점이 약하다. 기존 브랜드 자산이 있다면 작은 mark+wordmark를 header·footer·콘텐츠 카드에 일관되게 사용해야 한다. Evidence: `art01`, `art02`, `art07`, `art08`.

### F5 · P2 — 동일 콘텐츠의 읽기 시간 표기가 불일치

홈 대표 카드의 동일 제목은 `4 분 분량`, 상세 metadata는 `6분 읽기`로 표기된다. 제목·작성일은 일치하므로 사용자에게는 같은 콘텐츠의 정보가 서로 다르게 보인다. 한 계산 기준과 표기(예: `약 6분 읽기`)를 목록·상세에서 공유해야 한다. Evidence: `art01`, `art03`, `art07`, `art09`.

## 확인된 강점과 관찰

- 12개 캡처 모두 비어 있거나 로그인/차단/로딩 화면이 아니며 PNG가 정확히 1440×900 또는 390×844다.
- 모든 route에서 `scrollWidth === clientWidth`였다. mobile CJK 제목과 본문은 crop 없이 1–4줄로 자연스럽게 줄바꿈됐다.
- `/treatments`는 8개 진료행을 텍스트 중심으로 제공하고 mobile에서도 행 간 구분과 화살표가 유지된다.
- 홈 mobile은 첫 화면에 전화 상담·오시는 길과 하단 고정 전화/진료시간/진료안내/길찾기 바를 함께 제공한다.
- 홈·방문에서 초기 `naturalWidth=0`은 below-fold lazy image였다. 문서 끝까지 스크롤한 뒤 확인한 결과 두 route 모두 `complete=true`, `naturalWidth>0`으로 실패 이미지는 0개였다. 따라서 이번 실행에서는 URL broken으로 판정하지 않았다.
- 접근성의 전체 준수 여부는 이 캡처만으로 판정하지 않았다. 키보드 focus 순서, 실제 screen reader announcement, contrast ratio, reduced motion은 별도 검증이 필요하다.

## `manualQa` matrix

### surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| NOW-HOME-D | public home renders at desktop | `/` · 1440×900 | `openTab('https://ai.no1top365.co.kr/'); snapshot(page,{interactive:true}); screenshot('./home-desktop-1440x900.png')` | PASS | art01, art13 |
| NOW-CONTENTS-D | content list renders at desktop | `/contents` · 1440×900 | `openTab('https://ai.no1top365.co.kr/contents'); snapshot(page,{interactive:true}); screenshot('./contents-desktop-1440x900.png')` | PASS | art02, art13 |
| NOW-CONTENT-D | first real content detail renders at desktop | `/contents/596a17ac-f4c8-4da3-8cc7-1e169006c184` · 1440×900 | `openTab(detailUrl); snapshot(page,{interactive:true}); screenshot('./content-detail-desktop-1440x900.png')` | PASS | art03, art13 |
| NOW-DOCTOR-D | doctor surface renders at desktop | `/doctor` · 1440×900 | `openTab('https://ai.no1top365.co.kr/doctor'); snapshot(page,{interactive:true}); screenshot('./doctor-desktop-1440x900.png')` | PASS | art04, art13 |
| NOW-TREATMENTS-D | treatment list renders at desktop | `/treatments` · 1440×900 | `openTab('https://ai.no1top365.co.kr/treatments'); snapshot(page,{interactive:true}); screenshot('./treatments-desktop-1440x900.png')` | PASS | art05, art13 |
| NOW-VISIT-D | visit surface renders at desktop | `/visit` · 1440×900 | `openTab('https://ai.no1top365.co.kr/visit'); snapshot(page,{interactive:true}); screenshot('./visit-desktop-1440x900.png')` | PASS | art06, art13 |
| NOW-HOME-M | public home renders at mobile | `/` · 390×844 | `openTab(origin); Emulation.setDeviceMetricsOverride(390,844); snapshot(page,{interactive:true}); screenshot(clip 390×844,'./home-mobile-390x844.png')` | PASS | art07, art13 |
| NOW-CONTENTS-M | content list renders at mobile | `/contents` · 390×844 | `openTab(contentsUrl); Emulation.setDeviceMetricsOverride(390,844); snapshot(page,{interactive:true}); screenshot(clip 390×844,'./contents-mobile-390x844.png')` | PASS | art08, art13 |
| NOW-CONTENT-M | first real content detail renders at mobile | `/contents/596a17ac-f4c8-4da3-8cc7-1e169006c184` · 390×844 | `openTab(detailUrl); Emulation.setDeviceMetricsOverride(390,844); snapshot(page,{interactive:true}); screenshot(clip 390×844,'./content-detail-mobile-390x844.png')` | PASS | art09, art13 |
| NOW-DOCTOR-M | doctor surface renders at mobile | `/doctor` · 390×844 | `openTab(doctorUrl); Emulation.setDeviceMetricsOverride(390,844); snapshot(page,{interactive:true}); screenshot(clip 390×844,'./doctor-mobile-390x844.png')` | PASS | art10, art13 |
| NOW-TREATMENTS-M | treatment list renders at mobile | `/treatments` · 390×844 | `openTab(treatmentsUrl); Emulation.setDeviceMetricsOverride(390,844); snapshot(page,{interactive:true}); screenshot(clip 390×844,'./treatments-mobile-390x844.png')` | PASS | art11, art13 |
| NOW-VISIT-M | visit surface renders at mobile | `/visit` · 390×844 | `openTab(visitUrl); Emulation.setDeviceMetricsOverride(390,844); snapshot(page,{interactive:true}); screenshot(clip 390×844,'./visit-mobile-390x844.png')` | PASS | art12, art13 |

### adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| NOW-ADV-01 | responsive reflow | viewport overflow | `scrollWidth` must equal `clientWidth`; no accidental horizontal page scroll | PASS | art01, art02, art03, art04, art05, art06, art07, art08, art09, art10, art11, art12, art13 |
| NOW-ADV-02 | image integrity | lazy/failed image | below-fold lazy images may be pending initially, but must load after real scroll; no confirmed broken image | PASS | art01, art06, art07, art12, art13 |
| NOW-ADV-03 | mobile typography | CJK line wrap/crop | H1 and body copy remain readable with no clipped glyphs or overflow | PASS | art07–art12, art13 |
| NOW-ADV-04 | sparse data | empty/repeated content state | one content item must be explicit and not be padded by fake repeated cards | PASS | art02, art08, art13 |
| NOW-ADV-05 | card behavior | horizontal card scroller | cards should not force an undiscoverable horizontal page scroll | PASS | art01, art07, art11, art13 |
| NOW-ADV-06 | visual integrity | blank reserved image/hero region | no unexplained blank band between image and content metadata | FAIL — blank band observed in detail | art03, art09, art13 |
| NOW-ADV-07 | action priority | emergency clinic CTA | phone and route actions should remain prominent on every public route | FAIL — subroutes rely mainly on small header phone link | art02, art03, art04, art05, art08, art09, art10, art11 |
| NOW-ADV-08 | content consistency | duplicate metadata | list and detail must use the same reading-time value | FAIL — 4분 vs 6분 | art01, art03, art07, art09 |
| NOW-ADV-09 | brand exposure | logo/brand discoverability | hospital name must remain visible in header and content context | PASS — text brand visible; distinct logo mark absent as noted in F4 | art01, art02, art07, art08 |

## `artifactRefs`

| id | kind | description | path |
|---|---|---|---|
| art01 | screenshot | home desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/home-desktop-1440x900.png` |
| art02 | screenshot | contents desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/contents-desktop-1440x900.png` |
| art03 | screenshot | first content detail desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/content-detail-desktop-1440x900.png` |
| art04 | screenshot | doctor desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/doctor-desktop-1440x900.png` |
| art05 | screenshot | treatments desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/treatments-desktop-1440x900.png` |
| art06 | screenshot | visit desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/visit-desktop-1440x900.png` |
| art07 | screenshot | home mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/home-mobile-390x844.png` |
| art08 | screenshot | contents mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/contents-mobile-390x844.png` |
| art09 | screenshot | first content detail mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/content-detail-mobile-390x844.png` |
| art10 | screenshot | doctor mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/doctor-mobile-390x844.png` |
| art11 | screenshot | treatments mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/treatments-mobile-390x844.png` |
| art12 | screenshot | visit mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/visit-mobile-390x844.png` |
| art13 | data | route metrics, dimensions, image-load checks, CTA/overflow observations, and PNG signatures | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/metrics.json` |
| art14 | report | this manual QA matrix and findings | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/noweontab365yiweon/report.md` |
