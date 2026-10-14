# Full-Automatic 파이프라인 설계 (2026-10-14)

> 이 문서는 **설계**다 — 코드 변경·배포·운영 데이터 변경의 증거가 아니다. 구현은 §5의 워크스트림 단위로 fresh 에이전트에 넘기고,
> 각 증분은 따로 배포·되돌릴 수 있게 한다. 배포는 [운영 배포 절차](../ops/deployment-runbook.md)의 별도 검증으로만 기록한다.

**대표 목표(원문):** "자율적인 워크플로우로 글이 정해진 일정에 맞춰서 게시되고, 이를 기반으로 월말에 보고서가 안정적으로 산출되는
Full-Automatic (Critical 이슈가 있을 때 제외)".

**기준 소스:** `claude/autonomy-p4` worktree(origin/main `b06b9c42` + Phase 4 진행 커밋). `file:line`은 2026-10-14 오전 이 worktree 기준이며,
P4 커밋이 계속 쌓이므로 **함수·상수 이름(앵커)으로 찾는다**. 배포 상태: 0090(fallback narrative SQL)은 배포됨. 비용 상한은 운영 서비스에서
이미 올렸다(content 500/일·4500/월, image 500/6000, essence 120/1200 — **상한 크기는 결정된 사항**, 이 문서는 예약 용량·우선순위만 다룬다).
빈 슬롯의 `LEGACY_SPEND_UNKNOWN` 핫픽스는 PR #252로 진행 중이다.

**입력 자료:** 10/14 08:33 KST 차단 감사(`scratchpad/blockers/` — `items_table.txt`·`incidents_summary.txt`·`reports_summary.txt`·`diag1~3.out`),
[MasterPlan](2026-10-11-autonomy-masterplan.md)(D1~D29), [Phase 3 콘텐츠 게이트](2026-10-13-p3-content-gates.md)(D23·D24·E1~E5),
[Phase 4 온보딩·Essence 자율화](2026-10-14-p4-onboarding-essence-autonomy.md)(D30~D34·E1~E8).

---

## 0. 목표·Critical 정의·불변식

### 0.1 Critical (사람을 부르는 유일한 콘텐츠 사유)

"Critical"은 **자동 수정 계단을 다 거친 뒤에도 남는** 아래 네 가지뿐이다. 감지 자체는 Critical이 아니다 — 감지되면 먼저 결정적 수정(문장 삭제·템플릿 삽입)과
재작성·주제 교체를 거치고, 그래도 남을 때만 "Critical-blocked"다.

| 코드 | 정의 | 현재 감지 지점 | 자동 수정 (먼저) |
|---|---|---|---|
| **C1 불법 의료광고** | 치료 결과 보장, 최저가·가격 최상급, 노골적 할인·이벤트·무료 유인(D24) | `utils/medical_filter.py`(`check_forbidden`, 라벨 `할인·유인`) — 생성·발행·공개·PATCH 공통 | 본문 문장 통째 삭제(P3 3-8, ≤3문장), 제목·메타·FAQ면 재작성 |
| **C2 의료 안전 HARD** | 응급 안내 누락, 위험한 의학 오정보(MEDICAL_SAFETY HARD) | `services/content_ai_review.py:189`(`ContentAiFindingKind.MEDICAL_SAFETY`) | 응급 템플릿 삽입·최소 교정(`content_minimal_correction.run_minimal_correction`), 삭제형 재작성, 주제 교체 |
| **C3 프롬프트 주입** | 입력·출력에 지시문 잔재 | 입력: `essence_auto_review._has_prompt_injection`(P4 D33은 입력에서 제외), 출력: 신규 결정적 검사(§1.3) | 입력은 제외, 출력 후보는 폐기·재작성 |
| **C4 tenant 혼입·공개 권한** | 다른 병원의 이름·전화·도메인이 본문에 있음, PAUSED·비공개·권한 회수·`authority_change` | tenant: 신규 결정적 검사(§1.3), 권한: `content_publication.assess_content_publication`·`content_visibility.assess_public_visibility` | tenant 혼입은 후보 폐기·재작성. 권한 상태는 사람이 만든 상태라 **존중만** 한다(알림 없음) |

그 밖의 모든 것 — HOSPITAL_FACT 지적(근거 없는 병원 주장), 수치 불일치, UNCERTAIN, 참고자료 없음·주제 불일치, 문체·분량, 검수 장애, 공급자 장애,
예산 상태 — 은 **비-Critical**이다: 진행하거나 스스로 낫는다. 사람의 일이 아니다.

### 0.2 불변식

| # | 불변식 | 측정 (§1.6 SLO) |
|---|---|---|
| **I1** | 일정에 있는 모든 슬롯은 **예정일에** 발행된다. Critical-blocked 슬롯은 **같은 날** 대체 글(주제 교체)이 발행되어 그 달 편수가 유지된다 | 일별: `due_today_published / due_today` = 100%(Critical 대체 포함). 월별: 계약 월 `first_published_at` 편수 ≥ 약속 편수 |
| **I2** | 서비스 중인 모든 병원은 **매월 8일**까지 전달 가능한 월간 보고서를 갖는다(COMPLETE/LIMITED/UNAVAILABLE — "보고서 없음"은 없다) | 8일 09:00 KST: 서비스 구간에 든 병원 × 지난달 = 검증된 원장용 artifact 1개 |
| **I3** | 사람에게 묻는 것은 Critical과 대표 결정뿐이다 | Slack 중 `[Error]`/조치 필요 = Critical·대표 결정·개발 결함(자기 치유 소진)만. 조용한 사고는 OPEN/HIGH가 되지 않는다 |
| **I4** | 지출은 계약 물량에서 계산한 상한 안이고, **일정 경로(on-schedule)에 예약 용량**이 있다 | 일정 경로 호출이 `COST_BLOCKED`로 막힌 슬롯 = 0 |

### 0.3 알림 등급 (Slack 하나, D29)

| 등급 | 언제 | 라벨(`notification_labels.py`) |
|---|---|---|
| **A-CRIT** | Critical-blocked 슬롯(대체 결과와 함께), 공개 글의 Critical(§6 O2) | `[Error : 오류 발생]` + `[Critical]` 표지 |
| **A-OWNER** | 대표 결정이 필요한 상태(일정 경로 예산 소진, 대체 불가로 월 편수 미달 예상, 공급자 3일 장애) | `[Error : 오류 발생]` + `[대표 결정]` |
| **A-DEV** | 자기 치유를 다 쓴 시스템 결함(원인별·하루 1건) — §6 O7로 유지 여부 결정 | 기존 `[개발 확인]` |
| **Report** | 일일 SLO 요약(기존 fleet heartbeat 확장), 월간 전달 준비 | `[Report : 운영 현황 보고]` |

그 밖(비-Critical 보류, 교정 실패, 검수 장애, 사후검수 지적, 비용 소프트 경고)은 Slack을 만들지 않는다 — 사고 행과 일일 요약 숫자로만 남는다.

---

## 1. 단계 모델

```
Input(P4) ─▶ Generation(D-3..D-1) ─▶ Gate(분류) ─▶ Publish(D 08:00~23:00) ─▶ Measurement(24~말일) ─▶ Report(1~8일)
                 ▲            │            │                │                                     │
                 └── 자기 치유 계단(§3) ◀── 정체 감지기(§3.5) ◀── 일일 SLO 점검(§1.6) ◀──────────────┘
```

### 1.1 Input — 온보딩·Essence (Phase 4가 소유)

P4가 끝나면 Essence는 승인 단계 없는 자동 요약이라(D33) `MISSING_APPROVED_ESSENCE`가 생성을 멈추지 않는다. 이 설계가 P4에 더 요구하는 것은 둘이다.

1. **0편 병원 3곳**(세종다온·장보고·뉴플랜)은 P4 배포 B의 `essence_auto_backfill --apply` 직후 첫 일정(D3 안분)이 생겨야 한다 — 배포 B 완료 기준에 이미 있다.
2. **온보딩 병원의 비용**: 10/13 콘텐츠 일일 호출의 32%를 0편 병원 두 곳이 썼다(Essence 재합성·근거 재추출). §2.3의 병원별 비-일정 상한이 이것을 묶는다.

`ONBOARDING_PROFILE_INCOMPLETE`(세종다온)는 P4 D30/D31 자동 입력 뒤에도 남는 칸(원장 약력 등)이면 계약 인수 정보라 사람의 일로 남는다 — 대표 결정의
범주(계약·공식 정보)이며 정체 사고 1건 + 1회 Slack을 유지한다.

