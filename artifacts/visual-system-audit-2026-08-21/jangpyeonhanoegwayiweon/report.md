# 장편한외과의원 공개 사이트 시각 시스템 감사

- 감사일: 2026-08-21 (Asia/Seoul)
- 대상: [https://jangclinic.kr/](https://jangclinic.kr/)
- 공개 표면: `/`, `/contents`, 첫 공개 콘텐츠 상세 `/contents/a30cf244-455c-4758-aaf6-f4c6affcd7dd`, `/doctor`, `/treatments`, `/visit`
- 캡처 방식: Aside REPL에서 `snapshot()`을 먼저 읽고 실제 브라우저 상태를 캡처. 최종 증거는 viewport-only PNG로 저장했다. Aside의 `fullPage:true`는 반응형 에뮬레이션에서 상단 화면이 반복 타일로 저장되어 거부했다.
- 뷰포트: desktop 1440×900, mobile 390×844
- 데이터 특성 확인: 콘텐츠 19편, 병원 사진 8장(홈), 진료 영역 12개, 원장 사진/hero 존재

## 공통 요약

장편한외과의원 공개 표면은 신뢰감 있는 원장 hero, 명확한 전화 상담 CTA, 의료 정보 허브·진료 영역·방문 안내의 일관된 정보 구조가 강점이다. desktop과 mobile 모두 `scrollWidth === clientWidth`로 유지되어 페이지 전체 가로 넘침은 없었고, 모바일은 하단 고정 상담/진료시간/진료안내/길찾기 바가 핵심 행동을 보조한다.

공통 리스크는 콘텐츠 상세 모바일의 markdown 표가 620px로 렌더되어 312px 컨테이너를 넘는 점, 상세/의료진/콘텐츠 카드 이미지의 DOM `alt`가 빈 문자열인 점, 그리고 화면 전반의 CTA 색이 짙은 네이비·밝은 블루 중심으로 보여 제공된 primary `#17365D`·accent `#B79045` 토큰과 시각적으로 완전히 일치하지 않는 점이다. 금색 accent는 캡처된 핵심 CTA에서 거의 노출되지 않았다.

## 화면별 감사

### 1. 홈 `/`

![홈 desktop](./01-desktop-1440x900.png)
![홈 mobile](./01-mobile-390x844.png)

- Strength: desktop은 텍스트/원장 hero 사진의 50:50 분할이 명확하고, 전화 상담·오시는 길 CTA가 첫 화면에 있다. mobile은 H1이 3줄로 자연스럽게 리플로우되고 원장 사진의 얼굴과 손동작이 잘 보인다. 8개 이미지 중 실패 이미지는 0개이며 홈에서 제공된 병원 사진·원장 hero 자산이 브랜드 신뢰를 만든다.
- UX risk: 정보량이 큰 홈은 7,558px(desktop) / 12,216px(mobile)로 길다. 첫 화면의 주요 진료 4개만 노출되므로 12개 진료 영역과 19개 콘텐츠로 이동하는 경로가 후속 섹션에 분산된다.
- Accessibility risk: H1은 desktop/mobile 모두 3줄이며 큰 글자 대비는 좋지만, 모바일 상단 2행 네비게이션과 하단 고정 바가 동시에 존재해 키보드/스크린리더 순서와 고정 요소의 가림 여부는 DOM·focus 테스트가 추가로 필요하다. 홈의 사진은 alt가 비교적 잘 제공되지만 콘텐츠 대표 이미지 1장은 빈 alt다.
- System-level implication: hero, `#17365D` 계열 네이비 헤더, 블루 CTA, 모바일 고정 행동 바가 모든 공개 라우트의 공통 shell로 재사용되고 있다. 공통 shell의 focus/target-size/색상 토큰을 한 번 고치면 6개 화면에 파급된다.

### 2. 콘텐츠 목록 `/contents`

![콘텐츠 목록 desktop](./02-desktop-1440x900.png)
![콘텐츠 목록 mobile](./02-mobile-390x844.png)

- Strength: H1 아래 `전체 19`, `FAQ 10`, `질환 가이드 4`, `시술 안내 1`, `원장 칼럼 2`, `건강 정보 2` 필터가 노출되어 콘텐츠 규모와 taxonomy가 즉시 이해된다. mobile은 필터가 2행으로 잘 감기며 카드 이미지가 390px 폭 안에 맞는다.
- UX risk: desktop 첫 화면에서는 대표 카드의 이미지와 일부 제목만 보이고 제목/읽기 행동은 fold 아래로 밀린다. mobile도 대표 카드 제목이 첫 화면 하단에서 잘려 있어 “무엇을 읽을지” 판단하기 전에 스크롤이 필요하다.
- Accessibility risk: 필터는 링크형으로 보이며 색 대비는 양호하지만 선택 상태를 색상만으로 전달하는지, 대표 이미지의 빈 alt가 장식 이미지인지 의미 이미지인지 확인이 필요하다.
- System-level implication: 콘텐츠 수와 유형 필터는 운영 데이터(19편)를 직접 노출하는 공통 허브 패턴이다. 유형 추가/편수 변경 시 필터 줄바꿈과 대표 카드 높이를 함께 회귀해야 한다.

### 3. 첫 공개 콘텐츠 상세 `/contents/a30cf244-455c-4758-aaf6-f4c6affcd7dd`

![콘텐츠 상세 desktop](./03-desktop-1440x900.png)
![콘텐츠 상세 mobile](./03-mobile-390x844.png)

- Strength: desktop은 본문 2/3 + 병원 정보/환자 질문 sidebar로 근거와 상담 경로를 함께 보여준다. mobile은 제목이 2줄, FAQ 라벨, 작성 기준·업데이트·출처·검수 메타가 순서대로 보여 신뢰 신호가 좋다.
- UX risk: desktop의 sidebar CTA가 mobile에서는 첫 화면에 노출되지 않아, 읽기 시작한 사용자가 바로 전화 상담으로 전환하려면 상단 헤더의 작은 링크를 이용해야 한다. 긴 본문(6,749px desktop / 10,871px mobile)에서는 관련 FAQ가 후반으로 밀린다.
- Accessibility risk: **FAIL** — mobile markdown table은 `scrollWidth: 620`, `clientWidth: 312`로 측정되어 가로 스크롤 또는 내용 잘림 위험이 있다. 표를 카드/스택 레이아웃으로 변환하거나 명시적인 스크롤 영역과 안내를 제공해야 한다. 상세 대표 이미지는 빈 alt이며, 제목의 2~3줄 리플로우 뒤 focus/읽기 순서는 추가 검증이 필요하다.
- System-level implication: 생성 콘텐츠의 markdown renderer가 표·긴 제목·출처를 모든 상세 페이지에 적용한다. 이 renderer의 responsive table 처리와 이미지 alt 생성 규칙을 공통으로 보강해야 한다.

### 4. 의료진 `/doctor`

![의료진 desktop](./04-desktop-1440x900.png)
![의료진 mobile](./04-mobile-390x844.png)

- Strength: desktop은 원장 portrait와 긴 약력의 2열 구성으로 전문성을 전달하고, mobile은 사진·이름·전문 분야를 한 화면에 함께 배치한다. 병원명·전문과·지역 메타가 H1 바로 아래에 있어 신뢰/지역 맥락이 빠르다.
- UX risk: mobile portrait가 작고 약력 본문이 즉시 이어져 사진의 증거 가치가 줄어든다. 원장 칼럼 링크는 화면 후반 콘텐츠 목록으로 내려가므로 전문성에서 콘텐츠로 넘어가는 CTA가 약하다.
- Accessibility risk: 의료진 portrait `img`의 DOM alt가 빈 문자열이다. 의미 있는 인물 사진이라면 `이성근 원장`과 역할을 alt 또는 인접 텍스트로 노출해야 한다. 작은 mobile portrait와 텍스트 대비 외의 실제 focus/zoom 동작은 추가 테스트가 필요하다.
- System-level implication: 원장 프로필은 홈 hero·의료진·콘텐츠 작성자 metadata가 공유하는 entity다. 이름/전문분야/사진 대체텍스트를 단일 데이터 소스에서 공급해야 불일치를 막을 수 있다.

### 5. 진료 영역 `/treatments`

![진료 영역 desktop](./05-desktop-1440x900.png)
![진료 영역 mobile](./05-mobile-390x844.png)

- Strength: 12개 진료 항목이 desktop에서는 이름-설명-화살표의 행 구조로 빠르게 훑히고, mobile에서는 한 열로 자연스럽게 쌓인다. 첫 두 항목의 설명도 390px 안에서 잘리지 않고 질환/검사 범위를 폭넓게 보여준다.
- UX risk: mobile은 설명이 길어 첫 화면에서 2개 항목만 보인다. “진료 상담” 같은 상위 CTA가 목록 상단에 없어 읽기와 상담 전환이 분리된다.
- Accessibility risk: 각 행의 작은 chevron만으로도 클릭 가능해 보이므로 터치 target 크기와 링크의 accessible name이 충분한지 추가 측정이 필요하다. 이미지가 없는 화면이라 정보가 텍스트에만 의존한다.
- System-level implication: 진료 영역 12개는 홈의 대표 4개와 상세 목록을 연결하는 taxonomy다. 행/카드 컴포넌트의 길이·target·focus 규칙을 공통화해야 한다.

### 6. 방문 안내 `/visit`

![방문 안내 desktop](./06-desktop-1440x900.png)
![방문 안내 mobile](./06-mobile-390x844.png)

- Strength: desktop의 전화하기(파란 primary)·길찾기·진료시간 보기 3-card CTA는 목적별 구분이 분명하고, mobile에서는 전화하기가 full-width로 먼저 노출된다. 주소·전화번호·운영시간 안내가 H1 주변에서 즉시 확인된다.
- UX risk: mobile에서 길찾기/진료시간 카드가 fold 아래로 내려가며, 지도·주차·대중교통 세부는 더 스크롤해야 한다. 외부 채널이 많아 사용자가 전화/지도/홈페이지 중 무엇을 선택할지 판단하는 비용이 있다.
- Accessibility risk: 카드 전체가 링크인지, 내부 텍스트와 아이콘이 하나의 accessible name으로 읽히는지 추가 확인이 필요하다. 지도·외부 채널 링크는 새 창/외부 이동을 명시하면 예측 가능성이 높아진다.
- System-level implication: 전화/지도/시간 CTA는 홈과 mobile 고정 바에도 중복된다. 전화번호·주소 변경 시 visit, home, header/footer, fixed bar를 동시에 회귀해야 한다.

## 핵심 5개 findings

1. **P1 반응형 표 overflow:** 콘텐츠 상세 mobile의 markdown table이 620px로 312px 컨테이너를 초과한다. 표 전용 responsive renderer가 필요하다.
2. **P1 의미 이미지 alt 누락:** 콘텐츠 카드/상세 대표 이미지와 의료진 portrait의 DOM alt가 빈 문자열이다. 의미 이미지와 장식 이미지를 데이터에서 구분해야 한다.
3. **P1 상세 mobile 전환 CTA 약화:** desktop sidebar의 병원 정보/전화 CTA가 mobile 첫 화면에 없다. 헤더의 전화 상담만으로는 콘텐츠 읽기 맥락의 전환이 약하다.
4. **P2 콘텐츠 탐색 fold:** `/contents` 대표 카드의 제목·읽기 행동이 desktop/mobile 모두 첫 화면 아래로 일부 밀린다. 대표 카드 상단에 제목과 최소 메타/읽기 CTA를 당기는 편이 좋다.
5. **P2 디자인 토큰 드리프트:** 캡처된 CTA는 네이비와 밝은 블루가 주도하고 제공된 accent `#B79045`는 핵심 행동에서 거의 보이지 않는다. 공통 shell의 색상 토큰을 브랜드 기준과 대조해 결정해야 한다.

## 측정 요약

정확한 원본 측정은 [`metrics.json`](./metrics.json)에 저장했다. 모든 최종 PNG에서 `pixelWidth/pixelHeight`를 로컬 `sips`로 확인했다.

| route | viewport | H1 | scrollWidth/clientWidth | document height | images (count / failed) |
|---|---:|---:|---:|---:|---:|
| `/` | 1440×900 | 1 | 1440/1440 | 7558 | 8 / 0 |
| `/` | 390×844 | 1 | 390/390 | 12216 | 8 / 0 |
| `/contents` | 1440×900 | 1 | 1440/1440 | 4184 | 1 / 0 |
| `/contents` | 390×844 | 1 | 390/390 | 5086 | 1 / 0 |
| detail | 1440×900 | 1 | 1440/1440 | 6749 | 1 / 0 |
| detail | 390×844 | 1 | 390/390; table 620/312 | 10871 | 1 / 0 |
| `/doctor` | 1440×900 | 1 | 1440/1440 | 2530 | 1 / 0 |
| `/doctor` | 390×844 | 1 | 390/390 | 3709 | 1 / 0 |
| `/treatments` | 1440×900 | 1 | 1440/1440 | 3695 | 0 / 0 |
| `/treatments` | 390×844 | 1 | 390/390 | 5862 | 0 / 0 |
| `/visit` | 1440×900 | 1 | 1440/1440 | 2495 | 5 / 0 |
| `/visit` | 390×844 | 1 | 390/390 | 4281 | 5 / 0 |

## 캡처 invocation / 증거 한계

각 화면은 다음 순서로 실행했다: `openTab(url)` → `setVp(width,height)` via CDP `Emulation.setDeviceMetricsOverride` → `snapshot(page,{interactive:true})` → 안정화 대기 및 top reset → `page.screenshot(...)`. desktop은 `fullPage:false`, mobile은 `clip:{x:0,y:0,width:390,height:844}`를 사용했다. 캡처 PNG는 첫 화면만 증거하므로 전체 스크롤 중간의 sticky/focus/키보드 상태, 실제 screen reader 순서, 색상 대비 수치, 터치 target 수치는 이 감사에서 확정하지 않았다.
