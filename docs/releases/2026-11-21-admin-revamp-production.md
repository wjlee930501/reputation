# 2026-11-21 새벽 Backend·Admin 두 번 배포 — 어드민 개편 1단계·레이아웃 편집기 3a

상태: **2026-11-21 KST에 Backend·Admin을 두 번 배포했다.**
- 1차: 02:01~02:07, main `2241f431`.
- 2차: 04:46~04:51, main `85754f20`. 2차에는 마이그레이션 `0100`이 있다.
- 사용자 지시: "완료 되면 병합, 배포까지 하고 바로 다음 단계로", "1단계에서 할 수 있는 것들 바로 진행 먼저 싹하고, 다음으로 레이아웃 편집기 3a 진행".
- Site는 바꾸지 않았다. 운영 Site는 그대로 `00157-q7b`다.

## 왜
- **어드민 개편 1단계(마케팅 요청):** 병합된 PR을 운영 Admin·Backend에 올렸다. 계획은 `docs/plans/2026-11-20-revamp/`에 있다.
- **레이아웃 편집기 3a:** 병원 공개 페이지 섹션 설정의 저장소와 API다. 설계는 `docs/plans/2026-11-20-clinic-site-sections-design.md`다.
  - 3b(사이트 그리기)와 3c(Admin 편집 화면)가 이 API 위에 올라간다. 그래서 3a를 먼저 운영 DB에 올렸다.

## 범위

### 1차 — main `2241f431`(지난 Backend 배포 `c1d6e179` 뒤 병합된 것)

