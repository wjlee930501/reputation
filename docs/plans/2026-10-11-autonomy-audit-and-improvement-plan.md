# 자율 운영 점검·평가·개선 계획 (2026-10-11)

기준 소스: main `8c8f6e60` (PR #238까지). 방법: 영역별 코드 정적 감사 5개(콘텐츠·측정/보고서·인시던트/알림·병원 생애주기·인프라/배포) + 사실 수집 2개(Beat/Admin/예산 전수, 9~10월 릴리스 기록) + 핵심 주장 재현 검증 1개. 운영 DB는 조회하지 않았다. 이 문서는 계획이며 배포·운영 데이터 변경의 증거가 아니다.

## 1. 한 줄 결론

**"AI·워크플로우 기반 자율 운영"의 뼈대는 맞게 지어졌다. 그러나 지금은 자율 시스템이 아니라 "사람이 매일 배포하고 알림을 읽어야 굴러가는 반자동 시스템"이다.** 원인은 설계 철학이 아니라 세 가지 구조 결함이다.

1. **재시도 사다리가 수렴하지 않는다** — 가장 큰 자동 복구 장치(본문 표본 재시도 → 주제 교체)가 예산 설계 오류로 사실상 작동하지 않는다(재현 확인).
2. **사고(Incident) 모델이 "사람의 일"을 과잉 생산한다** — 모든 사고가 OPEN·기한 없음으로 열리고, 9개 유형은 닫는 코드가 없어 영구 OPEN이다. 그래서 일일 요약은 늘 "확인 필요"이고 알림의 65~75%는 조치 불필요다.
3. **시스템이 자기 장애를 사람에게 전하지 못한다** — 10월 사고 3건(워커 4h 정지, 개발 웹훅 19일 사망, 일일 요약 7일 무음)은 모두 사람이 배포하다 우연히 발견했다.

여기에 **배포 자체가 100% 수동**(CD 없음, 작업 PC 의존, `.env.production` 수동 대조)이고 9~10월 커밋의 39%가 `fix`라서, 현재 사람 개입의 가장 큰 몫은 "운영"이 아니라 "시스템을 고치고 배포하는 일"이다.

### 영역별 판정

| 영역 | 자율성 | 판정 요지 |
|---|---|---|
| 콘텐츠 생성·발행 | △ | 정상 경로는 자동. 실패 복구 사다리가 수렴하지 않아 OPERATOR_REQUIRED가 과잉 생산됨 |
| 측정·보고서 생성 | ○ | 측정·마감·PDF까지 자동. 매월 확정적 오탐 3건, 사람이 손대도 못 푸는 경로 1건 |
| 보고서 전달 | ✕ | 100% 사람(제품 결정). 자동 전달에 필요한 데이터(수신자·동의)가 모델에 없음 |
| 사고·알림·운영자 큐 | ✕ | 술어는 맞으나 입력이 틀림. 영구 OPEN 9종, 기계 소유 일에 Error 알림 |
| 병원 온보딩 | △ | 활성화·Essence 승인은 자동. **일정 저장이 사람 몫**이라 "공개 중인데 글 0편"이 조용히 생김 |
| 인프라 실행 계층 | ○ | 워커 liveness·cold shutdown·검증→claim 순서·에피소드 경보 갖춤 |
| 자기 감시 | ✕ | 운영 Slack 사망·Beat 멈춤·watchdog 토큰 공백은 아무도 모름 |
| 배포 | ✕ | CD 없음. 하루 1회 규칙 미강제(10/9 4회). env 정본이 로컬 파일 |

## 2. 검증된 핵심 결함 (우선순위순)

### F1. 주제 교체가 작가 호출을 한 번도 못 한다 — **재현 확인**
- `generation_attempt_state.py:437-438`에서 작가 호출 1회 = `writer_results += 1`, 상한 6(`:367-370`). 한 세션이 최대 3회(`tasks.py:2281`)이므로 실패 세션 2개면 **첫날에 6회 소진**.
- 이후 세션은 `GENERATION_BUDGET_EXHAUSTED`(`generation_run_control.py:179`)가 되는데 이 코드는 `SAMPLE_BODY_CODES`(`generation_retry_policy.py:103`)라서 남은 3일 사다리가 **지출 없이 실패 횟수만** 소모한다.
- `record_topic_swap`(`generation_attempt_state.py:485-502`)은 `topics`만 바꾸고 예산을 이월한다. 교체된 새 주제는 첫 `reserve`에서 즉시 소진 → 3일 뒤 다시 `OPERATOR_REQUIRED`.
- 결과: CLAUDE.md가 약속한 "표본 2세션×3일 → 주제 교체 1회" 사다리는 **실제로 2세션 → (3일 공회전) → 교체 → (3일 공회전) → 사람**이다. 사람에게 가는 글의 상당수가 "자동 복구를 다 써서"가 아니라 "자동 복구가 안 돌아서" 온다.

### F2. 본문이 저장되지 않은 실패는 같은 후보를 0원에 재생한다 — **부분 확인**
- 작가 캐시 키 `writer:{context_id}:{topic}:{sha(findings)}`(`tasks.py:2322-2331`)에 세션·날짜·claim이 없다.
- 검수 차단 후보는 DRAFT로 저장되고 지적이 다음 프롬프트에 들어가 키가 바뀌므로 **재생되지 않는다**(원 주장 중 이 부분은 틀림).
- 그러나 시험 제목 ValueError(`content_generation_review.py:202-210`)나 세션 중간 예산 초과처럼 **본문이 저장되지 않는 실패**는 findings가 없어 다음 세션 첫 호출 키가 같고, 같은 날이면 후보·검수·재작성이 전부 캐시 재생되면서 표본 예산·소진 일수만 오른다.

### F3. 모든 사고가 OPEN·기한 없음으로 열린다
- `open_or_touch_incident`(`incidents.py:79-81`)의 기본이 OPEN·`sla_due_at=None`. 처음부터 RETRYING+기한을 다는 곳은 콘텐츠 생성(`generation_incident_control.py:1189-1229`)과 Essence(`tasks.py:3921-3958`)뿐.
- 스윕이 재시도를 맡은 `MONTHLY_SLOT_GENERATION_FAILED`, `MONTHLY_SOV_MEASUREMENT_FAILED`(창 열린 동안), `*_DISPATCH_FAILED`가 첫 실패부터 사람의 일로 집계된다.
- Essence 보류는 OPEN Slack을 enqueue한 **뒤에** RETRYING으로 바꾼다(`tasks.py:4085-4112`). outbox 발송기는 사고 상태를 다시 보지 않는다(`notification_store.py:84-95`) → 자동 재검수 1회차부터 Error 알림.

### F4. 영구 OPEN 9종 — 사람에게도 닫을 수단이 없다
- 해결 규칙 목록(`incident_backlog.py:333-344`)은 10종뿐. OPEN을 받는 상태 전이가 없다(`operations_center_serializers.py:239-276`).
- 닫는 코드 없음: `ESSENCE_MUST_USE_EXCLUDED`, `SITE_BUILD_DISPATCH_FAILED`, `CONTENT_DISPATCH_FAILED`, `MONTHLY_SOV_COHORT_OVER_LIMIT`, `BROKER_UNAVAILABLE`, `UNVERIFIED_ADMIN_ACTOR`, `LEAD_DIAGNOSIS_*`(갈음될 때만), `LEAD_REPORT_BLOCKED`, `LEAD_DELIVERY_ABANDONED`.
- 이것들이 일일 요약(`fleet_heartbeat.py:455-459`)을 영구히 "담당자 확인 필요"로 만든다 → 요약의 신호 가치 0.

### F5. 매월 확정적으로 발생하는 오탐 3건 (측정/보고서)
- 1일: `MONTHLY_SOV_MEASUREMENT_FAILED`가 HIGH·OPEN으로 열리는데(`tasks.py:12317-12324`) 같은 날 공백 요약은 "수동 반복 실행하지 마세요"라고 말한다. 1~7일 catch-up이 맡은 일.
- 7일 09:00 "자동 복구 종료 [조치 필요]"(`monthly_report_gap_notifications.py:223`)는 **8일 00:15 마감**(`celery_app.py:432`, `tasks.py:11236-11240`)이 몇 시간 뒤 자동으로 푸는 공백이다. 주석이 "7일 뒤 배치 없음"이라 잘못 알고 있다.
- `SOV_MONTHLY_COHORT_LIMIT=7 < 병원 9곳`(`config.py:636`) → `MONTHLY_SOV_COHORT_OVER_LIMIT`이 매월 확정, 닫는 코드 없음(F4).

### F6. 사람이 손대도 못 푸는 경로
- 코호트에 못 들어간 달(24일 이후 활성화·SovRecord 없음·질문<10)은 매니페스트가 없어 전달 게이트가 영구 차단(`monthly_report_delivery.py:323-328`). MANUAL 공백 안내는 "리포트 다시 만들기"를 누르라 하지만 다시 만들어도 매니페스트는 생기지 않는다. 주간 측정은 코호트 공백을 보고하지 않아(`tasks.py:10016`) 24일 전엔 알 수도 없다.

### F7. 온보딩의 조용한 정체 — 일정 저장
- 활성화와 Essence 승인(PASS면 사람 없이 APPROVED, `essence_auto_review.py:1799-1857`)은 자동인데, 그다음 **`POST /schedule`은 사람 몫**(`content.py:301`). 요금제는 계약값이 강제되어(`:316-330`) 남는 입력은 게시 요일·시작일뿐.
- `schedule_set`을 읽는 워커·알림이 없다. 병원이 ACTIVE(공개 중)인데 콘텐츠 0편이어도 인시던트·Slack·기한이 없다.
- Essence 보류 시 사람 차례까지 7일(+처리 ERROR 자료 72h 제외 = 최대 10일). 결정론적 출처 충돌(`conflict_notes`)도 7일을 돌고서야 사람에게 간다.

### F8. 자기 감시 단일 장애점
- 운영 Slack 웹훅이 죽으면 감지 결과를 전할 곳이 없다(단일 채널 모드, watchdog도 같은 웹훅 `pipeline_watchdog.py:759`). 이메일 정책은 worker/beat ERROR만 본다.
- Beat `/live`는 부모 프로세스 생존만 본다(`docker-entrypoint.sh:34`에 `--worker-heartbeat` 없음). Redis에 막혀 멎으면 사람이 재기동.
- watchdog 토큰이 비면 Scheduler 호출이 401인데 readiness 검사 항목이 없고(`production_readiness.py:322-334`) Scheduler 실패 경보도 없다.
- RUNNING 고착 인수는 V0·REBUILD_SITE만(`autonomous_recovery.py:156-163`). 9/7 NIGHTLY RUNNING 잔재가 10/8까지 남았다.
- `DISPATCH_TTL_SECONDS` 1h vs `visibility_timeout` 2h 불일치(`dispatch_envelope.py:22`, `celery_app.py:197`)는 "다음 릴리스"로 미뤄진 뒤 배포 5회가 지났다.
- QUEUED 재배달에 횟수 상한이 없다(REBUILD_SITE만 예산) → poison 메시지는 3시간마다 영원히.

### F9. 환경 장애 예산이 날짜로 초기화되지 않는다
- `ENVIRONMENT_ATTEMPT_BUDGET=4`(`generation_retry_policy.py:31, 579`)는 평생 예산. 23·01·04·07시 4번이면 **하룻밤 공급자 장애로 해당 일자 슬롯 전부 소진** → "작업 다시 시도" 일괄 클릭. 같은 성격의 `REVIEW_UNAVAILABLE`은 일일 초기화+백오프라 정책이 어긋난다.
- 분류 안 된 예외(OpenRouter 5xx/429 `APIStatusError`)가 전부 `GENERATION_FAILED`로 떨어진다(`generation_run_control.py:190`).
- 게이트의 `CONTENT_NOT_GENERATED`는 OPERATOR_REQUIRED·기한 없음(`tasks.py:1464-1531`) → 10/8 워커 정지처럼 디스패치가 유실되면 이후 스윕이 전부 건너뛴다.

### F10. 공개 글 사후 검수 실패가 조용하다
- `POST_PUBLISH_REVIEW_FLAGGED`만 QUIET(`incident_types.py:76`). 교정 2회 실패·제목 지적·인용 없음(NEEDS_HUMAN)은 "직접 고치세요"라고 쓰면서 사람의 일에서 빠지고 스윕도 다시 고르지 않는다 → **HARD 의료 안전 지적이 붙은 글이 기한 없이 공개 상태**.
- `AUTO_PUBLISH_HOLD_HOSPITALS`(`config.py:677`)는 발행 due 조건에서 걸러져 감시·게이트·운영센터 어디에도 안 보인다. 켜 두고 잊으면 그 병원은 영구 발행 0건, 생성 비용만 계속.

### F11. 배포가 사람 개입의 최대 원천
- CI는 이미지 빌드만(`ci.yml:377`). 배포는 작업 PC `deploy.sh`. env 정본이 로컬 `.env.production`이라 9/23·10/1 두 번 stale 발견, 배포마다 15~40분 대조.
- "하루 1회·배치 시간대 밖" 규칙은 코드에 없다(10/9 4회 → 10/8 V0 유실의 방아쇠).
- readiness 실패 시 Worker/Beat는 이미 새 리비전인데 롤백은 수동.
- Admin 신규 화면 검증은 매 릴리스 "로그인 필요해 하지 않았다".

### F12. 도입문의 부수효과는 재시도가 없다
- Slack 실패는 `FAILED`로 끝(`leads.py:223-224`, drain은 PENDING/NULL만 집음). SMS는 예외를 삼키고 미설정이면 SKIPPED(`inquiry_sms.py:135-180`). 리드 접수 사실을 AE가 Admin 목록에서만 알 수 있다.

## 3. "사람의 일"의 실체

전수 조사(Admin 행동 약 50개, 사고 유형 35종)를 성격별로 가르면:

| 성격 | 예 | 비율(추정) | 처리 방향 |
|---|---|---|---|
| **진짜 비즈니스 결정** | 계약 등록, 공식 병원 정보, 공개 주소, 일시정지, Essence 출처 충돌 판단, 보고서 전달 | ~20% | 유지. 단 입력 시간을 줄이고(리드 값 이관, 자동 입력 선실행) 기한을 둔다 |
| **기술 공백(자동화 가능)** | 일정 저장, DNS 확인 클릭, Google URL 필수, V0 재실행, 리드 재측정, 복구 확인 클릭, 네이버 재수집, "작업 다시 시도" 10여 종 | ~35% | 스윕이 대신 누른다 |
| **오탐·과잉 사고** | 월초 3건, 영구 OPEN 9종, 기계 소유 일의 Error 알림, `[개발 확인]`의 운영 채널 혼입 | ~30% | 사고 모델 수정 |
| **시스템 수리·배포** | fix 301건/765, 수동 배포, env 대조, TEMPLATE_REFRESH, 재전달 | ~15% (시간 기준으론 최대) | CD·env 정본·자동 TEMPLATE_REFRESH |

## 4. 개선 계획

원칙: (1) 사람 개입을 줄이되 **의료·사실·tenant·공개 권한 검사는 완화하지 않는다**. (2) 각 단계는 배포 1회로 닫히는 크기. (3) 대표 결정이 필요한 것은 분리한다.

### Phase 0 — 수렴하지 않는 것을 수렴시킨다 (S 위주, 배포 1~2회, ~1주)

| # | 변경 | 파일 | 효과 | 위험 | 공수 |
|---|---|---|---|---|---|
| 0-1 | **주제 교체 시 작가·검수 예산을 `prior_topic_spend`로 옮기고 0으로 리셋.** 평생 상한은 `TOPIC_LIMIT(2)×6`으로 유지. `text_http_attempts` 이월도 확인 | `generation_attempt_state.py:485-502`, 통합 테스트에 "교체 뒤 첫 작가 호출이 실제로 구매됨" 추가 | F1. OPERATOR_REQUIRED 본문 실패의 대부분 | 슬롯당 비용 상한 2배(비용 가드가 묶음) | S |
| 0-2 | 작가 캐시 접두어에 `generation_claim_token` 삽입. 같은 claim 재배달은 멱등 재생, 새 세션은 재표본 | `tasks.py:2322-2331` | F2 | 재배달 시 중복 구매 없음(claim 단위 유지) | S |
| 0-3 | 환경 장애 예산을 일일 초기화+백오프로(`DAILY_RESET_ENVIRONMENT_CODES`에 PROVIDER_*·GENERATION_FAILED), `APIStatusError` 5xx/429 → PROVIDER_UNAVAILABLE 분류, 3일 상한 | `generation_retry_policy.py:34`, `generation_run_control.py:172-190` | F9 | 영구 오류 매일 재시도(3일 상한) | S |
| 0-4 | 게이트 `CONTENT_NOT_GENERATED`를 복구 가능 증상(ENV)으로 재분류 | `generation_retry_policy.py:200-210`, `tasks.py:1531` | F9. 인프라 사고 뒤 일괄 클릭 제거 | 낮음 | S |
| 0-5 | 주제 교체 후보 N개(5) 순회. incompatible·similar·no-curated는 제외 집합에 넣고 재선택 | `topic_swap_fallback.py:367-386`, `content_target_planner._choose_target` | 교체 실패 반복 제거 | 낮음 | S |
| 0-6 | 인용 없는 모델 단정 HARD도 `_escalate_exhausted_correction`으로(교정 이력 조건 제거). BODY_REPAIR 소진·operator_decides 참고자료도 교체 계단에 포함(수기 목록 문서 있는 의료 주제로 한정) | `tasks.py:1061-1069`, `topic_swap_fallback.py:248-262`, `generation_retry_policy.py:594` | INPUT_CHANGE_REQUIRED·MISSING_REFERENCES 대부분 | 진료비 질문 노출 기회 감소 | M |
| 0-7 | `DISPATCH_TTL_SECONDS` 6h로 상향 + 테스트 "서명 수명 ≥ visibility_timeout" 고정 | `dispatch_envelope.py:22` | F8 | 낮음(수락 상한 이미 6h) | S |
| 0-8 | QUEUED 재배달에 유형별 예산(3회), 소진 시 원인별 사고 1건. V0·보고서 유예 3h→45m | `autonomous_recovery.py:_redispatch_operation_run` | poison 무한 재배달 차단, V0 지연 단축 | 낮음 | S |
| 0-9 | RUNNING lease 만료 인수를 정책 표의 전 유형으로 확대(멱등성은 정책 단위 확인). 오래된 RUNNING 잔재는 FAILED로 정리+사고 1건 | `autonomous_recovery.py:156-163` | F8 RUNNING 고착 수동 정리 0 | 비멱등 재실행 비용 | M |

**검증 기준**: 운영 DB에서 `budget.acquisitions`의 writer RESULT 수 분포와 주제 교체 뒤 첫 시도 결과 코드를 배포 전후로 비교. 교체 뒤 `WRITER_RESULTS_EXHAUSTED`가 0건이어야 한다.

### Phase 1 — 사고 모델을 "사람의 일"에 맞춘다 (~2주)

| # | 변경 | 파일 | 효과 | 위험 | 공수 |
|---|---|---|---|---|---|
| 1-1 | **사고 유형 소유 정책 레지스트리** `{audience, quiet, initial_state, deadline_fn, resolver}`. `open_or_touch_incident`가 스윕 소유 유형을 RETRYING+기한으로 연다. 누락 시 기본 OPEN(안전) | `incident_types.py`, `incidents.py:52-140` | F3. 잘못 올라오는 일 ~30%↓ | 레지스트리 누락 | M |
| 1-2 | 해결 규칙 추가: LEAD_*(진단 READY·이후 성공), MUST_USE(새 승인본), NAVER(같은 URL 저장), BROKER(실행 QUEUED 이상), *_DISPATCH_FAILED(대상 완료 또는 24h) | `incident_backlog.py:333-344` | F4 영구 OPEN 해소 | 성급한 닫힘(테스트) | M |
| 1-3 | `sla_due_at` 없는 RETRYING에 기본 기한 24h 강제 | `operator_action.py:19`, `fleet_heartbeat.py:349-353` | 주인 없는 "복구 중" 제거 | 사람 일 일시 증가 | S |
| 1-4 | outbox 발송 시 연결 사고가 RETRYING(기한 안)·ACKNOWLEDGED면 INCIDENT_OPEN 건너뜀. Essence는 RETRYING으로 바로 열기 | `notification_store.py:84-140`, `tasks.py:4085-4112` | 기계 소유 일의 Error 알림 제거 | 낮음 | S |
| 1-5 | `autonomous_recovery.reconcile`이 "RETRYING+연결 실행 SUCCEEDED"와 "RECOVERED 미확인"을 자동 닫기 | `autonomous_recovery.py:170`, `lead_recovery_incidents.py`, `naver_handoff_incidents.py`, `topic_swap_fallback.py:523` | 복구 확인·문제 확인 클릭 제거 | 낮음(서버가 이미 같은 조건 검사) | S |
| 1-6 | 월초 오탐 3건: ① 1일 사고를 `mark_retrying`+기한=복구 마감으로 ② 공백 요약을 1~8일로 늘리고 종료 판정을 8일 마감 뒤 전달 게이트 기준으로 ③ `SOV_MONTHLY_COHORT_LIMIT`을 비용 한도에서 계산 | `weekly_sov_incident_control.py:187-280`, `monthly_report_gap_notifications.py:223-246`, `celery_app.py:438`, `config.py:636` | F5 | 낮음 | S |
| 1-7 | 공백 판정을 전달 게이트 기준으로, `manifest_missing` 별도 상태. 안내를 "이번 달은 측정 없어 전달 불가, 다음 달부터 자동"으로. 주간 실행에서도 코호트 공백 보고 + SovRecord 없는 ACTIVE 병원 즉시 1회 측정 | `monthly_report_gap_notifications.py:80-97`, `tasks.py:10016`, `sov_tracking_set.py:218` | F6 | 비용 소폭 | M |
| 1-8 | 대상 재분류: UNVERIFIED_ADMIN_ACTOR·COHORT_OVER_LIMIT·COST_GUARD·소진 후 SITE_BUILD → 개발. 사고→상태/로그 강등: SOV_HIGH_PRIORITY_CAP, WEEKLY_SOV_FAILED, NOTIFICATION_DELIVERY_UNKNOWN, SOURCE_PROCESSING_FAILED | `incident_types.py:56-70` 등 | AE 큐에서 못 고치는 일 제거 | 개발 채널 모니터링 필요 | S-M |
| 1-9 | V0_REPORT_FAILED·리드 측정/보고서 실패에 예산 있는 자동 재실행(6h×3), 소진 뒤에만 OPEN. 도입문의 Slack을 PENDING으로 저장해 drain이 보내고, SMS는 재시도 1회+실패 시 사고 | `autonomous_recovery.py`, `lead_diagnosis_tasks.py`, `leads.py:213-229` | "다시 측정" 클릭·리드 유실 제거 | 비용 가드 연동 | M |
| 1-10 | 사후 검수 NEEDS_HUMAN(교정 실패 HARD 의료 안전)은 비조용 유형으로 사람의 일에. UNCERTAIN만 남은 글은 N일 뒤 상위 모델 재검수로 자동 닫기. `AUTO_PUBLISH_HOLD_HOSPITALS`를 readiness/일일 Report에 노출 | `incident_types.py:76`, `post_publish_ai_review.py:264-313`, readiness | F10 | 알림 소폭 증가(안전 우선) | S |

### Phase 2 — 온보딩·자기 감시·배포 (~3주)

| # | 변경 | 파일 | 효과 | 위험 | 공수 |
|---|---|---|---|---|---|
| 2-1 | **Essence 첫 자동 승인 시 일정 자동 생성**(`schedule_set=False`면 계약 요금제+기본 요일, `active_from=max(내일, contract_effective_at)`). 사람은 나중에 교체 가능 | `content.py:301-470`→서비스 추출, `auto_review_essence_snapshot` AUTO_APPROVED 분기 | F7. 온보딩의 사람 단계 1개 제거 | 요일 불일치(교체 가능) | M |
| 2-2 | 온보딩 경과 이벤트: 인수 3일 프로필 미완료, Essence current 24h 일정 미설정, ACTIVE 7일 발행 0편, 필수 자료 0건+fetch ERROR 24h → 원인별 사고 1건 | `onboarding_events.py`, `milestone_onboarding_projection.py`, `tasks.py:3544-3563` | 조용한 정체 표면화 | 소음(원인 묶음으로 억제) | S-M |
| 2-3 | Essence 보류 분류: `conflict_notes`·주입은 즉시 사람(OPEN), 확률적 지적은 1h/4h/12h. ERROR 자료 제외 72h→6h(또는 다른 PROCESSED 있으면 즉시) | `essence_auto_review.py:68-76, 518-543, 593-599` | 사람 차례 7일→~17h | 합성 비용 소폭 | S |
| 2-4 | 계약 등록 시 리드의 진료과·지역·키워드·전화 이관, 등록 직후 자동 입력을 백그라운드로 선실행. 활성화 게이트에서 Google URL·원장 약력/철학을 보강 항목으로 | `handoffs.py:448`, `hospital_profile_autofill.py`, `hospital_lifecycle.py:130-213` | 프로필 입력 수일→확인만 | 게이트 완화는 **대표 결정** | M |
| 2-5 | PENDING_DOMAIN+aeo_domain 병원 15분 DNS 폴링 → 자동 활성화. 또는 기본 주소로 먼저 활성화 후 canonical 전환 | `tasks.py:12661`, `domain_verification.py:79`, `hospital_activation.py:154-167` | DNS 확인 클릭 제거, 도메인 대기 중 글 0편 해소 | canonical 전환 처리 | M |
| 2-6 | **자기 감시 2차 경로**: 운영 채널 사고·watchdog 실패를 이메일(Resend)/GCP 경보로. readiness에 `pipeline_watchdog_token_configured`. Scheduler 실패 로그 경보 | `notification_channel_health.py`, `production_readiness.py`, `monitoring.tf` | F8 Slack 단일 장애점 | 낮음 | S |
| 2-7 | Beat liveness: tick 신호로 파일 갱신, `/live`에서 RedBeat 락 소유·엔트리 신선도 확인 | `docker-entrypoint.sh:34`, `health_server.py` | Beat 멈춤 자동 복구 | 낮음 | S |
| 2-8 | 비용 가드 fail-open 첫 발생을 ERROR 1회+사고 1건. 달러 기준 일 상한 보조. spool 잘림 시 사고 | `cost_guard.py:464-471, 595`, `provider_usage.py:262` | 조용한 비용 노출 | 낮음 | M |
| 2-9 | **배포 코드화 1단계**: env를 현재 서비스 env+Secret Manager에서 조립(diff≠0이면 중단), 배치 시간대 차단·하루 1회 잠금(`--override-reason`), readiness 실패 시 자동 롤백, API 배포 뒤 release smoke, `--unpin` | `scripts/deploy.sh` | env 대조·수동 롤백·규칙 위반 제거 | env 정본 전환 | M |
| 2-10 | **배포 코드화 2단계**: GitHub WIF로 main 태그 → `deploy.yml`. 추가형 마이그레이션 검사 | `.github/workflows/deploy.yml` | 작업 PC 의존 제거 | 중간 | L |
| 2-11 | TEMPLATE_REFRESH 자동화: 템플릿 버전 변경 시 PASS 병원만 배치 갱신. 숫자 같으면 "다음 전달 때 반영"으로 재전달 요구 없음. 전달 준비 후 5일 미전달이면 병원·월당 사고 1건 | `monthly_template_refresh.py`, beat 신설, `milestone_monthly_projection.py` | 회당 1~2h×9곳 수동 실행·재전달 제거 | 낮음 | M |
| 2-12 | Admin 인증 후 E2E(Playwright) 최소 세트: 계약 등록·일정·전달 기록·예외 카드. CI에 포함 | `admin/e2e/` | 매 릴리스 "검증 안 했다" 제거 | 테스트 유지비 | M |

### Phase 3 — 대표 결정이 필요한 구조 변경

| 항목 | 결정할 것 | 기술 선행 조건 |
|---|---|---|
| **월간 보고서 자동 전달(opt-in)** | 제품·법무 결정. 자동 전달 대상은 COMPLETE/LIMITED만, UNAVAILABLE은 AE | `hospital_report_recipients`(이메일·동의 시각/버전), 병원별 `auto_delivery_enabled`(기본 off), outbox 의도 커밋→Resend 서명 링크→`DELIVERED(actor=system)` append-only. 기존 mailer 멱등 패턴 재사용. **L** |
| **활성화 게이트 축소** | Google URL·원장 약력/철학을 필수에서 제외할지 | 2-4 |
| **질문 단위 비교** | 사전 수정 달에 9곳 전부 비교 불가 → 바뀐 질문만 제외 | 측정 계약 변경, **L** |
| **진료비·병원 선택 주제** | 공신력 문서가 없는 주제를 생성 단계에서 질환·시술 각도로 강제할지 | 0-6 확장 |
| **단일 Slack 채널 유지 여부** | 2차 경로(이메일)만으로 충분한지 | 2-6 |

## 5. 성공 지표 (배포 전후 비교)

| 지표 | 측정 방법 | 현재(추정) | 목표 |
|---|---|---|---|
| 일일 요약의 "사람의 일" 건수 | `is_operator_todo` 집계 | 영구 OPEN 때문에 항상 >0 | 평상시 0~2 |
| Slack 알림 중 조치 필요 비율 | outbox 14일 종류별 | 25~35% | ≥70% |
| 7일 넘게 OPEN인 사고 | `state='OPEN'` 나이 분포 | 9종 영구 | 0 (전 유형에 resolver) |
| 주제 교체 뒤 첫 시도 결과 | `budget.acquisitions` | `WRITER_RESULTS_EXHAUSTED` | 실제 작가 호출 |
| OPERATOR_REQUIRED 글/월 | generation 사고 집계 | — | -70% |
| 월초(1~8일) 오탐 사고 | 유형별 | 매월 3종 확정 | 0 |
| 온보딩 리드타임(계약→첫 발행) | 병원별 타임스탬프 | 사람 대기 2곳, 상한 없음 | 프로필 완료+2일 |
| 배포당 사람 시간 | 릴리스 기록 | 1~2h + env 대조 | <20분(승인+관찰) |
| 자기 장애 감지 경로 | 운영 Slack 외 | 0 | 이메일/GCP 1개 이상 |

## 6. 즉시 처리할 미결 (코드 변경 작음)
릴리스 기록에서 열려 있는 것: 시험 리드 `[테스트]`·`[테스트2]`와 시험 알림 정리, 노출된 도입문의 웹훅 교체, `operation_runs`의 9/7 NIGHTLY RUNNING 잔재 정리, legacy frontend IAM 회수, 10/9 리팩터링(`b4fd0c2d`, head 0086) 릴리스 문서 작성, CLAUDE.md "PROPOSED" 절과 10/10 배포 상태 불일치 정정, 런북 247-251행의 옛 채널 모델 문구 수정, `/ai-diagnosis` 유지 여부·처리방침 버전 결정.

## 7. 한계
- 전부 정적 코드 감사다. 운영 DB의 실제 OPEN 사고 분포·알림량·정체 병원 수는 조회하지 않았다. Phase 1 우선순위 확정 전에 `notification_outbox` 14일 집계와 `incidents` OPEN 나이 분포를 한 번 뽑는 것을 권한다.
- F2는 "검수 차단 후보 재생"이라는 원 주장은 틀렸고 "본문 미저장 실패 재생"으로 범위가 좁다.
- 알림 비율·시간 비용은 추정치다.
