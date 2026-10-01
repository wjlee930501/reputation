# 월간 리포트 템플릿 갱신 — 숫자는 그대로, 문구·디자인만 새 버전

문서 버전 1.0 · 2026-10-01 · 코드 기준 `feature/monthly-report-plain-language`

원장용 월간 PDF 템플릿(`doctor_report_v3.html`)을 바꿔 배포한 뒤, 이미 만든 달의 리포트를
새 문구·디자인으로 다시 찍는 절차다. **이 절차는 숫자를 하나도 바꾸지 않는다.** 숫자를 지킬 수
없다는 판정이 하나라도 나오면 새 버전을 만들지 않는다.

## 왜 일반 재생성을 쓰지 않는가

일반 재생성(`rebuild=true`, `MANUAL_REBUILD`)은 마감 시각을 **지금**으로 잡고 현재 행을 다시 읽는다.
늦은 발행·공개 철회·사후검수 완료·새 노출 행동·Essence 변화가 계약 이행 편수, 사후검수 건수,
공개 글 목록, 인용 매칭, 전략 요약을 바꾸고, 노출 행동을 새 리포트에 연결하는 부수효과도 있다.

템플릿 갱신(`rebuild=true&template_only=true`, `TEMPLATE_REFRESH`)은 다르게 동작한다.

- 마감 시각은 대체할 버전의 `content_summary.contract_timing.observed_at`이다.
- `sov_summary`·`content_summary`(운영·계약 시점·귀속·인용·전략)·`essence_summary`·
  `quality`·각 건수·`manifest_id`·`cutoff_at`·전달 차단 사유를 대체할 버전에서 그대로 옮긴다.
- 현재 행에서 읽는 것은 원장에게 보여 줄 공개 글 제목(현재 공개분), 동결된 관측의 답변 발췌,
  초기 측정 참고선, 누적 발행 편수(최초 공개 시각 기준이라 바뀌지 않는다)뿐이다.
- 노출 행동 연결·매니페스트 마감·Essence 재집계가 없다. 공급자를 호출하지 않는다.
- 새 버전은 `supersedes_report_id`로 이전 버전을 가리키고, 사유 감사(`admin_audit_logs`, `mode:
  TEMPLATE_REFRESH`)와 `GENERATE_MONTHLY_REPORT` OperationRun을 남긴다. 원장 PDF는 일반 생성과
  같은 검증(`render_validated_doctor_pdf`·`MonthlyReportArtifact`)을 통과해야 붙는다.

워커는 만들기 직전에 같은 판정(`build_monthly_template_refresh_plan`)을 다시 하고, `PASS`가 아니면
쓰기 없이 실패로 닫는다(`template_refresh_refused`, 재시도 없음).

## 판정

| 구분 | 의미 | 예 |
|---|---|---|
| BLOCKER | 지금은 만들 수 없음 | 측정 복구 기간(매월 1~7일 KST)에 측정이 덜 끝난 병원(`RECOVERY_PENDING` — 품질이 COMPLETE가 아니면 자동 복구가 다시 측정·재생성한다), 진행 중인 `GENERATE_MONTHLY_REPORT`·`SCHEDULED_MONTHLY_REPORT`·`RUN_SOV`, 매니페스트 미마감, 검증된 원장 PDF 없음, 저장 요약의 필수 칸 누락 |
| DIFF | 숫자가 어긋남 — 만들지 않음 | 같은 마감으로 다시 센 발행·계약 이행·조기/지연 편수가 저장값과 다름, AE 토킹 포인트의 숫자 순서가 다름, 원장 뷰 핵심 숫자(이번 달·지난달 비율, 약속한 글 칸)가 저장값과 다름, 매니페스트 요약 건수가 다름, 옛/새 원장 PDF의 숫자 사실이 다름 |
| WARN | 출력 숫자와 무관한 기록 | 동결 관측으로 다시 계산한 측정 요약이 저장값과 다름(출력은 저장값을 쓴다) |

원장 PDF의 숫자 사실은 `33.3%`처럼 `%`가 붙은 값, `12편 중 12편`, `6번 중 2번` 꼴이다. 옛 템플릿의
고정 문구(`0–100%`, `95% 구간`)·증감 표기(`+33.4%p`)와 새 부록의 설명 예시(`6번 중 2번`)는 뺀다.
옛 PDF의 사실이 새 PDF에 모두 있고, 새 PDF에 새 사실이 생기지 않아야 PASS다.

## 운영 절차 (2026년 9월분 예시)

전제: 새 템플릿이 API·Worker에 배포됐다. 운영 DB는 사설 IP라 명령은 `reputation-migrate` Job을
실행 단위 override로 재사용해 돌린다(Job 정의는 바뀌지 않는다). 컨테이너 entrypoint는 인자가
있으면 그 명령을 그대로 실행한다.

