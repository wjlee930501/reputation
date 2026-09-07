# Re:putation 목적 적합성·자율 운영·비용 효율 검수와 개선 계획

문서 버전: **1.0** · 조사·작성일: **2026-09-07 (Asia/Seoul)**
조사 HEAD: **`54efc07c6d11f78f22bb5eb8d3a2244cf6c62b8b`**
애플리케이션 기준: **`345a6420998bcba21169519cf5ad77600cbfa94b`**
상태: **코드 근거에 기반한 개선 제안. 아래 미해결 항목을 구현 완료로 해석하지 않는다.**

## 1. 검수 결론

현재 시스템에는 근거 자료에서 콘텐츠를 만들고, 자동 발행하고, AI 노출을 측정하여 월간 보고로 연결하는 핵심 흐름이 있다. 정상 생성·발행의 무알림, Essence 자동 승인, 실패 부분 복구, 월간 측정 manifest, 원장용 PDF 검증, outbox 등 자율 운영에 필요한 기반도 상당 부분 구현되어 있다.

그러나 **업무가 정상 종료되었다는 상태와 제품 목적을 달성했다는 상태가 일치하지 않는 경로**가 남아 있다. 승인된 치료별 설명이 작성 프롬프트에서 빠지고, 독립 검수의 수정 요청이 발행 판단에 보존되지 않으며, 반복 표본 일부만 확보한 측정이 COMPLETE가 될 수 있다. 예산·재시도·화면의 사람 업무 판정도 진입 경로마다 다르다.

따라서 다음 단계는 다음 순서가 적절하다.

1. 사실·표본·최신 버전·비용 통제의 계약을 먼저 일치시킨다.
2. 시스템이 복구 가능한 상황을 스스로 끝내고, 화면도 그 상태를 정확하게 보여주게 한다.
3. 이미 구매한 답변·이미지를 재사용하고 실제 호출·캐시·재시도 비용을 계측한다.
4. 그 결과를 바탕으로 프롬프트, 모델, 동시성, 모듈 경계를 조정한다.

현재 자료로 **“완전 자율 운영”이나 “토큰 최적화 완료”라고 판정할 수는 없다.** 반대로 모든 안전 검사를 사람 승인으로 바꾸거나, 비용 절감을 위해 근거·반복 측정을 임의로 줄이는 것도 제품 목적에 맞지 않는다.

## 2. 조사 범위와 증거 수준

현재 구조의 정본은 [시스템 구조](../architecture/system-map.md)다. 이 문서는 그 구조가 목적에 맞게 작동하는지 검수한 후속 문서다.

- 커밋 이력: 최근 구조·월간 계약·콘텐츠·알림 변경 및 PR #79의 앱 수정, PR #80의 문서 정비를 현재 코드와 대조했다. 과거 리뷰의 이미 해결된 내용을 새 결함으로 재등록하지 않았다.
- 조사 대상: Backend API·모델·서비스·Celery/Beat, Admin의 주요 운영 흐름, Site 공개·검색 표면, 비용·복구·알림 및 배포 구성.
- 증거: 실제 함수·SQL 조건·상태 전이·프롬프트·호출 경계·관련 테스트를 읽었다. 일부 콘텐츠 계약은 네트워크를 차단한 순수 함수 probe로 재현했다.
- 이번 감사에서 유료 모델 호출, 고객 메시지, 운영 DB 변경, 애플리케이션 수정은 수행하지 않았다. 전체 테스트 통과 여부는 별도 [직전 릴리스 검증](../releases/2026-09-07-345a642.md)의 기록이며, 아래 신규 시나리오의 통과를 뜻하지 않는다.
- **확인**은 코드 또는 로컬 probe로 확인한 동작이다. 콘텐츠 probe는 SimpleNamespace fixture를 사용하는 순수 함수·payload 시험이며, 실제 DB의 자료 철회→새 승인→발행 전체 사고 재현은 아니다. **영향**은 그 조건에서 발생 가능한 결과이며, 실제 운영 발생 건수는 별도 관측이 없으면 미확인이다. 우선순위와 설계는 **제안**이다.
- 현재 청구액, cache hit, 429 빈도, 작업 대기 p95, 사람의 처리 시간, 검색 노출 상승의 인과 효과는 측정하지 않았다. 금액·절감률·성과 수치를 만들지 않았다.

P1은 사실성·상태 무결성·비용 통제 등 우선 수정할 계약, P2는 복구·사용성·효율의 구체적 결함, P3는 관측 후 확장할 구조 개선이다. P1이라는 분류가 현재 운영 장애나 임상 위해 발생을 입증하는 것은 아니다.

## 3. 기능별 목적과 현재 판정

| 기능 | 달성해야 할 목적 | 현재 구현의 장점 | 남은 검수 사항 |
|---|---|---|---|
| 계약·프로파일·공개 활성화 | 실제 병원 정보와 서비스 범위 확정 | 공개 활성화와 콘텐츠 준비 게이트 분리 | 사람이 결정할 사실과 자동 처리 상태를 UI에서 분리 |
| 자료 수집·근거 추출 | 전체 공식 자료를 추적 가능한 근거로 변환 | 동일 hash 처리 재사용, Naver 수집, 자료 상태 | 일괄 50개 이후 정체, 예산 우회, 긴 문서 coverage |
| Essence | 근거에 맞는 운영 기준을 자동 승인·갱신 | snapshot 신선도, 독립 검수, 자동 승인, 수동 편집 보호 | 수동 초안 유도, 80개 근거 제한, 치료 서사 소비 스키마 |
| 일정·노출 타깃 | 계약 월 편수와 환자 질문을 연결 | 7유형·요금제 배분, 계약 월과 실제 발행일 분리 | 노출 보완 액션의 잘못된 결함·완료 판정 |
| 본문 생성 | 근거 있는 환자 답변과 병원 고유 정보 제공 | 유형별 prompt, 빠른 독립 검수, 금지 표현·인용·FAQ 검사 | 승인 brief 신선도, hard finding 처리, 검수 범위 |
| 이미지 | 주제에 맞는 안전한 대표 이미지 확보 | 멀티모달 검수, 미검증 이미지 발행 차단 | 업로드 실패 시 재구매, 생성 버전과 이미지 연결 |
| 발행·복구 | 예정 시각에 최신 검증본 공개, 실패 자동 복구 | 저장 상태 가드, 공개 API 재검증, 부분 복구 | 생성 writeback fencing, 잠금 후 발행일 재검증, 환경 회복 재개 |
| SoV·V0 | 불확실성을 보존한 초기 기준과 반복 관측 | 실패를 0으로 만들지 않음, 판정 prefilter, 정책 snapshot | 반복 슬롯 완결성, V0 확정 표본, 전달 artifact 불일치 |
| 월간 리포트 | 계약 실적과 비교 가능한 AI 노출을 정직하게 보고 | matched cohort, k/n, 원장용 PDF 검증·자동 복구 | cell coverage와 반복 적정성 분리, 판정만 재시도 |
| 무료 진단 | 신청 후 측정·PDF·이메일 자율 완료 | 플랫폼별 확정 하한, 답변 캐시, 중복 전달 방어 | 캐시 경로 비용 귀속·킬스위치, 동시 miss, checkpoint |
| SEO/GEO/AEO | 공개 정보가 읽히고 질문의 근거 있는 답으로 쓰이게 함 | host별 canonical·sitemap, 가시 본문과 schema 정합 | upstream 장애 표현, IndexNow 재전송, 효과 관측 |
| 운영 센터·알림 | 시스템이 처리 중인 일을 사람에게 떠넘기지 않음 | 정상 발행 무알림, incident 중복 억제, 복구 자동 종결 | 08시 일괄 사람 업무화, 잘못된 CTA, 상세 링크 문맥 소실 |
| 비용·인프라 | 안전한 예산 내 지속 운영, 병목 관측 | Redis 원자 예약, KST 기준, DB 연결 예산 검사 | 실제 비용 원장, 예약 receipt, 공유 worker pool의 지연 |

