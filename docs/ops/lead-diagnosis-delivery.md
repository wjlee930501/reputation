# 진단 리포트 전달 — 무료 진단 이메일과 도입문의 내부 보관

문서 버전: **3.3** · 갱신일: **2026-09-16 (Asia/Seoul)**
소스 기준선: **`d96bd2143ba509cd1fcae73f028286b6d938ae8e`**
구현 상태: **코드·문서 정합 (INQUIRY Admin 내부 보관 반영). 런타임 동작 변경 없음**

리드 진단 리포트에는 전달 모드가 두 개 있고, 아래 대부분의 내용은 그중 **무료 진단(AI_DIAGNOSIS)** 모드를 설명한다. AE가 직접 전달하는 유료 월간 보고서와도 구분한다. 다음 값은 코드 기본값이며 운영 설정으로 달라질 수 있다.

## 전달 모드 두 가지

| 모드 | 진단이 태어나는 곳 | 전달 대상 | 고객 이메일 | 수동 재발송 |
|---|---|---|---|---|
| 무료 진단 (`AI_DIAGNOSIS`) | `/ai-diagnosis` 공개 접수 | 신청자 | 상태·리포트 링크 이메일. 아래 재시도 사다리를 탄다 | 허용 (중복 위험 확인 후) |
| 도입문의 초도 노출 진단 (`INQUIRY`) | 도입문의 폼 자동 생성 또는 Admin `진단 생성(내부용)` | AE(콜용) | **없음.** `delivery_status=INTERNAL`이 종결 상태다 | 없음. 요청은 거절된다 |

두 모드는 **측정과 PDF 생성 파이프라인을 공유**한다. 갈라지는 지점은 발송뿐이다. 그래서 아래 측정·PDF 항목은 양쪽에 그대로 적용되고, 이메일 항목은 무료 진단에만 적용된다. 도입문의 접수 직후의 자동 진단 생성과 원장 안내 문자는 [도입문의 접수 자동 처리](inquiry-intake-automation.md)가 정본이다.

## 무료 진단의 정상 흐름

1. `/ai-diagnosis` 신청이 Site의 같은 출처 BFF를 거쳐 Backend로 전달된다. 동의·허니팟·입력·중복 병원 identity·일일 슬롯을 검사한다.
2. 리드, 진단, 중복 제한 hash, 토큰을 하나의 DB 트랜잭션으로 저장한다. 슬롯 카운터는 08:00 KST 기준이며 중복으로 저장이 취소되면 슬롯도 rollback한다.
3. 접수 Slack은 별도 전달한다. 브로커 전달이 유실되어도 1분 drain이 DB의 PENDING 상태를 회수한다.
4. 접수 시 저장한 질문·모델·정책으로 측정한다. 각 답변은 완료 직후 저장하고, 병원·지역·경쟁사·정책을 포함한 입력 fingerprint가 같을 때 판정을 이어서 저장한다. 유효한 결과에서 PDF를 생성한 뒤 이메일 전달로 넘어간다.
5. 이메일은 PDF 첨부 대신 상태/리포트 링크를 보낸다. delivery UUID가 같은 발송의 멱등성 키다.

## 독립적인 상태와 재시도

| 단계 | 성공의 의미 | 실패·복구 경계 |
|---|---|---|
| 접수 알림 | 알림 전송 상태 SENT | 업무 접수와 별도. PENDING 회수·전송 실패 기록 |
| 측정 | SUCCEEDED 또는 기준을 만족한 PARTIAL | 실행 최대 3회와 lease 회수. 답변 저장 뒤 판정만 재개. 비용 차단은 재개 시각을 저장하고 외부 호출·시도 횟수 소모 없이 1분 회수가 예산 초기화 뒤 재개 |
| PDF | 최신 실행 결과의 artifact 저장 후 READY | 보고서 생성 최대 3회. BLOCKED는 정상 전달로 취급하지 않음 |
| 이메일 (무료 진단만) | provider 응답을 기록한 SENT | 기본 총 4회: 최초, +5분, +30분, +4시간. 최초 발송의 24시간 창 밖은 자동 재발송하지 않음 |

