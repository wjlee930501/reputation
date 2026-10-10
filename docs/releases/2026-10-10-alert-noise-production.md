# 2026-10-10 반복 Slack 알림 근본 수정 운영 배포

배포일: 2026-10-10 14:16~14:30 KST · 배포 소스: main `2cc60247`(PR #236 알림 소음 근본 수정, #237 배포 사전 확인 VPC 실행)
배포 대상: API·Worker·Beat·Admin (Site 변경 없음) · DB head `0086_preserve_historical_publications` 그대로(마이그레이션 없음)
리비전: api `00240-c9n`, worker `00230-s6m`, beat `00225-npd`, admin `00104-gkq`
(이전: api `00239-mqs`, worker `00229-rm6`, beat `00224-ndd`, admin `00103-h78`)

## 왜 했는가 — 10/9~10/10 #mkt-reputation의 반복 알림

| 알림 | 실제 원인 (운영 DB·로그로 확인) |
|---|---|
| 10/10 08:30·09·10·11·12시 "오늘 예정된 글이 한 건도 공개되지 않았습니다" 5회 | 외부 감시(watchdog)의 중복 억제 키에 KST 시각이 들어 있어 같은 상태를 매시간 다시 보냈다. 그날 예정 글 5편은 발행기가 정상으로 돌았고 안전검사가 모두 보류(참고자료 없음 3·HARD 지적 2)한 것으로, 07:45/08:00 보류 요약이 이미 알린 사실이었다. |
| 10/9 14:30~15:45 "자동 운영 실행기가 멈춰 있습니다"↔"돌아왔습니다" 5회 | 그 시간의 배포 4회(14:28·14:36·14:40·15:42)마다 Beat가 재시작해 RedBeat 락이 잠깐 비었다. 감시가 한 번 관측으로 경보·복구했다. |
| 10/9 00:00 "다시 공개되고 있습니다" | 자정 날짜 전환으로 발행 조건이 사라진 것을 복구로 읽었다. |
| 10/9 21:31 세 병원 "주소 접속 확인 — HTTP 503" | 21:30:09~20 UTC+9 한 번의 공용 사이트 503. 앞뒤 점검은 모두 정상, 배포 없음. 점검 1회 실패로 사고·Slack을 열었다(복구는 이후 자동). |
| 10/9 14:37 "8월 월간 리포트 차단 7건" | 10/9 배포의 새 전달 판정이 측정 가용성 필드가 없는 옛 8월 요약을 "월간 측정 가용성 상태를 확인할 수 없습니다"로 막았다(커서 상태 14:15 `READY:False` → 14:30 `BLOCKED:True:False`). 그 7곳은 9월이 이미 전달 준비 완료라 할 일이 없는 알림이었다. 9월 판정: 준비 완료 7·측정 미완료 2(장앤김·신기한속). |

## 바뀐 계약 (정본: CLAUDE.md 2.19·알림 정책 4.1)

- 감시: 조건 집합 에피소드당 한 번 경보, 전달 미확인만 15분 재시도, 복구 한 번. Beat·대기열 정지와 `publish_missing`은
  4분 이상 간격 연속 두 점검으로 확정·해제. 발행 조건은 KST 날짜에 속해 자정에 조용히 사라짐. `publish_gate_residual`은
  경보하지 않음(보류 요약·사고·일일 요약 소유). 운영 복구 문구는 실제 공개가 있을 때만 "다시 공개되고 있습니다".
  Redis 장애 fail-open은 20분에 한 번.
- 공개 주소 점검: 연속 두 번 실패해야 병원 사고. 한 회차에 두 병원 이상 공용 사이트 5xx면 개발 대상
  `PUBLIC_SITE_UNAVAILABLE` 하나(연속 두 회차).
- 월간 마일스톤: 같은 병원의 나중 달이 준비 완료·전달이면 이른 미전달 달의 차단·검증 대기 변화는 알리지 않음(상태 기억은
  이어 감). 그 이른 달이 준비 완료가 되면 한 번 알림.
- 배포: 로컬 `DATABASE_URL`이 없으면 legacy publish 사전 확인을 VPC 안 migrate Job(현재 이미지)으로 실행(#237).

## 배포 중 발견 — API 트래픽 고정

`deploy.sh backend`가 "API 배포 완료"라고 끝났지만 API 서비스의 트래픽이 10/9의 `00239-mqs`에 **고정**(spec.traffic
revisionName, latestRevision 아님)돼 있어 새 리비전 `00240-c9n`은 만들어지자마자 은퇴했다. 새 감시 코드가 서비스되지 않는
거짓 성공이었다. `gcloud run services update-traffic reputation-api --to-latest`로 옮겨 `00240-c9n`이 100%를 받는 것을
확인했다(Site·Admin·Worker·Beat는 최신을 따라가고 있었다). 재발 방지: 배포 전 고정 트래픽을 잡아 멈추고, 배포 뒤 트래픽을
받는 리비전이 최신 Ready인지 확인하는 검사를 `deploy.sh`에 더했다.

## 증거

- legacy publish 사전 확인(VPC): `READY`, open_legacy_transport 0, unapplied_sent 0, convertible 0, total_historical 16, unknown 2.
- readiness Job `reputation-production-readiness-srrf2` 통과.
- 새 API에서 감시 판정: 같은 상황(안전검사 전부 보류)에서 `critical_conditions=[]`, `healthy=true`, `publish_gate_residual=true`.
- 새 API 리비전 ERROR 0건, 공개 사이트·병원 사이트 200.
- 당일 즉시 조치(배포 전): 감시의 오늘 14~23시 시간 단위 중복 키와 다음 날 00~01시 복구 키를 Redis에 미리 넣어 반복을 멈춤(TTL로 자연 소멸).
