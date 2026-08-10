# 무료 진단 접수·리포트 전달 운영 절차

## 정상 흐름

1. 신청자가 `/ai-diagnosis`에서 병원·담당자·수신 이메일을 입력하고 확인 모달에서 최종 확정한다.
2. API가 리드, 진단, 1회 제한 해시, 상태/리포트 토큰을 한 트랜잭션으로 저장한다.
3. 응답이 끝난 뒤 접수 Slack 태스크를 큐에 넣는다. 브로커가 잠시 실패해도 1분 폴러가
   `notification_status=PENDING`인 접수를 다시 회수한다.
4. 폴러가 진단 측정 → PDF 생성 → 이메일 발송을 순서대로 실행한다.
5. 이메일은 PDF를 직접 첨부하지 않고 서명된 상태/리포트 링크를 전달한다. 발송 ID가 Resend
   멱등성 키이므로 같은 발송을 재시도해도 중복 메일을 만들지 않는다.

## 운영에서 확인할 상태

| 단계 | 정상 상태 | 최종 실패 시 |
|---|---|---|
| 접수 Slack | `SalesLead.notification_status=SENT` | `FAILED`와 오류를 저장 |
| AI 측정 | `LeadDiagnosis.execution_status=SUCCEEDED` 또는 `PARTIAL` | 3회 소진 후 `FAILED` + Slack 즉시 알림 |
| PDF | `report_status=READY` | 3회 소진 후 `BLOCKED` |
| 이메일 | `delivery_status=SENT` | 재시도/24시간 멱등성 창 소진 후 중단 + Slack 즉시 알림 |

신청 정보의 원문 연락처와 이메일은 Admin에서만 확인한다. `#mkt-reputation`에는 병원 맥락과
마스킹된 연락처만 보낸다.

## 배포 전 필수 확인

1. Secret Manager의 `LEAD_LOCK_HASH_PEPPER`, `LEAD_REPORT_TOKEN_SECRET`,
   `RESEND_API_KEY` 최신 버전이 활성 상태인지 확인한다. 앞의 두 값은 운영 개시 후 교체하지 않는다.
2. Resend에서 `reputation.motionlabs.kr` 발신 도메인이 인증됐고
   `LEAD_MAIL_FROM=Re:putation <noreply@reputation.motionlabs.kr>`를 보낼 수 있는지 확인한다.
3. `SLACK_WEBHOOK_URL`이 `#mkt-reputation`에 연결됐는지 확인한다. Incoming Webhook의 채널은
   코드에서 변경할 수 없다.
4. 배포 이미지 안에서 `python -m app.utils.production_readiness`를 실행해
   `lead_delivery_configured`, 필수 태스크 라우팅, `drain-lead-diagnoses` 스케줄이 모두 `true`인지
   확인한다.
5. 내부 테스트 병원 1건을 접수해 다음을 확인한다.
   - 신청 API가 Slack 지연과 무관하게 즉시 상태 URL을 반환한다.
   - 접수 Slack에 병원명·지역·진료과·키워드·마스킹 연락처·Admin 링크가 한 번만 온다.
   - 15분 안에 이메일이 도착하고 링크에서 PDF가 열린다.
   - 같은 발송 태스크를 재실행해도 이메일이 한 통만 도착한다.

실제 키 값이나 평문 연락처는 점검 로그에 출력하지 않는다.
