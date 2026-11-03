# GEO 노출 보완 — 코드 변경과 사람이 할 일

문서 버전 1.0 · 작성일 2026-11-03 (Asia/Seoul) · 브랜치 `claude/geo-visibility-improvements`
상태: 로컬 구현·검증만 끝났다. 운영 배포 전이다. DB head가 `0095_add_google_bing_site_verification`으로 바뀐다(추가형).

## 배경

2026-11-03 read-only 점검에서 노출의 병목은 발행 다음 단계인 색인이었다. 근거는 두 가지다.

- 2026-07-29 운영 측정(`backend/app/services/indexnow.py` docstring): AI 답변이 병원 허브를 인용한 경우 93.3%(14/15)가 병원을 언급했다. 인용하지 않은 경우는 4.1%(6/145)였다.
- 같은 병원의 대장내시경 글 7편은 85회 측정 동안 인용 0회였다.

외부 근거도 같은 방향이다.

- Google AI 개요와 AI 모드는 Google 색인에 있고 스니펫이 허용된 페이지만 쓴다(Google 문서).
- 네이버 블로그 robots.txt는 OAI-SearchBot과 Google-Extended를 차단한다(2026-11-03 직접 조회). 병원의 네이버 블로그는 ChatGPT 검색과 Gemini 앱 그라운딩에 보이지 않는다.

이 변경은 다섯 가지를 다룬다. 색인 신호를 한 곳에 모으고, 색인 상태를 사람이 확인할 수 있게 하고, AI가 읽은 출처를 보이게 하고, 지역 추천형 질문에 답하는 병원 소유 문장을 만들고, 측정 위치를 선택할 수 있게 한다.

## 코드 변경

### Site

1. 정본 호스트 영구 이동(`site/proxy.ts`, `site/lib/host-routing.ts`)
   - 플랫폼 호스트의 `/{slug}/...`는 병원 정본 호스트로 308 이동한다. 정본은 자기 도메인이 있으면 그 도메인, 없으면 관리형 서브도메인이다.
   - 관리형 서브도메인은 병원이 자기 도메인으로 공개 중일 때 그 도메인으로 308 이동한다.
   - 예전에는 canonical 태그만 정본을 가리키고 200으로 응답해 같은 글이 두 호스트에 색인 후보로 남았다.
   - 바뀌지 않는 것: 예약 경로(`/.well-known`·`/api`·`robots.txt`·`sitemap.xml`·정적 파일), 플랫폼 최상위 라우트, 로컬과 run.app 호스트, 역조회 404·장애 때의 동작.
   - 질의 문자열은 유지한다.
   - 끄는 방법: Site env `SITE_CANONICAL_HOST_REDIRECT=0`.
2. AI 크롤러·AI 유입 구조화 로그(`site/lib/ai-traffic.ts`)
   - 남기는 로그: `jsonPayload.event="ai_crawler_hit"`(OAI-SearchBot·Googlebot·Bingbot·Yeti·Daumoa 등)와 `"ai_referral_visit"`(chatgpt·gemini·perplexity·copilot·claude).
   - 기록 항목은 호스트·경로뿐이다. 질의·IP·쿠키는 남기지 않는다.
   - User-Agent는 위조할 수 있으므로 지표로만 쓴다.
3. 진료 항목 페이지 "진료 기본 정보"(`site/lib/treatment-answer.ts`)
   - 승인된 병원 정보로만 결정적으로 만든다. 들어가는 정보는 위치, 담당 의료진·자격, 전화, 진료시간 링크다.
   - 측정 질문("{지역}에서 {진료} 받을 수 있는 병원")에 바로 답하는 병원 소유 문장이다.
   - 비교·우위·추천 표현은 만들지 않는다.
4. 구조화 데이터·llms.txt 정합성
   - 글의 MedicalProcedure performer `@id`를 원장 페이지 노드와 같게 맞췄다.
   - 허브와 방문 안내의 `#clinic` sameAs를 한 함수로 통일했다(`buildClinicSameAs`).
   - 병원 llms.txt에 Google 비즈니스 프로필·네이버 플레이스·블로그·카카오 채널 URL을 추가했다.
5. 병원별 Google·Bing 소유 확인 메타
   - `google-site-verification`은 플랫폼 공용 값과 병원 값을 함께 싣는다.
   - `msvalidate.01`도 싣는다.

### Backend·Admin

6. 마이그레이션 `0095_add_google_bing_site_verification`
   - `hospitals.google_site_verification`·`bing_site_verification`(String 255, nullable) 컬럼을 추가한다. 추가형이고 시드는 없다.
   - Admin 병원 정보의 "검색엔진 소유 확인"에 두 입력이 추가됐다. meta 태그 전체를 붙여 넣으면 content 값만 저장한다.
   - 프로필 출처 원장에는 남기지 않는다.
