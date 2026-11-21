# 2026-11-21 다섯 번 배포 — 어드민 개편 1단계·레이아웃 편집기 3a·3b·3c·③-1·미리보기 비밀값·③-2a·③-2b

상태: **2026-11-21 KST에 다섯 번 배포했다.**
- 1차: 02:01~02:07, main `2241f431`, Backend·Admin.
- 2차: 04:46~04:51, main `85754f20`, Backend·Admin. 2차에는 마이그레이션 `0100`이 있다.
- 3차: 08:46~08:52, main `0f93551b`, **Backend·Site·Admin**. 마이그레이션은 없다.
- 4차: 10:13~10:17, 같은 main `0f93551b`, Backend만. 코드 변경 없이 새로 만든 미리보기 비밀값 `SITE_PREVIEW_TOKEN_SECRET`을 주입했다(사용자 지시 "만들고 배포").
- 5차: 15:36~15:43, main `84b8f70c`, **Backend·Site·Admin**. 레이아웃 편집기 ③-2a·③-2b(#357~#360). 마이그레이션은 없다. 14:20 첫 시도는 gcloud 로그인 만료로 사전 검증에서 멈췄다(운영 변경 없음).
- 사용자 지시: "완료 되면 병합, 배포까지 하고 바로 다음 단계로", "1단계에서 할 수 있는 것들 바로 진행 먼저 싹하고, 다음으로 레이아웃 편집기 3a 진행", "배포 2차 끝나고 문서 쓰고, 다음도 이어서 알아서 진행해".
- 1·2차는 Site를 바꾸지 않았다. 3차에서 Site가 `00157-q7b` → `00158-qt2`가 됐다.

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

### 3차 — main `0f93551b`(2차 뒤 병합된 것)

| PR | 내용 |
|---|---|
| [#351](https://github.com/wjlee930501/reputation/pull/351) | **3b ①** — 사이트가 공개 `site_config`로 홈 섹션·FAQ·메뉴·공유를 그린다. `site_config`가 없으면 지금 화면 그대로 |
| [#352](https://github.com/wjlee930501/reputation/pull/352) | **3b ②** — 섹션 설정 초안 미리보기 `/preview/{token}`(no-store·noindex). 미리보기 자산은 이미지 최적화기를 거치지 않는다 |
| [#350](https://github.com/wjlee930501/reputation/pull/350) | **3c PR1** — Admin 편집 화면: 섹션·첫 화면·FAQ, 임시 저장·미리보기·공개 반영·이력 |
| [#353](https://github.com/wjlee930501/reputation/pull/353) | **3c PR2** — 줄바꿈·초점·덮개, 섹션별 세부 설정, 페이지 전체 |
| [#354](https://github.com/wjlee930501/reputation/pull/354) | 1·2차 배포 기록(문서만) |
| [#355](https://github.com/wjlee930501/reputation/pull/355) | **③-1** — 사이트가 아직 읽지 않는 칸의 기본값을 지금 화면에 맞춤(`DEFAULTS_VERSION 2026-11-21.3`). 옛 공개 판은 그 칸만 지금 기본값으로 다시 합쳐 낸다(`site_defaults.refresh_stale_defaults`, 저장 스냅샷은 그대로) |

### 배포하지 않은 것
- 1·2차 때는 3b(#351·#352)와 3c(#350·#353)가 병합 전이었다. 모두 3차에 실렸다.
- **공개 반영은 여전히 막혀 있다.** Admin `SITE_RENDERS_SITE_CONFIG = false`는 3차에서도 그대로다. 아래 '알게 된 것·주의'를 본다.

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

### 3차

| 단계 | 시각(KST) | 결과 |
|---|---|---|
| #355 CI | 08:35 | 10/10 통과(앞서 Docker 기동 실패 1회는 러너 문제라 재실행). 병합 커밋 `0f93551b`의 트리가 CI가 검사한 `d29303db`와 같다 |
| 배포 전 리비전 | 08:36 | api `00299-5cz`, worker `00292-dng`, beat `00287-nlk`, site `00157-q7b`, admin `00131-szn`. `.omo/rollback-3rd1121/rollback.state` |
| `deploy.sh backend` | 08:46~08:50 | 성공. 08시 배치 시간대(~08:45)가 끝난 뒤 시작했다. 오늘 세 번째 릴리스라 `--override-reason`. head는 그대로 `0100`. worker `00293-89h`, beat `00288-wn7`, readiness `6c4st` 통과, api `00300-gpw`, 공개 표면 확인 통과 |
| `deploy.sh site` | 08:50~08:51 | 성공. site `00158-qt2`. 사진 캐시 미리 채우기가 뒤에서 돌았다(`.omo/rel3rd1121/.omo/warmup/`) |
| `deploy.sh admin` | 08:51~08:52 | 성공. admin `00132-ssx` |

```bash
cd .omo/rel3rd1121   # detached origin/main = 0f93551b
# env 렌더는 위와 같다
DEPLOY_ROLLBACK_STATE_FILE=.deploy-rollback-backend bash scripts/deploy.sh backend --override-reason="레이아웃 편집기 3b①②·3c·③-1 기본값 — 오늘 세 번째 릴리스"
DEPLOY_ROLLBACK_STATE_FILE=.deploy-rollback-site bash scripts/deploy.sh site
DEPLOY_ROLLBACK_STATE_FILE=.deploy-rollback-admin bash scripts/deploy.sh admin
```
- 이번에는 대상마다 롤백 파일을 따로 두어 덮어쓰지 않았다.

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
- [x] 04:46~05:22(+약 33분) 사이 Re:putation 서비스(api·worker·beat·admin·site) ERROR 0건. 같은 프로젝트의 다른 서비스 `kakaotalk-server`의 인증 ERROR 2건은 이 배포와 무관하다.
- [ ] readiness `facts.auto_publish_hold`: Job 로그에 사실 값이 찍히지 않아 '통과'만 기록한다.
- [ ] 새 화면을 로그인해서 조작해 보는 확인은 하지 않았다. 대상은 대시보드, 병원 목록 필터, 보고서 PDF 받기, ⋯ 메뉴다.

### 3차(08:52~)
- [x] `/api/v1/health/live`의 `release`가 `0f93551b20ea612aa2dc5ca2e3b55c4acc3d370e`다. 다섯 서비스 모두 새 리비전이 100%다.
- [x] 공개 병원 16곳 slug 200. 자기 도메인 13곳 health의 hospital ID가 일치한다.
- [x] **공개 화면이 바뀌지 않았다.** 16곳 모두 공개 응답의 `site_config`가 배포 전처럼 null이고 `site_layout`도 같다.
  - 홈 4곳(장편한 B, 행복드림·다대으뜸 C, 뉴플랜 기본)을 배포 전후로 받아 비교했다. 보이는 글자·링크·이미지 대체 글·섹션 class가 모두 같다. 문서 안 class 개수도 같다.
  - 다른 것은 Next RSC 데이터의 조각 번호·순서뿐이다(빌드가 바뀌어 생긴 차이).
- [x] Admin 로그인 200. 편집 화면 경로는 로그인 없이 열면 로그인으로 보낸다(307).
- [x] 미리보기: 없는 토큰이면 API는 404, 사이트 `/preview/{token}`은 "미리보기를 열 수 없습니다" 화면이다. 둘 다 `no-store`·`noindex`다.
- [x] Beat `00288-wn7`이 08:49:23에 `beat_lock_acquired`를 남겼다.
- [x] 08:43~09:18(배포 뒤 약 30분) 사이 Re:putation 서비스(`reputation-*`) ERROR 0건.
- [ ] 편집 화면을 로그인해서 조작해 보는 확인은 하지 않았다(임시 저장도 운영 데이터를 남긴다).

### 4차 — 미리보기 비밀값(10:13~10:17)
- Secret Manager에 `SITE_PREVIEW_TOKEN_SECRET`을 만들었다(자동 복제, 무작위 32바이트 hex 한 버전, 값은 어디에도 출력하지 않았다).
  - `reputation-sa`에 그 비밀 하나만 `secretAccessor`를 줬다(다른 비밀과 같은 비밀 단위 권한). API·Worker·Beat·readiness·migrate Job이 모두 이 계정이다. Site는 이 값을 쓰지 않는다.
- `deploy.sh backend --override-reason=…`(오늘 네 번째 릴리스). 선택 비밀은 배포 때만 주입되므로 같은 코드를 다시 배포했다.
  - 배포 전 리비전: api `00300-gpw`, worker `00293-89h`, beat `00288-wn7` → `.omo/rollback-4th1121/rollback.state`.
  - 결과: worker `00294-zv7`, beat `00289-m8j`, readiness `9xntv` 통과, api `00301-pjs`, 공개 표면 확인 통과.
- 확인: 세 새 리비전 모두 `SITE_PREVIEW_TOKEN_SECRET`을 Secret Manager 참조로 갖는다. release `0f93551b` 그대로, 공개 병원 16곳 200·hospital ID 일치, 가짜 토큰 미리보기는 여전히 404.
- 확인하지 않은 것: 편집 화면에서 실제 미리보기 링크를 발급해 열어 보는 것(로그인한 사람 계정 서명이 필요하다).
- **공개 반영은 여전히 막혀 있다**(`SITE_RENDERS_SITE_CONFIG = false`, Admin 배포가 따로 필요). 이 비밀값을 바꾸면 그때까지 발급된 미리보기 링크(10분짜리)만 무효가 된다.

### 5차 — ③-2a·③-2b(15:36~15:43)
범위(main `712dae5d`·`880dd185`·`135819de`·`84b8f70c`):

| PR | 내용 |
|---|---|
| [#357](https://github.com/wjlee930501/reputation/pull/357) | ③-2a — B안 첫 화면 정보 띠 칸·순서(오늘 진료·전화·위치·진료시간·예약), 예약 칸은 네이버 플레이스(없으면 빠지고 저장 때 경고만), 진료 항목 목록 모양·설명 끄기 |
| [#358](https://github.com/wjlee930501/reputation/pull/358) | A·C에서 정보 띠 칸·진료 항목 모양을 기본값과 다르게 저장하면 거절(`B_LAYOUT_ONLY`) |
| [#359](https://github.com/wjlee930501/reputation/pull/359) | 어드민 A·C 편집 화면에서 두 칸 숨김·저장 내용에서 뺌(#358 짝) |
| [#360](https://github.com/wjlee930501/reputation/pull/360) | ③-2b — 의료진 사진 비율·초점·직함 위치(A·B·C) |

- 배포 전 확인: 네 PR을 합친 main에서 백엔드 사이트 설정 테스트 200 passed(`test_site_config*`·`test_public_site`·로고), Site 607/607·Admin 753/753, 두 쪽 `tsc` 통과. 각 PR CI 10/10.
- 배포 전 리비전: api `00301-pjs`, worker `00294-zv7`, beat `00289-m8j`, site `00158-qt2`, admin `00132-ssx` → `.omo/rollback-5th1121/rollback.state`.
- 결과: worker `00295-5tm`, beat `00290-8mg`, readiness `nj74b` 통과, api `00302-ksn`, site `00159-jzr`, admin `00133-lp7`. 공개 표면 확인 통과.
- 확인: release `84b8f70c`, 공개 병원 16곳 200·hospital ID 일치, 16곳 `site_config` null 그대로, 홈 4곳(B·C·기본·C) 보이는 글자·링크·이미지와 class 구성이 아침 기준과 같다. Admin 로그인 200.
- **공개 반영은 여전히 막혀 있다.** ③-2c~f와 나머지 칸 정리(#361·#362·#365·#366·#367·#368)가 아직 병합 전이다.
- 사고: #360을 `--delete-branch`로 병합하자 그 브랜치를 base로 쌓인 #361이 GitHub에서 자동으로 닫혔다(재지정되지 않았다). 브랜치를 같은 커밋으로 되살리고 #361을 다시 열어 base를 main으로 바꿨다. 쌓인 PR의 아래 PR은 브랜치를 지우지 않고 병합한다.

## 알게 된 것·주의
- **3차 뒤 공개 반영을 풀 수 있는가:** ③-1로 '틀린 기본값이 공개 판에 굳는' 위험은 막혔다. 옛 판도 그 칸만 지금 기본값으로 낸다. 그래도 이번에는 풀지 않았다. 남은 두 가지가 사용자 결정이기 때문이다.
  - 미리보기 비밀 `SITE_PREVIEW_TOKEN_SECRET`이 운영에 없어 편집 화면의 미리보기가 503이다. 미리보기 없이 공개 반영만 여는 것은 위험하다.
  - ③-2에서 첫 화면 정보 블록(`hero.blocks`)·진료 항목 모양(`treatments.style`)을 B에만 연다(결정됨). 지금 편집 화면(#353)은 A·C에도 이 두 칸을 보이지만 A·C 화면은 이 칸을 그리지 않는다.
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
- api `00302-ksn`, worker `00295-5tm`, beat `00290-8mg`, site `00159-jzr`, admin `00133-lp7`.
- DB head는 `0100_add_hospital_site_configs`다.

## 롤백
`deploy.sh rollback`은 `서비스=리비전` 형식 파일을 읽는다(`DEPLOY_ROLLBACK_STATE_FILE`). 각 `before.txt`는 조회 결과라 그대로 쓸 수 없어 같은 폴더에 `rollback.state`를 만들어 두었다. 배포 worktree의 `.deploy-rollback`은 마지막 Admin 배포가 덮어써 Admin 좌표만 남아 있다.
```bash
DEPLOY_ROLLBACK_STATE_FILE=$PWD/.omo/rollback-2nd1121/rollback.state bash scripts/deploy.sh rollback   # 2차만
DEPLOY_ROLLBACK_STATE_FILE=$PWD/.omo/rollback-admin1121/rollback.state bash scripts/deploy.sh rollback # 1차까지
```
```bash
DEPLOY_ROLLBACK_STATE_FILE=$PWD/.omo/rollback-3rd1121/rollback.state bash scripts/deploy.sh rollback   # 3차만(Site 포함 다섯 서비스)
```
- 5차만 되돌릴 때: `DEPLOY_ROLLBACK_STATE_FILE=$PWD/.omo/rollback-5th1121/rollback.state` — api `00301-pjs`, worker `00294-zv7`, beat `00289-m8j`, site `00158-qt2`, admin `00132-ssx`(공개된 설정이 없으니 화면은 같다).
- 4차만 되돌릴 때: `DEPLOY_ROLLBACK_STATE_FILE=$PWD/.omo/rollback-4th1121/rollback.state` — api `00300-gpw`, worker `00293-89h`, beat `00288-wn7`(코드는 같고 비밀값만 빠진다 → 미리보기 발급 503).
- 3차만 되돌릴 때: api `00299-5cz`, worker `00292-dng`, beat `00287-nlk`, site `00157-q7b`, admin `00131-szn`.
  - 아직 공개 반영된 설정이 없으므로(16곳 모두 null) Site를 되돌려도 화면은 같다.
- 2차만 되돌릴 때:
  - Backend: `.omo/rollback-2nd1121/before.txt`의 api `00298-r5v`, worker `00291-xwr`, beat `00286-rwp`.
  - Admin: `00130-pds`.
  - 마이그레이션 0100은 새 표 하나뿐이라 그대로 둬도 옛 코드가 영향을 받지 않는다.
- 1차까지 되돌릴 때: `.omo/rollback-admin1121/before.txt`(api `00297-vs5`, worker `00290-ztm`, beat `00285-6vp`, admin `00129-8zc`).
  - #339로 다시 만든 Essence 판은 코드 롤백으로 돌아오지 않는다. 그리고 T7은 판의 `policy_version`이 지금 코드의 정책 판과 **다르면**(`!=`) 다시 만든다. Worker를 #339 이전으로 되돌리면 새 판 10곳이 옛 정책(`2026-10-14.5`)으로 한 번 더 다시 만들어진다.
- Worker를 `00223` 이하로 롤백하지 않는다는 기존 규칙은 그대로다.