### 1.2 Generation

**리드 타임 D-3..D-1.** `NIGHTLY_GENERATION_LOOKAHEAD_DAYS`(`core/config.py:547`, 지금 2)를 **3**으로 올린다. 23:00 창이 D-1·D-2·D-3을 담고
복구 스윕(01·04·07·12·18·22시, `generation_retry_policy.py:13-15`)이 같은 앞쪽 지평(`tasks._generation_lookahead_end`, `tasks.py:5483`)을 쓴다.
슬롯마다 첫 시도부터 발행까지 밤 3회 + 낮 스윕 6회 이상이 생긴다. 참고자료 검증 신선도 7일(P3 E4, `reference_verification.py`)과 검수 hash 결합은
D-3 생성을 그대로 덮는다. 전환 첫 밤만 하루치 슬롯이 더 들어오며(일회성), §2의 일정 경로 예약 용량 안이다.

**처리 순서.** 로더 정렬(`nightly_generation_batch._generation_order_columns`, `:518`)을 "예정일 오름차순 → 이월분(`carried_over_from`) → 병원 라운드로빈"으로
고정하고, 예정일 ≤ 내일인 슬롯은 같은 페이지의 앞자리를 받는다. 오래된 실패(교정·교체 계단을 이미 탄 슬롯)가 새 슬롯의 예산을 먼저 먹지 않게,
같은 스윕에서 한 슬롯이 쓰는 세션은 1회다(기존 일일 예산 규칙 그대로).

**결정적 치유 먼저.** P3 E2 사슬(병원명·필수 문구 덧붙이기, 금지 표현 문장 삭제, 검사+'치료' 제목) — 이미 있음(`content_engine._apply_deterministic_heals`).

**참고자료는 생성을 막지 않는다(새 계약, §6 O3).** 지금은 참고자료 필수 글이 비면 `MissingCitableReferencesError`(`content_engine.py:188`, raise `:2478`)로
거절하고, 수기 목록 치유(`_heal_from_curated_catalog`, `:1780`)가 실패하면 같은 프롬프트 계열로 재작성을 반복해 본문 전송 예산(36, `generation_attempt_state.py:48`)을
태운 뒤 `GENERATION_BUDGET_EXHAUSTED`로 잘못 분류된다(감사: 3건). 바꾼다:
- 수기 목록 치유가 실패하면 **거절하지 않고** `references=[]`로 결과를 돌려주고 `essence_check_summary.references_omitted = {reason, at, verified_drop_notes}`를 남긴다.
- 재작성 회차에서 참고자료 공백은 지적으로 넘기지 않는다(모델이 URL을 지어내게 만드는 유일한 압력이다).
- 발행 게이트는 §1.3 표대로 처리한다(O3 기본값: 비어도 발행).

**예산 소진 원인 분류.** `generation_run_control.classify_generation_failure`(`:177-200`)가 `GenerationBudgetExceeded`를 일률적으로
`GENERATION_BUDGET_EXHAUSTED`로 만든다. 시도 기록에 `exhausted_by = generation_rejection_kind(last_error)`(P3 3-13 라벨, `generation_retry_policy.py:118`)를 함께 남기고,
`exhausted_by ∈ {references}`면 교체 계단이 아니라 위 "참고자료 없이 저장" 경로로 한 번 더 산다(남은 일일 세션 안).

**같은 날 재시도.** 감사의 `abe0b28e`·`8e08fe0e`(TOPIC_SWAPPED, next 10/15 01:00 KST)는 교체가 "오늘 예산을 다 쓴 것"으로 기록되기 때문이다 —
`topic_swap_fallback._record_topic_swapped_attempt`(`:549-597`)는 **예정일 당일**에만 1회를 남기고, 이미 지난 슬롯(예정일 < 오늘)이나 내일 슬롯은 내일로 민다.
`same_day` 조건을 **`scheduled_date <= today + 1`(지난 슬롯 포함)**로 넓힌다. 교체된 주제는 자기 작가 예산 6회가 새로 있으므로(`prior_topic_spend`) 지출 위험은 늘지 않는다.

**긴급 세션.** `generation_retry_policy._sample_budget_is_spent`(`:324`)·`_earliest_eligible_date`(`:434`)에 "예정일 ≤ 내일 또는 지남"이면 하루 본문 세션
예산 +1(`SAMPLE_BODY_URGENT_EXTRA = 1`)을 둔다. 주제당 평생 상한(작가 6·검수 6·전송 36)은 그대로라 무한 루프가 없다.

### 1.3 Gate — 차단 사유 분류표

발행 게이트(`content_publication.assess_content_publication`, `:301-437`)와 생성 거절(`GENERATION_REJECTION_KINDS`, `generation_retry_policy.py:105-117`)의
**모든** 사유를 세 등급으로 나눈다. 분류는 새 순수 모듈 `services/finding_criticality.py` 한 곳이 정하고 생성·발행·사후검수·Slack이 같은 판정을 쓴다.

| 사유 / 지적 | 등급 | 자동 처리 | 끝 상태 |
|---|---|---|---|
| C1 금지 표현 — 본문 문장 | Critical(치유 전) | 문장 삭제(P3 3-8) → 남으면 재작성 → 교체 | 치유되면 진행. 교체까지 남으면 Critical-blocked |
| C1 — 제목·메타·FAQ·참고자료 제목 | Critical(치유 전) | 재작성(제목 결정적 교정 불가) → 교체 | 동일 |
| `MEDICAL_SAFETY` HARD + 인용 | Critical(치유 전) | 응급 템플릿·최소 교정 → 삭제형 재작성 → 교체 | 동일 |
| `MEDICAL_SAFETY` HARD, 인용 없음 | Critical(치유 전) | 재작성 → 교체(0-6a 그대로) | 동일 |
| `MEDICAL_SAFETY` UNCERTAIN(+인용) | 비-Critical | 인용 문장 결정적 삭제 → 재검수 | 남으면 상위 모델 1회 → 재작성 → 교체. 사람·Slack 없음 |
| `MEDICAL_SAFETY` UNCERTAIN, 인용 없음 | 비-Critical | 상위 모델 재검수(기존) → 재작성 | 교체 → 대체 불가면 SHORTFALL(§3.1 R7) |
| **`HOSPITAL_FACT` HARD/UNCERTAIN + 인용** (감사: 진료 철학 문장) | 비-Critical | **인용 문장 결정적 삭제**(LLM 제안 호출 없음) → Sonnet 재검수 | 진행. **교정·교체 예산을 쓰지 않는다** |
| `HOSPITAL_FACT` + 수치 불일치(감사: 2주 vs 3주) | 비-Critical | 승인 사실(`content_minimal_correction.approved_fact_texts`, `:594`)에 같은 단위의 값이 하나면 그 값으로 치환, 아니면 문장 삭제 → 재검수 | 진행 |
| `HOSPITAL_FACT`, 인용 없음 | 비-Critical | 지적을 실어 재작성 1회 | 교체(비-Critical 교체 예산) |
| `REFERENCE`(SOFT) | 비-Critical | 해당 출처 제거(기존) | 진행 |
| `STYLE`(SOFT), 분량 SOFT, 중복 주제 | 비-Critical | 기록만 | 진행 |
| `MISSING_REFERENCES`(필수 유형, 비어 있음) | 비-Critical | 07:45·08:00 수기 목록 치유(기존) | **O3 기본값: 참고자료 없이 발행**, `references_omitted` 기록 |
| `FAQ_FIELDS_MISSING` | 비-Critical | 본문 수리 세션(기존) → 교체 | 진행 또는 교체 |
| `ESSENCE_NOT_ALIGNED`(avoid 문구만) | 비-Critical | P4 E7: 공개 글은 유지. 미발행은 해당 문장 삭제 → 재검수 | 진행 |
| `MISSING_APPROVED_ESSENCE` | 비-Critical | P4 D33으로 소멸, 남으면 즉석 합성 디스패치(P4-13) | 진행 |
| `CONTENT_AUTHORITY_CHANGED` / PAUSED / 비공개 / 권한 회수 | **존중**(C4 권한) | 사람이 만든 상태. 자동 해제 없음 | 보류 유지, 알림 없음. 월 편수 SLO에서 제외 표기 |
| C4 tenant 혼입(다른 병원명·전화·도메인) | Critical(치유 전) | 후보 폐기 → 재작성 → 교체 | 동일 + A-DEV(버그 신호) |
| C3 출력 주입 잔재 | Critical(치유 전) | 후보 폐기 → 재작성 | 동일 |
| `CONTENT_AI_REVIEW_STALE` | 비-Critical | 재검수(기존 스윕) | 진행 |
| `CONTENT_AI_REVIEW_UNAVAILABLE` | 비-Critical, **PASS 아님** | §1.4 대체 사슬 | 결합된 PASS 없이 발행하지 않음(§6 O4) |
| `COST_BLOCKED` | 비-Critical | 일정 경로 예약 용량(§2)으로 원천 차단 | 남으면 A-OWNER 1회/일 |
| `CONTENT_AI_REVIEW_CONFIG_ERROR` | 시스템 결함 | — | A-DEV |
| `CONTENT_NOT_GENERATED` | 비-Critical | 다음 스윕(기존 ENVIRONMENT 창) | 진행 |
| 생성 거절 `truncated`·`seo`·`geo`(엔티티)·`faq`·`exam_title`·`must_use` | 비-Critical | 결정적 치유 → 지적 실어 재작성 | 교체 |
| 생성 거절 `price`(미검증 고정가) | 비-Critical | 해당 문장 삭제 치유(C1과 같은 삭제기, 새 라벨) → 재작성 | 교체 |
| 생성 거절 `references` | 비-Critical | 위 "참고자료 없이 저장" | 진행 |
| `GENERATION_BUDGET_EXHAUSTED` | 원인(`exhausted_by`)의 등급을 따른다 | 원인별 경로 | — |
| `LEGACY_SPEND_UNKNOWN` | 시스템 결함 | PR #252 + §3.5 정체 감지기 R-S0 | 진행 |
| `PROVIDER_*`·`GENERATION_FAILED` | 비-Critical | 하루 4회·3일 창(기존) | 3일 넘으면 A-OWNER(공급자 장애) |
| 이미지 없음·인증 실패 | 비-Critical | 재사용 → 병원 대표 → 이미지 없음(기존) | 진행 |

