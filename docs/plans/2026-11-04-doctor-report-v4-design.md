# 원장용 월간 보고서 v4 — 설계안

작성: 2026-11-04 · 기준 커밋 `0f0c4cf8`(main) · 입력: 대표 지시문 「보고서 개편」(2026-11-04)과 같은 날 대표 결정 R1~R8

이 문서는 1단계(보고서 v4)의 설계다. 2단계(측정 개편)는 §10에 넘길 것만 적고 따로 계획한다. 코드는 아직 바꾸지 않았다.

## 0. 대표 결정 (2026-11-04)

| # | 결정 |
|---|---|
| R1 | 범위는 원장용 월간 PDF만. V0·내부 AE PDF·리드 진단은 그대로 |
| R2 | 2단계. 1단계 = 보고서 v4 + 플랫폼별 노출률 계산식 수정(11월). 2단계 = 경쟁 추출·병원명 질문·질문 30개(12/1 측정 전) |
| R3 | 용어: 언급 비율→**AI 노출률**, 언급된 질문→**노출 키워드**, 질문→**측정 키워드**, 글→**콘텐츠**, 출처로 쓰인 답변→**AI 인용**, 다듬기·넓히기·보강→**최적화·키워드 확장·콘텐츠 고도화**, 다시 확인→**재측정** (10/1 '언급' 결정을 대체) |
| R4 | 측정 방식이 바뀐 달의 지난달 수치는 본문에서 빼고 부록 A에만 (10/2 '본문 참고값' 결정을 대체) |
| R5 | 면책 문장은 부록 D에 한 번만. 1면에서 뺀다 |
| R6 | PR #280·#281의 첫 장 안내 두 문장은 v4에서 뺀다. 뜻은 부록 C 용어 정의가 받는다 |
| R7 | P4 경쟁 상위 5곳은 답변 원문에서 병원명·근거 문장을 **인용만 허용**해 뽑는 판정을 새로 둔다(2단계). 1단계는 등록 경쟁사 기준 축소판 |
| R8 | 질문 30개(고정 20 + 교체 10)와 월 SoV 한도 인상(약 13,000)은 12월 측정부터(2단계). P1 핵심 3줄·P5 시사점은 LLM 없이 규칙 문장 |

## 1. 구조 — v3 옆에 v4를 세운다

- **새 파일**: `templates/doctor_report_v4.html`·`doctor_report_v4.css`, `services/doctor_report_v4.py`(뷰 모델·문장 규칙), `services/doctor_pdf_v4.py`(레이아웃·필수 문구 검증). 검증 판 이름은 `doctor-pdf-v4`.
- **v3는 지우지 않는다.** 렌더러(`doctor_pdf_rendering.py`)는 뷰가 `DoctorReportV4`면 v4 경로, dict 뷰면 종전 v3/옛 경로다.
- **템플릿 갱신은 v4로 올린다(구현 중 결정, 2026-11-04).** v4 배포 전에 만든 보고서(2026-10월분 포함)를 `TEMPLATE_REFRESH`하면 얼려 둔 v3 뷰를 다시 찍지 않고, 저장된 숫자(측정 요약·귀속·인용·발행 편수)로 v4 뷰를 새로 만든다(`tasks._upgrade_stored_report_to_v4`). 현재 행에서는 v3 갱신과 같은 것만 읽는다 — 공개 글 제목(저장된 마감 시각 기준)·답변 발췌(그 달 매니페스트)·타깃의 진료 항목·누적 편수·지난달 참고치·초기 측정 참고선. 저장 요약에서 다시 만든 사실(`stored_facts_v4`)과 새 뷰의 사실이 키 단위로 같지 않으면 `V4_UPGRADE_FACTS`로 거절하고 아무것도 쓰지 않는다. 새 버전의 스냅샷은 v4라 그다음 갱신·사후 확인은 v4 경로를 탄다. 새 버전은 다시 전달해야 한다(10월분은 아직 전달 전이라 v4로 전달하면 된다).
- **바뀌지 않는 것**: 측정·매니페스트·귀속·인용·발행 사실 조립(`tasks.py:12586` `_build_monthly_report_for_hospital`의 1~9단계), `MonthlyReport`·`MonthlyReportArtifact`·전달 이벤트, 전달 게이트(`monthly_report_delivery.py:338`), 안전망·대조·Admin 보고서 탭·운영센터 큐. 이들은 원장용 아티팩트가 유효한지만 본다.
- **문구 3겹 → 1겹.** v4는 `build_doctor_report_view`·`_director_copy`·`build_monthly_narrative`를 부르지 않는다. 입력(아래 §3)에서 `DoctorReportV4` 하나를 만들고, 템플릿은 그 필드만 읽으며 분기를 두지 않는다. 숫자는 전부 코드 바인딩이고 LLM은 없다(R8).

