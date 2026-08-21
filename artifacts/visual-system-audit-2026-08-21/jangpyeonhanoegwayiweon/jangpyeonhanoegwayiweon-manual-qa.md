# manualQa — 장편한외과의원 공개 사이트

실행일: 2026-08-21. 모든 시나리오는 Aside REPL에서 `snapshot()`을 먼저 읽고, 최종 PNG를 로컬로 복사한 뒤 `sips`와 `view_image`로 확인했다. `fullPage:true` 캡처는 반응형 에뮬레이션에서 반복 타일로 저장되어 증거에서 제외했다.

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| jang-01-desktop | C1 route capture; C2 desktop 1440×900 | `/` | `openTab('https://jangclinic.kr/'); setVp(1440,900); snapshot(page,{interactive:true}); page.screenshot({path:'01-desktop-1440x900.png',fullPage:false})` | PASS | png-01d, metrics |
| jang-01-mobile | C1 route capture; C2 mobile 390×844 | `/` | `openTab('https://jangclinic.kr/'); setVp(390,844); snapshot(page,{interactive:true}); page.screenshot({path:'01-mobile-390x844.png',clip:{x:0,y:0,width:390,height:844}})` | PASS | png-01m, metrics |
| jang-02-desktop | C1 route capture; C2 desktop 1440×900 | `/contents` | `openTab('https://jangclinic.kr/contents'); setVp(1440,900); snapshot(page,{interactive:true}); page.screenshot({path:'02-desktop-1440x900.png',fullPage:false})` | PASS | png-02d, metrics |
| jang-02-mobile | C1 route capture; C2 mobile 390×844 | `/contents` | `openTab('https://jangclinic.kr/contents'); setVp(390,844); snapshot(page,{interactive:true}); page.screenshot({path:'02-mobile-390x844.png',clip:{x:0,y:0,width:390,height:844}})` | PASS | png-02m, metrics |
| jang-03-desktop | C1 discovered first content detail; C2 desktop 1440×900 | `/contents/a30cf244-455c-4758-aaf6-f4c6affcd7dd` | `openTab('https://jangclinic.kr/contents/a30cf244-455c-4758-aaf6-f4c6affcd7dd'); setVp(1440,900); snapshot(page,{interactive:true}); page.screenshot({path:'03-desktop-1440x900.png',fullPage:false})` | PASS | png-03d, metrics |
| jang-03-mobile | C1 discovered first content detail; C2 mobile 390×844 | `/contents/a30cf244-455c-4758-aaf6-f4c6affcd7dd` | `openTab('https://jangclinic.kr/contents/a30cf244-455c-4758-aaf6-f4c6affcd7dd'); setVp(390,844); snapshot(page,{interactive:true}); page.screenshot({path:'03-mobile-390x844.png',clip:{x:0,y:0,width:390,height:844}})` | PASS (capture valid; responsive table finding recorded below) | png-03m, metrics |
| jang-04-desktop | C1 route capture; C2 desktop 1440×900 | `/doctor` | `openTab('https://jangclinic.kr/doctor'); setVp(1440,900); snapshot(page,{interactive:true}); page.screenshot({path:'04-desktop-1440x900.png',fullPage:false})` | PASS | png-04d, metrics |
| jang-04-mobile | C1 route capture; C2 mobile 390×844 | `/doctor` | `openTab('https://jangclinic.kr/doctor'); setVp(390,844); snapshot(page,{interactive:true}); page.screenshot({path:'04-mobile-390x844.png',clip:{x:0,y:0,width:390,height:844}})` | PASS | png-04m, metrics |
| jang-05-desktop | C1 route capture; C2 desktop 1440×900 | `/treatments` | `openTab('https://jangclinic.kr/treatments'); setVp(1440,900); snapshot(page,{interactive:true}); page.screenshot({path:'05-desktop-1440x900.png',fullPage:false})` | PASS | png-05d, metrics |
| jang-05-mobile | C1 route capture; C2 mobile 390×844 | `/treatments` | `openTab('https://jangclinic.kr/treatments'); setVp(390,844); snapshot(page,{interactive:true}); page.screenshot({path:'05-mobile-390x844.png',clip:{x:0,y:0,width:390,height:844}})` | PASS | png-05m, metrics |
| jang-06-desktop | C1 route capture; C2 desktop 1440×900 | `/visit` | `openTab('https://jangclinic.kr/visit'); setVp(1440,900); snapshot(page,{interactive:true}); page.screenshot({path:'06-desktop-1440x900.png',fullPage:false})` | PASS | png-06d, metrics |
| jang-06-mobile | C1 route capture; C2 mobile 390×844 | `/visit` | `openTab('https://jangclinic.kr/visit'); setVp(390,844); snapshot(page,{interactive:true}); page.screenshot({path:'06-mobile-390x844.png',clip:{x:0,y:0,width:390,height:844}})` | PASS | png-06m, metrics |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| jang-01-desktop / jang-01-mobile | C3 visual integrity | image failure / blank hero | hero and visible hospital assets load without broken-image state | PASS; home image count 8, failed 0 | png-01d, png-01m, metrics |
| jang-02-desktop / jang-02-mobile | C3 visual integrity | image failure / blank card | representative content image loads; no failed image | PASS; image count 1, failed 0 | png-02d, png-02m, metrics |
| jang-03-desktop / jang-03-mobile | C4 title reflow | long Korean title | H1 and section headings wrap within viewport without clipping | PASS; H1 2 lines desktop/mobile; mobile section headings 2–3 lines | png-03d, png-03m, metrics |
| jang-03-mobile | C5 responsive overflow | markdown table overflow | content tables remain readable within the mobile content column | FAIL; table scrollWidth 620 > clientWidth 312 | png-03m, metrics |
| jang-04-desktop / jang-04-mobile | C3 visual integrity | meaningful portrait asset | doctor portrait is visible and not blank | PASS visually; image count 1, failed 0 | png-04d, png-04m, metrics |
| jang-04-desktop / jang-04-mobile | C6 accessibility signal | meaningful-image alt | meaningful doctor portrait has a non-empty accessible alternative | FAIL; DOM image alt is empty string | png-04d, png-04m, metrics |
| jang-05-desktop / jang-05-mobile | C2 responsive reflow | dense 12-item list | treatment rows stack/reflow without page-level horizontal overflow | PASS; scrollWidth equals clientWidth at both viewports | png-05d, png-05m, metrics |
| jang-05-desktop / jang-05-mobile | C3 visual integrity | image failure | image-specific check | NOT_APPLICABLE — route intentionally contains no image elements (count 0) | metrics |
| jang-06-desktop / jang-06-mobile | C3 visual integrity | image failure / gallery blank | visit gallery images load without failed-image state | PASS; image count 5, failed 0 | png-06d, png-06m, metrics |
| jang-01-mobile / jang-02-mobile / jang-03-mobile / jang-04-mobile / jang-05-mobile / jang-06-mobile | C2 responsive reflow | viewport crop / page-level overflow | mobile surface fits 390px viewport without page-level horizontal scroll | PASS for all page roots; each scrollWidth/clientWidth is 390/390 (detail table exception separately failed) | png-01m, png-02m, png-03m, png-04m, png-05m, png-06m, metrics |
| jang-01-desktop / jang-01-mobile / jang-02-desktop / jang-02-mobile / jang-03-desktop / jang-03-mobile / jang-04-desktop / jang-04-mobile / jang-05-desktop / jang-05-mobile / jang-06-desktop / jang-06-mobile | C3 visual integrity | blank/loading/repeated section | fresh capture shows the requested surface, not loading/blank/repeated tile state | PASS for final viewport-only captures; the rejected fullPage tiled captures are not used | all PNGs, metrics |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| png-01d | screenshot | home desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/01-desktop-1440x900.png` |
| png-01m | screenshot | home mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/01-mobile-390x844.png` |
| png-02d | screenshot | contents desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/02-desktop-1440x900.png` |
| png-02m | screenshot | contents mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/02-mobile-390x844.png` |
| png-03d | screenshot | first discovered public content detail desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/03-desktop-1440x900.png` |
| png-03m | screenshot | first discovered public content detail mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/03-mobile-390x844.png` |
| png-04d | screenshot | doctor desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/04-desktop-1440x900.png` |
| png-04m | screenshot | doctor mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/04-mobile-390x844.png` |
| png-05d | screenshot | treatments desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/05-desktop-1440x900.png` |
| png-05m | screenshot | treatments mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/05-mobile-390x844.png` |
| png-06d | screenshot | visit desktop 1440×900 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/06-desktop-1440x900.png` |
| png-06m | screenshot | visit mobile 390×844 | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/06-mobile-390x844.png` |
| metrics | data | route/viewport DOM metrics, image counts, headings and CTA evidence | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/metrics.json` |
| report | report | screen-by-screen visual audit with strengths, UX/accessibility risks and system implications | `/Users/woojinlee/Documents/projects/reputation/artifacts/visual-system-audit-2026-08-21/jangpyeonhanoegwayiweon/report.md` |
