# 온보딩 병원 6곳 공개 사이트 디자인 시스템 감사

- 감사일: 2026-08-21
- 대상: ACTIVE + `site_live` 병원 6곳
- 캡처: Aside REPL fresh evidence
- 범위: 병원별 홈, 콘텐츠 목록, 첫 실제 콘텐츠 상세, 의료진, 진료 영역, 방문 안내
- 뷰포트: desktop 1440×900 + mobile 390×844
- 총 증거: 6병원 × 6화면 × 2뷰포트 = 72개 accepted PNG
- 판정: **FAIL at design-system approval gate — 반응형 기반은 안정적이지만, 병원별 데이터 변형·브랜드·의료진 자산을 안전하게 흡수하는 디자인 시스템으로는 아직 승인할 수 없다.**

독립 리뷰 두 개가 72/72 캡처를 별도로 열어 확인했다. 코드/시스템 리뷰는 `REVISE`, 직접 시각·CJK 리뷰는 `FAIL`을 반환했다. 공통 blocker는 dead theme plumbing, raw-color CSS, slug 전용 예외, 의료진 identity 슬롯의 일러스트형 인물 자산, article media/table 상태다.

## 감사 대상과 데이터 조건

| 병원 | 콘텐츠 | 사진 | 진료 | 브랜드 입력 | 건강 상태 | 대표 조건 |
|---|---:|---:|---:|---|---|---|
| 장편한외과의원 | 19 | 8 | 12 | primary + accent | REVISE | 풍부한 콘텐츠·실사진·별도 hero |
| 연세속시원내과의원 | 6 | 4 | 6 | 없음 | REVISE | 중간 밀도·원장 사진 |
| 행복드림의원 | 6 | 4 | 5 | gold + green | FAIL | 브랜드색 미적용·일러스트형 의료진 identity |
| 서울W내과의원 위례점 | 3 | 0 | 12 | 없음 | REVISE | 긴 병원명·사진 없음 |
| 강심장내과의원 | 3 | 0 | 12 | 없음 | REVISE | 사진 없음·콘텐츠 희소 |
| 노원탑365의원 | 1 | 22 | 8 | 없음 | REVISE | 콘텐츠 1편·사진 과다·당일 접근성 중요 |

## 화면 비교

### 실사진이 있는 경우

| 장편한외과의원 | 연세속시원내과의원 | 노원탑365의원 |
|---|---|---|
| ![장편한](jangpyeonhanoegwayiweon/01-desktop-1440x900.png) | ![연세속시원](yeonsesogsiweonnaegwayiweon/home-desktop-1440x900.png) | ![노원탑](noweontab365yiweon/home-desktop-1440x900.png) |

실사진과 진료 맥락이 맞으면 공통 50:50 hero가 신뢰를 만든다. 그러나 세 병원 모두 동일한 blue/navy·동일한 문장 구조를 사용해 병원 고유 브랜드보다 템플릿 정체성이 앞선다.

### 사진이 없거나 이미지 성격이 다른 경우

| 행복드림의원 | 서울W내과의원 위례점 | 강심장내과의원 |
|---|---|---|
| ![행복드림](haengbogdeurimyiweon/base-desktop-1440x900.png) | ![서울W](seoulwnaegwayiweon-wiryejeom/home-desktop-1440x900.png) | ![강심장](gangsimjangnaegwayiweon/home-desktop.png) |

사진이 없으면 우측 절반이 동일한 네이비 명함으로 바뀐다. 화면은 깨지지 않지만 ‘정상적인 fallback’보다 ‘미완성 hero’로 읽힌다. 행복드림은 일러스트형 의료진 자산이 실제 portrait 슬롯을 차지해, 자산의 용도·진위·승인 상태를 구분하는 온보딩 모델이 필요하다.

## 전체적으로 잘 된 점

