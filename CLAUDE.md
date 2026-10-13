# Re:putation — 현재 프로젝트 개발 안내

## 최신 운영 배포 — Phase 2 (배포 전)

**이 절은 배포 기록이 아니다.** 운영 기록은 아래 배포 절들을 따르고, 배포는 [운영 배포 절차](docs/ops/deployment-runbook.md)의 별도 검증으로
기록한다. 브랜치 `claude/autonomy-p2`(온보딩 P2-A + 인프라 P2-B, main `08abf747` 병합)가 [자율 운영 보완 MasterPlan](docs/plans/2026-10-11-autonomy-masterplan.md)의
Phase 2를 구현했다 — Essence 자동 재검수 1h/4h/12h·상충/주입 의심 보류는 곧바로 사람·처리 오류 자료 제외, Essence 자동 승인 뒤 첫 발행
일정 자동 생성과 첫 달 안분, 온보딩 정체 사고 4종, 계약 등록 때 리드 시드·자동 입력 초안, 대기 중인 자기 도메인의 15분 DNS 확인(P2-A),
readiness 감시 토큰 검사(18개 검사)·GCP 이메일 경보 2종·운영 경보 메일(`OPS_ALERT_EMAIL`)·Beat liveness·비용 가드 fail-open과 사용량
spool 잘림 개발 사고·`deploy.sh` 규칙 코드화(env 정본·배치 시간대·자동 롤백·`--unpin`)(P2-B). **마이그레이션은 없다** — DB head는
Phase 1의 `0088_add_outbox_open_pairing_index` 그대로이고 RedBeat 버전도 그대로다. 이 절을 병합할 때 Phase 1이 이미 배포됐을 수 있으니
배포 전에 운영 리비전·DB head를 다시 확인한다. 배포 대상은 API·Worker·Beat와 Admin(병원 정보 화면의 자동 입력 초안)이고, 배포일 절차
(Terraform 경보 2종 `-target` 적용, `PIPELINE_WATCHDOG_TOKEN` 확인, `OPS_ALERT_EMAIL`, 첫 `--accept-env-diff`, 잠금 버킷 확인)는 배포 안내의
Phase 2 항목을 따른다. 배치 시간대 밖이면 하루 여러 번 배포할 수 있다(2026-10-13 대표 결정). 플래그 `ONBOARDING_SOURCE_FETCH_ALERTS_ENABLED`·
`PROFILE_GATE_OPTIONAL_ENRICHMENT`는 기본 꺼짐(대표 결정 전)이다. **롤백**: 마이그레이션이 없어 트래픽 롤백으로 충분하다. Phase 0의 롤백
금지(Worker `00223` 이하)는 그대로다. 아래 계약 문장은 이 브랜치 기준이다.

## 최신 운영 배포 — 2026-10-13 Phase 1 자율성 보완 (배포 전)

**이 절은 배포 기록이 아니다.** 운영 기록은 아래 배포 절들을 따르고, 배포는 [운영 배포 절차](docs/ops/deployment-runbook.md)의 별도 검증으로
기록한다. 브랜치 `claude/autonomy-p1`(main `7a1e2174` 병합)이 [자율 운영 보완 MasterPlan](docs/plans/2026-10-11-autonomy-masterplan.md)의
Phase 1을 구현했다 — 사고 종류 소유 레지스트리·스윕 소유 사고의 RETRYING+기한·발송 전 해결된 알림 짝 종결·종류별 닫는 근거·
대상 재분류·사후검수 UNCERTAIN 재검수(P1-A), 월간 측정 사고와 공백 요약의 월초 오탐 제거·매니페스트 없는 달 안내·코호트 경고선
계산·V0 자동 재실행·리드 진단 백오프·도입문의 Slack/SMS 재시도(P1-B). DB head는 `0088_add_outbox_open_pairing_index`(추가형 부분 인덱스)다(Phase 0의
`0087_add_operation_run_redispatch_count` 위). RedBeat 버전이 `2026-10-12.1`로 올라 배포 때 영속 스케줄 재조정이 필요하다.
`SOV_MONTHLY_COHORT_LIMIT` 설정은 코드에서 없어졌으므로(남아 있어도 무시된다) 배포 때 운영 env와 `.env.production`에서 지운다.
**롤백 주의**: 코드를 되돌려도 이 릴리스가 종결한 알림 행(`INCIDENT_RESOLVED_BEFORE_SEND`로 끝난 outbox)과 근거 정리가
확인 처리한 사고는 되살아나지 않는다. RedBeat는 옛 스케줄로 다시 재조정해야 하고, 옛 발송기는 기한 안이라 보류해 둔 열림 알림을
곧바로 내보낸다(롤백 직후 '조치 필요'가 한꺼번에 나갈 수 있다).
아래 계약 문장은 이 브랜치 기준이다.

## 최신 운영 배포 — 2026-10-13 Phase 0 자율성 보완