기본 3질문 × 2플랫폼 × 3반복으로 18개 답변을 계획한다. PARTIAL은 단순히 일부 답변이 있다는 뜻이 아니다. **각 플랫폼에서 계획 수−1 이상 확정**이어야 하므로 기본값에서는 각각 8/9 이상이어야 한다. 실패·미확정 응답을 미언급으로 분모에 넣지 않는다.

답변은 질문·모델·플랫폼·정책·반복을 구분한 7일 공유 캐시에서 재사용할 수 있다. 같은 cache identity의 동시 요청은 Redis의 갱신 가능한 single-flight lease와 결과 인계로 한 번의 live 답변 구매에 모으고, owner가 끝나지 못하면 다른 요청이 인계한다. Redis 장애에는 fail-open하므로 중복 방지가 절대 보장은 아니다. 이 합치기는 같은 cache key에만 적용된다. provider별 leadgen semaphore는 프로세스 내부에서만 동작하므로 여러 Worker 인스턴스와 서로 다른 key가 같은 API key를 호출할 때의 전역 상한은 아니다. 병원 언급 판정은 다시 수행하고 원래 측정 시각을 보존한다.

live와 cache hit은 모두 lead·diagnosis·run·logical call 귀속을 가진 공급자 사용량 원장에 남긴다. 답변 HTTP attempt와 판정 HTTP attempt는 구분하고, token/search 단위를 알 수 없으면 0으로 만들지 않고 unknown으로 보존한다. 사용량 DB 저장 실패는 bounded Redis spool에서 1분 drain이 복구하지만 DB와 Redis가 함께 실패하면 관측이 빠질 수 있다. 이 원장은 업무 상태나 이메일 성공을 대신하지 않는다.

## 도입문의 초도 노출 진단 — Admin 내부 보관 전용

도입문의 리드의 진단은 AE의 콜용 자료다. 고객 채널로 나가는 자동 경로가 없고 운영자가 수동으로 내보낼 수도 없다. 판정은 `models/lead.py`의 `is_internal_inquiry` 한 곳이며, 리드의 `source`가 `INQUIRY`이거나 `clinic_type`이 도입문의 표식이면 내부용이다. 표식은 진료과 입력 같은 나중 단계가 덮어쓰지 않는다.

강제 지점은 다음과 같다.

- 생성: `services/inquiry_diagnosis.create_inquiry_diagnosis`가 `delivery_status=INTERNAL`로 진단을 만든다. 무료 진단 자리·신청자 잠금·공개 조회 토큰을 소비하지 않고, 감사 로그 detail에 `customer_delivery: False`·`report_token_minted: False`·`free_slot_claimed: False`·`applicant_locks_claimed: False`로 남는다. 리드당 1건이며 진료과·지역·키워드에 병원명이 섞이면 거절한다. 공개 접수의 자동 생성과 Admin 생성이 같은 함수를 쓴다.
- 발송 폴러: 1분 드레인의 발송 대상 질의가 `source`와 `clinic_type`을 SQL에서 함께 걸러 내부용 행을 집지 않는다(`workers/lead_diagnosis_tasks._deliveries_to_send`).
- 최종 방어선: `services/lead_delivery`의 `deliver_report`와 `sweep_stuck_deliveries`는 내부용 리드를 만나면 과거에 `PENDING`·`SENDING`으로 잘못 남은 행까지 `INTERNAL`로 정리해 영구 hold한다. 이미 `SENT`인 행은 건드리지 않는다.
- 수동 재발송: `POST /admin/leads/{id}/retry-report-delivery`가 403으로 거절하고 `rearm_report_delivery`도 같은 이유로 물러난다 — 「도입문의 리포트는 Admin 내부 보관 전용이라 고객 재발송할 수 없습니다.」
- 공개 링크: 조회 토큰 행이 어떤 이유로 존재하더라도 내부용 리드의 공개 조회는 404다(`api/public/diagnosis`). 내부용 리포트는 인증된 Admin 경로(`/api/admin/leads/{lead_id}/diagnoses/{diagnosis_id}/report`)에서만 연다.
- Admin 화면: 리드 목록이 「콜용 / 고객 미발송」으로 표시하고 고객 발송 축 배지는 `내부 보관`이며, 한 줄 안내도 콜용 보고서의 상태만 말한다. 신청자 잠금을 잡지 않았으므로 잠금 해제 버튼도 나오지 않는다(`admin/lib/lead-diagnosis-status.ts`, `admin/app/leads/page.tsx`).