**핵심 변경 둘:**
1. 비-Critical 지적은 `AUTO_CORRECTION_EXHAUSTED_KEY`(`generation_retry_policy.py:93`)를 켜지 않고 교체 예산을 먹지 않는다. 교정 상한
   (`CONTENT_AUTO_CORRECTION_MAX_PASSES`·`_MAX_REREVIEWS`, `config.py:555-556`)은 Critical 지적에만 센다.
2. 인용으로 짚은 비-Critical 문장은 LLM 제안(`propose_sentence_corrections`, `content_minimal_correction.py:886`) 없이 `_delete_sentence`(`:639`)로 지운다.
   삭제는 새 주장을 만들 수 없으므로 재검수는 첫 판정 모델(`POST_PUBLISH_AI_REVIEW_MODEL`, Sonnet)로 한다. 분량 하한(`CONTENT_BODY_MIN_CHARS`)·필수 문구 문장
   보호·hash 결합 PASS 요구는 그대로다(계약: 교정본은 반드시 재검수).

### 1.4 Publish

- 기존: 매시 08~23시 `morning_content_auto_publish`(`tasks.py:7904`, 대상 `_auto_publish_due_stmt` `:8138`), 07:45 보류 요약(`prepublish_content_generation_recovery` `:6395`).
- **D-day 마감선**: 예정일 18:00 KST에 발행 가능하지 않은 슬롯은 §3.1 R5(긴급 교체)로 바로 넘어가 22:00·23:00 스윕에서 대체 주제를 산다. 23:00 매시 발행이 그날의 마지막
  발행 기회다. Critical-blocked가 확정되는 순간(교체 상한 전이라도) 긴급 교체를 시작한다.
- **대체 정의**: 대체 글은 같은 슬롯 행(같은 `scheduled_date`, 같은 계약 월)에서 주제만 바꾼 글이다 — 행이 같으므로 계약 이행 집계(`first_published_at`)가 그대로 맞는다.
  별도 예비 풀은 쓰지 않는다(§6 O5 기본값).
- **검수 UNAVAILABLE 대체 사슬**(§6 O4): U1 기존 물러서기(1h·2^n, 최대 24h, `review_unavailable_backoff` `:236`) — 단 예정일 ≤ 내일이면 매 스윕 재시도.
  U2 같은 후보가 2회 연속 UNAVAILABLE이면 **다른 공급사의 대체 검수 모델**(`CONTENT_AI_REVIEW_FALLBACK_MODEL`, OpenRouter 슬러그)로 검수한다 — 독립 AI 검수이므로
  그 PASS는 유효하고 `ai_review.model`에 남는다. U3 둘 다 불가하면 발행하지 않고 다음 날로 넘긴다(7일 catch-up). 사람·Slack 없음. 공급사 전체 장애가 3일을
  넘으면 A-OWNER 1건.
- 결합된 PASS 없이 발행하는 경로는 만들지 않는다(현재 계약 유지).

### 1.5 Measurement & Report

| 단계 | 지금 | 바꿀 것 |
|---|---|---|
| 측정 창 24~말일 6시간마다(`run_monthly_sov_measurement`, `tasks.py:10858`) | 그대로 | — |
| 8일 00:15 최종화(`run_monthly_reports`, `:12718`) | LIMITED/UNAVAILABLE 확정 | UNAVAILABLE도 artifact를 만든다(아래) |
| `MONTHLY_SOV_MEASUREMENT_FAILED` 해결 | resolver `_same_period_measurement`(`incident_backlog.py:70-95`)가 **같은 기간의 RUN_SOV 성공**만 근거로 본다 — 최종화가 LIMITED로 닫은 달은 영원히 OPEN(감사 2건, 7.6일) | 근거 추가: `now ≥ monthly_final_judgment_at(period)`이고 그 기간의 최신 MONTHLY 보고서가 있으면 닫는다(`finalized_as_{quality}`). 측정 결손은 보고서 품질이 표현한다 |
| 원장 PDF 무결성(`monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents`, `:47-183`) | hash 불일치(`DOCTOR_PDF_ARTIFACT_INVALID`)면 사고만 연다(감사 4건, 6~8일 OPEN) | **자동 재생성**: 숫자 판정(`build_monthly_template_refresh_plan`) PASS면 `TEMPLATE_REFRESH`, 아니면 같은 기간 `GENERATE_MONTHLY_REPORT(rebuild)`를 OperationRun 키 `ARTIFACT_REPAIR:{report_id}:{n}`로 디스패치(보고서 판당 2회, 하루 1회). 사고는 RETRYING(기한 = 다음 시도 + 6h), 유효 artifact가 생기면 기존 회복 경로가 닫는다. 소진 시 A-DEV |
| `coverage_is_final`(`monthly_report_delivery.py:241`) | COMPLETE·LIMITED만 "최종" — 대조 작업이 UNAVAILABLE 달을 건너뛴다(`reconcile…` 안의 `if not coverage_is_final(report): continue`) | 최종 판정 시각이 지난 UNAVAILABLE도 최종으로 본다(같은 함수, 기간 인자). 전달 게이트 판정은 그대로 |
| 매니페스트 없는 달(`MANIFEST_MISSING`, `tasks.py:12428`) | D10: "전달 불가, 다음 달부터 자동" | §6 O6 기본값: **운영 보고서(UNAVAILABLE)** — 콘텐츠 실적 + V0 기준점 + "이번 달은 측정 전" 문구로 전달 가능 artifact를 만든다 |
| 전달 준비 알림 | `MONTHLY_CUSTOMER_READY` 마일스톤(`monthly_events.project_monthly_event`) | 그대로(Report). 8일 09:00 SLO가 누락을 잡는다 |
| 사람 전달 기록 | `monthly_delivery_events` 0건(전 병원) | 시스템 범위 밖(계약: 자동 전달 없음). 10일 이후 미전달 준비본을 일일 요약 한 줄로 보인다(§6 O8) |

**주의(감사 해석):** `monthly_reports.customer_ready`·`delivery_blockers` 컬럼은 생성 시점 스냅샷이다 — `monthly_manifest.apply_manifest_to_report`(`:395-405`)가 항상
`customer_ready=False`와 `DOCTOR_ARTIFACT_UNVALIDATED`를 쓴다. 전달 가능 여부의 정본은 `monthly_report_delivery.monthly_report_delivery_gate`(`:317`)다. 감사의
"ready=False 20/20"은 결함 신호가 아니다. SLO·운영 조회는 게이트를 쓴다(W8에서 운영 조회 q를 게이트 기준으로 바꾼다).

### 1.6 Monitoring — 병원별 일일 SLO 점검

