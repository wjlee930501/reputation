# 원장 보고서 발행 귀속·노출 0건 핵심 줄 — 운영 배포 (2026-11-09 새벽)

상태: 2026-11-09 02:27~02:45 KST에 Backend(API·Worker·Beat)를 한 번 배포했다(그날 첫 릴리스, 배치 시간대 02:01~03:14 창 안).
런타임 코드: main `d293c5e4`(PR #296 발행 귀속 + PR #297 노출 0건 핵심 줄). 이 문서 커밋은 런타임 이미지의 소스가 아니다.

| 서비스 | 리비전 | 이미지 |
| --- | --- | --- |
| API | `reputation-api-00283-hnr` | `reputation@sha256:5c7665c5…ec626` (태그 `20261109-022721`) |
| Worker | `reputation-worker-00273-7cn` | 같음 |
| Beat | `reputation-beat-00268-9qb` | 같음 |

Admin·Site·DB head 그대로(마이그레이션 없음). 롤백 좌표 `.omo/rollback-month/backend`(api `00282-2zl`·worker `00272-hd5`·beat `00267-tnn`).
env 정본은 운영 서비스(차이 0), `--accept-env-diff`·`--override-reason` 없이 배포. readiness·큐 준비·공개 표면 확인 통과, `/health/live` release `d293c5e4`.

## 바뀐 것

- **PR #296 — 원장 보고서 '발행 콘텐츠'는 글마다 한 달에만 싣는다** (`monthly_publication_facts.load_report_month_publications`).
  장보고연합내과 10월 보고서가 10/30 예정·11/1 08:00 공개 글을 목록에서 빼고 1면이 '11편 · 계획 7편 중 12편 발행 · 누적 11편'이던 문제
  (11/06 김찬중 요청). 계약 월 기준으로 싣고, 계약 월 보고서 첫 판 뒤에 처음 공개된 글만 실제 공개 달로 보낸다. 상세 규칙은 CLAUDE.md.
- **PR #297 — 노출 0건인 달의 1면 '잘된 점'** (`doctor_report_v4._key_lines`·`apply_report_copy_policy`). '측정 키워드 15개 중 0개에서
  노출됐습니다'(11/06 행복드림·마포 스크린샷) 대신 그 달 발행 사실('콘텐츠 12편을 발행해 누적 37편이 됐습니다.'), 보완할 점은 '측정 키워드
  15개 모두 아직 노출되지 않았습니다.'. 옛 판은 템플릿 갱신 때 카드 숫자로 바꾸고, AE가 발행 카드만 고치면 문장을 다시 만들며, 템플릿 갱신
  사후 확인은 같은 규칙을 거친 옛 핵심 3줄과 비교한다. Codex(gpt-6.1-sol, xhigh) 3회 — 1·2차 P2 4건 반영, 3차 이상 없음.
- 검증: 보고서·월간·경쟁 관련 로컬 테스트 1,727 passed. CI는 main과 같은 기존 16건 실패뿐(새 실패 0).

## 배포 뒤 작업

- **장보고연합내과 10월 v7** — 일반 재생성(`rebuild=true`, 사유 '발행 콘텐츠 귀속 수정(PR #296)…', actor `system:monthly-report-regenerate`,
  작업 `6dd8092e`). `SUCCEEDED/ARTIFACT_VALIDATED`. 1면 '12편 · 계획 12편 중 12편 발행 · 누적 12편', 목록 끝에 11-01 '시지국가건강검진 가능한
  병원, 예약 전 확인할 다섯 가지', 진료 항목 막대 시지국가건강검진 2편. 측정 숫자(AI 노출률 7%, 90건 중 6건)는 v6와 같다. v5에서 찬중님이
  손으로 고친 값(11/06 15:11 v6 일반 재생성 때 `NUMBERS_CHANGED`로 빠짐)과 같은 숫자를 이제 코드가 낸다.
- **행복드림의원·마포성모탑정형외과 10월 v5** — `monthly_template_refresh` precheck PASS → execute(사유 '노출 0건 달 1면 핵심 줄 수정(PR #297) —
  숫자 그대로') → postcheck PASS(WARN `REPORT_COPY_POLICY`). 1면 잘된 점은 각각 '콘텐츠 12편을 발행해 누적 37편이 됐습니다.'·'콘텐츠 20편을
  발행해 누적 39편이 됐습니다.'.
- **세 곳 모두 원장에게 전달하지 않았다** — 새 버전은 AE가 다시 전달해야 반영된다.

## 관찰 — 옛 Beat 인스턴스가 퇴역 뒤에도 남음

새 Beat `00268-9qb`가 RedBeat 잠금을 받지 못했다. 옛 리비전 `00267-tnn`(퇴역, `Active=False/Retired`)의 인스턴스(11/06 21:07 UTC 기동)가
배포 뒤 50분 넘게 살아서 잠금을 갱신하며 같은 스케줄을 보냈다. 새 Beat는 첫 tick 전 기동 유예(900초)가 끝날 때마다 `/live` 실패로 재시작돼
ERROR 로그(`LIVENESS HTTP probe failed`)가 약 15분마다 남았다(17:45·18:01 UTC …). 옛 Beat가 같은 스케줄(RedBeat 판 불변, 이번 릴리스에 Beat
변경 없음)을 같은 큐로 보내므로 빠진 작업은 없다. 11/06 배포에서는 약 5분 만에 넘어갔다. 옛 리비전 삭제는 Beat 롤백 기준을 없애므로 하지 않았다.
