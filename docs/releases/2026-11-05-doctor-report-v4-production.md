# 원장용 월간 보고서 v4 — 운영 배포와 10월분 재갱신 (2026-11-05)

상태: 2026-11-05 두 번 배포하고 10월분 원장 보고서 12곳을 두 번 템플릿 갱신했다. 둘 다 검증 완료.
런타임 코드: 1차 `27710adb`(PR #282·#283), 2차 `547c743b`(PR #284). 이 문서 커밋은 런타임 이미지의 소스가 아니다.
설계 정본은 [v4 설계안](../plans/2026-11-04-doctor-report-v4-design.md), 절차는 [템플릿 갱신](../ops/monthly-template-refresh.md).

## 1차 배포 — 02:03~02:10 KST, main `27710adb`

PR #282(보고서 v4 — 뷰 모델 하나·6쪽+부록·'AI 노출률' 용어, 10월분 v3→v4 템플릿 갱신 경로)와 PR #283(실제 10월 데이터의 지면
넘침 수정, 쪽 범위 계약 검증, 갱신은 PDF 없는 버전을 남기지 않음)을 Backend(API·Worker·Beat)에 배포했다. Admin·Site·DB head는 그대로다.

| 서비스 | 리비전 | 이미지 digest |
| --- | --- | --- |
| API | `reputation-api-00277-khs` | `sha256:6223af4c…8308f89e` |
| Worker | `reputation-worker-00267-cf4` | 같음 |
| Beat | `reputation-beat-00262-dqb` | 같음 |

롤백 좌표 `.omo/rollback-v4/backend`(api `00276-drg`·worker `00266-9mt`·beat `00261-np6`).
이어 02:28~03:05에 10월분 12곳을 `TEMPLATE_REFRESH`(사유 `October-2026-v4-refresh`)로 v3 스냅샷에서 v4 뷰로 올렸다 — 노원탑365 카나리 뒤 11곳,
전부 precheck PASS → `SUCCEEDED/ARTIFACT_VALIDATED` → postcheck PASS(WARN `V3_TO_V4_UPGRADE`·`V4_PLATFORM_RATE_RECOMPUTED`는 예상된 것).
PDF를 보니 표의 고아 행(히트맵·콘텐츠 목록·부록이 1~2행만 다음 쪽으로)과 검은 요약 박스가 단독으로 쪽을 차지하는 문제가 있어 대표가 "고쳐서 재갱신"을 지시했다.

## 2차 배포 — 08:46~08:51 KST, main `547c743b`

PR #284 `claude/doctor-report-v4-pagination`:

- **쪽 넘김 고아·과부 정리** `services/doctor_pdf_pagination.py`(새 파일). WeasyPrint 69는 표 행의 `break-before/after: avoid`를 한 줄 행에서만
  지키고 `tr { break-inside: avoid }`(행 분할 방지, 유지해야 한다)와 만나면 무시한다. 그래서 렌더러가 레이아웃 상자를 읽어 3행 미만 조각과
  박스만 남은 쪽을 찾아 행·모듈에 `page-start`(`break-before: page !important`)를 붙이고 최대 6회 다시 그린다. 부록 표의 첫 조각은 옮기지 않는다.
  변형 순서는 comfortable(정리) → compact(정리) → comfortable(원본) → compact(원본)이고 **변형마다** 이진 검증(`validate_doctor_pdf`)까지
  통과해야 채택한다 — 정리된 레이아웃이 빈 쪽·쪽 상한으로 거절되면 다음 변형으로 물러나 전에 통과하던 보고서는 계속 통과한다.
- **진료 항목 폴백의 서술어 조각 제거** `services/query_target_structure.py`: '치료하는'에서 조사를 뗀 '치료하'·비용·시간 조각을 진료 항목으로
  쓰지 않는다(`_NON_SUBJECT_RE`, 접미사가 있어야 거른다 — 검사·수술·상담 같은 어간 주제어는 그대로). 분류 사전 지문은 바뀌지 않아 측정 비교에
  영향이 없다. 운영 12곳 PDF에 이 라벨이 실제로 찍힌 곳은 없었다.
- 검증: 운영 10월 v4 스냅샷 12곳을 새 렌더러로 로컬 렌더 → 전부 검증 통과(10~12쪽), 124쪽 눈 점검 고아 0건. 로컬 테스트 821 passed.
  Codex `gpt-6.1-sol` xhigh 3회(13~15차) — 14차가 잡은 "정리된 레이아웃이 빈 쪽으로 거절돼도 물러서지 않는" 구멍을 고쳤고 15차 "safe to deploy".
  CI는 main과 같은 기존 16건 실패뿐(새 실패 0).

