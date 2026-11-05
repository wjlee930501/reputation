# 월간 리포트 템플릿 갱신 — 숫자는 그대로, 문구·디자인만 새 버전

문서 버전 1.2 · 2026-11-05 · 코드 기준 `claude/report-competitor-regen-6b360d`(경쟁 병원 반영 절 추가)

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
| BLOCKER | 지금은 만들 수 없음 | 측정 복구 창(1일 00:15~07:00 KST)에 측정이 덜 끝난 병원(`RECOVERY_PENDING` — 품질이 COMPLETE가 아니면 자동 복구가 다시 측정·재생성한다), 진행 중인 `GENERATE_MONTHLY_REPORT`·`SCHEDULED_MONTHLY_REPORT`·`RUN_SOV`, 매니페스트 미마감, 검증된 원장 PDF 없음, 저장 요약의 필수 칸 누락 |
| DIFF | 숫자가 어긋남 — 만들지 않음 | 같은 마감으로 다시 센 발행·계약 이행·조기/지연 편수가 저장값과 다름, AE 토킹 포인트의 숫자 순서가 다름, 원장 뷰 핵심 숫자(이번 달·지난달 비율, 약속한 글 칸)가 저장값과 다름, 매니페스트 요약 건수가 다름, 원장 뷰·새 PDF의 숫자 사실이 저장값과 다름 |
| WARN | 출력 숫자와 무관한 기록 | 동결 관측으로 다시 계산한 측정 요약이 저장값과 다름(출력은 저장값을 쓴다) |

숫자 대조는 PDF 본문의 정규식이 아니라 **구조 사실**로 한다(2026-10-07 개편). 문구만 바꾼 달
(`16.7~30.0%`→`16.7%~30.0%`, `150번 중 150번`→`150회 전부`)이 숫자 차이로 막혀 핫픽스가 3번
필요했기 때문이다.

- 정본 사실: 템플릿이 그리는 원장 뷰의 값(이번 달·지난달 비율, 약속한 글 칸, 질문 수·누적 발행·출처 답변
  수, 부록 행의 `N번 중 M번`, 처음 측정 참고선)을 키별로 뽑는다(`doctor_view_facts`). 대체할 버전의 저장
  요약에서 같은 방식으로 다시 만든 사실(`stored_doctor_facts`)과 **키 단위로** 대조한다 — 값이 다르거나
  저장 사실이 뷰에서 빠지면 DIFF다. 옛 PDF 본문은 비교하지 않는다(대체할 버전이 검증된 원장 PDF를
  가졌는지만 본다).
- 이차 확인: 정본 사실과 저장된 답변 비율 범위(`ci95`)·확인 횟수(`observation_adequacy`)·측정 답변 수의
  숫자가 **새 PDF 본문에 찍혀 있는지** 본다(`pdf_fact_problems`). 사실마다 템플릿이 찍는 토큰을 공백만 빼고 그대로 요구한다(`12편 중 3편`·`6번 중 2번`·`N개 중 M개`·`N건 중 M건`). 비율은 범위 끝처럼 `%`가 빠질 수 있어 숫자만 정확히 본다. 확인 횟수처럼 문장 틀이 바뀌는 값은 온전한 정수만 본다(`150번 중 150번`→`150회 전부` 통과). 저장된 사실을 PDF가 조용히 버려도 DIFF다.
- 지난달 참고 값은 저장 요약이 아니라 지난달 보고서의 저장 비율이 근거다. 새 뷰의 값과 같아야 한다.

## 운영 절차 (2026년 9월분 예시)

전제: 새 템플릿이 API·Worker에 배포됐다. 운영 DB는 사설 IP라 명령은 `reputation-migrate` Job을
실행 단위 override로 재사용해 돌린다(Job 정의는 바뀌지 않는다). 컨테이너 entrypoint는 인자가
있으면 그 명령을 그대로 실행한다.