1. 72개 캡처 모두 페이지 수준 `scrollWidth === clientWidth`였다. 390px에서 가로 페이지 overflow가 없었다.
2. 모든 route가 H1 하나를 유지했고, 긴 한국어 병원명과 제목도 잘리거나 한 글자만 고립되는 심각한 CJK 오류는 없었다.
3. 홈의 전화·진료시간·진료안내·길찾기 모바일 행동 바는 환자 과업에 직접적이다.
4. 치료 목록은 5–12개 조건에서 가로 carousel 없이 안정적으로 리플로우됐다.
5. 사진·콘텐츠가 없는 조건도 깨진 이미지나 가짜 카드로 채우지 않고 fallback을 제공한다.

## P0 — 디자인 시스템 정확성 문제

### 1. 병원별 테마 파이프라인이 UI와 연결되지 않았다

Admin/API에는 `brand_primary_color`, `brand_accent_color`, `logo_url`이 있고 `buildClinicThemeStyle()`도 `--clinic-primary`, `--clinic-accent`를 만든다. 하지만:

- 홈 root는 `buildClinicThemeStyle(hospital)`을 적용하지 않는다.
- secondary route는 변수를 주입하지만 실제 Medical Editorial CSS는 `#1558c8`, `#0a234d` 등을 직접 사용한다.
- 행복드림의 `#D6A72C`/`#6F8A56`, 장편한의 gold accent는 실화면에서 사실상 사용되지 않는다.

따라서 새 컬러 피커가 아니라 semantic token 연결이 먼저다.

권장 토큰:

- `--clinic-brand-strong`
- `--clinic-brand`
- `--clinic-brand-soft`
- `--clinic-on-brand`
- `--clinic-ink`
- `--clinic-paper`
- `--clinic-line`
- `--clinic-focus`

AE는 공식 로고와 대표색 하나를 승인하고, 나머지 ramp와 대비 안전 색상은 시스템이 파생해야 한다. 병원마다 임의의 두 색을 페이지 전체에 뿌리는 방식은 금지한다.

### 2. 공통 레이아웃이 데이터 밀도에 적응하지 않는다

- 모바일 홈 문서 높이는 약 10,000–12,000px까지 늘어난다.
- 콘텐츠 1편인 노원탑도 featured·질문·갤러리·연락 섹션의 기본 골격을 대부분 유지한다.
- 사진 22장은 갤러리에서 모두 렌더링된다.
- 진료 12개 병원은 우선순위나 그룹 없이 긴 목록을 제공한다.

병원별 예외 분기 대신 다음 세 축의 명시적 variant가 필요하다.

| 축 | 값 | 동작 |
|---|---|---|
| `contentDensity` | sparse 0–2 / standard 3–8 / rich 9+ | lead 1개, lead+rows, library 구조를 선택 |
| `mediaMode` | real-photo / brand-graphic / typographic | hero·doctor·gallery의 허용 자산과 fallback을 선택 |
| `accessMode` | urgent / appointment / specialist | 오늘 시간·전화·의료진 중 첫 화면 우선순위를 선택 |

### 3. `DESIGN.md`, 코드 주석, 실제 렌더 순서가 서로 다르다

`DESIGN.md`의 v4 정보 구조와 현재 Medical Editorial v5 CSS, 홈의 실제 섹션 순서, 장편한 전용 hero 분기가 일치하지 않는다. 문서가 구현 계약 역할을 하지 못하면 병원별 예외가 계속 늘어난다.

교정 순서:

1. 6개 데이터 fixture를 디자인 계약에 명시한다.
2. semantic theme와 density/access/media variant를 `DESIGN.md`에 먼저 확정한다.
3. primitive showcase에서 390/768/1440 상태를 검증한다.
4. 이후 실제 병원 화면을 교체한다.

### 4. 의료진 identity 자산의 provenance가 보장되지 않는다

