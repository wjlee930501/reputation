# 2026-11-13 병원 사이트 레이아웃 A/B/C 운영 배포

상태: **2026-11-13 19:44~20:06 KST에 5개 서비스에 배포했다.** 결과는 아래 '실행 기록'.

## 범위
- PR #306(`claude/clinic-layout-abc`). 설계·결정은 [계획 문서](../plans/2026-11-13-clinic-site-layouts-abc.md).
- 운영은 이미 main `6c21141d`(PR #305)로 돌고 있다(`/api/v1/health/live` release, 2026-11-13 17:44 KST 확인). 그래서 이번 배포에 실리는 변경은 이 PR뿐이다.
- 대상: **5개 서비스 전부**(`deploy.sh all`) + 마이그레이션 `0095_add_hospital_site_layout`.
  - DB: `hospitals.site_layout VARCHAR(1) NULL` + CHECK(`A`/`B`/`C`). 추가형이고 기존 행은 NULL이다. 병원 행이 20개 남짓이라 CHECK 검증 잠금은 순간이다.
  - Backend: 프로필 PATCH·계약 등록·공개 API의 `site_layout`, `GET /admin/hospitals/site-layout-distribution`. Worker·Beat는 같은 이미지라 함께 바뀌지만 동작 변화는 없다.
  - Site: 레이아웃 A/C 홈, B CSS, 하위 페이지 셸, 명조 서체(`/fonts/clinic-serif/`).
  - Admin: 계약 등록 필수 선택, 병원 정보에서 변경, BFF 반복 질의값 보존.

## 배포해도 바뀌지 않는 것
- 운영 공개 병원의 화면. 모든 병원이 NULL이라 레이아웃 도입 전 구성 그대로 그린다. 바뀌는 것은 마케터가 레이아웃을 고른 병원뿐이다.
- 단, 모든 병원 페이지가 명조 서체 `@font-face`를 함께 받는다. `unicode-range` 조각이라 쓰지 않는 병원은 글꼴 파일을 받지 않는다.
- 섹션 제목·안내문 문자열은 공통 상수로 옮겼을 뿐 글자는 같다(`git show 6c21141d:<파일>`과 대조함).

## 배포 전 체크리스트
1. PR #306 CI 통과, main에 병합.
2. 병합된 main으로 배포용 worktree를 만든다(작업 폴더의 미추적 파일을 섞지 않는다).
   ```bash
   git fetch origin && git worktree add .omo/deploy-layout origin/main
   ```
3. `gcloud auth login`(사람). 2026-11-13 17:44에 토큰이 만료돼 있었다.
4. 운영 상태를 읽기 전용으로 남긴다.
   - 리비전 5개와 트래픽 고정 여부. `deploy.sh`도 고정이면 멈춘다.
   - 오늘 배포 잠금(`gs://reputation-reports-mso-platform-481505/ops/deploy-lock/<KST 날짜>.json`). 같은 날 다른 릴리스가 있으면 `--override-reason`이 필요하다.
   - DB head가 `0093_add_hospital_search_verification`인지(`ops_sql_report` 또는 migrate Job).
5. **시간대:** Worker·Beat를 바꾸므로 KST 22:15~02:00과 04·07·08·12·18·22시 앞뒤 45분을 피한다. 오늘 가능한 창은 **18:45~21:30**이다(빌드·마이그레이션 시간을 감안). 매월 1일이 아니므로 그 제한은 없다.

## 실행
```bash
cd .omo/deploy-layout
make db-budget-guard
make copy-guard   # 시스템 Python 3.9는 한글 ast 버그로 멈춘다 — backend/.venv/bin/python scripts/check_user_facing_terms.py
bash scripts/deploy.sh all
mkdir -p ../rollback-layout && cp .deploy-rollback ../rollback-layout/all
```
`deploy.sh`가 스스로 하는 일: 운영 env 정본 사용, 배치 시간대 재확인, readiness Job, 공개 표면 확인(release·병원 slug·canonical_host·Site 리비전), 실패 시 트래픽 자동 롤백.

## 배포 뒤 확인
- [x] `/api/v1/health/live`의 release가 병합 SHA다. readiness Job이 통과했다.
- [x] DB head가 `0095_add_hospital_site_layout`이다.
- [x] 공개 병원 13곳의 헬스(hospital ID·canonical host·현재 Site 리비전)를 확인했다. 홈 HTML에 `clinic-shell--layout-`이 없다(NULL 그대로).
- [x] 공개 API에 `site_layout: null`이 실린다.
- [x] `/fonts/clinic-serif/ClinicSerif-Bold.subset.0.woff2`가 200이고 CSP 위반이 없다.
- [ ] **Admin(로그인)**:
  - [ ] 계약 등록 화면에 카드 3장이 보이고, 고르기 전에는 등록 버튼이 막힌다(실제 등록은 하지 않는다).
  - [ ] 병원 정보 → 브랜드 섹션에 '사이트 레이아웃'과 같은 지역 분포 줄이 보인다.
  - [ ] 저장은 대표 승인 뒤 한 병원으로만 한다. 그 병원 공개 페이지가 몇 분 안에 바뀌는지 본다.
- [ ] 새 리비전의 ERROR 로그가 30분 동안 0건이다.
- [ ] `run_beat_entry daily-fleet-heartbeat` 또는 다음 일일 요약이 "점검 이상 없음"이다.

## 롤백
- 트래픽을 되돌리면 된다(`bash scripts/deploy.sh rollback`, 좌표 `.omo/rollback-layout/all`). 모든 병원이 레이아웃 도입 전 화면으로 돌아온다.
- 0095 컬럼은 남겨도 옛 코드에 무해하다(옛 ORM이 그 컬럼을 읽지 않는다). DB는 되돌리지 않는다.
- 롤백 전에 마케터가 레이아웃을 고른 병원이 있으면, 그 값은 DB에 남았다가 다시 배포하면 그대로 적용된다.

## 실행 기록

### 배포
- PR #306을 19:19 KST에 main으로 병합했다(`ccd4b37c`). CI의 실패는 모두 main과 같은 기존 실패였다 — Backend 16건(main 기준선과 같은 목록), Security·Terraform(만료된 npm audit 예외 등), 무결성 리허설(DB head 검사는 `0095`로 통과, 뒤의 화면 클릭 타임아웃은 #305와 같다).
- `.omo/deploy-layout`(origin/main)에서 `make db-budget-guard`(75/80)·용어 가드 통과 뒤 `deploy.sh all`을 실행했다. 첫 두 번은 사전 검증에서 멈췄다(gcloud 기본 프로젝트 없음, `PUBLIC_DOMAIN`·`ADMIN_DOMAIN` 미지정 — 운영 변경 전). 세 번째 실행:
  ```bash
  GCP_PROJECT_ID=mso-platform-481505 PUBLIC_DOMAIN=reputation.motionlabs.kr ADMIN_DOMAIN=admin.reputation.motionlabs.kr bash scripts/deploy.sh all
  ```
- 19:44 시작 → 이미지 3개 → 마이그레이션 `0093 → 0095`(19:48) → Worker → RedBeat 정합성 복구 → Beat → readiness(`reputation-production-readiness-zxqmk` 성공) → API → Site → Admin → 공개 표면 확인 → 20:06 완료(`exit=0`, 경고 0). 오늘 첫 릴리스라 `--override-reason` 없음.

| 서비스 | 새 리비전(100%) | 롤백 기준 |
|---|---|---|
| API | `reputation-api-00290-xrp` | `00289-ldl` |
| Worker | `reputation-worker-00282-hxd` | `00281-b92` |
| Beat | `reputation-beat-00277-x9p` | `00276-fx4` |
| Site | `reputation-site-00150-26b` | `00149-6ck` |
| Admin | `reputation-admin-00122-2dr` | `00121-h5z` |

롤백 좌표는 `.omo/rollback-layout/all`.

### 확인(20:06~20:10 KST)
- release `ccd4b37cfcbfb5fc7dad05c1befc394d87d348d7`, 트래픽 고정 없음.
- 공개 병원 13곳 모두 공개 상세에 `site_layout` 키가 있고 값은 null, 홈 200, 홈 HTML에 `clinic-shell--layout-` 0건. 자기 도메인 10곳의 헬스는 hospital ID·slug·canonical host가 맞고 release가 `reputation-site-00150-26b`다. 자기 도메인이 없는 3곳(서귀포삼성·장앤김더나은속·강심장)은 헬스 대상이 아니라 홈 200만 봤다.
- 명조 서체 조각 200(`font/woff2`), 응답 CSP `font-src 'self' data:` 안.
- ERROR: 새 리비전에서 Beat `/live` 실패 1건(아래). 그 밖 0건.

### 관찰 — 옛 Beat가 다시 남음
11-09에 두 번 본 것과 같다. 퇴역한 옛 Beat `00276-fx4`의 인스턴스가 배포 뒤에도 RedBeat 잠금을 쥐고 매분 같은 스케줄을 보내, 새 Beat `00277-x9p`가 기동 유예(900초) 끝에 `LIVENESS HTTP probe failed`로 재시작됐다(11:05 UTC). 이 릴리스는 Beat 스케줄을 바꾸지 않았고 옛 Beat가 같은 큐로 보내므로 빠진 작업은 없다. 옛 인스턴스가 사라질 때까지 약 15분마다 같은 ERROR가 남을 수 있다. 세 번째 재발이라 배포 절차에서 옛 Beat 인스턴스를 끝내는 방법이 따로 필요하다.

### 남은 것
- **Admin 실조작**: 계약 등록 카드 3장·필수 선택, 병원 정보의 레이아웃·지역 분포 줄. 로그인이 필요해 확인하지 못했다. 저장은 대표 승인 뒤 한 병원으로만.
- 일일 요약은 18:00에 이미 돌았다 — 다음 요약(11/14 18:00)에서 '점검 이상 없음'인지 본다.
