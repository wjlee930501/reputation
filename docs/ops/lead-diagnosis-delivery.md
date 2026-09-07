# 무료 진단 접수·리포트 전달

문서 버전: **2.0** · 갱신일: **2026-09-07 (Asia/Seoul)**
구현 기준: **`345a6420998bcba21169519cf5ad77600cbfa94b`**

무료 진단은 신청자에게 링크를 자동 이메일로 전달한다. AE가 직접 전달하는 유료 월간 보고서와 구분한다. 다음 값은 코드 기본값이며 운영 설정으로 달라질 수 있다.

## 정상 흐름

1. `/ai-diagnosis` 신청이 Site의 같은 출처 BFF를 거쳐 Backend로 전달된다. 동의·허니팟·입력·중복 병원 identity·일일 슬롯을 검사한다.
2. 리드, 진단, 중복 제한 hash, 토큰을 하나의 DB 트랜잭션으로 저장한다. 슬롯 카운터는 08:00 KST 기준이며 중복으로 저장이 취소되면 슬롯도 rollback한다.
3. 접수 Slack은 별도 전달한다. 브로커 전달이 유실되어도 1분 drain이 DB의 PENDING 상태를 회수한다.
4. 접수 시 저장한 질문·모델·정책으로 측정하고, 유효한 결과에서 PDF를 생성한 뒤 이메일 전달로 넘어간다.
5. 이메일은 PDF 첨부 대신 상태/리포트 링크를 보낸다. delivery UUID가 같은 발송의 멱등성 키다.

## 독립적인 상태와 재시도

| 단계 | 성공의 의미 | 실패·복구 경계 |
|---|---|---|
| 접수 알림 | 알림 전송 상태 SENT | 업무 접수와 별도. PENDING 회수·전송 실패 기록 |
| 측정 | SUCCEEDED 또는 기준을 만족한 PARTIAL | 실행 최대 3회와 lease 회수. 예산·정책 변경 차단은 무조건 재큐하지 않음 |
| PDF | 최신 실행 결과의 artifact 저장 후 READY | 보고서 생성 최대 3회. BLOCKED는 정상 전달로 취급하지 않음 |
| 이메일 | provider 응답을 기록한 SENT | 기본 총 4회: 최초, +5분, +30분, +4시간. 최초 발송의 24시간 창 밖은 자동 재발송하지 않음 |

기본 3질문 × 2플랫폼 × 3반복으로 18개 답변을 계획한다. PARTIAL은 단순히 일부 답변이 있다는 뜻이 아니다. **각 플랫폼에서 계획 수−1 이상 확정**이어야 하므로 기본값에서는 각각 8/9 이상이어야 한다. 실패·미확정 응답을 미언급으로 분모에 넣지 않는다.

답변은 질문·모델·플랫폼·정책·반복을 구분한 7일 공유 캐시에서 재사용할 수 있다. 병원 언급 판정은 다시 수행하고 원래 측정 시각을 보존한다. 무료 진단과 유료 SoV는 동시 호출 pool이 분리되어 있지만 현재 같은 Worker 서비스가 큐를 소비한다.

## 링크와 개인정보

조회 토큰은 진단 UUID에서 HMAC으로 도출하고 pepper를 적용한 hash를 DB에 저장한다. 기본 30일 만료이며 폐기·열람 이력을 관리한다. 무료 진단 파일은 서버가 stream하며 no-store/noindex/no-referrer 정책을 적용한다. 유료 보고서의 GCS signed redirect와 다른 경로다.

평문 연락처·이메일은 Admin에서 확인하고 Slack에서는 마스킹한다. 조회 토큰·원문 답변·키를 알림이나 점검 로그에 넣지 않는다. 개인정보 파기 후에도 중복 제한 hash는 남을 수 있으며 명시적인 운영자 해제 경로가 따로 있다. 기본 보존 기간은 180일이며 외부 파일 파기 성공과 DB 파기 상태를 구분한다.

## 운영자가 개입할 때

- 측정/PDF/이메일 중 어느 단계가 실패했는지 먼저 확인한다. 이미 만들어진 PDF 때문에 전체 측정을 반복하지 않는다.
- 이메일 응답이 불확실하거나 24시간 창을 넘겼다면 provider 기록과 중복 위험을 확인한 뒤 허용된 수동 재무장을 사용한다.
- CRM 병원 전환과 계약 인수는 명시적인 Admin 작업이다. 월간 측정 cohort 등록과 동일하지 않다.
- 배포 설정은 `LEAD_LOCK_HASH_PEPPER`, `LEAD_REPORT_TOKEN_SECRET`, `RESEND_API_KEY`, 발신 주소·도메인 설정, Site BFF와 Backend 연결을 확인한다. 토큰·hash 키 교체는 기존 링크·중복 제한에 영향을 주므로 별도 이전 계획이 필요하다.
- 준비 검사는 `app.utils.production_readiness`의 설정·라우팅·drain 상태로 확인한다. 실제 신청·이메일·Slack 발송 검증은 실수신자를 확인한 별도 검증으로 구분한다. 문서 점검을 위해 운영 채널에 테스트 알림을 보내지 않는다.

근거: [접수 API](../../backend/app/api/public/diagnosis.py), [진단 작업](../../backend/app/workers/lead_diagnosis_tasks.py), [판정·캐시](../../backend/app/services/lead_diagnosis_engine.py), [발송 제어](../../backend/app/services/lead_delivery.py), [토큰](../../backend/app/services/lead_report_token.py), [파기](../../backend/app/services/lead_privacy.py).