## 2. 입력 — 이미 조립되는 것만 쓴다

`_build_monthly_report_for_hospital`이 `build_doctor_report_view`를 부르는 자리(`tasks.py:12915`)에서 같은 인자로 v4 빌더를 부른다. 새 조회는 둘뿐이다.

| 입력 | 출처 | 비고 |
|---|---|---|
| 노출률·플랫폼·키워드별·비교·실패 수 | `MonthlySovPayload`(`monthly_sov_types.py:386`) — `sov_pct`, `attempts_used`, `mentioned_attempts`, `platforms[]`, `queries[]`, `cells[]`, `comparison`, `planned/success/failed_count` | 플랫폼 `mention_rate`는 §7에서 풀드로 바꾼다 |
| 키워드 행·신규/이탈 노출 | `ContentAttributionPayload.question_rows`·`new/lost_mention_cells` | 그대로 |
| AI 인용 | `CitationSummaryPayload`(`measured_cell_count`, `cited_cell_count`, `cited_items[]`) | 그대로 |
| 발행 콘텐츠 | `actual_published_contents`(+ `plan_quota`·`fulfilled_count`·`cumulative_published_count`) | **새 조인 1**: `ContentItem.query_target_id` → `AIQueryTarget.treatment`/`condition_or_symptom`(진료 항목), `content_brief.target_keyword`(타깃 키워드) |
| 측정 키워드 ↔ 진료 항목 | **새 조인 2**: 매니페스트 셀 `query_target_id` → `AIQueryTarget.treatment`/`condition_or_symptom`/`target_intent` | 비어 있으면 '확인 필요'(지시문 §0) |
| 경쟁 | `SovRecord.competitor_mentions`(등록 경쟁사만, `report_engine.py:364`) | 1단계 축소판. 2단계에서 원문 추출로 교체 |
| 대표 사례 | `_pick_evidence`와 같은 선택 + `matched_text`/`mention_context`(`sov_engine.py:494`) | 강조 표시에 `matched_text`를 쓴다 |
| 지난 달 참고 수치 | `_prior_reported_sov_pct` | 본문에 안 쓰고 부록 A에만(R4) |
| V0 기준값 | `v0_baseline` | 부록 A 한 줄 |
| 다음 달 계획 | `build_strategy_summary`의 targets/gaps/actions + 미노출 키워드 행 | P6 표 |

## 3. 뷰 모델 `DoctorReportV4`

frozen dataclass 하나. 스냅샷에 그대로 얼리고(`freeze_doctor_view`), 복원(`restore_doctor_view`)도 이 한 형을 만든다. 템플릿은 이 필드 밖을 읽지 않는다.

```
DoctorReportV4
  template_version: "v4"
  report_kind: "MONTHLY" | "SETUP"
  hospital_name, period_label("2026년 10월"), period_ym("2026-10"), public_url
  summary:   SummaryPage          # P1
  content:   ContentPage          # P2
  exposure:  ExposurePage         # P3
  competition: CompetitionPage    # P4
  cases:     CasesPage            # P5
  plan:      PlanPage             # P6
  appendix:  Appendix             # A~D
  facts:     dict[str,str]        # 템플릿 갱신 숫자 대조용(§8)
```

값이 없는 칸은 `None`이고 템플릿이 "다음 달부터 측정"(지시문 4-2)으로 찍는다. 0과 None을 한 번도 섞지 않는다.

## 4. 지면별 데이터 매핑