### 콘텐츠 7유형의 검수 기준

| 유형 | 현재 반영된 목적 | 다음 개선에서 반드시 확인할 것 |
|---|---|---|
| FAQ | 질문과 직접 답변, FAQ 필드 필수 | 본문·짧은 답·구조화 데이터의 주장 일치 |
| DISEASE | 원인·증상·진단·치료 안내, 참고 근거 | 근거 없는 진단 단정·결과 보장 차단, 전체 본문 검수 |
| TREATMENT | 시술 과정·회복·주의사항 | 승인된 `patient_language`·`cautions`가 실제 prompt에 도달 |
| COLUMN | 원장 전문성과 진료 철학 | 최신 brief와 원장 정보만 사용, 철회된 주장 재유입 방지 |
| HEALTH | 계절·생활습관·예방 정보 | 일반 정보와 병원 고유 사실 구분, 구체 인용 유지 |
| LOCAL | 지역 환자의 질의에 답변 | 검색 타깃 지역을 실제 주소나 제공 진료로 오인하지 않음 |
| NOTICE | 확인된 병원 소식, 구체 소식이 없을 때 상시 이용 안내 fallback | 확인되지 않은 행사·날짜·장비를 만들지 않는 기존 fallback을 유지하고, 병원 고유 사실은 A01의 hard finding으로 검증 |

유형별 프롬프트·규칙의 존재는 확인했지만, 실운영 생성물의 임상적 정확성과 독창성을 유형별 표본으로 새로 평가한 것은 아니다. [content_engine.py](../../backend/app/services/content_engine.py), [content_publication.py](../../backend/app/services/content_publication.py), [schema.ts](../../site/lib/schema.ts)를 함께 보아야 한다.

## 4. 먼저 보존할 좋은 구현

- 새로운 자료가 있으면 신규 생성·발행은 최신 전체 자료에 대한 승인까지 대기한다. 기존 공개 글은 온전한 승인 baseline을 사용하는 동안 유지한다. 이를 한 가지 stale 플래그로 합쳐 전부 공개 중지하지 않는다.
- 인용·FAQ·금지 표현·이미지 검증과 공개 API 안전 검사를 유지한다. 정상 콘텐츠마다 수동 승인 버튼을 추가하지 않는다.
- 본문이 성공하고 이미지만 실패하면 본문을 유지하는 부분 복구, Essence의 동일 snapshot 재합성 방지, 완료된 월간 cell checkpoint를 유지한다.
- 월간 집계는 확정 반복 전체의 k/n을 사용하고, 비교 조건이 바뀌면 비교를 제한한다. 계약 월 충족과 실제 발행일을 분리하며 과거 발행일을 조작하지 않는다.
- 정상 생성·발행은 Slack을 보내지 않는다. 차단은 묶고 복구는 incident 상태에 반영한다. 개발 채널이 없다고 운영자 채널로 기술 오류를 무차별 우회하지 않는다.
- outbox의 lease/version 확인과 전달 결과 불명 상태를 보존한다. 불명 상태를 무조건 재발송해서 중복 고객 이메일을 만들지 않는다.

근거: [essence_readiness.py](../../backend/app/services/essence_readiness.py), [monthly_sov.py](../../backend/app/services/monthly_sov.py), [notification_delivery.py](../../backend/app/services/notification_delivery.py), [알림 정책](../ops/slack-notification-policy.md).

## 5. 우선 개선 항목

### A01 · P1 · 독립 콘텐츠 검수의 의미와 검수 범위 일치

**확인:** 마지막 후보의 AI 검수 결과가 `REVISE`·blocking이어도 결정론적 발행 재검사가 ALIGNED를 만들고 blocking을 덮어쓸 수 있다. 순수 함수 probe에서 `ai_status=REVISE`만 남고 `publishable=True`가 되었다. 결정론적 필터가 잡지 못하는 병원 고유 사실에는 이 차이가 중요하다. 또한 검수는 본문 앞 6,000자만 보지만 작성 길이 검사는 공백 제외 기준이라 검사를 통과하는 원고의 뒷부분이 빠질 수 있다.

