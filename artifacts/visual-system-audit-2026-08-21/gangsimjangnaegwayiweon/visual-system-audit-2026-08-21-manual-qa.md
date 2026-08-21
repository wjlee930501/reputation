# manualQa — 강심장내과의원 공개 표면 시각 감사

Scope: live `https://reputation.motionlabs.kr/gangsimjangnaegwayiweon`, captured in the current Aside REPL run on 2026-08-21. No product code or other worker files were changed.

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| GS-HOME-D | desktop home / hero and CTA | `/gangsimjangnaegwayiweon`, 1440×900 | `openTab(url); snapshot(page); page.screenshot({path:'./artifacts/home-desktop.png',fullPage:false})`; `file`, `shasum -a 256`, `view_image(detail:'high')` | PASS | A1, S1, M1 |
| GS-HOME-M | mobile home / responsive hero | `/gangsimjangnaegwayiweon`, 390×844 | `openTab(url); Target.attachToTarget + Emulation.setDeviceMetricsOverride(390,844); snapshot(page); page.screenshot({clip:{x:0,y:0,width:390,height:844}})`; `file`, `shasum`, `view_image` | PASS | A2, S2, M1 |
| GS-CONTENTS-D | desktop list / 3-content state | `/gangsimjangnaegwayiweon/contents`, 1440×900 | `openTab(url); snapshot(page); page.screenshot({path:'./artifacts/contents-desktop.png',fullPage:false})`; `file`, `shasum`, `view_image` | PASS | A3, S3, M1 |
| GS-CONTENTS-M | mobile list / filters and first card | `/gangsimjangnaegwayiweon/contents`, 390×844 | `openTab(url); mobileize; snapshot(page); page.screenshot({clip:{x:0,y:0,width:390,height:844}})`; `file`, `shasum`, `view_image` | PASS | A4, S4, M1 |
| GS-DETAIL-D | desktop first content detail / article header | first `/contents` item UUID `9489e9c6-02f7-49bf-bd03-a90e3bfa3a3f`, 1440×900 | discovered first link via `/contents` snapshot, then `openTab(detailUrl); snapshot(page); page.screenshot({path:'./artifacts/content-detail-desktop.png',fullPage:false})`; `file`, `shasum`, `view_image` | PASS | A5, S5, M1 |
| GS-DETAIL-M | mobile first content detail / article reflow | same first content detail, 390×844 | `openTab(detailUrl); mobileize; snapshot(page); page.screenshot({clip:{x:0,y:0,width:390,height:844}})`; `file`, `shasum`, `view_image` | REVISE | A6, S6, M1 |
| GS-DOCTOR-D | desktop doctor / photo fallback | `/gangsimjangnaegwayiweon/doctor`, 1440×900 | `openTab(url); snapshot(page); page.screenshot({path:'./artifacts/doctor-desktop.png',fullPage:false})`; `file`, `shasum`, `view_image` | REVISE | A7, S7, M1 |
| GS-DOCTOR-M | mobile doctor / monogram card | `/gangsimjangnaegwayiweon/doctor`, 390×844 | `openTab(url); mobileize; snapshot(page); page.screenshot({clip:{x:0,y:0,width:390,height:844}})`; `file`, `shasum`, `view_image` | REVISE | A8, S8, M1 |
| GS-TX-D | desktop treatments / 12-row list | `/gangsimjangnaegwayiweon/treatments`, 1440×900 | `openTab(url); snapshot(page); page.screenshot({path:'./artifacts/treatments-desktop.png',fullPage:false})`; `file`, `shasum`, `view_image` | PASS | A9, S9, M1 |
| GS-TX-M | mobile treatments / row reflow | `/gangsimjangnaegwayiweon/treatments`, 390×844 | `openTab(url); mobileize; snapshot(page); page.screenshot({clip:{x:0,y:0,width:390,height:844}})`; `file`, `shasum`, `view_image` | PASS | A10, S10, M1 |
| GS-VISIT-D | desktop visit / phone-map-hours CTAs | `/gangsimjangnaegwayiweon/visit`, 1440×900 | `openTab(url); snapshot(page); page.screenshot({path:'./artifacts/visit-desktop.png',fullPage:false})`; `file`, `shasum`, `view_image` | PASS | A11, S11, M1 |
| GS-VISIT-M | mobile visit / stacked action cards | `/gangsimjangnaegwayiweon/visit`, 390×844 | `openTab(url); mobileize; snapshot(page); page.screenshot({clip:{x:0,y:0,width:390,height:844}})`; `file`, `shasum`, `view_image` | PASS | A12, S12, M1 |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| GS-ADV-01 | all 6 routes × 2 viewports | bounds / clipping | `scrollWidth` equals `clientWidth`; no visible edge crop or repeated/blank viewport in accepted capture. | PASS | M1, A1–A12 |
| GS-ADV-02 | all 6 routes × 2 viewports | blank/loading/wrong-state | Capture shows stable route content, not spinner, blank, 429, login wall, or wrong hospital. | PASS | A1–A12, S1–S12 |
| GS-ADV-03 | home, list, detail, doctor, treatments, visit | CJK line breaking | Korean H1/body remains readable at 390px without clipped glyphs, tofu, or detached particle. | PASS | A2, A4, A6, A8, A10, A12, M1 |
| GS-ADV-04 | contents + first detail | data cardinality / repeated-card pressure | 3-content state is accurately represented; filters and related cards do not invent empty or duplicate entries. | PASS | A3–A6, S3–S6, M1 |
| GS-ADV-05 | treatments | long-list/card-scroll pressure | 12 treatment records remain reachable in a predictable single-column list; no hidden horizontal card rail at mobile width. | PASS | A9, A10, S9, S10, M1 |
| GS-ADV-06 | first detail mobile | nested overflow / table scroller | Comparison table is contained and operable without page overflow; a visible cue should indicate horizontal movement. | REVISE | A6, S6, M1 |
| GS-ADV-07 | doctor, given 0 photos | missing-image fallback / identity | No broken-image icon; monogram fallback remains legible and communicates doctor identity when no photo exists. | PASS | A7, A8, S7, S8, M1 |
| GS-ADV-08 | doctor mobile | repeated metadata | Doctor role should be announced once in the visual and linear reading order. | REVISE | A8, S8, R1 |
| GS-ADV-09 | all routes, given no logo/brand-color asset | brand recognition / system fallback | A resilient fallback should preserve hospital identity using an intentional monogram/type/motif system, without pretending unsupported photography or logo data exists. | REVISE | A1, A2, A7, A8, M1, R1 |
| GS-ADV-10 | all routes | CTA discoverability | Phone, map/hours, contents, and official channels are named and reachable from the visible route surfaces. | PASS | A1–A12, S1–S12, M1 |
| GS-ADV-11 | all routes | semantic/accessibility evidence boundary | Named headings/navigation/regions and links are exposed in snapshots; full keyboard, focus, contrast, and screen-reader conformance requires an additional interactive pass. | REVISE | S1–S12, R1 |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A1 | screenshot | Home desktop, RGB PNG, 1440×900, 114407 bytes, SHA-256 `97bd294a52639aae921e3fab319c88040b5b53e40fc8865da85362e204611026` | `home-desktop.png` |
| A2 | screenshot | Home mobile, RGB PNG, 390×844, 61001 bytes, SHA-256 `0767e757c47f71108d7819931151b922085ec292478a3dceef4c2247e8d0ffb5` | `home-mobile.png` |
| A3 | screenshot | Contents desktop, RGB PNG, 1440×900, 291672 bytes, SHA-256 `2d1719816d0094952be364e5dffc4e490c9f360d7e07b48e5dd089952713fbee` | `contents-desktop.png` |
| A4 | screenshot | Contents mobile, RGB PNG, 390×844, 125163 bytes, SHA-256 `edcc5841d7b7e5176ecee9f24571984e7f879f518c73e9b6bd3ef26751c0cbc3` | `contents-mobile.png` |
| A5 | screenshot | First content detail desktop, RGB PNG, 1440×900, 278531 bytes, SHA-256 `e219c049efe78f156e18eae0af80f8c9f0f975334b61e240db0d33f342d1f` | `content-detail-desktop.png` |
| A6 | screenshot | First content detail mobile, RGB PNG, 390×844, 134419 bytes, SHA-256 `0d91ac62da48f35809dbb69927684764fad121c38a228e812a83174afff4851e` | `content-detail-mobile.png` |
| A7 | screenshot | Doctor desktop, RGB PNG, 1440×900, 94861 bytes, SHA-256 `a663ec4897f49a95852116abde9ccd15b1419cc29e9494aba4130b4d31d2a6ea` | `doctor-desktop.png` |
| A8 | screenshot | Doctor mobile, RGB PNG, 390×844, 62469 bytes, SHA-256 `9768f0fb92081800eb9b5f91d1c656282a56b0bf75caac96cf5d2cd324c98f60` | `doctor-mobile.png` |
| A9 | screenshot | Treatments desktop, RGB PNG, 1440×900, 104375 bytes, SHA-256 `0f08dd2c9147bc5e5e1b9d51a3574b4435ecc9b9283189f37a8b814b6c0ab90d` | `treatments-desktop.png` |
| A10 | screenshot | Treatments mobile, RGB PNG, 390×844, 66170 bytes, SHA-256 `3f8aef2c37039d1d86e06adb050d298b9e9606d6baf1e888e355f440c989c9dc` | `treatments-mobile.png` |
| A11 | screenshot | Visit desktop, RGB PNG, 1440×900, 91651 bytes, SHA-256 `c37da48f4f5f98da9eba94d78b4748997fe4eba6655468a162982897dd8049e1` | `visit-desktop.png` |
| A12 | screenshot | Visit mobile, RGB PNG, 390×844, 51761 bytes, SHA-256 `936d9146c7268dbae84c25989e45f9eaf7fee2ac5bda83334c6c082c917f9b0c` | `visit-mobile.png` |
| S1–S12 | snapshot | Accepted Aside accessibility snapshots, one per PNG | `home-desktop.snapshot.txt`, `home-mobile.snapshot.txt`, `contents-desktop.snapshot.txt`, `contents-mobile.snapshot.txt`, `content-detail-desktop.snapshot.txt`, `content-detail-mobile.snapshot.txt`, `doctor-desktop.snapshot.txt`, `doctor-mobile.snapshot.txt`, `treatments-desktop.snapshot.txt`, `treatments-mobile.snapshot.txt`, `visit-desktop.snapshot.txt`, `visit-mobile.snapshot.txt` |
| M1 | data | DOM metrics, image status, H1/CTA/overflow checks, capture inventory and signatures | `metrics.json` |
| R1 | report | Visual, UX, accessibility, and system implications | `report.md` |

## verdict

**REVISE.** All 12 surfaces were captured and are stable/responsive, but the doctor fallback/identity system, repeated mobile role label, and article table affordance should be improved before a clean visual-system pass.
