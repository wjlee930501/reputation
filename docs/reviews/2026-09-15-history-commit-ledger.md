# Re:putation 전체 도달 가능 커밋 로그

검토 시작 기준: origin/main d96bd21, 로컬 개선 4db1b69. 제목 전수 분류이며 모든 diff 줄의 전수 감사는 아니다.

| SHA | Author date | 범위 | 제목 |
|---|---|---|---|
| 1aa98512a3996164d8357a29922f1e06e219ced0 | 2026-02-21T17:18:36+09:00 | main | Initial commit: Re:putation AEO platform v1.0 |
| 49c15aa644ea616cd7dc87e4889df28c2d682133 | 2026-02-21T17:59:32+09:00 | main | fix: codereview_v1 수정 사항 반영 (v1.0 → v1.1) |
| cf80e4b1cb3d64a01f81878b37a486ed124926d9 | 2026-02-21T22:28:34+09:00 | main | feat: AEO-02 E-E-A-T + SoV 쿼리 18개 확장 |
| 756056ab58caa3307ff7c1b9638b1c9768585523 | 2026-02-21T23:07:38+09:00 | main | docs: Re:putation 서비스 가이드 문서 추가 (v1.1) |
| b63fdc48550d57b0101b32b45432bf336e86be2d | 2026-02-21T23:21:57+09:00 | main | test: 종합 테스트 스크립트 추가 + .env.example 정리 |
| ee114ffe4e198f78d89c5249b21721d96e007694 | 2026-02-21T23:51:55+09:00 | main | feat: replace Perplexity with Gemini for SoV measurement |
| 90ff3b61e9d46ac256c2192692db874894685ee2 | 2026-02-22T00:17:36+09:00 | main | fix: share sites_data volume between api and worker containers |
| 6cf34b5979820e7c597b8580aac042f2f962a350 | 2026-02-22T01:14:00+09:00 | main | fix(e2e): split hospital create and profile PATCH in test |
| 60ba1413edd50543028d9ef8ea392f65264aff39 | 2026-02-22T01:56:04+09:00 | main | fix(worker): isolate image generation failure from text content commit |
| 6d622b340c8180e8ad2aece4be04d13cf0f8f9b8 | 2026-02-22T04:19:16+09:00 | main | feat: PDF download endpoint, next-month slot auto-generation, Gemini unification |
| a2144ef1ea78d0b523185e07b7690116d5f9152d | 2026-03-12T17:24:18+09:00 | main | fix: system-wide security hardening, code quality, and operational reliability |
| da3f5b202e363a4b22f5733e60ec6e2992702bdd | 2026-03-18T00:24:53+09:00 | main | feat: v2 system-wide improvement — 3-team sprint with QA fixes |
| 98dc41d2486f03e8e0520953a709e8a88e488d94 | 2026-03-18T15:56:01+09:00 | main | fix: pre-launch refiner — P0/P1 production risk elimination |
| d77bd18e7587ed03a067e83947699e6840055fd1 | 2026-03-30T13:34:13+09:00 | main | fix: production-readiness hardening — security, reliability, performance |
| 00e0bfe4d24d1a1963e403d31f1e0b881b730187 | 2026-05-04T01:35:18+09:00 | main | feat: add AI webblog operations workflow |
| ef972de0e19884d9f8cee4e0c8a9b1cb11628390 | 2026-05-04T02:17:33+09:00 | main | Prepare service for production deployment |
| e02301b3228ce34c0fae9266aec3489f8086e6e6 | 2026-05-04T15:18:48+09:00 | main | feat: polish Re:putation operator workflow and terminology |
| f448c64f4db90afae5648d53e776259166c024a1 | 2026-05-05T00:55:05+09:00 | main | fix: wash exposure work queue terminology |
| 8d4cf6db3455ef79ca51d0cec3d02ffc81fa6e5a | 2026-05-05T03:21:44+09:00 | main | fix: wash operator-facing AI terminology |
| 3374098bf7358c4f26420466333b48ac4409ad4b | 2026-05-05T03:55:20+09:00 | main | fix: wash content review operator terminology |
| 15a2d456658359be7d9b9caa61efb296511f022d | 2026-05-05T04:27:25+09:00 | main | fix: wash content guide API wording |
| 903312eca0dd67e927e9a5e43e90eb75b97b31e3 | 2026-05-05T05:02:38+09:00 | main | fix: wash stale exposure action copy |
| 776fb132f12cfe508d20c17ef540cf957c52dd1d | 2026-05-05T05:38:58+09:00 | main | fix: wash content guide operator copy |
| d0c3c80560fda5a9ac98b60cff968ae706b20387 | 2026-05-05T06:48:49+09:00 | main | fix: wash essence operator API terminology |
| da0a5a17a49cc5050470de243eb2720605e9bc56 | 2026-05-05T07:59:09+09:00 | main | fix: wash essence operator UI terminology |
| d127b633eac1b2316e0406b5d5e34d019195e5d2 | 2026-05-05T08:36:18+09:00 | main | fix: wash essence draft evidence details |
| 888d29d9611cb12da7db9c79946fc0921561bfec | 2026-05-05T09:45:21+09:00 | main | fix: collapse content guide advanced editor |
| b670fb0ba80ceccb7eb2ad3b9d134d78bac61d23 | 2026-05-05T09:47:22+09:00 | main | fix: wash patient question operator terms |
| 9d8cbffe987a0918b75101fc1d7e3ba960eb5e72 | 2026-05-05T10:21:45+09:00 | main | fix: wash AI service target label |
| 92227a38a50cfc2234025efee0d9bb06bd7dfed2 | 2026-05-05T10:54:59+09:00 | main | fix: wash dashboard readiness terminology |
| cb87cadb746fba9aab43361d13dcad70f746f905 | 2026-05-05T12:48:48+09:00 | main | fix: align public site exposure policy |
| 3028043144a1974e3f6f86959f8ec7e5b7a9673a | 2026-05-05T21:49:36+09:00 | main | fix: enforce essence and publish actor contracts |
| a105f04e18b56c932f1f8895d5b27bd390c72f37 | 2026-05-05T21:49:36+09:00 | main | feat: expose platform-level AI mention results |
| dbf521a9be85c3ed2aafde9896048299e0a22702 | 2026-05-05T21:49:36+09:00 | main | feat: strengthen report and demo essence review contracts |
| 2cdfeb82ac0622087e4c589e72dfe923a1e7d2f4 | 2026-05-05T22:14:24+09:00 | main | fix: refocus public exposure wording |
| 34f31e30eb1f702ae3144df401c7138d3bed664e | 2026-05-05T22:27:58+09:00 | main | fix: surface owner-ready admin summaries |
| 51b94a207c54433a7e58ce62fad1c94f7a280874 | 2026-05-05T22:31:37+09:00 | main | fix: clarify report review empty states |
| 0cda222e33c1ad549d535d3a36ec7204e14c7683 | 2026-05-05T22:42:47+09:00 | main | test: add demo seed runtime smoke |
| 55df0ceb193ecb9924a196e977da4ceb9d06b63d | 2026-05-05T22:52:40+09:00 | main | fix: expose action display labels |
| f267e9fec11555cb68478e86e5373cdc46d94894 | 2026-05-05T23:02:07+09:00 | main | fix: expose action evidence display summaries |
| 312e57be3fc850bb77952095ae61ca54dcc1db78 | 2026-05-05T23:06:37+09:00 | main | fix: expose content display labels |
| 9ccb26b13588eecd2cf4a73583ab9741fee1f1f9 | 2026-05-05T23:25:01+09:00 | main | fix: expose query and sov display labels |
| 5da5c9e1b6955bf99f1e91f5f680bd2aecafd38e | 2026-05-05T23:42:29+09:00 | main | fix: expose essence display labels |
| 9e5636f12b53704793d722518e6c51cb0b56c24b | 2026-05-05T23:49:27+09:00 | main | fix: expose report display labels |
| f1eb120703709304e8c6e8821f7f6e2527214d56 | 2026-05-05T23:52:51+09:00 | main | fix: expose dashboard readiness labels |
| de5070d578aa90149fbeb3823920a1a42de2e06c | 2026-05-06T00:03:50+09:00 | main | fix: reduce dashboard washing dependency |
| b6dfd99c650d451ed715e833c261bb10b63cc634 | 2026-05-06T00:13:45+09:00 | main | fix: define 1.0 scope and clean admin copy |
| b145a70038b050adb32a448e517d49fb636f4d71 | 2026-05-06T00:47:12+09:00 | main | fix: polish admin browser qa copy |
| 9d226e79da3a0e4f25f841c10914319a73944709 | 2026-05-06T00:55:52+09:00 | main | feat: add reputation lead landing page |
| 349af6f67f9bd1d9ef08cb3425f59615866b5277 | 2026-05-06T01:50:31+09:00 | main | fix: refocus landing page narrative |
| 90522c560fd931ba5106ae2dfcb275be56e7f241 | 2026-05-06T02:07:08+09:00 | main | fix: apply crisp landing visual system |
| d229de9a28aae8ae213a812cfc13df57a9107c5d | 2026-05-06T02:20:51+09:00 | main | fix: tune landing typography rhythm |
| 276e1fe7cdd3bc475015cc219c2233d67aad4ad8 | 2026-05-06T02:45:05+09:00 | main | fix: redesign landing for clinic diagnosis |
| c82124cc9185bb7c5bc85116ab40271fb17c5d11 | 2026-05-06T13:56:42+09:00 | main | feat: redesign reputation landing page |
| 2440e7b8d41418c7d6a52fffe6eef50704bb9fd0 | 2026-05-08T19:31:35+09:00 | main | feat: ship 1.0 MVP with sales leads, audit, compliance hardening |
| 062b5dadb9c2feb160822ed7efc18dacb5aff216 | 2026-05-08T19:39:07+09:00 | main | feat: tone-match Re:putation to MotionLabs Research Preview |
| 24401b15005081acce17846a69c302f960f5f84b | 2026-05-08T19:52:32+09:00 | main | fix: research preview launch hotfixes (audit + llms + privacy + ops) |
| 4ee566cd641ed2c9c2c8ca7c2e072667a0ddf09e | 2026-05-08T20:07:33+09:00 | main | feat: clinic public surface — IA, design system, components |
| b2d284b7a47d15c6d032d0a319b46a70a76de1f4 | 2026-05-08T20:23:29+09:00 | main | fix: smooth first-time dev setup (3 boot blockers) |
| ea6aac18f2046abc85ab21c4cbb6b4c6d16a770d | 2026-05-08T20:42:37+09:00 | main | feat: clinic surface as content hub — visual depth + IA reframe |
| 42d3e3e3152753b4497cd7bfeb77737351208e93 | 2026-05-08T21:33:40+09:00 | main | feat: clinic article — TL;DR + reading time + references + PAA (PR-B Phase 1) |
| 537309ec8ac4ec6651a1e0e7c861da1a3218618c | 2026-05-08T21:44:59+09:00 | main | feat: GEO/AEO schema 분기 + 측정 정합 + revalidate (PR-B Phase 2+3) |
| d76c64d58ab4da52a5b5468dfc5233ddf1aa8565 | 2026-05-08T21:51:50+09:00 | main | feat: clinic /doctor /treatments /visit — nav 풀 페이지 3종 |
| 39b1a0fc8d10300f80d3e6350a07a835ff7d1863 | 2026-05-08T22:06:27+09:00 | main | feat: mobile optimization across all clinic + landing + legal pages |
| ca7bdcc96a27c3deafdf9318b60dc2cef3542341 | 2026-05-09T00:52:01+09:00 | main | feat: Phase A — asset 인입 인프라 (file storage + URL crawl + 추출) |
| 9bf9fcb8038c2672af213f6496a005145a77b0c6 | 2026-05-09T00:55:54+09:00 | main | feat: Phase B — AE 온보딩 wizard 페이지 (5단계 한 화면) |
| 93da489199ee8c3850cc271ef38849a5bdb3bff5 | 2026-05-09T01:06:45+09:00 | main | feat: Phase C — Wiki view + 사진 자동 매핑 + /site 갤러리 |
| f23e72d1136ce5dec0b1048cd08befc93ef9adf3 | 2026-05-09T01:10:53+09:00 | main | test: AE 자산 인입 → wiki 분류 파이프라인 회귀 테스트 (+23 cases) |
| bfeb26f36d72fb4cb5b8a9760d2400073754f954 | 2026-05-09T01:24:18+09:00 | main | fix(admin): /process 실패를 alert 대신 인라인 표시 + ERROR 자료 재시도 UX |
| 2f9f47e64ca009315711b2ee81c7c1320b31a5aa | 2026-05-09T01:25:07+09:00 | main | fix(admin): wiki 사진 공개 토글 실패를 alert 대신 인라인 표시 |
| 2b0e7a7e262996efefed63c583c68fbdd01b9f2e | 2026-05-09T01:28:18+09:00 | main | feat(admin): 자료 '제외' 액션을 온보딩 wizard에 노출 + 백엔드 audit 일관성 |
| 94f016e47abb9be821e1ed25bd1d41788a8d0d1a | 2026-05-09T01:29:40+09:00 | main | fix(public): EXCLUDED 상태 사진이 /site에 잔존하는 leak 차단 |
| 37c4ac1bfe96f4eafdccc94a7bd77c16ab807d08 | 2026-05-09T01:32:53+09:00 | main | fix(admin): 자산 인입/제외/공개 토글 audit 액션을 dashboard에서 한글 라벨로 노출 |
| 1aa7ae451b008a6b0fb9949232854dd64b2ae47c | 2026-05-09T01:39:13+09:00 | main | chore: 데모 seed 사진 + tsc 호환 + gitignore 정리 |
| 5d841947f079155fe560a7ab474c7b8450d50092 | 2026-05-09T01:58:30+09:00 | main | feat(site): editorial-medical-trust 비주얼 리톤 (clinic-* CSS only) |
| f9f95059546426ac48a0fc72c62742199b0ff932 | 2026-05-09T02:02:28+09:00 | main | feat(site): 콘텐츠 본문 페이지 editorial-article 톤 정렬 |
| 9f131b996416adb40b35bd665849332223d135b8 | 2026-05-09T12:49:22+09:00 | main | feat(site): /contents listing /treatments /visit 등 sub-page editorial 톤 정렬 |
| e4a1a2423ff2793a96e222b227c64e2263c515a2 | 2026-05-09T13:43:31+09:00 | main | Stop versioning TypeScript build cache |
| f507c513262774d356ad2ab443db42a151db0f7b | 2026-05-09T20:15:23+09:00 | main | fix(site): 헤더 컨텐츠를 본문 컨테이너(1080px)와 좌우 정렬 |
| 9879ddec5e1097bd1a64f3045e1b87ed499bb731 | 2026-05-09T20:18:03+09:00 | main | fix(site): 헤더 max-width를 1128(=1080+padding)로 보정해 본문과 정확히 정렬 |
| 587f074a30de55b55adffd56e472247a63a333ca | 2026-05-10T23:02:46+09:00 | main | Harden public asset and cache boundaries |
| 9bda3fb5ec76a71dacb30436e23f89c98e0c13bc | 2026-05-11T01:04:07+09:00 | main | Make hospital onboarding E2E usable end to end |
| 79bb323cf2a6c6f018907408a3e287bad9a56b2a | 2026-05-12T12:47:41+09:00 | main | Refine clinic-facing identity for AI-era trust |
| dc9c0b7be386d8e7b0335ee2ba229c0882cdcd0f | 2026-05-13T05:15:38+09:00 | main | feat(admin): add session verification middleware |
| 3e4caf465a0d50ca9c6861ae9617b602a8b97c23 | 2026-05-13T05:17:11+09:00 | main | feat(site): tighten AI-SEO metadata and crawler policy |
| b062b9a9bc0c5f4f2c1ca473cb6f5525200e35ff | 2026-05-13T05:17:26+09:00 | main | chore(infra): close env+compose gaps for production parity |
| 1e3e990c22156bd66b44e2e9655d77326bc1ace6 | 2026-05-13T05:22:53+09:00 | main | feat(site): deepen AI-SEO surface (ISR, llms.txt, entity linking) |
| ea4490942695634613cb175e2752977b8c74cb71 | 2026-05-13T05:26:16+09:00 | main | Merge pull request #3 from wjlee930501/feature/audit-p0 |
| 30824ddfe0e1e71fed574e195c96529c98d34443 | 2026-05-13T05:38:58+09:00 | main | feat(content): apply GEO research to content engine + filter |
| d5947b8e88cb24b70705a638f9470de461fc3ff4 | 2026-05-13T05:39:07+09:00 | main | feat(site): enrich content image alt, ImageObject schema, AI disclaimer |
| c0766349f75430ae01643f04dc4a8e1f78b5b2af | 2026-05-13T05:39:47+09:00 | main | Merge pull request #4 from wjlee930501/feature/geo-research-impl |
| dbaee2270d627d51f88d646cde480071271a784f | 2026-05-13T05:53:36+09:00 | main | feat(entity): hospital sameAs identifiers + director credentials |
| 0c085f7e7a5037d268527f86ddca589f096e8206 | 2026-05-13T05:55:10+09:00 | main | feat(citations): tag content references with authority source_type |
| d23e16ae7d7cf6d952c7f15a5cbf7acbe6f23c47 | 2026-05-13T05:55:51+09:00 | main | Merge pull request #5 from wjlee930501/feature/p2-schema-entity |
| 7772285886446c0a459edcec627bbe17f618ee26 | 2026-05-13T09:55:13+09:00 | main | chore(admin): sync next-env.d.ts with current Next.js dev build |
| b1bd853fe3de0e80f369ee2dfb4cede0c297f83b | 2026-05-13T11:00:44+09:00 | main | feat(site): treatment pillar pages + bidirectional cluster links |
| 0780806f15d3eee3d112d9834ac0aaed0c9a6ee3 | 2026-05-13T11:03:19+09:00 | main | style(site): pillar backlink styles in article header |
| ff793f6f655014eaf5c35708e2ff9b784f4f3260 | 2026-05-13T11:24:31+09:00 | main | fix(site): decode percent-encoded Korean slug in pillar route |
| 2db249d11189d350acb5254fd182b0d10d9f39c3 | 2026-05-13T11:25:30+09:00 | main | Merge pull request #6 from wjlee930501/feature/pillar-pages |
| 361734482a72d9948c62b0fa89e860ec69f2454d | 2026-05-15T06:29:29+09:00 | main | Harden release blockers found in full review |
| ec776161cdd155f3b7c65cccdc9e8a5bf7f3e088 | 2026-05-15T07:20:44+09:00 | main | Align hospital UI with AI-search operating model |
| 06fe198733a4596ec1d1e2d0b56ea048e8a67fce | 2026-05-15T15:55:48+09:00 | main | Clarify onboarding operations handoff |
| 4b50e7d5485a1da2775ec7d3d338d53b041ceefe | 2026-05-15T16:13:55+09:00 | main | Smooth operator onboarding from captured leads |
| d384148cbc6bb488660f3b2d1624961b673da5c3 | 2026-05-19T23:01:14+09:00 | main | Protect admin auth during Next proxy migration |
| 2fb1317d54abf3aae11113163308014351845c5f | 2026-05-19T23:24:30+09:00 | main | Raise admin and public flows to release readiness |
| d6963c36f62746a1c9fcedf18cfd7f97957a8c14 | 2026-05-25T14:49:59+09:00 | main | feat: production readiness — 4-round comprehensive audit + fix (28 issues) |
| ef8c35a8acf6749c6e296ebf9d8af9e3ee78f9e0 | 2026-05-25T16:56:46+09:00 | main | Make release gates repeatable before production handoff |
| 334be211dc255ea8214d6f09637f19da1b8ad4f2 | 2026-05-25T23:00:45+09:00 | main | fix(admin): medical filter regex sync, publish site-link, schedule navigation, 401 redirect |
| 6a390ed62b47a35d168b63c3d7b7f4ac4ce1beee | 2026-05-25T23:00:53+09:00 | main | feat(admin): SVG nav icons, tab architecture split, skeleton loading states |
| 49aa1792900f223f98977ea74574bfb33557c059 | 2026-05-25T23:01:01+09:00 | main | fix(backend): content batch success/failure tracking with Slack summary |
| ca8502c1a26c6193a6f67dd49675ab398247c8d4 | 2026-05-25T23:01:09+09:00 | main | feat(backend): Cache-Control headers for public API responses |
| 8ae104a3427f31d30d615d0ba1f4a52f14b79d06 | 2026-05-25T23:01:19+09:00 | main | style(site): replace '블로그' with '의료 정보' terminology uplift |
| f37d634b742821ed2944e01a3b7e5270a4dd252a | 2026-05-25T23:01:27+09:00 | main | feat(site): hero CTA enhancement, mobile scroll indicator, OG image fallback |
| a43fa22113460d00e0d11193c7a4e7a974ac2734 | 2026-05-26T18:36:28+09:00 | main | feat(site): rebuild landing as minimal white (Stripe/Toss) with interactive AI answer demo |
| 47c06390fd5dae9bb4567836993ccbe099ba38e3 | 2026-05-27T13:24:31+09:00 | main | feat(standalone): v2.2 AI-reputation landing + terms/privacy pages |
| 09eb6855e5f146197e2b90e04c9d57cd0a72077b | 2026-05-27T14:12:50+09:00 | main | feat(site): editorial refresh of clinic public surface |
| 8b8071e7b9cdd6d039d6075fdc6d6e4058f41b0b | 2026-05-27T14:26:12+09:00 | main | Merge remote-tracking branch 'origin/main' into feature/pillar-pages |
| c016fec163d2b30347b74c0bbb397a5878fbacbc | 2026-05-27T14:28:13+09:00 | main | fix(merge): reconcile main into branch — restore hospitalSlug prop and lead onboarding helper |
| 93b940d6213f9a897d0f1e9eef573f62c131eed5 | 2026-05-27T14:28:46+09:00 | main | Merge pull request #7 from wjlee930501/feature/pillar-pages |
| 107d463095b8d24ee5f41e8111a1169db904b050 | 2026-05-27T14:31:27+09:00 | main | fix(ci): clear lint errors after main reconciliation |
| be7aecf31c65107fddce0a18ed762d533f0bee28 | 2026-05-27T14:33:28+09:00 | main | Merge pull request #8 from wjlee930501/feature/pillar-pages |
| f1d99e68a194089d89b019ad61368c297f3380dc | 2026-05-28T08:57:34+09:00 | main | Make hospital demos credible enough for sales and onboarding |
| 05c09714713cf66f5a3bf8a14d91c068f59d01c5 | 2026-05-28T22:01:35Z | main | feat(site): restructure hospital webblog IA and remove anti-slop visual debt |
| 8b8a2f135feca9c8a0d239ce875eb741d3380591 | 2026-05-28T22:33:22Z | main | feat(site): editorial content-hub redesign + self-hosted Pretendard |
| 35b1e3e92e37ddbf83a56539b38e5965047d16d1 | 2026-05-28T22:44:58Z | main | refactor(site): brand-forward hero + patient-facing copy audit |
| 2db88580823db715e972494a9ecabbfa10dd4654 | 2026-05-28T22:55:50Z | main | fix(site): use clinic-space photo in hero media instead of director portrait |
| 48c1f876f3529bf8c2c1ab9e2e1cdda8dcb426ea | 2026-05-29T07:58:05+09:00 | main | Merge pull request #9 from wjlee930501/claude/hospital-web-redesign-5wj8g |
| 0812d4fc857088155213eb259320c7070f081f48 | 2026-05-29T19:15:55+09:00 | main | Polish clinic webblog design: dedupe sections, color depth, mobile hero |
| 725f6fb351835a25a4003ca82859d8cb3a7249e1 | 2026-05-29T19:17:02+09:00 | main | Merge clinic webblog design polish (P0+P1) |
| 0417dddb6aaf6c6598c13b652c4025e1158d76f0 | 2026-06-08T13:04:53+09:00 | main | harden(backend): Wave 1 — security, correctness, reliability core |
| 19e443cfb0024fe63f8530251a2524839a48c190 | 2026-06-08T13:21:56+09:00 | main | harden(frontend): Wave 2 — admin + site security hardening |
| b06b3ef8696609caf94a612b66e92cf638d631da | 2026-06-08T13:24:36+09:00 | main | harden(infra): Wave 3 — Terraform, Dockerfile, CI hardening |
| 40287514daa9f0de495fd954b9af57af2c4f4073 | 2026-06-08T13:35:11+09:00 | main | harden(compliance+tests): Wave 4 — PII, auth seeding, real-DB test harness |
| d74176c9a055b1ff42c0f3b7a54b5adb63607f99 | 2026-06-08T13:50:01+09:00 | main | harden(review): remediate adversarial-review findings |
| e4fbca701d953dc0526769a95e6204b6bfd48122 | 2026-06-08T14:49:14+09:00 | main | Merge production-hardening pass (security, correctness, reliability, compliance) |
| d14d2809b79a844e299bf41f9f5796f41fc2cad8 | 2026-06-08T18:24:47+09:00 | main | harden(codex-review): remediate Codex adversarial-review findings |
| b47fe7a1dfc7c3215d474c582de6eef131186bc5 | 2026-06-08T18:33:02+09:00 | main | harden(codex-review-2): morning all-success mark-done + document residual items |
| 01c56362df6cabd48730645f4866e5370ad0dd5d | 2026-06-08T18:52:48+09:00 | main | Merge Codex adversarial-review remediation |
| 01072a35b06b024b8538bd0b07d956f24d1ac237 | 2026-06-08T19:05:54+09:00 | main | docs: add post-hardening deployment runbook |
| 15b2190b44f7a685589130738d5e10fece321d31 | 2026-06-09T20:53:05Z | main | harden(pass-2): resolve CDX deferred items + fix fresh adversarial-review findings |
| 49196e964647bcfc416df9ce07b44ea56c25cd13 | 2026-06-10T05:55:44+09:00 | main | Merge pull request #10 from wjlee930501/claude/production-readiness-review-hfhpwr |
| f866bfd2c7452ff77ee35472fd502ec38fa8560b | 2026-06-09T21:14:47Z | main | infra: move site/admin from Vercel to GCP Cloud Run (full-GCP deployment) |
| 1a73162a3107dd7c2f4e0fb3cd77b8e848349550 | 2026-06-10T06:19:26+09:00 | main | Merge pull request #11 from wjlee930501/claude/production-readiness-review-hfhpwr |
| 395a8491980090741960cd73453785b885e2a646 | 2026-06-09T21:24:39Z | main | fix(ci): force ADMIN_SECRET_KEY in test conftest — CI job env was shadowing it |
| 500e4cc7826308d15c00b0cb7a4045c67b01a9fc | 2026-06-10T06:25:04+09:00 | main | Merge pull request #12 from wjlee930501/claude/production-readiness-review-hfhpwr |
| b42d203517bfe6a7cc5e8c5fba73af6a2bf64545 | 2026-06-09T21:56:14Z | main | fix(deploy): resolve runtime blockers that would break first production serve |
| 558525d57294dbe53958956b0a820dc3521e4f8d | 2026-06-10T06:56:46+09:00 | main | Merge pull request #13 from wjlee930501/claude/production-readiness-review-hfhpwr |
| dc2eba2377ecc41293a9b1f613d1f6435b119899 | 2026-06-11T17:14:43Z | main | harden(infra): make deploy path actually runnable + close infra audit findings |
| 30cae8fe147cad09eb87dd3d9d2c863d47c2162e | 2026-06-11T17:15:36Z | main | fix(site): close public-surface launch blockers from product review |
| dea9ce4aa311b0e93d663da6c6075e4fcda9097a | 2026-06-11T17:24:25Z | main | harden(backend): close flow-integrity audit findings + admin-support endpoints |
| 6db911aaec25bf3af182f4b676616dba730c0ad7 | 2026-06-11T17:24:25Z | main | fix(admin): unbreak AE operational flow per product review |
| c6427909c09ed31e6e34697aeb55fae77472b452 | 2026-06-11T17:26:06Z | main | fix(backend): never 500 post-commit on revalidate in update/reject paths; sync stack docs |
| 4c5d2e78e20315d2e43bce21334d706a46159848 | 2026-06-11T17:55:27Z | main | fix(review): close adversarial-review findings on site/admin surfaces |
| 5c238cb5858c54de0629819a894bd3fea3aca53b | 2026-06-11T18:03:23Z | main | harden(review): close adversarial-review findings on backend + ops |
| 2cd6c5815c8ecd702c97085b220e2537cbaad2ad | 2026-06-11T21:25:23Z | main | feat(infra): per-hospital custom domain serving path |
| 76a0fd7fabe8b0aecdad78ecc7569e9120963d92 | 2026-06-11T21:32:44Z | main | feat(admin): custom-domain connection UX + carried-over content todo |
| c4436a2a8e726cdef5a603d88652f0c4e87dd232 | 2026-06-11T21:34:01Z | main | feat(site): serve hospital hubs on their own custom domains |
| dd007e887573d2a9f48be416f039799acde4c768 | 2026-06-11T21:35:53Z | main | feat(backend): custom-domain resolution + month-end reject carry-over |
| 15dad6d8663d8f26ed13062044eed583ab706d8d | 2026-06-12T07:14:52+09:00 | main | Merge pull request #14 from wjlee930501/claude/product-launch-prep-ml4x5w |
| 5e8096b5c4f50de4b438980ec6ef39f1c2d5e2bb | 2026-06-13T09:05:34+09:00 | main | feat(deploy): launch GCP domain onboarding |
| a241a2cc57dfd8e7ad00ae7b66a789e8d405a639 | 2026-06-13T09:09:01+09:00 | main | fix(deploy): cap beat timeout for Cloud Run |
| b3bea1bfed81341a2066500fa56fcf7f4ede8148 | 2026-06-13T09:09:42+09:00 | main | fix(deploy): meet beat memory floor |
| 6876ea8ac61412fccc7a78be102f35de081e5bcd | 2026-06-16T22:54:18+09:00 | local / other / stash | untracked files on feature/pillar-pages: 107d463 fix(ci): clear lint errors after main reconciliation |
| ba89687ce303bc98a29fd6518a75c96d825f75cc | 2026-06-16T22:54:18+09:00 | local / other / stash | index on feature/pillar-pages: 107d463 fix(ci): clear lint errors after main reconciliation |
| 3414e202b6320920176ff4a41f8e29d94c14c303 | 2026-06-16T22:54:18+09:00 | local / other / stash | On feature/pillar-pages: codex-main-merge-20260616 |
| 247173df38b7994237edcbc5d373d3410a6a6666 | 2026-06-20T22:14:42+09:00 | main | fix(backend): close release-blocker integrity gaps |
| 0ef5128119a5a70f4300626c749ec1d6b27d2d32 | 2026-06-20T22:14:47+09:00 | main | fix(admin): harden auth proxy release paths |
| ac2d325767281c95fa88d7288da66e51494742f7 | 2026-06-20T22:14:52+09:00 | main | fix(site): harden public hospital surfaces |
| 16dff94ecdb4b09171dfedbab41a4e2c5e7cd2e1 | 2026-06-20T22:14:58+09:00 | main | fix(deploy): prepare hybrid launch path |
| 5f209110a94359b998e5d99b2d15d7c5ee385298 | 2026-06-20T22:27:14+09:00 | main | fix(deploy): clarify live proof and Vercel host routing |
| 6747c15f144f0e6081da1f89161a7c0d6f8d5ea9 | 2026-06-20T23:15:00+09:00 | main | chore(deploy): ignore admin vercel metadata |
| a13adc285ac4ecef58b16e2e5bcfa730432bfaad | 2026-06-21T14:41:09+09:00 | main | fix(infra): pin GCP_LOCATION in terraform to match live deploy |
| 7dad336df8858872f3862cb68048a8e58f83500f | 2026-06-21T14:50:38+09:00 | main | docs(infra): correct http-redirect deferral reason to IN_USE_ADDRESSES quota |
| 7f7ffc083723aab6ba07338bb369824a0d42043e | 2026-06-21T15:46:44+09:00 | main | feat(onboarding): real source→essence→SEO/GEO content pipeline for clinic onboarding |
| 11365a92a3efa6deb206fc181575d1ad6ac64198 | 2026-06-21T20:25:34+09:00 | main | fix(deploy): send DNS preflight output to stderr so it can't corrupt the site/admin image URL |
| 2af5f0790042d57de8e12d40ad1aeebd1c7c541e | 2026-06-21T20:32:59+09:00 | main | feat(onboarding): bulk Naver blog ingestion via RSS |
| 08d282d60a571bc61f54018ac9db0564034e4d78 | 2026-06-21T21:52:16+09:00 | main | fix(ingestion): read peer IP from httpx server_addr — URL crawling was fully broken |
| ef8db517a4981bd0d32b584c664aced07b261ca1 | 2026-06-21T22:17:02+09:00 | main | fix(onboarding): pre-onboarding screen remediations — CJK PDF font + 6 fixes |
| 2b8402739e8f7a661be42a88e5a39670e7540110 | 2026-06-22T00:16:39+09:00 | main | feat(onboarding): auto-fill hospital profile from online sources |
| b5addfb656ae04cbcdada410c5e60ce0eb4b0c6c | 2026-06-22T00:43:49+09:00 | main | fix(admin): raise BFF proxy timeout for slow autofill endpoint |
| dbb78352b2e083718d62b210ef127b522d20c321 | 2026-06-22T00:57:54+09:00 | main | fix(admin): un-nest autofill modal form to stop page navigation on 가져오기 |
| de93c68e29b027cee8ef5fd752f9fbba765f9a24 | 2026-06-22T01:56:14+09:00 | main | fix(site): stop false strikethrough in content body + fix hero hours label |
| bc18dd2bc1ebf22be0a46c6a649dbea4451b6397 | 2026-06-22T02:04:11+09:00 | main | fix(site): align FAQ Q&A block with article body (was 44px off) |
| 7202f3c8d438c6a78d882addc18c781a5d950b6f | 2026-06-22T02:34:41+09:00 | main | feat(site): premium micro-interactions + fill empty answer cluster |
| 269ed84674c43d7caf952f561c99f79cb9202dc7 | 2026-06-22T05:17:07+09:00 | main | fix(site): improve mobile information density + flatten hover polish |
| fe167685668e01c0f3f2837238bd05c0ce703e3e | 2026-06-22T05:35:12+09:00 | main | ci: fix site image build (inject NEXT_PUBLIC_* build-args) + clear ruff lint |
| 7503d8077714665791ca9c7e313d38d94f211afb | 2026-06-22T05:59:47+09:00 | main | fix(site): tidy article line-height + enrich doctor & related sections |
| 74a0a845f86befc45813221569948b40a23f56dd | 2026-06-22T06:46:24+09:00 | main | fix(site): prevent mobile h-overflow on 12-item treatment grid |
| 6a842142409a9faeb352889c5dae67379c9244e2 | 2026-06-22T14:09:31+09:00 | main | feat(site): aggregate FAQPage + enrich Physician credentials on hub landing |
| dcca695202ed45239fe47899659e4770fa3c67bd | 2026-06-22T14:19:55+09:00 | main | fix(site): remove slop decorations from treatment cards (icons + accent) |
| 630c683b07fce4ccec589a82fbd834a9ee20ebf5 | 2026-06-22T16:00:46+09:00 | main | fix(site): remove decorative AI-slop across the public hub (deslop pass) |
| c2f8ca90ae5d950a2291e3e463b11bac5c96071d | 2026-06-22T16:25:40+09:00 | main | fix(images): serve content images via stable proxy URL (Option A) |
| e016f82ae1fe92111ced5b6b1aeb996f3938b03a | 2026-06-22T16:51:00+09:00 | main | feat(backend): backfill-images job for content missing 대표 이미지 |
| e500a2dce9871bc65ec26ab32e389eb3c13edbf6 | 2026-06-22T17:09:36+09:00 | main | feat: 대장내시경 deep-format content cluster seed + fix deploy DNS preflight |
| 3293c1bb5f5753bb8b2bfab46caf6a7fc17b4c88 | 2026-06-22T17:15:01+09:00 | main | fix(seed): use ->> operator for content_brief seed_tag idempotency check |
| 37123bcb25f6ba26266412edcae3375363bf5943 | 2026-06-22T17:40:56+09:00 | main | fix: retry-safe colon-cluster seed (avoid-safe COLUMN) + drop empty answer-cluster filler |
| 64375970f96f1e8404f1723844b8105cc9c4a1b9 | 2026-06-22T17:44:35+09:00 | main | fix(deploy): send DNS preflight stdout to stderr (was polluting image_url) |
| df17b8a89b7ab2003c0d5f7619e86a9df03978e6 | 2026-06-22T17:56:44+09:00 | main | fix(seed): dedupe legacy seed_tag items lacking seed_item |
| 7f42ec5f943d49923fd43c1d61e8bf10d1d022ac | 2026-06-22T19:48:02+09:00 | main | fix(api): never cache error responses on public endpoints |
| 5d768718469cac30e1e227c38a4609961b829f4e | 2026-06-22T20:24:30+09:00 | main | feat(backend): unpublish-flagged job to take violating content live->DRAFT |
| e95d3271b56f59feed784c9b6c5777a576051900 | 2026-06-22T20:30:13+09:00 | main | fix(content): stop prompt from instructing fabricated stats/citations + restrict to profile facts |
| eabf6ed3bf53b4746e9a276bdd36c867699a8841 | 2026-06-22T20:56:14+09:00 | main | fix(content): harden prompt (no superlatives/overstatement) + DRAFT-only gen + fix director credential |
| 6dae4e258de8b5334eb91e0f801868cc5089f3c9 | 2026-06-22T21:24:13+09:00 | main | fix(review): resolve code-review HIGH/MED findings (prompt contradictions, seed draft-loss, proxy passthrough) |
| a8cf477ff9e104208f0231f8b1e85b99a6dcc4e4 | 2026-06-22T21:27:18+09:00 | main | fix(content): drop internal '추천율 73%' example from LOCAL prompt (codex residual) |
| 645d4cc52cf0cfb3c6ba7f36efdce3c3e972cdce | 2026-06-22T23:16:01+09:00 | main | fix(migration): recover phantom revision 0030 (unique ai_query_target per hospital) |
| 91d69dbebc13fbe9b437a0c0b5ef291a413f50fa | 2026-06-22T23:16:11+09:00 | main | feat(images): generate content images with gpt-image-2 (topic-aware, slop-free) |
| 76a6c03beea30b0ee8f5578a650227fbf0ceb2bb | 2026-06-22T23:16:19+09:00 | main | fix(site): stop cropping director portrait in hero panel |
| 4da9380ebb3e194cd71df7e17107ce4688f8f45b | 2026-06-22T23:57:21+09:00 | main | fix(images): don't retry moderation blocks + harden prompt against false positives |
| 784790ebcec735f8dc110e0a7a3304003a28bfec | 2026-06-23T18:09:11+09:00 | main | feat(domain): hybrid domain model on Certificate Manager (subdomain default + custom domain) |
| cf5bcd8d89122523bae79585819ea4161a3ee627 | 2026-06-23T18:18:46+09:00 | main | Merge pull request #17 from wjlee930501/feature/hybrid-domain-certmanager |
| 30275b6370670259b271c22a6150ac6cb9f3dfd4 | 2026-06-28T21:58:58+09:00 | main | Merge branch 'main' of https://github.com/wjlee930501/reputation |
| 7873e1f01bc70433ddeb849ccc3f23fa91f2dee5 | 2026-06-29T15:58:29+09:00 | main | fix(release): harden domain assets and manual publishing |
| ffec70850a8acd245e5d91b12635d60b6fe4d8af | 2026-07-01T20:45:11+09:00 | main | Merge branch 'main' of https://github.com/wjlee930501/reputation |
| c544b719a6e84d4df8c134bd0c959b43ee67dd81 | 2026-07-01T23:04:38+09:00 | main | fix(security): harden admin sessions and deploy rollout |
| c3cd3165dcbefdfdaf0c375611e082c2dc079930 | 2026-07-07T18:36:42+09:00 | main | fix(release): keep custom-domain rollout safe |
| 0af6ac4e9cd6f8eda9cc52de59153f462281453c | 2026-07-08T03:47:23+09:00 | main | fix(backend): close audit-confirmed defects and add cost guardrails |
| 23cfee853614573d3e20918530791aa336a050cb | 2026-07-08T03:48:11+09:00 | main | fix(admin): session revocation immediacy, draft safety, pause/resume UI |
| 05a77e3a4ee2c1ab6e6f8976454a0253f3cc77f8 | 2026-07-08T03:48:11+09:00 | main | feat(site): medical editorial redesign + host-scoped sitemap |
| 7f3c7cdc6ee9350cce5bcce5323ddcb2bb4eaf7b | 2026-07-08T03:48:11+09:00 | main | docs: add root README, align CLAUDE.md flow with enforced gates |
| b4977a46397dc6c7ad857a6956107552626dc428 | 2026-07-09T00:45:01+09:00 | main | fix(backend): block crawl error-page text from contaminating public content |
| af4d0bcd349412fca88b089bdbde36c3c15c7397 | 2026-07-09T00:45:01+09:00 | main | feat(site): design pass 2 — visual anchors and rhythm for real-world data |
| e25841f83f060bcf6231273b688682ceba5b5a02 | 2026-07-15T12:02:43+09:00 | main | Give Jang Clinic a faster, trustworthy first impression |
| a31349c874eb63ba440582569c410ca0e3743a77 | 2026-07-15T12:10:55+09:00 | main | Merge pull request #18 from wjlee930501/codex/jangclinic-redesign |
| 823897c7b513649a7db5ce99d9bdda6615060283 | 2026-07-16T02:16:49+09:00 | main | Harden onboarding and production operations |
| 8b4acb86bd3e23c19c5bf27b25d31373d04b0216 | 2026-07-16T02:24:37+09:00 | main | Restore the deployed migration lineage |
| 8cbe06656e8b5acfd40c7b0f907a5743e94f2ff1 | 2026-07-16T02:45:07+09:00 | main | Converge production operations |
| 9d3cecfe259d7d34f030f5457569a8ad84c501cf | 2026-07-16T02:53:44+09:00 | main | Close operator monitoring gaps |
| 09bffd1f22a119f858e564f78a74f921a04b3bc8 | 2026-07-16T02:58:55+09:00 | main | Make security gates warning-free |
| 0d86462b4a21669d2bf424562302e5b0eb5ce1ab | 2026-07-16T06:27:13+09:00 | main | Automate content publishing with post-review |
| d54e53b2faa916f56a219ecaf118a5c85bd4bf78 | 2026-07-16T06:29:25+09:00 | main | Avoid false-positive secret detection |
| 76d09d2258148f94791de366011420516842b8d5 | 2026-07-16T07:19:04+09:00 | main | Refine post-publish content operations UX |
| 821e0be50987b7b83aa8fc4c716cdc2e7f2ae30f | 2026-07-19T22:24:28+09:00 | main | Fix public content visibility with pending sources |
| 0076d9bb81664e3724e13712699e845d700e6b8b | 2026-07-20T00:27:05+09:00 | main | fix: harden publishing and AI exposure operations |
| d5509bc5cb15c65c54edf1a87fa13d2e5edadb8a | 2026-07-20T00:42:55+09:00 | main | fix: forward production flags during deploy |
| 68af5cf5c54fa90fadb9ff6b69c724d79be30c96 | 2026-07-20T00:54:12+09:00 | main | fix: enforce citable medical source evidence |
| 632561ba4ff6aa1a0de68eb48abce404b71c48fd | 2026-07-20T01:09:06+09:00 | main | fix: canonicalize hospital custom-domain routes |
| 55b8898f83c06c6dfdaeced505befba8ae633387 | 2026-07-20T01:13:35+09:00 | main | fix: align generated content with publish dates |
| e4a4d9a4ef497dd13d2951c1f817a2bd3d34a97b | 2026-07-28T06:36:37+09:00 | main | fix: let the page title own the only article H1 |
| a6c50b8ad39bf75d068e1ec4b7d83ee43962b101 | 2026-07-28T06:36:44+09:00 | main | feat(admin): re-compose the operating console for mobile |
| cd4c2d336150a0aa2b673abf291c6663966137e4 | 2026-07-28T06:36:51+09:00 | main | feat(site): add section navigation to the hospital hub |
| 3cde25e6bc8be976b17a5ac00b49c99f497e0ba2 | 2026-07-28T06:36:58+09:00 | main | docs: refresh DESIGN.md with the 2026-07-22 visual audit |
| e6a44a3926fdc6702aa1295bcf6ab2d7c5fb6763 | 2026-07-28T06:43:16+09:00 | main | fix: keep an internal term out of the measurement failure log |
| 8c743d0d5bb5149a1a2d9b50b9d592e5a7806f4b | 2026-07-28T06:43:22+09:00 | main | ci: run the copy and DB budget guards |
| 693101c51bc72b8d1d1433c30aeb34ffbb649651 | 2026-07-29T07:52:05+09:00 | main | fix: 측정 무결성 결함 4건 수정 + IndexNow 색인 제출 추가 |
| 317d89e593147734201c1a185965ecd5ec2aaa31 | 2026-07-29T07:52:37+09:00 | main | docs: AI 노출 진단 퍼널 PRD + 재현 가능한 측정 도구 |
| c874b978444bbfd577f94c1867d99b5c3f888bb5 | 2026-07-29T07:57:23+09:00 | main | fix(deploy): 이미지 빌드·푸시 실패를 성공으로 보고하던 문제 |
| 731418a04320a814680eb12d49849cfe03830fa0 | 2026-07-29T08:15:38+09:00 | main | merge: 측정 무결성 수정 + IndexNow 색인 제출 |
| bebcaddef9e833b437d352b911e53decdb2552b3 | 2026-07-29T13:15:14+09:00 | main | fix(deploy): 측정 모델이 부동 별칭이면 배포를 차단한다 |
| 3bdae8673f29b5d41f79cf2692c71e710d600ce5 | 2026-07-29T16:27:43+09:00 | main | fix(deploy): 선택 시크릿 때문에 site 배포가 막히던 문제 |
| 9c80133fb3b53878299941070080a24c513c9573 | 2026-07-29T16:28:06+09:00 | main | fix(compliance): 의료광고 금지 표현 검사를 렌더 결과 기준으로 수행한다 |
| 55af6eac3c0cacb9927220717eb84a8b6641a203 | 2026-07-29T16:28:25+09:00 | main | fix(worker): 생성 중 취소된 콘텐츠가 되살아나 자동 발행되던 문제 |
| 8effee57457823a99bf613d6e4b39143bec78e1e | 2026-07-29T16:28:43+09:00 | main | fix(sov): 측정 실패를 "언급률 0%"로 원장에게 보고하던 문제 |
| c6e599cb239dd0b9e801cc63ad9602df5b391502 | 2026-07-29T16:28:59+09:00 | main | fix(indexnow): 소유를 증명할 수 없는 색인 제출을 차단한다 |
| d6bc2e0020231a32fae8e96c4b75698843d6622d | 2026-07-29T16:29:12+09:00 | main | fix(copy): 랜딩에 노출되던 "AEO" 제거 + 용어 가드 사각지대 해소 |
| e22ddbeddd9cf094b68c0fda89e16289c129f9dd | 2026-07-29T16:34:22+09:00 | main | fix(compliance): 링크 오인 미탐 + 참고자료 제목 검사 누락 + 이미지 누락 집계 |
| d1c4c3bc3aa3d412b49bf919c05473d556dcccd3 | 2026-07-29T16:44:32+09:00 | main | merge: 발행 무결성 + 측정 계약 + 색인 소유 증명 |
| 5821409e5f7b65e8a80cf4a91bed0390263a997c | 2026-07-29T17:11:47+09:00 | main | fix(state): 일시정지 해제·NOTICE 영구 미발행·stale 발행 검증 |
| 0bb7a3f5a4aecf02f3c60a0546b56fc44928770c | 2026-07-29T17:12:09+09:00 | main | fix(worker): V0 영구 고착·야간 claim 잠금·격주 측정 공백 |
| 170e78dd90ced81e8e59f6feb5c8b7d069ddee18 | 2026-07-29T17:12:37+09:00 | main | fix(security): 리드 PII 평문 유출·감사 로그 공백·업로드 메모리 적재 |
| 749c0a9883695aa1c47cd86ff07835295432c07c | 2026-07-29T17:12:59+09:00 | main | fix(scheduler): 월간 슬롯 유실·조기 생성·죽은 네이버 동기화·비용 계상 |
| 01034094ddca9a389d65daf845079a459ab0364b | 2026-07-29T17:13:18+09:00 | main | fix(site): sitemap 리다이렉트·JSON-LD 무효·본문 링크 무방비·호스트 판정 불일치 |
| 97708e5a0d7c088b778ec18f25cc0b56867f0822 | 2026-07-29T17:13:40+09:00 | main | fix(infra): provider lock 미추적·배포 불가 템플릿·env 소실·롤백 부재 |
| 80f1ba79b83553e6fd53e464b360aac486e26d2f | 2026-07-29T17:14:05+09:00 | main | test: 조용한 skip·크로스 테넌트 미검증·소스 regex 테스트·죽은 코드 |
| f8422ef1e7f3dcdac1b35ef5c316ad2fadc85701 | 2026-07-29T22:59:55+09:00 | main | fix(sov): 측정 타임아웃·모델·플랫폼 비대칭 — 실측 기반 재설정 |
| 17a15e546b07fad8a9ffecac449183c424b6cd98 | 2026-07-29T23:19:05+09:00 | main | fix(sov): 언급률 분모 분리 + V0 표본 확대 + 죽은 설정·시크릿 불일치 정리 |
| 42ee1b97ee4231ce39cc91c934e33d0f33dfe508 | 2026-07-29T23:33:21+09:00 | main | docs(prd): AI 노출 진단 퍼널 PRD rev4 — 당일 실측 반영, 미해결 9→7건 |
| d496f4494b39c6c41fbc5ef59737edda32272b20 | 2026-07-29T23:39:13+09:00 | main | docs+copy: 랜딩이 못 지킬 약속 제거 + 리드마그넷 설계 착수 핸드오프 |
| 76f1af28ece354314575828ad2b6c02a9a8aed88 | 2026-07-30T01:22:24+09:00 | main | feat(leadgen): 무료 진단 접수 — 선착순 20건 + 전화번호·이메일 이중 잠금 |
| f8fb7a97956adcd201215cdbcfb4ea0845d038ba | 2026-07-30T01:33:15+09:00 | main | feat(leadgen): 측정 실행 + 질의 공유 캐시 + DB 폴러 |
| d5baae7c636ecc5a50fa3b00632dfa9c33681d23 | 2026-07-30T01:43:42+09:00 | main | feat(leadgen): 블러 리포트 생성 + 열람 표면 |
| 82879afdca8db41cf1ef504a83bf4d23359d0cb4 | 2026-07-30T01:50:50+09:00 | main | feat(leadgen): 리포트 메일 발송 + 파기 cascade |
| ebb3d37a4cc67e7dfdfeefc6e59e087087f700fd | 2026-07-30T01:57:25+09:00 | main | feat(leadgen): 랜딩 신청 폼 + 확인 모달 + 실시간 자리 카운터 |
| b9e3457e339822e52a6f46ccd9d0afe8d27938c7 | 2026-07-30T01:59:22+09:00 | main | feat(leadgen): 상태 페이지 + 리포트 열람 프록시 |
| 67f3ebdb959fb9ba32176dfdc0b59b4926dc4898 | 2026-07-30T02:00:41+09:00 | main | docs: 리드마그넷 잔여 작업 핸드오프 (Admin 1건 + 배포 준비) |
| 5d1f59179b4302229326277ff72d9810c55c00b3 | 2026-07-30T02:29:57+09:00 | main | fix(leadgen): 적대적 검토(Codex) 반영 — BLOCKER 5 + HIGH 8건 |
| 5064c8dc9bdb5301479760e662d6622a47cf7feb | 2026-07-30T02:30:53+09:00 | main | docs: 핸드오프에 적대적 검토 결과 반영 |
| ed4ab14284e2313ef96da818a25426ff6d3e06cd | 2026-07-30T02:31:08+09:00 | main | Merge: 리드마그넷 무료 AI 노출 진단 퍼널 (1단) |
| d0b64a415da9ec388af3728e32429460bc01a8cd | 2026-07-30T09:27:41+09:00 | main | fix(ci): ruff 규칙셋 명시 + import 정렬 81건 — CI가 계속 빨간불이던 원인 |
| ccaf82285f1a650deaa1343a600c0066f7b3dbdf | 2026-07-30T09:30:10+09:00 | main | docs: 리드마그넷 배포 런북 — 시크릿 3종·마이그레이션·확인 절차 |
| 1c5afd598a878318983c755b774194766c1d0ba3 | 2026-07-30T09:48:44+09:00 | main | fix(leadgen): BFF 프록시 경로 중복 — /api/v1/public/public/diagnosis 404 |
| f9bfa4f37fa61f88733b6ebfe61383c953b72a7f | 2026-07-30T09:54:35+09:00 | main | fix(admin): proxy config를 정적 리터럴로 — Turbopack 빌드 실패 해소 |
| 2d7381b05db28623ef1996de135082fdba473b92 | 2026-07-30T10:16:42+09:00 | main | docs: 배포 E2E 결과 — 측정·리포트 통과, Resend 키가 PLACEHOLDER였다 |
| 7d434d4b53938ca5f3f3200e925058d3a5f13f88 | 2026-07-30T11:35:52+09:00 | main | docs: 메일 경로 검증 완료 기록 — 401→403→200 |
| 39a585395399b88a0f102ba4cf85d47e45fe3f65 | 2026-07-30T12:42:27+09:00 | main | feat(leadgen): 발송 실패 복구 경로 + Admin 리드마그넷 운영 화면 |
| 5a3713d4ad201d706e54c50ac97e6e1541c99ba8 | 2026-07-30T12:42:54+09:00 | main | feat(leadgen): 무료 진단 호출 예산 상한 — 자리 20개는 호출 상한이 아니었다 |
| 033c5a10cef270f7d8489ef1e6c785baa7f8fe9a | 2026-07-30T12:51:42+09:00 | main | fix(deps): site·admin 의존성 보안 패치 — admin 미들웨어 우회 권고 포함 |
| c32dc3b29413775b9ba880589830bba29b20386f | 2026-07-30T12:52:07+09:00 | main | Merge: 리드마그넷 복구 경로·운영 화면·호출 예산 상한 + 의존성 보안 패치 |
| 3164807c9ed0f3e8bf82a25a37ae8df668224e8f | 2026-07-30T13:10:50+09:00 | main | ci(security): npm audit 게이트를 배포 코드 범위로 + 고칠 수 없는 권고는 기한부 예외 |
| e5a723ceea2853f43e8a4fff7727e48d2383ab94 | 2026-07-30T17:20:59+09:00 | main | feat(site): 랜딩 재작성 — 데이터로 논증하는 구조 + 실측 선착순 카운터 |
| 74664708ed890ed3f7ac21a02357efa3708e631f | 2026-07-30T17:21:28+09:00 | main | fix(report): 실제 발송된 리포트에서 발견한 결함 3건 |
| a6b9e459343ea5928713ddb5595b585357e72c36 | 2026-07-30T17:21:43+09:00 | main | Merge: 랜딩 재작성 + 리포트 결함 3건 수정 |
| 763e904616592a450078da472fe68326b3ad7fb0 | 2026-07-30T21:11:09+09:00 | main | style(site): 랜딩 색·리듬·모션 — 흰 회색 문서에서 브랜드 면으로 |
| b879feaed5d5af88233868dfefefc6d6bb64f83a | 2026-07-30T21:11:27+09:00 | main | Merge: 랜딩 색·리듬·모션 조정 |
| 2f3d183b307f0a38721a9be8a967c0ab743df1ef | 2026-07-30T23:26:52+09:00 | main | fix(site): 랜딩 카피에서 반복되는 수사 제거 — 기계가 쓴 글처럼 읽히던 원인 |
| ae022f7312bbd10628ece064a8d63dd2137ebf9a | 2026-07-30T23:27:04+09:00 | main | Merge: 랜딩 카피 슬롭 제거 |
| 78da15b82eecd49f43ef7deae8fc7bb81e167970 | 2026-07-31T00:21:44+09:00 | main | feat(leadgen): 자리 리셋을 KST 자정 → 08:00으로 |
| c12770a10530dcc90b9613b8f9b21171ad37d88c | 2026-07-31T00:22:11+09:00 | main | feat(site): 히어로를 대표 문안으로 + 굴러가는 AI 로고, 섹션 11→9 |
| 9c7d1b8654257565771f9f914971f64d3f7e05b1 | 2026-07-31T00:22:25+09:00 | main | Merge: 히어로 대표 문안 + 자리 리셋 08:00 |
| 2a3343813acf59a2427aca142150ecd4ffdea9f5 | 2026-07-31T07:01:46+09:00 | main | feat(site): 랜딩에 움직임 — 질문 마퀴 · 스크롤 고정 시퀀스 · 자동 순환 |
| f6a8c054f4fbddabf836ef402d9dd6805285e9d3 | 2026-07-31T07:01:59+09:00 | main | Merge: 랜딩 움직임 — 질문 마퀴·스크롤 고정 시퀀스·자동 순환 |
| 030f6746a5cdf71a207f38d3a9492c9e5f36c9e9 | 2026-07-31T07:54:52+09:00 | main | fix(deploy): 시크릿 "없음"과 "조회 실패"를 구분 |
| 197102bda9d74a0207889b96528cc2870a52230e | 2026-07-31T12:03:00+09:00 | main | Merge: 배포 사전 검증이 시크릿 부재와 조회 실패를 구분 |
| d5ddea74c53ec2ba1fcf60a58f3bbb31daf80e55 | 2026-07-31T12:18:33+09:00 | main | feat(site): 랜딩 밀도 — 여백을 깎고 시퀀스를 다시 짠다 |
| eec9f5942c5d16ed21cfa2e330f4d4ba67564f4f | 2026-07-31T12:18:41+09:00 | main | Merge: 랜딩 밀도 — 여백 축소와 시퀀스 재설계 |
| 7b736d0355c4d39e54a7e6d2e233fe97ca623461 | 2026-07-31T12:33:16+09:00 | main | feat(site): 텍스트를 덜고, 움직임을 더한다 |
| de16a85517a0f2b8f77d289b0b7f49a7c985340c | 2026-07-31T12:33:26+09:00 | main | Merge: 랜딩 — 텍스트 절제와 인터랙션 마감 |
| c2a068f274d460834c334f31198f30ef7cf5801a | 2026-07-31T12:45:56+09:00 | main | feat(site): FAQ를 접는다 — 넷만 세우고 일곱은 뒤로 |
| 20394564632b01a2b8c96498a6372e98ab582220 | 2026-07-31T12:46:06+09:00 | main | Merge: FAQ 접기 — 넷만 세우고 나머지는 뒤로 |
| fe3dea784d0f203f45d9407e26c412fa47f9b4a0 | 2026-07-31T13:05:18+09:00 | main | feat(site): 파랑에 뜻을 주고, 축을 하나로 — 디자인 리뷰 반영 |
| da7f2a5ec29ea88b3256c4dd530ff318f394f43a | 2026-07-31T13:05:29+09:00 | main | Merge: 디자인 리뷰 반영 — 파랑의 의미 통일, 축 정리, 산출물 가독성 |
| f301c42dee7f399c3092bb28be1002edffd60e83 | 2026-07-31T13:18:09+09:00 | main | fix(site): 롤링 AI 로고가 한 바퀴 돌고 선다 |
| 9f0fb52992d111b1a17f5fe72db48c9d7f4f9c7a | 2026-07-31T13:18:19+09:00 | main | Merge: 롤링 로고가 한 바퀴 돌고 정지 |
| 1200c3d8088f8a51011a776fcb4e0aaa9142dc13 | 2026-08-01T15:34:59+09:00 | main | fix(worker): 일괄 근거 추출이 큐잉하던 태스크가 없었다 |
| 7d306cec639e43ff80076c555768b91612e2fa28 | 2026-08-01T15:37:36+09:00 | main | Merge: 큐잉만 하고 실행되지 않던 일괄 근거 추출 |
| 1d88b832270004def29bdcf2a91174665cb47e7f | 2026-08-03T10:49:33+09:00 | main | feat(site): 접힘 위에 계기판을 세우고, FAQ 답변의 박자를 흩는다 |
| c4344968381d693ae0e0e165032237ac79474bd0 | 2026-08-03T10:49:47+09:00 | main | Merge: 랜딩 계기판 + FAQ 카피 박자 |
| c7a45244449e8ea2656c9762004c02da689d79e2 | 2026-08-03T10:53:05+09:00 | main | Merge remote-tracking branch 'origin/main' |
| 9c6deacb39e540a8763b5d63bb42be915c8df12d | 2026-08-03T11:13:00+09:00 | main | fix(site): 넓은 화면에서 갈라져 있던 좌측 기준선을 하나로 |
| 72be61b513667264e9b181b9e83e39c3038c8a89 | 2026-08-03T11:13:14+09:00 | main | Merge: 넓은 화면 좌측 기준선 통일 |
| 7a0accb49f477c74948e7e830004802e6a49a6d4 | 2026-08-03T11:23:15+09:00 | main | fix(site): 장면 섹션의 제목-카드 사이 빈 구간 349→45px |
| 02207c7b0b843c37978e5b50d4cb84692c72df17 | 2026-08-03T11:23:17+09:00 | main | Merge: 장면 섹션 제목-카드 간격 정정 |
| 7febc5273d3ad45c34fa28031c3f891bdcbbc321 | 2026-08-03T11:44:00+09:00 | main | feat(site): 장면 섹션을 시간 기반으로 + 섹션 문안 대표 교체본 반영 |
| 4377c3ec67296103ccaa67763918456ab181fb57 | 2026-08-03T11:44:02+09:00 | main | Merge: 장면 섹션 시간 기반 전환 + 섹션 문안 교체 |
| c4e2f12d38e5327a3ee60a5d40ec64f3645b47bd | 2026-08-03T11:57:06+09:00 | main | fix(site): 레이아웃 폭을 1440으로 고정하고 헤더를 본문 축에 맞춘다 |
| 0ec31d3780feced3548e7adab05da9e81c2eed90 | 2026-08-03T11:57:08+09:00 | main | Merge: 레이아웃 폭 1440 고정 + 헤더 축 정렬 |
| 8315516466126c1bb6e783181bd88d6b402517e6 | 2026-08-04T10:19:29+09:00 | main | fix(site): 넓은 화면에서 히어로 카피가 한 글자 폭으로 짜부라지던 문제 |
| 30612121e66a3b819ac12790b0b57a12dbdaac0b | 2026-08-04T10:19:41+09:00 | main | Merge: 넓은 화면 히어로 카피 짜부라짐 수정 |
| 9f4a35af475dd66fae0b4907e1e2119488e8b73b | 2026-08-04T18:09:30+09:00 | main | feat(ops): AE가 개발자 없이 운영하도록 — 계정·비용·리포트 자립화 |
| 31cf02b2e61ea3580bf7b719a157fc31c569fdfb | 2026-08-04T18:09:48+09:00 | main | Merge: AE 자립 운영 — 계정·비용·리포트·확인 큐 |
| 6f7e310dd4b777ce6f102f64b85fe75e38c84203 | 2026-08-04T19:30:31+09:00 | main | fix(site): 랜딩이 스스로를 반박하던 문구 넷 |
| e94696ce32a4e6401e25d174eb7a475d7b9e3c1c | 2026-08-04T19:30:40+09:00 | main | Merge: 랜딩이 스스로를 반박하던 문구 넷 |
| 7bb39cafd15f8cee4516a0c36b51c33327f93aed | 2026-08-04T20:12:57+09:00 | main | fix(site): 랜딩 접근성·숫자 정합·죽은 CSS |
| 2df59ae89fd414b0fc85149de6023249e909a7a8 | 2026-08-04T20:13:07+09:00 | main | Merge: 랜딩 접근성·숫자 정합·죽은 CSS |
| b4421e923dd20a3e2c13e2fbcc947f6265a81a91 | 2026-08-04T21:17:02+09:00 | main | fix(site): 랜딩 P0 — 폰트 2MB·모바일 헤더 잘림·히어로 설명 부재 |
| 9136a5e3baff450e1532662622711f0059b28173 | 2026-08-04T21:17:27+09:00 | main | Merge: 랜딩 P0 — 폰트 2MB·모바일 헤더 잘림·히어로 설명 부재 |
| 874ffba44c9700c252f6ec1c8f1a94db0c40d961 | 2026-08-04T21:34:29+09:00 | main | fix(site): 리포트 미리보기를 FAQ 앞으로 + 사이드 제목 그리드·틴트 가시화 |
| a8f18435027bb009103fdd4d2f6eb4fb647c10a4 | 2026-08-04T21:34:29+09:00 | main | Merge: 리포트 미리보기 순서·사이드 제목 그리드·틴트 가시화 |
| 905e2cdc1600a78b1fbab5273c5f2cbbbe0af953 | 2026-08-04T22:00:20+09:00 | main | feat(site): 랜딩 히어로에 브랜드 라인 아트 — 병원 표면과 같은 시각 언어 |
| e37e74d36581e3742c9060c10b6c71dc808a25ff | 2026-08-04T22:00:20+09:00 | main | Merge: 랜딩 히어로 브랜드 라인 아트 + brand.tsx 공용화 |
| f99e9c03b5d310d28dd19d80406f315f6cd4993d | 2026-08-04T22:12:14+09:00 | main | fix(site): 공유 카드 이미지를 생성 목업에서 실제 리포트 지면으로 |
| 7df66da0484d41f067f3cfbb9769f536a3cdf400 | 2026-08-04T22:12:14+09:00 | main | Merge: 공유 카드를 실제 리포트 지면으로 |
| 3df8b5319672bdea23cc41cd1e3e14259d604673 | 2026-08-04T22:36:39+09:00 | main | fix(site): 리포트 섹션에 자기 면을 준다 — 운영 3단계와 분리 |
| 4b74d18e361e1998be2ba42d34127f827790ef0a | 2026-08-04T22:36:39+09:00 | main | Merge: 리포트 섹션 독립 면 분리 |
| aa6690470b0a7c5b7e29d586b864b88f7f87ffde | 2026-08-04T23:03:13+09:00 | main | feat(site): 히어로 배경에 브랜드 라인아트 루프 영상 |
| 9af5cdc9eb717a98e5d528300f93037daead3245 | 2026-08-04T23:03:13+09:00 | main | Merge: 히어로 브랜드 라인아트 루프 영상 |
| 884f6de13114267d1d46baf70e98af934a92603f | 2026-08-04T23:25:26+09:00 | main | fix(site): 롤링 슬롯이 빈칸으로 멈추던 버그 + 계속 구르게 |
| b6f5343e3acf0cd8155e8b0c8a04d86c6d349cf3 | 2026-08-04T23:25:26+09:00 | main | Merge: 롤링 슬롯 빈칸 버그 + 연속 롤링 |
| 00d87d42859900b14c0cca1a18f1312616d2bc5b | 2026-08-04T23:34:46+09:00 | main | feat(site): 히어로 아트를 실제로 보이게 — 마스크 도려내기에서 흰 스크림으로 |
| 9627f528c4f5121f7f799a16621d05ee38f8ed37 | 2026-08-04T23:34:46+09:00 | main | Merge: 히어로 아트 가시화 (스크림 방식) |
| 11740773ec2c39e798861618b1f1d38f0e04c253 | 2026-08-04T23:47:14+09:00 | main | fix(site): 롤링 슬롯 폭이 현재 칸을 따라가게 — 문장 한가운데 빈칸 제거 |
| 98ebd2acb524c7188e4b3fc61bb109377db84427 | 2026-08-04T23:47:14+09:00 | main | Merge: 롤링 슬롯 폭 추종 |
| 705ff724064516bee3478f46a604dacd9da3ac3a | 2026-08-05T00:43:08+09:00 | main | feat(site): 근거를 우리가 먼저 지킨다 — 구조화 데이터·측정 논증·퍼널 위치 |
| 12a1ba2150b1e49da6f824206f305ee670bf9fec | 2026-08-05T00:43:08+09:00 | main | Merge: 구조화 데이터·측정 논증·퍼널 위치 |
| 29fec529b2e042d9b53f2bb7087fc1d19992ba29 | 2026-08-05T05:52:11+09:00 | main | feat(site): 원장이 구미 당기게 — 개인화 훅 + 요금제 공개 |
| 59f947983aa63d31e238d89e954bba143d6a0788 | 2026-08-05T05:52:11+09:00 | main | Merge: 히어로 개인화 훅 + 요금제 공개 |
| 49280baf80d10f73def748423a9e26bfc737df15 | 2026-08-05T06:59:51+09:00 | main | fix(site): 쓰지 않는 폭을 걷어낸다 — 콘텐츠 축 1200 + 제목 캡 버그 + 탭 타겟 |
| 87f7aa1d4d137beb6e535f1f890a0a84c3d3925b | 2026-08-05T06:59:59+09:00 | main | Merge: 콘텐츠 축 1200 + 제목 캡 버그 + 탭 타겟 |
| dbfcde9b6ebe6b22afabdc9edba9841de821743f | 2026-08-05T09:02:01+09:00 | main | feat(site): 원장이 결정하는 것으로 논조를 옮긴다 — 시점·비용·자기 인식 |
| 18b28c1050dd0920fc6376d825a07e4a047f2f2c | 2026-08-05T09:02:10+09:00 | main | Merge: 논조를 원장의 결정으로 — 시점·비용·자기 인식 |
| 1d0481b70158152a2649de2d6846277461a26bd5 | 2026-08-05T09:19:28+09:00 | main | fix(site): 그림이 문장과 같은 말을 하게 — 자리 도식·제목 폭·히어로 정리 |
| 1d2cfc86d96ac739553a3d3fce5759b6e0ad30a4 | 2026-08-05T09:19:28+09:00 | main | Merge: 자리 도식·제목 폭·히어로 영상 제거 |
| 4a08a9588f0535ee1d5fc02a6802b2a27f9d2d7f | 2026-08-05T09:39:29+09:00 | main | fix(site): 페이지가 열세 개의 균등한 칸으로 읽히던 것 — 밴드 교대 복구 + 히어로 정리 |
| e06a2705d4efdbd6470c2a67f0576f2a9fadc9b0 | 2026-08-05T09:39:29+09:00 | main | Merge: 밴드 교대 복구 + 여백 리듬 + 히어로 아트 제거 |
| c119c2507bc067fa405f0a936062858f73359edc | 2026-08-05T09:53:51+09:00 | main | feat(site): 요금제에 고를 근거를 넣는다 — 투입량이 아니라 우선순위로 |
| f511c47310671b843c840f29884af0204c1d0989 | 2026-08-05T09:53:51+09:00 | main | Merge: 요금제 티어별 추천 문구 |
| a6cd9e71e5cb91fb7e118803751cc83fbd72790f | 2026-08-05T09:59:16+09:00 | main | fix(site): 계기판 각주에서 표본 조건을 뺀다 |
| fbafe560ba0b65b894002a1c2f30c86af6a969eb | 2026-08-05T09:59:16+09:00 | main | Merge: 계기판 각주에서 표본 조건 제거 |
| a2fb4854c995cd785be2cb659622420e56368cfb | 2026-08-05T10:24:41+09:00 | main | fix(site): 파는 것을 실제로 주는 것에 맞춘다 — 횟수에서 "어떤 질문에서 왜"로 |
| b9bc72df4bccf9c2e23a849a550fd3770a2577ff | 2026-08-05T10:24:41+09:00 | main | Merge: 횟수에서 '어떤 질문에서 왜'로 |
| 8d26b31969fb6a635ce977bf21a9694e76d410ff | 2026-08-05T10:42:51+09:00 | main | feat(site): 접힘 위에 받는 것의 실물을 둔다 — 계기판 오른쪽을 리포트로 |
| 4cf0a5ceeed437f1eae762034b56ad31b7f37c13 | 2026-08-05T10:42:51+09:00 | main | Merge: 접힘 위 리포트 실물 |
| f338bfe9877d1ee6bc25a1d233c3e0a7c81f809c | 2026-08-05T11:08:33+09:00 | main | Revert "Merge: 접힘 위 리포트 실물" |
| 24dc852e6b86386e2bffdf2dc3fbe6c0216b70f6 | 2026-08-05T11:21:47+09:00 | main | feat(site): 받는 것을 절반 앞으로, 담기는 것을 글로 — 리포트 섹션 이동 + 강화 |
| 530b76eb34820a5e39d8bb8d8211400d3108c252 | 2026-08-05T11:21:47+09:00 | main | Merge: 리포트 섹션 앞으로 + 담기는 것 명시 |
| 23dd4aa1c5100c18041bd06f505668361b2b4845 | 2026-08-10T04:05:24+09:00 | main | feat(plans): adopt starter grower leader tiers |
| 7e3b570f847fa76164c49c108099985ea73e3582 | 2026-08-10T04:11:01+09:00 | main | feat(onboarding): add legacy-safe handoff contract |
| d19098480ea5966aa5c22c772e66739b06d8c6c3 | 2026-08-10T04:22:16+09:00 | main | feat(reports): add immutable monthly measurement manifest |
| 64eac7bcac5bc8a0373bb3a931e3730af1d8c8f9 | 2026-08-10T04:26:47+09:00 | main | feat(onboarding): require explicit customer acceptance |
| faa73b01ca97397f976c96bfe8bde6fdef3d8218 | 2026-08-10T04:33:09+09:00 | main | fix(reports): harden monthly manifest versioning |
| f5da32bffa5e022833a1d9f2d58bbfb0a95021d7 | 2026-08-10T04:37:56+09:00 | main | fix(reports): refuse destructive monthly downgrade |
| a21e28a7240ae39145e0100817539fe703ea08f1 | 2026-08-10T04:40:38+09:00 | main | fix(onboarding): secure handoff acceptance cleanup |
| e9574e1fd3a291587765e9e277a564f3699bc5a9 | 2026-08-10T04:53:20+09:00 | main | fix(onboarding): enforce accepted scheduled activation |
| 81ad5ae1af8fd5d9cab00251aeac81e4d8feccef | 2026-08-10T04:55:12+09:00 | main | fix(reports): bind delivery to validated doctor artifact |
| 4d53fd51a6d6bffa304026dd880a1aec5b3f548f | 2026-08-10T04:58:19+09:00 | main | fix(onboarding): serialize service interval transitions |
| 97c2bedf9fba5c27f396e8d2c56d1000246ccb93 | 2026-08-10T04:59:52+09:00 | main | fix(reports): enforce delivery tenant authorization |
| 26344a2a115c927ea7bccfb3fac12012f1a90c49 | 2026-08-10T05:11:35+09:00 | main | fix(admin): proxy customer handoff routes |
| 54ddd739d894343498ce8cc684db60962ef26558 | 2026-08-10T05:19:47+09:00 | main | fix(admin): align onboarding with activation gates |
| f6cd768d707fd525250a10faedd64e1cabf0d493 | 2026-08-10T05:27:51+09:00 | main | fix(admin): clarify report delivery artifacts |
| 647af74e43b7d384565f54fd287ba657a6bc33b5 | 2026-08-10T05:53:32+09:00 | main | fix(admin): prevent mobile report header orphans |
| 644453dd78ece133d5e424e371f144037befc1ed | 2026-08-10T05:58:26+09:00 | main | feat(operations): add durable control-plane schema |
| e50fd678ed7ef482ff1bd96597be46fc301e0a17 | 2026-08-10T06:07:59+09:00 | main | fix(operations): clear terminal outbox retry schedule |
| bf993dd8e51f3570a843840e5408e66d95a5cb5e | 2026-08-10T06:17:52+09:00 | main | feat(operations): add concurrent incident lifecycle |
| c5bb1279e76d922592dc4d1601d4bf9738334a6a | 2026-08-10T06:43:38+09:00 | main | feat(notifications): add durable Slack outbox |
| f836d062d58b9dc4a8cbe2128030dff6af631d57 | 2026-08-10T06:50:39+09:00 | main | feat(operations): add durable command lifecycle |
| 4e69be2d47c703c1a7e792dc5458c51c6768b1ce | 2026-08-10T07:24:47+09:00 | main | feat(api): expose unified operations center |
| 928c4c21558c5866bdf23cfa1738d5f3d7da6dfb | 2026-08-10T08:03:34+09:00 | main | feat(content): make generation recovery observable |
| 80dc8d7cf4ac1a8402e046078499daf14a14bca6 | 2026-08-10T08:21:12+09:00 | main | feat(operations): project onboarding and reports to Slack |
| 0927e0f6036b69fc6029e98e5ed61abde9067db4 | 2026-08-10T08:27:25+09:00 | main | fix(admin): clarify onboarding due-date copy |
| 0c508b6d2152e35612a2692c89fbbaffcea63f88 | 2026-08-10T08:40:54+09:00 | main | feat(leads): add terminal diagnosis recovery |
| 7420ca1ccd1d32f81f9d3cde99016a2d1b4e5d8c | 2026-08-10T09:01:36+09:00 | main | fix(operations): make incident guidance actionable |
| eefda8281244f5fded3179f5e9bcd20d2a44a455 | 2026-08-10T09:04:34+09:00 | main | feat(admin): add unified operations center |
| 80697de07af566297d6ad4b5e6f3e504349c5b24 | 2026-08-10T09:08:39+09:00 | main | fix(content): unify publish notification recovery |
| 2bf92daea4259237ce62604a43413317fc926430 | 2026-08-10T09:28:53+09:00 | main | fix(admin): forward recovery idempotency key |
| a93358f47ecb84c25cc322fd1266617a4523450c | 2026-08-10T09:37:19+09:00 | main | fix(leads): clarify diagnosis recovery dialog |
| 56216409f89350aaec775f3e62a4a77450ea21f6 | 2026-08-10T09:40:06+09:00 | main | feat(evidence): make Naver handoff recoverable |
| 3b6449c85fc814d5b19800662b6d78ade812aed0 | 2026-08-10T10:09:08+09:00 | main | feat(reports): make monthly recovery observable |
| 06730c0644ad15ad281970f61200f3fa0102058a | 2026-08-10T10:24:26+09:00 | main | fix(operations): verify worker recovery |
| 3e923ffaed48617a0c619a38b2396c2419e28b64 | 2026-08-10T10:28:40+09:00 | main | test(operations): drop unavailable lead route assertion |
| 7a2439b664b7f70c28fa9b6ce02abda1daccadd8 | 2026-08-10T10:25:03+09:00 | main | fix(operations): persist domain and dependency degradation |
| 12447e8fbecc7845181869634cde44b8c10d34dd | 2026-08-10T10:47:22+09:00 | main | fix(reports): close monthly periods after boundary |
| 8c5689c4f11025615ea719363efaa80584bce663 | 2026-08-10T11:20:38+09:00 | main | fix(metrics): use fixed-manifest platform macro SoV |
| 6f61fdce789fea49f834f6b50cf69cbc8f4cb011 | 2026-08-10T11:49:50+09:00 | main | fix(reports): require measured baseline for new mentions |
| 574dce3bca2af03c23c5ec84b2d747dc95998e5c | 2026-08-10T12:50:18+09:00 | main | test(operations): stabilize onboarding projector timing |
| 89448d90ac9bdd75596070ac6202caf34f239c1d | 2026-08-10T12:53:50+09:00 | main | feat(reports): validate doctor PDF artifacts |
| 87b8cc06270ed814d651f84312570bffa5ba5817 | 2026-08-10T13:44:36+09:00 | main | feat(reports): harden review and delivery workflow |
| 40e48b250cd517acaa0e156fcc6539d370784f56 | 2026-08-10T14:05:51+09:00 | main | fix(notifications): harden operator copy and links |
| fe908ee53632ba785f342b738daa66cc82b752d4 | 2026-08-10T14:06:40+09:00 | main | fix(notifications): harden legacy Slack alerts |
| 2b41d47891ba501eff1229db9d632a6a57a249c7 | 2026-08-10T14:46:54+09:00 | main | fix(migrations): sort artifact validation imports |
| 5124021d3060550eeaa8a15ab58aa75781be6c9b | 2026-08-10T16:35:20+09:00 | main | fix(slack): harden operator-facing alerts |
| 3a22851452d9804641c4b09b65164353f63d7615 | 2026-08-10T17:21:25+09:00 | main | feat(operations): complete marketer control journey |
| 71bd19174e1b4e8ac8e415c29a93e75edee5125b | 2026-08-10T17:30:29+09:00 | main | fix(notifications): make nightly content summary actionable |
| b5b612eeb836d840fe77643fe79f8816cbf74238 | 2026-08-10T23:33:46+09:00 | main | feat(operations): close autonomous recovery loop |
| 16dc4b20f882732453c0a91d9551f439080170fa | 2026-08-11T00:02:13+09:00 | main | feat(site): clarify free diagnosis journey |
| a584edcd2aa2fd8f69f82fd13a49c2f4c366d27a | 2026-08-11T00:02:22+09:00 | main | chore(deps): refresh nanoid lock resolution |
| 99bb27716be82ced72db1f67150a56ad8ca1e608 | 2026-08-11T00:03:06+09:00 | main | docs(audit): preserve landing and report evidence |
| f76628f1b4ec4ebfcb83b21fb468dffdcd266ee1 | 2026-08-11T00:11:56+09:00 | main | fix(deploy): pass release revision to migrations |
| dadb7dba7dcac8f600d7f99a3c5175a260fbc4d4 | 2026-08-11T00:23:45+09:00 | main | fix(workers): align queue canary dispatch purpose |
| 0db07aa822de98694fe03d985bf8f7a15a1ec1f2 | 2026-08-12T09:13:54+09:00 | main | fix: make operations alerts exception-driven |
| b3754e1b24ae48472e91857c1cf9aa556dc77f96 | 2026-08-12T09:44:15+09:00 | main | fix(ci): provide integration test services |
| 0ea44df2c7f293340be68d2684ad944d249d10d6 | 2026-08-12T09:50:04+09:00 | main | fix(outbox): persist explicit retry schedule nulls |
| 915e6748bfc65c0d4e601a76d818125af5decb12 | 2026-08-12T09:54:33+09:00 | main | Merge pull request #19 from wjlee930501/agent/exception-driven-alerts |
| 60fb88e17b9be3d3920919092336447bbb178c9c | 2026-08-12T13:46:06+09:00 | main | feat: close the autonomous content operations loop (#20) |
| 0bf9fbc5680826fada896ce34950e53377af9c7e | 2026-08-12T14:03:59+09:00 | main | fix: preserve signed tasks across release handoff (#21) |
| 40f504976662221f5f25d28eb2de27e2a9966d95 | 2026-08-13T12:45:07+09:00 | main | fix: remove upward bias from AI mention measurement |
| 73b9ec8057f57ec22334aa3f900dd829f8037da6 | 2026-08-13T13:31:25+09:00 | main | fix: close AMBIGUOUS aggregation gaps found in post-deploy review |
| 7d19ca06aa1f2e8bb7b9f64f60bcc4415b0389ce | 2026-08-14T01:17:09+09:00 | main | feat: instrument search usage and fix prompt-role reproducibility gap |
| 7628fb1527658e18baf25ba9b4959de593dfa725 | 2026-08-14T01:49:43+09:00 | main | fix: stop generating ungrammatical diagnosis queries |
| f74d9fd0256f583574bc084d741fdd762ee95e00 | 2026-08-14T02:04:59+09:00 | main | fix: align paid query matrix with the free diagnosis standard |
| b6831e17b9dd8fd3c1c44599993012ac9ba2b9ac | 2026-08-14T02:13:17+09:00 | main | fix: stop duplicating the institution word in specialty queries |
| 7f88bfc2b56a7d95bfa7cd8b9fd985eb250b1b94 | 2026-08-14T02:39:20+09:00 | main | fix: do not reseed query targets from deactivated matrix rows |
| e7f7dd6ae8b48e209098d7b0cd46e1e086da44ca | 2026-08-14T02:46:30+09:00 | main | fix(ci): sort imports in new migrations |
| f1a999e63e9182185b6ecf82004fdedc1f1a9471 | 2026-08-15T11:10:26+09:00 | main | Make unattended operations trustworthy and measurable |
| a58133147761bbfc5813a1bdbf8f2faffc544c88 | 2026-08-15T11:15:07+09:00 | main | Keep request-size tests deterministic across Node runtimes |
| e6789404f7d1d4bd51ced92fd84fdf99fef9863d | 2026-08-15T11:19:11+09:00 | main | Merge pull request #22 from wjlee930501/agent/automation-measurement-hardening |
| 14d2a84602f02359542878a4894b41131fb6ed91 | 2026-08-15T15:04:45+09:00 | main | Harden onboarding and autonomous operations |
| 98d0d4ddc33caa2401afb6aba6a36284f097de5d | 2026-08-14T23:09:27-07:00 | main | Merge pull request #23 from wjlee930501/agent/onboarding-autonomous-operations |
| 50e1537d725edbf8b108b133ed8653e7c743bd8b | 2026-08-15T19:18:23+09:00 | main | Keep monthly reporting accountable to actual operations |
| e7aa4fb40bde8f57ccf2da89f7d5e589f00540cb | 2026-08-15T19:18:39+09:00 | main | Complete production reporting ops and monthly measurement flow |
| 8a8631ab185a1242977b7d1aa1e265d4f17fb580 | 2026-08-15T19:18:40+09:00 | main | feat(operations): complete reporting and operations hardening |
| b4493214970c44c117e386781b07b5b048942b1a | 2026-08-18T00:20:03Z | main | fix ops alert episode idempotency |
| 960cde9e03ffdcac0c3506cea73cdb62bcdae9d2 | 2026-08-18T00:23:45Z | main | Keep Slack for human-now generation states only. |
| 94e68fd8a2e0c411260ee819be7f791af10c53cb | 2026-08-18T00:24:27Z | main | Revert Slack-gating inversion that silenced actual generation failures. |
| ef0fd0acb598d055976e796c6959a4572e14c4a6 | 2026-08-18T00:25:53Z | main | Revert "Revert Slack-gating inversion that silenced actual generation failures." |
| 53c8925e34a422394f0e5b374300838c71d0028e | 2026-08-18T00:37:29Z | main | Apply Fable 5 notify gates and 48h expected-pending promotion. |
| 5759128ac39ca92cc6f22287e819a7658a1f604b | 2026-08-18T00:44:08Z | main | Reset episode start on reopen so 48h promotion can fire again. |
| 9039e7f727a2b6941fe1285942088b23d31c0cdd | 2026-08-18T00:49:37Z | main | Skip stale promotion on reopen and lock first_seen_at to the new episode. |
| 392c8da4c224706f47bd8079670fc64311b7c170 | 2026-08-17T17:52:10-07:00 | main | 운영 알림 episode 멱등성 / human-now Slack only (#25) |
| d124357af1de7e13ab63f83b0554d1f999fa05f9 | 2026-08-17T20:32:19-07:00 | main | keep approved hospitals generating after weekly naver PENDING ingest (#26) |
| 7d18dae8598965add5bc714a9dd0ef29acf6796e | 2026-08-18T15:26:39+09:00 | main | fix content generation recovery and image pipeline |
| a8946dc0edbe086c84de25e2b384e26c56ec36ed | 2026-08-18T15:37:24+09:00 | main | fix topic-specific medical references |
| 27a5929c430e57bd968d78dd0ee22d7c26e5669d | 2026-08-18T15:53:30+09:00 | main | harden content generation and queue recovery |
| d5e724c3d0c898ea7e2d28021d536df3c9fb7c0d | 2026-08-18T16:05:00+09:00 | main | block unsupported medical pricing claims |
| c50a11de632577f71ebd225538b5ba1624e428f9 | 2026-08-18T17:15:24+09:00 | main | use approved guide topics for medical references |
| ae97855947e162fc794ebd34e22929d7cd0dcfcf | 2026-08-18T18:22:00+09:00 | main | Keep content operations moving through safe AI recovery |
| c8acbf3c81ee942764223d8c3ca013a11a8f6161 | 2026-08-18T18:48:15+09:00 | main | Recover safe Essence candidates without operator blockers |
| d6f6a4b7a39f1b9e7cde2afbb12985b048983e48 | 2026-08-18T19:08:51+09:00 | main | Remove system-authored blockers from Essence fallback |
| 3bd80b9d971486d25cb4b562b73037bc2742ccef | 2026-08-18T19:26:53+09:00 | main | Stabilize autonomous Essence review |
| 5b9820c0faf07180ee3ad9eae9cc25db311f5462 | 2026-08-18T20:59:27+09:00 | main | Keep autonomous Essence recovery within safe bounds |
| 9f11b0f2550a6faf3648137d201294206c045fba | 2026-08-18T21:36:17+09:00 | main | Add independent AI adjudication for Essence blockers |
| 82e12096ab1e92981518c598b66ece2eb47f2415 | 2026-08-18T21:48:22+09:00 | main | Use Anthropic-compatible Essence schema |
| 96302be970b7d2e9281ce6fbe340e81ca7c5fddf | 2026-08-18T22:05:36+09:00 | main | Bound Essence structured output budgets |
| 3db1e6063204b4fe9104c23027902f17885cf80f | 2026-08-18T22:23:34+09:00 | main | Compact Essence synthesis output |
| 7eaf0dd3b3c6a6572f41ee434d9b18974cdf8038 | 2026-08-18T23:04:46+09:00 | main | Automate initial Essence approval |
| 157440a5348ec44d76181e008d423f8c4ebc22b3 | 2026-08-18T23:13:42+09:00 | main | Correct initial Essence reviewer typing |
| 9d7848d994f9e62ebcc3cc5f5a2c40962cd9097c | 2026-08-18T23:18:41+09:00 | main | Reject unverified admin actors by default |
| 0d2a8d40610c04738197df8242d3281a3f0a326f | 2026-08-18T23:25:16+09:00 | main | Hide obsolete Essence drafts from review queue |
| e339c9b83c9ab4dd693f0c3474cf6f88439d5468 | 2026-08-19T04:36:31+09:00 | main | Keep hospital onboarding truthful under partial failures |
| e47d52931ed05a05f13de87fdd3b43ab451326b6 | 2026-08-19T08:05:10+09:00 | main | Harden onboarding provider and content fallbacks |
| 9dee40f4dc5aee3f64ebae3d37a5814dc3583824 | 2026-08-19T08:19:09+09:00 | main | Normalize partial V0 operator summaries |
| 2b85317a5a510d313a54a63f108f65e6e7391c23 | 2026-08-19T17:05:23+09:00 | main | chore(merge): connect ops alert episode idempotency |
| 07a82732aae63ddc8ac62bbf557370344f9c2bac | 2026-08-19T17:05:26+09:00 | main | Merge remote-tracking branch 'origin/codex/onboarding-qa-fixes' |
| e175bc2a094679e3fc356206cde4bab549db6139 | 2026-08-19T05:39:07-07:00 | main | Keep approved publishing operational without Slack floods (#27) |
| 7fd1218b6bebb51c8b14bcc1fc359a9b36715d88 | 2026-08-19T14:16:22-07:00 | main | Stop matching 수원 as an unverified price (#28) |
| 650a07ad158cf809daf5b940267c6d9ee4194976 | 2026-08-19T21:00:26-07:00 | main | Publish onboarding photos in one save and show them on visit (#29) |
| 58a4e569bc0d594d80a5895299430f967c9b598a | 2026-08-19T22:45:29-07:00 | main | feat: multi-file photo upload with batched revalidation (#30) |
| a9a79ace0ea4de9f9e4bd0aad168cbd258ac6af1 | 2026-08-20T01:19:53-07:00 | main | fix(admin): keep onboarding progress trustworthy (#31) |
| ee8ad197e0667aaf6e732cd3277cf7bc7e8af121 | 2026-08-20T07:03:26-07:00 | main | fix: keep in-flight V0 visible and refuse duplicate retries (#32) |
| c9ca0a95d237387a27fd695094190a14a307d6e5 | 2026-08-20T14:57:24-07:00 | main | Fix Re:putation hospital onboarding step 5 (custom domain cert blocking) (#33) |
| 8c1c7aaa643ddf9b1b1471a70a37a43842bbc572 | 2026-08-21T08:53:43+09:00 | main | fix(domain): make certificate issuance rollout-safe |
| 4f0f224dec0ee9adb5fcb1d0d566a08561824a06 | 2026-08-21T15:03:34+09:00 | main | feat(admin): add clinic visual identity controls |
| 1b69fce78824836cb11ffb22dd656aed757ee5a0 | 2026-08-21T15:03:49+09:00 | main | feat(site): apply hospital-aware visual system |
| 96039c7e840ed6907a14cc7a358539fdf7bfca06 | 2026-08-21T15:04:42+09:00 | main | docs: record multi-clinic visual QA |
| 8e52b4d399744f128396ad9fac0cbaeaf68af779 | 2026-08-21T15:09:20+09:00 | main | fix(admin): keep image guidance operator-facing |
| 1f97c0d5a16df88f718d83abc75cc1a7e422f176 | 2026-08-21T17:59:28-07:00 | main | fix: 병원별 시각 요소를 기존 온보딩 8단계 안에서 승인하고 공개 표면에 실제로 반영 (#35) |
| a9b3006eb5f8c4da698b269f4ba6b1a4b804d5e3 | 2026-08-21T18:44:42-07:00 | main | 병원 시각 부분 커스터마이즈 마무리: 저장 실패·공개 계약 누락·사진 분류 차단 해소 (#36) |
| 8ad0f2b3fa52c84d8440567b76f8283aa38f30c6 | 2026-08-21T21:29:02-07:00 | main | fix(alembic): 프로덕션 스탬프(0054)와 어긋난 마이그레이션 이력을 단일 선형 체인으로 복구 (#37) |
| 357037a616fa28a14b73d68165f7c9534f3e82e0 | 2026-08-22T05:51:19Z | main | docs(review): #27–#37 웨이브 읽기 전용 코드 리뷰 — BLOCK (공개 사진 provenance P0) |
| baeb61bb75f6b4bbecad3d44e91eda7fd0d64a60 | 2026-08-22T06:09:11Z | main | fix(v0): 세션 advisory 락이 오류 경로에서 커넥션에 잔류하지 않게 한다 |
| d4b84a605872418e7b6c42fb510f5e389db6ebce | 2026-08-22T06:22:40Z | main | fix(photos): 공개 사진에 권리 근거를 기록해 저장·재공개 경로를 되살린다 |
| 2c414d06054e659cb4d3caf27c101e060c048c22 | 2026-08-22T06:26:47Z | main | fix(onboarding): 시각 요소 입력이 5초 폴링에 사라지지 않게 한다 |
| 5b1a9bea97090cf5c8d8966706ae097d71fca7f9 | 2026-08-22T06:39:25Z | main | fix(cert): 인증서 발급 예산을 실제 발급 시간에 맞추고 영구 실패를 알린다 |
| a2a5fdda87288ae9f5e3f8b4391e22cd823f1df0 | 2026-08-22T07:10:07Z | main | fix(photos): 공개를 요청한 사진 업로드는 근거가 없으면 422로 돌려보낸다 |
| 31571690b99bd6587fa4bd17d11afd010cc36893 | 2026-08-22T07:10:17Z | main | fix(domain): 인증서 리스가 만료되면 재확인 버튼이 새로고침 없이 풀리게 한다 |
| c086a6961b29cfba614932eb3f476856770a1b49 | 2026-08-22T07:10:17Z | main | fix(onboarding): 병원이 바뀌면 시각 요소 폼을 새 병원 값으로 다시 채운다 |
| e80f898bc82c26d2693ecb100d5c2c1b62d443e1 | 2026-08-22T00:30:09-07:00 | main | Merge pull request #39 from wjlee930501/cursor/fix-wave-27-37-blockers-a25a |
| 11213a916f15ed9a6cdf8f8a7505917899c315b1 | 2026-08-22T15:10:00Z | main | fix(exposure): 성공 측정이 있는 병원의 미측정 질문을 '성공 측정값 없음'으로 진단하지 않는다 |
| 63f0e19bd55e653c452152d0cc7b6bc2d52c9606 | 2026-08-22T15:22:43Z | main | fix(domain): 실제 응답으로 도메인 배지·트래커를 갱신하고 마지막 확인 시각을 남긴다 |
| e9c827b2f7c1766d181e3e9f53e82daa86b7e055 | 2026-08-22T15:26:25Z | main | fix(essence): 항목별 근거 연결을 실제로 불러오고, 못 불러오면 승인을 막는다 |
| d2e523fc88e7066f5ef83ac03d86d23f08af1efa | 2026-08-22T15:29:35Z | main | fix(visit): 진료 안내 페이지에 요일별 진료시간 표를 넣고 자기 링크를 없앤다 |
| ae9f4a1b3a15a8932ed5e7fae3889d395bd7de88 | 2026-08-22T15:36:21Z | main | fix(leads): 상담 요청 전환이 이미 등록된 병원을 다시 만들지 않는다 |
| 1be1c187f9a9a0b79a1abca716ff4e83f6f1bb67 | 2026-08-22T15:37:18Z | main | docs(review): 2026-08-22 Admin·공개 화면 리뷰 기록 |
| f975392501237c73b7e5d6a2a6191ca25164d977 | 2026-08-22T16:11:45Z | main | fix(review): 교차 리뷰 BLOCK 4건 — 도메인 관측 범위, 리드 자동 연결 조건, 진단 정렬, 근거 검증 |
| d06a54db8e75a8cdf96d628bbaaaaa6c5607e18f | 2026-08-22T16:41:30Z | main | fix(review): 인증서 발급 중·실패를 관측이 가리지 않게 하고, 근거 노트 출처를 보관함으로 단일화 |
| da4d052b924531210ca29c0b9f0ce7e21ef434cb | 2026-08-22T10:18:34-07:00 | main | Merge pull request #40 from wjlee930501/cursor/wave1-blocker-critical-fixes-b0b1 |
| 21ea52721f24c11418c0be858f08100ccc3051d3 | 2026-08-22T21:23:53Z | main | fix(onboarding): 시각 승인과 리포트 PDF가 없으면 온보딩 단계를 완료로 표시하지 않는다 |
| 30bd46adc4817172a4993320a2dba6c83196e6eb | 2026-08-22T21:26:51Z | main | fix(reports): 대상 월 선택에 이번 달을 남기고 마감 전·미래 달은 이유와 함께 잠근다 |
| 1cfa8f08948e46dfe5a01ffb6338f895879f506e | 2026-08-22T21:37:29Z | main | fix(essence): 제외한 자료의 근거를 집계·Wiki에서 빼고 제외 해제를 되돌릴 수 있게 한다 |
| 252790ebc214feeecb63cad2b86a50c40ef48097 | 2026-08-22T21:43:07Z | main | fix(essence): 업로드한 파일마다 제목을 갖고, 올린 뒤에도 사진 제목을 고칠 수 있다 |
| 3d915429243d7c96563817c033e00bf754e6701b | 2026-08-22T21:50:50Z | main | fix(site): 공개 병원 홈 컨테이너 폭을 하나로 모으고 모바일 터치 영역을 44px로 넓힌다 |
| 060442be841b2df4ce9ff489a1447b810a5777b4 | 2026-08-22T22:41:11Z | main | test(site): 컨테이너 폭 테스트 이름에서 금지 용어를 뺀다 |
| cd38b148b39b51d072e1649e8af588e63aa833f5 | 2026-08-22T23:12:10Z | main | fix(onboarding): 월간 리포트 PDF가 초기 진단 단계를 완료시키지 못한다 |
| 5cec673713d7b63c5aa8438f1eb59a08483e301f | 2026-08-22T23:12:22Z | main | fix(site): 모바일 터치 영역을 세로만이 아니라 가로까지 44px로 보장한다 |
| a264284ccf80167220d671fe09a7b27837699dba | 2026-08-22T23:45:33Z | main | fix(site): 44px 하한이 셸 한정 규칙에 밀리지 않게 특정도를 맞춘다 |
| 9a4baeab7c99dc72746420be6293147f14f8f458 | 2026-08-22T17:16:26-07:00 | main | Merge pull request #41 from wjlee930501/cursor/wave2-high-top-fixes-f2b1 |
| 50c3f54057bf0d579438eebf013e633870d59737 | 2026-08-23T03:29:22Z | main | style(site): 공개 표면의 시각 시스템을 토큰으로 수렴한다 |
| cf74698301151dd5c6a84f3bcc0a05fd51fcaeef | 2026-08-23T03:29:43Z | main | fix(site): 공개 화면이 병원 사실을 실제로 말하게 한다 |
| 2e5b2f147e1b18bf86042f371739888915964725 | 2026-08-23T03:45:12Z | main | fix(site): FAQPage에 승인된 질문·답변만 내보내고 Q&A 주인을 하나로 둔다 |
| d230f78a6dcdf761484b520bf7db670fd61cf668 | 2026-08-22T20:58:48-07:00 | main | Merge pull request #42 from wjlee930501/cursor/wave3-public-site-fixes-26c5 |
| d7a28d70f2aedd38e5582c449a21b8fe4f4b5b4b | 2026-08-23T04:01:11Z | main | fix(dashboard): 측정 로그·질문 개수·주간 추이·보완 작업 집계를 한 어휘로 모은다 |
| 941b1f000277fb95fefb109899e09cf579c7b7db | 2026-08-23T04:10:53Z | main | fix(essence,wiki): 사진을 근거 집계에서 빼고 승인자·확인자를 실제 계정으로 표시한다 |
| 255d8e2f887689eaa1c5bd66730850b57d8fdabe | 2026-08-23T04:15:30Z | main | fix(onboarding): 단계 아코디언에 결과물과 승인 대기 초안 알림을 싣는다 |
| 53c85103269881a06654ffdc7083431067b514bf | 2026-08-23T04:21:26Z | main | fix(profile,header): 외부 채널 중복·도메인 안내 번호·헤더 접힘을 바로잡는다 |
| 741b58d4a954d9ad3359f7f3f81d1de30d4bba63 | 2026-08-23T04:27:12Z | main | fix(leads): 점검용 요청을 구분하고 경과·첫 연락 기한을 목록에 싣는다 |
| 12326232439dd36cb5928c6d52a0f07816db440f | 2026-08-23T04:37:02Z | main | fix(operations): 원인 문구·처리 기한·담당자·제목·집계를 실제 사실에 맞춘다 |
| ee168d2bf013f2c7bd9231ddc7a2678552167fb2 | 2026-08-23T04:45:58Z | main | fix(essence): 운영 기준 승인 기록을 확인된 로그인 계정에 묶는다 |
| cf86569808ccc8c0dc51db3c746ae9f29a7f026b | 2026-08-23T05:30:05Z | main | fix: count confirmed measurement results |
| ea6fdc506c6983559277d4e7fe1275495595be7f | 2026-08-23T05:30:05Z | main | fix: retain failed measured trend weeks |
| cae09b8eb63fd4708d0005fc648a157d6911083a | 2026-08-23T05:30:14Z | main | fix: distinguish onboarding artifact fetch states |
| b87385c5300f788dae1cbb6145d2fa52b641ab00 | 2026-08-23T05:30:14Z | main | fix: require verified philosophy approver |
| b8ec87d5dbecf113bfdbd853fbdeb775fd52fd39 | 2026-08-23T05:30:14Z | main | fix: use monthly close for report SLA |
| 36d9ddb7214222ba268a280834e79ef215e87b92 | 2026-08-22T23:09:19-07:00 | main | Merge pull request #43 from wjlee930501/cursor/wave3-admin-fixes-e5ef |
| 867a3b75b0cda668751252472a32a953ec128bd9 | 2026-08-23T07:05:37Z | main | fix(content): add trauma sources for GEO generation |
| f41f21df0c33dbec3f938ef64616c1283226d478 | 2026-08-23T07:25:39Z | main | fix(content): narrow trauma source keywords |
| 71d5de7af023239851683c3e2e62c2a3237bd1b4 | 2026-08-23T16:38:48+09:00 | main | fix(exposure): 큐를 읽을 때 최신 측정으로 다시 진단한다 |
| e0b8b0e5dcdcf7ca44d9e87bf3cc55765f475b87 | 2026-08-23T16:38:59+09:00 | main | fix(reports): 초기 진단을 월간 전달 게이트로 판정하지 않는다 |
| 4441e081df31438500cac2fcc97fc20d8d2b704d | 2026-08-23T16:39:15+09:00 | main | fix(logo): 승인한 로고가 실제로 공개 화면에 뜨게 한다 |
| 4dfad3dfaca7bb937bc662e61b0955181283639a | 2026-08-23T16:39:33+09:00 | main | fix(site): 첫 화면·의료진·컨테이너의 시각 결함을 바로잡는다 |
| 72330274c82601b55d2e7a2189c9dc23884264c8 | 2026-08-23T16:39:45+09:00 | main | fix(operations,leads): 원인 없는 행에 원인을 지어내지 않는다 |
| dee7236e556270a30678183fad3784efd9ac7313 | 2026-08-23T00:53:10-07:00 | main | Merge pull request #44 from wjlee930501/fix/noweontab365-disease-geo |
| 4dfbfa695e2bd619f3ef187c1aa4ea58d24d527c | 2026-08-23T03:02:06-07:00 | main | Merge pull request #45 from wjlee930501/claude/pull-request-804cdc |
| cc0c453382919edb79110f1b033e583b95a26919 | 2026-08-23T22:23:21+09:00 | main | fix(onboarding): 승인이 남았다는 사실을 목록과 배지가 숨기지 않는다 |
| f497ce9a0cfa8ba84809c2f9f31f5a3e4e0f14a8 | 2026-08-23T22:23:49+09:00 | main | fix(admin): 살아 있는 공개 주소를 "준비 중"이라 부르지 않는다 |
| 84dc195419371fa7456274d3b3986e7f590fd61a | 2026-08-23T22:23:49+09:00 | main | fix(site): 눌러도 아무 일 없는 링크와 휑한 첫 화면을 없앤다 |
| 5f6fa67999b8f095a762a5985d5699abde646c36 | 2026-08-23T22:27:01+09:00 | main | style: essence 임포트 정렬을 ruff 규칙에 맞춘다 |
| 214cdcda65ed2679c9b31f0aa8346138e8ce23db | 2026-08-23T07:09:16-07:00 | main | Merge pull request #46 from wjlee930501/claude/pull-request-804cdc |
| c76b63751f51145c95b917fe200d4b690dd72154 | 2026-08-24T05:18:11Z | main | fix profile saves with legacy external logos |
| 4f123b6a818b9c0eae751703e0cbafd66e3b59c1 | 2026-08-24T05:47:13Z | main | fix: normalize unchanged legacy logo URLs |
| 63a541a52794f0781aea9d26802ee7b96d39c174 | 2026-08-24T06:13:08Z | main | fix profile saves with stored logos |
| 1c9cf9aad74bd825ca9253da36ce376f05620514 | 2026-08-23T23:27:57-07:00 | main | Merge pull request #47 from wjlee930501/fix/p0-profile-save-legacy-logo |
| 98d0b72c0a8b5e3c25cc3a88c8291a2563a8f5ca | 2026-08-24T11:09:55Z | main | fix(admin): implement UX wave R1 |
| 5d107187c84bf56ba27fb6881f903810871f26c4 | 2026-08-24T04:24:41-07:00 | main | Merge pull request #48 from wjlee930501/fix/admin-ux-r1 |
| 81ef00762bc752e6ed7f2b982db4134f0feb68e3 | 2026-08-24T11:37:12Z | main | fix(admin): implement UX wave R2 |
| 2ee1873e9d04297f2ccfe8d8428439cc16a4f33a | 2026-08-24T11:42:13Z | main | test: advance migration chain contract |
| f8d5766d16912832c12ebb326d4317bb98d7c75a | 2026-08-24T05:03:06-07:00 | main | Merge pull request #49 from wjlee930501/fix/admin-ux-r2 |
| d26f36785dde869bd9fe4bc84fd743f3ae972535 | 2026-08-24T12:04:33Z | main | wip(admin): persist and expose ops failure cause codes (R3 FN-04) |
| 444b9b5b15eecc92bd4c0427cf80198ebe03dfd8 | 2026-08-24T12:13:04Z | main | wip(admin): wiki crawl encoding/nav filter and note noise UI (R3 3-1) |
| 0eff7b6c726927e82f495b4a4577aa740bd83848 | 2026-08-24T12:33:48Z | main | fix(admin): complete operations and wiki UX wave R3 |
| 32bd419b5e968a768ae208a0188bb38cfd4fdead | 2026-08-24T12:40:17Z | main | test(backend): align incident queue integration contract |
| 0f194f3e8107a9880b292f5c501fb295a73b77d7 | 2026-08-24T12:57:45Z | main | fix(backend): prefer Korean HTML decoding |
| f4869f01bd02ae133f69d3affdf4cf02f6b3512b | 2026-08-24T06:19:21-07:00 | main | Merge pull request #50 from wjlee930501/fix/admin-ux-r3 |
| 124b3b88a5c08b1dd04b0607407e792e633250a2 | 2026-08-24T14:09:28Z | main | refactor(admin): share record type guard |
| a8a08b5de5a653485788d4d169117a6119837dd3 | 2026-08-24T14:09:33Z | main | refactor(backend): share enum value helper |
| e53b22d5c94475050576b6c3314921c981e40c65 | 2026-08-24T07:29:09-07:00 | main | Merge pull request #51 from wjlee930501/fix/non-destructive-refactor |
| a304357ee82341dafe10d4dac8ac60dd47831add | 2026-08-24T20:10:29Z | main | fix monthly short-month slots and suppress essence escalate Slack when approved exists |
| 956da16ad758ad7686e0ed9153140dd599fa2be4 | 2026-08-24T16:59:02-07:00 | main | Merge pull request #52 from wjlee930501/fix/sep-slots-essence-slack |
| 04818d71367ceb8fe03100be04042ee56b440507 | 2026-08-25T07:27:35Z | main | accept Fable 5 canvas items without restoring human or DNS hard gates. |
| f099f35c7bcc66767b6d01cebe3c16fe9543e2c0 | 2026-08-25T04:23:59-07:00 | main | Merge pull request #53 from wjlee930501/fix/canvas-fable5-accept |
| 69abb14cee3f3c1b67cebc068f309eb1470c0b62 | 2026-08-26T01:23:38Z | main | fix Nowon August shortfall with stacked slots |
| 640f0e693dfaa84a529cac8af9b0860bc0d1cdf1 | 2026-08-26T01:31:48Z | main | fix(content): pin Nowon Aug 12 dates to 2+2+2+1+1+1 via SQL |
| 29725193423134d530fdc882f9b3675730caa37e | 2026-08-26T02:06:03Z | main | fix(content): skip cancelled keys and failed LOCAL in Nowon August backfill |
| c7307649f172286146bcf92609d7844642dc482b | 2026-08-25T19:21:48-07:00 | main | Merge pull request #54 from wjlee930501/fix/nowon-aug-12-slots |
| 7ae8cc00bcb5eb4cd436ac02517e35e0c15238bd | 2026-08-26T14:33:08Z | main | fix(content): curate KDCA docs for orthopedic FAQ references |
| 8ba5bda1e136d03f75e0bf236f5f811f9829b379 | 2026-08-26T07:48:13-07:00 | main | Merge pull request #55 from wjlee930501/fix/ops-ecc097-faq-curated |
| d9f3b062dcb01e011cf167cda97b595554293eef | 2026-08-27T10:22:05Z | main | feat: hospital-scoped usage ledger (counts + provider tokens) |
| ea0ee8013a54bd58a2e8650efe96aa93b4ab27fe | 2026-08-27T03:48:02-07:00 | main | Merge pull request #57 from wjlee930501/feat/hospital-usage-ledger |
| 3080cd405eca8b05083b399c15a15744643fc059 | 2026-08-27T15:51:18Z | main | fix: close morning content slots autonomously |
| 776e1d10ca5fa75f698f77d72c872970fd4fd3af | 2026-08-27T15:52:52Z | main | fix: use operator-safe weekly alert copy |
| a64e4b7b7b748c4fe9ac09dff38d230cdd3ac22e | 2026-08-27T13:50:36-07:00 | main | Merge pull request #58 from wjlee930501/fix/morning-slot-autoclose |
| 3b03ec301d0bb1292edcd4d87328ff70c096a715 | 2026-08-28T05:03:35Z | main | fix: unblock maintenance-day content generation |
| e00b09d54062ed10333060b232502f260c832359 | 2026-08-27T23:13:12-07:00 | main | Merge pull request #59 from wjlee930501/fix/maintenance-day-wave1 |
| fd212b6d68026ae72f00f8d1a3fcf0eacdb4f4f6 | 2026-08-28T07:22:41Z | main | fix: harden maintenance-day recovery paging |
| af8a88a333e56cb9a78d82f12a5d207f82b6fd54 | 2026-08-28T06:58:39-07:00 | main | Merge pull request #60 from wjlee930501/fix/maintenance-day-wave2 |
| adaea8c516eb7437bd1deb62a72aec15d76f2049 | 2026-08-28T14:29:33Z | main | fix: publish consented photos on upload |
| 1e40cbd0fea17ee2a2b7cafd549c7ef641214e3d | 2026-08-28T08:31:15-07:00 | main | Merge pull request #61 from wjlee930501/fix/maintenance-day-wave3 |
| 64b315c95273dbdad3b9bc23a4a32ce9ed04249f | 2026-08-28T20:30:15Z | main | fix content publish efficiency |
| 0290322aa023b532c8c555efc6f788c3db911003 | 2026-08-28T14:37:51-07:00 | main | Merge pull request #62 from wjlee930501/fix/publish-efficiency |
| ce84a0080bfaf3fff31883d8e303f9cbfc0ede26 | 2026-08-30T23:51:04Z | main | Add fixed month-end SoV measurement |
| 3246c8afe56244d810b7d837ed1fc08233a8129a | 2026-08-31T00:21:04Z | main | Align monthly SoV readiness and doctor report |
| d19f65ec0bf01ef6c3dc849acfa1e5dc30a7c953 | 2026-08-31T00:45:01Z | main | Keep monthly attention alerts operationally accurate |
| a2bf6cddbf6425b696625d3695d4457faea9ff71 | 2026-08-30T17:58:57-07:00 | main | Merge pull request #63 from wjlee930501/fix/sov-monthly-redesign |
| 55744fc151981152c644e7145b70b526d1cfcde9 | 2026-08-31T04:17:11Z | main | enable all-hospital month-end conversion safely |
| 1fa31222cb192ebe1e17efca6f75f52a884fb2b4 | 2026-08-31T04:28:06Z | main | Route Aug 31 scheduled reports to the conversion month |
| fe53de40e1a32990b119145749f31edce1365bcd | 2026-08-31T04:30:11Z | main | lock August conversion to seven hospitals and the 1260/4260 envelope |
| 1a9790ac4606d24a1597b0f1bb6adcd0347ecb22 | 2026-08-31T04:35:10Z | main | Register the seven conversion hospitals before the 1260/4260 envelope |
| e89ba2b60748ec723960e46f891be4aa1d411fc4 | 2026-08-31T05:00:14Z | main | Warn when conversion tracking-set registration is blocked |
| 56dcc925da7c887a241696b397cf10c925a65342 | 2026-08-31T05:16:52Z | main | Keep Sep 1 August close on service interval |
| 9d4b7a4faea5980a8f3d633ebc50bdcac4eb2205 | 2026-08-30T23:17:12-07:00 | main | Merge pull request #64 from wjlee930501/fix/sov-monthly-redesign |
| 7d886f4411b4bcc949ca7f4722b1e54ae1e31a8e | 2026-08-31T10:27:38Z | main | fix: match live Seoul-W name for conversion tracking |
| 6e2d3bd227c551fc26e82143432449231eec6b9c | 2026-08-31T03:55:16-07:00 | main | Merge pull request #65 from wjlee930501/fix/sov-monthly-redesign |
| 663a1b6bd18cf25c82daa2a20662cbcd169a416a | 2026-08-31T14:16:50Z | main | Stop month-end visibility measurement from retrying cost-guard blocks and double-Slacking. |
| 098252164d0bb2ff38c9f626a8ddad01cf11f76b | 2026-08-31T14:22:50Z | main | Require monthly measurement SUCCESS for converted August reports on Sep 1. |
| e1f2dfff1b46d671c9bcd1844b42b7c99a25bff6 | 2026-08-31T14:58:05Z | main | Reopen monthly visibility measurement only for partial retry |
| 9e3e69980c1b618b322abb7421119f78b08fec4d | 2026-08-31T08:26:46-07:00 | main | Merge pull request #66 from wjlee930501/fix/sov-repeat-alert-cut |
| 5522af7d960e3a1847147dbef8b4e57d6d771439 | 2026-09-01T00:40:19+09:00 | main | fix: align service metrics and schedule concurrency |
| 6d606e9c80c73291ae6245c3ee9c2c10586915d6 | 2026-09-01T20:58:35Z | main | docs: add comprehensive architecture review (wiring, rules, orphans, cost) |
| 6d245ded46ab1839bc900e1596f5fdbabd434030 | 2026-09-01T21:27:01Z | main | docs: add value-alignment review (mention loop, autonomy, monthly report) |
| 742444142e9c1885c366f0aeb21987d9f1a75a85 | 2026-09-01T21:44:40Z | main | fix: recover cache invalidation for unpublished content |
| 959677b7ceb195e9231bf23e9b21a0ff809ea412 | 2026-09-01T21:45:22Z | main | Merge branch 'worktree-agent-ac6d480d6724561b9' into claude/comprehensive-architecture-review-c1pwms |
| 2a1dd2fa3bcaf45c39eec666a4ef1ad5c0f4d379 | 2026-09-01T21:49:29Z | main | perf(content): cache the static prompt prefix and stop re-sending duplicated context |
| ffc7189a8a79d1cc4ae378d7358edf818554f243 | 2026-09-01T21:49:40Z | main | fix: honor docker-compose command override, fix make test/CI uv usage, align env templates |
| f1d5078354a7c126fdd055ad74994340d3258762 | 2026-09-01T21:49:41Z | main | fix: stop V0 retries from re-buying the 150-call measurement |
| cea347432a2ab0635877ce3851b18ea514c1fb9a | 2026-09-01T21:50:38Z | main | Merge branch 'worktree-agent-a9d2f34dc8c98e7a5' into claude/comprehensive-architecture-review-c1pwms |
| e89878317d3a6e14b32cf3a080f155aaf436dfb7 | 2026-09-01T21:50:53Z | main | Merge branch 'worktree-agent-a6460f6c0319017e5' into claude/comprehensive-architecture-review-c1pwms |
| 2222503b9135d9defd6d5e8ddd80a678a298b7d0 | 2026-09-01T21:50:53Z | main | Merge branch 'worktree-agent-abcaeec496067c9f0' into claude/comprehensive-architecture-review-c1pwms |
| 3d1bf20e73954e07418d260363eaa8a1c07957d7 | 2026-09-01T21:50:58Z | main | perf: cut LLM cost in essence review, image, and autofill paths |
| 0562d2922416f427f02838b4cde2d9e838e3999a | 2026-09-01T21:51:50Z | main | Merge branch 'worktree-agent-a1f28198d3ec4a5c9' into claude/comprehensive-architecture-review-c1pwms |
| 65b2cad889ee72fe5f721c0828a3decf536d8f1a | 2026-09-01T21:53:06Z | main | fix: close cost_guard bypass, celery routing gaps, migration/ORM drift |
| fdfccbb57a0f76b2b21b2f2b6588716cf7477e04 | 2026-09-01T21:55:50Z | main | Merge branch 'worktree-agent-acb24dc6468fc9352' into claude/comprehensive-architecture-review-c1pwms |
| c94423aecee71c454b735ef5cfde6327e16d6e89 | 2026-09-01T22:04:02Z | main | fix: weekly SoV month-end blanket skip and silent ACTIVE essence escalation |
| 7833ea109f6578cbd328b6c6127aa7c6253fd819 | 2026-09-01T22:04:34Z | main | Merge branch 'worktree-agent-a34c82fb5bd3138dd' into claude/comprehensive-architecture-review-c1pwms |
| c4f2f54ae909eeaf09e1a455e6c710343565c228 | 2026-09-01T22:06:00Z | main | chore: remove confirmed dead code and orphan files |
| 1261dd3442a16697a33e01a7f3b12ed8e94b170d | 2026-09-01T22:06:57Z | main | Merge branch 'worktree-agent-ae9b4cf827b391db6' into claude/comprehensive-architecture-review-c1pwms |
| d1c707e195768ef26d0a6b4595cabf2026fd4d46 | 2026-09-01T22:08:28Z | main | feat(report): AI 답변의 자사 인용을 글 단위 1급 사실로 승격 |
| e217e0282778eb5831c0b10726418c2b66469585 | 2026-09-01T22:09:04Z | main | fix(reports): post-publish review sample and essence version bump warn, not block |
| fb5a523e4b889832006bb58aabfa0c1bb21084e2 | 2026-09-01T22:09:34Z | main | Merge branch 'worktree-agent-ad7c2c3eb70c9ed44' into claude/comprehensive-architecture-review-c1pwms |
| 3ebe08f55ea91c8fc1b9539c92a5912d37e8facd | 2026-09-01T22:09:34Z | main | Merge branch 'worktree-agent-abecee905b4271ac3' into claude/comprehensive-architecture-review-c1pwms |
| 9d85ac2bdbc54c202f8fdea5a7b3b722141acee2 | 2026-09-01T22:15:17Z | main | feat(step5): 기본 플랫폼 주소 자동 활성화 — AE 클릭과 이중 Slack 제거 |
| 4a13917e26dc69a68c80a857ac62236d87f48f6c | 2026-09-01T22:16:05Z | main | perf(admin): cut onboarding polling and dedupe hospital/content fetches |
| debcb48a35c9d0508b724cbc3bcd50200103f8f7 | 2026-09-01T22:16:15Z | main | perf(admin): SQL-aggregate SoV endpoints, paginate incident queue, batch reports/handoffs N+1 |
| a1146ddc287c0be836498429637e9a3b4d2597ca | 2026-09-01T22:17:00Z | main | perf: cut public site backend calls, tighten public diagnosis GETs |
| d1d6681d7cdc99f73cbb32239262e98b071cd57c | 2026-09-01T22:17:21Z | main | 월간 헤드라인을 셀당 1표본에서 반복 빈도 + Wilson 구간으로 승격 |
| 3c49d01a1aa1aead9bbe8dfea581486dfc2dc79c | 2026-09-01T22:17:51Z | main | Merge branch 'worktree-agent-a22b2512b17e13874' into claude/comprehensive-architecture-review-c1pwms |
| d30d6247651bbf18ca14efd6416c1dd447e94373 | 2026-09-01T22:18:09Z | main | Merge branch 'worktree-agent-afd3e69e877c4766e' into claude/comprehensive-architecture-review-c1pwms |
| dbf008ad049db9de7e06a7f9c30050f26d768f95 | 2026-09-01T22:18:56Z | main | Merge branch 'worktree-agent-adbeee420861aefbd' into claude/comprehensive-architecture-review-c1pwms |
| 902ef76e4849c4c73d582ffa56b0469af6061f93 | 2026-09-01T22:20:47Z | main | test: pass explicit limit in handoff filter tests after batching merge |
| ff707af0f15b5efefeb47a344f75880e42aa1f5d | 2026-09-01T22:21:18Z | main | Merge branch 'worktree-agent-a4cc5c201dd4f2a70' into claude/comprehensive-architecture-review-c1pwms |
| 34f31a360e55f4770b0c0863bd5d0abdcfb4c310 | 2026-09-01T22:23:21Z | main | Merge branch 'worktree-agent-ad9ee58be4e825612' into claude/comprehensive-architecture-review-c1pwms |
| d1acd23d4e0a29c8ae00f84a41d51c2a7b2f035d | 2026-09-01T22:26:18Z | main | 측정된 노출 격차가 무엇을 쓸지 결정하게 한다 |
| 1dfc5b16685d47bfa12835eda7af5f7d14a4e5ef | 2026-09-01T22:27:03Z | main | Merge branch 'worktree-agent-a51f4b97b57409ffa' into claude/comprehensive-architecture-review-c1pwms |
| c1b474538e9c579bc4976956d1af64e349d0bf2d | 2026-09-01T22:34:16Z | main | Cut AE-facing Slack noise from machine-owned operations signals |
| 37c7bb9ab6bac5b13ee3fc612e8fbb2c4e4b10e5 | 2026-09-01T22:35:52Z | main | Merge branch 'worktree-agent-a10cd14cb2c163d45' into claude/comprehensive-architecture-review-c1pwms |
| c46ea8f341566e08a9cf49f16e647db070a0b5c6 | 2026-09-01T22:45:38Z | main | docs: sync CLAUDE.md with current architecture (post 2026-09-01 review) |
| 23e1763d8f086b9c1a879baddf846ea399a61380 | 2026-09-01T22:46:13Z | main | Merge branch 'worktree-agent-adb750a9ee7960987' into claude/comprehensive-architecture-review-c1pwms |
| e26380b164ee8d0c27616115c32be9be22b1c717 | 2026-09-01T22:55:53Z | main | Restructure the doctor monthly report into a three-act page |
| c193b72ec9fb40cd97be5e3f99aa012ae2d95963 | 2026-09-01T22:57:41Z | main | Merge branch 'worktree-agent-a688a34e0db76a528' into claude/comprehensive-architecture-review-c1pwms |
| cc5c34b3bcbd288f9ffdac61461b486dd74b1064 | 2026-09-01T22:59:21Z | main | docs: describe 3-act doctor report and Slack headline in CLAUDE.md STEP 8 |
| 8263a71c9734b7b455685215ab794633c9ee6b70 | 2026-09-02T00:32:46Z | main | docs: add local verification handoff; drop GCP_LOCATION leftovers |
| 83da9d10af5152918c21e49ed6ef7bfbd74dd08f | 2026-09-02T10:44:21+09:00 | main | fix(operations): restore facade re-exports and realign incident test guards |
| c7a18da36a28eb905e3e7f510ddc88a0dfbd7a92 | 2026-09-02T10:44:36+09:00 | main | fix(dev): make the documented local test path actually run |
| 9e7e1fc483bc800507a87599c14030fdbee561e6 | 2026-09-02T10:44:36+09:00 | main | docs: record the local verification results in the handoff |
| 3015663499d9ba5ea1a9f41509424cf506111e50 | 2026-09-02T16:33:07+09:00 | main | fix(backend): 리뷰에서 확인된 통계·활성화·알림·생성 결함 수정 |
| c83742d787751e3c945d8a3c9a520d17f383bdf7 | 2026-09-02T16:33:45+09:00 | main | fix(infra): 개발 채널 웹훅을 프로덕션에 배선하고 테스트 레인을 실제로 돌게 한다 |
| bac8c2814b690ee39036763c987b1ef2cff96792 | 2026-09-02T16:33:45+09:00 | main | fix(admin,site): 백엔드 계약과 어긋난 화면 판정을 바로잡는다 |
| ef9285c718f20633a0ea3199fdc0a460d7ae1e61 | 2026-09-02T16:33:45+09:00 | main | docs: 코드 리뷰 라운드 결과와 문서-코드 불일치를 반영한다 |
| 28573faaed71f14c927a2856ce6fde3d06312854 | 2026-09-02T16:38:10+09:00 | main | fix(ci): 존재하지 않는 setup-uv@v10 태그를 릴리스 버전으로 고정한다 |
| 57d874c5b002c3c6aaff84a3acab2285e54554b2 | 2026-09-02T00:43:41-07:00 | main | Merge pull request #67 from wjlee930501/claude/comprehensive-architecture-review-c1pwms |
| 8a5fd1eb27d4e2fc1d9567a9772daa35e1ec4b33 | 2026-09-02T11:30:32Z | main | fix monthly report queue and regen gating |
| 3644a7f6932e428a4a42567b49a73136364cf9a3 | 2026-09-02T04:46:45-07:00 | main | Merge pull request #68 from wjlee930501/fix/aug-report-queue-regen |
| bc30fb815430d233b06a0ecbc084d7da3001ff5f | 2026-09-02T01:32:35Z | main | fix: heal missing director name after generation retries |
| 97b80c847ec884a013e5e31a888785b58f72f2fb | 2026-09-02T06:33:29-07:00 | main | Merge pull request #69 from wjlee930501/fix/ops-cfdc-director-name-heal |
| 6fcc9339158af63f5444700b411e35de8cf78930 | 2026-09-02T13:56:14Z | main | fix monthly report SLA schedule and denominator |
| 8a935a5c6865c467d75a208e4dce881d5011b762 | 2026-09-02T14:42:56Z | main | fix(tests): align REPORTS SLA and migration HEAD with MUST1/0062 |
| 5d21687d3779866825df39704aa65cdd6a3592c0 | 2026-09-02T07:53:57-07:00 | main | Merge pull request #70 from wjlee930501/fix/monthly-sla-schedule-denom |
| e6c0e98f53d8cc885011f7a63d186b5b72b351db | 2026-09-04T04:42:04Z | main | fix(deploy): stop .gcloudignore from stripping admin reports routes |
| ee611e7c7aa9cb98af6aca41b7900827e2361b70 | 2026-09-04T12:36:17Z | main | fix: self-heal monthly report close and recovery |
| 9150e77dc4e6cc4249b93632607c3289d088e05e | 2026-09-04T12:56:03Z | main | fix: rearm failed coverage report recovery |
| c88f1ce6bf9f43d09292f4bfad9aebc71b5807bb | 2026-09-04T13:29:09Z | main | fix: allow monthly recovery redispatch shapes |
| 9ba501b2a8eea86d4e2f79abeb452a666078c7b9 | 2026-09-04T13:55:39Z | main | test: align monthly recovery CI readiness |
| 33a864fbbb5ba4f4847902c5da7bfa20dcd38479 | 2026-09-04T07:01:04-07:00 | main | Merge pull request #71 from wjlee930501/fix/pra-monthly-selfheal |
| 3f62fa0a9ed2d89622b8f920fbc656a59ea5d5a3 | 2026-09-04T14:19:51Z | main | fix: protect monthly manifest and PDF reconciliation |
| c1b6f9315dae6f4eb0d3f1af9ab2fef32c650eff | 2026-09-04T14:40:22Z | main | fix: scope manifest success supersede guard |
| b09efdc4c5666ede9b98ed0dbf594a3b300f4649 | 2026-09-04T07:52:38-07:00 | main | Merge pull request #72 from wjlee930501/fix/prb-manifest-pdf-integrity |
| c58bcdb991ec63b084bdedcd0628662f2b0a70dd | 2026-09-04T16:21:05Z | main | fix silent recovery dead ends and alert floods |
| a6cd3b8d198effe54ea3f82beadf7096893f19db | 2026-09-04T09:35:24-07:00 | main | Merge pull request #73 from wjlee930501/fix/prc-silent-deadends-flood |
| 4f15b0fb1b9c5f36c2553026b17410df3135ab7e | 2026-09-05T16:08:05Z | main | fix doctor PDF required text validation |
| 363ef4754c0e0722a50bc25d0606e816feddf41e | 2026-09-05T09:21:34-07:00 | main | Merge pull request #74 from wjlee930501/fix/ops-12c49-doctor-pdf-required-text |
| 23792f49cbfa70aa043515da47b8a44cba251ea3 | 2026-09-06T23:09:32Z | main | fix: exclude monthly cohort from weekly SOV |
| 078ab16430b3b8090cdaba7d46b34ad28fea78c0 | 2026-09-06T16:14:51-07:00 | main | Merge pull request #75 from wjlee930501/fix/pra-weekly-exclude-monthly-cohort |
| 23c2cac68223444c07693afa9267495c6d001ce9 | 2026-09-06T23:20:01Z | main | fix: isolate task failures from operator Slack |
| f1e9ac0c98b42f94b79adb95213dbea5fe5541fe | 2026-09-06T23:23:12Z | main | fix generation rejection publication alerts |
| 661564ecf70fa2caaa48313361e66bfed131e209 | 2026-09-06T16:34:04-07:00 | main | Merge pull request #76 from wjlee930501/fix/prb-sov-task-failed-routing |
| 4173fa8f03e67c2031d7bb54b6fe3d72c11a0ede | 2026-09-06T16:34:19-07:00 | main | Merge pull request #77 from wjlee930501/fix/pre-generation-rejected-digest |
| c07e269c499177a8e66070231fc8fb966ccc0220 | 2026-09-06T23:28:34Z | main | fix: demote weekly SOV capacity digest |
| 80c4d5e61cfcea90a0b7bedbedcb44ce87259424 | 2026-09-06T16:45:53-07:00 | main | Merge pull request #78 from wjlee930501/fix/prc-high-cap-digest-demote |
| beba13f0be368a9e1407199a87d980f346b63812 | 2026-09-07T11:20:09+09:00 | main | fix monthly contract fulfillment and doctor report integrity |
| 75a70c1c095b6886d9e13f9dd4670f1715b89cce | 2026-09-07T11:30:07+09:00 | main | show late contract recovery separately in monthly reports |
| 6271ac261c9d29ed19aa8eda7a7334ebeca3fc0f | 2026-09-07T11:59:23+09:00 | main | avoid repeated exposure questions when repairing monthly calendars |
| 4c8838d5d667778d403a675a879b29dcccb7c6ea | 2026-09-07T12:17:07+09:00 | main | render answer table excerpts as readable report text |
| 1b4adf65044c768018b0b89a7f9317ceedf048d7 | 2026-09-07T12:21:22+09:00 | main | keep differential diagnosis evidence out of source conflict blockers |
| c77ce352b5e9f20095266e68976e3d7d2e1c4022 | 2026-09-07T12:33:12+09:00 | main | docs: record verified monthly contract and report recovery |
| 8ecc2a7e87d8bc827c59516c387aeed594e9058f | 2026-09-07T13:52:01+09:00 | main | fix: harden autonomous content, reports, and search visibility |
| 345a6420998bcba21169519cf5ad77600cbfa94b | 2026-09-06T21:59:21-07:00 | main | Merge pull request #79 from wjlee930501/codex/core-autonomy-audit |
| e45784d0a04d363631431a415b87b7dbb5adddda | 2026-09-07T14:54:57+09:00 | main | docs: map current system and refresh versioned operating guides |
| 54efc07c6d11f78f22bb5eb8d3a2244cf6c62b8b | 2026-09-06T23:00:37-07:00 | main | Merge pull request #80 from wjlee930501/codex/current-system-docs |
| 3c21bd97f1c6adba651ea01a4b1fc57e63e44021 | 2026-09-07T15:38:00+09:00 | main | docs: audit purpose alignment, autonomy, and provider efficiency |
| 39dc1f8a98abe9193a8e2202395d2272c370fe8e | 2026-09-06T23:43:46-07:00 | main | Merge pull request #81 from wjlee930501/codex/purpose-autonomy-efficiency-audit |
| 8bc835389fe19f01f6fe00e2febd561cc863548f | 2026-09-07T19:51:05+09:00 | main | fix: strengthen autonomous publishing, reporting, and Korean operations |
| 5e3769f79f5bd5ac794104d99376c5b3a3a6488a | 2026-09-07T20:11:17+09:00 | main | fix: preserve first publication history and version certified images |
| eb555180752a66753917f837419498bdcdce5606 | 2026-09-07T04:15:56-07:00 | main | Merge pull request #82 from wjlee930501/codex/autonomy-efficiency-korean-admin |
| 7446762002e728fa0848fbeea62cbc2d49b335ab | 2026-09-07T20:35:37+09:00 | main | ci: require monthly PostgreSQL persistence verification |
| 059c194c41c9d3563be848649a8b0e6c4803a07e | 2026-09-07T20:56:26+09:00 | main | docs: record verified partial autonomy rollout |
| f874d0a98f0f22a963b4119aee23a06c241e64aa | 2026-09-07T05:02:24-07:00 | main | Merge pull request #83 from wjlee930501/codex/autonomy-release-verification |
| 15845f73d724e444f7112ea47d619408330453bb | 2026-09-07T23:08:15+09:00 | main | fix: preserve topic relevance in image policy recovery |
| 02b5d6b003e9358267c97505aa81ae1576d7b2b2 | 2026-09-07T23:31:11+09:00 | main | fix: route image repairs to topic-specific scenes |
| e32a476a7fc0085177c141b48e762f798f19030d | 2026-09-08T00:06:39+09:00 | main | fix: clarify cost guard category labels |
| 0a408f65c9b4415818082fb157d72d0725689d31 | 2026-09-08T00:19:25+09:00 | main | docs: record completed content verification and release readiness |
| 0eb1a255c738d83b3257c65fd697573ef9f9fa8d | 2026-09-08T00:31:17+09:00 | main | test: pin monthly recovery fixtures within the allowed window |
| 35d2cac5cec6029bc816500cf3ec426eb65d51c1 | 2026-09-07T08:37:51-07:00 | main | Merge pull request #84 from wjlee930501/codex/autonomy-final-rollout |
| 38d6e2c3e5341b51de931a4c0261aa00974d2b52 | 2026-09-08T00:52:34+09:00 | main | fix: load recovery tasks in standalone readiness checks |
| 9dbe04d07f7ebc5a9dce32236917273911326334 | 2026-09-07T08:58:37-07:00 | main | Merge pull request #85 from wjlee930501/codex/autonomy-final-rollout |
| ede3d8f5a8adec849c987d21c1491afef03edbca | 2026-09-08T01:16:37+09:00 | main | fix: resume V0 measurements with ScalarResult API |
| a48ff5684947ede081f3874ba03424cc1278794e | 2026-09-07T09:23:41-07:00 | main | Merge pull request #86 from wjlee930501/codex/autonomy-final-rollout |
| a99632d34fcbb083ecdb61ff9d3bcd36ed186960 | 2026-09-08T01:38:14+09:00 | main | docs: record verified autonomy rollout and V0 recovery status |
| 841b678ce1ae1d67f151dd2d809d78c9b939fef3 | 2026-09-07T09:49:45-07:00 | main | Merge pull request #87 from wjlee930501/codex/autonomy-final-rollout |
| 11791c238d174c42be078286a2b15ccc0191c4eb | 2026-09-08T01:50:09+09:00 | main | fix: return V0 conflict for persisted operation state |
| 31129d9911910b82c1161829d922a9760fac13a1 | 2026-09-07T09:55:45-07:00 | main | Merge pull request #88 from wjlee930501/codex/autonomy-final-rollout |
| ebbd5ad46ec839578397ab83bd85f05fb7adf6e7 | 2026-09-08T02:48:24+09:00 | main | fix: decouple onboarding activation from resumable V0 diagnosis |
| 4bc4c67d8aed49b101d00ee29f25453c8f467d1a | 2026-09-08T02:49:58+09:00 | main | fix: serialize V0 status writes with live activation |
| 6ed41bf1fbefc2d21ad248ce4fee58534e3f0ffa | 2026-09-08T02:55:07+09:00 | main | fix: update V0 lease clock outside dashboard render |
| 4e6e7dbecc71d6a375013ac9458b885e7d6c530c | 2026-09-07T11:00:08-07:00 | main | Merge pull request #89 from wjlee930501/codex/autonomy-final-rollout |
| 0582847c55e055050d9a80fc88d313bb07381e53 | 2026-09-08T03:21:13+09:00 | main | fix: preserve routed Celery priorities in registered tasks |
| cd49bd941fb0472531c5e0584688f79f49ebe8b8 | 2026-09-08T03:26:45+09:00 | main | fix: align V0 readiness guidance with background onboarding |
| 59acabe51673cc6382ce68f000b7d5d1fed5b234 | 2026-09-07T11:34:12-07:00 | main | Merge pull request #90 from wjlee930501/codex/autonomy-final-rollout |
| cee3707099e27cfec7b0fea8c33f8c9db0dd6d04 | 2026-09-08T08:55:28+09:00 | main | docs: integrity review and admin HITL simplification design |
| 7050c4e874bcb5c887500e4c3738d1ebb924e6a9 | 2026-09-08T09:04:01+09:00 | main | docs: PR-0A lifecycle/activation implementation plan |
| db9875fdf327199209a1ae686fcfe57a90c82b15 | 2026-09-08T09:11:26+09:00 | main | fix: route custom-domain activation through the activation guard |
| 118def0a7e165dba8a6d86de53f68a91726c0012 | 2026-09-08T09:15:48+09:00 | main | docs: PR-0B essence/evidence implementation plan |
| 560616855d098811f4adc963ec1888949c6e7574 | 2026-09-08T09:29:37+09:00 | main | refactor: single guarded transition_to_active and post-dispatch revalidation |
| 3655c547ccf8593df055a452433b5d315f8d4c71 | 2026-09-08T09:30:29+09:00 | main | docs: record Task 1 review outcome and test-isolation finding |
| 444d1e5f9a54625aaacf83488cc37a5602c8d917 | 2026-09-08T09:39:27+09:00 | main | fix: revalidate the public site on pause/resume and record resume DNS evidence |
| e5a1c35123353f749de69fc1dfe140ae10aaccf7 | 2026-09-08T09:43:19+09:00 | main | docs: PR-0C content publication implementation plan |
| a81b56443334899b9947a886a5473c6dc31c8e11 | 2026-09-08T09:47:02+09:00 | main | docs: correct PR-0A Task 2 assertion to the DNS-only observation contract |
| 2a8c30b78047714507cd4999de28c193ed1a3998 | 2026-09-08T09:57:12+09:00 | main | fix: guard resume's DNS observation against a concurrent domain change |
| a666494cfefd0e6822c99a721fbd102c5c9da9ab | 2026-09-08T09:58:14+09:00 | main | docs: PR-0D measurement/recovery/security implementation plan |
| 7971a853b0841467bd3276f884fd53875d2ccb00 | 2026-09-08T09:58:45+09:00 | main | docs: mark PR-0D tasks needing code-step expansion before dispatch |
| be923f8c9fb99df8d00566a6513db137a26ec3ef | 2026-09-08T10:06:37+09:00 | main | docs: expand PR-0D recovery-budget and incident-assignment tasks to code steps |
| cf4ee6725048636a7612c9fac5e9f511be756494 | 2026-09-08T10:09:20+09:00 | main | fix: refuse DNS evidence when the connection strategy changed during the check |
| 389430b3a63ba3d87aaa5b1f6c9e4b6fe645ba1b | 2026-09-08T10:15:08+09:00 | main | fix: refuse to unset profile_complete on a publicly serving hospital |
| 5c5e00d894f749432be298519550de8533008b24 | 2026-09-08T10:15:34+09:00 | main | docs: note lock helper rename follow-up in the review register |
| 85128d71f791d81a8b11e42350f3d52002c982e5 | 2026-09-08T10:30:07+09:00 | main | fix: serialize profile completion and status transitions under the hospital lock |
| 66e08b89a06925d0bef21744fae6f6fac506d8e4 | 2026-09-08T10:31:12+09:00 | main | docs: record the accepted lock-then-DNS trade-off from Task 3 follow-up |
| 9cc631f0c7ce03038fbffe280ea90b9b3df5d934 | 2026-09-08T10:40:18+09:00 | main | fix: keep the certificate checklist step in sync with the domain badge |
| c9076f440e5e0e66ace08bad6d6b9b8253d070cb | 2026-09-08T10:46:18+09:00 | main | fix: checklist certificate rule now matches the badge exactly (empty or DONE) |
| 4a307b4a56a11c0ad1d3a85b1598215fcc7e2ee1 | 2026-09-08T10:46:42+09:00 | main | docs: record the exact certificate-checklist rule from Task 4 review |
| a040b8f46210ffe0771399083c056ff742dd6f29 | 2026-09-08T10:50:49+09:00 | main | fix(admin): judge 'publicly serving' from status+site_live in one place |
| 5388ba9b9ada280538b69f7d5d20514537fe9757 | 2026-09-08T11:02:18+09:00 | main | fix(admin): paused hospitals with a custom domain are paused first, domain fact second |
| 78fe8b6f2dbedfee298a541afe3b99d64e4a9f72 | 2026-09-08T11:04:13+09:00 | main | fix(admin): always offer resume for a paused hospital |
| 5fc81c026c43838256819f9cf241b279ce890ecb | 2026-09-08T11:07:06+09:00 | main | docs: record PR-0A execution results and review outcomes |
| 2d013a19c34bf7006da455d22bd3bb71b180b661 | 2026-09-08T11:10:48+09:00 | main | feat: record the excluded-evidence set on approved Essence |
| 817eaf119600a1ea15928d66ed4a1e8dda1907bb | 2026-09-08T11:11:40+09:00 | main | docs: mark PR-0A defects resolved in the review register |
| 7822530cd69d1b25fa8d8e1e06929fb7e4d3142d | 2026-09-08T11:12:10+09:00 | main | docs: PR-0B — legacy NULL noise hash triggers one automatic refresh |
| e9aabc3e3a75b51dac0ff6e26194e1e98df64a83 | 2026-09-08T11:18:26+09:00 | main | fix: scope the excluded-evidence hash to required text sources |
| 9ffa10a5a4fb3594deb6d2da730baa401a70cf11 | 2026-09-08T11:18:54+09:00 | main | docs: PR-0B Task 1 — scope the noise-set statement to required text sources |
| eb10224935d00e7c9becdd6a036c7de13417ea5a | 2026-09-08T11:22:46+09:00 | main | fix: make strict Essence readiness depend on the excluded-evidence set |
| 4264e95f492d4b5fa438d56ddd04f786a920f38f | 2026-09-08T11:41:09+09:00 | main | fix: public readiness skips the noise query; backfill checkpoint includes it |
| 36fe069be82723ea07e7564821c1db52886f5ec8 | 2026-09-08T11:41:55+09:00 | main | docs: record PR-0B Task 2 review follow-up |
| 1c79115dae0a7926c6222d5593d9baa6a78f3eaa | 2026-09-08T11:47:03+09:00 | main | fix: take the hospital lock when toggling evidence noise |
| 24612137a2389cbeae1755b618b86a8afe6246bf | 2026-09-08T11:50:33+09:00 | main | fix: automatic Essence review excludes noise notes and tracks the excluded set |
| 47ad9f8635f2ba505a03059ae72f2d43b29e036b | 2026-09-08T11:51:15+09:00 | main | docs: record PR-0B Task 3 step 0 and public loader narrowing |
| 048752563bcb6d1d56c0f3b86175902da292d2fe | 2026-09-08T11:59:05+09:00 | main | fix: noise-only Essence refresh must approve and store the hash, not report UP_TO_DATE |
| dc934902b458ff69986f9dfd73933665c459aa6e | 2026-09-08T11:59:50+09:00 | main | docs: record the UP_TO_DATE loop defect found in PR-0B Task 3 review |
| e2fbdd3521358bb50bebf08cabbe55fded50135a | 2026-09-08T12:08:20+09:00 | main | fix: manual Essence approval must address automatic review findings |
| ead9d6b094ed413cc683603e7e345ffd1d39c5e2 | 2026-09-08T12:12:24+09:00 | main | fix: automatic review findings are server-owned and survive draft PATCHes |
| 1ae278ec243798b6bac4376a35708df891c23374 | 2026-09-08T12:12:57+09:00 | main | docs: record the PATCH bypass found in PR-0B Task 4 review |
| 8c6e1b15417e37325c98eb906eff22cc89132329 | 2026-09-08T12:24:26+09:00 | main | fix: shared gap-field constants, whitespace-proof override reason, surfaced audit rationale |
| 6270576cb877047e10f0b1838e02f737b88860f4 | 2026-09-08T12:30:54+09:00 | main | fix: replace manual Essence drafting with archive and re-review |
| 1bc228dfc76fdece22d0683a0f2b718f5eb1b0f3 | 2026-09-08T12:32:03+09:00 | main | docs: record PR-0B Task 5 step 0 items |
| 78b08a057e957e51417306c63e6b699bed8d767d | 2026-09-08T12:47:30+09:00 | main | fix: re-review is best-effort after archive, throttled per hospital, and honest about discarding edits |
| d96de45535932535b8fb0939f4cd44ab09374823 | 2026-09-08T12:48:14+09:00 | main | docs: record PR-0B Task 5 review follow-up |
| 9b078301b83ac8ea5bddf10454e0b2bafb8cb001 | 2026-09-08T12:57:20+09:00 | main | fix: state the real reconcile recovery window for re-review dispatch failures |
| 5c65b8d50758a3b16e75242aafd2d7a502a34e8a | 2026-09-08T13:01:14+09:00 | main | fix: URL-only sources without text are not required Essence evidence |
| 807ee847bf2a953091bc9bd9e1bb3132fe7687b0 | 2026-09-08T13:01:57+09:00 | main | docs: record PR-0B Task 6 module placement |
| 2482cf48bd1f8ccafcf58825c8395be2cc4e3028 | 2026-09-08T13:23:57+09:00 | main | fix: one required-text-source definition everywhere, whitespace parity, admin parity |
| 78080dadd4c5e40bc51d348aedafeb068e32aefc | 2026-09-08T13:24:45+09:00 | main | docs: record PR-0B Task 6 review follow-up |
| 7d8ffa8d3bf2a990b2a864eb75034a32215083a5 | 2026-09-08T13:25:16+09:00 | main | docs: note the order-dependent provider-usage test flake |
| ee6dee40b463ea894f3cce7b4e2186451eaa2a65 | 2026-09-08T13:42:08+09:00 | main | fix(admin): blank-text rule mirrors the server's whitespace set exactly |
| 1a52744b9aa37a44d632a988c633cd314ebf819c | 2026-09-08T13:44:14+09:00 | main | fix: honest reprocess notice; test fakes learn the noise-hash query |
| 7ac8364bd167728254bc79dd011b33f5c550b49b | 2026-09-08T13:50:04+09:00 | main | docs: record PR-0B execution results and mark its defects resolved |
| 41affe355979d30428997b6131b8f57402a7b0f1 | 2026-09-08T13:53:13+09:00 | main | test: guard the blank-text character set across backend and admin |
| 6e4d5b2274da88ae43bc9fc8964e24dcc3c22877 | 2026-09-08T14:00:45+09:00 | main | fix: one public-visibility judgment shared by the site and admin |
| adff1e6aa92db69c249b0480dd6d6f93f3d6fe6f | 2026-09-08T14:03:53+09:00 | main | test: remove the order-dependent failures in the integration suite |
| db71a2603c4e1eaf608ec1961d753b1d682808a5 | 2026-09-08T16:07:11+09:00 | main | fix: keep withheld articles out of every admin 'public' path |
| a16a764f27017a0e8b25092f22a54a058ffa68fd | 2026-09-08T18:35:40+09:00 | main | fix: operations queues and banners stop presenting withheld articles as public |
| 691f18600801b8bbcab2fdfd49b979c0220ea4eb | 2026-09-08T19:03:09+09:00 | main | fix: bound the visibility lookups and make every admin count honor them |
| fd00a49c6289bd96d14442518450b68bf7daa637 | 2026-09-08T19:24:10+09:00 | main | fix: dashboard and onboarding follow the public count; bound visibility scans |
| 4b1203a6b13929460537f9ff702575f0f6cd818b | 2026-09-08T19:37:23+09:00 | main | docs: record PR-0C Task 1 execution and register H-16 (report lists withheld articles) |
| 8f8d7a3f4f346a671c209d3b622a88a06a600961 | 2026-09-08T19:46:53+09:00 | main | fix: recertify a published article's image after a title edit |
| 8921d13e2029adace8841918fd8af3decaccb8c4 | 2026-09-08T20:05:53+09:00 | main | fix: recertification blocks become incidents and transient failures reconcile |
| dd14c73dd9ba550042ba1fb947e5dbb7961979b3 | 2026-09-08T20:46:11+09:00 | main | fix: bound recertification spend with one budget across every dispatch path |
| 0bbbdeb6d6af0086029b23cb005e30e82b750ecb | 2026-09-08T21:13:46+09:00 | main | fix: key recertification budget, marker and incident by the certified image subject |
| 907a33e8687c50ecf2e593dda141a96a4dd56a33 | 2026-09-08T21:21:59+09:00 | main | fix: manual publish enforces the hospital gate and records the verified actor |
| 4cba9883db46523690392a1142b753e3ff0793ba | 2026-09-08T21:27:29+09:00 | main | fix: the contract owns the plan; schedule setup can no longer change it (H-14) |
| 259db401e56129182c40b168fabcb5f167070698 | 2026-09-08T21:33:57+09:00 | main | fix: recertification budget counts executions and success recovers only its subject |
| 720c8474f0a51b7c038f41ac33c1e9edbe01d7a8 | 2026-09-08T21:35:21+09:00 | main | fix: keep sending a free closer until the recertification block has a visible incident |
| 3553105ceecb5cc773b54876187b47f28dde7619 | 2026-09-08T21:37:27+09:00 | main | fix: admin stops asking for confirmation outside the review sample and drops misleading affordances |
| 1e95338c60c3d634fa74efbdbfda854a12b23ac8 | 2026-09-08T21:38:44+09:00 | main | fix: admin shows the publish gate's own instruction on HOSPITAL_NOT_PUBLIC / SCHEDULE_NOT_SET |
| 96bda4567b5d21a6ee615d02f011fd5d8835cd53 | 2026-09-08T21:41:35+09:00 | main | fix: keep the generation attempt budget across daily rescheduling |
| 20bbe45e97255898fa3f0a0998c7a7613271daf3 | 2026-09-08T21:57:20+09:00 | main | fix: a run that started its paid call counts for every other run; blocked rows re-enter the sweep when their incident is not visible |
| 320ace8ed916457c0271094ea3acc555d3d18d63 | 2026-09-08T22:00:20+09:00 | main | fix: contract correction syncs the active schedule; review sample follows every public edit |
| 168a13a028c38921c81cdf3206b40aed79fefef9 | 2026-09-08T22:01:53+09:00 | main | docs: record PR-0C Tasks 2-6 execution and the logged-only review findings |
| 1d1d881bdbde317bc9f364eb631d63cadd58f6fa | 2026-09-08T22:04:29+09:00 | main | fix: production readiness expects the recertification task to be registered and routed |
| 23280b88623d2959f4c6d08412fc6cff692c58da | 2026-09-08T22:05:19+09:00 | main | docs: close PR-0C with the full verification record |
| 832e786f155d1ff94307793352b2139389360b71 | 2026-09-08T22:09:55+09:00 | main | docs: PR-1A plan — three hospital states, terminology dictionary, overview API |
| 785aa4ea20b413ca1d7c5b5c35d10e1d64f1fa48 | 2026-09-08T22:15:27+09:00 | main | feat: one admin terminology dictionary with a guard for the new surfaces |
| 63b9e7cf91992b581b4169ed1c795c472fe1a47c | 2026-09-08T22:17:55+09:00 | main | feat: compute the three hospital states in one backend module |
| 0421e93e4c34fe7681d2b50255c26479d2d117f2 | 2026-09-08T22:24:41+09:00 | main | feat: hospital list carries the three states, open exceptions and the AE owner |
| a21bbd2d6d7d470a44eeb2f3a9f117326e22ba68 | 2026-09-08T22:29:36+09:00 | main | feat: one overview call for the hospital status screen |
| 009f858dbabbdbb86375c769540e13e1608a02d8 | 2026-09-08T22:41:39+09:00 | main | feat: header and hospital list speak the three states |
| af0a8b55f81c83c8dbd2aa2fa99fced9834b6a10 | 2026-09-08T22:54:06+09:00 | main | fix: overview and list agree on operator exceptions, serving gate, quota and measurement date |
| a206c6781fa80cd33f220573c16822368d7b6b70 | 2026-09-08T23:17:56+09:00 | main | docs: PR-1B plan — the info screen (facts, brand, photos, domain, evidence) |
| 15b955ab73c1e946a9357a69fae86e8f3604c666 | 2026-09-08T23:24:19+09:00 | main | fix: list, header and overview give one answer and never hide human work |
| 41b01a3c5d57f30e41513160b866d572bc36485f | 2026-09-08T23:27:16+09:00 | main | docs: close PR-1A with the verification record |
| 1d94791580028aaeac51d04a1c22ed5866335630 | 2026-09-08T23:34:33+09:00 | main | feat: profile completeness is derived on the server and the missing requirements are labelled |
| 1032ed974da986f0b3922958ead779648a407565 | 2026-09-08T23:36:04+09:00 | main | feat: info screen — facts, official channels and the remaining required items |
| f0b5d723da9f83ff7ddda7691c727deb168cabe2 | 2026-09-08T23:47:36+09:00 | main | feat: saving an official channel registers and processes it as evidence automatically |
| 3260de79cd43d9f1ff8af9068b704d12d76795b2 | 2026-09-08T23:50:48+09:00 | main | feat: info screen — public page brand, logo, photos with rights, custom domain |
| cf6ceebdccefd526dbb9b6b027fb04e47e2f4096 | 2026-09-08T23:58:13+09:00 | main | feat: info screen — evidence sources and notes with only the human toggles |
| 3f628908d238729bebe809165e9e5e733f295f52 | 2026-09-09T00:19:45+09:00 | main | docs: PR-1C plan — the content screen (month table, publish days, sample confirmation only) |
| 637539954e942566e31061f1d7b500f9fbf7cd9e | 2026-09-09T00:32:26+09:00 | main | feat: every article row carries the site's judgment and its incident link |
| 86814c6fec778df88ba1671d72896a1e81b3476b | 2026-09-09T00:41:31+09:00 | main | fix: channel evidence registers durably, saves never clobber another section, honest source states |
| 6476df7396e4eae49ffb551111d8e979a8472b37 | 2026-09-09T00:43:32+09:00 | main | docs: runbook — migration head 0070 and the two content-queue tasks added at the 2026-09-09 checkpoint |
| 13e95341376c6403646f90e79e7688d7388830eb | 2026-09-09T00:54:03+09:00 | main | fix: photo uploads honour the public flag, document uploads are validated and served safely, info screen keeps only human toggles |
| 6d4982f5e80794ac0369da5ca25d8e9c0dd289c3 | 2026-09-09T00:55:34+09:00 | main | docs: record PR-1B execution and draft the checkpoint-1 release note |
| 6f299624a9ebd748ffdc8f72cff5901a9336b80c | 2026-09-09T00:56:36+09:00 | main | docs: checkpoint-1 verification record |
| f6b7870344c1dcf2f62df5b17489fb568f4538a4 | 2026-09-09T01:03:22+09:00 | main | docs: record the Astra checkpoint-1 first-pass verdict |
| 194477499f04999d0d4737d5c4fcc235c1775332 | 2026-09-09T01:03:52+09:00 | main | feat: readiness reports the recertification candidate count and NULL noise-hash approvals as deploy evidence |
| e4c174de4af7ccf79f47b9ea86bad8696954c1f2 | 2026-09-09T01:27:03+09:00 | main | fix: checkpoint-1 release blockers — per-execution paid accounting, transactional channel rows, sweep eligibility and reconciliation, hospital gate on content rows, transitional profile body compatibility |
| 8dbb18632459e0e7cf60f6080120bc1bab73cc38 | 2026-09-09T01:30:02+09:00 | main | docs: checkpoint-1 re-verification after the blocker fixes |
| e71d4cb2c7f7b5a7e8212d85af4d2d5aa94e743e | 2026-09-09T01:32:33+09:00 | main | docs: Astra second-pass SHIP verdict and the post-deploy evidence checklist |
| 4bd1e0312a9178ad53c2f1065ec42798d81d7c7c | 2026-09-08T09:45:09-07:00 | main | Merge pull request #91 from wjlee930501/claude/integrity-hitl-simplification |
| c8b8727555c1e364d7003c8f129542068935e6b7 | 2026-09-09T01:58:55+09:00 | main | docs: checkpoint-1 deployment evidence |
| c3e8fccf019a3143cca604e28f23b1855c5fa7a3 | 2026-09-09T02:00:37+09:00 | main | feat: content screen shows the site's judgment per row and keeps only sample confirmation |
| 0f240b3c480ddc8c2563b93d8afac1dc348015da | 2026-09-09T02:09:01+09:00 | main | feat: content screen — publish days and read-only patient questions and exposure suggestions |
| 998ac3ad2a7de089ac2407e6c82fe6c2cbe92de5 | 2026-09-09T02:11:31+09:00 | main | docs: PR-1D plan — status screen with executable exception cards, incident assignment (H-15) |
| 3ca8d5a4ce2d84d8fae1188c0ce09f1d2e4442bf | 2026-09-09T02:33:55+09:00 | main | feat: exception cards carry executable actions; incidents are auto-assigned and assignable (H-15) |
| acb0686ce08b2118dc4ad5d0f173de3493f07fb2 | 2026-09-09T02:39:13+09:00 | main | fix: content rows link to routable operations views, ignore automatic recovery and superseded runs; takedown allowed for any published article |
| b0ffa1db43342a2cafbb7d91e0ffe3762f3a13bf | 2026-09-09T02:40:49+09:00 | main | fix: onboarding next-action copy points at the new screens; auto-assign skips inactive AEs |
| 6c3cd1c796e44a7c519e717dd9132a37f64a1991 | 2026-09-09T02:41:06+09:00 | main | docs: PR-1E plan — legacy route deletion, four tabs, global term guard, contract registration, reports dialog |
| 21d93d4efacba4bddbe359e121a1849505be1fe2 | 2026-09-09T02:46:16+09:00 | main | feat: operations center — assign or change the incident owner; unified status terms |
| 04d8fa8484d14609149f3ca66265b0252e240aeb | 2026-09-09T02:51:38+09:00 | main | feat: hospital status screen — three state cards, executable exception cards, month summary |
| 68cbe19b97b23cc55e91e8b266b870a34eaf64c0 | 2026-09-09T03:24:55+09:00 | main | feat: require a BFF-signed actor assertion on human admin mutations |
| 8de0590c14094d68551594ad16bebd458a7c6715 | 2026-09-09T03:32:29+09:00 | main | fix: one definition of a final monthly measurement; isolate milestone projection per report |
| 55733be8883a3da8094efe66a6a5170ef6c4d658 | 2026-09-09T03:40:04+09:00 | main | fix: V0 cannot start before the profile is complete; cost deferral says why (H-12) |
| d04f98845fa33b657f7958529b16dc892cd82091 | 2026-09-09T03:42:24+09:00 | main | fix(admin): bind delivery records to the bytes actually downloaded (M-08) |
| 46ca2c0c01c8d99a308dfe9bfbd68f0b9aecd96c | 2026-09-09T03:48:11+09:00 | main | feat: seed patient questions from the V0 matrix when a hospital has none (M-18) |
| dc537f390b6c650f9bbd9a5b0f65c7047453ec94 | 2026-09-09T03:50:03+09:00 | main | fix: assignment on every incident path and screen, actionable-first incident queue, resolvable deep links, gated approvals and audited takedown |
| 80b4e26f5970b3c3bc39398e0e4ada50bc33ae0f | 2026-09-09T03:52:22+09:00 | main | docs: record PR-1C and PR-1D execution |
| d525b25f4e91bbd5d2813b9698e0c064d7bddcc3 | 2026-09-09T03:54:42+09:00 | main | chore(db): drop PLAN_8 from the plan enum and constrain content_schedules.plan (M-20) |
| 69590de5532cec3df89e183265eec7c9561c1215 | 2026-09-09T04:02:19+09:00 | main | feat: reports tab — one dialog for open, deliver, correct |
| 8be9fbd0ba990602fe6fdb404b530a2b35c234c6 | 2026-09-09T04:05:16+09:00 | main | feat: four hospital tabs; legacy routes redirect to the new screens; old screens removed |
| b6440f7dbede15991a91f15107db615194151464 | 2026-09-09T04:09:56+09:00 | main | fix: bound site-build recovery with the REBUILD_SITE operation-run budget (H-13) |
| 0c1c2c7d1484ee7d33e6c1b6d82b375a87ff88c0 | 2026-09-09T04:11:49+09:00 | main | feat: one-screen contract registration creates the hospital and accepts the handoff |
| 74e281188607c92d344f55e0f76ab30043fdb54e | 2026-09-09T04:28:15+09:00 | main | feat: the unified-term guard covers the whole admin |
| 9df28d96b8c98211afb010d2f834e2db8fb2e716 | 2026-09-09T04:47:17+09:00 | main | fix: sweep-owned site-build incidents — no per-attempt alerts, reopen on recurrence, recover on retry (H-13 review) |
| 81ed1cf8d14d8ceafef1574323410a4cdef68e37 | 2026-09-09T04:50:34+09:00 | main | fix: contract registration — accepted handoffs are not overdue, leads convert, errors name their cause (PR-1E review) |
| 9fa4d8acd1797d06d6b1d94885494cc72b85c59c | 2026-09-09T05:04:45+09:00 | main | docs: CLAUDE.md 2.6 admin contracts, PR-0D/PR-1E records, register updates; copy names the 병원 정보 tab |
| 6bcc59c07d1acbc82f5ed1bc8fee8513243dd06b | 2026-09-09T05:15:48+09:00 | main | fix: site-build incidents — operator retries stay visible, locked touch/reopen, budget resets after success (H-13 round 2) |
| 806a1e4fbdf91027e80e651e2d0f6dfa070fc7d4 | 2026-09-09T05:19:48+09:00 | main | docs: checkpoint-2 record (scope, cautions, clean-DB verification), H-13 round-2 in PR-0D record and register |
| fc423ab21cc73de4b8208a455ef0aea29e326cb7 | 2026-09-09T05:27:32+09:00 | main | fix(guard): the copy allowlist excludes only backend internal modules, not any 'models/' or 'schemas/' path under admin or site |
| 26d7765e431c48b187bc0b173a0fe89fc1ea26dc | 2026-09-09T05:31:05+09:00 | main | fix(db): 0071 keeps the plan enum type and adds CHECK constraints instead (rolling-deploy safe) |
| f93a2cb7de4055ac206d908417b5770a20d40ae2 | 2026-09-09T05:33:52+09:00 | main | fix: content rows stay quiet while automatic recovery owns a failed run (Astra B4) |
| e14bea9777e46d6eb035cd10bafa1f7f92160f83 | 2026-09-09T05:38:03+09:00 | main | fix: site-build budget key stays monotonic after a success; one hospital-level incident for operator failures (H-13 round 3) |
| 7f8345516efe5cf22daa678f5bf37c1ea35596c4 | 2026-09-09T05:38:56+09:00 | main | fix(security): the verified actor is the only authority for human admin mutations; system jobs cannot impersonate accounts (Astra B2) |
| 7cbcf80d5bcbcc282e1e2ef5264c805a77d17ad6 | 2026-09-09T05:40:58+09:00 | main | docs: checkpoint-2 record after Astra round 1 (blockers → fixes), runbook rollout window wording, PR-0D record updates |
| 4d7f419646260aca4de30fbbfe6c8fefcb03276e | 2026-09-09T05:42:29+09:00 | main | docs: checkpoint-2 verification after blocker fixes (backend 3,443/0) |
| 8010f0e9f52acbeeb7215dd06e8a0f7817e38951 | 2026-09-09T05:43:22+09:00 | main | fix: sweep key date comparison uses UTC explicitly, independent of the DB session time zone |
| 605490a4c53892d36f111463825c1addea670d88 | 2026-09-09T05:46:54+09:00 | main | fix: BFF trims BFF_ACTOR_SECRET like the API; runbook creates the secret without a trailing newline; copy-guard allowlist is prefix-matched (Astra pass 2) |
| d36f999dc481d1bc9964455d41309b2fdc68a459 | 2026-09-09T05:48:56+09:00 | main | docs: checkpoint-2 gate — Astra pass 2 HOLD (secret newline) → pass 3 SHIP |
| 82d6ac258af39750ea43124399b96f2fa4106f7a | 2026-09-09T05:57:45+09:00 | main | fix(setup): setup-gcp.sh creates the BFF_ACTOR_SECRET container and grants the frontend SA (deploy preflight policy) |
| a77485155dce67eb8309b74b42410e73a648797e | 2026-09-08T14:02:36-07:00 | main | Merge pull request #92 from wjlee930501/claude/phase1-content-status |
| ee59c6f685b9f9b476ec501a157290518057cc0f | 2026-09-09T06:14:23+09:00 | main | docs: checkpoint-2 deployment evidence (release a774851, head 0071, 8 hospitals healthy) |
| 85cd93e48df491c91bc3832ecd56c8b56094558f | 2026-09-08T14:20:06-07:00 | main | Merge pull request #93 from wjlee930501/claude/checkpoint-2-evidence |
| f2ec20cbacd133865a42464e93935a559ecf00d0 | 2026-09-08T23:28:29Z | main | fix: heal essence blockers before paging exhausted publication gates |
| a6faf1dfc5a37e4dd5b15774584daca2028ba53b | 2026-09-08T16:42:42-07:00 | main | Merge pull request #94 from wjlee930501/fix/publish-block-selfheal |
| 8c5914171660f41374a33a3c58b46c4b831ecfc5 | 2026-09-09T00:40:53Z | main | fix: keep approved essence as stable base |
| d34734fc41c7767c97b5dabd9289e26260982bde | 2026-09-09T02:51:49Z | main | fix: align Postgres contracts with approved base readiness |
| db0d088c3584c163f1ae25b60ac47992e7c79ce0 | 2026-09-08T21:10:09-07:00 | main | Merge pull request #95 from wjlee930501/fix/essence-base-current-readiness |
| db082afeed03b5e432b6e7777ac63de872657349 | 2026-09-09T15:04:41Z | main | feat(site): 매니저 검토 반영 — 랜딩 카피·접수 표시·요금제 정리 |
| e2367f9ae5aee4080380d71008abfafac850e54f | 2026-09-09T08:18:04-07:00 | main | Merge pull request #97 from wjlee930501/claude/landing-page-updates-pu8x3b |
| b18953fac5ad1a62e750e67134fa53c075e5dc01 | 2026-09-10T08:28:12Z | main | fix(site): soften limitItems copy for growth, ads, and review |
| d1a500b624658c79d480601087b58a06fa40122c | 2026-09-10T08:32:56Z | main | fix(site): keep soft limit copy within growth and sampling guards |
| a8ffb64979453472d3ee2866a5c44de8cf160d9a | 2026-09-10T01:38:52-07:00 | main | Merge pull request #98 from wjlee930501/fix/landing-limit-copy-soft |
| 837ae2a5cce08be0314f8246267ba20db6d60435 | 2026-09-11T16:35:19Z | main | fix: keep monthly report source_stale as audit, not delivery blocker |
| cfb12b9ec080c8fa0234e7cb7e2a8a50898a5bfe | 2026-09-11T09:41:55-07:00 | main | Merge pull request #99 from wjlee930501/fix/monthly-source-stale-audit-only |
| 2d3674e7432af8fbd9b7cd25faa9e577e43c0fab | 2026-09-09T02:45:21Z | main | feat: apply director feedback as stable essence deltas |
| 4c2732e365350a4bebafd442ecf718da996aed38 | 2026-09-11T17:05:32Z | main | fix: invalidate content briefs when director deltas change |
| e1e51efc2518ac8a73b9ecca6eef8794db94c43a | 2026-09-11T17:13:46Z | main | fix: expect alembic head 0073 in upgrade postgres test |
| 2c465bb57aa638de62a3b63244cf62a5deeb6055 | 2026-09-11T10:19:43-07:00 | main | Merge pull request #100 from wjlee930501/fix/essence-director-delta |
| c6051646a717005970f342de8d830d0ebcaf8a22 | 2026-09-11T22:20:32Z | main | fix: require director_delta_ids on content brief match |
| 5b4a2415fe2f5aad54ad806a0317e37b4b4e74f7 | 2026-09-11T15:26:16-07:00 | main | Merge pull request #101 from wjlee930501/fix/brief-delta-ids-required |
| c3f3021f2fa2cd28960701c9dffded34c73dc203 | 2026-09-12T01:11:29Z | main | fix: roll up generation rejections weekly |
| b974db22aaceeebbed8fda9f0ceeb88e7bb551fa | 2026-09-12T01:29:12Z | main | fix: narrow content gate false positives |
| 1ed6454e9e9f493106dd049bc29ac53ac2b45d1d | 2026-09-12T02:16:09Z | main | fix: recover rejected content after gate updates |
| 55fa24e10e7107edd97851fd3261c3cd622c2625 | 2026-09-12T02:30:24Z | main | fix: restore morning cadence for generation failures |
| 3001f3ecfe435b8e79aae356aca733e64275ca93 | 2026-09-12T03:10:30Z | main | fix: bind free screening exception to claim target |
| ef15c47ac9966888699819356c967e5eafb5cb08 | 2026-09-12T03:21:24Z | main | fix: scope free screening exemption per claim |
| 197f4bb5531b9df68150d003676ab1fa84c645d6 | 2026-09-11T20:27:32-07:00 | main | Merge pull request #102 from wjlee930501/fix/slack-noise-rollup-loop |
| 4598d076f775ccb80283c45d8bf197e7cc3bdf04 | 2026-09-12T07:02:56Z | main | fix: prevent content publish schedule slips |
| 4662fd420b44aae3400006fba8ac5fe9d5a088cc | 2026-09-12T07:10:24Z | main | fix: regenerate repairable stored content |
| 7af5545a92ec76d99d04bde3422d114cd30ae9e7 | 2026-09-12T07:20:08Z | main | fix: block image spend after hard rewrite |
| ca48ee87b88bc4391afc56042842dcb06fcb342f | 2026-09-12T00:25:34-07:00 | main | Merge pull request #103 from wjlee930501/fix/publish-schedule-slip-h123 |
| 0182ececb7d46000057a17f2a4fbb546a43ab2f6 | 2026-09-12T17:54:15Z | main | docs: add content yield version-up plan and release record skeleton |
| 2d5cdeb6a3f4d6337da12814d37e88caa9fa2f82 | 2026-09-12T17:57:37Z | main | docs: WP-5 becomes certified image reuse instead of image-less publish |
| 3f25acef822f72fa82aee63f6740907e9980092f | 2026-09-12T18:05:17Z | main | feat: escalate low-confidence content reviews instead of blocking forever |
| 29ec46563f65fb55cca29dd53c177977b99718ec | 2026-09-12T18:06:55Z | main | feat: align writer prompt with validators and feed rejections back |
| 8992c197a7a876894c5b7b5b4467418eb2753708 | 2026-09-12T18:12:43Z | main | feat: reuse a certified hospital image when image generation fails |
| f54e5763c8dc41414780ccb76c8157450e242249 | 2026-09-12T18:18:17Z | main | docs: record v2.7 content yield contracts in CLAUDE.md, system map, Slack policy |
| d53704157426e5dfd1bff159d6144ae7d03461c4 | 2026-09-12T18:27:36Z | main | feat: bounded sample retries, essence auto re-review, and content yield facts |
| 7afd492c54ef364a9a29baf84974a46895f3cef3 | 2026-09-13T03:47:32Z | main | Merge branch 'cursor/review-wave-27-37-2946' (read-only wave 27-37 review record) |
| 74d289df9bbd7e8f87eaab78ec01276933c5db96 | 2026-09-13T09:32:56Z | main | feat: external pipeline watchdog independent of Celery Beat |
| 9b8fdcab52d016c034eb81c85e1d35e72aaece7c | 2026-09-13T09:43:42Z | main | feat: fan-out generation, reference/duplicate safeguards, image diagnosis and hero fallback |
| beca1191a817d5e1d58301e13a37231f803ddf6a | 2026-09-14T01:26:37Z | main | fix: surface introduction inquiries in Slack and admin |
| e0a75700473c198bf87af189ddb7ed31a1aad2e3 | 2026-09-14T01:38:26Z | main | fix: always use inquiry copy for lead alerts |
| 296d0dcc287c2fb8873ce2f285bc4a2719df1d85 | 2026-09-13T18:51:30-07:00 | main | Merge pull request #104 from wjlee930501/fix/inquiry-admin-slack-visibility |
| 507f14920f1e10dbe465da8966d4932935876833 | 2026-09-14T05:32:38Z | main | fix: keep inquiry diagnoses admin-only |
| 3ef6f6d0177813d53d04f1391bb91057d038ada9 | 2026-09-14T05:44:58Z | main | fix: shorten inquiry hold copy to 콜용/고객 미발송 |
| f3863162a8c676477c221d503832e1f35431a709 | 2026-09-13T22:52:50-07:00 | main | Merge pull request #105 from wjlee930501/fix/inquiry-diagnosis-admin-only |
| d54c1e135ac367a7320525c829e643048addd4bf | 2026-09-15T01:21:46Z | main | fix(leads): 내부 진단 생성이 도입문의 표식을 덮지 않는다 |
| 32945b0c0cf0bd8a5a31962ced4bb29788f8e9e3 | 2026-09-15T01:21:53Z | main | chore(notifier): notify_lead_created의 미사용 clinic_type 인자를 제거한다 |
| dc7c508c6a1b5d22a880e067a3c87a5ae2bbad57 | 2026-09-14T18:36:38-07:00 | main | Merge pull request #106 from wjlee930501/cursor/inquiry-marker-and-notifier-cleanup-436b |
| ea7e3773b074747bb4fa588f2d9a831244f6822d | 2026-09-15T01:48:49Z | main | fix(leads): 도입문의 콜용 진단 행에서 고객 전달 문구를 없앤다 |
| 89bf894fef3f201ed7bc94cc9859c1149367d5e2 | 2026-09-15T01:51:52Z | main | fix(leads): 고객 영향 문구를 콜용 진단 행의 발송 상태로도 막는다 |
| 5fb91003211b39907ea16d3327b2d30e9bafa28a | 2026-09-14T19:00:45-07:00 | main | Merge pull request #107 from wjlee930501/cursor/inquiry-internal-ops-copy-d14a |
| 9537a9b6a3d8deb14c57022fa6c620deeb836625 | 2026-09-15T10:52:07+09:00 | main | feat: fall back to OpenAI gpt-image-2.5 when the Gemini image path fails |
| 3d1dc1af42eb0b88014e9d7c6ccbe1628d6e2a98 | 2026-09-15T11:15:03+09:00 | main | test: keep the OpenAI image fallback off by default in the test environment |
| be161d461f1db3fdf738fc69a1fdcdd1b6d1da8e | 2026-09-15T11:32:41+09:00 | main | chore(ci): make the Terraform job green again |
| 64cf6dbc35193fc875fa66a3b88c4274ee8c918d | 2026-09-15T11:32:41+09:00 | main | fix(deps): upgrade next to 16.3.3 and sharp to 0.35.4 in site and admin |
| c57a00fd52d85e4f695f4d7633873f119c0b9d54 | 2026-09-14T19:38:53-07:00 | main | Merge pull request #108 from wjlee930501/claude/pull-request-e2bee4 |
| fa6cdfbd1439d0d6569cf12f6da279c10ba3e2be | 2026-09-15T03:06:37Z | main | feat: auto-create the inquiry diagnosis and text the director on public intake |
| 8db5bf7a4ddadf7f98f66b3f4ce464bc12081d02 | 2026-09-14T20:16:19-07:00 | main | Merge pull request #109 from wjlee930501/claude/inquiry-auto-diagnosis-sms |
| 929cf0b1168351cb7fc2db4a9eb19b267e343e34 | 2026-09-15T12:42:11+09:00 | main | fix(content): carry writer and review JSON through forced tool use, not text |
| e8d35bdd6098efdf574a22ae207e5e2ed89fbb84 | 2026-09-15T12:42:11+09:00 | main | feat(backend): hospital physicians, address detail, non-blocking geocoding |
| b5ad7a16f0a91fcf4272e3079ba6bb4832dbfcaa | 2026-09-15T12:42:11+09:00 | main | feat(admin): physician rows, hero designation, honest blocker and slot copy |
| 1c4bd7812b01205afb4f62ae9f04860353005cac | 2026-09-15T12:42:11+09:00 | main | feat(site): one Physician per doctor, curated gallery, editorial polish |
| 01471db163d4a506e769028a17347eda8463990e | 2026-09-15T12:58:55+09:00 | main | chore(ci): keep the 0064→head migration test and env templates in step with main |
| 31dbb38943a82a72d3921b7064e69f373f16657e | 2026-09-14T21:05:06-07:00 | main | Merge pull request #110 from wjlee930501/feature/medical-staff-and-onboarding |
| 1b119c8511d0919dd66e6f3f46859a2e122561c3 | 2026-09-15T14:36:40+09:00 | local / other / stash | feat(site): rebuild the hospital home on a token-driven layout layer |
| 4ed50d74f85bb088894a23ab4cdb4f6ee345ab63 | 2026-09-15T18:38:06+09:00 | main | fix(worker): make generation recovery actually retry within its budget |
| aa17f2aa0f8a65a7a50e3701dd200f0af533642f | 2026-09-15T18:38:06+09:00 | main | feat(admin): stop raising the post-publish review sample as operator work |
| dbf8a263178c93dc1a8df5091466fb00d9068738 | 2026-09-15T18:38:06+09:00 | main | refactor(worker): treat channel source fetch failures as source state, not incidents |
| 3e0999cb3528d349c0455a173b7eafd808eab55e | 2026-09-15T18:38:39+09:00 | main | feat(worker): swap the topic once before a body slot becomes operator work |
| 174b887473b3710cafdece921d1dd76f18eb4ed9 | 2026-09-15T18:38:39+09:00 | main | docs: record the operator-label ladder contracts and plan |
| d96bd2143ba509cd1fcae73f028286b6d938ae8e | 2026-09-15T02:46:30-07:00 | main | Merge pull request #111 from wjlee930501/claude/reputation-admin-labels-8d1f62 |
| 4db1b69e0c29033dd28df18f83597766a6887e9a | 2026-09-15T23:02:25+09:00 | local / other / stash | Harden autonomous GEO operations, authority changes, monthly quota and feedback loop |