본문 6쪽, 부록 2쪽. 쪽마다 숫자 또는 차트가 하나 이상 있다(지시문 §7). 차트는 **인라인 SVG**로 템플릿이 그린다(WeasyPrint 지원, 외부 자원 없음). 색은 DESIGN.md 그대로 — 노출 주황 `#ff3d00`, 미노출 회색 `#a3a3a3`, 측정 실패는 빗금 패턴.

### P1 월간 요약 (`SummaryPage`)
- 제목 `"{period_label} AI 노출 성과 보고서"`, 부제 `"{hospital_name} · {period_label}"`. 인사말·질문형 제목 없음.
- **카드 4개** (큰 숫자 + 한 줄 정의):
  1. 발행 콘텐츠 — `"{published}편"`, 정의 `"계획 {plan_quota}편 중 {fulfilled}편 발행 · 누적 {cumulative}편"`. `plan_quota` 없으면 정의는 누적만.
  2. AI 노출률 — `"{sov_pct:.0f}%"`, 정의 `"AI 답변 {attempts_used}건 중 {mentioned_attempts}건에 {hospital_name} 노출"`. 풀드 k/n(`_pooled_rate`) 그대로. 없으면 `"측정 미완료"`.
  3. 노출 키워드 — `"{measured}개 중 {mentioned}개"`, 정의 `"측정 키워드 {measured}개 중 {mentioned}개에서 병원명 노출"`.
  4. 노출 진료 항목 — 노출된 키워드의 진료 항목 이름을 중복 없이 최대 4개 나열(`"심장초음파, 위내시경"`), 정의 `"병원명이 노출된 진료·검사 항목"`. 항목이 비면 `"확인 필요"`.
- **핵심 3줄** (규칙 문장, §5.1).
- 측정 방식이 바뀐 달에만 하단 한 줄: `"측정 방식이 바뀌어 지난달과 직접 비교하지 않았습니다. 지난달 수치는 부록 A에 있습니다."` (R4). 비교한 달은 아무 줄도 없다.
- SETUP 달: 카드 2~4는 `"다음 달부터 측정"`, 3줄은 기준 설정 문장 세트.

### P2 콘텐츠 발행 현황 (`ContentPage`)
- 소제목 `"진료 항목별 발행 편수"` + 가로 막대(SVG). 진료 항목은 조인 2로, 없으면 `"기타"`.
- 표: `발행일 | 콘텐츠 제목 | 진료 항목 | 타깃 키워드 | 링크`. **발행 채널 열은 뺀다**(값이 병원 안내 페이지 하나뿐). 발행일은 `first_published_at`(KST). 타깃 키워드는 `content_brief.target_keyword`, 없으면 빈칸이 아니라 열을 통째로 비우지 않도록 `"—"` 대신 `"일반"`.
- 인용 여부는 행마다 적지 않는다(P3에서 한 번).

### P3 AI 노출 성과 (`ExposurePage`)
- (2개월 이상 같은 고정 세트로 잰 뒤) 상단 **월별 추이 꺾은선** — 1단계에서는 자리와 로더만 만들고, 같은 매니페스트 지문(`tracking_set_fingerprint`)으로 비교 가능한 직전 달들의 `sov_summary.sov_pct`를 잇는다. 비교 가능한 달이 2개 미만이면 섹션을 숨긴다.
- **진료 항목별 노출률** 막대 — 키워드 행을 진료 항목으로 묶어 풀드 k/n.
- **플랫폼별 노출률** — `platforms[]`의 `mention_rate`(§7에서 풀드로) + `"답변 {attempts}건 중 {mentioned}건"`.
- **질문 그룹별 노출률 표** — 1단계는 기존 `target_intent`(`query_target_structure.py:42`)를 그룹으로 매핑: 추천 탐색·진료 가능 확인→B, 증상 탐색→C, 비용 확인·비교 검토→D, 정보 탐색→(측정 세트 밖이라 없음). A(병원명)는 2단계 전까지 `"다음 달부터 측정"`.
- **키워드 × 플랫폼 히트맵** — 셀 `state`(SUCCESS/FAILED/EXCLUDED)와 `mentioned_attempts>0`로 노출/미노출/실패. 행은 키워드, 열은 플랫폼.
- **AI 인용** 1~2줄 — `cited_known`일 때만 `"출처 확인이 가능한 AI 답변 {measured_cell_count}건 중 {hospital_name} 콘텐츠 인용 {cited_cell_count}건"`, 아니면 `"이번 달은 출처를 확인하지 못했습니다"`(0으로 쓰지 않는다).

