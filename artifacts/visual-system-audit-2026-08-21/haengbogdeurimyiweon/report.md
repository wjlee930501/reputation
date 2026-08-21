# 행복드림의원 공개 사이트 시각 감사

실행일: 2026-08-21 (Asia/Seoul)

## Audit scope

라이브 공개 표면 `https://reputation.motionlabs.kr/haengbogdeurimyiweon`의 6개 화면을 Aside REPL로 새로 캡처했다. 대상은 base home, `/contents`, 목록에서 확인한 첫 콘텐츠 상세, `/doctor`, `/treatments`, `/visit`이며, 각 화면을 desktop 1440×900과 mobile 390×844에서 확인했다. 모든 PNG는 화면 캡처 직후 파일 크기·PNG 차원·SHA-256을 확인하고 저장 파일을 다시 열어 육안 검수했다.

## Capture inventory

| surface | desktop | mobile | document height (desktop/mobile) | root width check |
|---|---|---|---:|---|
| base home | [base-desktop-1440x900.png](./base-desktop-1440x900.png) | [base-mobile-390x844.png](./base-mobile-390x844.png) | 7365 / 11223 | 1440=1440 / 390=390 |
| contents | [contents-desktop-1440x900.png](./contents-desktop-1440x900.png) | [contents-mobile-390x844.png](./contents-mobile-390x844.png) | 2346 / 2730 | 1440=1440 / 390=390 |
| first content detail | [content-detail-first-desktop-1440x900.png](./content-detail-first-desktop-1440x900.png) | [content-detail-first-mobile-390x844.png](./content-detail-first-mobile-390x844.png) | 4744 / 7459 | 1440=1440 / 390=390 |
| doctor | [doctor-desktop-1440x900.png](./doctor-desktop-1440x900.png) | [doctor-mobile-390x844.png](./doctor-mobile-390x844.png) | 2459 / 3337 | 1440=1440 / 390=390 |
| treatments | [treatments-desktop-1440x900.png](./treatments-desktop-1440x900.png) | [treatments-mobile-390x844.png](./treatments-mobile-390x844.png) | 2754 / 3521 | 1440=1440 / 390=390 |
| visit | [visit-desktop-1440x900.png](./visit-desktop-1440x900.png) | [visit-mobile-390x844.png](./visit-mobile-390x844.png) | 2262 / 3564 | 1440=1440 / 390=390 |

세부 DOM/이미지/CTA/H1 측정값은 [metrics.json](./metrics.json), 캡처 서명은 [checksums.txt](./checksums.txt), 화면별 접근성 트리는 각 `*.snapshot.txt`에 있다.

## Strengths

- 정보 구조가 여섯 화면에서 일관된다. 상단 병원명·섹션 내비게이션, breadcrumb, 제목, 설명, 본문 순서가 유지된다.
- 모바일 reflow는 실제 390px에서 안정적이다. 여섯 화면 모두 `scrollWidth === clientWidth === 390`이고, desktop도 1440px에서 동일하다. 수평 페이지 잘림은 확인되지 않았다.
- home의 전화 상담/오시는 길, visit의 전화하기/길찾기/진료시간 보기처럼 환자 행동 CTA가 화면 목적에 맞게 반복 노출된다.
- contents는 실제 6편을 `전체 6 / FAQ 1 / 시술 안내 3 / 원장 칼럼 1 / 건강 정보 1`로 노출한다. 빈 상태나 로딩 화면은 확인되지 않았다.
- treatments는 5개 진료 영역을 단순한 행 목록으로 보여 주어 desktop에서 비교·스캔이 쉽고, mobile에서도 설명이 자연스럽게 줄바꿈된다.

## Key findings (top 5)

### 1. 브랜드 토큰이 실제 UI에 반영되지 않음 — High, system implication

요청 기준 primary `#D6A72C`, accent `#6F8A56`가 여섯 화면의 computed style에서 모두 0회였고, 캡처에서는 blue/navy 계열(`rgb(21, 88, 200)`, `rgb(48, 58, 72)`)이 CTA·링크·강조에 사용된다. 방문자의 병원 브랜드 인지와 화면 간 토큰 일관성을 해치는 시스템 수준 리스크다. [brand-color-check.json](./brand-color-check.json), [base-desktop-1440x900.png](./base-desktop-1440x900.png), [visit-desktop-1440x900.png](./visit-desktop-1440x900.png)