1. 측정 복구 창(1일 00:15~07:00 KST)에도 시작할 수 있다. 측정이 전부 확정된(품질 COMPLETE) 병원은 자동 복구
   대상이 아니라 숫자가 바뀌지 않는다. 덜 끝난 병원은 사전 확인이 `RECOVERY_PENDING`으로 막으므로
   측정 마감(07:00) 이후 다시 돌린다.

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
   `--allow-recovery-pending`으로 측정 미완료 병원을 갱신하면 월간 작업이 늘 `PARTIAL`(단계 `BLOCKED`,
   `MONTHLY_REPORT_BLOCKED`)로 끝난다 — 전달 조건(측정 완료)이 아닐 뿐 갱신은 성공이다. 이때 **새 버전이
   실제로 생겼으면** 성공으로 보고 사후 확인을 계속한다. `FAILED`·시간 초과·새 버전 없음, 그리고 사후
   확인 DIFF/BLOCKED(원장 PDF 검증 실패 포함)에서만 멈춘다. 플래그가 없으면 `PARTIAL`에서 멈춘다.

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

4. 사후 확인(최신 버전과 그것이 대체한 버전의 저장 숫자 비교, 새 원장 PDF에 저장 숫자 사실이 찍혔는지 확인):

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
- 누적 발행 편수(“지금까지 올린 글은 모두 N편”)와 처음 측정 참고선은 저장 요약에 없고 현재 행에서
  읽는다. 최초 공개 시각 기준이라 다시 만들어도 같고, 저장값 대조 대신 PDF에 찍혔는지만 본다.
- 정본 사실의 PDF 확인은 숫자가 찍혔는지만 본다 — 같은 숫자가 다른 뜻의 칸에 찍혀도 알 수 없다.
  숫자의 의미는 뷰의 키 대조(저장값↔뷰)가 지킨다.
- 옛 원장 PDF를 GCS에서 읽지 못하면 BLOCKED다. Job 서비스 계정에 리포트 버킷 읽기 권한이 필요하다.

## 경쟁 병원 반영 — 경쟁사 재판정 → 경쟁 현황만 새 버전 (2026-11-05)

경쟁 병원을 **뒤늦게** 등록한 달의 보고서에서 04절 경쟁 현황(1면 카드·P6 계획의 경쟁 표기·부록의 경쟁 열·
`facts["competitor.*"]`, AE PDF의 측정 키워드별 경쟁 열)만 다시 만드는 절차다. 템플릿 갱신과 같은 보존
(측정 요약·귀속·인용·발행 숫자는 저장값 그대로) 위에서, 경쟁 사실만 현재 판정으로 다시 계산한다.

### 왜 새 유료 측정이 아닌가

월간 측정은 다음 달 1일 07:00 KST 뒤 새 유료 단계(답변 구매·**얼린 숫자를 바꾸는** 판정 체크포인트)를 하지 않는다.
경쟁 병원 반영은 그 규칙에 대한 **명시적·한정된 예외**다 — 유료 판정 호출을 하긴 하지만, (1) 답변을 새로 사지
않고 그 달 고정 매니페스트가 점수에 쓴 확정 답변의 저장 원문(`SovRecord.raw_response`)만 읽으며, (2) 대상은 보고서
스냅샷이 얼린 관측(`render_inputs.measurement.cells[].attempts[].record_id`)으로만 한정하고(스냅샷이 없으면 아무것도
묻지 않는다), (3) 자사 판정·측정 슬롯·셀 상태·분모·노출률 등 얼린 측정 사실은 손대지 않고 경쟁 현황(얼린 측정 사실이
아닌, 현재 행에서 읽는 면)만 바꾼다. 묻는 것은 측정 때 등록돼 있지 않아 물어보지 못한 이름뿐이고 판정은 측정과
**같다**(`sov_engine._parse_competitors`, 같은 프롬프트·사전 필터·`judgment:competitors`). 그래서 마감이 지난 달도
대상이다(`services/competitor_rejudge.py` 모듈 설명).

### 동작