새 서비스 `services/autonomy_slo.py`(순수 집계) + 기존 일일 요약(`fleet_heartbeat.collect_fleet_facts` `:294`, `build_fleet_heartbeat` `:450`, `FLEET_HEARTBEAT_HOUR_KST=18`)
확장. 점검은 하루 두 번 계산한다: **08:20**(08:00 발행 직후 — 행동 트리거용, Slack 없음)과 **18:00**(요약 Report).

병원별 지표:
- `S-PUB-D` 오늘 예정 슬롯 중 발행 수 / Critical-blocked(대체 상태) / 진행 중.
- `S-PUB-M` 계약 월 발행 수 vs 약속 편수의 오늘까지 비례치(floor), 남은 슬롯으로 채울 수 있는지(`at_risk = 약속 − 발행 − 남은 슬롯 > 0`).
- `S-48H` 48시간 안 예정인데 발행 가능하지 않은 슬롯 수와 각 슬롯의 현재 계단(§3.1).
- `S-REP` 지난달 보고서: 1~7일은 "진행", 8일 09:00 이후는 게이트 통과 artifact 유무. 이번 달 24일 이후는 측정 창 진행률.
- `S-COST` 일정 경로 남은 예약 용량 vs 오늘 밤 예상 필요량(§2.4).

행동: 08:20 점검이 §3.5 정체 감지기를 같은 술어로 한 번 돌린다(멱등). Slack: 18:00 Report 한 통(병원별 한 줄, 정상은 "이상 없음" 한 줄로 접는다).
A-CRIT·A-OWNER는 사건 시점에 따로 나간다. **조용한 사고는 OPEN/HIGH가 되지 않는다**(§3.6).

---

## 2. 예산·용량 모델

### 2.1 관측 (10/13, content 카테고리, `provider_usage` 실제 호출)

| workflow | 시도 | 실제 호출 | 성격 |
|---|---|---|---|
| `SOURCE_EVIDENCE_EXTRACT` | 107 | 51 | 화요일 03:00 `weekly-naver-source-sync`(`celery_app.py`, `naver_sync.weekly_naver_source_sync`) → `NAVER_SOURCE_HANDOFF` 11건 → 근거 재추출. 예약 `tasks._metered_process_source_asset`(`:3334`, `category="content"`) |
| `CONTENT_INDEPENDENT_REVIEW` | 65 | 62 | 생성 검수 + 03:10 사후검수(~50) |
| `CONTENT_GENERATION` | 59 | 39 | 작가 |
| `CONTENT_MINIMAL_CORRECTION` | 25 | 24 | 교정 |
| `CONTENT_IMAGE_REVIEW` | 23 | 14 | 이미지 정책 검토(content 카테고리) |
| `ESSENCE_ENGINE` | 51 | 0* | *essence 예약을 쓰지만 사용량은 content로 기록(`essence_engine.metered_llm_calls`, `:385-425`). `provider_usage` 검사 제약(`models/usage.py:85`)에 `essence`가 없다 |

Redis 일일 카운터는 18:31에 250/250(당시 상한)에 닿아 22:00·23:00 생성이 `COST_BLOCKED`(GENERATE 14 CANCELLED + 5 FAILED + REGENERATE 22 FAILED)로 막혔다.
18:00 스윕 한 번이 133회를 썼다 — 이미 여러 번 실패한 HARD 슬롯의 교정·재검수·재작성이 새 슬롯보다 먼저 돈 결과다. 월 누계는 10/14 08:33에 1,089(일 평균 ~82).

### 2.2 수요 모델 (상한은 결정됨: content 500/일·4,500/월)

글 한 편의 content 호출(작가 + 생성 검수 + 이미지 검토 + 교정·재검수):
- 깨끗한 경로 ≈ 4회, 어려운 경로(교정 2·재검수 2·재작성) ≈ 12회, 교체까지 ≈ 30회.
- §1.3 변경 뒤 기대값 `E ≈ 0.80×4 + 0.17×10 + 0.03×30 ≈ 5.8` (비-Critical이 교체로 가지 않으므로 교체 비율 3%).

| 물량 | 일정 경로/월 | 일정 경로/피크일(12편, 어려운 비율 50% 가정) |
|---|---|---|
| 124편(현재 9곳) | ~720 | ~96 |
| 160편(+3곳) | ~930 | ~96~130 |
| 200편(여유, ~16곳) | ~1,160 | ~150 |

비-일정 lane(월): 사후검수·공개 교정 ≤ 70/일 상한 → 실제 ~30/일 ≈ 900, 근거 추출(중복 제거 뒤) ≈ 300, 온보딩 자동 입력·관리자 재생성 ≈ 300.
**합계 ≈ 2,400~2,700/월** → 4,500 상한은 160~200편까지 1.7배 여유다. 하루 500도 피크 일정 경로 150 + 비-일정 150을 넉넉히 덮는다.
**문제는 총량이 아니라 순서다** — 낮 시간 비-일정·재시도 호출이 밤의 일정 경로를 먼저 먹는다. §2.3이 그것을 막는다.

### 2.3 Lane 예약 (content 카테고리 안)

카테고리를 늘리지 않는다(`provider_usage` 검사 제약 변경·롤백 위험). `cost_guard.reserve`/`check_and_increment`(`:464`, `:557`)에 `lane` 인자를 더하고
lane별 Redis 일·월 카운터를 카테고리 카운터와 **함께** 원자적으로 올린다(같은 Lua, `_RESERVE_BUDGET_SCRIPT` 확장). 판정은 "카테고리 총량 ≤ 상한 ∧ lane 사용 ≤ lane 상한".

| lane | 포함 | 일 상한 | 월 상한 | 우선 |
|---|---|---|---|---|
| `schedule` | 예정일 ≤ D+3 슬롯의 작가·생성 검수·교정·재검수·이미지 검토, 발행 게이트 재검수 | 카테고리 잔여 전부(예약) | 카테고리 잔여 전부 | 1 |
| `gate_review` | 발행 전 재검수(STALE·UNCERTAIN 스윕) | `schedule`과 공유 | 공유 | 2 |
| `post_publish` | 03:10 사후검수·공개 교정·UNCERTAIN 재검수 | 90 | 1,200 | 3 |
| `evidence` | 근거 추출·자동 입력(`hospitals.py` 자동 입력 미리보기, `profile_autofill_tasks.py:146`) | 80 | 600 | 4 |
| `adhoc` | 운영자 버튼 재생성(`regenerate_content_item`, `tasks.py:7428`) | 40 | 400 | 2(사람이 누른 것) |

추가 규칙:
- **밤 예약**: KST 22:00 이전에는 `schedule` 외 lane과 `schedule`의 "예정일 > 내일" 슬롯이 `카테고리 일 상한 − NIGHT_RESERVE(150)`을 넘지 못한다.
  22:00·23:00·01:00 스윕은 예약분을 쓴다. (일 단위 키가 KST 날짜(`cost_guard._daily_period` + `_now()` KST)이므로 01:00은 새 날 예산이다.)
- **월 예약**: 월 사용이 상한의 85%를 넘으면 비-`schedule` lane을 멈춘다(`schedule`만 100%까지).
- **병원별 비-일정 상한**: `evidence`+`post_publish` 합계 병원당 하루 30. 온보딩 병원 하나가 일 예산을 먹는 일(10/13 32%)을 막는다. 키 `cost_guard:content:hosp:{id}:{yyyymmdd}`.
- **스윕당 상한**: 낮 스윕(12·18시)은 한 실행에서 `schedule` 잔여의 30%까지만 claim한다(로더가 예상 호출 = 슬롯 수 × 6으로 페이지를 자른다).
- 경보 재분류: `COST_GUARD_SOFT_WARNING`(80%)은 Slack에서 빼고 일일 요약 숫자로. `schedule` lane이 막힌 경우만 A-OWNER(하루 1회). 비-일정 lane 소진은 조용하다.

### 2.4 우선순위 (같은 lane 안)

on-schedule 생성(예정일 오름차순) > 발행 게이트 재검수 > 사후검수 > 근거 재추출. 근거 재추출은 **내용 hash가 바뀐 자료만** 산다(W2):
`tasks._claim_source_processing`(`:3370` 부근)이 마지막 PROCESSED 입력 hash와 같으면 `UNCHANGED`로 물러나고, 주간 네이버 동기화는 새 URL·바뀐 본문만 넘긴다.

---

## 3. 단계별 자기 치유 계단