### 2. 이미지 대체 텍스트가 비어 있는 표면이 반복됨 — High, accessibility risk

브라우저 DOM 측정에서 base 6개 중 3개, contents 1개 중 1개, 콘텐츠 상세 1개 중 1개, doctor 1개 중 1개의 `img`가 빈 `alt`를 가졌다. 화면의 시각 정보는 보이지만 스크린 리더 사용자에게 사진의 역할·대상이 전달되지 않는다. 장식 이미지라면 `alt=""`의 의도를 명확히 하고, 원장·병원 공간·대표 콘텐츠 이미지라면 설명형 대체 텍스트를 제공해야 한다. [metrics.json](./metrics.json), [doctor-mobile-390x844.png](./doctor-mobile-390x844.png)

### 3. 원장 모바일 카드에서 `대표원장`이 중복 표기됨 — Medium, UX risk

doctor mobile에서 사진 오른쪽에 파란 `대표원장` 배지가 이름 위·아래로 반복된다. 같은 정보가 연속되어 시선이 분산되고, 이름보다 라벨이 강조된다. 한 위치만 남기고 이름·직함의 계층을 분리하는 편이 명확하다. [doctor-mobile-390x844.png](./doctor-mobile-390x844.png), [doctor-mobile-390x844.snapshot.txt](./doctor-mobile-390x844.snapshot.txt)

### 4. 모바일 콘텐츠 필터가 두 줄로 접힘 — Low/Medium, UX risk

contents mobile의 5개 필터가 `건강 정보 1`을 두 번째 줄로 밀어낸다. 페이지 폭을 침범하지 않는다는 점은 좋지만, 필터 그룹의 높이가 변하고 마지막 필터가 첫 줄과 분리되어 선택 집합으로 읽히는 속도가 떨어진다. 작은 화면에서는 가로 스크롤 필터 또는 동일 폭의 2열 grid 중 하나로 의도를 명시하는 것이 낫다. [contents-mobile-390x844.png](./contents-mobile-390x844.png)

### 5. 본문 표면의 정보량 대비 강한 whitespace — Low, system implication

contents/doctor/treatments/visit desktop의 상단 소개 영역과 본문 사이에 넓은 여백이 반복되고, 첫 콘텐츠 상세는 본문이 화면 아래로 밀린다. 차분한 의료 톤에는 맞지만 1440×900에서 핵심 정보 도달 시간이 길어진다. 공통 page-intro spacing token을 줄이거나 첫 콘텐츠/진료 CTA를 intro 아래에 더 빨리 배치하는 것이 전체 템플릿 개선으로 파급된다. [contents-desktop-1440x900.png](./contents-desktop-1440x900.png), [doctor-desktop-1440x900.png](./doctor-desktop-1440x900.png), [treatments-desktop-1440x900.png](./treatments-desktop-1440x900.png)

## Accessibility and verification limits

- 확인 가능 범위: 화면의 읽기 순서, 제목 존재, 대체 텍스트 값, 실제 390/1440 reflow, root 수평 overflow, CTA의 보이는 이름과 href.
- 확인 불가 범위: 키보드 tab 순서·focus ring, 실제 스크린 리더 발화, 색상 대비의 전수 WCAG 계산, 외부 전화/지도 링크의 최종 목적지 성공 여부.
- visible viewport는 모두 안정된 상태로 캡처했다. home의 viewport 밖 사진은 lazy loading 상태가 DOM 측정에 남아 있어, 화면 밖 리소스 로딩 성공까지의 보증으로 해석하지 않는다.

## Recommended next checks

1. 공통 theme token에 `#D6A72C`/`#6F8A56`를 연결하고 six-route visual diff를 다시 실행한다.
2. 이미지 소스별 alt 정책을 정하고 empty-alt 목록을 CI 검증한다.
3. doctor mobile의 직함 중복과 contents mobile filter grouping을 정리한 뒤 390px 캡처를 재검증한다.
