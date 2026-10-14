# 2026-10-14 Phase 4-B(승인 없는 자동 Essence) + Full-Automatic 게이트(D36·D37) 운영 배포

배포일: 2026-10-14 13:23~13:37 KST(migrate 13:23:34~ · backend 13:28:33~13:35:46 · admin ~13:37:04) · 배포 소스: main `5b13b02c`(PR #258 `claude/autonomy-p4b` 병합 `2ee59226` + PR #256 `claude/fa-gates` 병합)
배포 대상: API·Worker·Beat·Admin(Site 그대로) · DB head `0091_add_profile_provenance_and_essence_metadata` → **`0092_publish_without_references_or_verdict`**(승인 판 SQL 함수 교체) · RedBeat 재조정 `reputation-redbeat-reconcile-5882v`
리비전: api `00253-nnp`, worker `00243-h45`, beat `00238-762`, admin `00109-d6r`(site 그대로)
(이전: api `00252-qfn`, worker `00242-c5q`, beat `00237-vnd`, admin `00108-9hv` — Phase 4-A. 롤백 좌표 배포 worktree `.omo/rollback-gates/backend`·`admin`)
이미지: migrate용 `reputation:20261014-132334`(`sha256:7fbf6020…ed22`) · backend `reputation:20261014-132833`(`sha256:8bf07232…87e4`) · admin `admin:20261014-133553`(`sha256:b44b20e4…019f`)
하루 잠금: `ops/deploy-lock/2026-10-14-override-1328.json` — `--override-reason="Phase 4-B + D36·D37 게이트(2026-10-14)"`(오늘 세 번째 릴리스). 배치 시간대(11:15~12:45, 17:15~18:45) 밖. env 차이 0(새 플래그는 코드 기본값 False). 자동 롤백 켜짐(발동 없음).

P4-B는 처음에 따로 배포할 예정이었다. 그런데 배포 전 dry-run에서 공개 병원 신기한속내과연합의원(레거시 v1)이 `T3_SURVEY_CHANGED`로 나왔다. 설문 docx 3건이 v1 승인 10분 뒤에 처리돼 v1에 들어간 적이 없었다. P4-B 에이전트는 배포를 멈췄고, 오케스트레이터가 "설문 업로드 → 자동 파이프라인으로 다시 만든다" 규칙대로 이 재합성을 받아들였다. 그 뒤 #256과 함께 한 번의 배포로 실었다.

## 바뀐 계약 (정본: CLAUDE.md, PR #258·#256)

- **P4-B(D33)**: Essence가 사람 승인 없이 자동 판으로 적용된다(`system:essence-auto`). 필수 문구는 원문 문장만 쓴다(SURVEY/SOURCE_VERBATIM). 옛 `ESSENCE_AUTO_REVIEW_*` 사고와 "승인 기준 없음" 아침 요약은 은퇴했다. 백필 CLI는 `python -m app.utils.essence_autonomy_backfill`이다.
- **D36**: 참고자료가 없어도 생성·발행·공개를 막지 않는다. 비면 수기 목록 치유 뒤 참고자료 없이 저장·발행하고 `references_omitted`를 기록한다.
- **D37**: 검수 장애(UNAVAILABLE)가 지금 본문 hash에 묶여 있으면, 자동 교정본이 아니고 같은 hash가 차단 판정을 받은 적이 없을 때 먼저 발행한다(`ai_review_deferred`). 03:10 사후 검수가 이 글을 먼저 본다.
- **기본 꺼짐**: `CONTENT_FINDING_TIERS_ENABLED=false`(W4 지적 등급), `POST_PUBLISH_CRITICAL_ALERTS_ENABLED=false`(D35 알림). 운영 env에 없고 코드 기본값 False가 적용된다.
- 게이트 카탈로그 판 `2026-10-13.1` → `2026-10-14.1`: 종착 슬롯이 한 번 다시 평가된다.

## 절차와 증거

1. CI: #256은 #258 병합 뒤 `origin/main`을 다시 합쳤다(`d070c850`). CLAUDE.md 충돌은 두 쪽 문장을 모두 살려 풀었다. CI 10/10 통과 뒤 병합(`5b13b02c`).
2. **배포 전 영향 조회(읽기 전용, 0091 스키마)**: `ops_sql_report`의 q16 SQL을 migrate Job에서 base64 `python -c`로 돌렸다(12:00, 13:20 두 번, 결과 같음). 원본: scratchpad `gatesdeploy/q16-pre.jsonl`.
   - q16(참고자료 0개 발행 전 글): **1건** `25124b9b`(신기한속, COLUMN "대구 동구 건강검진 비용 문의 전…", DRAFT). 이 글은 `CONTENT_AI_HARD_FINDING`/`OPERATOR_REQUIRED`라 D36으로 새로 발행되지 않는다(판정된 HARD). 40건 상한 안.
   - q16b(검수 장애 보류 발행 전 글): **0건**.
   - q16c(공개 글 중 참고자료 0개·검수 장애): 5건(`d05a2b7d`·`6b3cc6bc`·`8344b365`·`f717bcb8`·`ab196937`). 모두 D23 진료비·병원 선택 글이고 AI 검수 PASS다. 지금도 공개 중이며, 롤백 때 다시 숨을 수 있는 목록이다.
   - q16d(알림 의도 없는 FLAGGED 공개 글): 74건. D35 플래그가 꺼져 있어 알리지 않는다.
   - q4 종착 슬롯: 3(`OPERATOR_REQUIRED/CONTENT_AI_HARD_FINDING`, DRAFT).
3. **P4-B dry-run(새 이미지)**: `deploy.sh migrate`로 migrate Job을 `5b13b02c` 이미지로 바꿨다. 0092는 이때 먼저 적용됐다(13:23, `reputation-migrate-8k8h8`). 옛 코드와 0092의 조합은 롤백과 같은 조합이라 지원된다. 그 뒤 `essence_autonomy_backfill`(dry-run)을 돌렸다(`reputation-migrate-vhf2k`). 대상은 장보고·뉴플랜·세종다온(T1_NO_ESSENCE) 3곳이고, 공개 8곳은 trigger null, 신기한속만 T3로 기대와 같았다.
4. 배포 전 Essence 기준: 승인 판 id는 행복드림 `3e10aa36` v9, 강심장 `d20f11f3` v3, 장편한 `626b9c09` v11, 마포성모 `dad02465` v4, 연세속시원 `e3747d6f` v10, 장앤김 `1c2f9008` v3, 노원탑 `e1744c8f` v7, 서울W 위례 `a3dc1d52` v3, 신기한속 `71fb4eb1` v1.
5. `deploy.sh backend`(13:28:33~13:35:46): 운영 env를 정본으로 썼고 차이 0. legacy publish 사전 확인 `READY`, 롤백 좌표 기록. migrate `reputation-migrate-8n2cg`(변경 없음) → worker `00243-h45` → RedBeat reconcile → beat `00238-762` → readiness `reputation-production-readiness-pph82` → api `00253-nnp` → 공개 표면 smoke 통과. `deploy.sh admin`(→ `00109-d6r`)은 별도 롤백 파일을 썼다.
6. **배포 뒤 확인**:
   - readiness `ready=true`, **18/18**, `schema_revision=0092_publish_without_references_or_verdict`, `auto_publish_hold.active=false`, Slack operator alive.
   - `/api/v1/health/live`의 `release=5b13b02c…`, 병원 헬스 11/11 200.
   - 새 리비전 ERROR: +10분 0건, +35분 1건. 그 1건은 worker `cost_guard_fail_open category=content error=RuntimeError`(13:51:21, 자동 입력 수집 디스패치와 같은 시각)이고 13:51 열림 → 14:02 복구됐다. 원인은 조사하지 않았다.
   - 설정 실측(새 이미지): `CONTENT_FINDING_TIERS_ENABLED=False`, `POST_PUBLISH_CRITICAL_ALERTS_ENABLED=False`, 카탈로그 `2026-10-14.1`.
   - 14:00 발행 틱 뒤 q16 글 `25124b9b`는 그대로 DRAFT/OPERATOR_REQUIRED였다(판정된 HARD). 배포 뒤 새 발행 0건이고 q4는 3 그대로다.
   - 배포 뒤 Slack outbox 2행: `COST_GUARD_FAIL_OPEN` 열림 1·복구 1(SENT). Essence 관련 행과 요약 행은 0이다.
7. **P4-B 백필 `--apply`**(`reputation-migrate-f4bdh`):
   - 프로필 사실 동기화 12곳.
   - 자동 판 3/3 적용: 뉴플랜 `77e43504` v9(SOURCE_VERBATIM), 세종다온 `02b3c7cc` v5(SURVEY_VERBATIM), 장보고 `dab32483` v5(SOURCE_VERBATIM).
   - 첫 일정: 뉴플랜·장보고는 PLAN_12 월·수·금, 10/15 시작, 10월 7슬롯. 세종다온은 ONBOARDING이라 일정 대상이 아니다.
   - 은퇴한 `ESSENCE_AUTO_REVIEW_ESCALATED` 6건(OPEN 3·RETRYING 3)이 Slack 없이 ACKNOWLEDGED로 닫혔다.
   - 신기한속은 13:45 틱에서 v2 `d21c8cf3`(T3, SURVEY_VERBATIM)로 다시 만들어졌다.
   - 공개 8곳의 Essence id·판은 그대로다.
8. **필수 문구 결함과 데이터 핫픽스(14:2x)**: 신기한속 v2가 설문지 안내문 세 문장을 필수 문구로 골랐다("모든 질문은 실제 원장님의 말투로, 풍부하게 답변 부탁드립니다." 등). 세종다온 v5는 조각("중요하며, 음주, …")과 명사구 두 개를 골랐다.
   - 오케스트레이터 결정으로 두 판의 `must_use_messages`와 `synthesis_metadata.must_use.items`를 `[]`로 비웠다. 지운 문장과 사유는 `synthesis_metadata.hotfix_20261014`에 남겼다.
   - 감사행 `hotfix_philosophy_must_use`(actor `system:essence-hotfix-20261014`)를 남겼다. 한 트랜잭션이고, 쓰기 전 id·판·문구를 대조했다. 두 판 모두 APPROVED·판 번호 그대로다.
   - 신기한속의 10/16까지 DRAFT 4건(`25124b9b`·`40f30525`·`80495e84`·`13d36190`)의 본문에는 안내문이 없다.
   - 코드 수정 PR #259(정책 판 `2026-10-14.4`, 설문 안내·질문·조각 제외)를 따로 냈다.
9. **대표 지시 — 자동 입력(공개 병원 2곳)**: 뉴플랜·장보고에 dry-run(`DISPATCH_COLLECTION`)을 돌렸다. 이어 `--apply`를 `--include-live` 없이 실행해 수집만 했다(초안 저장, `SKIPPED_LIVE`). 그다음 `--include-live` dry-run으로 필드별 계획을 받고, `--apply --include-live`로 적용했다. 네이버 신원은 두 곳 모두 `STORED_ID`(확인됨)다.
   - 뉴플랜: 채운 필드 `naver_place_id`(1141367271, 네이버 플레이스). 마케터 칸이라 건너뛴 필드는 주소·전화·홈페이지·블로그·네이버 URL·진료과. 충돌 기록은 주소·홈페이지·네이버 URL·진료과.
   - 장보고: 채운 필드 `naver_place_id`(37675646, 네이버 플레이스). 마케터 칸이라 건너뛴 필드는 원장명·약력·주소·전화·네이버 URL·키워드. 충돌 기록은 약력·주소·전화·네이버 URL·키워드.
   - 두 곳 모두 확인되지 않은 출처로 건너뛴 필드는 0이고 완료 상태는 그대로다.
10. **대표 지시 — 세종다온 온보딩 사고**: 개원 전 병원이다. `ONBOARDING_PROFILE_INCOMPLETE`(`ea9e21cf`)를 `recover_ops_incident(notify=False)`로 조용히 닫았다(actor `system:owner-decision-20261014`, 사유 "개원 전 병원 — 대표 확인"). 상태는 OPEN → ACKNOWLEDGED이고 복구 Slack은 없다. 10/13에 열림 알림이 이미 나갔으므로 복구 알림 짝이 없는 채로 남는다. ACKNOWLEDGED는 정체 스캔이 다시 열지 않는다. 병원 행은 바꾸지 않았다.

## 후속 배포 — PR #259 필수 문구 규칙(14:41~14:51 KST)

main `d5f8e333`(PR #259 + 문서 #253)을 API·Worker·Beat에 배포했다(`deploy.sh backend --override-reason="필수 문구 설문 안내문 제외 핫픽스 PR #259(2026-10-14)"`,
롤백 좌표 `.omo/rollback-mufix/backend` = api `00253-nnp`·worker `00243-h45`·beat `00238-762`). 리비전은 api `00254-qv8`, worker `00244-mhh`,
beat `00239-9jc`이고 Admin·DB head는 그대로다. 이미지는 `reputation:20261014-144055`.

- `essence_claims.is_not_doctor_statement`로 설문 안내·답변 요청·원장을 부르는 말·질문·조각·명사구·12자 미만 문장을 고르는 쪽과 소비하는 쪽 모두에서 뺀다.
  자동 판 정책 판은 `2026-10-14.4`.
- 확인: readiness 18/18(`reputation-production-readiness-27w98`), release `d5f8e333`, 병원 헬스 11/11, 새 리비전 ERROR 0. 배포 뒤 Slack outbox 0행,
  새 사고 0건.
- 15:00 틱이 자동 판 4곳을 다시 만들었다. 레거시 8곳은 그대로다.
  - 뉴플랜 v11 `0494f30c`(SOURCE): 원장 문장 3개.
  - 장보고 v7 `2c857568`(SOURCE): 원장 문장 3개.
  - 신기한속 v3 `37552572`(SOURCE): "빠른 내시경이 아니라 바른 내시경…"과 홈페이지의 검사 설명 2문장.
  - 세종다온 v6 `79cef15b`(SURVEY): 약물·검사 설명 문장(트립탄·저혈당·PWV 조각). 진료 철학이 아니므로 개원 전 결정이 필요하다 — 일정이 없어 지금은 생성되지 않는다.

## 롤백 주의

- `deploy.sh rollback`(트래픽 복귀, 좌표 `.omo/rollback-gates/backend`·`admin`)은 DB를 되돌리지 않는다. 0092 함수는 남지만 옛 코드와 함께 쓸 수 있다(PR #256 롤백 테스트).
- 트래픽만 되돌리면 옛 코드가 공개 표면에서 참고자료 0개 의료 글과 검수 장애 선발행 글을 **다시 숨긴다**. 숨을 후보는 위 q16c 5건과, 배포 뒤 D36·D37로 새로 공개된 글이다. 롤백 전에 q16c를 다시 떠 둔다.
- 카탈로그 판이 `2026-10-13.1`로 돌아가 종착 슬롯 재평가가 반복된다(P3와 같은 주의). 특수 롤백 이미지는 만들지 않았다.
- P4-B로 만든 자동 Essence 판·첫 일정·은퇴 사고 종결·데이터 핫픽스·자동 입력 적용은 코드 롤백으로 되돌아오지 않는다.
- Phase 0~2의 롤백 금지(Worker `00223` 이하)는 그대로다.

## 남은 일

- 세종다온 v6 필수 문구(설문의 약물·검사 설명)를 정리한다 — 진료 철학·환자 약속 노트로 고르는 범위를 좁히거나 개원 전에 비운다.
- 신기한속 v3의 검사 설명 문장 두 개가 모든 글에 원문 그대로 들어가는 것이 맞는지 확인한다.
- `COST_GUARD_FAIL_OPEN`(13:51, RuntimeError, 11분 뒤 복구)의 원인 조사.