모든 계단은 **유한 예산**과 **명시적 끝 상태**를 가진다. 끝 상태는 PUBLISHED / REPLACED(같은 슬롯 새 주제로 발행) / CRITICAL_BLOCKED(대체 시도 중·완료) /
SHORTFALL(대체 불가, A-OWNER) / RESPECTED_HOLD(사람이 만든 보류) 중 하나다. "기한 없는 RETRYING"과 "OPERATOR_REQUIRED인데 아무도 안 보는 상태"를 없앤다.

### 3.1 생성·게이트 계단 (슬롯 단위)

| 단계 | 시점 | 예산 | 실패 시 |
|---|---|---|---|
| R0 첫 시도 | D-3 23:00 | 작가 세션 1(전송 ≤5, `GENERATION_PROVIDER_CALL_BUDGET` `content_engine.py:133`) | R1 |
| R1 결정적 치유 | 같은 세션 | 0원 | R2 |
| R2 비-Critical 문장 삭제 + Sonnet 재검수 | 같은 세션 | 재검수 ≤2(비-Critical 카운터) | R3 |
| R3 Critical 최소 교정 | 같은 세션·다음 스윕 | 교정 2·재검수 2(기존) | R4 |
| R4 지적 실은 재작성 | 다음 스윕(일 2세션, D≤1이면 3) | 주제당 작가 6·검수 6·전송 36 | R5 |
| R5 주제 교체 | 소진 즉시(D1) / D-day 18:00 / Critical-blocked 확정 즉시 | 비-Critical 원인 1회, **Critical 원인 +1회(총 2)** — `TOPIC_LIMIT` 2→3(`generation_attempt_state.py:45`) | R6 |
| R6 같은 날 재시도 | 교체 직후 같은 스윕(§1.2 same_day 확대) | 새 주제의 예산 | R7 |
| R7 끝 | 예정일 23:00 미발행 | — | Critical이면 CRITICAL_BLOCKED + A-CRIT(대체 결과 포함). 비-Critical이면 7일 catch-up 계속(사람 없음), catch-up 창 끝(D+7)에 SHORTFALL + A-OWNER |

`TOPIC_LIMIT` 상향은 평생 상한(`LIFETIME_*`, `:53-55`)을 함께 키운다. 기존 기록의 불변식(`_validate_versioned_budget`)은 "상한 이하"만 보므로 버전을 올리지 않는다
(0-1의 교훈: 리셋이 아니라 상한 상향이라 `LEGACY_SPEND_UNKNOWN`을 만들지 않는다 — W6 테스트로 증명).

### 3.2 발행 계단

P1 매시 게이트(08~23) → P2 STALE 재검수(같은 시간 안 디스패치) → P3 UNAVAILABLE 대체 사슬(§1.4 U1~U3) → P4 18:00 마감선에서 R5로 → P5 catch-up 7일
(`AUTO_PUBLISH_CATCHUP_DAYS`, `post_publish_review_policy.py:20`) → P6 22:30 백로그 복구(`content_backlog_recovery.reconcile`)가 미래 날짜로 옮김 — **I1 위반**이므로
SHORTFALL 지표로 센다(옮긴 슬롯 수 = 그 달 일정 미준수 편수).

### 3.3 측정·보고서 계단 (병원·월 단위)

| 단계 | 시점 | 행동 | 끝 |
|---|---|---|---|
| M1 | 24~말일 6시간마다 | 측정 창(기존) | — |
| M2 | 1~7일 | 미완 셀 복구 창(기존) | — |
| M3 | 8일 00:15 | 최종화 → COMPLETE/LIMITED/UNAVAILABLE, 매니페스트 없으면 운영 보고서(O6) | 보고서 행 |
| M4 | 15분 대조 | 원장 PDF 무결성 → 실패 시 자동 재생성 2회(§1.5) | 유효 artifact |
| M5 | 8일 09:00 | SLO: 서비스 구간 병원마다 게이트 통과 artifact 확인 → 없으면 rebuild 1회 즉시 디스패치 | — |
| M6 | 8일 18:00 | 여전히 없으면 A-DEV(병원·월 목록 한 통) | 사람(개발) |

`MONTHLY_SOV_MEASUREMENT_FAILED`는 M3 뒤 resolver 근거(§1.5)로 닫힌다.

### 3.4 기타 계단

- **리드 진단**(I1/I2 밖, I3 적용): 기존 5회 백오프 뒤 실패면 `LEAD_DIAGNOSIS_FAILED`를 열지 않고 조용한 상태(리드 행 `execution_status=FAILED`)로 남긴다. 리드가
  NEW가 아니게 되거나 14일이 지나면 resolver가 닫는다(`incident_backlog.RESOLVERS`의 `LEAD_DIAGNOSIS_FAILED`·`RECOVER_LEAD_MEASUREMENT` 근거 확장, `:643-646`).
  감사의 25일짜리 1건(해누리내과의원, 9/22)은 이 근거로 닫힌다.
- **레거시 `BACKGROUND_TASK_FAILED`**(resolver 없음, 9~21일 OPEN 2건): 같은 task 이름이 7일 동안 다시 실패하지 않으면 닫는 resolver.

### 3.5 정체 감지기 (stall detector)

새 모듈 `workers/content_stall_detector.py`. 실행: 15분 `reconcile_essence_snapshots` 틱(`tasks.py:4706`, P4 E3과 같은 방식 — RedBeat 판 올리지 않음)과 08:20 SLO 점검.
P4가 그 함수를 바꾸는 중이므로 **P4 배포 B 병합 뒤** 붙인다.

**술어(슬롯):** 상태 DRAFT/READY ∧ 예정일 ≤ 오늘+2(48시간) ∧ 발행 게이트 불통과(dry `assess_content_publication`) ∧ 살아 있는 claim 없음
(`nightly_generation_batch.generation_claim_is_active`) ∧ 병원 ACTIVE·발행 보류 아님 ∧ `last_progress_at < now − 6h`.
`last_progress_at = max(generation_attempt.observed_at, generated_at, body_updated_at, 그 글의 마지막 OperationRun finished_at, 마지막 교체 시각)`.

**행동(다음 계단, 슬롯·계단별 한 번 — OperationRun 키 `CONTENT_STALL:{item}:{rung}:{yyyymmdd}`):**

| 상태 | 계단 |
|---|---|
| S0 시도 기록을 읽을 수 없음(`LEGACY_SPEND_UNKNOWN`) | `generation_attempt_state.replace_legacy_budget_and_release`(D22 함수)를 `system:stall-detector`로 호출 — PR #252의 일반화된 안전망 |
| S1 `next_retry_at > now`인데 예정일 ≤ 내일 | `next_retry_at`을 다음 스윕으로 당긴다(긴급 세션 규칙 안에서) |
| S2 비-Critical 종착(`OPERATOR_REQUIRED`·`INPUT_CHANGE_REQUIRED`) | 즉시 주제 교체(R5) |
| S3 Critical 원인 + 교체 남음 | 즉시 교체(R5) |
| S4 교체 다 씀 | Critical이면 CRITICAL_BLOCKED + A-CRIT, 비-Critical이면 R7 경로 |
| S5 원인이 `COST_BLOCKED` | 예산을 올리지 않는다(I4). A-OWNER 하루 1회(위험 슬롯 수와 함께) |
| S6 디스패치 유실(QUEUED·claim 없음) | 기존 자율 복구가 맡는지 확인, 아니면 `generate_claimed_content_item` 재디스패치 |

**술어(보고서):** 서비스 구간 병원 × 지난달, 5일 09:00에 MONTHLY 보고서가 없음 → `GENERATE_MONTHLY_REPORT` 1회 디스패치. 8일은 §3.3 M5.

**끝 보장:** 각 계단은 위 예산으로 한정되고, 감지기 자체는 슬롯·계단·날짜당 1회라 무한 루프가 없다. 감지기 실행 결과(행동 수·계단 분포)는 일일 요약 한 줄.

### 3.6 사고 위생 (조용한 사고는 OPEN/HIGH가 아니다)

- `ops_incident_alerts.open_ops_incident`(기본 `severity=HIGH`, `:55`)가 `incident_types.incident_is_quiet(type)`(`:55`)이면 `severity=LOW`로 연다.
- `POST_PUBLISH_REVIEW_FLAGGED`(`post_publish_ai_review._open_flag_incident`, `:453-503`, 지금 `SAFETY_BLOCKED` 지문 → HIGH)를 지적 등급으로 가른다:
  비-Critical 지적만이면 사고를 열지 않고 FLAGGED 표시만 남기며, 7일 재검수(1-10b) 뒤 PASS·교정 성공·비-Critical 확정이면 표시를 닫는다.
  Critical(C1은 공개 가시성이 이미 숨김, C2 인용 HARD가 공개 교정 뒤에도 남음)은 §6 O2를 따른다.
