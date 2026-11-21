# 2026-11-17 Beat 잠금 인계 운영 배포

11-09·11-13·11-15 배포마다 옛 Beat가 RedBeat 잠금을 쥔 채 남아 같은 스케줄을 계속 보내고, 새 Beat가 `/live` 실패로
재시작하며 ERROR를 남기던 현상의 근본 수정(PR #308, `b064a5cb`)을 올린 Backend 배포다. 함께 실린 것은 문서 PR #307(`26e77d75`)뿐이다.

## 범위

| PR | 내용 |
|---|---|
| #308 (`b064a5cb`) | Beat 잠금 인계 — 새 Beat는 잠금을 기다리는 동안 `/live`를 실패시키지 않고 WARNING `beat_lock_wait_long`만 남긴다. 옛 Beat는 새 Beat의 대기 공지를 보면 물러난다(`SUPERSEDED_TAKEOVER_SECONDS=60`) |
| #307 (`26e77d75`) | 11-13·11-15 배포 기록 정정(문서만) |

## 실행 기록 (2026-11-17 KST)

main `26e77d75`를 `deploy.sh backend`로 api·worker·beat에 배포했다(그날 첫 릴리스, 12시 배치 시간대가 끝난 뒤).

- **1차 시도(12:46~12:51) — 운영 변경 전 실패.** 다시 로그인하면서 gcloud 기본 프로젝트(`core/project`)가 비었고,
  `deploy.sh`의 `gcloud run jobs update reputation-migrate`가 `--project` 없이 그 값을 읽어 `Error parsing [job]`으로 멈췄다.
  이미지 푸시까지만 했고 마이그레이션·서비스는 바뀌지 않았다. 로그 `.omo/rollback-beat/backend/deploy-attempt1.log`.
- **2차 시도(12:51~13:05) — 성공.** `CLOUDSDK_CORE_PROJECT=mso-platform-481505`를 더해 같은 릴리스로 다시 실행했다
  (`deploy.sh`가 같은 릴리스의 오늘 재시도로 인정).

| 서비스 | 배포 전 | 배포 후 |
|---|---|---|
| api | `00291-gkq` | `00292-j45` |
| worker | `00283-msm` | `00284-kjf` |
| beat | `00278-q9d` | `00279-szw` |

Site `00152-s4v`·Admin `00123-p24`·DB head `0095` 그대로(새 마이그레이션 없음). 이미지 `reputation:20261117-125152`.
롤백 좌표 `.omo/rollback-beat/backend`(배포 전 리비전·`.deploy-rollback`·배포 로그 두 개).

### 확인 (13:05~ KST)

- 마이그레이션 Job·RedBeat 저장 스케줄 정합성 복구·작업 큐 6개 준비 확인·readiness Job(`reputation-production-readiness-lqpbf`) 성공.
- 배포 뒤 공개 표면 확인 통과(배포 전 정상 병원 13곳 기준), `/api/v1/health/live` release `26e77d75`.
- Beat: 예상대로 이번이 #308을 싣는 첫 배포라 옛 Beat `00278`에 물러나는 코드가 없어 잠금을 계속 쥐고 보낸다(13:00 이후 INFO 61줄).
  새 Beat `00279`는 13:00:31에 WARNING `beat_lock_wait_long waited_seconds=300` 한 줄만 남기고 `/live` 실패·ERROR 없이 기다린다.
- 13:06까지 reputation 서비스의 ERROR 0건(다른 서비스 `kakaotalk-server`의 인증 오류 1건은 무관). 13:04의 WARNING 묶음은 외부 스캐너의 경로 탐색 404(`litellm.yaml`·`settings.php` 등, Site를 거쳐 api `00291`에 도달)다.

## 2차 — Site (14:01~14:03 KST, PR #312)

마케팅팀 11-17 요청(365서귀포삼성정형외과 공개 페이지, 공통 항목)을 고친 main `116e6cb0`(PR #312)을 `deploy.sh site`로 배포했다(Site는 배치 시간대 제한 없음).
**Site `00152-s4v` → `00153-tnp`**, Backend·Admin·DB 그대로. `deploy.sh site`가 읽는 `.env.production`은 운영 `reputation-api` env를 `deploy_env.py render`로 만들어 쓰고 배포 뒤 지웠다.
롤백 좌표 `.omo/rollback-sitecopy/site`.

- 주간 진료시간 '오늘 화요일' → '11월 17일 화요일'. 첫 화면 '오늘 진료'·전화 버튼·'다음 진료'와 함께 브라우저에서 한국 시간으로 다시 잰다(캐시된 HTML이 지난 요일을 보이지 않게).
- 문장 속 지역은 앞의 대표 지역만(`제주 서귀포시 법환동`), 태그 전체는 목록·구조화 데이터에만.
- 첫 화면 주말 칸 `토·일요일 진료`(토·일 같은 시간), 소제목 '의료진 소개', 진료 영역 페이지 안내문, 대중교통 기본 안내의 '지하철역' 제거, 진료 원칙 공통 기본 문구 교체.

확인: 공개 13곳 모두 새 지역 표기·주말 칸·'11월 17일 화요일'·'의료진 소개'·새 진료 원칙 문구(365서귀포·마포·행복드림은 `토·일요일 진료`, 노원탑365는 `휴무일 연중무휴`). 365서귀포 `/treatments` 새 안내문, `/visit` 새 대중교통 안내·'지하철역' 0건.
새 리비전 ERROR 0건. 사진 미리 채우기는 배포 직후 자동으로 돌기 시작했다(`.omo/warmup/site-images-20261117T050257Z.log`).

병원별 진료 원칙·주차·대중교통 입력칸(PR #313, 마이그레이션 0096)은 아직 병합·배포하지 않았다.

Beat 인계 확인: 옛 Beat `00278`은 14:55:12 KST에 멈췄고 새 Beat `00279`가 14:55:13에 잠금을 넘겨받았다(그 사이 `/live` 재시작 ERROR 없음).

## 3차 — 전체 배포 시도 (15:07~15:30 KST) — readiness 실패·자동 롤백

main `9d548ce1`(PR #313 병원별 진료 원칙·주차·대중교통 칸·마이그레이션 0096 + PR #314 콘텐츠 방향 질문 대조의 기호 무시 — 'PDRN 주사'가
'유전자(DNA/PDRN) 주사'에 맞음)을 `deploy.sh all`로 배포했다(`--override-reason`). 마이그레이션 0096과 Worker·Beat 교체까지는 됐지만 readiness Job이
15분 동안 `queue_canaries_current=false`(보고서·무료 진단 큐)로 끝나 `deploy.sh`가 5개 서비스 트래픽을 배포 전 리비전(api `00292`, worker `00284`,
beat `00279`, site `00153`, admin `00123`)으로 되돌렸다. DB는 0096으로 남았다(추가형 nullable 칸 셋이라 이전 코드와 함께 돌아도 문제없음). ERROR 0.

원인: 큐 점검(canary)은 Beat가 5분마다 큐마다 하나씩 보내고, 받은 Worker의 릴리스 키에만 기록된다. 교체된 옛 Worker `00284`가 Cloud Run에서 약 2시간
남아 같은 7개 큐를 계속 받는데, 두 큐의 점검 세 번을 모두 옛 Worker가 가져갔다. 고친 것은 PR #315(`9c51c7a8`) — readiness 대기가 아직 새 릴리스로
확인되지 않은 큐의 점검을 30초마다 Beat와 같은 서명 디스패치로 다시 보낸다(점검은 부수효과 없음, 옛 Worker가 받은 사본은 옛 키에만 쓰임).
같은 PR에서 #313이 새로 만든 main CI 실패 1건(`test_forbidden_impact_scan` fixture에 0096 칸 없음)을 고쳤다. main CI의 나머지 16건과 보안·Terraform·리허설
실패는 11-14부터 main·모든 브랜치에서 같은 모습이다.

## 4차 — 전체 재배포 (18:58~19:07 KST) — 성공

main `9c51c7a8`(#313·#314·#315)을 `deploy.sh all --unpin --override-reason=…`로 배포했다(3차 자동 롤백이 트래픽을 리비전에 고정해 `--unpin`).
배포 전 `.env.production`은 운영 API env로 만들고 배포 뒤 지웠다. 롤백 좌표 `.omo/rollback-nudge/all`(배포 전 리비전·배포 로그).

| 서비스 | 배포 전 | 배포 후 |
|---|---|---|
| api | `00292-j45` | `00293-l9j` |
| worker | `00284-kjf` | `00286-kq5` |
| beat | `00279-szw` | `00281-js6` |
| site | `00153-tnp` | `00154-7bp` |
| admin | `00123-p24` | `00124-9mv` |

5개 서비스 모두 최신 리비전 100%(고정 해제). DB head `0096_add_hospital_page_copy`. 이미지 `20261117-185845`.

- readiness Job `reputation-production-readiness-h28dt` 약 2분 30초 만에 성공 — 대기 중 점검 재발송 5회(`canary_nudges=5`).
- 배포 뒤 공개 표면 확인 통과, `/api/v1/health/live` release `9c51c7a8`.
- Beat 인계(#308의 두 번째 배포): 옛 Beat `00279`가 19:03:00에 `beat_superseded releasing_lock=true`로 물러나고 새 Beat `00281`이 19:03:04에 잠금을 잡았다(4초).
- 19:07까지 새 리비전 ERROR 0건.
- 공개 API에 `care_principles`·`parking_note`·`transit_note`가 실린다(365서귀포는 아직 저장 전이라 모두 `null` — 사이트는 공통 기본 문구를 쓴다). 페이지 200.

## 남은 것

- 365서귀포 진료 원칙·주차·대중교통 문구 입력(마케터가 병원 정보 > 브랜드의 '병원 페이지 문구'에서) — 로그인한 Admin에서 그 칸의 저장·거절 화면은 아직 확인하지 않았다.
- 마포성모탑 콘텐츠 방향 'PDRN 주사'가 실제 측정 질문과 맞는지 Admin에서 입력해 확인한다(운영 질문 목록은 읽지 않았다).
- `deploy.sh`의 Cloud Run Job `update` 폴백 세 곳(migrate·redbeat-reconcile·production-readiness)은 `--project`를 넘기지 않아 gcloud 기본 프로젝트가 비면 실패한다.
  배포 때 `CLOUDSDK_CORE_PROJECT`(또는 `gcloud config set project`)를 함께 준다. 스크립트 수정은 하지 않았다.