행복드림의 hero·doctor에서 같은 일러스트형 인물이 실제 원장 identity처럼 사용된다. 화면만으로 생성 여부를 확정할 수는 없지만, 실사진과 구분되지 않는 슬롯·alt·카피로 제공되는 것 자체가 의료 신뢰 리스크다.

교정 원칙:

- `verified_real_person`, `verified_facility`, `generated_editorial`, `abstract_brand`를 데이터 모델에서 구분한다.
- hero/doctor identity에는 `verified_real_person`만 허용한다.
- 검증된 사진이 없으면 인물 대체 이미지를 만들지 않고 monogram·경력·진료 정보 중심으로 재구성한다.
- 현재 행복드림 자산은 provenance 확인 전까지 hero/doctor identity 사용을 중지하거나 검증된 실사진으로 교체한다.

## P1 — 반복해서 확인된 컴포넌트 문제

### Hero

- 장편한 외 병원은 모든 진료과를 한 줄 데이터로 합치고 같은 `증상을 정확히 확인하고 / 필요한 치료만 안내합니다` 문장을 반복한다.
- 진료과가 4개인 서울W·강심장은 모바일 H1이 길어진다.
- 사진 없는 병원은 화면 절반을 차지하는 동일 네이비 fallback을 사용한다.

개선: `display_specialty`는 1–2개만 hero에 사용하고 전체 진료과는 fact rail로 내린다. 검증되지 않은 공통 약속 문구 대신 병원 프로파일의 승인된 진료 원칙을 사용한다. 사진이 없으면 거대한 대체 portrait가 아니라 브랜드 그래픽+오늘 정보 중심 구성으로 재조합한다.

### Doctor

모바일에서 `대표원장` eyebrow와 fallback role이 연속으로 중복된다. `doctorRole = boardCerts[0] ?? '대표원장'`과 고정 eyebrow가 동시에 렌더링되는 구조 문제다. 동일 문자열이면 한 번만 렌더하고, 사진 없는 상태는 이름·전문자격·핵심 경력이 먼저 보이게 한다.

### Gallery와 이미지 자산

- 노원탑은 홈 24개, 방문 21개 이미지 DOM이 콘텐츠 1편보다 훨씬 큰 시각 비중을 가진다.
- 갤러리는 approved photo를 전부 `map()`하며 preview 상한이나 순서 모델이 없다.
- 여러 병원의 hero/content/doctor 이미지가 빈 `alt`로 관찰됐다.
- 행복드림의 의료진 이미지는 시각적으로 일러스트형이지만 실제 portrait 슬롯에 사용된다.

온보딩 자산에 `asset_kind`, `approved_usage`, `is_featured`, `sort_order`, `focal_point`, `alt_text`가 필요하다. hero/doctor는 승인된 실제 의료진 사진만 허용하고, 생성 일러스트는 콘텐츠 cover 또는 명시적 브랜드 그래픽으로만 사용한다. 홈 갤러리는 6–8장만 preview하고 나머지는 `더 보기`로 분리한다.

### 콘텐츠 상세 renderer

- 장편한·강심장 상세의 표는 mobile 내부에서 `620px > 312px` 가로 scroller가 되지만 안내가 없다.
- 노원탑·서울W 상세는 대표 이미지 아래 비어 보이는 media band가 남는다.
- 노원탑의 동일 글 읽기 시간이 홈 4분, 상세 6분으로 다르다.

표는 mobile key-value/card 변환을 기본으로 하고 원본 표가 필요한 경우에만 명시적 `좌우로 보기` scroller를 제공한다. 대표 이미지 슬롯은 하나의 aspect-ratio와 crop 정책을 가져야 한다. 읽기 시간은 API 또는 단일 공유 함수에서 계산한다.

### Route action priority

홈에만 `ClinicHero` 내부 mobile action bar가 있어 secondary route의 전화/길찾기 우선순위가 낮다. 특히 노원탑 같은 urgent access 병원은 route와 무관하게 today-hours·전화·길찾기를 동일한 우선순위로 유지해야 한다. 행동 바는 hero가 아니라 shared clinic shell이 소유해야 한다.