7. 인용 도메인 지형(`backend/app/services/citation_landscape.py`)
   - 성공 측정 답변의 인용 도메인을 답변 단위로 센다.
   - `COMPETITOR_VISIBILITY`·`SOURCE_SIGNAL_GAP` 행동의 근거(`evidence.citation_landscape`)와 문구에 "병원 없이 경쟁 병원만 언급된 답변에서 AI가 읽은 출처"를 붙인다.
   - 관측 집계이며 인과를 말하지 않는다.
8. 운영 조회 `ops_sql_report`(읽기 전용)
   - `q17`: 최근 35일 인용 도메인. 서비스 전체 상위 40개와 병원별 상위 5개를 보이고, `competitor_only`를 함께 싣는다.
   - `q18`: 최근 90일 처음 공개된 글의 참고자료 부착률(병원·유형별).
   - `q19`: 서로 다른 병원이 같은 정규화 제목으로 공개 중인 글. 기록만 하고 차단·사고·Slack을 만들지 않는다.
9. 측정 검색 위치(`SOV_WEB_SEARCH_USER_LOCATION_COUNTRY`, 기본 빈 값 = 끔)
   - 켜면 OpenRouter `openrouter:web_search`에 `user_location`(approximate·국가·시간대)을 넘긴다.
   - 측정 정책 스냅샷에 `web_search_user_location`이 생겨 그 뒤 달은 이전 달과 비교되지 않는다.
   - 끈 상태의 정책 스냅샷·지문·캐시 키는 이 변경 전과 같다.

## 검증

- Site: `npm run typecheck`, `npm test` 482/482, `npm run lint` 통과.
- Admin: `npm run typecheck`, `npm test` 662/662, `npm run lint` 통과.
- Backend 관련 테스트(마이그레이션 체인·ORM drift·0094 업그레이드·Admin 프로필·SoV 엔진·노출 행동·ops 조회·신규 테스트)는 통과했다. 로컬 Postgres 16 UTF-8 클러스터 3개와 Redis, CI env로 돌렸다.
- Backend 전체 suite: 9,632건 중 9,617 통과, 15 실패.
  - 15건 모두 main에서도 같은 테스트가 똑같이 실패한다. 월간 재측정 잠금, 월간 측정 기한, 운영 채널 신호, 비용 정산 등 이번 변경과 무관한 영역이다.
  - 이번 변경으로 깨졌던 1건(`tests/integration/test_ops_sql_report_postgres.py`, q17~q19 목록)은 고쳤다.
- ruff는 사전 존재 1건(`tests/test_monthly_sov_postgres.py` 미사용 import) 외에 오류가 없다.
- 운영 배포, 실제 리다이렉트 응답, 실제 로그 수집은 확인하지 않았다.

## 배포 시 주의

- Backend 마이그레이션 → API·Worker·Beat → Admin → Site 순서다. Site가 먼저 나가도 새 필드가 없으면 메타를 생략하므로 깨지지 않는다.
- Site 배포 직후 확인 항목:
  - `curl -sI https://reputation.motionlabs.kr/{slug}/contents`가 308로 `https://{slug}.reputation.motionlabs.kr/contents`를 가리키는지
  - `/.well-known/reputation-health`가 그대로 200인지
- 리다이렉트가 문제를 일으키면 Site env `SITE_CANONICAL_HOST_REDIRECT=0`으로 되돌린다. 308은 브라우저·크롤러가 캐시할 수 있으므로 끄더라도 한동안 이동이 남을 수 있다.
- `SOV_WEB_SEARCH_USER_LOCATION_COUNTRY`는 이번 배포에서 켜지 않는다. 켜는 것은 아래 대표 결정 사항이다.

## 사람이 해야 하는 일

### 즉시(계정·권한이 필요해 코드로 할 수 없는 일)

1. Google Search Console에 플랫폼 도메인 속성을 DNS로 등록한다(`motionlabs.kr` 도메인 속성이면 모든 `{slug}.reputation.motionlabs.kr`이 함께 들어온다). 병원 서브도메인별 sitemap(`https://{slug}.reputation.motionlabs.kr/sitemap.xml`)을 제출하고, 색인 생성 보고서에서 "발견됨 - 현재 색인이 생성되지 않음" 비율을 병원별로 기록한다.
2. 자기 도메인 병원은 도메인마다 Search Console URL 접두어 속성을 만들고, HTML 태그 방식의 content 값을 Admin 병원 정보 → 검색엔진 소유 확인 → "Google Search Console 확인 코드"에 저장한 뒤 확인을 누른다.
3. Bing Webmaster Tools에 같은 속성을 등록한다. Search Console 가져오기를 쓰면 병원별 코드가 필요 없고, 그렇지 않으면 "Bing Webmaster Tools 확인 코드"를 저장한다. IndexNow 제출 현황도 같은 화면에서 확인한다.
4. 네이버 서치어드바이저·다음 웹마스터에 자기 도메인 병원을 등록한다(기존 0093 경로).
5. Cloud Logging에 `jsonPayload.event="ai_crawler_hit"`와 `"ai_referral_visit"` 기준 로그 기반 측정항목 또는 저장된 쿼리를 만든다. 호스트별 주간 OAI-SearchBot·Googlebot 방문 수를 색인 선행 지표로 본다.