main `31751a88`(PR #239)을 API·Worker·Beat·Admin에 배포했다(2026-10-13 09:55~10:03 KST). 현재 리비전은 api `00241-lhd`,
worker `00231-4rx`, beat `00226-gpw`, admin `00105-x4r`이고 Site는 그대로, **DB head는 `0087_add_operation_run_redispatch_count`**(추가형)다.
[자율 운영 보완 MasterPlan](docs/plans/2026-10-11-autonomy-masterplan.md)의 Phase 0 — 주제 교체 뒤 새 예산과 즉시 교체(P0-A), 공급자 장애
일일 예산·3일 창·원고 미생성 재분류·교체 계단 확대(P0-B), 봉투 서명 6시간·재배달 예산 3회·lease 인수 확대·10일 잔재 정리·읽기 전용 운영
조회 CLI(P0-C)다. readiness 17개 검사 통과, 병원 11곳 헬스 200, 새 리비전 ERROR 0건. 35일 RUNNING 잔재 1건이 첫 자율 복구에서
`OPERATION_RUN_ABANDONED`로 종료돼 예상한 개발 대상 사고·Slack 1건이 나갔다. **이후 Worker를 `00223` 이하로 롤백하지 않는다.**
배포 뒤 1회 조치(원고 미생성 동결 슬롯 "작업 다시 시도")와 10/14 지표는 남아 있다. 상세는
[Phase 0 배포 기록](docs/releases/2026-10-13-autonomy-phase0-production.md), 근거는 [감사·개선 계획](docs/plans/2026-10-11-autonomy-audit-and-improvement-plan.md).

## 최신 운영 배포 — 2026-10-10 반복 Slack 알림 근본 수정

main `2cc60247`(PR #236·#237)을 API·Worker·Beat·Admin에 배포했다. 현재 리비전은 api `00240-c9n`, worker `00230-s6m`,
beat `00225-npd`, admin `00104-gkq`이고 DB head는 `0086_preserve_historical_publications` 그대로다. 감시는 조건 에피소드당
한 번·연속 두 점검으로 알리고 안전검사 보류는 알리지 않으며, 공개 주소 점검은 연속 두 번 실패해야 사고를 연다. 같은 병원의
나중 달이 준비된 이른 달의 차단은 알리지 않는다. 배포 중 API 트래픽이 10/9 리비전에 **고정**돼 새 코드가 서비스되지 않는 거짓
성공을 발견해 `--to-latest`로 옮겼고, `deploy.sh`가 고정 트래픽을 배포 전에 막고 배포 뒤 서비스 리비전을 확인하게 했다.
상세는 [반복 알림 근본 수정 배포 기록](docs/releases/2026-10-10-alert-noise-production.md)을 본다.

## 이전 운영 배포 — 2026-10-08 근본 원인 수정(dispatch 배달·운영 채널 하나·운영 신호)

main `c5ada5a1`(PR #226·#227·#228 런타임, #229 문서)을 API·Worker·Beat에 배포했고, 이어 main `de74e3ed`(PR #231)를 두 번째로
배포했다. 현재 리비전은 api `00236-hhz`, worker `00225-cmn`, beat `00220-cxc`이고 Site·Admin은 그대로, **DB head는 `0083_add_operation_run_not_before`**다.
`SLACK_WEBHOOK_URL_DEV` 버전 1을 비활성화해 운영 채널 하나로 전환했다(옛 리비전 롤백 전에 다시 enable 필요). readiness 17개
검사 전부 통과, `slack_webhook_valid={operator: alive, developer: not_configured}`. 유실된 V0 2건을 재실행했고 옛 `SLACK_DEV`
채널 사고는 단일 채널 모드로 닫혔다. 배포 중 네 번째 원인을 발견했다 — 옛 워커가 12:25 KST에 Redis 연결을 잃은 뒤 4시간 10분
동안 조용히 멎어 있었고(로그 0줄), 그 감지 경보는 죽은 개발 웹훅으로 가 전달되지 않았다. 브로커 연결 복원력(소켓 timeout·
keepalive·health check·멎은 소비자 자가 종료)은 PR #231(`ad5a4b69`)로 같은 날 20:57 KST에 두 번째 배포했다(대표 지시, 재발 방지 우선) — api `00236-hhz`, worker `00225-cmn`, beat `00220-cxc`. 옛 워커의 cold shutdown으로 기동 직후 ERROR·WARNING 0건, `/live` 정상. 배포는 하루 한 번, 배치 시간대 밖에서 한다. 상세는
[근본 원인 수정 배포 기록](docs/releases/2026-10-08-root-cause-fixes-production.md)을 본다.

## 이전 운영 배포 — 2026-10-06 원장 보고서 마케팅 검토 반영

PR #211·#212·#213(최종 main `61725afd`)을 API·Worker·Beat에 배포했다. 현재 리비전은 api `00228-wr6`,
worker `00217-nfj`, beat `00212-fbx`이고 Site `00143-mvd`·Admin `00100-nvl`과 DB head
`0082_add_content_reference_checks`는 그대로다. 원장 보고서의 틀 문장·중복 질문·단위 혼동을 고쳤고,
9월 보고서 9곳 전부를 `TEMPLATE_REFRESH`로 숫자 그대로 다시 만들었다(postcheck 9/9 PASS, 측정 미완료 4곳은
운영자 지정 `allow_recovery_pending`). 새 버전은 원장에게 재전달해야 한다. 분류 사전에 검사 키워드를 더해
**10월 월간 측정은 9곳 모두 9월과 비교하지 않는다**(대표 결정). 운영 측정 질문 45건·공개 글 제목 3편의
잘못된 문형도 고쳤다. 상세는 [마케팅 검토 반영 배포 기록](docs/releases/2026-10-06-report-marketing-feedback-production.md)을 본다.

## 이전 운영 배포 — 2026-10-01 미결 PR 정리·원장 보고서 개편

main `a8a86b57`(PR #163·#168·#177·#179·#181~#193)을 5개 서비스에, 이어 #194·#196·#197을 API·Worker·Beat에
배포했다(최종 main `ce731db7`). 현재 리비전은 api `00215-mtl`, worker `00204-q9m`, beat `00199-gq7`,
site `00143-mvd`, admin `00099-fc4`, **DB head는 `0082_add_content_reference_checks`**다. 원장용 월간 보고서가
쉬운 말·새 디자인으로 바뀌었고, 9월 보고서 9곳 전부를 `TEMPLATE_REFRESH`로 숫자 그대로 v2를 만들었다
(postcheck 9/9 PASS, 측정 미완료 4곳은 운영자 지정 `allow_recovery_pending`). 새 버전은 원장에게 재전달해야 한다.
일일 운영 요약이 9/24부터 `fallback_text` 1000자 초과로 저장에 실패하던 기존 장애를 #197로 고쳤다.
배포 전 `.env.production`을 운영 API env로 다시 만들었다(작업 PC 파일이 10/1 모델 변경 이전 값이었다). 상세는
[원장 보고서 개편 배포 기록](docs/releases/2026-10-01-report-plain-language-production.md)을 본다.

## 이전 운영 배포 — 2026-09-28 도입문의 Slack 채널 분리

main `7266674`(PR #164·#165)를 API·Worker·Beat에 배포했다. 현재 리비전은 api `00206-8hf`,
worker `00195-7dt`, beat `00190-bqz`이고 Site·Admin과 DB head `0080_lead_diagnosis_supersede`는 그대로다.
공개 도입문의 접수 알림은 선택 secret `SLACK_WEBHOOK_URL_INQUIRY`(`#noti-도입문의-뉴비짓`)로 가며,
비어 있으면 `SLACK_WEBHOOK_URL`로 간다. Aside로 실제 문의 2건을 제출해 새 채널 수신을 확인했다.
요약은 처리방침의 Slack 고지 범위(병원명·진료과·지역·마스킹 연락처·알려진 유입 경로·접수 시각·
자동 처리)만 싣고 제목에 `[Re:putation]` 출처를 붙인다. 상세는 [도입문의 Slack 채널 분리 배포 기록](docs/releases/2026-09-28-inquiry-slack-channel-production.md)을 본다.

## 최신 운영 배포 — 2026-09-23~24 통합 정합성 보완·파비콘

main `38cff0e`(PR #153 런타임)를 5개 서비스에, 이어 Site만 `fad966d`(PR #155·#156 파비콘)까지 배포했다.
현재 리비전은 api `00201-g5w`, worker `00190-f9b`, beat `00186-8sg`, site `00142-xh2`, admin `00096-rrh`,
DB head는 `0080_lead_diagnosis_supersede` 그대로다. readiness Job, 공개 9개 병원 헬스, 새 리비전 오류 0건을
확인했고 Admin 신규 화면 조작·도입문의 실제 제출은 남아 있다. 배포 전 `.env.production`을 운영 env와
대조한다 — `deploy.sh`가 서비스 env를 파일로 통째로 바꾸며, 이번에는 오래된 파일을 운영 값으로 다시 만들었다.
상세는 [통합 정합성 보완 배포 기록](docs/releases/2026-09-23-integration-hardening-production.md)을 본다.

- 병원 페이지 탭 아이콘은 Re:putation 심볼이 아니라 `/favicon/{slug}`의 병원 모노그램(대표색 바탕 + 병원명
  첫 글자, `lib/clinic-favicon.ts`)이다. 병원 조회 실패 시에도 플랫폼 심볼로 떨어지지 않는다.
  플랫폼 `favicon.ico`는 `site/public/`에 둔다(`app/`에 두면 Next가 병원 페이지에도 링크한다).

PR #153이 더한 계약:

- 갈음된 리드 진단의 복구 요청은 HTTP 경계에서 409다(워커 claim과 같은 집합). 갈음은 옛 진단의
  lead 인시던트를 같은 트랜잭션에서 닫고, 실행 도중 갈음된 진단은 새 인시던트를 열지 않는다.
  값 고쳐 다시 만들기는 고친 병원명을 리드·판정 대상에 반영하고 연락처는 덮지 않는다.
- `POST /admin/lead-diagnoses`는 `Idempotency-Key`로 멱등하다(`CREATE_LEAD_DIAGNOSIS`
  OperationRun 영수증). Admin BFF는 키 없는 생성 요청을 막는다.
- 원장용 PDF는 내부 전용 표식("내부 검수용"·"원장 전달 불가"·"토킹 포인트"·"AE 전용")이
  읽히면 검증에서 거절된다.
- 백엔드 Admin 링크는 병원 4개 탭 경로만 만든다. 옛 경로로 저장된 인시던트 `admin_path`는
  읽는 시점에 새 탭으로 바뀐다(`incident_safety.current_admin_path`, `route-redirects.ts`와 동기).
- Admin의 V0 판정은 `report_type`이다. 서버의 `delivery_tracked`는 V0에도 true다.
- `site/app` 최상위 정적 라우트는 모두 백엔드 예약 slug여야 한다(`contact`·`brochure` 추가).
- NHN 문자 secret은 `INQUIRY_SMS_PROVIDER=nhn`일 때만 조회하고, readiness `facts.inquiry_sms`가
  문자 설정 상태를 참고 사실로 보여준다.
- 미결: 랜딩 병합(#151)이 `/ai-diagnosis` 셀프 신청을 Site에서 종료했고 폼 앵커가 `#contact`로
  바뀌었다(`#lead`는 `#contact`로 보낸다). 아래 도입문의 절의 `#lead`·`/ai-diagnosis 유지` 문구는
  대표 결정 뒤 함께 고친다. 처리방침 버전·수집 항목, `/brochure` 저장 경로도 같은 상태다.

## 최신 운영 배포 — 2026-09-22 노출 진단 수동 생성

main tip `300a6636aee6d5fb73141848f34189194a86c9e5`(기능은 PR #143 `c104bad`)를 5개 서비스에 배포했다.
**DB head는 `0080_lead_diagnosis_supersede`다** — 리드당 진단이 '1건'에서 '활성 1건'이 되고
갈음된 진단이 폴러·운영자 큐에서 빠진다. Admin에 `노출 진단 생성` 탭과 상담 요청의
`값 고쳐 다시 만들기`가 생겼다. CI 10/10, readiness 7개 큐, 공개 9개 병원 헬스 200,
새 리비전 오류 0건을 확인했다. 새 화면의 실제 조작 검증은 남아 있다.
상세는 [노출 진단 수동 생성 배포 기록](docs/releases/2026-09-22-manual-diagnosis-production.md)을 본다.
아래 날짜별 ‘최신/현재’ 표시는 각 배포 당시의 이력이다.

## 최신 운영 배포 — 2026-09-22 도입문의 CTA 전환

PR #141의 main 소스 `8f40f49590345ccd333ff85d786d9fcfe41848f2`를 5개 서비스에 배포했다.
DB head는 `0079_topic_swap_fallback` 그대로이며 schema 변경이 없다.
랜딩의 모든 CTA가 도입문의 폼(`#lead`)을 가리키고, 보고서 재생성이 정상 완료 상태까지 열렸다.
CI 10/10, readiness 7개 큐, 공개 9개 병원 헬스 200, 새 리비전 오류 0건을 확인했다.
도입문의 성공 경로(진단 자동 생성·안내 SMS)의 실제 1건 검증은 남아 있다.
상세는 [도입문의 CTA 배포 기록](docs/releases/2026-09-22-inquiry-cta-production.md)을 본다.
아래 날짜별 ‘최신/현재’ 표시는 각 배포 당시의 이력이다.

## 최신 운영 배포 — 2026-09-22 통합 안정성 보완

PR #138의 main 소스 `a35b7f2a0c8d7ee568abbd9a7abdda75a231576f`를 5개 서비스에 배포했다.
현재 suffix는 `integrity-a35b7f2`, DB head는 `0079_topic_swap_fallback`이다.
main CI 9/9와 별도 큐 리허설을 통과했고, 운영에서 5개 서비스의 digest·트래픽·설정 보존,
16개 readiness 검사·새 release 7개 큐, 공개 9개 병원·기존 글 보존을 확인했다.
2026-09-22 10:05 KST까지 새 리비전 오류 조회 0건이다. 전체 사용자 E2E와 향후 주기 관측은
별도 범위이며, 이 문서의 갱신 커밋은 런타임 이미지 소스가 아니다.
실제 digest·롤백 좌표·검증 범위는 [통합 안정성 배포 기록](docs/releases/2026-09-22-integrity-production.md)을 따른다.
아래 날짜별 ‘최신/현재’ 표시는 각 배포 당시의 이력이다.

## 최신 운영 배포 — 2026-09-17 통합 보완

PR #120의 main 소스 `2381566883cfcfcf87d317a2c9671b96817499a8`를 API·Worker·Beat에 배포했다.
Backend suffix는 `ops-235758-2381566`, DB head는 `0079_topic_swap_fallback`, RedBeat는 `2026-09-17.1`이다.
계약 이행·월간 PDF·도메인 감시 보완이 포함됐다. Site 이미지 최적화와 기존 Admin 리비전은 유지했다.
main CI 9/9, 4,326개 Backend 테스트, 운영 readiness와 공개 9개 병원·151개 글 검증을 통과했다.
개발 Slack 미설정과 기존 NHN SMS 설정 경고는 별도 후속 사항으로 남았다.
아래 날짜별 상태는 당시 이력이며 현재 실행 정본은 [통합 운영 배포 기록](docs/releases/2026-09-17-integrated-production.md)이다.

## 현재 운영 배포 — 2026-09-16 리팩터링

`18531d3452287afa012e28ec256d920708c90a57`(PR #116)이 실제 운영에 배포됐다.
5개 서비스는 `ref-151613-18531d3` 리비전, DB head는 `0079_topic_swap_fallback`이다.
main CI 9/9, 새 release 7개 큐 readiness, 병원 9곳·공개 글 128개·이미지·Admin 로그인
사후 검증을 통과했다. 환경·secret·IAM·자원은 보존했다.
[리팩터링 운영 배포 기록](docs/releases/2026-09-16-refactor-production.md)이 최신 실행 정본이며,
아래 날짜별 상태는 당시 이력이다. 문서 후속 커밋은 런타임 이미지의 소스 SHA가 아니다.

## 이전 운영 배포 — 2026-09-16 GEO

`012aeeb68e1965ea2194300989140ab9b8c4f155`가 실제 운영에 배포됐다(PR #114).
API/Worker/Beat/Site/Admin은 `geo-125451-012aeeb` 리비전, DB head는 `0079_topic_swap_fallback`이다.
9개 병원·128개 공개 글, 현재 릴리스 7개 큐, production Admin 로그인 화면을 사후 확인했다.
Site/Admin 전용 SA로 전환했고 기존 frontend grants는 롤백을 위해 유지했다.
증거·범위·롤백 좌표는 [운영 배포 기록](docs/releases/2026-09-16-geo-autonomy-production.md)을 본다.
아래 날짜별 절의 ‘운영 배포 전’은 해당 시점의 이력이며 최신 배포 상태는 위 리팩터링 절이 우선한다.

## 2026-09-15 GEO 자율 운영 보강 — 로컬 구현 우선 계약

상태: `codex/geo-autonomy-hardening-20260915`, d96bd21 기반 로컬 구현·선별 검증. 운영 배포 전.
기존 문구와 충돌할 때 아래의 새 계약을 적용한다. 상세 구현·한계·검증은 [GEO 보강 기록](docs/releases/2026-09-15-geo-autonomy-hardening.md)을 본다.
정상 생성마다 메시지를 보내지 않되, 하루 한 건의 GEO 운영 요약은 Slack outbox로 보고한다. 기본 18시 KST 이후이며 누락 tick은 당일 다음 hourly tick이 회수한다.
원장 상담 피드백은 다음 신규 생성부터 반영하며 기존 글 일괄 재생성을 하지 않는다. 활성 피드백은 총 6,000자, 중복은 합치고 사람의 기록 종료만 허용한다.
BaseEssence의 일반 자료 추가 drift는 재합성하지 않는다. 명시적 근거 제외·수정은 새 생성만 보류하고 의존 원고를 선택적으로 철회·수리한다. 철회 표시를 단순 발행 판정으로 지우지 않는다.
일정 재설정으로 원고/최초 발행 이력을 삭제하지 않는다. 병원·계약 월 단위로 남은 할당만 채우며 다음 달 plan 변경은 append-only다.
주요 공개 변경과 SITE_REVALIDATION intent는 한 transaction이다. cache invalidation 수락을 실제 환자 페이지 공개 확인으로 표시하지 않는다.
새 생성 task는 소유권이 검증된 OperationRun을 바탕으로 실행 시 reservation token을 execution token으로 바꾸며, 대기 시간을 실행 lease로 오인하지 않는다.
Site/Admin IAM 분리는 단계적으로 적용한다. 구 revision 종료 전 legacy grants를 회수하지 않는다.


문서 버전: **2.23** · 갱신일: **2026-10-13 (Asia/Seoul)**
소스 기준선: **`4db1b69` 이후 커밋 이력 정합성 보완**
구현 상태: **체크포인트 2(`a774851`) 운영 배포 완료. 그 뒤 main의 stable-base Essence(`8c59141`) 등 31개 커밋과 콘텐츠 수율 버전업 v2.7(`claude/system-performance-review-x6vtn4`, [계획](docs/plans/2026-09-12-content-yield-versionup-plan.md))은 미배포**

이 파일은 과거 제품 브리프를 현재 코드 기준의 개발 안내로 교체한 것이다. 전체 흐름과 근거 파일은 [현재 시스템 구조](docs/architecture/system-map.md), 문서의 지위는 [문서 인덱스](docs/README.md)에서 확인한다. 코드 기본값과 운영 환경, 목표 정책과 현재 구현 차이를 구분한다.

## 운영 철학

- 사람의 최소한의 개입으로 지속 운영한다. 정상 생성·측정·발행과 자동 복구에 수동 승인이나 반복 알림을 추가하지 않는다.
- 사람이 맡는 일은 계약 인수·공식 정보와 공개 주소 결정·자동 검토가 해결하지 못한 예외·고객 보고서 전달이다.
- 자동 복구 중인 작업을 운영자의 할 일로 만들지 않는다. 최종 차단은 원인별로 묶고 중복을 억제한다. 개발 문제가 운영 채널로 쏟아지지 않도록 한다.
- 상태 저장, 외부 호출, 알림 성공을 구분한다. 저장 성공 후 큐·캐시·Slack 장애가 났다고 원래 업무를 실패한 것처럼 되돌리거나 중복 생성하지 않는다.

## 2026-10-09 purpose-first refactor 실행 계약

이 절의 **PROPOSED**는 purpose-first refactor가 구현할 단일 제품 기준선 이름이다. 이 기준선의 구현은 현재 작업에서 승인되었지만, 이 문서 변경은 배포 완료·운영 데이터 변경·과거 보고서 재작성의 증거가 아니다. 배포는 [운영 배포 절차](docs/ops/deployment-runbook.md)의 별도 검증으로만 기록한다.

- 병원 공개는 `profile_complete && site_built` 뒤 기본 주소에서 자동 활성화한다. V0, `schedule_set`, Essence는 공개 활성화의 조건이 아니며, 자체 도메인은 기존 DNS·TLS 확인을 통과해야 한다. PAUSED, 명시적 비공개, 공개 권한 회수는 자동 활성화·복구로 되돌리지 않는다.
- 승인된 운영 기준과 일정으로 생성된 안전한 콘텐츠는 **자동 발행**한다. AE는 정상 발행의 승인자가 아니라 재시도로 해결되지 않은 terminal exception, 명시적 정정·철회, 공식 정보·공개 주소 결정만 처리한다. 콘텐츠 공개는 여전히 tenant 경계, 공개 권한, 의료·사실 검수, 금지 표현, 승인 근거와 artifact 무결성 검사를 모두 통과해야 한다.
- 이미지 정책의 단일 PROPOSED 순서는 검증된 글 전용 이미지, 검증된 병원 공용 이미지, **이미지 없음** 또는 현재의 장식 모티프다. 이미지 부재·일시 생성 장애·형식 문제는 안전한 본문 공개를 막지 않는다. 검증되지 않은 이미지, 합성 provenance/hash, 실제 원장으로 오인될 생성 인물은 허용하지 않는다.
- 근거 자료의 수정은 기본적으로 보수적인 정정/철회로 취급한다. 과거 승인 snapshot을 유지하는 **future-only** addendum/version은 명시적으로 선택한 동작이며, 숨김 상태·`authority_change`·의료 또는 사실 위험을 자동 해제하거나 과거 공개물을 자동 재공개하지 않는다.
- 월간 보고서는 닫힌 기간만 COMPLETE/LIMITED/**UNAVAILABLE** 중 하나로 표현한다. UNAVAILABLE은 `sov_pct=null`과 플랫폼별 confirmed/failed/ambiguous/pending 수, 콘텐츠 실적과 다음 조치를 담은 정직한 운영 보고서로 전달 가능하다. null은 0이 아니고 비교 근거가 없으면 delta를 숨긴다. 병원·기간·대상·경로·bytes·digest가 검증된 artifact와 일치할 때만 AE가 **사람 전달** 사실을 append-only로 기록한다. 시스템은 원장에게 자동 전달하지 않는다.

## 현재 기술·책임 경계

- Backend: Python 3.11/FastAPI, PostgreSQL/SQLAlchemy/Alembic, Celery/Redis/RedBeat, Jinja2/WeasyPrint. API async와 Worker sync 세션이 공존한다.
- Admin/Site: Next App Router, 저장소 선언 기준 Next 16.3.8, standalone 서버. Admin은 내부 전체 병원 운영 콘솔이며 브라우저→인증 BFF→Backend 구조다. 사람이 일으키는 admin 변경(POST/PATCH/PUT/DELETE)은 BFF가 서명한 actor 단언(`X-Admin-Actor-Assertion`, `BFF_ACTOR_SECRET`, 120초)을 요구하며, 배치·CLI는 `X-Admin-Actor-System`으로 감사에 `system:<job>`으로 남는다. 공유 `X-Admin-Key`만으로 actor를 고르는 경로는 없다.
- 운영 배포: API, Worker, Beat, Admin, Site 모두 GCP Cloud Run. Cloud SQL, Memorystore, GCS, HTTPS Load Balancer와 인증서 구성을 사용한다.
- 모든 LLM·이미지 호출은 `OPENROUTER_API_KEY` 하나로 OpenRouter 게이트웨이를 거친다 — 콘텐츠는 Claude 계열, 이미지는 Gemini/OpenAI 계열, 측정은 OpenAI/Gemini 계열 모델을 `vendor/model` 슬러그로 호출한다. 공급자 직결 SDK(Anthropic·OpenAI·google-genai·Vertex)는 쓰지 않는다. 개발 에이전트 모델과 서비스의 모델을 혼동하지 않는다. 실제 모델은 `backend/app/core/config.py`와 배포 설정으로 확인한다.
- `build_aeo_site`는 상태 준비·자동 활성화 작업이다. 별도의 `site_builder.py`나 병원별 HTML/CSS 생성기를 전제로 개발하지 않는다.

## 변경 시 보존할 계약

### 공개와 콘텐츠 게이트

공개 활성화의 공통 선행조건은 `profile_complete && site_built`다. V0 초기 진단은 독립 백그라운드 작업이며 공개 시작을 막지 않는다. 일정·Essence를 활성화 선행조건에 추가하지 않는다. 기본 주소는 조건 충족 시 자동 활성화하고, 자기 도메인은 기존 도메인·TLS 확인 경로를 따르며(대기 중이면 15분마다 시스템이 수동 확인과 같은 경로로 확인한다), PAUSED는 배경 작업으로 재개하지 않는다. 상태 변경은 서비스 구간·도메인 확인·감사 기록까지 검수한다. `PROFILE_GATE_OPTIONAL_ENRICHMENT`(기본 **False**, 대표 결정 전)가 True이면 프로필 완료 판정(=공개 활성화 선행조건)에서 원장 약력·진료 철학·Google 병원 정보를 보강 항목으로 내린다 — 원장명·네이버 플레이스·주소/전화/진료시간·홈페이지 또는 블로그·지역/키워드는 계속 필수이고 요구 키(`director_basic`·`director_philosophy`·`ai_channels`)는 그대로다. 기본 꺼짐에서는 종전 게이트 그대로다.

`PENDING_DOMAIN`(자기 도메인 `aeo_domain` 보유) 병원은 15분 도메인 감시(`monitor_live_custom_domains`)가 수동 "지금 확인"과 **같은 경로**(`verify_domain_for_hospital`, actor `system:domain-dns-poll`)로 DNS를 확인해 맞으면 활성화·인증서 작업을 연다. 창은 도메인을 마지막으로 저장한 시각(`connect_domain` 감사행 → 병원 행 `updated_at` → 인증서 작업 시작 시각)부터 `DOMAIN_DNS_POLL_MAX_DAYS`(14)일이고, 창이 지나면 사람의 일 `CUSTOM_DOMAIN_DNS_NOT_CONFIGURED`를 **한 번만** 열되 **폴링은 멈추지 않는다**(늦게 맞춘 병원도 같은 경로로 열린다). 맞으면 사고를 조용히(`notify=False`) 닫는다. 도메인을 다시 저장하면 창을 새로 센다. 게이트 미충족·인증서 진행 중(HTTPException)은 건너뛰고, 한 도메인의 오류가 나머지를 막지 않으며, 폴링 실패가 라이브 도메인 감시 결과를 지우지 않는다.

콘텐츠 신규 생성·자동 발행은 일정과 승인된 BaseEssence를 사용한다. 일반 자료 추가·노이즈 hash 변화는 진단 정보이며 자동 운영을 막지 않는다. 명시적 승인 근거 철회·수정만 재승인 전 신규 생성을 보류한다. `EssenceReadiness.current`와 기존 승인 근거를 유지하는 `public_philosophy`의 목적을 섞지 않는다. 일반 텍스트 자료 생성·실질 변경은 durable 처리 run에 연결하고, 동일 정규화 값 PATCH는 근거와 처리 상태를 바꾸지 않는다. 사진과 원문 없는 URL 자료는 이 자동 처리 대상과 구분한다.

Essence가 **자동 승인**되면(또는 15분 조정이 일정이 한 번도 없던 공개 병원을 회수하면 — 사람이 승인한 Essence도 여기서 회수된다) 시스템이 그 병원의 **첫 발행 일정**을 계약 요금제로 만든다(`initial_schedule.ensure_initial_content_schedule`). 대상은 일정 행이 한 번도 없고 `schedule_set`이 아닌 `ACTIVE`·`PENDING_DOMAIN` 병원이다(`INITIAL_SCHEDULE_STATUSES`; PAUSED 제외). 사람이 끈 일정을 포함해 일정 행이 하나라도 있으면 되살리지 않는다(`SCHEDULE_EXISTS`). 기본 발행 요일은 PLAN_20 월~금, PLAN_16 월~목, PLAN_12 월·수·금(`DEFAULT_PUBLISH_DAYS_BY_PLAN`, `admin/lib/schedule.ts`와 같은 값)이고 시작일은 `max(내일, 계약 효력일)`이다. 첫 계약 월은 **남은 게시 요일 비율로 안분**한다 — 약속 편수 = `ceil(남은 게시 요일 ÷ 그 달 전체 게시 요일 × 요금제 편수)`(최소 1, 1일 시작이면 요금제 그대로), 남은 게시 요일이 없으면 **0편**이라 그 달은 슬롯을 만들지 않고 월말 배치가 다음 달을 만든다(`content_month_target.prorated_first_month_quota`). 그 달 약속 편수의 근거는 첫 일정을 만든 감사행(`set_schedule`, actor `system:essence-auto-approval`, `target_id` = 만든 일정)이 기록한 `active_from`·`first_month_target`이고(슬롯의 `total_count`나 병원의 가장 이른 일정을 읽지 않는다), 계약 이행 집계·현황·보고서·당월 조정·**사람의 일정 교체**가 같은 값을 읽는다(`content_month_target.month_content_target(_sync)`; 0편이면 약속 0). 그래서 사람이 같은 달에 요일·시작일을 바꿔도 안분 편수(0편 포함)는 그대로이고 교체 경로도 그 편수까지만 슬롯을 만든다. 안분은 **시스템이 만든 첫 일정**에만 적용되고, 사람이 만든 첫 일정은 종전대로 계약 편수를 채운다. 감사행은 사람 경로와 같은 `set_schedule`(+ `trigger`·`contract_effective_on`·`first_month_target`)이며 사람은 언제든 콘텐츠 화면에서 바꿀 수 있다. 일정 생성 실패는 Essence 승인을 되돌리지 않는다(15분 조정이 다시 시도).

수동 예외 처리와 자동 발행은 공통 콘텐츠 안전 검사를 사용하지만 현재 생애주기·예정일 조건은 완전히 같지 않다. 정상 경로는 자동 발행이며 수동 행위가 이를 대체하는 일반 승인 단계가 아니다. Public API의 활성화·자료·본문·참고자료 검사를 제거하지 않는다. DB PUBLISHED만으로 공개 성공을 선언하지 않는다.

### 콘텐츠와 월간 계약

- 유형은 FAQ/DISEASE/TREATMENT/COLUMN/HEALTH/LOCAL/NOTICE, 월간 계약은 12/16/20편이다. 기본 배분은 `models/content.py`, 노출 부족에 따른 조정은 `gap_driven_slots.py`다.
- 현재 생성 분량 검사는 평문 1,800~5,200자다. FAQ의 전용 질문/답변, NOTICE의 참고자료 예외를 생성·편집·발행·공개에서 맞춘다. 참고자료 필수 판정은 `services/reference_requirement.py` 한 규칙이다 — 의료 안내 유형은 항상, NOTICE는 측정 질문이 연결됐을 때(`query_target_id`·브리프의 `query_target`·`exposure_action.query_target_id`) 필수이고, 질문 연결 없는 순수 운영 공지만 면제다(2af00d02 0개 공개·5821409e 영구 미발행 양쪽). 브리프의 `target_keyword`·`treatment_narrative`·계획 모드만으로는 판정하지 않는다. 생성(규칙·스키마·치유·GEO 거절)과 발행 게이트(07:45·08:00·catch-up·수동 발행·restore)가 이 규칙을 쓰고, 필수 글이 비면 수기 목록 치유를 먼저 한 뒤 `MISSING_REFERENCES`로 보류한다. 공개 가시성(`content_visibility`)만 이미 공개된 글을 배포로 내리지 않으려고 유형 규칙을 유지한다(`public_surface_has_required_references`).
- 의료광고 금지 표현은 `utils/medical_filter.py`를 정본으로 삼는다. 참고자료 제목과 공개 메타데이터 등 새 공개 필드도 검사 대상에 포함한다. 문맥 예외(역학 통계의 1위, 부정·한정이 바로 뒤따르는 완치·100%·성공률 등)는 필터 안에서만 정의하고 양방향 테스트를 함께 둔다. 경로별 허용 목록이나 우회를 만들지 않는다. 화이트리스트 도메인의 참고자료 제목이 필터에 걸리면 생성 단계에서 기관명 라벨로 치환하고, 발행·공개 게이트는 모든 제목을 그대로 검사한다.
- 작가 프롬프트와 검증기는 같은 단위를 말해야 한다. 분량은 공백·마크다운 제외 평문 1,800~5,200자이고 프롬프트도 그 단위로 요구한다. 참고자료 필수 유형은 생성과 발행이 같은 집합을 쓰며 프롬프트가 비워 두라고 지시하지 않는다. 결정적 검증기의 거절은 같은 프롬프트로 blind 재시도하지 않고 지적 내용을 다음 회차에 넘긴다. `stop_reason`이 `max_tokens`/`refusal`이면 잘림으로 처리한다.
- 필수 문구는 **현재 APPROVED 승인본의** `must_use_messages`뿐이다. 콘텐츠 가이드(brief)에 저장된 문구·운영자가 가이드에 덧붙인 문구는 원문 요구 대상도 검수 면제 근거도 아니며, 승인본이 없으면 요구·면제가 없다(재검수 스윕·공개 재검수 백필 포함). 필수 문구는 의역하지 않고 원문 그대로 독립된 문장으로 본문에 넣는다. 생성 후 `services/must_use_verbatim.py`가 확인하고, 빠지면 기존 재작성 루프·호출 예산 안에서 다시 쓰며 끝내 빠지면 `GENERATION_REJECTED`(본문 표본 실패)다. 의료광고 금지 표현이 든 승인본 문구는 작가 프롬프트·원문 요구·면제에서 모두 제외하고, 제외할 때마다 경고 로그를 남기며 승인본 버전당 한 건의 운영자 인시던트(`must_use_exclusions`)로 기록한다. 독립 검수 지적의 `quote`가 후보 안의 필수 문구 원문과 정규화 기준으로 완전히 같으면 그 지적은 `target=MUST_USE_MESSAGE`·SOFT로 기록만 남고 게시를 막지 않는다. 일부만 겹치거나 말을 덧붙인 문장, 인용 없는 지적, 지적 문구가 다른 문장을 함께 인용·지목한 지적은 면제하지 않고 그대로 막는다. 정규화는 숫자 사이의 공백·기호를 지우지 않는다(`1 0cm`≠`10cm`, `1.0cm`≠`10cm`). 단어·어간 목록으로 판정하지 않는다.
- LLM 출력은 확률적이므로 작가·이미지·독립 검수의 실패는 `SAMPLE_RECOVERABLE`로 분류하고 KST 하루 단위 예산(본문 2세션, 이미지 4회)과 소진 3일 상한 안에서 재시도한다. 소진 뒤에만 `OPERATOR_REQUIRED`로 전이해 원인별 인시던트 1건을 OPEN으로 올린다. 본문 표본 실패는 예외로 계단이 하나 더 있다 — 3일 소진 뒤(현재 주제의 작가 예산 6회가 다 차 `GENERATION_BUDGET_EXHAUSTED`가 되면 소진 일수를 기다리지 않고 곧바로) 같은 슬롯·같은 예정일로 주제 교체를 1회 거친 뒤에만 `OPERATOR_REQUIRED`가 된다. 3일 공급자 장애가 주제당 본문 전송 상한(36)을 다 써 생긴 소진도 같은 교체를 쓴다(전송 예산이 없는 주제는 교체로만 이어갈 수 있다). 교체된 주제는 작가·검수 결과 각 6회와 본문 전송 36회를 새로 받는다 — 평생 계수는 지우지 않고 교체 시점의 획득 위치(`prior_topic_spend`)부터 센다. 슬롯 평생 상한은 주제 수 × 주제당 상한(작가 12회)이고, 이미지 전송 상한(12회)은 슬롯 단위로 유지한다(`generation_attempt_state.py`). 교체 대상은 공개 이력(`first_published_at`)과 사람 편집(`human_edited_at`)이 없는 글뿐이고, 교체는 인시던트 epoch를 새로 열어 옛 주제의 인시던트를 닫고 새 주제의 실패를 새 건으로 연다. 모델이 HARD로 단정한 사실·안전 지적 중 인용(`quote`)으로 문장을 짚은 것과 응급 안내 누락(HARD/UNCERTAIN)은 사람의 PATCH를 기다리지 않고 최소 교정 패스(`services/content_minimal_correction.py`)가 푼다 — 지적 문장만 승인 프로필대로 고치거나 지우고(새 수치·고유명사가 들면 거절하고 삭제), 응급 안내는 코드 상수 템플릿을 넣으며, 지적 문장 밖이 바뀌거나 본문이 분량 하한(생성 검사와 같은 `CONTENT_BODY_MIN_CHARS`, 순수 1,800자) 아래로 내려가면 거절한다(원문이 하한을 지킨 경우). 교정본은 반드시 독립 재검수를 받고, 게이트는 교정 기록(`auto_correction`)이 있으면 교정본 hash에 묶인 PASS 없이는 통과시키지 않는다. 첫 생성(빈 슬롯)도 이미지 구매 전에 같은 패스를 거친다. 교정 패스는 발행 이력·사람 편집이 있거나 DRAFT·READY가 아닌 글, 필수 문구 문장(여러 문장짜리 문구의 한 문장 포함)을 고치지 않으며 저장 UPDATE도 같은 술어를 건다. 공개 중인 글의 사후 검수 FLAGGED만 예외로 공개 모드가 맡는다(후행 검수 항목) — 그 모드도 필수 문구 문장은 보호하고 제목은 바꾸지 않는다. 검수 장애 물러서기·한도(`CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES`)·비용 보류 중에는 돌지 않고, 두 계수는 서로의 기록을 쓰지 않는다. 글(주제)당 교정·재검수 상한(`CONTENT_AUTO_CORRECTION_MAX_PASSES`·`_MAX_REREVIEWS`, 기본 2·2)을 넘기면 주제 교체(`CONTENT_AUTO_TOPIC_SWAP_MAX`, 기본 1), 그것도 다 쓰면 OPERATOR_REQUIRED다. 23:00 창이 내일 글을 담으므로 전날 밤에 교정된다. 주제 선정은 키워드에만 있고 진료 항목에 없는 검사·시술 질문을 고르지 않는다. 주제 교체 후보는 최대 5개를 차례로 살핀다(진료과 불일치·수기 목록 문서 없음·유사 주제는 제외 집합에 넣고 다시 고른다, `TOPIC_SWAP_CANDIDATE_LIMIT`). 승인 기준 없음, 저장 본문의 금지 표현은 `INPUT_CHANGE_REQUIRED`로 남긴다. 문장 단위로 고칠 수 없는 모델 HARD(인용 없음·제목·필수 문구 인용)는 교정 이력과 무관하게 주제 교체 계단(교체, 그다음 사람)으로 넘긴다. 저장 본문 수리 세션 예산(3일)을 다 쓴 `MISSING_REFERENCES`·`FAQ_FIELDS_MISSING`·`ESSENCE_NOT_ALIGNED`도 같은 주제 교체 계단(교체 1회, 그다음 사람)을 탄다. 참고자료 때문에 막힌 슬롯은 진료비·병원 선택 질문으로 바꾸지 않는다. 진료비·병원 선택 보류는 `CONTENT_TOPIC_SWAP_OPERATOR_DECIDES`(기본 꺼짐)가 켜져 있을 때만 수기 목록 문서가 있는 의료 주제로 한 번 교체하고, 꺼져 있으면 종전대로 곧바로 `OPERATOR_REQUIRED`다. `scheduled_date`는 시도 지문에 넣지 않는다.
- 독립 AI 검수의 확신도 부족만으로 붙은 합성 UNCERTAIN은 같은 호출 안에서 상위 모델로 1회 재검수하고, 저장된 UNCERTAIN 차단은 스윕이 예산 안에서 재검수한다. HARD 사실·안전 지적은 삭제형 재작성 1회 뒤 재검수를 거친다. 미해결 HARD·UNCERTAIN이 발행을 막는 계약과 UNAVAILABLE이 PASS를 주지 않는 계약은 그대로다. PASS·비차단 REVISE도 그 검수가 본 본문에만 유효하다 — `candidate_sha256`이 현재 본문과 다르면 발행 게이트가 `CONTENT_AI_REVIEW_STALE`로 막고(PATCH 편집이 검수 없이 발행되지 않게), PATCH가 단 `edited_after_review` 표시를 야간 재검수 스윕이 집어 재검수한다(비공개 글은 03:10 사후 검수 스윕이 맡는다). 시스템의 참고자료 치유·제거(`apply_publication_reference_refresh`)는 변경 직전 해시에 묶인 PASS를 새 해시로 옮겨(`restamp_review_after_system_reference_change`) 예정 발행을 놓치지 않게 하고, 사람의 PATCH는 옮기지 않는다. 해시 없는 옛 기록은 그 이유만으로 재검수하지 않으며, **이미 공개된 글은 이 이유로 숨기지 않는다**(공개 가시성은 해시를 보지 않고 사후 검수가 이어받는다).
- 이미지는 업로드 전 정책 검토를 거치며 이미지가 있는 새 발행은 내용 hash·주제 hash·정책 버전에 연결된 인증을 요구한다. 생성 인물을 실제 원장 신원으로 사용하지 않는다. 2026-09-07~08 전환에서 당시 운영 공개 글 115건의 기존 이미지를 실제 바이트 재검수·content-addressed 불변 사본·CAS 저장으로 이 계약에 편입했다. 공개 GCS 이미지 프록시 URL은 인증된 내용 hash를 버전 질의값으로 포함해야 하며, 이미지 교체 때 그 값도 바뀌어 Site 이미지 캐시가 이전 바이트를 계속 제공하지 않게 한다. 비공개 기존 행도 이미지가 있으면 일반 생성·발행의 엄격한 인증 gate를 통과해야 한다. 영구 레거시 우회나 합성 인증값을 만들지 않는다. 이미지 생성이 실패해 당일 예산이 소진되면 같은 병원의 검증된 글 전용 이미지, 이어 검증된 병원 공용 이미지를 쓸 수 있으며 provenance를 그대로 보존한다. 둘 다 없으면 **이미지 없음** 또는 현재의 장식 모티프로 안전한 본문을 발행한다. 이 순서는 의료·사실·tenant·공개 권한·본문 안전 검사를 완화하지 않으며, 이미지 부재만으로 `CONTENT_IMAGE_NOT_READY`를 만들지 않는다.
- 생성 claim 이후 외부 호출 결과를 저장할 때 현재 상태·claim token·content revision을 재확인한다. 취소·발행·최근 편집을 늦은 응답으로 덮어쓰지 않는다.
- 23:00과 복구 스윕(01·04·07·12·18·22시, `RECOVERY_SWEEP_HOURS`) 배치는 디스패처다. 슬롯을 claim한 뒤 글 단위 태스크 `generate_claimed_content_item`(content 큐, claim token 동반, `GENERATE_CONTENT_ITEM` OperationRun)으로 넘기고 즉시 끝난다. 글 단위 태스크는 lease 토큰이 바뀌었거나 만료됐으면 공급자 호출 없이 물러난다. 작가 결과의 저장 재생은 같은 예약(reservation token, 태스크의 `claim_token` 인자) 안에서만 한다 — 같은 예약의 재배달은 실행 토큰(`generation_claim_token`)이 회전해도 산 결과를 다시 쓰고, 스윕이 새로 claim한 예약은 새 표본을 산다(검수는 후보 hash 단위로 재생한다). 재시도는 태스크가 아니라 스윕과 재시도 정책이 소유한다. 23:00 창은 내일과 그 다음 날(`NIGHTLY_GENERATION_LOOKAHEAD_DAYS=2`)이고 병원 간 라운드로빈으로 상한 50을 나눈다. 복구 스윕의 창은 발행 catch-up과 같은 7일이며, 로더가 워커와 같은 규칙으로 재시도 불가 행을 claim 전에 거른다. 30분 유예 안의 claim은 진행 중인 일감이라 stuck으로 보지 않는다. 종착 기록(`generation_retry_policy.attempt_is_terminal` — 소진된 본문 표본 실패 `OPERATOR_REQUIRED`·사람이 정하는 참고자료 보류·승인 Essence 없음)이 입력 지문 그대로이고 판정 뒤 사람 편집이 없으면 로더(claim 전)와 워커가 같은 술어(`tasks._terminal_attempt_is_unchanged`)로 건너뛴다 — 재집기가 OperationRun·인시던트 쓰기만 늘리기 때문이다. 입력(승인 자료·유형·질문)이 바뀌거나 사람이 고치면 다시 시도하고, 본문 수리·최소 교정·이미지 재사용 코드는 종착으로 보지 않는다. 야간 로더의 참고자료 필수 판정은 `reference_requirement`가 정본이며(NOTICE의 질문 연결 포함) 주제 교체 상한은 `topic_swap_fallback.topic_swap_budget_left` 한 곳이다. 처리량은 `CELERY_CONCURRENCY × (8h ÷ 6분)`으로 추정하며 부족하면 디스패치 시 경고를 남긴다. 07:45·08:00·매시 게이트가 본 빈 슬롯의 원고 미생성(`CONTENT_NOT_GENERATED`)은 저장된 원인이 없거나 환경 장애면 복구 가능한 증상(`ENVIRONMENT_RECOVERABLE`·3일 창)이라 다음 스윕이 다시 쓰고 그동안 인시던트는 RETRYING이다(창이 닫히면 스윕 소유가 아니다). 소진된 표본·입력 변경 같은 종착 기록을 덮을 때는 종전처럼 사람의 일이다.
- 참고자료의 주제 불일치는 차단이 아니라 제거다. 사람이 실제 GET으로 근거가 될 수 없다고 확인한 주소는 `REFERENCE_URL_EXCLUSIONS`(항목마다 사유)에 두고 수기 목록 후보·모델 참고자료·저장된 통과 어디서도 인정하지 않는다(수기 목록과 같은 문서 판정으로 비교해 별칭도 막고, GET의 최종 주소나 저장된 통과 기록의 최종 주소가 제외 문서여도 같다). 기본 선택지는 검증된 수기 목록(`CURATED_MEDICAL_SOURCE_PAGES`)이고, 목록 밖 URL은 실제 GET(200·화이트리스트 유지·soft-404/빈 템플릿/통계·메뉴 아님·실제 제목·본문이 글 주제와 일치)을 통과해야만 남는다. 판단 불가(403/429/시간 초과/영문·기관명뿐인 제목/운영 외 환경의 오프라인 기본값)는 목록 밖이면 제거, 목록이면 유지다. 모델 라벨은 주제 판정에 쓰지 않는다. 검증 결과는 `content_items.reference_checks`(0082)에 남고, 07:45·08:00·수동 발행은 같은 URL·같은 글 주제 지문(제목·첫 H2·FAQ 질문·brief)의 24시간 내 통과 기록을 요구해 없으면 다시 검증한다(미래 시각 기록은 5분 시계 차이까지만, 형식이 깨진 항목은 실패). 기관 장애는 같은 URL·같은 주제의 7일 내 직전 통과를 재사용하고, 그것도 없는 목록 밖 URL의 일시 장애(연결·시간 초과·5xx·408/429)는 빼지 않고 다음 시간대로 미루며 예정일 23시 발행기부터 08:00 요약에 '기관 사이트 접속 불가'로 알린다. 실행당 GET 상한 초과도 미룸이다. 비어지면 글 주제로 채점한 수기 목록 치유, 그래도 없으면 `MISSING_REFERENCES` 보류다. 진료비·병원 선택 글(제목 기준, `reference_requirement.references_left_to_operator`)은 공신력 있는 문서가 본질적으로 없어 생성·발행 모두 수기 목록으로 채우지 않고, 비면 그 보류는 자동 본문 수리·주제 교체 없이 곧바로 `OPERATOR_REQUIRED`(사람이 정함)다(`CONTENT_TOPIC_SWAP_OPERATOR_DECIDES`를 켜면 주제 교체 1회를 먼저 거친다). 병원 선택은 제목에 수기 목록의 질환·시술 키워드(의료 주제)가 없는 고르기 글만이다(`title_names_medical_subject`). 아직 쓰이지 않은 슬롯은 작가가 만든 제목으로 판정해, 작가 회차 뒤에도 참고자료가 0개면 표본 사다리 없이 `MISSING_REFERENCES`+`operator_decides` 기록과 기한 없는 OPEN이 된다. 통과한 참고자료가 있으면 그대로 발행한다 — 단, 발행 전(DRAFT·READY와 생성 중인 슬롯) 진료비·병원 선택 글에서는 작가가 인용해 통과한 수기 목록 문서(`is_curated_source_url`, 별칭 포함)도 빼고 목록 밖 문서의 실제 GET 통과만 남긴다(그 통과는 치유와 같은 카탈로그 키워드 대조다). 공개된 글의 참고자료는 그대로다. 생성 프롬프트의 검증된 문서 힌트도 브리프의 측정 질문(질의·핵심 키워드·질문 이름)이 진료비·병원 선택이면 주지 않는다(생성 뒤 치유 판정은 작가 제목 그대로). 수기 목록 대조·치유 선택·의료 주제 판정은 진료과 이름과 병원 고르기 경로 키워드(정형외과·심장내과·병원선택·통증종류 …, `authority_sources.keyword_names_provider`)를 주제로 세지 않는다. 관리자 PATCH가 발행 전 진료비·병원 선택 글(저장될 제목 기준)에 수기 목록 문서를 담으면 GET 전에 422 `CURATED_REFERENCE_NOT_ALLOWED`로 거절한다. 수기 목록 문서인지는 목록 항목별 문서 판정(`authority_sources._matching_documents`: scheme·www·기본 포트·경로 표기·항목이 쓰지 않는 질의 무시, 항목 URL 자신의 id 이름마다 정수 비교 — KDCA `cntnts_sn`은 값의 숫자만 모아, 다른 이름은 앞 정수로 — 반복 값은 빼는 판정(`is_curated_source_url`·제외 목록)은 상한 없이 하나라도 맞으면, 인정해 주는 판정(`curated_source_entries`·검증기의 `curated`)은 서버가 쓰는 첫 값만)으로 판정하고, GET의 최종 주소가 목록 문서면(리다이렉트) 그 주소도 목록 문서다 — 생성·발행 재검증은 빼고 PATCH는 GET 뒤 같은 422다. GET은 행·병원 잠금 밖에서 하고 잠근 뒤 상태·판·참고자료·주제가 그대로이고 발행 전(DRAFT·READY)일 때만 적용한다 — 공개된 글의 참고자료는 어떤 자동 경로도 바꾸지 않는다. 아직 생성되지 않은 슬롯(제목·본문 없음)은 재검증·치유하지 않는다(판이 올라 진행 중인 생성 저장이 버려진다). 관리자 PATCH도 같은 검증을 거쳐 실패 URL과 사유를 400으로 돌려준다. restore는 검증만 하며 공개됐던 글의 참고자료를 바꾸지 않고, 통과하지 못하면 409로 거절한다. 상세는 `services/reference_verification.py`. 검수자의 REFERENCE 지적은 SOFT 비차단이며 해당 출처만 제거한다. 중복 주제 유사도 검사는 어떤 경우에도 차단·인시던트·Slack을 만들지 않는다. 문체 보완 예산 1회로 다른 각도를 요구하고, 그래도 유사하면 발행하고 `duplicate_topic_findings`에 기록만 남긴다.
- 재사용 이미지는 같은 content_type을 먼저 고른다. 재사용할 글 이미지가 없으면 병원 대표 이미지(`hero_image_url`)를 병원 단위로 한 번 인증(로고·병원 정체성 허용, 문자·인물 금지, 주제 일치 요구 없음)해 `image_fallback_source='HOSPITAL_HERO'`로 붙인다. 이 모양도 내용 hash와 정책 버전을 요구한다. 검증된 대체 이미지가 없으면 이미지를 생략하고 본문 공개를 계속한다.
- Celery 밖의 외부 감시(`/admin/watchdog/pipeline`, Cloud Scheduler 5분·08:30)가 canary 신선도·Beat 생존·당일 발행 0건을 보고 Slack 웹훅에 직접 보낸다. 감시 토큰이 비어 있어도 부팅은 막지 않는다. 인프라 조건은 개발 대상, 발행 누락은 운영 대상이며 개발 웹훅이 없거나 실패하면 운영 채널로 보낸다(일반 알림도 같다). 경보는 조건 집합 에피소드당 한 번이고(전달이 확인되지 않은 경보만 15분 재시도 — 응답 불명 포함, 복구 한 번), Beat·대기열 정지와 `publish_missing`은 4분 이상 간격의 연속 두 점검으로 확정·해제하며, 발행 조건은 KST 날짜에 속해 자정에 조용히 사라진다. 안전검사가 예정 글을 모두 보류한 날(`publish_gate_residual`)은 감시가 알리지 않는다 — 보류 요약·사고·일일 요약이 소유한다. 공개 주소 점검은 연속 두 번 실패해야 병원 사고를 열고, 한 회차에 두 병원 이상이 공용 사이트 5xx면 개발 대상 `PUBLIC_SITE_UNAVAILABLE` 하나로 묶는다.
- 생성 당시 Essence와 최근 재검사 Essence를 분리한다. 전체 공개 후보 hash·검수 coverage와 unresolved HARD/UNCERTAIN finding을 발행 게이트에서 보존한다.
- 2026-09-07~08 기존 공개 글 전환은 고정된 운영 manifest와 CAS로 수행했다. FAQ 3건 구두점 수리, 본문 22건 독립 검수, 이미지 115건 인증 뒤 정확한 공개 ID 집합과 새 엄격 공개 gate를 read-only로 검증했다. AI 검수 메타데이터가 레거시인 글을 그 이유만으로 유료 재검수하지 않는 원칙을 유지한다. 상세 수치와 증거는 [운영 전환 기록](docs/releases/2026-09-07-eb55518-partial.md)을 본다.
- 지연 발행은 원 계약 월을 보존한다. `published_at`·`published_by`는 현재 공개 판을 나타내고, 처음 공개한 사실은 `first_published_at`·`first_published_by`에 한 번만 기록해 실제 발행·계약 이행·귀속의 닫힌 월 집계에 사용한다. 반려나 근거 철회로 현재 공개 판이 내려가도 최초 사실을 지우지 않으며 재발행은 현재 판 시각만 갱신한다. 마이그레이션 전에 반려가 이미 지운 발행일은 추정해 복원하지 않는다.
- 후행 검수는 조건부 표본 확인이다. 모든 글의 수동 승인이나 월간 보고 차단으로 확대하지 않고, 운영자 큐 행이나 목록 라벨로도 올리지 않으며 콘텐츠 탭과 보고서 증빙에만 보인다. 미확인 표본은 매일 03:10 스윕(`workers/post_publish_ai_review.py`, `POST_PUBLISH_AI_REVIEW_DAILY_CAP` 기본 20, 편집된 글·오래된 글 순)이 생성과 같은 독립 검수로 처리한다. 첫 판정은 `POST_PUBLISH_AI_REVIEW_MODEL`(기본 Sonnet)이고, 확신도 부족으로 애매하면 검수 모델(Opus)이 한 번 더 본다 — 현재 본문에 묶인 PASS는 `post_publish_reviewed_by='system:ai-review'`로 기록하고, 차단 지적은 **자동으로 내리지 않고**(`ai_review`에 쓰면 공개가 숨겨진다) `post_publish_ai_review` FLAGGED 표시(구조화 지적 `structured_findings` 포함)와 글당 인시던트 하나를 남기되, 같은 실행에서 **최소 교정 패스의 공개 모드**(`content_minimal_correction.run_published_correction`, `services/published_correction.py`)가 먼저 푼다(이미 FLAGGED인 글 먼저, `POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP` 기본 20편·글당 `POST_PUBLISH_AUTO_CORRECTION_MAX_PASSES` 기본 2). 교정본은 메모리에서만 만들고 `POST_PUBLISH_AI_REVIEW_MODEL`로 독립 재검수하며, **그 후보 hash에 묶인 PASS일 때만** 병원 잠금·행 잠금 아래 CAS(상태 PUBLISHED·`content_revision`·본문 hash·FLAGGED 표시가 읽은 때와 같을 때)로 쓴다. 쓸 때는 관리자 PATCH처럼 revision 증가·`body_updated_at` 갱신·최초 공개 사실 보존·발행 게이트 재평가(통과 못 하면 쓰지 않는다)와 색인·사이트 재검증 intent를 한 트랜잭션에 묶고, `ai_review`에 hash 결합 PASS를 남기며 `post_publish_reviewed_by='system:ai-correction'`과 `auto_correction`(mode=POST_PUBLISH, 전후 hash·고친 문장·사람 편집 여부) 기록을 남기고 인시던트를 닫는다. 사람이 편집한 공개 글도 대상이지만 지적 문장·응급 템플릿 삽입 지점 밖과 필수 문구 문장은 바꾸지 않고, **제목은 어떤 경우에도 바꾸지 않는다**(제목 지적이 있으면 돈을 쓰지 않고 `NEEDS_HUMAN`). 재검수가 PASS가 아니거나 범위·분량·금지 표현·게이트 검사에 걸리면 글은 그대로이고 FLAGGED·인시던트가 시도 기록(`correction.finished`·사유)과 함께 남으며 인시던트가 사람이 직접 고쳐야 한다고 말한다. 재검수 불가는 시도로 세지 않고 다음 실행으로 넘긴다. 표시에 구조화 지적이 없는 옛 FLAGGED 글은 문구에서 인용을 짐작하지 않고 재검수로 구조를 얻는다. 월간 보고서의 '필수 사후검수' 경고는 공개 3일이 지나고도 남은 표본만 센다. 교정으로 풀지 못하고 UNCERTAIN(확신 부족) 지적만 남은 FLAGGED 글은 마지막 검수에서 `POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS`(기본 7)일 뒤 검수 모델로 한 번만 더 보고(표시의 `senior_rereview`가 재구매를 막는다), PASS면 `system:ai-review` 확인으로 닫는다. 그 재검수가 HARD를 내도 조용한 사고의 문구만 갱신한다 — Slack·사람의 일로 올리지 않는다(10/8 지시).

### Admin 화면과 사람의 일

- 병원 화면은 `/hospitals/{id}` 아래 탭 4개(`현황 · 병원 정보 · 콘텐츠 · 보고서`)뿐이다. 옛 8개 경로(`dashboard, onboarding, profile, schedule, wiki, essence, query-targets, exposure-actions`)는 `admin/lib/route-redirects.ts`의 매핑으로 새 탭에 redirect되며 2026-10-09에 제거한다. 그 전에 백엔드가 만드는 admin 딥링크도 새 경로로 옮긴다. 새 탭·화면·전용 lib를 만들 때 옛 경로를 되살리지 않는다.
- 병원 상태는 `hospital_states.py`의 3상태(`준비 중 · 운영 중 · 일시정지`)와 `hospital_overview`의 예외 카드로만 표현한다. 사람의 할 일은 `operator_action.is_operator_todo`(기한 지난 사람의 일 ∧ 운영 대상 ∧ 조용한 종류 아님) 한 규칙으로 판정하고 현황·콘텐츠·운영센터·Slack이 같은 판정을 쓴다 — 직렬화된 행의 `requires_operator_action`도 이 술어의 값이다. 기한 안의 자동 재시도(`RETRYING`)와 `RUNNING`을 사람의 일로 표시하지 않으며, 스윕이 소유한 복구(예: 사이트 준비 재시도)는 시도마다 인시던트를 열지 않고 예산 소진 시 원인별 인시던트 하나만 연다.
- 사람이 하는 일은 계약 등록(한 화면 `/hospitals/new` → 병원 생성·계약 기록·인수 수락 한 트랜잭션), 병원 정보·공개 주소 결정, 예외 카드의 서버 허용 행동, 보고서 전달 기록이다. 발행 일정 저장은 첫 일정 자동 생성이 실패했거나 일정을 바꿀 때만 사람의 몫이다. 계약 등록(`register_contract`)은 연결된 상담 요청의 진료과·지역·핵심 키워드를 병원 기본값(`specialties`·`region`·`keywords`)으로 옮기고(의료광고 금지 표현이 든 키워드는 버린다), 대표번호(`clinic_phone`)는 복사하지 않는다 — 공개 페이지에 바로 나가는 사실이라 AE 확인이 필요하고 병원 행으로 복사하면 리드 보관기간 파기를 우회한다. 등록 커밋 뒤 `autofill_registered_hospital_profile`(default 큐, 서명 목적 `autofill-registered-profile`, `PROFILE_AUTOFILL` OperationRun, `content` 비용 카테고리)이 자동 입력 초안을 실행 결과에만 저장하고(병원 행에는 쓰지 않는다), 병원 정보 화면이 `GET /admin/hospitals/{id}/profile/autofill-draft`(`has_draft`)로 불러와 AE가 확인·저장해야 반영된다. 리드의 대표번호는 `lead_suggestions.phone`으로 그때그때 제안만 한다(리드가 파기되면 제안도 사라진다). 디스패치·작업이 실패해도 등록은 그대로다. 운영자 문구는 `admin/lib/admin-copy.ts`의 `ADMIN_COPY`만 쓰고, `scripts/check_user_facing_terms.py`가 `admin/app`·`admin/lib`·`admin/types` 전체에서 통일 전 용어를 막는다. 백엔드가 만드는 운영자 문구(`readiness_operator_copy.py` 등)는 가드 밖이므로 존재하는 탭 이름만 쓰는지 검토 때 확인한다.
- 계약 등록으로 태어난 인수 기록은 `HANDOFF_ACCEPTED`이며 `sla_due_at`은 인수 기한이라 수락 뒤에는 온보딩 큐·마일스톤에서 기한 초과로 읽지 않는다.

### 측정·리포트

- API 답변 측정은 소비자 ChatGPT/Gemini 화면의 노출과 동일하지 않다. 실패·미확정과 미언급을 분리한다.
- 월간/V0 고정 관측 슬롯의 질문·플랫폼·반복·protocol과 답변·판정 체크포인트를 보존한다. V0는 짧은 실행 구간마다 같은 작업·측정 lineage로 자동 이어가고, 이미 성공한 공급자 단계는 다시 구매하지 않는다. 정상 이어가기 횟수와 실제 실패 재시도 예산을 섞지 않는다. COMPLETE/LIMITED/UNAVAILABLE와 legacy lineage를 구분하며 비교 불가한 기간을 상승/하락으로 표현하지 않는다.
- 보고 대상은 과거 서비스 구간과 계약 월로 결정한다. 현재 ACTIVE 목록만 사용하지 않는다.
- 내부용과 원장용 PDF를 분리하고 원장용 artifact의 실제 파일·hash·검증 결과를 확인한다. 내부 오류·명령어·검수 항목을 원장용에 넣지 않는다.
- 원장용 월간 PDF는 쉬운 합니다체로 원장님께 말한다. ‘소개’ 대신 ‘언급’, 첫 측정은 ‘기준점’이라 쓰고, 감소·미언급은 결손 나열 대신 우리가 바꿀 다음 수(더 넓은 키워드·새 질문 유형·안내 보강)로 말한다. 숫자는 칸에 그대로 보이고, 모르는 값과 0을 섞지 않으며, 환자 수·순위를 약속하지 않는다. 문구는 `report_narrative.py`·`report_engine._director_copy`에 두고 `tests/test_report_plain_language.py`가 금지어·의료광고 필터를 막는다. 다음 달 할 일은 질문 문장 기준으로 합쳐 한 질문에 한 줄(`“질문”: 할 일.`)만 쓰고, 출처 집계는 질문×AI 서비스의 '답변' 단위로 센다(2026-10 마케팅 검토, `tests/test_report_marketing_feedback.py`).
- 분류 사전(`keyword_analysis._LEXICON`·`_SUFFIX_RULES`)의 지문은 모든 병원의 측정 기준에 들어간다 — 사전을 고친 달은 9개 병원 모두 지난달과 비교하지 않는다. 추적 질문 문장 하나를 고쳐도 그 병원의 질문 세트 지문이 바뀌어 그 달 전체가 비교 불가다. 둘 다 월간 측정(24일~말일) 전에 끝내고 같은 달에 묶는다(2026-10: 초음파·촬영·조영술·골밀도·심전도를 검사로 추가). 사전에 없는 말(UNKNOWN)은 '치료' 템플릿을 받지 않고 중립 '진료' 문형만 받는다(템플릿 변경은 그 질문 문장이 바뀌는 병원의 추적 질문 세트 지문만 바꾼다). 질문·제목 문형은 `services/question_wellformed.py` 한 곳이 검사한다 — 질문 생성·시드·브리프(작가 프롬프트)에서 지역 중복·군더더기 키워드·검사에 '치료'를 거르고 경고 로그를 남기며, 검사 이름에 '치료'가 붙은 제목은 보완 재작성 한 번 뒤에도 남으면 `GENERATION_REJECTED`(표본 실패)로 거절한다.
- 템플릿만 바꾼 뒤 지난달 리포트를 다시 찍을 때는 일반 재생성이 아니라 `TEMPLATE_REFRESH`(`rebuild=true&template_only=true`)를 쓴다. 대체할 버전의 저장 요약·마감 시각을 그대로 옮기고, 숫자 판정(`build_monthly_template_refresh_plan`)이 PASS가 아니면 만들지 않는다. 숫자 대조는 PDF 정규식이 아니라 원장 뷰의 정본 사실(`doctor_view_facts`)을 저장 요약에서 다시 만든 사실과 키 단위로 맞추고, PDF 본문은 그 숫자가 사실별 토큰 그대로 찍혔는지만 이차 확인한다(옛 PDF 본문과는 비교하지 않아 문구 변경으로 막히지 않는다). `--allow-recovery-pending` 실행은 새 버전이 생긴 `PARTIAL`·`BLOCKED` 종료를 성공으로 보고 사후 확인을 계속한다. 노출 행동 연결 같은 부수효과가 없다. 절차는 [템플릿 갱신](docs/ops/monthly-template-refresh.md).
- 월간 측정 코호트(`Hospital.monthly_sov_cohort`)는 저장된 플래그가 정본이지만 사람이 켜지 않는다. 월간 창과 주간 배치의 등록 단계가 SovRecord가 있고 유효한 고정 세트(LOCAL·ACTIVE 10개 이상)를 만들 수 있는 ACTIVE 병원을 자동 편입하며 한 번 편입한 병원은 자동으로 빼지 않는다. 비용 경고 기준은 손으로 맞추는 설정이 아니라 `COST_GUARD_MONTHLY_SOV_QUERIES ÷ (15문항×2플랫폼×5반복)`으로 계산한다(`sov_tracking_set.monthly_sov_cohort_cost_threshold`, 지금 28곳). 자르는 상한이 아니다 — 편입 병원은 전원 측정하고 초과하면 경고 로그와 월 1건의 `MONTHLY_SOV_COHORT_OVER_LIMIT`(개발 대상)만 남기며 그 사고는 달이 끝나면 닫힌다. 편입되지 못한 ACTIVE 병원은 병원·월마다 `MONTHLY_SOV_COHORT_GAP` 한 건이다. 사람이 풀어야 하는 공백(고정 세트 부족 등)은 주간 실행에서 미리 OPEN으로 알린다. 기록이 없어 못 든 병원은 월말 창 tick이 주간 측정 1회를 보내고 그 공백은 측정 요청 + 24시간(`COHORT_BOOTSTRAP_GRACE`)까지 RETRYING이다 — 기한이 이미 지났으면 OPEN·HIGH로 연다. 편입되면 근거(`incident_backlog`)가 닫는다. 코호트에서 빠진 병원은 보고서가 주간 자료로만 만들어져 전달이 영구히 막히므로 이 공백을 조용히 두지 않는다.
- 월간 측정 사고(`MONTHLY_SOV_MEASUREMENT_FAILED`)의 부분·미완료·비용 보류는 다음 달 8일 09:00 KST(`monthly_period.monthly_final_judgment_at`)까지 RETRYING이고 열림 알림은 보류된다. 8일 00:15 최종화가 COMPLETE이거나 마감으로 LIMITED/UNAVAILABLE이 확정된 달의 사고를 닫는다. 질문 세트·측정 기준 원인(`MONTHLY_SOV_HUMAN_ERROR_CODES`)은 처음부터 OPEN이다. 지난달 보고서 공백 요약은 1~8일 09:00에 돌고, '자동 복구 종료'는 8일 00:15 최종화 뒤 09:00에 한 번만 판정한다. 그 달 고정 매니페스트가 없는 달은 `MANIFEST_MISSING`(전달 불가, 다음 달부터 자동)으로 병원·월마다 한 번 알리고 다시 만들기를 권하지 않는다. 원장 PDF 문제는 월간 원장 PDF 대조 작업이 소유한다.
- V0 최종 실패는 재시도를 다 쓴 뒤 같은 실행을 6시간 뒤로 미뤄 최대 3회 다시 돈다(`V0_AUTO_RERUN_BUDGET`, 측정 기준 변경 `V0MeasurementPolicyDrift`는 제외). 같은 OperationRun lineage는 7일 안에 끝난 측정을 다시 쓰므로 공급자 호출을 다시 사지 않는다. 그동안 `V0_REPORT_FAILED`는 RETRYING(재실행 시각 + 6시간 `V0_AUTO_RERUN_GRACE`, 비용 창 미룸이 겹치면 미룸 시각 + 6시간으로 연장)이고, OPEN·Slack은 소진 뒤에만이다.
- 월간/V0 전달 API는 검증된 현재 원장용 artifact에 결합하여 AE가 사람 전달한 사실을 append-only로 기록한다. 닫힌 UNAVAILABLE 월간 보고서도 headline을 0으로 바꾸지 않고 유효한 artifact/evidence identity가 있으면 전달 가능하다. 무료 진단의 자동 Resend 이메일과 다르고, 월간 보고서를 원장에게 자동 전달하지 않는다.
- 도입문의 공개 접수는 진료과·지역·키워드가 함께 오면 INTERNAL 초도 진단을 자동 생성하고, 원장 휴대전화로 안내 문자(NHN Cloud SMS)를 보낸다. 리드 저장 뒤의 부수효과라 어느 쪽이 실패해도 접수를 되돌리지 않으며, 진단 규칙은 Admin 수동 생성과 같은 서비스 함수를 쓴다. 랜딩의 모든 CTA는 이 도입문의 폼(`#lead`)을 가리키고 그 폼은 세 입력을 필수로 받는다 — 셋 중 하나라도 빠지면 문의만 쌓이고 연락할 근거가 만들어지지 않는다. 셀프서브 무료 진단(`/ai-diagnosis`)은 유지하되 랜딩 CTA에서는 분리한다. 리드 진단 측정 실패는 즉시·1·2·4시간으로 물러서 최대 5회 시도한다(`EXECUTION_RETRY_BACKOFF`, `MAX_EXECUTION_ATTEMPTS`). 접수 Slack 실패는 리드를 PENDING으로 남겨 1분 drain이 접수 뒤 1시간 동안 다시 보내고(보내는 도중 멈춘 SENDING 행도 만료 대상), 그래도 못 보내면 운영 채널로 `LEAD_INTAKE_NOTIFICATION_FAILED` 한 건을 연다. 안내 문자는 연결 실패·429에만 한 번 다시 보내고(503 등 5xx·읽기 시간 초과는 문자가 나갔을 수 있어 재발송하지 않고 '수신 불명'으로 남긴다) 최종 실패는 `INQUIRY_SMS_FAILED`다. 접수 Slack도 읽기 시간 초과·5xx 뒤에는 '수신 불명'(SENDING 유지, 재발송 없음, 만료 시 사고)이다. 두 사고는 사람이 그 리드를 맡으면(상태 ≠ NEW) 닫힌다. SKIPPED(설정 없음·휴대전화 아님·같은 연락처 중복)는 알리지 않고 리드 행에만 남으며, 문자 설정 상태는 readiness `facts.inquiry_sms`로 본다. 상세는 [도입문의 접수 자동 처리](docs/ops/inquiry-intake-automation.md).
- 콜용 노출 진단은 사람이 직접 만들 수 있다. 문의가 없는 병원은 Admin `노출 진단 생성` 탭에서 리드와 함께 새로 만들고(동의를 받은 적이 없으므로 `privacy`는 False), 도입문의의 값이 틀렸을 때는 상담 요청 화면의 `값 고쳐 다시 만들기`로 고친다. 리드당 진단은 '1건'이 아니라 '활성 1건'이며 옛 진단은 `superseded_at`·`superseded_by_id`로 갈음해 보존한다 — 실제로 지출한 공급자 호출이 기록으로 남아야 한다. 갈음된 진단은 폴러·운영자 큐·복구 버튼 어디에도 올라오지 않고 보고서만 계속 열린다. HTTP 경계와 워커 claim은 같은 집합을 본다. 고객에게 나간 진단은 갈음 대상이 아니다.
- 보고서 재생성은 실패 복구 전용이 아니다. 정상적으로 만들어진 보고서도 생성 기준이 바뀌면 사유를 남기고 새 버전으로 다시 만들 수 있다. 월간은 새 report 행과 버전을 만들고 이전 보고서·전달 기록을 보존하므로 전달 완료본도 대상이며, 화면은 "새 버전은 다시 전달해야 반영된다"를 경고로 알린다. 초기 진단(V0)은 월간 생성 경로가 만들지 않으므로 그 버튼을 달지 않는다. 리드 진단은 아티팩트를 버전으로 쌓되 `PENDING`·`INTERNAL`만 제자리 재생성 대상이다 — 고객에게 나간 리드는 공개 토큰 뷰가 최신 버전을 서빙해 이미 보낸 링크의 내용이 바뀌고, `ck_lead_diagnoses_delivery_requires_report`가 같은 선을 긋는다. HTTP 경계와 워커 claim은 항상 같은 집합을 봐야 한다.

### 자율 작업·알림·비용

- OperationRun, Incident, NotificationOutbox와 자료 처리 run·관측 슬롯의 멱등성·lease·에피소드, 서명된 worker dispatch를 유지한다.
- 워커·Beat·발행자의 Redis 연결은 소켓 timeout 30초·connect 10초·TCP keepalive·`retry_on_timeout`·health check 25초를 쓰고(`celery_app.py`, RedBeat URL 질의값 포함), 소비자 재연결은 `broker_connection_max_retries=100`(약 47분 뒤 `Unrecoverable error`로 종료 → Cloud Run 재기동)이다. 그 상한은 소비자 재연결 경로만 덮는다 — 연결을 잃은 뒤 `Heart._send → Connection.ensure(max_retries=None)`에 갇힌 워커(2026-10-08 12:25 KST, 4시간 10분 무응답)는 **liveness만이 끝낸다**: Celery `heartbeat_sent` 신호가 `/tmp`의 파일을 2초마다 갱신하고, 워커의 health server(`--worker-heartbeat`)는 그 파일이 `WORKER_LIVENESS_STALE_SECONDS`(600) 넘게 오래되면 `/live`를 실패시켜 Cloud Run이 인스턴스를 바꾼다(기동 유예 `WORKER_LIVENESS_STARTUP_GRACE_SECONDS` 300). Beat의 `/live`는 RedBeat 스케줄 루프의 tick 파일 나이를 본다 — `LivenessRedBeatScheduler.tick`이 tick마다(루프 최대 30초) 인스턴스 로컬 파일(`/tmp/reputation-beat-tick`)을 갱신하고, health server(`--beat-tick`)는 그 파일이 `BEAT_LIVENESS_STALE_SECONDS`(600) 넘게 오래되면 `/live`를 실패시켜 Cloud Run이 Beat를 재시작한다. 첫 tick 전에는 `BEAT_LIVENESS_STARTUP_GRACE_SECONDS`(900; 옛 Beat의 RedBeat 락 TTL 300초 대기를 덮는다) 동안 통과시킨다. 락을 잃으면 `tick`이 예외로 프로세스를 끝내고, `/live`가 Redis를 직접 읽지 않으므로 Redis 장애 동안 재시작을 반복하지 않는다. canary 키는 리비전의 모든 인스턴스가 공유하므로 liveness 근거로 쓰지 않는다. 만료 봉투 중 OperationRun 없는 **주기 ≤ 봉투 수명(6시간)의 Beat 작업**(매분 drain·5분 canary·15분 projector·시간 heartbeat)만 WARNING `dispatch_expired_dropped` 한 줄과 `Ignore`로 버리고, 야간 생성·주간 측정·월간 보고서 같은 긴 주기 작업의 만료는 종전처럼 실패·사고다. 만료 판정은 서명·문맥 검증을 통과한 봉투에만 내린다. 봉투가 6시간 유효하므로 워커 정지 뒤 6시간 안의 매분 사본은 버려지지 않고 실행된다(멱등).
- 서명 봉투의 배달은 **검증 → claim → 판정** 순서다(`dispatch_auth.before_start`). 판정은 AUTHORIZED·STALE·INVALID 셋이고, STALE(이미 다른 사본이 claim·완료·판이 바뀜)은 부수효과 없이 `Ignore`로 물러나 INFO `dispatch_skipped reason=`만 남긴다 — task_failure도, 사고도, 실행 종결도 아니다. 봉투 수명은 두 상수다 — 수락 상한 `DISPATCH_MAX_LIFETIME_SECONDS`(6시간, 검증은 `0 < 수명 <= 상한`)와 서명 수명 `DISPATCH_TTL_SECONDS`(6시간, 수락 상한과 같고 broker visibility_timeout 2시간 이상 — 2026-10-11에 올렸다. 워커를 #226 이전 리비전으로 롤백하지 않는다). 수락 상한을 먼저 올리고 서명을 나중에 올렸으므로 롤아웃 겹침 구간의 옛 워커가 새 봉투를 거절하지 않는다. 자율 복구의 재배달은 매번 새 task id·새 봉투를 쓰고 `mark_operation_run_queued`로 QUEUED를 표시하며, QUEUED 유실 판정은 기본 3시간, V0·월간 보고서는 45분이고 재배달마다 두 배가 된다(마지막 유예 뒤 FAILED). 재배달은 유형별 예산 3회(`operation_runs.redispatch_count`, 0087)를 쓰며 QUEUED 유실·lease 만료 인수만 센다(publish 실패 REQUESTED·미룬 실행 제외). 다 쓰면 FAILED(`OPERATION_REDISPATCH_EXHAUSTED`)이고 원인별 사고 하나(`OPERATION_RECOVERY_EXHAUSTED`, 개발 대상)로 묶인다(사이트 준비는 병원 사고). 그 사고는 같은 원인으로 멈춘 실행이 모두 같은 종류의 성공한 재시도(`parent_run_id` 후손, 병원 없는 실행은 같은 종류의 더 나중 성공)를 가지면 자동으로 닫히고(`incident_backlog`), 일일 요약은 같은 원인 코드로 멈춘 실행 전부를 그 사고로 설명된 것으로 센다. lease가 만료된 RUNNING은 멱등 유형(V0·사이트 준비·RUN_SOV·월간 보고서(rebuild 아님)·REGENERATE_CONTENT(_IMAGE)·GENERATE_CONTENT_ITEM)만 인수한다 — 인수는 새 task id로 재배달하는 것이고 실행 재개를 보장하지 않는다(`GENERATE_CONTENT_ITEM`은 새 task id가 기존 실행 claim에 막혀 CANCELLED로 물러나고 다음 슬롯 스윕이 다시 집는다) — 월간 보고서 재생성은 FAILED(`OPERATION_LEASE_EXPIRED`), 공개 글 이미지 재인증은 자기 sweep이 맡는다. 10일(월간 측정 구간 실행 `MONTHLY_SOV_PERIOD`는 20일) 넘게 갱신이 없고 lease도 없는 RUNNING(재검증 제외)은 FAILED(`OPERATION_RUN_ABANDONED`)로 정리하고 원인 사고 하나로 알린다(`autonomous_recovery.py`). countdown/eta는 15분(`MAX_DISPATCH_COUNTDOWN_SECONDS`)을 넘기지 않는다 — 더 길게 기다리는 일(V0 비용 창 보류)은 `defer_operation_run`으로 실행을 QUEUED로 되돌리고 `operation_runs.not_before_at`(0083)에 시각만 남기며, 재배달은 그 시각 뒤에 자율 복구가 한다. 워커는 `REMAP_SIGTERM=SIGQUIT`(cold shutdown)으로 종료되어 실행 중 메시지를 즉시 큐에 반납한다. 배포는 배치 시간대(23:00·01/04/07/12/18/22시 KST) 밖에서 하고, 그 밖이면 하루 여러 번 할 수 있다(2026-10-13 대표 결정) — `deploy.sh`가 시간대를 막고 하루 잠금은 `--override-reason`으로 넘긴다(검증과 배포 절).
- 새 태스크는 명시적 큐 라우팅·서명 목적·시간 제한·실패와 복구 연결·canary/readiness 영향을 확인한다. 현재 7개 큐는 한 Worker 서비스가 소비하며 `control`은 다음 빈 슬롯의 우선순위만 제공한다. 전용 용량이나 선점을 보장한다고 설명하지 않는다. RedBeat 변경은 영속 스케줄 재조정도 필요하다.
- 재시도는 일시 오류와 표본 실패에 한정하고 업무별 최대 횟수를 따른다. 공급자 재시도, 생성 개선 반복, 배치 재실행, 이메일 재발송을 일률적인 3회 규칙으로 바꾸지 않는다. 공급자 장애(`PROVIDER_TIMEOUT`·`PROVIDER_UNAVAILABLE`·`GENERATION_FAILED`, OpenRouter 429·5xx·연결 오류는 `PROVIDER_UNAVAILABLE`)는 KST 하루 4회 예산이 날마다 다시 열리고, 같은 환경 장애 구간을 처음 관측한 날(`environment_since`, 레거시 기록은 마지막 관측일)부터 3일이 지나면 사람의 일이다. 검수 장애는 후보별 연속 실패 한도가 따로 끝을 소유한다. 자동 복구가 예산 안에서 소유한 상태(RETRYING)는 운영자 큐·현황 카드·Slack에 사람의 일로 올리지 않는다.
- 사고 종류의 소유는 `incident_types.INCIDENT_TYPE_POLICIES` 표 하나가 정한다 — 대상(운영/개발)·조용함·스윕 소유·기한 창. 스윕 소유 종류(월간 슬롯 생성, 창이 열린 동안의 월간 측정, 사이트 준비·글 생성 큐 투입 실패, 재검수 예산이 남은 Essence 보류, 부트스트랩 기한이 있는 코호트 공백, V0 자동 재실행)는 RETRYING+기한으로 열린다. 종류의 기한 창은 에피소드 시작에서 세고(터치마다 밀리지 않는다), Essence·코호트 공백·V0처럼 다음 시도 시각을 아는 호출자는 기한을 직접 준다(`retry_until`). 기한이 지나면 OPEN(사람의 일)이며, 표에 없는 종류는 종전처럼 OPEN으로 열린다. 조용한 상태로 충분한 관찰(주간 측정 실패 `WEEKLY_SOV_MEASUREMENT_FAILED`, 주간 상한 초과 `SOV_HIGH_PRIORITY_CAP_EXCEEDED`, 행 단위 수신 불명 `NOTIFICATION_DELIVERY_UNKNOWN`)은 Slack·할 일·요약 건수에서 빠진다. 비용 한도(`COST_GUARD_*`)는 대표 결정이라 운영 대상이고, AE가 Admin에서 고칠 수 없는 `UNVERIFIED_ADMIN_ACTOR`·`MONTHLY_SOV_COHORT_OVER_LIMIT`·`SITE_BUILD_RETRIES_EXHAUSTED`는 개발 대상이다. 사고를 닫는 근거는 종류별로 `workers/incident_backlog.RESOLVERS`에 있고, 새 사고 종류는 resolver나 명시적 복구 경로와 함께 들어온다. 복구 확인은 사람의 일이 아니다 — 연결 실행이 사고의 마지막 관측 뒤 성공하면 RETRYING을 닫고(생성 사고 제외, 자체 복구가 닫는다), 24시간 넘은 RECOVERED는 시스템이 확인으로 닫으며, 어느 경로로 닫든 열림 알림이 나갔으면 복구 알림을 넣는다(`incident_backlog.settle_incidents`).
- 채널 URL 자료의 fetch 실패는 인시던트가 아니라 자료 행의 상태(`ERROR`·`fetch_error`)이며, 필수 자료가 없는 병원은 콘텐츠 상태 카드가 사람을 부른다. 옛 `CHANNEL_SOURCE_FETCH_FAILED`는 스윕의 한도 있는 멱등 정리가 링크를 걷고 열린 것만 RECOVERED로 내린다. 단 `ONBOARDING_SOURCE_FETCH_ALERTS_ENABLED=True`(기본 꺼짐, 대표 결정 전)이면 필수 근거 자료가 0건이고 채널 주소를 모두 24시간 넘게 읽지 못한 병원만 `ONBOARDING_SOURCES_UNREACHABLE` 사고 하나로 올린다.
- 시간만 흐르고 다음 단계로 가지 않는 온보딩은 병원·원인당 사고 하나로 올리고 풀리면 **조용히** 닫는다(`services/onboarding_stalls.py`, 15분 `project_milestone_events`에 붙은 별도 스캔 — 스캔 실패가 마일스톤 투영을 되돌리지 않는다). PAUSED 병원은 대상이 아니다. 종류는 `ONBOARDING_PROFILE_INCOMPLETE`(계약 인수 수락 뒤 3일에 프로필 미완료), `ONBOARDING_SCHEDULE_MISSING`(공개 병원이고 Essence가 current가 된 뒤 24시간에 일정 미설정), `ONBOARDING_NO_PUBLICATION`(ACTIVE·활성 일정이 있고 공개 운영·일정 시작 중 늦은 시각부터 7일에 발행 0건; 사람이 일정을 꺼 활성 일정이 없으면 의도된 상태라 제외), `ONBOARDING_SOURCES_UNREACHABLE`(위 플래그가 켜졌을 때만)이다. 모두 운영 대상·처음부터 OPEN(스윕 소유 아님)·`MISSING_PREREQUISITE`·MEDIUM이며 열 때 알리고(`notify=True`) 닫을 때 알리지 않는다. 이미 열린·확인된 사고는 다시 열지 않는다.
- Essence 자동 검수가 확률적으로 보류하면 초안에 복구 사이클(`automatic_recovery_cycle`)과 시각(`automatic_recovery_last_at`)을 남기고 **1h → 4h → 12h** 백오프로 자동 재검수한다(`AUTO_ESSENCE_RECOVERY_BACKOFFS`, `automatic_recovery_backoff`가 읽기·쓰기 공통 표; `AUTO_ESSENCE_MAX_RECOVERY_CYCLES`=4라 4번째 보류에서 예산이 끝나 사람의 일이 된다, 합계 약 17시간). 자료 간 상충(`conflict_notes`)·프롬프트 주입 의심은 같은 입력으로 다시 사도 결론이 같으므로 사다리를 타지 않고 곧바로 사람에게 넘긴다(서버 소유 표식 `automatic_recovery_terminal` — 다음 자동 시도가 없어 사고가 바로 OPEN이다). 같은 snapshot의 DRAFT가 여럿이면 모두 사람이 손대지 않은 시스템 초안일 때만 최신 version 하나로 다시 만들고 나머지 옛 시스템 초안은 새 판과 함께 ARCHIVED 한다. 하나라도 사람 판단이 든 초안이 있으면 기계가 덮지 않는다. 재검수 예산이 남은 보류 사고(`ESSENCE_AUTO_REVIEW_ESCALATED`)는 다음 자동 재검수 시각 + 30분(`ESSENCE_ESCALATION_RETRY_MARGIN`, 15분 재조정 주기 여유)까지 RETRYING이고 열림 알림은 예산이 끝나 OPEN이 될 때 나간다. 사람이 손댄 초안은 자동 재시도하지 않는다. 처리 `ERROR`인 필수 자료는 상태를 바꾸지 않고 이번 합성 입력과 완결성 검사에서만 제외하며 그 사실을 gap(`excluded_error_source`)에 남긴다 — 다른 필수 자료가 PROCESSED면 1시간(`ERROR_SOURCE_EXCLUSION_GRACE`), ERROR뿐이면 6시간(`ERROR_SOURCE_EXCLUSION_AFTER`) 뒤에 뺀다. Essence 합성·검수는 `essence` 비용 카테고리를 쓴다.
- 비용 예약 영수증과 공급자 HTTP attempt 원장을 분리한다. usage 누락은 0으로 바꾸지 않고 DB 실패 시 bounded Redis spool로 복구한다. Redis 비용 가드와 single-flight는 장애 시 fail-open이므로 절대 지출 상한이라고 설명하지 않는다. 비용 가드가 Redis를 읽지 못해 호출을 통과시키는(fail-open) 첫 발생은 프로세스·카테고리마다 ERROR 한 줄(worker ERROR 경보가 이메일로 승격)과 **개발 대상** 사고 하나(`COST_GUARD_FAIL_OPEN`, 키는 카테고리 단위)로 남긴다 — 비용 한도 도달은 대표 결정이라 운영 대상이지만 이것은 AE가 고칠 수 없는 인프라 사실이다(D25). 같은 프로세스에서 그 카테고리의 가드가 다시 Redis로 판정하면 사고를 복구하고(복구 커밋이 성공해야 '닫았음'이며 DB 오류·충돌이면 60초 뒤 다음 정상 판정이 다시 닫는다), 복구 뒤 10분 안의 fail-open은 다시 열지 않는다. 알린 프로세스가 복구 전에 교체되면 15분 근거 정리의 resolver(`incident_backlog`, `COST_GUARD_FAIL_OPEN`)가 마지막 관측 10분 뒤 같은 Redis의 PING 응답을 근거로 닫는다. 사고 기록에 성공해야 '알렸음'이며 실패하면 60초 뒤 다음 fail-open이 다시 기록하고, 사고 기록 실패는 가드 결정에 영향을 주지 않는다. 사용량 Redis spool이 상한(10,000건)을 넘겨 기록을 버리면 버린 수를 카운터(`provider_usage:recovery:dropped`)에 세고, DB가 돌아온 뒤 drain이 개발 대상 사고 `PROVIDER_USAGE_SPOOL_TRUNCATED`를 KST 하루 한 건으로 연다(같은 날 추가 유실은 갱신만, 유실은 되살릴 수 없어 자동 복구하지 않는다). 사고 기록이 실패하면 카운터를 되돌려 다음 drain이 다시 시도한다. 정상 spool 복구는 여전히 알리지 않는다.
- 무료 진단 single-flight는 동일 cache key의 중복 구매만 프로세스 간 합친다. provider semaphore는 프로세스 내부 한도이며 같은 API key의 전역 동시 호출 상한이 아니다.
- 공개 commit 뒤 IndexNow 제출은 durable intent와 제한된 재시도로 처리한다. 정상 제출·재시도 성공·usage spool 복구를 Slack 알림으로 만들지 않는다.
- Slack은 [알림 정책](docs/ops/slack-notification-policy.md)을 따른다. 정상 발행 알림을 다시 추가하지 않는다. 사후검수 지적(`POST_PUBLISH_REVIEW_FLAGGED`)처럼 `incident_types.INCIDENT_TYPE_POLICIES`에 조용함(`quiet`)으로 등록한 사고는 사고 행·Admin에는 남되 Slack 알림을 만들지 않는다(outbox 입구에서 넣지 않고, 이미 쌓인 행도 발송기가 집지 않는다). 발송기는 스윕 소유 사고의 열림 알림을 그 기한까지 보류하고(`notification_store.claimable_notifications_stmt`), 기한 전에 사고가 복구·확인되면 그 알림을 `INCIDENT_RESOLVED_BEFORE_SEND`로 종결한다. 열림 알림이 나가지 않은 사고는 복구 알림도 보내지 않는다(`settle_incident_notices`). 복구 알림은 같은 에피소드의 열림 알림이 실제로 나간 뒤에만 나간다 — SENT·보낸 뒤 결과 불명 HOLD만 '나간 것'이고, SENDING은 claim이 HTTP 전에 커밋한 선점이라 대기다. 열림이 아직 나갈 수 있으면(PENDING·RETRYING·SENDING·보내기 전 보류 HOLD) 복구는 기다리고, 짝 열림이 모두 FAILED이거나 없으면 발송 전 종결한다(`notification_store._paired_open_exists`, 종결·claim 공용). 알림기는 Slack 429에서 `Retry-After`를 따르되(한 요청 안 대기 합계 5초 이하) 5xx·읽기 시간 초과는 처리됐을 수 있어 다시 보내지 않는다. outbox 실패가 도메인 트랜잭션을 되돌리지 않게 한다.
- `#mkt-reputation`의 모든 메시지는 fallback text와 Block Kit header의 첫 토큰으로 `[Lead : 도입 문의]`·`[Error : 오류 발생]`·`[Report : 운영 현황 보고]` 중 하나를 쓴다. 문구와 종류→라벨 대응은 `services/notification_labels.py` 한 곳에 두고 라벨 문자열을 다른 파일에 복사하지 않는다. 라벨은 기존 문장 앞에만 붙고 버튼·`OPS-` 참조·개발팀 안내를 지우지 않으며, 자동 복구 사실(`INCIDENT_RECOVERED`)은 Error가 아니라 Report다. 전달 준비 완료 마일스톤(`MILESTONE_SUMMARY_REPORT`)·사이트 준비/공개 완료·자동 복구가 도는 월간 공백(`MONTHLY_REPORT_GAP_AUTO`)도 Report다. 라벨을 중복 억제·채널 라우팅·발행 게이트 판정에 쓰지 않는다.
- Slack 채널은 운영 채널 하나다(2026-10-08 대표 결정). `IncidentAudience`(운영/개발 대상)는 유지하되 개발 대상 알림은 라벨 바로 뒤 `[개발 확인]` 표시와 함께 운영 채널로 가고, 죽은 웹훅을 우회한 알림은 `[채널 대체 전송]` 표시를 단다. 웹훅 생존은 채널 단위 전송 사고 하나가 정본이며 배포(`deploy.sh`)·준비 점검(`slack_webhooks_not_dead`)·한 시간 탐침이 같은 분류(400 `no_text` = 살아 있음, 3xx/403/404/410 = 죽음, 5xx·시간 초과 = 모름, 400 본문 거절 = 그 알림 하나의 실패)를 쓴다. 보낼 곳이 없어 보류한 행(`SLACK_CHANNEL_UNAVAILABLE`)은 복구 때 다시 보낸다. 전송 실패는 outbox 행의 `incident_id`를 덮어쓰지 않는다. 쓰지 않는 선택 시크릿은 버전을 비활성화해 끄고, `deploy.sh`는 활성 버전 없는 선택 시크릿을 주입하지 않는다.
- Slack이 닿지 않을 때만 쓰는 **운영 경보 메일(2차 경로)**: `OPS_ALERT_EMAIL`(기본 빈 값 = 보내지 않고 ERROR 로그 `ops alert email: …`만 남김; 운영 값은 Terraform `alert_email`과 같은 주소)로 Resend 메일을 보낸다(`ops_alert_email.send_ops_alert`, 결정적 `idempotency_key`). 보내는 경우는 둘뿐이다. (1) 외부 감시(`/admin/watchdog/pipeline/*`)의 경보가 Slack에 전달되지 않았을 때 — 에피소드당 한 통(`watchdog:{audience}:{episode_id}`, 에피소드가 없으면 KST 날짜+조건 서명), 복구는 메일로 보내지 않으며 판정·에피소드 기록을 바꾸지 않는다. (2) 운영 채널 사고가 열렸는데 대신 받을 살아 있는 개발 채널도 없을 때 — 사고 episode당 한 통(`ops-channel-down:{incident_id}:e{episode_seq}`, 감사 `ops_alert_email_sent`로 중복 방지, 실패하면 프로세스 안에서 15분 뒤 같은 키로 재시도, outbox·사고 상태는 바꾸지 않음). 정상 경로가 아니다. Slack을 거치지 않는 GCP 이메일 경보도 둘 더 있다(시간당 1회, 24시간 자동 종료): Cloud Scheduler 감시 호출(`watchdog-heartbeat`·`watchdog-publish-check`)의 ERROR 로그(토큰 공백 401·LB·API 장애)와 API 서비스의 `pipeline watchdog`·`ops alert email` ERROR 로그(`terraform/monitoring.tf`). Worker쪽 Slack 경로 실패는 기존 worker ERROR 경보가 이메일로 보낸다.
- 사람의 일 건수는 `operator_action.is_operator_todo` 하나로 센다(기한 지난 사람의 일 ∧ 운영 대상 ∧ 조용한 종류 아님). RETRYING에는 언제나 기한이 있다 — `mark_retrying`은 기본 24시간(`DEFAULT_RETRYING_DEADLINE`)을 달고, 정착 스윕은 기한 없는 옛 행에 마지막 변경 + 24시간을 단다. 예외는 사람의 일을 별도 Essence 사고가 가지는 `MISSING_APPROVED_ESSENCE` 생성 보류 하나다. 일일 요약·운영센터 큐·현황 카드·병원 목록이 같은 술어와 같은 원인 묶음(`incident_cause_group.cause_group_key`)을 쓰므로 요약의 건수와 운영센터의 묶음 수가 같다. 개발 몫은 "개발 확인 M건"으로 따로, 둘 다 0이면 "점검 이상 없음"이다. 월간 마일스톤 상태 키는 `monthly:{hospital_id}:{YYYY-MM}`이고 새 버전 알림은 처음 준비·차단 뒤 재준비·전달 뒤 숫자가 바뀐 재빌드 셋뿐이다(TEMPLATE_REFRESH는 알리지 않는다). 07:45·08:00 발행 보류 요약은 새로 막힌 (글, 사고 epoch)만 알리고 `GENERATION_BLOCKED_NOTICE` 실행 기록으로 중복을 막는다. 월간 공백은 자동 복구 중(AUTO, Report)과 사람이 정할 것(MANUAL, 조치 필요)으로 가르고 분류가 바뀔 때만 알린다.

## 검증과 배포

변경 범위에 맞는 테스트·타입·lint·계약 검사를 수행한다. 기본 진입점은 `make test-backend-local`, `make test-frontend`, `make copy-guard`, `make db-budget-guard`다. 통합 테스트의 DB/Redis/PDF 의존성과 skip 여부를 함께 기록한다. 테스트 DB·Redis URL에는 기본값이 없고 테스트는 `backend/.env`를 읽지 않는다 — 전용 테스트 URL뿐 아니라 앱 자체의 `DATABASE_URL`·`SYNC_DATABASE_URL`·`REDIS_URL`도 같다. 변수가 비어 있으면 그 변수를 쓰는 테스트는 변수 이름을 밝힌 실패로 끝나며, 앱 코드가 연결 오류를 삼키는 경로도 연결 불가능한 표지 호스트 가드(`tests/conftest.py`)가 실패시킨다. 값이 있는데 DB에 접속하지 못해도 실패이고, DB URL은 `_test`로 끝나는 DB(또는 `db_env`의 전용 DB)만 허용한다. 그러니 README의 목록(CI backend 잡 env와 같다)을 모두 export하고 DB·Redis를 띄운다. 새 DB/Redis 테스트도 `tests/db_env.py`의 `require_db_url`·`require_redis_url`로 URL을 읽고, 접속 실패는 skip 대신 `fail_unreachable`로 끝낸다. DB/Redis 테스트 모듈의 skip·xfail과 로컬 포트 기본값은 `test_no_skip_in_db_tests.py`·`test_no_default_test_db_port.py`가 막는다. `make test`의 컨테이너 경로 제약, `make setup`의 기존 `.env` 덮어쓰기 동작에 유의한다.

배포는 [현재 배포 안내](docs/ops/deployment-runbook.md)를 따른다. `scripts/deploy.sh`가 스스로 지키는 규칙: (1) 옵션은 `--accept-env-diff`·`--override-reason="사유"`·`--unpin`뿐이고 모르는 옵션·빈 사유는 멈춘다. (2) **env 정본은 운영 서비스다** — backend 대상(`backend·api·worker·beat·all·migrate`)은 운영 `reputation-api`의 현재 비밀 아닌 env를 입력으로 쓰고, 로컬 `.env.production`이 다르면 키 이름과 종류(added/removed/changed)만 보여 주고 멈춘다. 로컬 값으로 바꾸려면 `--accept-env-diff`(로컬 파일이 서비스 env를 통째로 바꿔 `removed` 키는 사라진다). 운영 env를 읽지 못하면 로컬 파일로 대신 배포하지 않는다. (3) **배치 시간대** — Worker·Beat를 바꾸는 대상(`backend·api·worker·beat·all`)은 KST 22:15~02:00 전체와 04·07·08·12·18·22시 앞뒤 45분이면 멈춘다. 그 밖이면 하루 여러 번 배포할 수 있다(2026-10-13 대표 결정). 하루 잠금(`gs://reputation-reports-<project>/ops/deploy-lock/<KST 날짜>.json`)은 권고다 — 같은 날 다른 릴리스가 이미 나갔으면 경고하고 `--override-reason`으로 사유를 남겨야 진행하며(같은 릴리스의 재시도는 그대로 허용), 운영 변경 전에 실패하면 이 실행이 만든 잠금을 되돌린다. 사유는 배치 시간대도 넘으므로 그 경우는 따로 경고하고 기록한다. `site·admin·migrate·rollback`은 막지 않는다. (4) **자동 롤백** — readiness Job 실패, 또는 API 배포 뒤 공개 표면 확인(`scripts/deploy_smoke.py`: `/api/v1/health/live`의 `release`가 이번 릴리스인지, 배포 전 정상이던 병원 헬스의 slug·canonical_host·Site 리비전)에서 API release 불일치이거나 기준선 병원의 절반 이상이 실패하면 배포 시작 전 리비전으로 트래픽을 되돌린다(`AUTO_ROLLBACK=0`이면 멈추기만; 절반 미만 실패는 경고). DB 마이그레이션은 되돌리지 않는다. (5) **고정 트래픽** — 트래픽이 특정 리비전에 고정돼 있으면 첫 변경 전에 멈추고, `--unpin`이면 새 리비전이 Ready가 된 뒤 `--to-latest`로 옮긴다. 준비 점검(`production_readiness.build_report`)은 18개 검사이며 `pipeline_watchdog_token_configured`는 운영에서 `PIPELINE_WATCHDOG_TOKEN`이 비면 실패한다(토큰이 비어도 API는 부팅하므로 외부 감시가 조용히 꺼지는 것을 배포 단계에서 막는다). `facts.ops_alert_email_configured`는 참고 사실이다. 운영 DB 조회는 읽기 전용 CLI `python -m app.utils.ops_sql_report`(migrate Job override, `--only=q1,--only=q3`처럼 인자마다 한 토큰)로 한다. 병원별 헬스는 HTTP 200만 보지 않고 hospital ID·canonical host·현재 리비전까지 확인한다. 배포 후 페이지·sitemap·llms·콘텐츠·이미지·큐 canary·스키마를 변경 범위에 맞게 확인하고, readiness `facts.auto_publish_hold.active`가 의도와 같은지 본다(예약 자동 발행 보류는 감시·운영센터에 '보류 중'으로 보이지 않아 켜 두고 잊으면 그 병원의 발행이 0건이 된다). 문서만 변경했다면 런타임이 바뀐 것처럼 새 배포 성공을 주장하지 않는다.

## 문서 유지 규칙

현재 안내에는 문서 버전·갱신일·코드 기준을 남긴다. 수정 시 실제 서비스/Worker/API/Public 경로를 따라가고 코드 주석도 검증한다. 과거 PRD·계획·검수 기록은 그 시점의 기록으로 보존한다. 사용자 지시가 이 안내보다 우선한다.
