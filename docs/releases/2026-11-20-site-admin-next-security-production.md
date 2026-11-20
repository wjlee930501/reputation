# 2026-11-20 저녁 Site·Admin 배포 — Next 16.4.6 보안 상향·병원 공개 페이지 개편 1단계

상태: **2026-11-20 19:15~19:23 KST에 Site와 Admin에만 배포했다**(첫 시도 포함, 새 리비전 생성 19:22). 배포한 코드는 main `b6888300`(PR #340 병합)이고, 사용자가 "Site·Admin 지금 배포해줘"라고 지시했다. Backend·DB는 그대로다.

## 왜
- 운영 Site·Admin은 `next@16.3.8`이었다. 이 버전에는 **인증 없는 원격 코드 실행** 권고가 열려 있었다(이미지 최적화 API GHSA-2464-fhr4-w9xw, `use cache` GHSA-673q-mjm6-r5xr, 그 밖에 서버 액션 DoS 등).
- PR [#340](https://github.com/wjlee930501/reputation/pull/340)이 next·eslint-config-next를 **16.4.6**으로 올려 위 Next 권고를 해소했다. 이 PR은 CI 10개(보안 게이트 포함)가 모두 통과한 뒤 main에 병합됐다.
- 보안 게이트(`scripts/check_npm_audit.py`)는 운영 의존성의 high·critical만 본다. 이미지 빌드의 `npm` 출력에는 여전히 Site 15건·Admin 13건(각 high 4건, dev 의존성 포함)이 보인다 — 이번 배포가 '모든 취약점 0'을 뜻하지는 않는다.
- 병원 공개 페이지 개편 1단계(마케팅 요청)는 병합만 되어 있었고, Site를 배포하면서 함께 운영에 올렸다.

## 범위

### Site — 지난 Site 배포(`00156-fsf`, main `e8f769ec`, 11-19) 뒤 병합된 것

| PR | 내용 |
|---|---|
| [#324](https://github.com/wjlee930501/reputation/pull/324) | 글 날짜를 한국 시간으로 표시, 발행 전 편집으로 날짜가 역전되던 표시 제거, 병원 정보 라벨 간격 |
| [#325](https://github.com/wjlee930501/reputation/pull/325) | 같은 질문의 FAQ를 홈에 한 번만 표시(화면 FAQ = FAQPage 유지) |
| [#326](https://github.com/wjlee930501/reputation/pull/326) | 원장 사진을 220px 정사각으로 자르던 것 해제, 의료진 정보 줄 맞춤, A/C안 학회 목록형 |
| [#327](https://github.com/wjlee930501/reputation/pull/327) | 시간·괄호·전화번호·제목 가운뎃점 낱말을 중간에서 끊지 않음 |
| [#328](https://github.com/wjlee930501/reputation/pull/328) | C안 진료 영역 제목·버튼 왼쪽 정렬 |
| [#334](https://github.com/wjlee930501/reputation/pull/334) | 글 모음 이름을 '의료 정보' 하나로 통일 |
| [#335](https://github.com/wjlee930501/reputation/pull/335) | C안 첫 화면 좌우 분할(사진 덮개 없앰, 휴대폰은 사진 위·글 아래) |
| [#336](https://github.com/wjlee930501/reputation/pull/336) | B안 첫 화면 부제를 병원명 아래 한 줄 크기로(4줄 → 3줄) |
| [#337](https://github.com/wjlee930501/reputation/pull/337) | 첫 화면 아래 바로가기 메뉴 빼기(기본형·B) |
| [#338](https://github.com/wjlee930501/reputation/pull/338) | 글 상세 보조 칸을 본문 아래 한 줄로 |
| [#340](https://github.com/wjlee930501/reputation/pull/340) | Next 16.4.6 |

### Admin — 지난 Admin 배포(`00128-4sr`, main `c1d6e179`, 11-20 02:01) 뒤 병합된 것
- #340의 Next 16.4.6뿐이다. 어드민 개편 PR(#323, #330~#333, #339, #341, #342)은 병합 전이라 포함되지 않는다.

### 배포하지 않은 것
- **Backend(API·Worker·Beat)는 바꾸지 않았다.** 그래서 #340의 원장 PDF 검증기 수정(`report_artifact_validation.py`, 부록 질문표 숫자 꼬리 오인 거절)과 `tasks.py` 별칭 import는 다음 Backend 배포부터 적용된다.
- 운영 API 리비전은 02:01 배포(`c1d6e179`)의 `00297-vs5` 그대로다.
- DB는 이번 배포로 바뀌지 않았다. main의 마지막 마이그레이션은 `0099_add_content_schedule_daily_max_superseded`이고 02:01 배포의 migrate Job이 성공했다(`alembic_version`을 직접 조회하지는 않았다).

## 실행 기록

| 단계 | 시각(KST) | 결과 |
|---|---|---|
| 배포 전 리비전 | 19:14 | site `00156-fsf`, admin `00128-4sr`(둘 다 100%, 트래픽 고정 없음). `.omo/rollback-sitesec/before`에 저장 |
| 배포용 worktree | 19:13 | `.omo/deploy-sitesec`(detached `origin/main` = `b6888300`). 두 `package-lock.json`의 `node_modules/next`는 `16.4.6` |
| `.env.production` | 19:15 | 운영 `reputation-api` env를 `scripts/deploy_env.py render`로 만들었다(`--drop SERVICE APP_ENV REPUTATION_RELEASE_REVISION`). `GCP_STORAGE_BUCKET`이 운영 Site의 `NEXT_PUBLIC_GCP_STORAGE_BUCKET`(`reputation-images-mso-platform-481505`)과 같음을 확인했다. 배포 뒤 파일을 지웠다 |
| 1차 `deploy.sh site` | 19:15~19:19(이미지 태그 `191518`) | **실패(운영 변경 전)** — Artifact Registry 레이어 업로드가 `timeout awaiting response headers`로 끊겼다 |
| 1차 `deploy.sh admin` | 19:19~19:21(이미지 태그 `191914`) | **실패(운영 변경 전)** — 이미지 푸시는 성공했지만 `gcloud run deploy`가 `The [project] resource is not properly specified`로 멈췄다(아래 '알게 된 것') |
| 2차 `deploy.sh admin` | 19:21~19:22(태그 `192144`, 리비전 생성 19:22:05) | 성공 — `reputation-admin-00129-8zc` 100% |
| 2차 `deploy.sh site` | 19:22~19:23(태그 `192223`, 리비전 생성 19:22:47) | 성공 — `reputation-site-00157-q7b` 100%. 19:23:04에 사진 캐시 미리 채우기(`warm_site_images.py`)가 뒤에서 시작됐다 |

실행 명령(2차):
```bash
cd .omo/deploy-sitesec
export CLOUDSDK_CORE_PROJECT=mso-platform-481505 GCP_PROJECT_ID=mso-platform-481505 \
  PUBLIC_DOMAIN=reputation.motionlabs.kr ADMIN_DOMAIN=admin.reputation.motionlabs.kr
bash scripts/deploy.sh admin --override-reason="Next 16.4.6 보안 상향(PR #340)·병원 공개 페이지 개편 1단계 반영 — 오늘 두 번째 릴리스"
bash scripts/deploy.sh site  --override-reason="(같은 사유)"
```
`site`·`admin` 대상은 하루 잠금(`require_deploy_slot`)과 배치 시간대를 보지 않는다(`scripts/deploy.sh`의 `require_deploy_slot` 호출은 backend 계열 대상뿐). 그래서 `--override-reason`은 쓰이지 않았다. 19:2x에 `gcloud storage cat`으로 읽은 오늘 잠금 파일 `ops/deploy-lock/2026-11-20.json`은 02:01 Backend 기록(`c1d6e179`) 그대로였다.

## 배포 뒤 확인(19:25~19:30, 새 리비전 생성 뒤 약 8분)
- [x] 리비전: site `00157-q7b`, admin `00129-8zc`가 각각 100%다.
- [x] 공개 병원 16곳(`/api/v1/public/hospitals` 목록)의 홈 `https://reputation.motionlabs.kr/{slug}`을 19:26에 하나씩 요청해 모두 200이었다.
- [x] 랜딩 `https://reputation.motionlabs.kr/` 200, Admin 로그인 `https://admin.reputation.motionlabs.kr/login` 200.
- [x] 개편 반영: 장보고연합내과 홈에 '의료 정보'가 보인다(#334).
- [x] 새 리비전 두 개(`00157-q7b`·`00129-8zc`)로 좁힌 `gcloud logging read … severity>=ERROR`가 19:30 조회 시점까지 0건이다. 리비전 생성 뒤 약 8분 관찰이며 **배포 뒤 30분 관찰은 하지 않았다**.
- [ ] 로그인한 Admin 화면 조작은 하지 않았다. 이번 Admin 변경은 Next 버전뿐이다.
- [ ] 병원별 hospital ID·canonical host 대조는 하지 않았다. `site`·`admin` 대상은 `deploy_smoke.py`도 돌지 않는다.
- [ ] 개편 화면 검증(A/B/C안·휴대폰 폭·글 상세)은 운영에서 따로 하지 않았다. 각 PR에서 화면을 확인했고, 운영에서는 HTTP 200과 '의료 정보' 문구만 봤다.
- [ ] 사진 캐시 미리 채우기 결과는 `.omo/deploy-sitesec/.omo/warmup/site-images-20261120T102304Z.log`에서 본다(약 20분).

## 알게 된 것
- **`deploy.sh`의 Site·Admin `gcloud run deploy`에는 `--project`가 없다.** 그래서 gcloud 기본 프로젝트(`core/project`)에 기댄다. 사전 검증·롤백 좌표·이미지 푸시는 `GCP_PROJECT_ID`를 쓰므로 두 값이 어긋날 수 있다.
  - 기본 프로젝트가 비어 있으면(이번처럼 `gcloud auth login`을 다시 한 뒤) 이미지를 푸시한 뒤 배포 단계에서 멈춘다(운영 변경 없음).
  - 기본 프로젝트가 **다른 프로젝트**로 잡혀 있으면 검증·좌표는 운영 프로젝트인데 배포는 다른 프로젝트로 갈 수 있다.
  - 그래서 실행할 때 `CLOUDSDK_CORE_PROJECT`를 `GCP_PROJECT_ID`와 같은 값으로 주거나, 스크립트에 `--project`를 넣어야 한다(후속).
- Artifact Registry 푸시는 가끔 네트워크 시간 초과로 끊긴다. 다시 실행하면 이미 올라간 레이어는 건너뛴다.

## 같은 날 앞선 배포(이 문서 밖)
- 11-20 02:01~02:08 KST에 main `c1d6e179`(PR #322 일정 시작일·계약 편수·하루 최대 발행 편수)를 `deploy.sh backend`와 `deploy.sh admin`으로 배포했다.
- 결과 리비전은 api `00297-vs5`, worker `00290-ztm`, beat `00285-6vp`, admin `00128-4sr`이다.
- 롤백 좌표는 `.omo/rollback-schedule/{backend,admin}`이다. 마이그레이션 `0099`가 들어간 배포지만 별도 기록 문서(사후 확인·롤백 영향)는 아직 없다 — 이 문서의 범위가 아니며 따로 남겨야 한다.

## 지금 운영 리비전
api `00297-vs5` · worker `00290-ztm` · beat `00285-6vp` · **site `00157-q7b`** · **admin `00129-8zc`**

## 롤백
- 좌표: `.omo/deploy-sitesec/.deploy-rollback`(site `00156-fsf`)와 `.omo/rollback-sitesec/before`(site `00156-fsf`, admin `00128-4sr`).
- `deploy.sh rollback`은 마지막 실행(site)의 좌표만 담고 있다. Admin은 직접 되돌린다.
  ```bash
  gcloud run services update-traffic reputation-admin --region asia-northeast3 --project mso-platform-481505 --to-revisions reputation-admin-00128-4sr=100
  gcloud run services update-traffic reputation-site  --region asia-northeast3 --project mso-platform-481505 --to-revisions reputation-site-00156-fsf=100
  ```
- 위 명령은 트래픽을 그 리비전에 **고정**한다. 그 뒤 평소처럼 `deploy.sh site|admin`을 돌리면 고정 트래픽 검사로 멈추므로 `--unpin`을 붙인다.
- **되돌리면 Next 16.3.8의 원격 코드 실행 권고가 다시 열린다.** 개편 화면 문제라면 롤백보다 Site 코드를 앞으로 고치는 쪽을 먼저 본다.
- DB·Backend는 이번에 바뀌지 않았으므로 되돌릴 것이 없다.