- 일회성 정리 CLI `app.utils.quiet_incident_settle`(dry-run 기본): 열린 조용한 사고 74건을 위 규칙으로 재판정해 닫거나 LOW로 내린다(Slack 없음, 감사행).

---

## 4. 갭 목록 — 감사 소견·정체 패턴 → 제거 장치

| # | 소견(10/14 08:33) | 원인 | 장치 | 워크스트림 |
|---|---|---|---|---|
| G1 | 예정일 지난/임박 미발행 초안 30건 | 아래 G2~G7의 합 | 각 장치 + 정체 감지기 | W3·W4·W5·W10 |
| G2 | 10건 fallback narrative SQL 거절 | 0090 이전 판 조건 | **해결(0090 배포됨)**. 재발 감시: SLO `S-48H`의 사유 분포 | — |
| G3 | 6 동결 + 56 위험: 미시도 슬롯 `LEGACY_SPEND_UNKNOWN` | 예산 기록 없는 슬롯 판독 | **PR #252**(진행 중) + 정체 감지기 S0 안전망 | (#252), W10 |
| G4 | 생성 하드 실패 3건: 지어낸 참고자료 URL 전부 탈락 | 참고자료 공백 = 거절 | 참고자료 없이 저장, 공백을 재작성 지적으로 넘기지 않음 | W5 |
| G5 | 재작성 거절이 전송 예산을 태워 "예산 소진"으로 오분류 | `classify_generation_failure`가 원인 미보존 | `exhausted_by` 라벨 + 원인별 경로 | W5 |
| G6 | HARD 5건 중 비-Critical 2건(진료 철학 문장, 2주 vs 3주)이 교정·교체 예산 소진 | 지적 등급 구분 없음 | `finding_criticality` + 결정적 삭제·수치 치환, 비-Critical은 교체 예산 미사용 | W4 |
| G7 | 진짜 의료 안전 HARD 3건 | 실제 Critical | 계단 R3~R5(+Critical 교체 2회) → 같은 날 대체 + A-CRIT | W4·W6 |
| G8 | 교체 뒤 `next_retry_at`이 다음 KST 날로 | `_record_topic_swapped_attempt`의 `same_day`가 예정일 당일만 | 조건을 `≤ 오늘+1`(지난 슬롯 포함)로 | W3 |
| G9 | content 상한 18:31 도달, 22:00·23:00 차단 | 순서 없는 공유 예산 | lane 예약·밤 예약·스윕당 상한(상한 크기는 결정됨) | W1 |
| G10 | 330 호출 중 근거 재추출 107 | 주간 네이버 동기화가 바뀌지 않은 본문도 재추출 | 입력 hash 같으면 UNCHANGED | W2 |
| G11 | 0편 병원 2곳이 일 예산 32% | Essence 재합성 churn + 근거 추출 | P4 D33(검수 제거) + 병원별 비-일정 상한 | P4, W1 |
| G12 | 월 3000 상한 빠듯 | 상한 크기 | **해결(4500으로 상향됨)** + 월 85% 비-일정 정지 | W1 |
| G13 | Essence 미승인으로 0편 병원 3곳 | 승인 게이트 | P4 배포 B | P4 |
| G14 | 원장 PDF hash 불일치 4건 6~8일 OPEN | 대조 작업이 사고만 열고 재생성하지 않음 | 자동 재생성 2회 | W8 |
| G15 | 월간 SoV 사고 2건이 최종화 뒤에도 OPEN | resolver가 같은 기간 RUN_SOV 성공만 봄 | 최종화 근거 추가 | W8 |
| G16 | 리드 진단 실패 25일 OPEN | resolver가 측정 복구만 봄 | 리드 상태·14일 근거, 조용한 상태 | W9 |
| G17 | `monthly_delivery_events` 전 병원 0 | 사람 전달 미기록 | 시스템 범위 밖, 요약 한 줄 + O8 | W10, O8 |
| G18 | 조용한 `POST_PUBLISH_REVIEW_FLAGGED` 74건 OPEN/HIGH | 조용함이 Slack만 끄고 등급·수명은 그대로 | LOW + 등급별 생성·종결 + 일회 정리 | W9 |
| G19 | 레거시 `BACKGROUND_TASK_FAILED` 2건 9~21일 | resolver 없음 | 7일 무재발 resolver | W9 |
| G20 | `GENERATION_BLOCKED_DIGEST`가 비-Critical 보류도 "조치 필요"로 보냄(10/12·13 3통) | 다이제스트 대상 = 모든 새 보류 | Critical-blocked만 A-CRIT로, 나머지는 요약 숫자 | W10 |
| G21 | `COST_GUARD_SOFT_WARNING`이 `[Error]`로 나감 | 등급 | 요약 숫자로 | W1 |
| G22 | 감사의 `customer_ready=False` 20/20 | 스냅샷 컬럼 오독 | 운영 조회를 전달 게이트 기준으로 | W8 |
| G23 | UNAVAILABLE 달이 PDF 대조에서 빠짐 | `coverage_is_final`이 UNAVAILABLE 제외 | 최종 시각 뒤 UNAVAILABLE 포함 | W8 |
| G24 | 매니페스트 없는 첫 달 = "전달 불가"(I2 위반) | D10 | O6 운영 보고서 | W8 |
| G25 | 백로그 복구가 슬롯을 미래로 옮김 | 설계상 | SHORTFALL 지표로 셈(I1 위반 가시화) | W10 |
| G26 | ESSENCE 사용량이 content로 기록 | `provider_usage` 제약에 essence 없음 | 기록 정합(관측 정확도만, 지출 영향 없음) | W1(선택) |

**이 설계가 덮지 않는 것(명시):**
- OpenRouter 전체 장애 3일 초과·크레딧 소진(402 — 환경 장애 분류 밖): 발행 불가. A-OWNER로만 알린다.
- Redis 장애 시 비용 가드 fail-open(D25): lane 예약도 함께 꺼진다 — 절대 상한이 아님(기존 계약).
- 측정 공급자 장기 장애로 월 전체가 UNAVAILABLE: I2는 지켜지지만(UNAVAILABLE 보고서) 내용은 빈약하다 — 수용.
- 사람이 PAUSED·비공개·근거 철회한 병원·글: RESPECTED_HOLD. 월 편수 SLO에서 제외 표기만.
- 원장에게 실제로 전달했는지(사람 행위) — O8.
- GCS·Cloud SQL·LB 장애: 기존 GCP uptime/ERROR 경보 영역.

---

## 5. 워크스트림 (영향 순, 증분마다 독립 배포·되돌림 가능)

공통: 브랜치는 origin/main에서 `claude/full-auto-wN`. `tasks.py`·`incident_backlog.py`를 겹쳐 쓰는 워크스트림은 병합 순서로 직렬화한다. P4(이 worktree)가
`tasks.py`(Essence·reconcile), `incident_types.py`, 알림 문구를 바꾸는 중이므로 그 영역을 건드리는 W9·W10은 **P4 배포 B 병합 뒤** 시작한다. 각 워크스트림은
`make test-backend-local`(관련 파일 + `*_postgres.py`), `ruff`, `make copy-guard`, `make db-budget-guard` 새 실패 0이 완료 기준이고, 배포 기록에 전후 운영 조회를 남긴다.

### W3 — 같은 날 재시도·리드 타임 D-3 (1순위, 작음)
- 변경: `topic_swap_fallback._record_topic_swapped_attempt`의 `same_day` → `scheduled_date <= today+1`; `generation_retry_policy`에 긴급 세션 +1
  (`_sample_budget_is_spent`·`_earliest_eligible_date`·`next_recovery_deadline` 공통 판정 함수 하나); `NIGHTLY_GENERATION_LOOKAHEAD_DAYS=3`;
  로더 정렬(`nightly_generation_batch._generation_order_columns`).
- 테스트: `tests/test_topic_swap_fallback.py`(지난 슬롯·내일 슬롯 교체 → 같은 날 기한), `tests/test_generation_retry_policy.py`(긴급 세션, 평생 상한 불변),
  로더 정렬 단위 테스트, `next_recovery_deadline`이 `None`으로 굳는 경우가 늘지 않는지(기존 회귀).
- 배포 검증: 다음 23:00 디스패치의 창(D-1..D-3) 로그, 교체 슬롯의 `next_retry_at` ≤ 당일 22:00.
- 롤백: 코드 롤백만. 이미 쓴 긴급 세션 기록은 무해(상한 이하). lookahead는 env로도 되돌릴 수 있게 설정 유지.