배포 절차: `bash scripts/deploy.sh backend --override-reason="원장 보고서 v4 쪽 넘김 고아 행 수정(PR #284) — 10월분 12곳 재갱신 전 반영, 배치 시간대 밖"`.
첫 시도는 같은 날 두 번째 릴리스 잠금에 걸려 사유 없이 멈췄고(운영 변경 전), 사유를 붙여 다시 돌렸다. env 정본은 운영 서비스, 차이 0,
`--accept-env-diff` 없음. 마이그레이션 없음(DB head `0093_add_hospital_search_verification` 그대로). 이미지 `reputation:20261105-084645`.

| 서비스 | 리비전 | 이미지 digest |
| --- | --- | --- |
| API | `reputation-api-00278-bmk` | `sha256:b77d1e56cad506b5c765caa17d57cda9f88ecc1e2ed2a0cd57f255a60c352b19` |
| Worker | `reputation-worker-00268-pwp` | 같음 |
| Beat | `reputation-beat-00263-nm4` | 같음 |
| Admin | `reputation-admin-00115-rwt` | 그대로 |
| Site | `reputation-site-00149-6ck` | 그대로 |

롤백 좌표 `.omo/rollback-v4b/backend`(api `00277-khs`·worker `00267-cf4`·beat `00262-dqb`). 잠금 `gs://…/ops/deploy-lock/2026-11-05-override-0847.json`.

검증(08:51 KST): readiness 18/18(`reputation-production-readiness-qwcsw`), `/api/v1/health/live` release = `547c743b`, 공개 표면 smoke 병원 12곳
통과, `facts.auto_publish_hold.active=false`, 새 리비전 ERROR 0건(10:03까지 — 옛 worker `00267`의 cold-shutdown SIGQUIT 2줄은 예상된 것).
새 리비전 WARNING은 WeasyPrint CSS 파서 소음(`@media screen and (max-width:600px)`·`word-break:keep-all`·`font-weight: 100 900`)뿐이다.

## 10월분 12곳 두 번째 템플릿 갱신 — 08:53~10:01 KST

사유 `October-2026-v4-pagination-refresh`(1차와 다른 문자열 — 요청 키가 사유를 포함해 중복 버전을 막는다). 한 곳씩 `migrate` Job에서
`precheck → execute --confirm → postcheck`(Job 메모리 512Mi라 병원별 실행). 노원탑365 카나리(08:53~08:59) PDF를 눈으로 확인한 뒤 11곳.

| 병원 | 버전 | 쪽 | sha | 전달 게이트 | 보고서 id |
| --- | --- | --- | --- | --- | --- |
| 마포성모탑정형외과 | v3 | 11 | 일치 | 준비 | `817bd40f-15d3-41f5-8861-bc48659240ca` |
| 신기한속내과연합의원 | v3 | 10 | 일치 | 준비 | `a64411fe-ac1f-4023-b689-38efd59b30be` |
| 연세속시원내과의원 | v3 | 10 | 일치 | 준비 | `6debc6ff-52bc-49c0-9a78-5936d4fc0af9` |
| 강심장내과의원 | v3 | 10 | 일치 | 준비 | `12cc62c4-35e6-4cae-ac86-90eae1e72276` |
| 장편한외과의원 | v3 | 10 | 일치 | 준비 | `106f3636-ac88-4993-9505-3bd0d1fe734f` |
| 서울W내과의원 위례점 | v5 | 10 | 일치 | 준비 | `1039f877-cbea-48ae-b858-0eaf5bbf1a04` |
| 노원탑365의원 | v3 | 10 | 일치 | 준비 | `dc0473c8-466f-4d54-b333-4116f4951ec5` |
| 뉴플랜여성의원 | v4 | 10 | 일치 | 준비 | `7a80193c-06cc-415d-b0b2-a93542b48dbd` |
| 장앤김더나은속내과의원 | v3 | 10 | 일치 | 준비 | `1e757513-077d-46a9-8bec-c4f4199f8464` |
| 장보고연합내과의원 | v3 | 11 | 일치 | 준비 | `07a469fc-d154-44a1-8b4a-687541c1c5e8` |
| 세종다온신경과의원 | v3 | 12 | 일치 | 준비 | `6f64d26c-913d-4328-a3b6-57d3e4af2883` |
| 행복드림의원 | v3 | 11 | 일치 | 준비 | `4853afbf-eae5-45a9-8774-e33f4297b154` |