### P4 경쟁 현황 (`CompetitionPage`) — 1단계 축소판
- 표: `병원 | 언급 답변 수 | 언급률`. `_competitor_outcomes` 상위 5. 머리글에 `"등록한 경쟁 병원 {n}곳 기준"`을 밝힌다.
- `"{hospital_name}이 빠진 키워드에서 대신 언급된 병원"` — 미노출 키워드 행마다 그 셀의 `competitor_mentions` 중 `is_mentioned`인 이름.
- 언급 근거(심평원 등급·장비·후기)는 2단계. 1단계는 그 칸을 두지 않는다(빈 열 금지).
- 등록 경쟁사가 없으면 쪽 전체를 `"경쟁 병원은 다음 달부터 측정"` 한 줄 + 미노출 키워드 수 하나로 채운다(텍스트만 있는 쪽을 피하되 숫자를 지어내지 않는다).

### P5 대표 답변 사례 (`CasesPage`)
- 노출 1건, 미노출 1건. 선택 규칙은 `_pick_evidence`와 같다.
- 발췌는 전문이 아니라 `_excerpt_around` 2~3문장(≤260자). 노출 사례는 `matched_text`가 있으면 그 구간만 `<mark>`로 강조하고, 없으면 병원명 일치 구간.
- **시사점 1줄**은 규칙 문장(§5.2). 사례마다 `"{플랫폼} · {날짜} 확인"`.
- 답변 전문은 부록에도 싣지 않는다(지시문 §5 중복 금지).

### P6 다음 달 실행 계획 (`PlanPage`)
- 표: `우선순위 | 진료 항목 | 현재 노출률 | 실행 내용 | 목표 키워드`.
- 행 = **미노출 키워드 전부** + 이탈(`lost_mention_cells`) 키워드. 본문 밖 '그 밖에 살펴볼 질문' 목록은 없앤다.
- 우선순위 점수(결정적): ① 병원 `hero_specialties`·`treatments`에 든 진료 항목 가점, ② 등록 경쟁사가 그 키워드에서 언급됐으면 가점(경쟁 강도), ③ 현재 노출률 오름차순. 같은 점수는 키워드 문자열 순.
- 실행 내용은 규칙 문장(§5.3) — `"'{지역} {진료 항목}' 키워드 대응 콘텐츠 1편 발행 예정"` 꼴.

### 부록 (`Appendix`)
- **A. 측정 키워드 전체와 결과** — 표 `측정 키워드 | 그룹 | 진료 항목 | ChatGPT | Gemini | 합계`. 각 칸 `"{mentioned}/{attempts}"`. 지난달 수치는 비교한 달이면 `지난달` 열, 방식이 바뀐 달이면 머리말 한 줄 `"지난달 노출률 {ref}% (측정 방식이 달라 참고만)"`(R4). V0 기준값도 여기 한 줄.
- **B. 측정 방법** — 고정 문장 4줄: 측정 키워드 수·플랫폼·반복 수·창(`month_start_dawn`), `"계획 {planned}회 / 확인 {success}회 / 실패 {failed}회"`, `"월별 비교는 고정 키워드로만 계산합니다"`, `"AI 답변은 질문할 때마다 달라질 수 있습니다"`.
- **C. 용어 정의** — AI 노출률·노출 키워드·AI 인용·측정 키워드 네 줄. R6의 두 문장 뜻이 여기 들어간다.
- **D. 유의사항** — 3줄 고정: `"AI 답변에 노출된 횟수는 환자 수가 아닙니다."` / `"순위나 환자 수 증가를 약속하지 않습니다."` / `"다음 달 계획은 예정 사항입니다."` 본문에는 어디에도 없다(R5).

내부 정보(모델명·게이트웨이·시스템 상태값)는 어느 쪽에도 없다 — `INTERNAL_ONLY_MARKERS` 검사를 v4에도 건다.

## 5. 문장 규칙 (LLM 없음)