측정과 PDF는 무료 진단과 같은 작업·같은 재시도 경계를 쓴다. 다만 내부용 진단의 측정·PDF 실패는 고객이 결과를 기다리는 상태가 아니므로 발송 복구를 찾지 않는다. 접수 단계에서 자동 생성이 거절됐거나 입력이 비어 있었을 때 사람이 하는 일은 [도입문의 접수 자동 처리](inquiry-intake-automation.md)에 있다.

## 링크와 개인정보

조회 토큰은 진단 UUID에서 HMAC으로 도출하고 pepper를 적용한 hash를 DB에 저장한다. 기본 30일 만료이며 폐기·열람 이력을 관리한다. 무료 진단 파일은 서버가 stream하며 no-store/noindex/no-referrer 정책을 적용한다. 유료 보고서의 GCS signed redirect와 다른 경로다.

평문 연락처·이메일은 Admin에서 확인하고 Slack에서는 마스킹한다. 조회 토큰·원문 답변·키를 알림이나 점검 로그에 넣지 않는다. 개인정보 파기 후에도 중복 제한 hash는 남을 수 있으며 명시적인 운영자 해제 경로가 따로 있다. 기본 보존 기간은 180일이며 외부 파일 파기 성공과 DB 파기 상태를 구분한다.

## 운영자가 개입할 때

- 측정/PDF/이메일 중 어느 단계가 실패했는지 먼저 확인한다. 저장된 답변이 있는 판정 실패나 이미 만들어진 PDF 때문에 전체 측정을 반복하지 않는다.
- 이메일 응답이 불확실하거나 24시간 창을 넘겼다면 provider 기록과 중복 위험을 확인한 뒤 허용된 수동 재무장을 사용한다. 이 경로는 무료 진단 전용이다 — 도입문의 진단에는 고객 발송 단계가 없다.
- CRM 병원 전환과 계약 인수는 명시적인 Admin 작업이다. 월간 측정 cohort 등록과 동일하지 않다.
- 배포 설정은 `LEAD_LOCK_HASH_PEPPER`, `LEAD_REPORT_TOKEN_SECRET`, `RESEND_API_KEY`, 발신 주소·도메인 설정, Site BFF와 Backend 연결을 확인한다. 토큰·hash 키 교체는 기존 링크·중복 제한에 영향을 주므로 별도 이전 계획이 필요하다.
- 준비 검사는 `app.utils.production_readiness`의 설정·라우팅·drain 상태로 확인한다. 실제 신청·이메일·Slack 발송 검증은 실수신자를 확인한 별도 검증으로 구분한다. 문서 점검을 위해 운영 채널에 테스트 알림을 보내지 않는다.

근거: [접수 API](../../backend/app/api/public/diagnosis.py), [도입문의 접수](../../backend/app/api/public/leads.py), [진단 작업](../../backend/app/workers/lead_diagnosis_tasks.py), [판정·캐시](../../backend/app/services/lead_diagnosis_engine.py), [공급자 사용량](../../backend/app/services/provider_usage.py), [발송 제어](../../backend/app/services/lead_delivery.py), [내부용 진단 생성](../../backend/app/services/inquiry_diagnosis.py), [내부용 판정](../../backend/app/models/lead.py), [Admin 리드 API](../../backend/app/api/admin/leads.py), [토큰](../../backend/app/services/lead_report_token.py), [파기](../../backend/app/services/lead_privacy.py). 도입문의 접수 자동 처리의 정본은 [inquiry-intake-automation.md](inquiry-intake-automation.md)다.