12곳 모두 precheck PASS → `SUCCEEDED/ARTIFACT_VALIDATED` → postcheck PASS, 저장 숫자 그대로(`stored_facts_v4` 대조), 원장 PDF 상태 VALID·
전달 게이트 통과. 내려받은 PDF의 sha256이 저장된 `doctor_artifact_sha256`과 전부 일치한다. 쪽 점검 결과는 아래 절.

## 쪽 점검

(126쪽 — 세 사람 몫으로 나눠 래스터를 눈으로 봤다.)

- **결함 0건**: 3행 미만 표 조각(고아 행)·검은 요약 박스만 남은 쪽·제목만 남은 쪽·푸터 겹침·빈 쪽 없음(12곳 126쪽 전부). 1차 갱신본에서 보였던
  히트맵·콘텐츠 목록 1행 넘김과 박스 단독 쪽은 모두 사라졌다.
- 경계(결함 아님): 콘텐츠 목록·측정 키워드 표 이어짐이 정확히 3행인 쪽(신기한속·연세속시원·장보고·세종다온·뉴플랜 p3, 마포·신기한속·연세·장앤김·행복드림
  p5 — 2단 표는 5칸=3행), 부록 계획 표 1~2행 이어짐(연세·서울W p10 1행, 장앤김 p10 2행), 세종다온 p12가 'D. 유의사항'만 담음(1차와 같음).
- 연세속시원 p5의 Gemini 한 칸이 빗금(미측정 표시)인 것은 데이터 표시이지 쪽 넘김 문제가 아니다.

## 3차 배포 — 14:13~14:22 KST, main `4fa3e61f` (AE 수정 판·경쟁 병원 반영)

대표 지시("머지 순서는 알아서, 배포는 12:46 창부터, 기능 동작 확인"). 머지 순서: #285(이 기록) → #286(AE 수정 판) → #287(경쟁 병원 반영).
#287은 #286과 7개 파일이 충돌해 작성 세션이 main을 머지 커밋으로 올려 풀었다 — 경쟁 재생성은 AE 수정 값을 새 기준 뷰 위에 다시 얹되 경쟁 내용을
담을 수 있는 문장 수정만 버리고, AE 수정은 다시 계산된 경쟁 현황을 그대로 든다(머지 트리 백엔드 1267건·Admin 674건, Codex 머지 검토 블로킹 없음).
#287 CI가 main에 없던 실패 하나를 냈다 — 새 작업 `competitor_refresh.rejudge_month_competitors`가 readiness 작업 목록(`EXPECTED_TASKS`)과
`_workflow_facts`의 import에 빠져 있었다(Worker는 include 목록으로 등록하므로 동작에는 영향 없음, readiness가 등록을 확인하지 못하는 구멍). `a158a00e`로
두 줄을 넣고 머지했다. 나머지 Backend CI 실패 16건은 main과 같다(rehearsal·Terraform·Security도 기존 실패).

| 서비스 | 리비전 | 이미지 |
| --- | --- | --- |
| API | `reputation-api-00279-bvp` | `reputation:20261105-141339`, `sha256:6ad2c5159020985d81a4aaaa00e539793eeb0019fe5e969f80f983e4e2889702` |
| Worker | `reputation-worker-00269-rz2` | 같음 |
| Beat | `reputation-beat-00264-n8w` | 같음 |
| Admin | `reputation-admin-00116-8g2` | `admin:20261105-142142`, `sha256:6de187bf758e7a2bdf046a203fd7abcf0a0cbc25508ff729fee52fae9da187cd` |
| Site | `reputation-site-00149-6ck` | 그대로 |

- 같은 날 세 번째 릴리스라 `--override-reason` 필요. 마이그레이션 없음(DB head 그대로). 롤백 좌표 `.omo/rollback-feat1105/backend`(api `00278-bmk`·worker
  `00268-pwp`·beat `00263-nm4`)·`.omo/rollback-feat1105/admin`(`00115-rwt`).
- Admin 대상은 env 정본 로직을 타지 않고 로컬 `.env.production`을 요구해 첫 시도가 운영 변경 전에 멈췄다. `deploy_env.py render`로 운영 API의 현재
  env(비밀 아닌 값 50개)를 그대로 파일로 만들어 배포하고 지웠다.
- 검증: readiness 18/18(새 재판정 작업 등록 포함), `/health/live` release `4fa3e61f`, 공개 표면 smoke 통과, 자동 발행 보류 꺼짐, 새 리비전 ERROR 0건(14:37까지),
  Admin 로그인 화면 200.