모든 문자열은 `doctor_report_v4.py` 한 파일에 둔다. 서술은 `~습니다`, 제목·표는 명사형, 한 문장 40자 이내, 지시문 6-2 금지 표현 없음.

### 5.1 P1 핵심 3줄
각 줄은 조건 → 고정 틀. 위에서부터 첫 참인 것.
- **잘된 점**: 신규 노출 키워드 ≥1 → `"'{키워드}' 등 {n}개 키워드에서 새로 노출됐습니다."` / 노출률 유의 상승 → `"AI 노출률이 {prev}%에서 {cur}%로 올랐습니다."` / 인용 ≥1 → `"{hospital_name} 콘텐츠가 AI 답변 {n}건에 인용됐습니다."` / 그 밖 → `"측정 키워드 {m}개 중 {k}개에서 노출됐습니다."`
- **보완할 점**: 이탈 키워드 ≥1 → `"'{키워드}' 등 {n}개 키워드에서 노출이 끊겼습니다."` / 미노출 ≥1 → `"'{키워드}' 등 {n}개 키워드는 아직 노출되지 않았습니다."` / 그 밖 → `"측정 키워드 전체에서 노출됐습니다."`
- **다음 달 중점**: P6 1순위 행에서 `"'{진료 항목}' 키워드 대응 콘텐츠 {n}편을 우선 발행합니다."`

### 5.2 P5 시사점
노출 사례: 그 셀의 인용 콘텐츠가 있으면 `"'{콘텐츠 제목}'이 출처로 쓰였습니다 → 같은 형식으로 다른 항목도 발행"`; 없으면 `"'{진료 항목}' 키워드에서 노출됐습니다 → 인접 항목으로 키워드 확장"`.
미노출 사례: 대신 언급된 경쟁사가 있으면 `"{경쟁사}이 대신 언급됐습니다 → '{진료 항목}' 콘텐츠 고도화"`; 없으면 `"어느 병원도 특정되지 않았습니다 → '{진료 항목}' 콘텐츠 1편 발행"`. 근거 문장(장비·등급)은 2단계에서 인용으로 붙인다.

### 5.3 P6 실행 내용
미노출: `"'{지역} {진료 항목}' 키워드 대응 콘텐츠 1편 발행 예정"`. 이탈: `"'{진료 항목}' 기존 콘텐츠 고도화 후 재측정"`. 지역은 타깃 `region_terms[0]`, 없으면 병원 `region[0]`.

### 5.4 금지어·필터
- 테스트 금지어(`BANNED_IN_DOCTOR_COPY`)에서 `"언급률"`을 빼고, 지시문 6-2·6-3의 옛 표현(`우리 병원`, `언급 비율`, `올린 글`, `출처로 쓰인 답변`, `다듬겠습니다`, `약속드리지`(부록 D 제외), `원장님,`)을 **v4 전용 금지어**로 넣는다.
- 모든 새 문장은 `check_forbidden`을 통과해야 한다(`test_report_plain_language`와 같은 방식). 지시문 예문(`잘하는 치과`, `노출률`, `최적화`, `콘텐츠 고도화`)은 2026-11-04에 필터 통과를 확인했다.

## 6. 검증 계약 v4 (`doctor_pdf_v4.py`)