근거: [content_publication.py](../../backend/app/services/content_publication.py), [content_ai_review.py:153](../../backend/app/services/content_ai_review.py#L153), [tasks.py](../../backend/app/workers/tasks.py).

**개선:** 근거 없는 병원 사실·의료적 위험 주장, 문체 개선, 검수 불확실성을 별도로 분류한 typed review 결과를 만든다. 확신도 숫자만으로 hard/soft를 나누지 않는다. 전자는 근거를 보강하거나 해당 주장을 제거하는 bounded 자동 수정 뒤 재검사하고, 해결되지 않으면 공개를 막는다. 문체 지적이나 공급자 장애를 모두 영구 수동 승인으로 바꾸지 않는다. 검수 coverage와 후보 hash를 기록하여 전체 공개본이 검토되었는지 확인한다.

**합격:** hard fact finding은 마지막 재작성 후에도 남으면 발행 불가, soft finding만 있는 안전한 글은 정책에 따라 자동 진행. 6,000자 이후에 넣은 위험 주장도 검수 대상이며, 후보 수정 후 이전 판정을 재사용하지 않는다. 실제 의료 정확성 평가는 별도 전문가 기준 표본으로 수행한다.

### A02 · P1 · 승인 Essence·brief·치료 서사의 데이터 계약 연결

**확인:** 새 Essence 승인 시 기존 본문을 결정론적으로 재검사하고 `content_philosophy_id`를 새 ID로 바꾼다. 생성 근거와 최신 재검사 근거가 같은 필드에 들어가므로, 철회된 자료에 의존하던 주장도 결정론적 검사를 통과하면 기존 본문을 유지할 수 있다. 승인 brief를 새 Essence ID와 비교하지 않고 재사용할 수 있다. 과거 운영 기준에서 철회된 문구가 오래된 brief에 남으면 새 생성 prompt에 다시 들어갈 수 있다. LLM 합성의 치료 서사는 `patient_language[]`, `cautions[]`인데 writer는 `angle`을 읽는다. brief에 보관한 상세 정보도 렌더 과정에서 빠지고 reviewer payload에는 치료 서사가 없다. 순수 함수 probe로 두 고유 표식이 저장 brief에는 있지만 writer/reviewer 입력에는 없는 것을 확인했다.

근거: [essence_auto_review.py:861](../../backend/app/services/essence_auto_review.py#L861), [content_target_planner.py:54](../../backend/app/services/content_target_planner.py#L54), [essence_engine.py:920](../../backend/app/services/essence_engine.py#L920), [content_engine.py:432](../../backend/app/services/content_engine.py#L432), [content_brief.py](../../backend/app/services/content_brief.py), [content_ai_review.py:136](../../backend/app/services/content_ai_review.py#L136).

**개선:** 생성 당시 근거와 최근 재검사 근거를 분리하고 주장→근거 의존성을 보존하여 철회·변경의 영향을 받은 원고만 재평가한다. 새 자료가 추가되었어도 기존 근거가 온전한 글을 일괄 재생성하지 않는다. Essence와 brief에 공유하는 버전 명시 스키마를 두고, brief의 Essence ID·자료 snapshot·타깃 revision을 검사한다. 자동 생성 brief는 stale 시 자동 재작성하고, 실제 사람이 편집한 내용은 변경을 보존하면서 충돌 부분만 해결한다. 선택 치료에 필요한 환자 설명·주의사항·근거 ID를 writer와 reviewer에 동일하게 전달한다.

**합격:** Essence v1→v2에서 철회 문구가 재사용되지 않음. LLM 형태와 결정론적 fallback 형태 모두 같은 의미로 정규화됨. 진료 주의사항이 저장→brief→writer→reviewer까지 보존됨.

### A03 · P1 · 생성 결과 저장과 발행의 마지막 조건 재검증

**확인:** 본문 writeback은 ID와 DRAFT/REJECTED/READY 상태만 검사한다. 이미지 writeback은 여기에 제목만 비교한다. claim 소유자·버전이나 편집 revision은 조건에 없다. 제목·본문을 바꾸는 Admin 경로도 기존 이미지 검증 시각을 무효화하지 않아, 주제가 바뀌어도 이전 이미지의 의미 검증을 사용할 수 있다. `_auto_publish_one()`은 행 잠금 후 병원 ACTIVE/LIVE를 재확인하지만, 최초 후보 선택 뒤 변경된 `scheduled_date`는 다시 확인하지 않는다. 기존 테스트는 PUBLISHED/CANCELLED 보호를 검증하므로 그 보호는 이미 존재한다.

근거: [nightly_generation_batch.py:23](../../backend/app/workers/nightly_generation_batch.py#L23), [동일 파일:45](../../backend/app/workers/nightly_generation_batch.py#L45), [tasks.py:3388](../../backend/app/workers/tasks.py#L3388), [content.py:546](../../backend/app/api/admin/content.py#L546), [기존 통합 테스트](../../backend/tests/integration/test_generation_write_back_guard.py).

**영향:** 느린 옛 생성이 더 최근 DRAFT 편집을 덮거나, 오늘 후보로 뽑은 글을 내일로 옮긴 직후 조기 발행할 수 있는 경합 조건이다. 운영 발생 사례를 확인한 것은 아니다.

**개선·합격:** claim token과 input revision을 조건부 저장에 포함한다. 이미지 검증은 이미지 hash·주제 hash·정책 버전에 묶는다. 주제가 바뀌면 기존 이미지부터 재검수하고 부적합할 때만 새로 생성한다. 오탈자마다 새 이미지를 구매하지 않는다. 발행 잠금 안에서 현재 KST 발행 자격을 다시 검사한다. A 생성→B 편집/claim 재획득→A 완료에서 A는 저장 0건, 후보 선택→내일로 이동→발행에서 공개 0건이어야 한다.

### A04 · P1 · 모든 유료 호출 경로에 같은 비용·킬스위치 계약 적용

**확인:** 단건 자료 처리 API는 예산 검사 후 호출하지만, 일괄 처리와 Naver가 사용하는 worker는 사용량 기록만 수행한다. 기록 함수는 차단하지 않는다. 무료 진단은 모든 답변이 캐시이면 answer 예약이 0이고, 예산 함수가 kill switch보다 먼저 허용한다. 이름 prefilter를 통과하면 그 뒤 유료 판정은 발생한다. 캐시 경로는 `fetch_answer()`가 설정하던 leadgen 문맥도 건너뛰므로 일반적인 새 실행 문맥에서 sov actual에 귀속될 수 있다.

근거: [essence.py:658](../../backend/app/api/admin/essence.py#L658), [tasks.py:1258](../../backend/app/workers/tasks.py#L1258), [naver_sync.py:100](../../backend/app/workers/naver_sync.py#L100), [lead_diagnosis_engine.py:139](../../backend/app/services/lead_diagnosis_engine.py#L139), [sov_engine.py:1288](../../backend/app/services/sov_engine.py#L1288), [cost_guard.py:288](../../backend/app/services/cost_guard.py#L288).

**개선:** 공통 실제 실행 경계에서 유료 호출 허용과 예약을 처리한다. cached/live 모두 업무 비용 문맥을 설정·복원한다. 답변·판정·이미지와 retry를 분리하며 단건 API와 worker의 이중 예약을 피한다. 예약 상한과 실제 금액 상한은 구별하고 Redis fail-open 정책은 명시적으로 유지·검토한다.

**합격:** `COST_GUARD_ENABLED=true`이고 Redis가 정상인 조건에서 단건·일괄·Naver·전부 캐시인 무료 진단 모두 kill switch 시 외부 호출 0회. Redis 장애 시 동작은 별도 fail-open 정책 시험으로 확인한다. 중복 메시지로 중복 예약 없음. 캐시 판정도 leadgen 귀속. 예산 회복 시 A05의 대기 작업이 자동 재개됨.

### A05 · P1 · 환경 회복에 따라 자동 재시도 재개

**확인:** 콘텐츠 재시도 중복 억제는 콘텐츠·Essence 등 입력 fingerprint에 의존한다. COST_BLOCKED/PROVIDER_TIMEOUT처럼 입력이 그대로여도 예산이나 공급자 상태가 회복되는 오류를 같은 방식으로 억제할 수 있다. 별도 `next_retry_at=현재+15분` 기록도 있지만 해당 필드를 읽어 실행시키는 scheduler는 확인되지 않았다. 실제 예약 실행과 표시용 시각을 혼동하면 안 된다.

근거: [tasks.py:330](../../backend/app/workers/tasks.py#L330), [tasks.py:372](../../backend/app/workers/tasks.py#L372), [tasks.py:3065](../../backend/app/workers/tasks.py#L3065), [generation_run_control.py](../../backend/app/workers/generation_run_control.py), [generation_batch_run.py](../../backend/app/workers/generation_batch_run.py), [tasks.py](../../backend/app/workers/tasks.py), [autonomous_recovery.py](../../backend/app/workers/autonomous_recovery.py).

**개선:** 입력 변경이 필요한 오류, 시간·예산·공급자 회복으로 해소되는 오류, 운영자 판단이 필요한 오류를 구분한다. 시간·예산·공급자 회복형 오류는 fingerprint만으로 영구 차단하지 않고 실제 실행 주체가 소비하는 due time·attempt budget·lease를 갖는다. 예산 경계와 provider backoff에 맞춰 재개하고 같은 차단을 반복 알리지 않는다.

**합격:** 입력 변경 없이 429/timeout 회복 또는 KST 예산 reset 후 성공. 영구 금지 표현은 무한 재작성되지 않음. 표시된 다음 재시도와 실제 due 작업 일치. 종료된 작업에 대한 중복 실행·알림 없음.

### A06 · P1 · 월간 반복 표본과 V0 유효 진단의 완료 기준 명시

**확인:** 월간 cell은 확정 판정 하나로 SUCCESS가 되고, 복구는 FAILED cell만 대상으로 한다. 전달 coverage도 cell 수이므로 모든 cell이 1/5만 확정되어도 COMPLETE가 가능하다. 실제 SoV는 확정 반복 전체의 k/n이므로 대표 한 건만 계산하는 버그는 아니다. 전월과 표본 형태가 달라지면 비교에서 제외되는 보호도 있다. 반복 부족을 별도로 차단하는 후속 게이트는 없으므로 나머지 내용·Essence·artifact 조건이 충족되면 원장 PDF 다운로드와 전달 기록도 가능하다. 다만 현재 PDF는 실제 반복 수의 최소·최대와 오차를 표시하므로 부족한 표본을 항상 5회로 허위 표시하는 문제는 아니다.

V0는 확정 표본이 아닌 `measurement_status=SUCCESS` 수가 0보다 큰지만 확인한다. 전부 AMBIGUOUS인 경우 SoV가 None이어도 `v0_report_done=True`와 다음 빌드 단계로 진행할 수 있다. V0 PDF에는 플랫폼별 실패·스킵 coverage가 전달되지 않는다. 전부 모호한 V0는 PDF에 측정 데이터 없음으로 표시되지만 PDF 경로가 있으면 customer_ready와 기본 다운로드를 통과한다. A07의 artifact 불일치는 전달 기록만 막는다.

근거: [monthly_manifest.py:288](../../backend/app/services/monthly_manifest.py#L288), [tasks.py:4435](../../backend/app/workers/tasks.py#L4435), [reports.py:259](../../backend/app/api/admin/reports.py#L259), [monthly_sov.py:263](../../backend/app/services/monthly_sov.py#L263), [report_engine.py:635](../../backend/app/services/report_engine.py#L635), [tasks.py:1208](../../backend/app/workers/tasks.py#L1208), [tasks.py:1901](../../backend/app/workers/tasks.py#L1901), [tasks.py:2008](../../backend/app/workers/tasks.py#L2008).

**개선:** 질문 coverage, 예정 반복 슬롯 처리, 확정·모호·통신 실패를 분리한다. 고정한 관측 슬롯을 보존하고 답변 미수신은 답변 요청부터, 저장 답변의 판정 실패는 판정 단계부터 재개한다. AMBIGUOUS를 원하는 확정 결과가 나올 때까지 새 답변으로 대체하지 않는다. V0/월간의 정상·제한된 진단·측정 불가 정책과 공개 문구를 일치시킨다. 표본 부족이 무한 대기로 바뀌지 않도록 기한이 지나면 한계를 표시한 상태로 종결하는 정책이 필요하다.

**합격:** 4확정+1통신 실패에서 실패 슬롯만 재개. 부족한 반복 슬롯을 명시한 적정성 상태와 복구 정책을 적용하고, 실제 반복 수를 표시하는 기존 보호도 유지. 전부 모호한 V0와 한 플랫폼 장애가 PDF·API에 그대로 드러남. 서로 다른 월의 답변 재사용이나 모호 판정 제거로 표본을 부풀리지 않음.

### A07 · P2 · V0 전달 가능 표시와 실제 전달 기록 API 일치

**확인:** V0는 PDF 경로만으로 전달 가능 표시가 되지만 `mark_report_sent`는 모든 보고서에 MonthlyReportArtifact와 SHA 일치를 요구한다. V0 생성은 해당 artifact를 만들지 않는다.

근거: [reports.py:250](../../backend/app/api/admin/reports.py#L250), [reports.py:539](../../backend/app/api/admin/reports.py#L539), [tasks.py:1995](../../backend/app/workers/tasks.py#L1995).

**개선·합격:** V0에도 검증된 전달 artifact를 만들거나 종류별 증빙 계약을 구현한다. V0 생성→다운로드→전달 기록이 끝까지 성공하고, 수정된 PDF의 이전 hash나 월간 SHA 불일치는 계속 차단한다. 실제 원장 보고 역할을 제거하는 문제와 구별한다.

### A08 · P2 · 자료 전체를 처리하는 서버 소유 배치와 자동 준비 UI

**확인:** 온보딩은 본문이 있는 처리 대상 PENDING 전체를 보여주면서 최대 50건을 한 번만 큐잉한다. 추적 종료는 전체 pending=0이고 추적 중 처리 버튼을 잠근다. 다른 처리가 없는 해당 자료가 51개 이상이면 잔여 자료를 처리하지 못한 채 조회만 반복할 수 있다. Essence 화면은 자동 승인 대기 중에도 수동 초안 생성을 안내하고, 그 수동 초안은 자동화가 편집 보호를 위해 넘겨받지 않는 경로로 들어갈 수 있다. 일반 자료 생성은 PENDING 저장 후 처리 enqueue 없이 반환한다. 자료 PATCH는 값의 실제 차이가 아니라 material 필드가 요청에 있는지로 판단해 같은 값 저장도 근거 삭제·PENDING 전환을 일으킬 수 있다. Naver 전용 자동 처리와 단건 process의 동일 hash 재사용은 이미 존재한다.

근거: [onboarding/page.tsx:2344](../../admin/app/hospitals/[id]/onboarding/page.tsx#L2344), [동일 파일:2383](../../admin/app/hospitals/[id]/onboarding/page.tsx#L2383), [essence.py:516](../../backend/app/api/admin/essence.py#L516), [essence.py:582](../../backend/app/api/admin/essence.py#L582), [essence.py:723](../../backend/app/api/admin/essence.py#L723), [essence-next-action.ts:46](../../admin/lib/essence-next-action.ts#L46), [essence_auto_review.py:899](../../backend/app/services/essence_auto_review.py#L899).

**개선·합격:** 서버가 대상 snapshot과 분할 처리 cursor를 가진 batch run을 소유한다. 일반 자료 입력도 durable 처리로 연결하고, 정규화된 값과 처리 버전이 같은 PATCH는 상태·근거·시각·유료 호출 모두 바꾸지 않는 no-op으로 만든다. UI 기본 동선은 자동 준비 상태와 실제 미해결 사유다. 51개·120개 모두 한 번의 시작으로 종결하고 페이지 재진입 시 추적을 복원한다. 수동 편집 초안은 보존하고, 자동 작업 대기 자체를 수동 승인 요구로 표시하지 않는다. A04 비용 통제를 선행한다.

### A09 · P2 · 사람 업무 판정을 시각이 아닌 실행 상태와 deadline에 연결

**확인:** 운영 센터의 PUBLISH_DUE는 08:00 이후 실행/재시도 상태를 보지 않고 사람 업무가 된다. RETRYING incident는 접히더라도 콘텐츠 행은 별도로 조치를 요구할 수 있다. 후행 확인 POST 성공 뒤 GET만 실패해도 확인 버튼을 다시 누르라는 안내가 나온다. 재생성 완료는 자연스러운 새로고침에 맡겨져 있고, 보고서→운영 센터 링크의 hospital_id는 URL 정규화에서 사라진다.

근거: [operations_center_today_queries.py:56](../../backend/app/api/admin/operations_center_today_queries.py#L56), [동일 파일:212](../../backend/app/api/admin/operations_center_today_queries.py#L212), [content/page.tsx:509](../../admin/app/hospitals/[id]/content/page.tsx#L509), [content/page.tsx:547](../../admin/app/hospitals/[id]/content/page.tsx#L547), [ReportReviewDialog.tsx:101](../../admin/app/hospitals/[id]/reports/ReportReviewDialog.tsx#L101), [operations-center.ts:66](../../admin/lib/operations-center.ts#L66).

**개선·합격:** 동일 업무의 run·incident·콘텐츠를 연결하고 정상 RUNNING/기한 내 RETRYING은 관측 정보로 표시한다. 허용 지연 초과나 판단이 필요한 terminal failure만 사람 업무로 전환한다. POST 200→GET 503은 조회만 재시도한다. 재생성 완료가 자동 반영되고 첫 페이지 밖의 병원도 링크 한 번으로 정확한 사건이 열린다. 편집 중인 원고는 자동 갱신으로 덮지 않는다.

### A10 · P2 · 검색 신호의 관측 부족을 병원 자료 결함·성과 개선으로 오인하지 않기

**확인:** 노출 액션 엔진은 확정 판정된 성공 답변 가운데 URL이 없는 비율이 50% 이상이면 SOURCE_SIGNAL_GAP을 만든다. 검색 실행 여부와 자사 관련 URL인지 구분하지 않는다. 병원 전체 최신 1,000개 기록에서 추천 key가 사라지면 gap/action을 RESOLVED/COMPLETED로 바꾼다. 기록이 창 밖으로 밀리는 것은 개선 증거가 아니다.

근거: [exposure_action_engine.py:187](../../backend/app/services/exposure_action_engine.py#L187), [동일 파일:209](../../backend/app/services/exposure_action_engine.py#L209), [동일 파일:365](../../backend/app/services/exposure_action_engine.py#L365), [동일 파일:440](../../backend/app/services/exposure_action_engine.py#L440).

**개선·합격:** no-search·unknown·검색 후 출처 없음·자사 미인용·자사 인용을 구분한다. target별 같은 정책의 충분한 최신 관측으로 진단한다. 근거 부족·범위 제외·실제 개선 완료를 분리한다. no-search만으로 사람에게 자료 보강을 요구하지 않고, 다른 타깃의 측정 증가만으로 개선 완료가 되지 않아야 한다.

### A11 · P2 · 구매한 답변과 판정을 독립 체크포인트로 보존

**확인:** 유료 SoV는 답변과 판정을 한 번에 실행한다. 판정 실패 raw_response는 남지만 cell이 FAILED로 남아 재시도 대상이 되면 다시 답변부터 구매한다. 같은 cell에 확정 성공이 하나라도 있으면 A06에 따라 실패 반복도 재시도되지 않는다. V0는 전체 질문 루프 뒤 commit, 무료 진단은 전체 gather 뒤 결과와 캐시를 commit하므로 중간 worker 종료 시 수신 결과를 잃을 수 있다. V0의 완료 측정 checkpoint는 PDF 실패 시 재사용을 이미 지원한다.

근거: [sov_engine.py:1180](../../backend/app/services/sov_engine.py#L1180), [sov_engine.py:1232](../../backend/app/services/sov_engine.py#L1232), [tasks.py:3873](../../backend/app/workers/tasks.py#L3873), [tasks.py:1943](../../backend/app/workers/tasks.py#L1943), [v0_checkpoint.py:135](../../backend/app/workers/v0_checkpoint.py#L135), [lead_diagnosis_engine.py:395](../../backend/app/services/lead_diagnosis_engine.py#L395).

**개선·합격:** manifest/query/platform/repeat/protocol의 answer artifact를 수신 즉시 저장하고 judgment attempt를 분리한다. 답변 성공→판정 실패→복구는 답변 1회와 판정만 재실행. k번째 저장 직후 worker를 종료해도 k개는 재구매하지 않는다. 측정 시각·모델·검색·citation을 보존하며 같은 진단·답변 hash·판정 정책뿐 아니라 병원명·지역·경쟁사 등 판정 입력 fingerprint까지 같을 때만 판정을 재사용한다.

### A12 · P2 · 이미지 업로드 재시도를 생성·검수에서 분리

**확인:** 이미지 엔진의 retry 함수가 생성→검수→GCS 업로드 전체를 감싼다. GCS transient 실패에도 이미 생성·검수한 이미지를 다시 생성할 수 있다.

근거: [image_engine.py:550](../../backend/app/services/image_engine.py#L550), [동일 파일:591](../../backend/app/services/image_engine.py#L591), [동일 파일:600](../../backend/app/services/image_engine.py#L600), [동일 파일:672](../../backend/app/services/image_engine.py#L672).

**개선·합격:** 생성 바이트·검수 결과·업로드를 나누고 hash로 연결한다. 업로드 두 번 실패 후 성공 시 생성 1회·검수 1회·업로드 3회. 프로세스 재시작까지 재사용하려면 검증되지 않은 바이트를 공개하지 않는 임시 저장·만료 정책을 별도 설계한다. A03의 최신 입력 확인을 유지한다.

### A13 · P2 · 실제 비용을 설명할 사용량 원장과 멱등 예약 정산

**확인:** 사용량 원장에는 provider/model/workflow/run/attempt/cache 구분이 없다. cache 생성·읽기 토큰은 입력에 합쳐지고 이미지·검수 토큰 일부는 계측되지 않는다. 누락과 0도 구분되지 않는다. SoV query 클라이언트는 SDK retry를 끄지만 판정기는 기본 retry를 써서 logical call 한 건이 여러 HTTP 시도여도 actual은 한 번 증가한다. 예약 반환은 원래 예약 시점이 아니라 반환 시점의 일·월 key를 사용한다.

근거: [usage.py:20](../../backend/app/models/usage.py#L20), [hospital_usage.py:39](../../backend/app/services/hospital_usage.py#L39), [content_engine.py:714](../../backend/app/services/content_engine.py#L714), [image_engine.py:276](../../backend/app/services/image_engine.py#L276), [sov_engine.py:124](../../backend/app/services/sov_engine.py#L124), [cost_guard.py:425](../../backend/app/services/cost_guard.py#L425).

**개선:** 공통 usage event에 provider/model, 업무·병원/lead·item/run/attempt, provider request ID, 캐시별 입력·출력·reasoning·검색/이미지 단위, usage-known을 기록한다. 공급자별 토큰 필드는 정규화하되 원본 의미를 보존한다. reservation receipt에 ID·원 기간·예약/소비/반환량을 두고 원자적·멱등 정산한다. SDK retry를 끄고 공통 retry로 옮기거나 HTTP attempt를 계측한다.

**합격:** 429→성공은 HTTP 2회, 성공 토큰은 중복 합산 없음. unknown은 0과 구분. 23:59→00:01 및 월말 반환은 원 예약만 조정. 중복 반환으로 다른 작업 예산이 줄지 않음. 무료 진단 비용은 계약 병원 원가와 분리. 원장 저장 실패는 업무 성공과 분리하되 관측 누락을 재수집할 수 있음.

### A14 · P2 · 자료 길이·근거 수에 따른 자동화 한계 제거

**확인:** LLM 근거 추출에 전달하는 원문은 앞 24,000자로 제한되고, Essence 합성은 전체 근거 노트를 넣지만 입력 토큰 총량 제한은 없다. 독립 검수는 최대 80개 근거를 선택하며 그보다 많은 연결 근거가 필요한 후보는 호출 전 ESCALATE된다. 자료 누적이 단순히 사람의 검토 부담으로 바뀔 수 있다.

근거: [essence_engine.py:469](../../backend/app/services/essence_engine.py#L469), [동일 파일:812](../../backend/app/services/essence_engine.py#L812), [essence_auto_review.py:652](../../backend/app/services/essence_auto_review.py#L652), [동일 파일:747](../../backend/app/services/essence_auto_review.py#L747).

**개선·합격:** 자료 hash별 추출 재사용, 의미 중복 정규화, 주제별 합성, 근거 coverage를 보존하는 분할 검수를 적용한다. 길이 제한으로 미처리한 범위를 기록하고 자동 후속 처리한다. 긴 문서·100개 이상 근거에서 전체 범위가 처리되며 개수 초과만으로 수동 승인에 넘기지 않는다. 필요한 근거를 잘라 비용만 줄이는 방식은 사용하지 않는다.

### A15 · P2 · 무료 진단 동시 호출의 캐시·공급자 제한 정합

**확인:** 공유 캐시는 저장 경합을 처리하지만 동시 cache miss의 선행 유료 호출은 막지 못한다. 무료 진단은 플랫폼별 Gemini 제한 대신 공통 leadgen semaphore를 사용한다. 유료 경로에서 억제한 burst가 무료 경로에는 남을 수 있다.

근거: [lead_diagnosis_engine.py:100](../../backend/app/services/lead_diagnosis_engine.py#L100), [lead_query_cache.py:114](../../backend/app/services/lead_query_cache.py#L114), [sov_engine.py:79](../../backend/app/services/sov_engine.py#L79), [sov_engine.py:1293](../../backend/app/services/sov_engine.py#L1293).

**개선·합격:** 실제 캐시 identity를 공유하는 single-flight lease를 두고 실패 leader를 인계한다. provider 제한을 paid/lead 자원 분리와 함께 적용한다. 같은 캐시 키의 동시 10요청에서 답변 1회, 병원별 판정은 독립, 다른 모델·정책·repeat은 혼합하지 않는다. Gemini in-flight는 정한 제한 이내이고 OpenAI는 독립 진행. 여러 프로세스가 공유하는 API key의 합산 제한도 측정한다.

### A16 · P2 · 검색 표면의 일시 장애와 삭제를 구분하고 색인 신호 복구

**확인:** 병원 llms.txt는 upstream 예외를 모두 404로 반환한다. sitemap은 병원 조회 실패를 빈 결과로 만들고 콘텐츠 pagination 실패 때 일부 목록을 정상 결과로 돌려줄 수 있다. IndexNow 실패는 로그/false로 끝나고 수동 backfill은 있지만 자동 재전송 스케줄은 없다.

근거: [llms.txt/route.ts:167](../../site/app/[slug]/llms.txt/route.ts#L167), [sitemap-builder.ts:55](../../site/lib/sitemap-builder.ts#L55), [동일 파일:230](../../site/lib/sitemap-builder.ts#L230), [indexnow.py:160](../../backend/app/services/indexnow.py#L160), [tasks.py:7036](../../backend/app/workers/tasks.py#L7036).

**개선·합격:** 미등록/비공개와 transient API 오류를 구분한다. 500·429·timeout은 적절한 503/no-store 등으로 표현하고 일부 sitemap을 완전한 결과로 확정하지 않는다. 실제 비공개 글은 stale cache로 계속 내보내지 않는다. IndexNow는 host·URL·revision별로 합쳐 bounded 재전송하며 발행 알림은 늘리지 않는다. IndexNow 실패가 곧 색인 실패나 순위 하락이라는 뜻은 아니다.

### A17 · P3 · 긴 외부 호출과 업무 제어를 구조적으로 분리

**확인:** 자료 처리 중 병원 advisory lock과 DB session이 외부 호출 동안 유지된다. worker는 한 서비스에서 6개 큐를 함께 소비하고 기본 process concurrency는 2다. worker 최대 인스턴스 설정은 5이므로 전체가 항상 2개 슬롯이라는 의미는 아니다. tasks.py는 7천 줄 이상으로 여러 workflow를 조정한다. 줄 수만으로 성능 문제를 판정하지 않는다.

근거: [tasks.py:1277](../../backend/app/workers/tasks.py#L1277), [docker-entrypoint.sh:21](../../backend/docker-entrypoint.sh#L21), [cloudrun.tf:262](../../terraform/cloudrun.tf#L262), [variables.tf](../../terraform/variables.tf), [DB 연결 예산 검사](../../scripts/check_db_connection_budget.py).

**개선:** A03의 claim+revision 조건부 저장을 먼저 만든 뒤 외부 호출을 긴 DB 잠금 밖으로 옮긴다. 큐 대기와 처리 시간을 측정하고, 발행·복구 제어가 긴 SoV/PDF 작업에 밀리면 우선 제어 큐의 실행 용량을 확보한다. 이후 필요할 때 worker 자원을 분리한다. 기존 Celery task 이름을 보존한 얇은 진입점과 workflow 서비스로 점진적으로 추출한다.

**합격:** 느린 provider에도 같은 병원의 독립 수정이 과도하게 잠기지 않고 중복 처리가 없음. 긴 측정 부하 중 발행·복구 deadline 충족. 확장 후 DB 연결 상한 검사 통과. 현재 Site 단일 인스턴스 캐시 계약은 공유 cache/invalidation 설계 없이 해제하지 않는다.

## 6. Human in the Loop의 목표 계약

| 상황 | 목표 처리 | 사람을 부르는 조건 |
|---|---|---|
| 계약·병원 공식 사실·커스텀 도메인 결정 | 최초 사실 입력·권한 있는 결정 후 자동 진행 | 입력이 없거나 상충하여 시스템이 사실을 결정할 수 없음 |
| 자료 처리·Essence 초안·자동 검수 | 전체 자료 자동 drain, bounded 수정·재검토 | 근거 충돌이 자동 수정으로 해결되지 않거나 의도적인 수동 편집과 충돌 |
| 정상 생성·이미지·발행 | 안전 검사 후 자동 완료, 정상 Slack 없음 | 해결되지 않은 hard fact 위험 또는 허용 지연 초과 |
| timeout·429·예산 reset·업로드 장애 | 저장 결과 재사용, due time에 자동 복구 | 장기 장애·명시적인 설정/정책 변경 필요. 같은 사건은 한 번의 조치로 연결 |
| 후행 콘텐츠 검수 | 초기·변경·위험도에 따른 표본 검수로 발전 | 위험 신호가 있는 공개본. 모든 정상 글 확인을 새 선행 게이트로 만들지 않음 |
| 월간 보고서 생성·검증 | 자동 측정 복구·PDF 검증·준비 완료 | 데이터로 해결할 수 없는 계약/사실 불일치 |
| 원장에게 월간/V0 보고 | 현재 서비스의 AE 설명·전달 책임 보존 | 이는 제품상 사람의 역할이며 생성 파이프라인의 수동 승인과 구분 |
| 무료 진단 이메일 | 검증된 artifact 자동 전달, 멱등성 유지 | 공급자 전송 결과 불명 등 자동 재발송이 중복을 만들 수 있는 예외 |
| 정상 상태 조회·완료 반영 | UI가 실행을 추적하여 자동 갱신 | 완료 POST를 다시 누르게 하거나 새로고침 자체를 사람 업무로 만들지 않음 |

이 표는 목표 정책이다. 특히 후행 검수의 표본화는 현재 전수 확인 UI가 자동으로 바뀌었다는 뜻이 아니다. 위험도 기준과 검증된 생성 품질을 확보한 뒤 적용한다. “아무 알림도 없음”이 아니라 **시스템이 처리할 수 있는 일은 조용히 끝내고, 사람이 실제로 바꿀 수 있는 예외만 명확하게 전달**하는 것이 목표다.

## 7. 토큰·호출 예산과 최적화 순서

### 현재 코드의 호출 구조

아래는 정상 경로와 실행 한 회 안에서의 retry 확대 구조다. 월 총량·실제 청구액·보장된 절대 상한이 아니다. task 재실행은 별도이며, 사용 모델은 설정에 따라 달라진다. 출력 설정은 최대치로 실제 소비량이 아니다.

| 업무 | 정상 논리 호출 | 재시도 확대와 제한 |
|---|---|---|
| 본문 | Sonnet writer 1 + Haiku reviewer 1 | 최초 생성 포함 최대 2개 후보(재작성 최대 1회), writer 내부 최대 3회로 writer 최대 6; 검수 최대 2. 출력 설정 writer 5,500/reviewer 1,200 |
| 이미지 | 이미지 1 + 멀티모달 검수 1 | Google 주제 경로 최대 3 + 안전 fallback 최대 3; OpenAI 우선이면 앞단 최대 3 추가. 정책 거절 자체는 반복하지 않음 |
| 자료 추출 | 빠른 모델 1 | 내부 transient/parser retry 최대 3, 결정론적 fallback은 별도. LLM 입력 원문 앞 24,000자, 출력 3,000 |
| Essence | 합성 1 + 독립 검수 1 | 최대 2개 후보 × (합성 1 + 검수 최대 2 + 필요 시 재정 최대 2) = 서비스 실행 한 회 최대 10. task 재실행은 별도이며 정상 경로는 2회 |
| SoV 관측 슬롯 | 답변 1 + 자사 판정 0~1 + 경쟁사 판정 0~1 | 답변 최대 3, 각 판정 SDK HTTP 최대 3. prefilter가 불필요한 판정을 줄임 |
| V0 | 최대 15질문 × 2플랫폼 × 5반복 = 150답변 | 설정·대상 수·회로 차단에 따라 감소. 완료 checkpoint 재사용. 판정은 별도 |
| 주간·월간 cell | 질문·플랫폼당 기본 5반복 | 현재 성공 cell은 건너뜀. A06의 슬롯 완결성 보완 필요 |
| 무료 진단 | 3질문 × 2플랫폼 × 3반복 = 18답변에서 cache hit 차감 + 판정 최대 18 | 답변 retry·판정 SDK retry와 진단 전체 retry는 별도. 캐시 hit가 유료 호출 0이라는 뜻은 아님 |

근거: [tasks.py:288](../../backend/app/workers/tasks.py#L288), [tasks.py:639](../../backend/app/workers/tasks.py#L639), [tasks.py:980](../../backend/app/workers/tasks.py#L980), [essence_auto_review.py:46](../../backend/app/services/essence_auto_review.py#L46), [lead_diagnosis_engine.py:82](../../backend/app/services/lead_diagnosis_engine.py#L82), [lead_diagnosis_tasks.py:65](../../backend/app/workers/lead_diagnosis_tasks.py#L65).

### 최적화 원칙

1. **재구매부터 없앤다.** 답변 성공 후 판정 실패, 이미지 성공 후 업로드 실패, worker 종료 직전 수신 결과가 첫 대상이다(A11~A12).
2. **계측부터 비교 가능하게 만든다.** 업무 예약·SDK 논리 호출·HTTP 시도·성공 응답 사용량·실제 청구 단위를 구분한다(A13). unknown을 0으로 계산하지 않는다.
3. **안정된 prefix를 유지한다.** 현재 콘텐츠는 static→hospital→item 순서와 두 cache breakpoint를 이미 사용한다. 병원별 불변 context와 변하는 타깃·제목을 분리하고 실제 cache hit를 확인한다. Anthropic은 동일 prefix와 cache 수명 조건이 중요하므로 TTL을 늘리기 전에 재사용 간격과 비용을 측정한다. [Anthropic 공식 문서](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
4. **공급자별 토큰 회계를 그대로 이해한다.** cached/write/reasoning 등의 usage 구조는 API·모델에 따라 다르다. 현재 잠금 SDK와 응답 fixture로 지원을 확인한 뒤 정규화하고, 다른 공급자의 숫자를 그대로 더해 금액으로 취급하지 않는다. [OpenAI 공식 문서](https://developers.openai.com/api/docs/guides/prompt-caching)
5. **관련 근거를 선택하되 coverage를 잃지 않는다.** 중복 설명·전체 자료 반복 투입을 줄이면서 사용한 주장과 근거 연결을 보존한다. A02의 스키마를 먼저 고쳐야 토큰을 지불하고도 핵심 설명을 버리는 낭비가 사라진다.
6. **모델 변경은 품질 실험 후 한다.** 유형별 근거 충실도·위험 주장·자동 완료·재작성률·최종 원가를 고정 표본에서 비교한다. 더 싼 호출이라도 재작성과 수동 검수가 늘면 최종 편당 원가는 나빠질 수 있다.

현재 cost guard는 가용성을 우선한 논리 작업 수 제한이다. Redis fail-open과 재시도 확대·일부 경로 우회가 있으므로 **절대 지출 상한으로 설명하면 안 된다.**

## 8. SEO/GEO/AEO 목적 검증

현재 canonical, host 격리 sitemap, 실제 본문과 맞는 Article/FAQ/MedicalClinic, 정확한 날짜·저자, SSR 대표 이미지, 인용과 공개 안전 게이트는 유지할 가치가 있다. 추가 schema 개수나 llms.txt 존재를 성과 KPI로 삼지는 않는다.

Google은 AI 검색 기능에도 기존 SEO 기반을 적용하며 추가 AI 파일이나 특별한 schema를 요구하지 않는다고 설명한다. 따라서 우선순위는 공개 정보의 정확성, 읽을 수 있는 본문, 내부 링크, crawl 가능성, 가시 내용과 structured data의 일치다. 이 조건을 충족해도 색인·노출은 보장되지 않는다. [Google 공식 AI 검색 안내](https://developers.google.com/search/docs/appearance/ai-features)

이 시스템의 효과 측정은 다음을 구분해야 한다.

- **기술적 적격성:** 공개 URL 200, 정확한 canonical, sitemap 완결성, 의도하지 않은 robots 차단 없음, schema와 본문 일치.
- **관측 결과:** 동일 정책·모델·질문·표본 조건의 AI 언급/자사 인용, 실제 검색 실행 여부, 충분한 분모와 불확실성.
- **제품 성과:** 관련 환자 질문을 얼마나 충실하게 다루었는지, 유효한 문의·예약 연결이 있었는지. 해당 전환 계측의 구현·성과는 이번 감사에서 검증하지 않았다.

콘텐츠 발행 뒤 SoV가 변했다는 것만으로 인과 효과를 주장하지 않는다. A10의 액션 상태를 정직하게 고치는 것이 이 피드백 순환의 전제다.

## 9. 실행 순서와 변경 경계

| 단계 | 포함 항목 | 완료 조건 | 보존할 계약 |
|---|---|---|---|
| 1. 핵심 무결성 | A01~A04, A06 | 사실·최신 revision·비용 차단·표본 상태 회귀 통과 | 공개 안전 검사, 현재 비교 불가 처리, 수동 편집 보존 |
| 2. 자동 복구와 관측 | A05, A11~A13 | 저장 결과 재사용, due 실행, attempt/usage/예약 정산 검증 | 중복 메시지·중복 이메일 방어, 측정 시각 보존 |
| 3. 사람 업무 제거 | A07~A10, A14 | 전체 자료 drain, 정확한 준비·전달·조치 상태 | 자동 승인 기준, 원장 보고 역할, 근거 coverage |
| 4. 부하·검색 복구 | A15~A17 | cache single-flight, transient 검색 응답, 부하 시험 | tenant 격리, 공개 중지, DB 연결 예산·캐시 계약 |

단계는 배포 가능한 작은 변경으로 나눈다. A13의 계측 설계는 1단계부터 병행하고, 최적화 효과는 계측 이후 비교한다. A06 관측 슬롯과 A11 artifact는 함께 설계하되 마이그레이션 중 기존 완료 기록을 새로 측정하거나 과거 lineage를 만들어내지 않는다. 기존 행은 legacy/unknown으로 구분한다.

실제 구현은 요청한 **Sol high**가 맡고, 상위 검수자는 변경 diff·반례·운영 의미를 독립 확인한다. 구현 범위별 회귀, 통합 검증, 커밋·PR CI·머지, 기존 배포 절차와 헬스체크 순서를 따른다. 이 감사 문서 자체는 앱 동작 변경이나 신규 배포를 뜻하지 않는다.

## 10. 합격 시험과 운영 지표

### 변경 시 필요한 시험

| 시험 | 핵심 반례 | 기대 결과 |
|---|---|---|
| 콘텐츠 계약 | hard/soft review, 긴 본문 꼬리, Essence v2, 실제 LLM 치료 서사 | 위험 주장 차단·자동 수정, 전체 coverage, 철회 문구 없음 |
| PostgreSQL 경합 | 느린 생성→새 편집/claim, 후보 선택→발행일 변경 | stale write 0건·조기 발행 0건 |
| 비용·재개 | 단건/일괄/Naver/cached lead, kill switch, 자정·월경계 | 우회 0회·멱등 정산·회복 후 자동 진행 |
| 관측 슬롯 | 4성공+1통신 실패, 전부 ambiguous, judge 장애 | 필요한 단계만 재개, 분모·불확실성 보존 |
| 장애 복구 | k개 답변 뒤 worker 종료, 이미지 업로드 2회 실패 | 저장 결과 재구매 없음, 검증 없는 이미지 공개 없음 |
| Admin 통합 | 120자료, POST200/GET503, 08시 RUNNING, 병원 링크 | 한 번 시작으로 종결, 중복 확인 요구 없음, 정확한 문맥 |
| 공개 검색 | upstream500/429/timeout, 2페이지 실패, 비공개 전환 | 가짜404/빈 정상 sitemap 없음, 비공개 보존 |
| 전달 | V0 artifact, 월간 SHA 변경, 이메일 결과 불명 | 정상 기록 성공, 잘못된 증빙·중복 재전송 차단 |
| 부하 | 동시 cache miss, Gemini/긴 SoV 부하, 다중 worker | single-flight, 제한 준수, 제어 업무 deadline·DB 예산 충족 |

단위·통합 시험은 가짜 공급자 응답과 실제 테스트 PostgreSQL/Redis를 사용한다. 실제 모델 품질 평가는 별도의 제한된 표본·비용 예산으로 시행하며 자동 테스트 통과와 구분한다. 단순 문서 변경 때문에 유료 생성이나 고객 통지를 실행하지 않는다.

### 먼저 수집할 지표

| 지표 | 정의·주의점 | 목표 방향 |
|---|---|---|
| 계약 슬롯 자율 완료율 | 계약상 기한 도래 슬롯 중 수동 수정 없이 안전하게 완료한 수. 실패 후 임의로 분모에서 제외하지 않음 | 상승. blocked 사유와 적격성 보조 지표 병기 |
| 사람 개입 밀도 | 100개 계약 슬롯당 실제 사람 mutation/처리 시간, 의무적 고객 보고는 별도 | 불필요한 반복 조작 감소 |
| 알림의 조치 가능성 | incident별 알림 수, 정상·자동 복구 중 알림, 실제 사람 조치 필요 비율 | 정상 생성·발행 알림 0 유지, 동일 사건 중복 감소 |
| 최종 산출물 원가 | 모델·검색·이미지 비용 및 retry를 포함한 승인·공개 1편당 비용, 리포트당 비용 | 품질 유지 조건에서 감소 |
| 재구매 비율 | 같은 입력/관측 슬롯의 이미 성공한 단계가 다시 호출된 비율 | 장애 복구 재구매 제거 |
| 캐시 효율 | 공급자별 cache read/write/일반 input과 재사용 간격, unknown 비율 | 비용 환산 가능한 관측 확보 후 최적화 |
| 측정 충실도 | 예정·수신·확정·모호·실패 슬롯, 비교 가능 cohort 비율 | 완료 상태가 실제 관측 범위를 정확히 반영 |
| 자동 복구 지연 | 오류부터 회복 후 완료까지, 원인별 due 지연·queue wait p95 | 정상 작업 deadline 내 종결 |
| 공개 무결성 | stale write, 조기 발행, 미검증 hard finding 공개, 중복 고객 전달 | 회귀 시험에서 0, 운영에서도 사건별 추적 |

현재값은 미측정이다. 첫 관측 기간의 트래픽과 월간 주기 coverage를 확보한 뒤 수치 목표를 정한다. 짧은 정상 기간의 100%를 월간 자율 운영 입증으로 해석하지 않는다. 알림을 숨기거나 미완료 작업을 제외하여 KPI를 개선하지 않는다.

## 11. 이 문서의 사용과 갱신

A01~A17을 구현 작업의 기준 ID로 사용한다. 수정 PR에는 해당 ID, 바뀐 상태 계약, 회귀 반례, 검증 결과와 운영 관측을 적는다. 해결 여부는 실제 머지 커밋과 검증 근거를 붙여 갱신하고, 재조사 시 문서 버전·날짜·앱 기준 커밋을 올린다.

구조 설명은 [시스템 구조](../architecture/system-map.md), 현재 운영은 [마케터 안내](../ops/marketer-operations-runbook.md), 알림은 [알림 정책](../ops/slack-notification-policy.md), 배포 사실은 [릴리스 기록](../releases/2026-09-07-345a642.md)을 함께 사용한다. **이 문서의 목표 정책을 이미 배포된 동작으로 운영 안내에 섞지 않는다.**

## 부록 · 실제 실행한 순수 함수 재현

2026-09-07에 상위 검수자가 아래 fixture를 다시 실행해 관측값을 확인했다. 저장소 루트에서 테스트 의존성이 설치된 Python 환경을 사용한다. 가짜 키와 socket 차단으로 외부 호출을 막고 DB 저장을 하지 않는다. 짧은 NOTICE fixture는 **저장된 후보에 대한 발행 정책 함수**를 시험하며 전체 생성 길이·DB 조회·실제 발행 성공을 시험하지 않는다. 이 실행은 결함 재현이지 수정 후 통과 시험이 아니다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend APP_ENV=test \
ANTHROPIC_API_KEY=test-only OPENAI_API_KEY=test-only \
backend/.venv/bin/python - <<'PY'
import socket
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('No network'))
from types import SimpleNamespace as S
from datetime import datetime, timezone, date
from uuid import uuid4
from app.models.content import ContentType
from app.models.essence import PhilosophyStatus
from app.services.content_publication import assess_content_publication, apply_publication_assessment
from app.services.content_target_planner import prepare_automatic_content_brief_sync
from app.services.content_engine import _build_philosophy_context, _build_content_brief_context, _validate_body_length
from app.services.content_brief import build_content_brief
from app.services.content_ai_review import _review_data
from app.workers.tasks import _generation_attempt_context, _generation_attempt_is_unchanged

p = S(id=uuid4(), version=2, status=PhilosophyStatus.APPROVED,
      positioning_statement='환자와 상담하는 진료', doctor_voice='', patient_promise='',
      content_principles=[], tone_guidelines=[], must_use_messages=[], avoid_messages=[],
      medical_ad_risk_rules=[], treatment_narratives=[], local_context={}, evidence_map={},
      unsupported_gaps=[], conflict_notes=[], synthesis_notes='')
i = S(content_type=ContentType.NOTICE, title='진료 이용 안내',
      body='이 병원은 ABC 장비를 보유하고 있습니다.', meta_description='',
      faq_question=None, faq_answer_summary=None, references_list=[],
      image_url='gs://test/image.png', image_policy_verified_at=datetime.now(timezone.utc),
      essence_check_summary={'blocking': True, 'findings': ['병원에 없는 ABC 장비 주장'],
                             'ai_review': {'status': 'REVISE', 'confidence': .99,
                                           'findings': ['병원에 없는 ABC 장비 주장']}})
a = assess_content_publication(i, p)
apply_publication_assessment(i, a)
print('A01', a.publishable, i.essence_status, i.essence_check_summary['blocking'],
      i.essence_check_summary['ai_review']['status'])
slot = S(brief_status='APPROVED', scheduled_date=date(2026, 9, 9),
         content_brief={'target_query': '진료 안내',
                        'philosophy_reference': {'id': 'retired-v1'},
                        'must_use_messages': ['철회한 ABC 장비 안내']})
b = prepare_automatic_content_brief_sync(None, item=slot, hospital=None, philosophy=p)
print('A02', b['philosophy_reference'], b['must_use_messages'])
p.treatment_narratives = [{'treatment': '검사 A', 'patient_language': ['환자설명_고유표식'],
                          'cautions': ['주의사항_고유표식'], 'evidence_note_ids': ['note1']}]
h = S(id=uuid4(), name='테스트의원', treatments=[])
i = S(id=uuid4(), content_type=ContentType.TREATMENT, title='검사 A')
b = build_content_brief(hospital=h, content_item=i, philosophy=p)
prompt = _build_philosophy_context(p) + '\n' + _build_content_brief_context(b, p)
review = _review_data(hospital=h, philosophy=p, content={'body': '본문'}, content_brief=b)
for marker in ['환자설명_고유표식', '주의사항_고유표식']:
    print('A02', marker, marker in str(b), marker in prompt, marker in str(review))
body = ('가 ' * 3200) + '끝부분_검토표식'
_validate_body_length(body)
review = _review_data(hospital=h, philosophy=p, content={'body': body}, content_brief=b)
print('A01 coverage', len(body), len(review['candidate']['body']),
      '끝부분_검토표식' in review['candidate']['body'])
i = S(content_type=ContentType.NOTICE, scheduled_date=date(2026, 9, 9), query_target_id=None)
for reason in ['COST_BLOCKED', 'PROVIDER_TIMEOUT']:
    i.essence_check_summary = {'generation_attempt': {
        'reason': reason, 'context': _generation_attempt_context(i, p)}}
    print('A05', reason, _generation_attempt_is_unchanged(i, p))
PY
```

관측값:

```text
A01 True ALIGNED False REVISE
A02 {'id': 'retired-v1'} ['철회한 ABC 장비 안내']
A02 환자설명_고유표식 True False False
A02 주의사항_고유표식 True False False
A01 coverage 6408 6000 False
A05 COST_BLOCKED True
A05 PROVIDER_TIMEOUT True
```