1. **재판정** `REJUDGE_COMPETITORS`(Celery `app.workers.competitor_refresh.rejudge_month_competitors`, `sov` 큐, 서명 디스패치,
   OperationRun). 기록마다 지금 등록된 경쟁사 중 **확정 판정(bool)이 없는 이름만** 묻고 `competitor_mentions`에
   **합친다** — 측정 당시 판정은 덮지 않고, 등록에서 빠진 이름은 지우지 않는다(보고서 조립이 등록 목록으로 거른다).
   판정 실패(장애·빈 답·요청한 이름 누락)는 셀 단위로 빠진 이름만 최대 3회 다시 묻고, 그래도 없으면
   `is_mentioned=null`(`verdict=UNRESOLVED`)로 남겨 0으로 세지 않으며, 다음 실행이 다시 묻는다. 비용은 `sov`
   카테고리(월간 측정과 같다)로 대상 기록 수 × 1회를 예약하고 **실제 HTTP 호출 수**만 정산한다 — 예약 단위(기록 수)·
   판정 시도(기록당 최대 3회)·HTTP 시도(판정 1회 안에서 전송 재시도 최대 3회, 즉 기록당 최대 9회)는 서로 다른 수다.
   기록 단위로 커밋하고 쓰기 직전에 행을 잠가 다시 계산하므로 다시 돌려도 같은 이름을 두 번 사지 않는다(멱등).
   보고서 조립은 **지금 등록된** 경쟁사만 등록 표기로 센다(`services/competitor_scope.py`, 원장 뷰·전략 요약 공용) — 등록에서 뺀
   이름의 판정은 기록에 남아도 04절·전략 요약에 싣지 않고, "가나 의원"/"가나의원" 같은 표기 차이는 한 병원으로 합친다.
   같은 병원의 재판정은 세션 advisory lock(기다리지 않는 try-lock, 못 잡은 사본은 조용히 물러남)으로 직렬화되고, 부모
   종결과 자식 실행 행은 한 트랜잭션에 쓴 뒤 발행된다.
2. **부분 재생성** = 템플릿 갱신 사유 `COMPETITOR_REFRESH`(재판정 실행의 자식 OperationRun `GENERATE_MONTHLY_REPORT`,
   `parent_run_id`로 체인, 인자 `[…, rebuild=True, False, template_only=True, False, competitor_refresh=True]`).
   v4 스냅샷이라도 얼린 뷰를 그대로 쓰지 않고 저장 숫자로 뷰를 다시 만들어(`tasks._rebuild_v4_view_from_stored`)
   경쟁 현황이 지금 판정·등록 경쟁사를 반영하게 한다. 판정: 경쟁 사실(`competitor.*`)을 **뺀** 모든 얼린 사실이
   같아야 PASS(`COMPETITOR_REFRESH_FACTS` DIFF면 아무것도 쓰지 않는다), 경쟁 사실의 변화는 WARN
   `COMPETITOR_FACTS_RECOMPUTED`로만 기록한다. 저장 전략 요약(`content_summary.strategy.query_targets[*].competitor_outcomes`,
   AE 스냅샷 포함)도 같은 기록으로 다시 센다. 원장 PDF 검증을 통과해야 새 버전이 붙는다. 재판정 셀 일부가
   미확정이어도 재생성을 막지 않는다. 재판정이 비용 보류로 아무것도 묻지 못했을 때만(PARTIAL
   `COMPETITOR_REJUDGE_COST_BLOCKED`) 재생성을 부르지 않는다.
3. 새 버전은 `supersedes_report_id`로 이어지고 이전 버전·전달 기록은 그대로다. **다시 전달해야 반영된다.**

### Admin

보고서 탭 → 보고서 열기 → ‘4. 경쟁 병원 반영’. 등록 경쟁사 목록을 보여 주고(없으면 병원 정보 탭 링크), 버튼 한 번이
재판정 → 재생성 체인을 큐에 넣는다. 진행 중·완료(판정 보충 n건 · 미확정 m건 · 새 버전 번호)·실패를 같은 자리에서 보이며,
“새 버전은 다시 전달해야 반영됨” 경고를 언제나 붙인다. 등록 경쟁사 입력은 병원 정보 탭의 기존 저장 경로 그대로다.
API: `GET/POST /api/v1/admin/hospitals/{id}/reports/competitors/refresh?year=&month=`(POST는 `Idempotency-Key`와 actor
단언 필수, 등록 경쟁사 없음 `NO_REGISTERED_COMPETITORS`·보고서 없음 `NO_REPORT`·진행 중 `COMPETITOR_REFRESH_IN_FLIGHT`는 409).

