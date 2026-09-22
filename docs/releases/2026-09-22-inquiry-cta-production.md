# 2026-09-22 — 보고서 재생성 개방과 도입문의 CTA 전환 운영 배포

## 배포 기준

PR #141의 main 병합 코드 `8f40f49590345ccd333ff85d786d9fcfe41848f2`를 운영 5개
서비스에 배포했다. 이 문서를 추가한 커밋은 배포 기록이며 런타임 이미지의 소스 SHA가 아니다.

- PR CI 10/10 통과(`rehearsal` 포함). 배포 소스는 병합 커밋 `8f40f49`이며,
  워크트리를 그 커밋으로 detach한 뒤 `scripts/deploy.sh all`을 실행했다.
- 이미지 태그 `20260922-174108` 3종 모두 같은 소스에서 빌드했다.
- **DB head `0079_topic_swap_fallback`, 신규 schema 변경 없음.** 마이그레이션 Job은
  적용할 리비전이 없어 no-op으로 끝났다.
- 로컬 사전 가드: `make db-budget-guard`(75/80), `make copy-guard` 통과.

첫 실행은 `PUBLIC_DOMAIN` 미지정으로 preflight에서 멈췄고 운영에 아무 변경도 없었다.
`PUBLIC_DOMAIN`·`ADMIN_DOMAIN`은 `.env.production`이 아니라 **셸 환경변수**로 넘겨야
한다(`scripts/deploy.sh:113` — `NEXT_PUBLIC_*`가 빌드 시점에 번들로 인라인되기 때문).

## 이미지와 실제 런타임

| 서비스 | 이미지 digest (Artifact Registry `reputation` 저장소) |
|---|---|
| API / Worker / Beat | `sha256:602ca64ddd6ed1bde0ce83104ed3de8ba447a1c6fdb408d94c79f46acc9123bb` |
| Site | `sha256:ab8900f2fc3a81ad69b28c438868aefb3ffb2854810c4c94e98c261b0052981d` |
| Admin | `sha256:187928e868e9eb319cbab9c7fc880d234cc2869bd61538925ac9ad6113dd2718` |

| 서비스 | 배포 전 리비전 | 현재 리비전 |
|---|---|---|
| `reputation-api` | `reputation-api-00196-tl7` | `reputation-api-00197-8vm` |
| `reputation-worker` | `reputation-worker-00185-xvn` | `reputation-worker-00186-6f9` |
| `reputation-beat` | `reputation-beat-00181-mpt` | `reputation-beat-00182-t6q` |
| `reputation-site` | `reputation-site-00132-pdk` | `reputation-site-00133-j7l` |
| `reputation-admin` | `reputation-admin-integrity-a35b7f2` | `reputation-admin-00091-wbc` |

5개 서비스 모두 Ready이고 트래픽이 위 리비전에 있으며, 배포된 image가 위 태그와
일치함을 확인했다. Admin 리비전 이름이 suffix 방식(`integrity-a35b7f2`)에서 자동
번호(`00091-wbc`)로 바뀐 것은 이번 실행이 suffix를 지정하지 않았기 때문이다.
환경·secret 참조는 `--env-vars-file` 경로의 기존 계약대로 보존했다.

배포 순서는 runbook과 같다 — 롤백 좌표 기록 → 이미지 3종 빌드·푸시 → 마이그레이션 →
Worker → RedBeat 재조정 → Beat → production readiness gate → API → Site → Admin.

## 운영 사후 검증

- **작업 큐**: `reputation-production-readiness-scd9d` 실행 성공. 현재 릴리스의
  7개 큐 canary 준비 확인. RedBeat 저장 스케줄 정합성 복구 완료.
- **공개 병원 9곳**: `https://{slug}.reputation.motionlabs.kr/.well-known/reputation-health`
  전부 200이고 `slug` 일치, `release`는 모두 `reputation-site-00133-j7l`이다.
  대상은 `singihansognaegwayeonhabyiweon`, `jangpyeonhanoegwayiweon`,
  `noweontab365yiweon`, `yeonsesogsiweonnaegwayiweon`,
  `maposeongmotabjeonghyeongoegwa`, `gangsimjangnaegwayiweon`,
  `jangaengimdeonaeunsognaegwayiweon`, `haengbogdeurimyiweon`,
  `seoulwnaegwayiweon-wiryejeom` 9곳이다.
- **랜딩 CTA 전환**: `href="#lead"` 앵커 4개(헤더 `도입 문의` + 히어로·최종 밴드·모바일
  고정 바의 `도입 문의하기`), 도입문의 폼 입력 9개(`lead-specialty`·`lead-regionKeyword`·
  `lead-coreKeywords` 포함), 선착순 자리 카운터 제거 확인. 스타일시트 2개(1,509 rules)가
  로드되고 CTA 밴드 배경 `rgb(0,67,127)`·제출 버튼 `rgb(6,113,224)`로 계산된다.
  히어로 문구는 "문의를 남기시면 … 정리해 연락드립니다"로 교체됐다.
- **크롤러 표면**: `robots.txt`·`llms.txt`·`sitemap.xml` 200.
- **Admin**: `/` 307 → `/login` 200.
- **공개 API**: `/api/v1/public/hospitals` 200, 병원 9건으로 배포 전과 같다.
- **로그**: 새 리비전 5개 모두 `severity>=ERROR` 0건(배포 후 25분 창).

## 이번 배포가 바꾼 계약

- 월간 보고서 재생성이 실패 복구 전용이 아니게 됐다. 정상 완료 상태에도 열리고,
  보고서 목록 행마다 사유를 받아 실행한다. 전달 완료본도 대상이며 새 버전은 이전
  보고서·전달 기록을 보존한다. 초기 진단(V0)에는 이 버튼이 없다.
- 리드 진단 재생성이 `READY`까지 열렸다. 대상은 `delivery_status`가 `PENDING`·`INTERNAL`인
  건뿐이다. 고객에게 나간 건은 공개 토큰 뷰가 최신 버전을 서빙하므로 제외했고
  DB 제약 `ck_lead_diagnoses_delivery_requires_report`도 같은 선을 긋는다.
- 랜딩의 모든 CTA가 도입문의 폼(`#lead`)을 가리킨다. 폼은 진료과·지역·핵심 키워드를
  필수로 받고 프록시가 이를 백엔드로 전달한다. 셀프서브 무료 진단(`/ai-diagnosis`)은
  유지하되 랜딩 CTA에서 분리했고 푸터 링크로만 간다.

## 검증하지 않은 범위

- **도입문의 성공 경로를 끝까지 돌려보지 않았다.** 폼 제출 → INTERNAL 초도 진단 자동
  생성(`DIAGNOSIS_NOTE_QUEUED`) → 원장 안내 SMS(NHN) → Slack `[Lead : 도입 문의]`
  한 줄까지의 실제 1건 검증이 남아 있다. 실제 문의가 들어올 때 Slack 한 줄과 Admin
  상담 요청의 콜용 보고서 상태로 확인한다. 실패해도 리드 접수 자체는 되돌아가지 않는다.
- 재생성 버튼의 운영 실사용(월간 목록 행 1건, 리드 콜용 보고서 1건)은 확인하지 않았다.
- 전체 사용자 E2E와 주기 관측(야간 생성·아침 발행)은 이 배포의 범위 밖이다.

## 롤백

`.deploy-rollback`에 배포 직전 5개 리비전을 기록했다(위 표의 '배포 전 리비전').

```bash
bash scripts/deploy.sh rollback
```

트래픽만 되돌린다. 이번 배포는 schema를 바꾸지 않았으므로 DB 조치는 필요 없다.