1. 측정 복구 기간(1~7일)에도 시작할 수 있다. 측정이 전부 확정된(품질 COMPLETE) 병원은 자동 복구
   대상이 아니라 숫자가 바뀌지 않는다. 덜 끝난 병원은 사전 확인이 `RECOVERY_PENDING`으로 막으므로
   8일 이후 다시 돌린다.

2. 사전 확인(읽기 전용 — 세션 롤백, 저장소 쓰기·공급자 호출 없음, 옛 원장 PDF는 GCS에서 읽기만):

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.monthly_template_refresh,precheck,--year,2026,--month,9
   gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="reputation-migrate"' \
     --project mso-platform-481505 --freshness=30m --order=asc --format='value(textPayload)'
   ```

   병원별 `PASS/DIFF/BLOCKED` 표와 사유가 찍힌다. DIFF·BLOCKED 병원은 원인을 확인하기 전까지 손대지
   않는다(일반 재생성으로 대체하지 않는다 — 숫자가 바뀐다).

3. 실행. PASS 병원만, 한 곳씩, Admin API와 같은 경로로 요청하고 작업 종료 뒤 사후 확인까지 한다.
   하나라도 실패·불일치면 그 자리에서 멈춘다. `--confirm` 없이 먼저 대상만 확인한다.

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.monthly_template_refresh,execute,--year,2026,--month,9,--api-base,https://reputation.motionlabs.kr,--reason,새 원장 보고서 템플릿 반영(숫자 변경 없음)
   # 대상 목록이 맞으면 같은 명령에 --confirm을 붙여 다시 실행한다.
   ```

   요청에는 `X-Admin-Key`(Job 환경의 `ADMIN_SECRET_KEY`)와 `X-Admin-Actor-System:
   monthly-template-refresh`가 붙고, 감사에는 `system:monthly-template-refresh`로 남는다. 요청 키는
   `template-refresh:<병원>:<YYYY-MM>:v<대체할 버전>`이라 같은 원본 버전으로 다시 실행해도 새 버전이
   두 번 생기지 않는다. **`reputation-migrate` Job에 `ADMIN_SECRET_KEY`가 주입돼 있지 않으면** 실행 단계가
   요청 없이 멈춘다 — 그때는 Job 정의를 바꾸지 말고, 운영 담당자가 사전 확인에서 PASS인 병원마다
   아래 API를 같은 사유·요청 키로 한 곳씩 호출하고 작업 종료를 확인한 뒤 4단계 사후 확인을 돌린다
   (Admin 화면에는 이 모드의 버튼이 없다).

   ```bash
   curl -sS -X POST "https://reputation.motionlabs.kr/api/v1/admin/hospitals/<HOSPITAL_ID>/operations/generate-monthly-report?year=2026&month=9&rebuild=true&template_only=true" \
     -H "X-Admin-Key: $ADMIN_SECRET_KEY" -H "X-Admin-Actor-System: monthly-template-refresh" \
     -H "Idempotency-Key: template-refresh:<HOSPITAL_ID>:2026-09:v<VERSION>" \
     -H "Content-Type: application/json" -d '{"reason":"새 원장 보고서 템플릿 반영(숫자 변경 없음)"}'
   ```

4. 사후 확인(최신 버전과 그것이 대체한 버전의 저장 숫자·원장 PDF 숫자 사실 비교):

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.monthly_template_refresh,postcheck,--year,2026,--month,9
   ```

5. 전달. 새 버전은 `customer_ready=false`로 태어나며, **이미 원장님께 전달한 달은 AE가 새 버전을 다시
   전달해야 반영된다**(월간 재생성 계약). 전달 기록은 이전 버전에 그대로 남는다.

## 되돌리기

새 버전은 이전 버전을 지우지 않는다. 문제가 생기면 이전 버전 PDF를 그대로 다시 전달할 수 있고,
새 버전을 원장님께 전달하지 않으면 된다. 템플릿 자체를 되돌릴 때는 코드 롤백 뒤 같은 절차로
다시 갱신한다.

## 남은 한계

- 원장 PDF의 공개 글 제목은 현재 공개분이다. 9월 이후 철회된 글은 새 PDF 목록에서 빠진다(숫자 칸은
  저장값 그대로). 철회 글 제목을 원장에게 다시 보이지 않는 것이 공개 계약과 맞다.
- 누적 발행 편수(“지금까지 올린 글은 모두 N편”)는 옛 PDF에 없던 새 숫자다. 최초 공개 시각
  기준이라 다시 만들어도 같고, PDF 숫자 비교 대상에서 제외된다(`편 중` 꼴이 아니다).
- 옛 원장 PDF를 GCS에서 읽지 못하면 BLOCKED다. Job 서비스 계정에 리포트 버킷 읽기 권한이 필요하다.
