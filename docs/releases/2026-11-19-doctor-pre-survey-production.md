# 2026-11-19 원장 사전 설문 운영 배포

상태: **2026-11-19 18:47~18:55 KST에 5개 서비스에 배포했다**(main `e8f769ec`, 사용자 승인 "병합·배포 진행"). 결과는 아래 '실행 기록'. 설계·결정은 [설계 문서](../plans/2026-11-19-doctor-pre-survey.md).

## 범위

| PR | 브랜치 | 내용 |
|---|---|---|
| [#318](https://github.com/wjlee930501/reputation/pull/318) | `claude/doctor-interview-form` | 링크 발급·동의·자동 저장·제출, 설문 근거 자료(INTERVIEW + `DOCTOR_SURVEY`), Site `/survey/{token}`, Admin 링크 카드, 마이그레이션 `0097` |
| [#319](https://github.com/wjlee930501/reputation/pull/319) | `claude/doctor-survey-uploads` | 사진·로고·인증서 올리기, 마이그레이션 `0098` |
| [#320](https://github.com/wjlee930501/reputation/pull/320) | `claude/doctor-survey-facts` | 제출한 사실 답을 AE가 골라 병원 정보에 반영(서버가 잠금 아래 재확인) |

**병합 순서: #318 → #319 → #320.** 쌓인 PR이다 — #318을 main에 병합한 뒤 #319의 base를 main으로 바꾸고 병합, #320도 같다(GitHub이 base 브랜치 삭제 때 자동으로 바꿔 주지 않으면 직접 바꾼다). 세 PR을 모두 병합한 main으로 **한 번** 배포한다.

### 바뀌는 서비스
- **Backend(API·Worker·Beat)** — 모두 같은 이미지라 함께 바뀐다.
  - API: 공개 `/api/v1/public/doctor-survey/*`(조회·동의·저장·제출·파일), Admin `/api/v1/admin/hospitals/{id}/doctor-survey*`(링크·상태·파일·사실 제안·반영), 공개 캐시 미들웨어의 설문 no-store, 접근 로그 토큰 가림, 예약 slug `survey`, Admin 자료 API의 설문 자료 읽기 전용(409).
  - Worker: `essence_engine.process_source_asset`의 설문 자료 결정적 처리 분기(설문 자료가 생기기 전에는 타지 않는다). Beat: 동작 변화 없음.
- **Site** — `/survey/[token]` 화면, `/api/doctor-survey/*` 프록시, 설문 화면에서 GA·픽셀 태그 끄기, 예약 경로 `/survey`(커스텀 도메인에서 병원 페이지로 rewrite하지 않음).
- **Admin** — 병원 정보 탭 '원장 인터뷰 설문' 카드의 링크·상태·올린 파일·사실 반영 패널.
- 그래서 대상은 **5개 서비스 전부**(`deploy.sh all`) + 마이그레이션 `0097`·`0098`.

### 배포해도 바뀌지 않는 것
- 링크를 만들기 전에는 어떤 병원의 데이터·공개 페이지·Essence도 바뀌지 않는다(`doctor_surveys`가 비어 있다).
- 기존 설문 카드(파일·공유 링크 올리기)는 그대로다.
- 운영 env 변경 없음 — 새 비밀값·플래그가 없다(링크 토큰은 기존 `LEAD_REPORT_TOKEN_SECRET`에 접두어를 달아 재사용).

## 마이그레이션
- `0097_add_doctor_surveys`: 새 테이블 `doctor_surveys`(병원당 한 행, `hospitals`·`hospital_source_assets` FK). 추가형, 기존 행을 건드리지 않는다.
- `0098_add_doctor_survey_files`: `doctor_surveys.files JSONB NOT NULL DEFAULT '{}'`. 빈 테이블에 추가하므로 잠금은 순간이다.
- `deploy.sh all`이 새 이미지로 마이그레이션을 먼저 돌린다. 배포 뒤 DB head는 **`0098_add_doctor_survey_files`**.

## 배포 전 확인
1. 세 PR의 CI가 main과 같은 결과다(이번 PR에서 새로 생긴 실패 없음 — 아래 'CI').
2. 병합된 main으로 배포용 worktree를 만든다(작업 폴더의 미추적 파일을 섞지 않는다).
   ```bash
   git fetch origin && git worktree add .omo/deploy-survey origin/main
   ```
3. `gcloud auth login`(사람).
4. 운영 상태를 읽기 전용으로 남긴다.
   - 리비전 5개와 트래픽 고정 여부(`deploy.sh`도 고정이면 멈춘다).
   - 오늘 배포 잠금(`gs://reputation-reports-mso-platform-481505/ops/deploy-lock/<KST 날짜>.json`). 같은 날 다른 릴리스가 이미 나갔으면 `--override-reason="사유"`가 필요하다.
   - DB head가 `0096_add_hospital_page_copy`인지(`ops_sql_report` 또는 migrate Job).
5. **`survey` slug를 쓰는 병원이 없는지** — 이 배포로 `survey`가 예약 slug가 되고, Site가 `/survey`를 설문 화면으로 가로챈다. 그런 병원이 있으면 그 병원 페이지가 열리지 않는다. 운영 DB를 직접 조회하지 않고 공개 API로 확인한다(읽기 전용).
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" https://<운영 API 호스트>/api/v1/public/hospitals/survey
   ```
   `404`면 없다. `200`이면 배포를 멈추고 그 병원의 slug부터 정한다. (비공개 병원까지 보려면 Admin 병원 목록에서 slug `survey`를 찾는다 — 화면 조회만.)
6. **`LEAD_REPORT_TOKEN_SECRET`을 바꾸지 않는다.** 리포트 링크와 설문 링크가 이 값에서 나온다 — 바꾸면 이미 보낸 모든 설문 링크가 열리지 않는다(AE가 새 링크를 다시 만들어 보내야 한다).
7. **env 차이**: `deploy.sh`는 운영 `reputation-api`의 env를 정본으로 쓰고, 로컬 `.env.production`과 다르면 멈춘다. 이번 배포는 env를 바꾸지 않으므로 `--accept-env-diff`를 쓰지 않는다. `SITE_BASE_URL`이 플랫폼 주소(`https://reputation.motionlabs.kr`)인지 확인한다 — Admin이 보여 주는 설문 링크가 이 주소로 만들어진다.
8. **시간대**: Worker·Beat를 바꾸므로 KST 22:15~02:00과 04·07·08·12·18·22시 앞뒤 45분을 피한다(매월 1일은 00:15~09:00도). `deploy.sh`가 빌드 뒤 첫 운영 변경과 Worker 교체 직전에 다시 확인한다.
   - 2026-11-19(목) 가능 창: **18:45~21:15**(빌드·마이그레이션 약 15~25분을 넣으면 시작은 20:45 전).
   - 2026-11-20(금) 가능 창: 08:45~11:15, **12:45~17:15**, 18:45~21:15.

## 실행
```bash
cd .omo/deploy-survey
make db-budget-guard
backend/.venv/bin/python scripts/check_user_facing_terms.py   # make copy-guard — 시스템 Python 3.9는 한글 ast 버그로 멈춘다
bash scripts/deploy.sh all            # 같은 날 두 번째 릴리스면 --override-reason="원장 사전 설문(#318~#320)"
mkdir -p ../rollback-survey && cp .deploy-rollback ../rollback-survey/all
```
`deploy.sh`가 스스로 하는 일: 운영 env 정본 사용, 배치 시간대 재확인, 마이그레이션, readiness Job, 공개 표면 확인(release·병원 slug·canonical_host·Site 리비전), 실패 시 트래픽 자동 롤백.

## 배포 뒤 확인
- [ ] `/api/v1/health/live`의 release가 병합 SHA다. readiness Job(18개 검사)이 통과했다.
- [ ] DB head가 `0098_add_doctor_survey_files`이다.
- [ ] 공개 병원 헬스(hospital ID·canonical host·현재 Site 리비전)가 배포 전과 같다.
- [ ] 없는 토큰 `GET /api/v1/public/doctor-survey/xxxxxxxxxxxxxxxxxxxxxx`가 `404`이고 응답 머리에 `Cache-Control: no-store`·`X-Robots-Tag: noindex`가 있다. Site `https://reputation.motionlabs.kr/survey/xxxxxxxxxxxxxxxxxxxxxx`가 '링크를 열 수 없습니다'를 보인다.
- [ ] **Admin 설문 카드 실제 화면**(로그인한 AE): 아무 병원의 병원 정보 → '원장 인터뷰 설문' 카드에 '원장님이 직접 작성하는 설문'과 '링크 만들기'가 보인다. **이번 배포로 처음 실제 화면을 연다**(PR에서는 테스트·타입만 확인했다).
- [ ] **`/survey/{token}` 동작**(대표가 고른 시험 병원 하나로만, 실제 원장에게 보내기 전):
  - 링크 만들기 → 복사한 링크를 휴대폰으로 열기 → 동의·작성자 → 몇 문항 작성 → '저장됨' 표시 → 새로고침해도 이어 쓰기.
  - 사진 1장 올리기 → 미리보기 → Admin 카드 '원장님이 올린 파일'에 보임.
  - 제출 → Admin 상태 '제출됨' → 근거 자료 목록에 '원장 사전 설문(원장 작성)'(읽기 전용) → 사실 반영 패널.
  - 시험이 끝나면 '새 링크 만들기'로 시험 링크를 막는다. **시험 답이 그 병원 Essence에 들어간다** — 운영 병원으로 시험하면 그 병원의 필수 문구가 바뀔 수 있으니 시험용(비공개) 병원으로 한다.
- [ ] 새 리비전의 ERROR 로그가 30분 동안 0건이다. API 접근 로그에 설문 토큰이 `[token]`으로 보인다.
- [ ] GA 실시간 보고서에 `/survey/` 경로가 나타나지 않는다(설문 화면은 태그를 싣지 않는다).

## 롤백
- 트래픽을 되돌린다(`bash scripts/deploy.sh rollback`, 좌표 `.omo/rollback-survey/all`). DB는 되돌리지 않는다 — `0097`·`0098`은 추가형이라 옛 코드에 무해하다(옛 ORM이 `doctor_surveys`를 읽지 않는다).
- 롤백하면: 보낸 설문 링크가 열리지 않는다(Site에 `/survey`가 없다 — 커스텀 도메인에서는 병원 페이지 404). 작성 중·제출한 답은 `doctor_surveys`에 남아 다시 배포하면 이어진다.
- 이미 생긴 설문 자료 행(INTERVIEW + `DOCTOR_SURVEY`)은 옛 코드에서 **보통 설문 자료로 보인다** — Admin에서 고치거나 뺄 수 있고(읽기 전용 409가 없다), 다시 처리하면 LLM 추출을 탄다. 롤백 기간에는 그 행을 손대지 않는다.
- 옛 코드는 `survey` slug 병원을 만들 수 있다(예약 slug가 아니다). 롤백 기간에 병원을 등록하면 slug를 확인한다.

## CI(병합 전)
- main과 같은 결과여야 한다. main(`5d3dd327`)에서도 실패하는 검사: Backend lint and tests, Security scans(npm audit), Terraform fmt and validate, rehearsal(브라우저 단계 '검사 전 확인할 준비 사항' 버튼 대기 시간 초과 — 이번 PR과 무관, 마이그레이션 head 단계는 통과). 결과는 각 PR 설명에 적는다.

## 실행 기록

- **병합**: #318 → #319 → #320(병합 커밋 `1b201df5` → `a793ea22` → `e8f769ec`, 17:46~17:47). 병합 전 main(`5d3dd327`)이 #320 머리의 조상이라 병합 뒤 main 트리가 CI·로컬에서 검증한 트리와 같다.
- **CI(main 대비)**: 세 PR의 Backend lint and tests가 main과 같은 실패만 냈다 — 본 suite 16건(같은 테스트), 보조 단계 3건. 새 실패 0건. Security(npm audit)·Terraform·rehearsal(브라우저 단계 버튼 대기 시간 초과)은 main과 이미 병합된 #317·#316·#313에서도 실패하는 기존 실패다. Frontend·Docker·copy·DB budget 가드는 통과.
- **배포 전 확인(읽기 전용)**: 트래픽 고정 없음(5개 LATEST 100%), 로컬 `.env.production` 없음 → 운영 env를 정본으로 그대로 사용(env 차이 없음, `--accept-env-diff` 안 씀), 공개 API `GET /api/v1/public/hospitals/survey` 404(실제 병원 slug 200으로 대조). 오늘 배포 잠금이 없어 `--override-reason` 없이 첫 릴리스로 배포했다(#317은 11-18 15:27 배포).
- **실행**: `GCP_PROJECT_ID=mso-platform-481505 PUBLIC_DOMAIN=reputation.motionlabs.kr ADMIN_DOMAIN=admin.reputation.motionlabs.kr bash scripts/deploy.sh all`(18:47:25~18:54:59). 첫 시도(18:46)는 `PUBLIC_DOMAIN` 미지정으로 운영 변경 전에 멈췄다. legacy publish 사전 확인 0건, 이미지 `20261119-184725`, 마이그레이션 `0096 → 0097 → 0098`, RedBeat 정합성 복구, readiness Job(`reputation-production-readiness-n4k55`) 통과, 배포 뒤 공개 표면 확인 통과.
- **리비전**: api `00296-l9c`, worker `00289-w4p`, beat `00284-7dm`, site `00156-fsf`, admin `00127-gbf`(각 100%). 롤백 좌표 `.omo/rollback-survey/all`(배포 전 api `00295-m9s`, worker `00288-5xw`, beat `00283-ch9`, site `00155-gdr`, admin `00126-r5v`).

### 배포 뒤 확인(18:55~)
- [x] `/api/v1/health/live` release `e8f769ec42ecbed9213fc6f56e211f610adc6cf0`. readiness Job 통과.
- [x] DB head `0098_add_doctor_survey_files`(migrate Job 로그 `0096 → 0097 → 0098`).
- [x] 없는 토큰 `GET /api/v1/public/doctor-survey/…` 404 + `Cache-Control: no-store` + `X-Robots-Tag: noindex, nofollow`. Site `/survey/…` 200(화면이 '링크를 열 수 없습니다'), `<meta name="robots" content="noindex, nofollow, nocache">`, GA 태그 없음. 실제 병원 공개 API 200.
- [x] 새 리비전 5개의 ERROR 로그 0건(18:47~18:55 기준 — 30분 관찰은 이어서).
- [x] **Admin 설문 카드 실제 화면**: 연세유나유외과의원 병원 정보 → '원장 인터뷰 설문' 카드에 '원장님이 직접 작성하는 설문'·개원 병원 표시·'링크 만들기'가 보인다(상태 조회 정상). 링크는 만들지 않았다(운영 데이터 쓰기).
- [x] **실제 링크(사용자 확인 뒤)**: 연세유나유외과의원(비공개·준비 중)에 Admin에서 '링크 만들기'를 눌렀다(19:00, 설문 행 1개·감사 `doctor_survey_link_issue` 1건). 카드가 링크·'링크 복사'·'새 링크 만들기'·'원장님이 아직 열어 보지 않았습니다'를 보인다. 링크가 열리는지는 원장의 '열어 봄' 기록을 남기지 않게 빈 동의 요청으로 확인했다 — 실제 토큰 422(본문 검사, 쓰기 없음)·없는 토큰 404, Site 화면 200. 동의·작성·제출은 하지 않았다(원장이 처음부터 쓴다).
- [x] Slack `#C09CU7JAX6U` 사전 설문지 스레드에 배포·사용법 댓글(사용자 확인 문구, 19:02).
- [ ] 새 리비전 ERROR 30분 관찰.