### W5 — 참고자료는 생성을 막지 않는다 + 소진 원인 분류 (2순위, O3 필요)
- 변경: `content_engine` 검증(`:2460-2490`)이 수기 목록 치유 실패 시 `references_omitted`로 통과; 재작성 지적에서 참고자료 공백 제외;
  `generation_run_control.classify_generation_failure`에 `exhausted_by`; 발행 게이트 `content_publication.has_required_references`(`:77`)와 공개 표면
  `public_surface_has_required_references`(`:97`)를 O3에 맞춤(두 곳 같은 술어 — P3 교훈 4); `reference_requirement` 정본에 `references_omittable(item)`;
  `GENERATION_GATE_CATALOG_VERSION`(`tasks.py:656`) 올림(종착 슬롯 1회 재평가).
- 테스트: `tests/test_content_engine_seo_geo.py`(빈 참고자료 = 통과 + 기록), `tests/test_content_publication.py`(게이트·공개 표면 일치),
  `tests/test_reference_requirement.py`, 분류 테스트(`exhausted_by=references`).
- 영향 조회(배포 전): 참고자료 0개로 보류 중인 슬롯 목록(q 신설) — 배포 즉시 발행될 글.
- 롤백: 카탈로그 판을 되돌리면 재평가 반복(P3 롤백 주의와 같음) → 호환 롤백 이미지(판 유지) 준비. 참고자료 없이 발행된 글은 롤백으로 숨지 않게
  공개 표면 술어를 **기록(`references_omitted`) 기반**으로 둔다(옛 코드는 유형 규칙으로 숨긴다 — 롤백 시 숨김 발생 목록을 runbook에 남긴다).

### W4 — 지적 등급(finding criticality)과 비-Critical 결정적 해소 (3순위)
- 변경: 새 `services/finding_criticality.py`(순수: 지적·필터 라벨·거절 라벨 → CRITICAL/NONCRITICAL/RESPECT/SYSTEM, §1.3 표가 테스트 표);
  `content_minimal_correction.plan_corrections`(`:420`)가 비-Critical 인용 문장은 `_delete_sentence`로, 수치 불일치는 승인 사실 치환으로;
  재검수 모델 선택; 교정 계수 분리(비-Critical 카운터 `noncritical_rereviews` ≤2); `tasks._escalate_exhausted_correction`(`:931`)·
  `AUTO_CORRECTION_EXHAUSTED_KEY` 설정 지점이 Critical에만; 출력 C3(주입 문형)·C4(다른 병원명·전화·도메인 — 같은 DB의 다른 tenant 정확 일치) 결정적 검사를
  생성 검증에 추가.
- 테스트: 새 `tests/test_finding_criticality.py`(표의 모든 행), `tests/test_content_minimal_correction*.py`(삭제·치환·하한·필수 문구 보호·hash 결합 PASS 요구),
  감사 실례 픽스처 3종(진료 철학 문장, 2주/3주, 검은 변 응급) 회귀.
- 롤백: 코드만. 이미 삭제로 저장된 본문은 남는다(재검수 PASS에 결합돼 있어 안전).

### W8 — 보고서는 언제나 나온다 (4순위, **10/31 전 배포 필수** — 11월 1~8일 창)
- 변경: `incident_backlog._same_period_measurement`에 최종화 근거; `monthly_artifact_reconciliation`에 자동 재생성 디스패치(키·예산·RETRYING 기한);
  `coverage_is_final` 최종 시각 뒤 UNAVAILABLE 포함; 매니페스트 없는 달 운영 보고서(O6, `run_monthly_reports` `:12718` 경로·`report_engine` 템플릿 분기);
  `summarize_monthly_report_gaps`(`:12883`)에 5일·8일 보고서 계단(§3.3 M5); 운영 조회 q를 `monthly_report_delivery_gate` 기준으로.
- 테스트: `tests/test_monthly_artifact_reconciliation_postgres.py`(불일치 → 재생성 디스패치 → 유효 → 닫힘, 예산 소진 → A-DEV), incident_backlog resolver 테스트,
  `tests/test_monthly_artifact_corruption_postgres.py`, 운영 보고서 PDF 내용 가드(`test_report_plain_language.py` 금지어).
- 배포 뒤 1회: 현재 OPEN 4건(PDF)·2건(SoV)이 15분 안에 재생성·닫힘으로 가는지.
- 롤백: 재생성된 새 판 보고서는 남는다(append-only, 무해). 운영 보고서 템플릿은 옛 코드에서 생성되지 않을 뿐.

### W9 — 사고 위생 (5순위, P4 배포 B 뒤)
- 변경: `open_ops_incident` 조용한 종류 LOW; `post_publish_ai_review._open_flag_incident` 등급별(비-Critical은 사고 없음); 리드 진단·레거시 resolver;
  일회 정리 CLI `app.utils.quiet_incident_settle`.
- 테스트: `tests/test_post_publish_ai_review.py`·`test_post_publish_senior_rereview.py`, resolver 테스트, CLI dry-run 출력 테스트.
- 롤백: 닫힌 사고는 되살아나지 않는다(P1과 같은 주의). 옛 코드는 새 FLAGGED 글에 다시 HIGH 사고를 연다(Slack은 여전히 없음).

### W1 — lane 예약·우선순위 (6순위, 상한 크기는 이미 반영)
- 변경: `cost_guard`(`lane` 인자, Lua 확장, 밤 예약, 월 85%, 병원별 키), `config`(lane 상한·`NIGHT_RESERVE`), 호출 지점 태깅
  (`content_ai_review.py:1051,1124`·`content_minimal_correction.py:896`·`image_engine.py:513,835`·`content_image_reuse.py:293`·`tasks.py:3343,6033,7428`·
  `post_publish_ai_review`·`profile_autofill_tasks.py:146`·`hospitals.py` 자동 입력 미리보기) — 호출부가 lane을 모르면 기본 `schedule`이 아니라 **`adhoc`**(안전한 쪽);
  일정 경로 판정은 슬롯 예정일로; 비용 소프트 경고 Slack 제거.
- 테스트: `tests/test_cost_guard.py`(lane 독립·밤 예약·월 85%·병원별), `tests/test_cost_guard_redis_integration.py`(원자성), `test_cost_guard_fail_open_postgres.py` 회귀.
- 롤백: lane 키는 옛 코드가 무시한다(카테고리 카운터는 같은 값으로 계속 오른다).

### W2 — 근거 재추출 중복 제거 (7순위, 다음 화요일 03:00 전이면 좋음)
- 변경: `tasks._claim_source_processing`이 마지막 PROCESSED 입력 hash와 같으면 `UNCHANGED`; `naver_sync`가 새·바뀐 본문만 넘김.
- 테스트: `tests/test_naver_sync.py`, 자료 처리 run 테스트(UNCHANGED가 run 항목을 종결하는지).
- 롤백: 코드만.

### W10 — 일일 SLO 점검·정체 감지기·Slack 등급 (8순위, P4 배포 B 뒤)
- 변경: 새 `services/autonomy_slo.py`, 새 `workers/content_stall_detector.py`(15분 틱 + 08:20 — 08:20은 기존 매시 발행 태스크 뒤 hook, RedBeat 판 불변);
  `fleet_heartbeat` 확장(병원별 한 줄); `content_publish_notifications.enqueue_generation_blocked_digest_sync`(`:556`)가 Critical-blocked만;
  `notification_labels`에 `[Critical]`·`[대표 결정]` 표지(라벨 문자열은 그 모듈에만).
- 테스트: 감지기 술어·계단·멱등(같은 날 두 번 → 행동 1회), SLO 집계(서비스 구간·약속 편수 `content_month_target.month_content_target_sync`),
  heartbeat 문자 길이(`fallback_text` 1000자 — 10/1 장애 회귀).
- 롤백: 감지기가 당긴 `next_retry_at`·교체는 남는다(정상 계단의 결과).

### W6 — Critical 대체 교체 2회 (9순위)
- 변경: `TOPIC_LIMIT` 2→3, Critical 원인 교체 허용(`topic_swap_fallback.topic_swap_budget_left`(`:211`)에 원인 인자), D-day 18:00 마감선 트리거.
- 테스트: `generation_attempt_state` 불변식(기존 기록이 `LEGACY_SPEND_UNKNOWN`이 되지 않음 — 운영 표본 JSON 픽스처), 교체 2회 경로.
- 롤백: 옛 코드는 상한 2라 세 번째 주제 기록을 "상한 초과"로 읽을 수 있다 → **롤백 전** 세 번째 주제 슬롯 목록을 조회하고 runbook에 기록(옛 코드에서
  `GENERATION_BUDGET_EXHAUSTED`로 굳을 뿐 오지출은 없다).