- **레이아웃**: 쪽 수 7~10(본문 6 + 부록 1~2; 20편·30키워드 달이 막히지 않게 상한 10), 앵커 `p1-start/end … p6-start/end`, `appendix-start/end`, 부록 A 행마다 `kw-row-{i}`. 여백은 60pt = **80 CSS px**(WeasyPrint 좌표 단위)이고 앵커의 끝점(x+w, y+h)까지 본다. 밀도 두 단계(`comfortable`/`compact`) 재시도는 v3와 같다.
- **필수 문구는 뷰 모델에서 뽑는다**: `DoctorReportV4.required_texts()`가 쪽별 튜플을 돌려주고, 렌더 검증(`v4_expectation`)과 템플릿 갱신 숫자 대조(§8)가 **같은 함수**를 쓴다. v3처럼 검증 파일에 문장을 손으로 복사하지 않는다. v4는 본문 튜플을 **그 쪽의 추출 텍스트**에서, 부록 튜플은 7쪽 이후에서만 찾는다(v3의 전체 문서 검색과 다르다).
- **비율 표기**: 정수 %. 단 100은 `100.0%`로 찍는다 — 의료광고 필터가 `100%` 토큰을 어디서나 막고, 보고서 예외를 필터에 두지 않는다(`doctor_report_v4._pct`, 템플릿의 `pct` 필터가 같은 규칙).
- **헤드라인과 AI 서비스별 분모**: 비교한 달의 첫 장 노출률은 지난달과 같은 키워드(매칭 코호트)의 k/n이고 카드 정의의 "AI 답변 N건 중 M건"도 그 코호트다. AI 서비스별 막대는 이번 달 전체 키워드 기준이라, 두 분모가 다를 때(`current_unmatched_cell_count > 0`) P3에 `platform_note` 한 줄로 밝힌다.
- **추이의 각 달 값**은 그다음 달 보고서가 '지난달'로 비교한 값(`comparison.prior_sov_pct`)이다 — 첫 장의 지난달 수치와 추이의 지난달 점이 같다(`tasks._comparable_trend_points`).
- 카드·표의 숫자는 `facts`(§8)에 `p1.sov`, `p1.keywords`, `p3.platform.chatgpt`, `appendix.kw.{i}.chatgpt` 같은 키로 함께 나온다.
- `DoctorArtifactMetadata.validation_version` Literal에 `"doctor-pdf-v4"`를 더한다(`doctor_pdf_contracts.py:164`). `report_artifact_validation.validate_persisted_report_artifact`는 판 이름으로 v3/v4 검사를 고른다.
- 공개 링크(`public_url`) 요구, A4 검사, `INTERNAL_ONLY_MARKERS`는 그대로.

## 7. 측정 쪽 수정 — 플랫폼별 노출률을 풀드로

`_platform_summary`(`monthly_sov.py:174`)의 `mention_rate`는 셀 동일 가중 평균이고 헤드라인은 풀드 k/n이라 지시문 4-2가 짚은 불일치가 생긴다. `mention_rate`를 `mentioned_attempts / attempts_used`로 바꾸고 옛 값은 `cell_mean_rate`로 남겨 운영 조회가 깨지지 않게 한다. 비교·유의성 판정은 헤드라인만 쓰므로 영향 없다. 영향: `test_monthly_sov*`, Admin 측정 화면이 `mention_rate`를 "질문별 평균"이라 표기한 곳이 있으면 문구만 고친다. 저장된 옛 `sov_summary`는 다시 계산하지 않는다(보고서 재생성 때만 새 값).

## 8. 스냅샷·템플릿 갱신·호환

- 스냅샷 `ReportRenderInputs.doctor_view`에 v4 뷰를 얼린다(`template_version:"v4"` 포함). `restore_doctor_view`는 `template_version`을 보고 v4 dataclass로 복원하고, 없으면 기존 v3 복원 그대로.
- 템플릿 갱신의 숫자 대조(`monthly_template_refresh.doctor_view_facts`·`_pdf_tokens`)는 v4 뷰면 `facts`를 그대로 쓰고 PDF 토큰은 `required_texts()`의 숫자 토큰만 찾는다. v3 보고서는 종전 경로.
- `doctor_metric_facts`(`stored_doctor_facts`)의 키는 유지하고 v4 키를 **추가**한다 — 옛 보고서 postcheck가 깨지지 않게.
- 안전망·대조가 v4 아티팩트를 다시 만들 때도 같은 빌더·검증을 탄다(변경 없음).

## 9. 테스트