### 운영 동작 확인

- **AE 수정 판** — 노원탑365 10월 v3(`dc0473c8`)의 쓰기 없는 미리보기(`GET …/ae-edit/preview`): 편집 가능 칸 37개가 원문과 함께 나오고, 어색한 라벨
  '급성 통증, 경증 응급, 외상'을 '골절·상처 봉합'으로 바꾸면 키워드 표·진료 항목 막대·카드 4 이름에 반영되며 숫자 보존 판정이 통과한다. 금지 표현('완치')·
  원문에 없는 숫자('9')·고칠 수 없는 칸(부록 표)·내부 표식('내부 검수용')은 각각 `FORBIDDEN_EXPRESSION`·`NUMBER_NOT_IN_ORIGINAL`·`UNKNOWN_FIELD`·
  `INTERNAL_MARKER`로 거절된다. **버그 1건**: 라벨 수정으로 노출된 진료 항목이 4→5로 갈라져도 카드 4가 '4개 항목'으로 남고 여전히 노출된 '상처봉합'이 목록에서
  빠진다(숫자 보존 판정도 통과해 그대로 저장될 수 있다). 수정은 작성 세션에 맡겼다(별도 PR). 실제 저장(`POST …/ae-edit`)은 사람 계정 서명이 필요해 운영에서
  아직 실행하지 않았다 — 쓰기 경로는 PR #286의 Postgres 통합 테스트가 덮는다.
- **경쟁 병원 반영** — `monthly_competitor_refresh precheck`(읽기 전용, 12곳): PASS 9·BLOCKED 3(장편한·연세속시원·신기한속 — 등록 경쟁사 없음). 판정이 필요한
  답변은 세종다온 2건뿐이었다(측정 당시 경쟁사 이름 '화인마취통증의학과의원'이 뒤에 '화인마취통증의학과의원 세종'으로 바뀜). 세종다온 한 곳을 실제 실행:
  재판정 `f4bb0f69` SUCCEEDED → 보고서 **v4**(`8ec32330-b952-4212-9552-79bafdd8aee1`) SUCCEEDED, postcheck PASS(경쟁 외 숫자 그대로), sha 일치·VALID·전달 게이트 통과.
  v3의 04절은 '등록한 경쟁 병원 5곳 기준'에 같은 병원이 두 행(옛 이름 '2건 중 2건 100.0%', 새 이름 '88건 중 8건 9%')이었고, v4는 '4곳 기준' 한 행
  '90건 중 10건 11%'로 합쳐졌다(빠진 키워드 목록의 중복 이름도 정리). 유료 판정은 답변 2건분이다.
- precheck Job은 막힌 병원이 있으면 종료 코드 1로 끝난다(설계). 한 번에 12곳을 렌더해도 512Mi Job에서 끝까지 돌았다.

## 롤백 주의

- 10월분 보고서는 이미 v4 스냅샷(두 번째 템플릿 갱신 버전)이다. Worker를 `00266` 이하(v4 이전)로 되돌리면 그 버전의 스냅샷을 다시 찍을 수 없고
  템플릿 갱신·안전망 재생성이 `LEGACY_RENDER_INPUTS_INCOMPLETE`로 막힌다. 코드 롤백은 앞으로만 고친다(새 PR).
- 2차 배포의 롤백(`00267`)은 v4 렌더러이되 쪽 넘김 정리가 없어 고아 행이 다시 생길 뿐 숫자·검증 계약은 같다. 마이그레이션은 없다.

## 남은 일

- 새 버전은 원장에게 **다시 전달**해야 한다(전달 기록은 사람이 append-only로 남긴다). 10월분은 아직 어느 곳도 전달 전이다.
- 미세 조정(결함 아님): 부록 계획 표가 1~2행만 이어지는 경우(P6 본문은 8행으로 잘라서 넘긴다), 세종다온 12쪽이 'D. 유의사항'만 담는 것,
  행복드림 대표 사례 발췌의 punycode URL, 2단 히트맵의 빈 오른쪽 끝 칸.
- 세종다온 10월분은 이제 v4(경쟁 현황 갱신본)가 최신이다. 위 PDF 묶음의 v3가 아니라 v4를 전달한다.
- 경쟁 병원이 없는 3곳(장편한·연세속시원·신기한속)은 병원 정보 탭에서 등록한 뒤 '경쟁 병원 반영'을 돌린다.
- AE 수정 판의 카드 4 항목 수 버그 수정(별도 PR)과, 로그인한 Admin 화면에서 두 패널의 실제 조작 확인이 남았다.
