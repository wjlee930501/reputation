# 무료 진단 접수·리포트 전달

문서 버전: **3.2** · 갱신일: **2026-09-07 (Asia/Seoul)**
소스 기준선: **`39dc1f8a98abe9193a8e2202395d2272c370fe8e`**
구현 상태: **기준선 위 작업 로컬 검증 완료. 운영 배포 전**

무료 진단은 신청자에게 링크를 자동 이메일로 전달한다. AE가 직접 전달하는 유료 월간 보고서와 구분한다. 다음 값은 코드 기본값이며 운영 설정으로 달라질 수 있다.

## 정상 흐름

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
| 이메일 | provider 응답을 기록한 SENT | 기본 총 4회: 최초, +5분, +30분, +4시간. 최초 발송의 24시간 창 밖은 자동 재발송하지 않음 |

기본 3질문 × 2플랫폼 × 3반복으로 18개 답변을 계획한다. PARTIAL은 단순히 일부 답변이 있다는 뜻이 아니다. **각 플랫폼에서 계획 수−1 이상 확정**이어야 하므로 기본값에서는 각각 8/9 이상이어야 한다. 실패·미확정 응답을 미언급으로 분모에 넣지 않는다.

답변은 질문·모델·플랫폼·정책·반복을 구분한 7일 공유 캐시에서 재사용할 수 있다. 같은 cache identity의 동시 요청은 Redis의 갱신 가능한 single-flight lease와 결과 인계로 한 번의 live 답변 구매에 모으고, owner가 끝나지 못하면 다른 요청이 인계한다. Redis 장애에는 fail-open하므로 중복 방지가 절대 보장은 아니다. 이 합치기는 같은 cache key에만 적용된다. provider별 leadgen semaphore는 프로세스 내부에서만 동작하므로 여러 Worker 인스턴스와 서로 다른 key가 같은 API key를 호출할 때의 전역 상한은 아니다. 병원 언급 판정은 다시 수행하고 원래 측정 시각을 보존한다.

live와 cache hit은 모두 lead·diagnosis·run·logical call 귀속을 가진 공급자 사용량 원장에 남긴다. 답변 HTTP attempt와 판정 HTTP attempt는 구분하고, token/search 단위를 알 수 없으면 0으로 만들지 않고 unknown으로 보존한다. 사용량 DB 저장 실패는 bounded Redis spool에서 1분 drain이 복구하지만 DB와 Redis가 함께 실패하면 관측이 빠질 수 있다. 이 원장은 업무 상태나 이메일 성공을 대신하지 않는다.

## 링크와 개인정보

조회 토큰은 진단 UUID에서 HMAC으로 도출하고 pepper를 적용한 hash를 DB에 저장한다. 기본 30일 만료이며 폐기·열람 이력을 관리한다. 무료 진단 파일은 서버가 stream하며 no-store/noindex/no-referrer 정책을 적용한다. 유료 보고서의 GCS signed redirect와 다른 경로다.

평문 연락처·이메일은 Admin에서 확인하고 Slack에서는 마스킹한다. 조회 토큰·원문 답변·키를 알림이나 점검 로그에 넣지 않는다. 개인정보 파기 후에도 중복 제한 hash는 남을 수 있으며 명시적인 운영자 해제 경로가 따로 있다. 기본 보존 기간은 180일이며 외부 파일 파기 성공과 DB 파기 상태를 구분한다.

## 운영자가 개입할 때

- 측정/PDF/이메일 중 어느 단계가 실패했는지 먼저 확인한다. 저장된 답변이 있는 판정 실패나 이미 만들어진 PDF 때문에 전체 측정을 반복하지 않는다.
- 이메일 응답이 불확실하거나 24시간 창을 넘겼다면 provider 기록과 중복 위험을 확인한 뒤 허용된 수동 재무장을 사용한다.
- CRM 병원 전환과 계약 인수는 명시적인 Admin 작업이다. 월간 측정 cohort 등록과 동일하지 않다.
- 배포 설정은 `LEAD_LOCK_HASH_PEPPER`, `LEAD_REPORT_TOKEN_SECRET`, `RESEND_API_KEY`, 발신 주소·도메인 설정, Site BFF와 Backend 연결을 확인한다. 토큰·hash 키 교체는 기존 링크·중복 제한에 영향을 주므로 별도 이전 계획이 필요하다.
- 준비 검사는 `app.utils.production_readiness`의 설정·라우팅·drain 상태로 확인한다. 실제 신청·이메일·Slack 발송 검증은 실수신자를 확인한 별도 검증으로 구분한다. 문서 점검을 위해 운영 채널에 테스트 알림을 보내지 않는다.

근거: [접수 API](../../backend/app/api/public/diagnosis.py), [진단 작업](../../backend/app/workers/lead_diagnosis_tasks.py), [판정·캐시](../../backend/app/services/lead_diagnosis_engine.py), [공급자 사용량](../../backend/app/services/provider_usage.py), [발송 제어](../../backend/app/services/lead_delivery.py), [토큰](../../backend/app/services/lead_report_token.py), [파기](../../backend/app/services/lead_privacy.py).