### 대표 결정이 필요한 일

6. 측정 위치를 켤지 정한다(`SOV_WEB_SEARCH_USER_LOCATION_COUNTRY=KR`). 켜면 그 달부터 전월 비교가 끊긴다. 켠다면 월초 새벽 창 밖, 분류 사전 변경처럼 한 달에 묶어서 한다.
7. Gemini 측정 경로를 확인한다. OpenRouter 문서(2026-11 조회)의 Google 네이티브 검색 지원 목록은 Gemini 3 Flash·3 Pro·3.1 Flash/Lite·3.5 Flash이고 `gemini-3.6-flash`는 없었다. 목록에 없는 모델은 Exa 검색으로 대체된다. 실제 경로를 OpenRouter 사용 기록(`server_tool_use_details`)이나 문의로 확인하고, Exa로 돌고 있다면 Gemini 수치가 Google 그라운딩을 재현하지 않는다는 점을 보고서·IR 표현에 반영할지 정한다. 모델 교체는 기준선을 바꾸므로 같은 결정 범위다.
8. 원장 감수 표기 경로를 정한다. 지금 글 화면은 "의료진 직접 감수 표기 아님"을 노출하고 저자는 병원 법인이다. 월 1회 원장 일괄 확인 같은 실제 감수가 있을 때만 `reviewedBy`·감수일을 싣는 경로를 만들지 정한다. 실제 감수 없이 표기하는 것은 허용하지 않는다.
9. 글 URL에 키워드 slug를 넣을지 정한다. 지금은 `/contents/{UUID}`다. 바꾸면 기존 URL 리다이렉트·IndexNow 재제출·인용 귀속(`content_citations`) 수정이 함께 필요하다.
10. 원장 보고서에 원장 몫의 할 일("yours")을 채울지 정한다. Google 비즈니스 프로필 정비나 학회 프로필 갱신처럼 원장만 할 수 있는 일이 대상이다. 원장용 문구 계약(`report_narrative`·`tests/test_report_plain_language.py`)을 함께 바꿔야 한다.

### 정기 운영(월 1회, 월간 보고서 전달 주간)

11. `ops_sql_report --only=q17`로 인용 도메인을 본다. `competitor_only` 상위 도메인 중 병원이 등재될 수 있는 곳을 고른다. 크롤러를 허용하는 디렉터리(예: 모두닥), 학회 회원 프로필, 지역 언론이 후보다. 등재 정보(병원명·주소·전화·진료과)가 사이트와 같은지 확인한다.
12. `q18`로 참고자료 부착률이 낮은 병원·유형을 찾는다. D36은 그대로 두고, 낮은 유형은 수기 목록(`CURATED_MEDICAL_SOURCE_PAGES`) 보강 후보로 넘긴다.
13. `q19`로 여러 병원이 같은 제목을 쓰는 글을 본다. 같은 루트 도메인 아래 반복이 늘면 Google 대량 생성 콘텐츠 정책 위험이 모든 병원에 함께 걸리므로, 병원별 고유 정보를 더 넣는 방향으로 주제 선정을 조정한다.
14. Google 비즈니스 프로필 완성도를 점검한다(카테고리·진료시간·사진·웹사이트 URL을 병원 정본 호스트로). 미국 데이터에서는 AI 모드 지역 답변이 이 프로필을 많이 인용했다. 한국 반영 정도는 확인 중이다.
15. 후기를 늘리는 작업은 하지 않는다. 의료법 제56조(치료경험담 광고 금지)와 제27조(환자유인) 위험이 있다.

## 근거 등급 메모

- 강한 근거:
  - Google AI 기능은 Google 색인을 전제로 한다(Google 문서).
  - OAI-SearchBot을 허용해야 ChatGPT 검색에 노출된다(OpenAI 문서).
  - 네이버 블로그는 위 두 봇을 차단한다(직접 조회).
- 업계 연구:
  - schema·llms.txt의 AI 인용 효과는 유의하지 않았다(Ahrefs 2026-05, SE Ranking 2026-10). 이번 변경은 schema를 늘리지 않고 정합성만 맞췄다.
- 추정:
  - 진료 항목 페이지의 사실 블록이 추천형 질문 인용을 늘린다는 것은 내부 실측(허브 인용 → 언급)에서 나온 가설이다. q17과 월간 owned 인용 지표로 검증한다.