### 운영 절차 (2026년 10월분 12곳)

전제: 이 코드가 API·Worker에 배포됐고, AE가 병원 정보 탭에서 각 병원의 경쟁 병원을 등록했다. 10월분은 아직 원장
미전달이라 새 버전을 그대로 전달하면 된다.

1. 사전 확인(읽기 전용). 병원마다 등록 경쟁사 수·보고서가 쓴 답변 수·판정 없는 답변 수와 숫자 보존 판정이 찍힌다.
   등록 경쟁사가 없는 병원은 `BLOCKED NO_REGISTERED_COMPETITORS`다 — 등록 뒤 다시 돌린다.

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.monthly_competitor_refresh,precheck,--year,2026,--month,10
   ```

2. 실행. PASS 병원만 한 곳씩 Admin API와 같은 경로로 요청하고, 재판정과 자식 보고서 실행이 끝나면 사후 확인까지 한다.
   하나라도 실패·불일치면 그 자리에서 멈춘다. `--confirm` 없이 먼저 대상만 확인한다. 요청 키는
   `competitor-refresh:<병원>:<YYYY-MM>:v<대체할 버전>`이라 같은 원본 버전으로 다시 실행해도 체인이 두 번 생기지 않는다.
   끝난 실행(비용 보류·실패) 뒤 같은 버전에서 다시 시도하려면 `--retry-suffix retry2`처럼 키를 바꾼다. 한 병원의 예약은
   ‘판정 없는 답변 수’(보통 셀 수 ≤ 150) × 1회이고 `sov` 일일 한도 안에서 잡힌다(HTTP 시도는 전송 재시도로 그보다 많을 수 있다).

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.monthly_competitor_refresh,execute,--year,2026,--month,10,--api-base,https://reputation.motionlabs.kr
   # 대상 목록이 맞으면 같은 명령에 --confirm을 붙여 다시 실행한다.
   ```

   `ADMIN_SECRET_KEY`가 Job에 없으면 Admin 보고서 탭의 ‘경쟁 병원 반영’ 버튼으로 병원마다 요청하고 3단계를 돌린다.

3. 사후 확인. 최신 버전과 대체한 버전의 측정·귀속·인용·발행 숫자가 같고(경쟁 사실·전략 요약의 경쟁 집계만 다름),
   새 원장 PDF가 검증돼 있으며 저장 숫자 사실과 경쟁 행을 찍었는지 본다. 확정 판정이 없어 행이 없는 등록 경쟁사는
   `WARN COMPETITOR_NOT_SHOWN`이다(미확정은 0이 아니라 ‘없음’).

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.monthly_competitor_refresh,postcheck,--year,2026,--month,10
   ```

4. 전달. 새 버전은 `customer_ready=false`로 태어나며 AE가 Admin에서 새 버전을 전달한다.

### 되돌리기·한계

- 새 버전은 이전 버전을 지우지 않는다. 문제가 생기면 이전 버전을 전달하면 된다. 보충된 `competitor_mentions`는
  코드 롤백으로 사라지지 않는다 — 확정 판정은 측정 때 판정과 같은 자격이고, 미확정(null)은 어디서도 세지 않는다.
- 경쟁 현황은 **판정이 있는** 답변만 센다. 등록 뒤 처음 반영한 달은 사전 필터에서 걸러진 답변(이름이 원문에 없음)도
  판정(미언급)으로 기록되므로 분모는 보고서가 쓴 확정 답변 수와 같다.
- 경쟁 사실을 뺀 다른 사실(진료 항목·누적 편수 등)이 현재 행 변화로 어긋나면 DIFF로 거절한다 — 그때는 일반 재생성이
  아니라 원인을 먼저 본다.
