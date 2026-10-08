# 2026-10-08 근본 원인 수정 운영 배포 — dispatch 배달·운영 채널 하나·운영 신호

배포일: 2026-10-08 16:35 KST (07:35Z) · 배포 소스: main `c5ada5a1` (PR #226·#227·#228 런타임, #229 문서)
배포 대상: API·Worker·Beat (Site·Admin 변경 없음) · DB head: **`0083_add_operation_run_not_before`**
새 리비전: api `00235-db4`, worker `00224-xnt`, beat `00219-dfh` (이전: api `00234-c9r`, worker `00223-llc`, beat `00218-f97`)
롤백 좌표: `.deploy-rollback` (deploy.sh가 보관) — `SLACK_WEBHOOK_URL_DEV` 버전 1을 비활성화했으므로 옛 리비전으로
롤백하려면 먼저 `gcloud secrets versions enable 1 --secret=SLACK_WEBHOOK_URL_DEV`가 필요하다(`deploy.sh rollback`이 검사·안내).

## 왜 했는가 — 10/6~10/8에 반복된 오류의 근본 원인 네 가지

1. **배포·적체 중 봉투 만료와 중복 배달이 실행을 망가뜨렸다.** kombu `visibility_timeout`·봉투 TTL·lease가 모두 3600초로
   겹쳤고, claim(task_prerun)이 봉투 검증(before_start)보다 먼저 돌아 만료된 사본이 실행을 가져간 뒤 FAILED로 끝냈다.
   자동 복구는 FAILED를 다시 보내지 않아 V0 보고서 2건(장보고연합내과·뉴플랜여성의원)이 유실됐다. 잦은 배포(하루 여러 번)가
   방아쇠였다. → PR #226.
2. **개발 Slack 웹훅이 9/19 등록 이후 줄곧 302(죽은 주소)였는데 아무것도 그 사실을 보지 않았다.** 개발 대상 알림 전부가
   실패했고, 실패가 outbox 행의 `incident_id`를 전송 사고로 덮어써 열림/복구 짝이 어긋났으며, 외부 감시(watchdog)의 경보도
   그 죽은 채널로 갔다. → PR #228 + 대표 결정 "운영 채널 하나".
3. **운영 채널의 신호가 읽는 사람 기준이 아니었다.** 일일 요약은 65건, 운영센터는 14건을 말했고(사람의 일 술어가 화면마다
   달랐다), 월간 보고서 새 버전·발행 보류 요약·월간 공백이 바뀐 것이 없어도 매일 다시 알렸다. → PR #227.
4. **(배포 중 발견) 워커가 Redis 연결을 잃은 뒤 조용히 멎었다.** 옛 워커 `00223-llc`가 12:25 KST에
   `consumer: Connection to broker lost`를 남긴 뒤 교체되기까지 4시간 10분 동안 로그 0줄·실행 0건이었다. 매분 도는 유지
   작업 약 1,458건이 큐에 쌓였다가 새 워커가 받아 전부 만료 봉투로 버렸다. 외부 감시는 멎음을 감지했지만(12:40·13:00·14:00
   경보 시도) 죽은 개발 웹훅으로 보내 전달되지 않았다 — 2번 원인의 실제 피해 사례다. 연결 끊김 뒤 재개하지 못하는 소켓
   설정(timeout·keepalive·health check 없음)이 원인이며 후속 PR로 고친다.

## 바뀐 계약 (요약 — 정본은 CLAUDE.md 2.15와 알림 정책 4.0)

- 배달 순서 검증→claim→판정, STALE은 `Ignore`(사고·실패 없음), 재배달마다 새 task id, QUEUED 유예 3시간, countdown 상한
  15분, V0 비용 보류는 `operation_runs.not_before_at`(0083), 워커 cold shutdown(`REMAP_SIGTERM=SIGQUIT`), kombu
  `visibility_timeout` 7200. 봉투 수명은 2단계 — 이번 릴리스는 수락 상한 6시간·서명 1시간, 다음 릴리스에서 서명 6시간.
- Slack은 운영 채널 하나. 개발 대상은 `[개발 확인]`, 죽은 웹훅 우회는 `[채널 대체 전송]`. 채널 생존은 채널 사고 하나가
  정본이고 배포·준비 점검·한 시간 탐침이 같은 분류를 쓴다. `deploy.sh`는 비활성 선택 시크릿을 주입하지 않고, 죽은 웹훅이면 멈춘다.
- 사람의 일은 `is_operator_todo` 하나. 월간 마일스톤 키 `monthly:{hospital_id}:{YYYY-MM}`, 발행 보류 요약 키
  `병원:글:코드:epoch`, 월간 공백 AUTO/MANUAL.

## 배포 절차와 증거

1. `.env.production`을 운영 API env와 대조 — 값 차이 0건(파일에만 있는 4개 키는 빈 값).
2. `gcloud secrets versions disable 1 --secret=SLACK_WEBHOOK_URL_DEV` → 곧바로 `deploy.sh backend`.
   deploy.sh 로그: "선택 시크릿 SLACK_WEBHOOK_URL_DEV: latest 버전이 비활성이어 주입하지 않습니다", "Slack 웹훅
   SLACK_WEBHOOK_URL 확인됨". 실제 Slack 응답으로 분류표를 사전 확인했다 — 운영 `400 invalid_payload`(살아 있음), 개발 `302`(죽음).
3. readiness Job `reputation-production-readiness-2wscv`: 17개 검사 전부 true, `schema_revision=0083_add_operation_run_not_before`,
   `slack_webhook_valid={"developer":"not_configured","operator":"alive"}`, `slack_webhooks_not_dead=true`, 7개 큐 canary 현재.
4. 배포 뒤 40분간 새로 생긴 사고 0건, 열린 `NOTIFICATION_*` 사고 0건(옛 `SLACK_DEV` 채널 사고가 단일 채널 모드로 닫힘),
   새 워커 WARNING 이상 로그 0건(07:38Z 이후). 외부 감시 경보의 "Slack delivery failed status=302"도 더 나오지 않는다.
5. 유실된 V0 2건 재실행(`POST .../operations/trigger-v0-report`, `Idempotency-Key v0-retry-20261008-*`) → 둘 다 200,
   OperationRun `b2962213…`(장보고연합내과)·`5f9055c7…`(뉴플랜여성의원) RUNNING.
6. 일일 요약 즉시 실행(`run_beat_entry daily-fleet-heartbeat`)은 18시 창 전이라 `before_summary_window`로 건너뛰었다 —
   18:00 KST 정규 발송에서 "점검 이상 없음"/건수를 확인한다.
7. 정리 도구 `notification_channel_cleanup`: dry-run `{stale_developer_rows: 24, resent_developer_rows: 2,
   single_channel_mode: true}` → `--confirm` 실행(결과는 아래 후속 확인 절).

## 일회성 효과·남은 것

- 새 워커 기동 직후 07:35~07:37Z에 "expired authenticated dispatch envelope" ERROR 약 1,458건 — 4번 원인(옛 워커 멎음)으로
  쌓인 매분 유지 작업의 만료 사본이다. 새 순서라 실행을 claim하거나 FAILED로 끝내지 않았고 사고도 열리지 않았다. 다만
  OperationRun 없는 주기 작업의 만료 사본은 ERROR 대신 WARNING 한 줄·`Ignore`가 맞다 — 후속 PR에 포함.
- 후속 PR(브로커 연결 복원력): 소켓 timeout·keepalive·health check, 재연결 무한 재시도, 멎은 소비자를 스스로 끝내는
  엔트리포인트 감시, 주기 작업 만료 사본의 조용한 폐기.
- 다음 릴리스에서 봉투 서명 수명을 6시간으로 올린다(#226 이전 워커 리비전이 모두 사라진 뒤).
- `operation_runs`에 2026-09-07의 `NIGHTLY_CONTENT_GENERATION` RUNNING 1건이 남아 있다(옛 잔재, 실행 영향 없음) — 정리 대상.
- 사후 `SLACK_DEV` 행 분포(정리 전): HOLD `DELIVERY_OUTCOME_UNKNOWN` 48 · HOLD `DEV_WEBHOOK_MISSING` 26 · FAILED 27 · SENT 19.
