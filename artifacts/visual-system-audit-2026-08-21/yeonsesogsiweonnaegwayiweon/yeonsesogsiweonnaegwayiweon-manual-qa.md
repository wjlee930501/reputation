# manualQa — 연세속시원내과의원

- Run date: 2026-08-21
- Surface: `https://reputation.motionlabs.kr/yeonsesogsiweonnaegwayiweon`
Evidence directory: `artifacts/visual-system-audit-2026-08-21/yeonsesogsiweonnaegwayiweon`

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| home-desktop | VISUAL-001 | home / desktop 1440x900 | Aside REPL `openTab(base); snapshot(page,{interactive:true}); page.screenshot({path:'./home-desktop-1440x900.png'})` | PASS | A01 |
| home-mobile | VISUAL-001 | home / mobile 390x844 | Aside REPL `openTab(base); Target.attachToTarget + Emulation.setDeviceMetricsOverride(390,844); snapshot(page,{interactive:true}); page.screenshot({clip:{x:0,y:0,width:390,height:844}})` | PASS | A02 |
| contents-desktop | VISUAL-001 | `/contents` / desktop 1440x900 | Aside REPL `openTab(base+'/contents'); snapshot(page,{interactive:true}); page.screenshot({path:'./contents-desktop-1440x900.png'})` | PASS | A03 |
| contents-mobile | VISUAL-001 | `/contents` / mobile 390x844 | Aside REPL same route with `Target.attachToTarget + Emulation.setDeviceMetricsOverride(390,844)` then clipped screenshot | PASS | A04 |
| detail-desktop | VISUAL-001 | first public content detail / desktop | Aside REPL `openTab(base+'/contents/416fc295-cdf0-4c68-9e85-3677b6c3d793'); snapshot(...); screenshot(...)` | PASS | A05 |
| detail-mobile | VISUAL-001 | first public content detail / mobile | Aside REPL same detail route with 390x844 emulation, snapshot, clipped screenshot | PASS | A06 |
| doctor-desktop | VISUAL-001 | `/doctor` / desktop | Aside REPL `openTab(base+'/doctor'); snapshot(...); screenshot(...)` | PASS | A07 |
| doctor-mobile | VISUAL-001 | `/doctor` / mobile | Aside REPL same route with 390x844 emulation, snapshot, clipped screenshot | PASS | A08 |
| treatments-desktop | VISUAL-001 | `/treatments` / desktop | Aside REPL `openTab(base+'/treatments'); snapshot(...); screenshot(...)` | PASS | A09 |
| treatments-mobile | VISUAL-001 | `/treatments` / mobile | Aside REPL same route with 390x844 emulation, snapshot, clipped screenshot | PASS | A10 |
| visit-desktop | VISUAL-001 | `/visit` / desktop | Aside REPL `openTab(base+'/visit'); snapshot(...); screenshot(...)` | PASS | A11 |
| visit-mobile | VISUAL-001 | `/visit` / mobile | Aside REPL same route with 390x844 emulation, snapshot, clipped screenshot | PASS | A12 |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| all-12 | VISUAL-002 | horizontal overflow | `scrollWidth` must equal `clientWidth` at each required viewport | PASS | M01, A01–A12 |
| contents-detail | VISUAL-003 | blank/loading placeholder | final accepted capture must not show a blank/loading hero; wait and recapture if it does | PASS — first contents capture showed a blank lazy placeholder, was rejected and recaptured after wait | A03, A04, M01 |
| home-visit | VISUAL-004 | lazy/failed images | image failures must be rechecked after scrolling lazy images into view | PASS — all 6 home images and all 3 visit images loaded after the recheck | A01, A02, A11, A12, M01 |
| all-12 | VISUAL-005 | Korean wrapping/crop | headings and key CTA text must remain inside viewport without clipping | PASS | A01–A12 |
| all-12 | VISUAL-006 | empty state/repetition | populated surfaces must not render an unintended empty state; repeated sections must remain understandable | PASS — populated content/treatment/photo data observed; intentional repeated CTA sections recorded | A01–A12, M01 |
| all-12 | VISUAL-007 | card/carousel scroll | no unintended horizontal card scroller or carousel should trap content | PASS — no horizontal overflow; treatment/content cards stack or list | A01–A12, M01 |
| all-12 | VISUAL-008 | H1/accessibility structure | each route/viewport must expose exactly one H1 | PASS — H1 count 1 on all 12 captures | M01 |
| all-12 | VISUAL-009 | brand exposure | hospital identity and primary contact path must be visible | PASS — text wordmark, phone and/or directions visible; formal logo asset absent as observed data characteristic | A01–A12 |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A01 | screenshot | home desktop 1440x900 | `./home-desktop-1440x900.png` |
| A02 | screenshot | home mobile 390x844 | `./home-mobile-390x844.png` |
| A03 | screenshot | contents desktop 1440x900 | `./contents-desktop-1440x900.png` |
| A04 | screenshot | contents mobile 390x844 | `./contents-mobile-390x844.png` |
| A05 | screenshot | first public content detail desktop | `./detail-desktop-1440x900.png` |
| A06 | screenshot | first public content detail mobile | `./detail-mobile-390x844.png` |
| A07 | screenshot | doctor desktop 1440x900 | `./doctor-desktop-1440x900.png` |
| A08 | screenshot | doctor mobile 390x844 | `./doctor-mobile-390x844.png` |
| A09 | screenshot | treatments desktop 1440x900 | `./treatments-desktop-1440x900.png` |
| A10 | screenshot | treatments mobile 390x844 | `./treatments-mobile-390x844.png` |
| A11 | screenshot | visit desktop 1440x900 | `./visit-desktop-1440x900.png` |
| A12 | screenshot | visit mobile 390x844 | `./visit-mobile-390x844.png` |
| M01 | data | route/viewport DOM metrics and separated findings | `./metrics.json` |
| R01 | report | visual audit narrative and inventory | `./report.md` |

All PASS verdicts above reference at least one non-empty screenshot or metrics artifact.