- 새로: `test_doctor_report_v4.py`(뷰 모델 — 숫자 바인딩·None/0 구분·3줄 규칙·우선순위 정렬·용어 금지어·필터), `test_doctor_pdf_v4.py`(렌더 — 쪽 수·앵커·필수 문구·히트맵 색/빗금 클래스·긴 병원명·키워드 30개·SETUP·측정 미완료), `test_doctor_report_v4_snapshot.py`(얼리기/복원 왕복, v3 스냅샷 복원 불변).
- 고치기: `test_monthly_sov*`(§7), `test_monthly_template_refresh(_postgres)`(v4 facts), `test_report_artifact_validation`(판 이름), `test_w8_report_guarantee*`(v4 빌더 경유).
- 그대로: `test_report_plain_language`·`test_report_marketing_feedback`·`test_doctor_report_view`·`test_report_redesign` 등 v3 테스트는 v3 경로가 남으므로 유지한다. 단 `BANNED_IN_DOCTOR_COPY`의 `"언급률"` 제거만 반영.
- 샘플 PDF: 가상 병원 3종(비교 달·방식 바뀐 달·SETUP)을 `scripts/`로 찍어 대표 확인을 받는다(기존 `sample_label` 러닝헤드 사용).

## 10. 구현 순서와 배포

1. §7 플랫폼 풀드 수정 (작은 PR, 먼저)
2. 뷰 모델·문장 규칙 + 단위 테스트
3. 템플릿·CSS·SVG 차트 + 렌더 검증
4. 스냅샷·갱신·판 이름·렌더러 분기 연결, `_build`에서 v4 빌더 호출
5. 샘플 PDF 3종 대표 확인 → 문구 조정
6. 배포: 배치 시간대 밖, 11월 안. **12/1 07:15 첫 정기 생성이 v4로 나간다.** 배포 뒤 `ops_sql_report --only=q15`로 게이트 입력 확인, 안전망 틱이 v4 판을 유효하다고 보는지 확인.
7. 롤백: 트래픽 롤백만으로 충분 — v4로 만든 보고서가 있으면 옛 코드는 `template_version`을 모르므로 그 보고서의 템플릿 갱신만 막힌다(재생성은 v3로 새 버전). 11월분 생성 전 롤백이면 영향 없음.
8. **10월분 재전달**: 배포 뒤 공개 병원마다 `python -m app.utils.monthly_template_refresh precheck/execute/postcheck`(2026-10)로 v4 버전을 만든다(§1의 v4 올리기). 숫자는 11월 1일 마감본 그대로이고 새 버전은 전달 기록을 새로 남겨야 한다. 거절(`V4_UPGRADE_FACTS`)이 나오면 그 병원은 v3 버전을 그대로 전달한다.

## 11. 2단계로 넘기는 것

| 항목 | 왜 2단계인가 |
|---|---|
| 경쟁 상위 5곳 + 언급 근거(R7) | 등록 경쟁사만 판정한다(`sov_engine.py:1346`). 원문에서 병원명·근거 문장을 인용만 허용해 뽑는 판정과 `SovRecord` 저장 칸이 새로 필요 |
| A그룹 병원명 질문 + 정보 정확도 | 추적 세트가 LOCAL만 받는다(`sov_tracking_set.py:57`). 새 의도·새 판정 스키마 |
| 질문 30개(고정 20 + 교체 10) | `TRACKING_SET_N_MAX=15`·설정 검증(`config.py:403`)·교체 세트 개념 없음. 월 한도 9,000이면 10곳 → 약 13,000으로 인상. 세트를 바꾸는 달은 비교 불가 |
| 질문 설계 규칙 3-1·3-3 | `question_wellformed`·`query_target_structure`·시드에 반영 |
| 추이 꺾은선의 '고정 질문만' 기준 | 1단계는 매니페스트 지문이 같은 달만 잇는다. 고정/교체가 생기면 고정 지문으로 바꾼다 |

## 12. 확정된 세부 결정 (2026-11-04 대표)

1. P1 카드 4 '노출 진료 항목'은 큰 숫자 `"{n}개 항목"` + 아래 작게 이름 나열(최대 4개, 넘치면 `외 {k}개`).
2. P6 표가 한 쪽을 넘기면 상위 행만 본문에 두고 나머지는 부록 A 뒤에 `"다음 달 실행 계획 (이어짐)"`으로 잇는다. 같은 행을 두 번 싣지 않는다.
3. 비교 가능한 달은 P1 카드 2 정의에 `" · 지난달 {prev}%"`를 덧붙이고, P3 추이에도 보인다.
4. `matched_text`가 없는 옛 기록은 병원명 문자열이 든 구간을 강조하고, 그것도 없으면 강조 없이 발췌만 싣는다.