### W7 — 검수 UNAVAILABLE 대체 모델 (10순위, O4 필요)
- 변경: `content_ai_review`에 `CONTENT_AI_REVIEW_FALLBACK_MODEL`(2회 연속 UNAVAILABLE 뒤), `ai_review.model` 기록, 게이트 불변.
- 테스트: `tests/test_content_ai_review.py`(대체 모델 PASS 결합·UNAVAILABLE 계수·비용 lane).
- 롤백: 대체 모델 PASS는 hash 결합이라 옛 게이트도 인정한다.

### W11 — 문서
- 증분마다 CLAUDE.md 계약 문단(참고자료 필수 규칙·지적 등급·교체 상한·lane·보고서 계단·알림 등급), MasterPlan 결정표 D35~, 배포 안내.

### 병렬 웨이브

| 웨이브 | 병렬 | 주의 |
|---|---|---|
| A (지금) | W3, W8, W2 | W8·W2는 `tasks.py`의 다른 영역(월간·자료 처리). W3은 `generation_retry_policy`·`topic_swap_fallback` |
| B | W5 → W4 (직렬: 둘 다 `content_engine`·카탈로그 판) | W5가 카탈로그 판을 올리면 W4는 같은 판을 쓰거나 한 번 더 올린다(한 배포에 묶기 권장) |
| C (P4 배포 B 뒤) | W9, W10 | 둘 다 `incident_*`·알림 — W9 먼저 병합 |
| D | W1, W6, W7 | W1은 W4·W7의 호출 지점과 겹침 → W4 뒤 |

---

## 6. 대표 결정이 필요한 것 (정책만, 권장 기본값 포함)

| # | 결정 | 권장 기본값 | 이유 |
|---|---|---|---|
| **O1** | 일정 경로 예약 방식 — lane 상한 숫자 | `post_publish` 90/일·1,200/월, `evidence` 80/600, `adhoc` 40/400, 밤 예약 150(22시 전 사용 상한 = 일 상한 − 150), 월 85%에서 비-일정 정지, 병원별 비-일정 30/일 | 상한 크기는 결정됨. 순서만 보장하면 4,500/월로 200편까지 덮는다 |
| **O2** | 공개된 글에 Critical이 남았을 때(공개 교정 뒤에도 C2 인용 HARD) | **자동 비공개(WITHHELD) + 사이트 재검증 + A-CRIT 1건**. 비-Critical 사후검수 지적은 조용히 닫는다 | D2(10/8, Slack 금지)는 74건의 대부분이 비-Critical이던 시점의 결정이다. 의료 안전은 이번 목표의 Critical 정의에 든다. C1은 이미 공개 가시성이 숨긴다 |
| **O3** | 참고자료 필수 유형(질환·시술 등)이 검증된 참고자료 없이 발행될 수 있는가 | **허용** — 수기 목록 치유가 실패하면 참고자료 없이 발행, `references_omitted` 기록, 월간 보고서 운영 요약에 편수만 표시 | "참고자료는 막지 않는다"(목표). 지어낸 URL 압력을 없앤다. 현재 CLAUDE.md 계약(필수 → `MISSING_REFERENCES` 보류)을 바꾸는 결정이다 |
| **O4** | 검수 UNAVAILABLE이 이어질 때 | **결합된 PASS 없이는 발행하지 않는다**. 대신 2회 연속 UNAVAILABLE 뒤 **다른 공급사의 대체 검수 모델**을 쓴다(모델 선택은 대표/개발). 공급사 전체 3일 장애면 A-OWNER | 검수 없는 의료 글 공개는 Critical 위험. 대체 모델은 독립 검수라 계약을 지킨다 |
| **O5** | Critical-blocked 슬롯 대체 방식 | **같은 슬롯 주제 교체(Critical 원인은 2회까지)**. 예비 글 풀(병원당 사전 생성 1편)은 SLO 미달이 월 1건을 넘을 때만 도입 | 같은 행이라 계약 월 집계·일정이 그대로다. 예비 풀은 새 컬럼·롤백 위험·추가 비용 |
| **O6** | 매니페스트 없는 달(첫 달 병원) 보고서 | **운영 보고서(UNAVAILABLE)를 만들어 전달 가능하게**(콘텐츠 실적 + V0 기준점 + "이번 달은 측정 전") | I2("보고서 없음은 없다"). D10("전달 불가")을 대체 |
| **O7** | 개발 결함 알림(A-DEV, `[개발 확인]`)을 유지하는가 | **유지** — 자기 치유를 다 쓴 결함만, 원인별 하루 1건 | 목표는 "Critical·대표 결정만"이지만 자기 치유 소진 결함을 숨기면 I1/I2가 조용히 깨진다 |
| **O8** | 월간 보고서 원장 전달 | **사람 전달·기록 유지**(계약 그대로), 10일 이후 미전달 준비본을 일일 요약 한 줄로 | 자동 전달은 수신자·동의 모델이 필요(MasterPlan §7 범위 밖). 감사의 전달 기록 0건은 시스템 결함이 아니다 |

엔지니어링 기본값(이견 없으면 진행): 리드 타임 D-3, 긴급 세션 +1, 비-Critical 문장 삭제 뒤 Sonnet 재검수, 정체 6시간·48시간 창, D-day 18:00 마감선,
리드 진단 14일 종결, 레거시 사고 7일 무재발 종결.

---

### 6.1 대표 결정 (2026-10-14)

- **D35 (O2):** 공개된 글에서 나중에 Critical 지적이 나와도 **자동 비공개하지 않는다.** 글은 그대로 두고 "마케터 검토 필요" Slack 1건만 보낸다(글당 한 번). Critical이 아닌 지적은 조용히 닫는다.
- **D36 (O3):** 참고자료가 필수인 의료 글도 검증된 참고자료가 없으면 **참고자료 없이 생성·발행한다**(기록만 남긴다). 참고자료가 100% 있어야 하는 것은 아니다 — D23을 모든 유형으로 넓힌다. 있으면 기존 실제 문서 검증을 그대로 거친다.
- **D37 (O4):** 독립 AI 검수가 장애(UNAVAILABLE·시간 초과·공급자 오류)로 판정을 못 내리면 **먼저 발행하고 사후 검수**한다. 발행 전 결정적 검사(의료광고 금지 표현 필터·tenant·공개 권한·인젝션)는 그대로 막는다. 검수가 **판정한** HARD·Critical 지적은 종전대로 발행을 막고 교정·교체 계단을 탄다. 선발행된 글은 03:10 사후 검수 대상 맨 앞에 두고, 거기서 나온 Critical은 D35대로 알린다.
- **D38 (O6):** 측정 기준(매니페스트)이 없는 첫 달 보고서는 전달 가능한 보고서로 만들고 표시는 **'기준 설정'**이다('측정 없음'이라고 쓰지 않는다). 다음 달부터 비교의 기준점이 된다.
- **D39 (O8):** 월간 보고서의 원장 전달은 **사람이 전달하고 기록한다**(현행 유지). 10일이 지나도 전달 기록이 없는 준비본은 일일 요약 한 줄로 보인다.
- O1(lane 상한 숫자)·O5(같은 슬롯 주제 교체 최대 2회)·O7(개발 결함 알림 원인별 하루 1건)은 권장 기본값대로 한다.

## 부록 A. 배포 전후 지표 (운영 조회 `ops_sql_report`에 q 신설)

| 지표 | 목표 | 조회 |
|---|---|---|
| 예정일 당일 발행률(대체 포함) | 100% | q-slo-daily: 예정일 = D, `first_published_at::date(KST) = D` |
| 예정일 지나 미발행(SHORTFALL·백로그 이동) | 0 | q-slo-overdue |
| `COST_BLOCKED`로 끝난 일정 경로 실행 | 0 | `runs_by_type_day`(감사 쿼리) code=COST_BLOCKED, 예정일 ≤ D+3 |
| 비-Critical 지적으로 교체된 슬롯 | 0 | `topic_swap_history`의 원인 등급 |
| 8일 09:00 전달 가능 artifact 없는 서비스 병원 | 0 | q-slo-report(전달 게이트 기준) |
| 조용한 사고 중 OPEN/HIGH | 0 | 사고 표 |
| Slack `[Error]` 중 Critical·대표 결정·개발 외 | 0 | outbox 30일 |