## P2 — 온보딩과 QA 체계

### 온보딩 입력

필수 또는 승인 게이트:

1. 공식 로고/wordmark와 출처 URL
2. 공식 대표색 1개와 대비 검증 결과
3. hero/doctor/facility 자산의 종류·사용 허용 범위·focal point
4. mobile용 짧은 진료 표시명과 승인된 진료 원칙
5. 대표 치료·대표 콘텐츠·대표 사진의 순서
6. `urgent`, `appointment`, `specialist` 중 접근 유형

공식 홈페이지에서 로고·색상을 자동 추출할 수 있지만 **추천값으로만** 저장하고 AE 승인을 거쳐야 한다.

### 회귀 fixture

실제 병원 이름을 하드코딩한 스냅샷만 두지 말고 다음 조합을 가진 6개 fixture를 유지한다.

- 콘텐츠: 0 / 1 / 3 / 6 / 19+
- 사진: 0 / 1 / 4 / 8 / 22+
- 진료: 5 / 6 / 8 / 12+
- 짧은·긴 병원명, 진료과 2–4개, 지역 1–8개
- logo/color/photo의 부분 누락

각 fixture를 390/768/1440에서 home·contents·detail·doctor·treatments·visit로 렌더하고 H1, overflow, 이미지 실패, CTA, CJK wrap을 검사한다.

## 슬롭 방지 계약

- 병원별 raw CSS·slug 조건문·임의 palette theme를 추가하지 않는다.
- 색상은 공식 대표색에서 파생된 한 ramp만 사용하고 대비 실패 조합은 저장하지 않는다.
- 그라디언트, glass, noise, 과도한 shadow/card/pill을 차별화 수단으로 사용하지 않는다.
- 실제 의료진처럼 보이게 생성한 인물을 hero/doctor 신뢰 자산으로 사용하지 않는다.
- 모든 병원에 동일한 홍보 문장을 채우지 않는다. 승인된 사실이 없으면 운영정보를 먼저 보여준다.
- 비인터랙티브 gallery item의 hover zoom처럼 의미 없는 모션을 제거한다.
- 병원별 차별화는 공식 로고, 안전한 색상 ramp, 실제 사진, 정보 우선순위에서 만든다.

## 권장 실행 순서

1. **P0:** `DESIGN.md` 교정 + semantic theme 연결 + 6 fixture 상태 계약.
2. **P1:** Hero/Doctor/Gallery/Article renderer/Shared action bar를 공통 primitive로 교정.
3. **P2:** 온보딩 브랜드·자산 승인 필드와 Admin live preview 추가.
4. 72개 동일 route/viewport를 다시 fresh capture하고 independent visual QA를 통과시킨다.

## 병원별 상세 산출물

- [장편한외과의원](jangpyeonhanoegwayiweon/report.md)
- [연세속시원내과의원](yeonsesogsiweonnaegwayiweon/report.md)
- [행복드림의원](haengbogdeurimyiweon/report.md)
- [서울W내과의원 위례점](seoulwnaegwayiweon-wiryejeom/report.md)
- [강심장내과의원](gangsimjangnaegwayiweon/report.md)
- [노원탑365의원](noweontab365yiweon/report.md)

독립 디자인 시스템 리뷰: [.omo evidence](../../.omo/evidence/visual-system-audit-2026-08-21-clone-fidelity.md)

## 증거 한계

이번 감사는 live DOM, fresh screenshots, route/viewport metrics, 이미지 로딩과 가로 reflow를 확인했다. 키보드 전수 탐색, screen reader 발화, 200% zoom, 실제 색상 대비 전수, 네트워크 실패·저속 조건은 별도 검증이 필요하다. 이는 WCAG 전체 준수 판정이 아니다.
