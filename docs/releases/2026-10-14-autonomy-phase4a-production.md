# 2026-10-14 자율 운영 Phase 4-A 운영 배포 — 온보딩 자동 입력 즉시 반영·D31 완료 술어·출처 원장

배포일: 2026-10-14 12:46~12:57 KST(backend 12:46:13~12:55:46, admin ~12:57:17) · 배포 소스: main `c5ef4adf`(PR #257 `claude/autonomy-p4`, 병합 직전 main `0185a8d7` = W3 #254 + W8 #255)
배포 대상: API·Worker·Beat·Admin(Site 그대로) · DB head `0090_allow_fallback_treatment_narrative` → **`0091_add_profile_provenance_and_essence_metadata`**(JSONB 2열 추가형) · RedBeat 재조정 clean
리비전: api `00252-qfn`, worker `00242-c5q`, beat `00237-vnd`, admin `00108-9hv`(site `reputation-site-cr057b9d1e` 그대로)
(이전: api `00251-fq9`, worker `00241-8f8`, beat `00236-b2t`, admin `00107-d26` — 롤백 좌표 배포 worktree `.omo/rollback-p4a/backend`·`admin`)
이미지: `reputation:20261014-124614`(digest `sha256:a446aca6…20cb`) · 하루 잠금 `ops/deploy-lock/2026-10-14-override-1246.json`(`--override-reason="Phase 4-A 온보딩 자율화(2026-10-14)"`) · 배치 시간대(11:15~12:45) 밖

이 배포는 W8(#255, 월간 보고서 보장)도 함께 실었다 — W8 에이전트는 별도 배포하지 않았다. P4-B(#258)·게이트(#256)는 이 배포 뒤 main에 들어갔고 **포함되지 않았다**(배포는 `origin/main`이 아니라 `c5ef4adf`로 고정).

## 바뀐 계약 (정본: CLAUDE.md, PR #257)

- D30: 계약 등록 뒤 자동 입력(네이버 플레이스·홈페이지·블로그·설문)을 사람 확인 없이 **빈 칸에만** 적용, 마케터가 입력·수정·비운 칸은 덮지 않는다(출처 원장 `profile_provenance`). 적용 뒤 완료 재판정(승격만).
- D31: 진료 철학·Google만 선택인 고정 완료 술어, 15분 틱 승격 스윕(`promote_profile_complete`). `PROFILE_GATE_OPTIONAL_ENRICHMENT` 설정은 코드에서 삭제(운영 env·`.env.production`에 없었음 — env 차이 0).
- D32 설문 PDF·DOCX·Google 문서/시트 링크, D34 출처 간 충돌은 네이버 우선. Admin: 자동 입력 출처 배지·충돌 줄·설문 카드, 저장된 초안 불러오기·`GET …/autofill-draft` 삭제.
- 운영 조회 `ops_sql_report` q12~q14.

## 절차와 증거

1. CI: 첫 green(10:58) 뒤 #255 병합으로 `ops_sql_report`(q12~q14 vs q15)가 충돌 — 두 쪽을 합쳐 `0556120c`로 다시 올리고 CI 전부 통과(11:24) 뒤 병합. 배치 시간대 때문에 12:46에 배포.
2. **배포 전 q12(운영 DB, head 0090, 읽기 전용, 10:33 `reputation-migrate-b529k`·12:36 `reputation-migrate-xqgfm`)**: 병원 12곳 — ACTIVE·공개 11곳(`ALREADY_LIVE`), 세종다온신경과의원 ONBOARDING(`PROFILE_INCOMPLETE`, 옛/새 술어 모두 `contact`·`web_channels`·`ai_channels` 누락). **`would_publish_now` 0곳, `would_promote` 0곳**. PAUSED 0. q12 SQL은 0091 이전 열만 써 그대로 돌았다. 원본: scratchpad `p4adeploy/q12-pre.jsonl`.
3. `deploy.sh backend`: 운영 env 정본(차이 0), legacy publish 사전 확인 READY, migrate `reputation-migrate-p6m25`(0091 적용), worker → RedBeat reconcile `x8rz2` → beat → readiness `reputation-production-readiness-jfsxr` → api, 공개 표면 smoke 통과(자동 롤백 미발동). `deploy.sh admin` 이어서 통과.
4. readiness `ready=true` **18/18**, `schema_revision=0091…`(current), `auto_publish_hold.active=false`, live site 11, Slack operator alive. `/api/v1/health/live` `release=c5ef4adf…`. 병원 헬스 **11/11 200**(slug·canonical_host·hospital_id 확인). 트래픽 고정 없음.
5. 배포 뒤 q12~q14(새 이미지 `reputation-migrate-vrb9g`): q12 사전과 같음(`would_publish_now` 0). q13: APPROVED base 9곳, 승인 판 없음 3곳(뉴플랜·세종다온·장보고), `PROFILE_AUTOFILL` 실행 이력 **0건**(전 병원). q14: `ESSENCE_AUTO_REVIEW_ESCALATED` OPEN 3(3곳)·RETRYING 3(2곳), digest 알림 0.
6. 백필 dry-run(`reputation-migrate-7m2t4`): 12곳 모두 `DISPATCH_COLLECTION`(저장된 초안이 하나도 없어 적용할 필드 목록은 수집 전에는 나오지 않는다). 공개 11곳은 `apply_live=false`, 세종다온만 적용 대상. 출처 원장 백필은 전부 `operator`(감사 로그의 사람 입력), `legacy` 0.
7. **적용(세종다온만)**: `profile_autofill_backfill --apply --hospital=d5d9ca57-…`(`reputation-migrate-fvmfq`, 12:57) → `DISPATCHED_COLLECTION` 1. `--include-live`는 돌리지 않았고 공개 11곳에는 수집도 보내지 않았다(유료 수집 1회).
   - 결과(12:58): `PROFILE_AUTOFILL` SUCCEEDED, `apply_status=NOTHING_TO_APPLY` — 홈페이지·블로그 URL 미입력, 네이버 플레이스에서 병원을 찾지 못함. 바뀐 필드 0, 승격 없음, 세종다온은 ONBOARDING·미공개 그대로.
8. 15분 틱 관찰(13:00·13:15, `reputation-migrate-4mt6m`·`f4bsn`): `apply_profile_autofill`·`promote_profile_complete` 감사행 0, 사이트 준비·활성화 0, 새로 열린 사고 0(기존 콘텐츠 생성 사고 갱신만). outbox: W8 안전망 재생성(신기한속·장앤김 9월 보고서 v6·v5, `report-repair:`)에 따른 `INCIDENT_RECOVERED` 6(SENT 4, `SLACK_DEV` 2는 `INCIDENT_RESOLVED_BEFORE_SEND`로 발송 전 종결 — 정상 짝 종결) + 13:00 `MILESTONE_SUMMARY_REPORT` 1(9월 보고서 전달 준비 완료 2건, SENT).
9. 새 리비전 로그: +10분 WARNING 25·**ERROR 0**, +30분 WARNING 187·**ERROR 0**(API 404 스캐너, WeasyPrint CSS 경고, 기존 발행 보류).

## 남은 일

- 세종다온: 네이버 플레이스 주소·홈페이지/블로그 URL을 사람이 넣어야 자동 입력·완료 승격이 진행된다(온보딩 정체 사고가 이미 열려 있다).
- 공개 11곳의 자동 입력은 대표가 `--include-live` dry-run(수집 필요)을 본 뒤에만.
- 롤백: 코드 롤백으로 0091 열·출처 원장은 되돌아오지 않는다(downgrade는 열을 지운다). 이번 배포가 적용한 프로필 값은 없다.