| PR | 내용 |
|---|---|
| [#323](https://github.com/wjlee930501/reputation/pull/323) | ① 문구 정리 — 영문 코드 한글화, 첫 연락 기한 삭제, 지역 통계 범위. 개편 계획 문서 포함 |
| [#330](https://github.com/wjlee930501/reputation/pull/330) | ② 주소 링크·복사, 콘텐츠 「공개 글」 버튼 |
| [#333](https://github.com/wjlee930501/reputation/pull/333) | ③ 위험 동작 ⋯ 메뉴와 확인창 |
| [#331](https://github.com/wjlee930501/reputation/pull/331) | ④ 보고서 탭 — 최신 판 기준 숫자, 같은 달 작업 카드 하나, 원장 PDF 바로 받기 |
| [#332](https://github.com/wjlee930501/reputation/pull/332) | ⑤ 근거 자료 — 제외 자료 접기, 처음 5개만, 제외 노트 되돌리기 |
| [#342](https://github.com/wjlee930501/reputation/pull/342) | ⑥b·⑥c 병원 목록 칸·필터·정렬, 대시보드 첫 화면, `GET /admin/operations-center/nav-counts` |
| [#339](https://github.com/wjlee930501/reputation/pull/339) | 생성 품질 ⑦ — 필수 문구에서 깨진 추출 문장·자료 머리말·메모체를 뺀다. 자동 Essence 정리 정책 판은 `2026-11-20.1` |
| [#341](https://github.com/wjlee930501/reputation/pull/341) | ⑥a 메뉴 — 병원·처리 목록·노출 진단·설정, 배지, 자동 작업 한도를 설정으로(00:34 병합) |
| [#340](https://github.com/wjlee930501/reputation/pull/340) | (11-20 Site·Admin만 배포) 원장 PDF 검증기 수정 — 부록 질문표 숫자 꼬리 오인 거절. **이번이 이 수정이 실린 첫 Backend 배포다** |

### 2차 — main `85754f20`(1차 뒤 병합된 것)

| PR | 내용 |
|---|---|
| [#344](https://github.com/wjlee930501/reputation/pull/344) | 좁은 화면 표 열 머리글을 시각적으로만 숨김(접근성) |
| [#345](https://github.com/wjlee930501/reputation/pull/345) | 새 글 FAQ 질문을 존댓말 환자 질문형으로. 「ContentType.TREATMENT」 제목 노출 수정 |
| [#346](https://github.com/wjlee930501/reputation/pull/346) | 정체 감지 멱등 테스트의 KST 자정 의존 제거(테스트만) |
| [#347](https://github.com/wjlee930501/reputation/pull/347) | 처리 목록 배지 집계 경량화(원인 묶음 수만). 비용 한도 사고 링크를 설정 화면으로 |
| [#348](https://github.com/wjlee930501/reputation/pull/348) | 병원 상태 — 자동 공개 대기, 자기 도메인 대기, 명시적 비공개 구분 |
| [#329](https://github.com/wjlee930501/reputation/pull/329) | 섹션 설정(레이아웃 편집기) 설계·결정 대기 문서 |
| [#349](https://github.com/wjlee930501/reputation/pull/349) | **레이아웃 편집기 3a** — 섹션 설정 표·검증·쓰기 API·공개 응답 `site_config`·미리보기 토큰. 마이그레이션 `0100_add_hospital_site_configs`(새 표, 추가형) |

### 배포하지 않은 것
- **Site:** 3b(#351·#352)가 병합 전이라 사이트는 아직 `site_config`를 읽지 않는다. 공개 응답에 `site_config`가 새로 실려도 화면은 그대로다.
- **3c 편집 화면:** #350·#353은 병합 전이다.

## 실행 기록

| 단계 | 시각(KST) | 결과 |
|---|---|---|
| 1차 배포 전 리비전 | 01:59 | api `00297-vs5`, worker `00290-ztm`, beat `00285-6vp`, admin `00129-8zc`(모두 100%). `.omo/rollback-admin1121/before.txt` |
| 1차 `deploy.sh backend` | 02:01~02:05 | 성공. worker `00291-xwr`, beat `00286-rwp`, readiness `mvwlq` 통과, api `00298-r5v`, 공개 표면 확인 통과. 오늘(11-21) 첫 Backend 릴리스라 사유 없이 진행 |
| 1차 `deploy.sh admin` | 02:05~02:07 | 성공. admin `00130-pds` |
| main CI(`85754f20`) | 03:49 | 9/9 통과 |
| 2차 배포 전 리비전 | 04:3x | api `00298-r5v`, worker `00291-xwr`, beat `00286-rwp`, admin `00130-pds`. `.omo/rollback-2nd1121/before.txt` |
| 2차 `deploy.sh backend` | 04:46~04:50 | 성공. 07시 배치 시간대(06:15~) 전, 04시 시간대(~04:45) 뒤에 시작했다. 같은 날 두 번째 릴리스라 `--override-reason` 사용. migrate Job `0099 → 0100_add_hospital_site_configs`, worker `00292-dng`, beat `00287-nlk`, readiness `t7vzv` 통과, api `00299-5cz`, 공개 표면 확인 통과 |
| 2차 `deploy.sh admin` | 04:50~04:51 | 성공. admin `00131-szn` |

실행 명령:
```bash
cd .omo/deploy-2nd1121   # detached origin/main = 85754f20 (1차는 .omo/deploy-admin1121 = 2241f431)
export CLOUDSDK_CORE_PROJECT=mso-platform-481505 GCP_PROJECT_ID=mso-platform-481505 \
  PUBLIC_DOMAIN=reputation.motionlabs.kr ADMIN_DOMAIN=admin.reputation.motionlabs.kr
gcloud run services describe reputation-api --region asia-northeast3 --format=json \
  | python3 scripts/deploy_env.py render --service-json - --out .env.production --drop SERVICE APP_ENV REPUTATION_RELEASE_REVISION
bash scripts/deploy.sh backend --override-reason="어드민 개편 1단계 잔여(#344~#348)·레이아웃 편집기 3a(#349, 0100) — 오늘 두 번째 릴리스"
bash scripts/deploy.sh admin
```
- `.deploy-rollback`은 대상마다 덮어쓴다. 그래서 Backend 좌표는 위 `before.txt`가 정본이다.

## 배포 뒤 확인

### 1차
- [x] readiness 통과, 공개 표면 확인 통과.
- [x] 공개 병원 16곳 200, 자기 도메인 health의 hospital ID 일치.
- [x] Admin 로그인 200.
- [x] 약 30분 뒤 새 리비전 ERROR 0.

### 2차(04:51~04:55)
- [x] `/api/v1/health/live`의 `release`가 `85754f208761820d1af967738f3c11d1446e6125`다.
- [x] migrate Job 로그에 `Running upgrade 0099_add_content_schedule_daily_max_superseded -> 0100_add_hospital_site_configs`가 있다.
  - `alembic_version`은 직접 조회하지 않았다.
- [x] 공개 병원 16곳 slug 200. 자기 도메인 13곳 `/.well-known/reputation-health`의 hospital ID가 일치한다.
  - 다올·강심장·다대으뜸은 자기 도메인이 없다.
- [x] Admin 로그인 200.
- [x] Beat `00287-nlk`이 100%이고 04:49:08에 `beat_lock_acquired`를 남겼다(대기 경고 `beat_lock_wait_long` 없음).
- [x] 04:46 이후 Cloud Run 리비전 ERROR 0건(04:55 확인). +30분 확인은 아래에 덧붙인다.
- [ ] readiness `facts.auto_publish_hold`: Job 로그에 사실 값이 찍히지 않아 '통과'만 기록한다.
- [ ] 새 화면을 로그인해서 조작해 보는 확인은 하지 않았다. 대상은 대시보드, 병원 목록 필터, 보고서 PDF 받기, ⋯ 메뉴다.

## 알게 된 것·주의
- **#339 정책 판 상향:** 1차 배포 뒤 15분 조정에서 자동 Essence 판 10곳이 `T7_POLICY_UPGRADE`로 다시 만들어졌다(Worker 로그 `APPLIED` 10건).
  - 필수 문구는 새 정책으로 다시 골랐다. 깨진 추출 문장·머리말·메모체는 이제 고르지 않는다.
  - 새 판을 적용해도 공개 글은 숨지 않는다(E7).
- **레이아웃 편집기 — 3b ③ 배포 전에는 운영에서 '공개 반영'을 하지 않는다.**
  - 공개 판의 `resolved`는 스냅샷이다. 그래서 3a 기본값 중 지금 화면과 다른 칸(FAQ 6개, C안 영문 소제목 등)으로 공개하면 그 값이 영구히 남는다.
  - 고친 기본값(`DEFAULTS_VERSION 2026-11-21.2`)은 #351(3b ①)에 있다. 첫 화면 정보 블록과 의료진 사진 칸은 3b ③이 맞추고 판을 한 번 더 올린다.
  - 그래서 Admin의 공개 반영 막음(`SITE_RENDERS_SITE_CONFIG = false`)은 3b ③이 배포된 뒤 푼다.
  - 지금 운영에는 편집 화면(3c)이 없다. 그래서 API를 직접 부르지 않는 한 공개 반영이 일어나지 않는다.
- **미리보기 비밀값 `SITE_PREVIEW_TOKEN_SECRET`은 운영에 없다.**
  - #349가 이 값을 선택 비밀(`BACKEND_OPTIONAL_SECRET_NAMES`)로 더했다. Secret Manager에 이 이름의 비밀이 없음을 확인했다(`gcloud secrets list`). 없으면 미리보기 토큰 발급이 503(fail-closed)이다. 운영 Admin에는 아직 편집 화면(3c)이 없어 미리보기를 부를 곳도 없다.
  - 미리보기를 실제로 쓸 때 대표 확인 뒤 만든다.
- **#341 테스트:** `test_stall_dispatch_claims_and_hands_off_to_item_task`가 KST 자정을 넘는 시각에 돌면 실패했다. 변경과 무관하며 #346에서 정오 고정으로 고쳤다.
- **CI 리허설은 DB head를 하드코딩한다.** 위치는 `scripts/integrity_rehearsal/check.py`다. 마이그레이션을 더하는 PR은 이 값도 함께 올려야 한다(#349가 0100으로 올렸다).

## 지금 운영 리비전
- api `00299-5cz`, worker `00292-dng`, beat `00287-nlk`, admin `00131-szn`, site `00157-q7b`.
- DB head는 `0100_add_hospital_site_configs`다.

## 롤백
`deploy.sh rollback`은 `서비스=리비전` 형식 파일을 읽는다(`DEPLOY_ROLLBACK_STATE_FILE`). 각 `before.txt`는 조회 결과라 그대로 쓸 수 없어 같은 폴더에 `rollback.state`를 만들어 두었다. 배포 worktree의 `.deploy-rollback`은 마지막 Admin 배포가 덮어써 Admin 좌표만 남아 있다.
```bash
DEPLOY_ROLLBACK_STATE_FILE=$PWD/.omo/rollback-2nd1121/rollback.state bash scripts/deploy.sh rollback   # 2차만
DEPLOY_ROLLBACK_STATE_FILE=$PWD/.omo/rollback-admin1121/rollback.state bash scripts/deploy.sh rollback # 1차까지
```
- 2차만 되돌릴 때:
  - Backend: `.omo/rollback-2nd1121/before.txt`의 api `00298-r5v`, worker `00291-xwr`, beat `00286-rwp`.
  - Admin: `00130-pds`.
  - 마이그레이션 0100은 새 표 하나뿐이라 그대로 둬도 옛 코드가 영향을 받지 않는다.
- 1차까지 되돌릴 때: `.omo/rollback-admin1121/before.txt`(api `00297-vs5`, worker `00290-ztm`, beat `00285-6vp`, admin `00129-8zc`).
  - #339로 다시 만든 Essence 판은 코드 롤백으로 돌아오지 않는다. 그리고 T7은 판의 `policy_version`이 지금 코드의 정책 판과 **다르면**(`!=`) 다시 만든다. Worker를 #339 이전으로 되돌리면 새 판 10곳이 옛 정책(`2026-10-14.5`)으로 한 번 더 다시 만들어진다.
- Worker를 `00223` 이하로 롤백하지 않는다는 기존 규칙은 그대로다.
