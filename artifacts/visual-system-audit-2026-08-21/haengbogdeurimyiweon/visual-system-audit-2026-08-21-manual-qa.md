# manualQa — 행복드림의원 공개 사이트

실행일: 2026-08-21. 모든 시나리오는 Aside REPL에서 실제 라이브 URL에 대해 실행했다.

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| HD-S01 | C1 capture completeness | base home | `openTab("https://reputation.motionlabs.kr/haengbogdeurimyiweon"); snapshot(page); page.screenshot({path:"base-desktop-1440x900.png"})` at 1440×900 | PASS | A01,A02,A13 |
| HD-S02 | C1 capture completeness | base home | same URL; `Emulation.setDeviceMetricsOverride({width:390,height:844}); snapshot(page); page.screenshot({clip:{x:0,y:0,width:390,height:844}})` | PASS | A03,A04,A13 |
| HD-S03 | C2 route and content inventory | `/contents` | `openTab("https://reputation.motionlabs.kr/haengbogdeurimyiweon/contents"); snapshot(page); page.screenshot(...)` at both requested viewports | PASS | A05,A06,A13 |
| HD-S04 | C2 first content detail | `/contents/3dda503e-2856-5387-8bd9-b38de227d6e5` | discovered from contents list; `openTab(url); snapshot(page); page.screenshot(...)` at both requested viewports | PASS | A07,A08,A13 |
| HD-S05 | C2 route and CTA | `/doctor` | `openTab("https://reputation.motionlabs.kr/haengbogdeurimyiweon/doctor"); snapshot(page); page.screenshot(...)` at both requested viewports | PASS | A09,A10,A13 |
| HD-S06 | C2 route and 5-item treatment inventory | `/treatments` | `openTab("https://reputation.motionlabs.kr/haengbogdeurimyiweon/treatments"); snapshot(page); page.screenshot(...)` at both requested viewports | PASS | A11,A12,A13 |
| HD-S07 | C2 route and visit CTA | `/visit` | `openTab("https://reputation.motionlabs.kr/haengbogdeurimyiweon/visit"); snapshot(page); page.screenshot(...)` at both requested viewports | PASS | A14,A15,A13 |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| HD-A01 | C3 responsive safety | viewport overflow | `scrollWidth` equals `clientWidth` at 1440×900 and 390×844; no page-level horizontal crop | PASS | A13 |
| HD-A02 | C3 visual stability | blank/loading/crop | accepted PNGs show rendered content, no blank/login/error screen, and exact 1440×900 or 390×844 dimensions | PASS | A01–A15 |
| HD-A03 | C4 content integrity | repeated/empty state | contents page exposes 6 items and filter totals 6/1/3/1/1; no empty-state replacement | PASS | A05,A06,A16 |
| HD-A04 | C4 information hierarchy | Korean line wrapping | H1 and CTA labels remain readable without clipped glyphs at both viewports | PASS | A01–A12 |
| HD-A05 | C5 image integrity | failed image | visible images have non-zero natural dimensions; no visible broken-image icon | PASS (viewport evidence; below-fold lazy loading is a limit) | A13 |
| HD-A06 | C5 system branding | configured brand token absent | expected `#D6A72C` and `#6F8A56` should appear in computed UI styles | FAIL — observed zero computed hits; blue/navy colors dominate | A16,A01,A15 |
| HD-A07 | C6 accessibility semantics | missing image alternative | informative photos should expose descriptive non-empty alt text | FAIL — blank alt observed on 3/6 home, 1/1 contents, 1/1 detail, 1/1 doctor images | A13,A04,A06,A08,A10 |
| HD-A08 | C6 mobile grouping | card/filter sprawl | filter controls and doctor identity should remain grouped without confusing repetition | FAIL — contents filter wraps to two rows and doctor mobile repeats `대표원장` | A03,A10 |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A01 | screenshot | base desktop 1440×900 | `./base-desktop-1440x900.png` |
| A02 | snapshot | base desktop accessibility tree | `./base-desktop-1440x900.snapshot.txt` |
| A03 | screenshot | base mobile 390×844 | `./base-mobile-390x844.png` |
| A04 | screenshot | contents mobile 390×844 | `./contents-mobile-390x844.png` |
| A05 | screenshot | contents desktop 1440×900 | `./contents-desktop-1440x900.png` |
| A06 | snapshot | contents desktop/mobile DOM trees | `./contents-desktop-1440x900.snapshot.txt`, `./contents-mobile-390x844.snapshot.txt` |
| A07 | screenshot | first content detail desktop | `./content-detail-first-desktop-1440x900.png` |
| A08 | screenshot | first content detail mobile | `./content-detail-first-mobile-390x844.png` |
| A09 | screenshot | doctor desktop | `./doctor-desktop-1440x900.png` |
| A10 | screenshot | doctor mobile | `./doctor-mobile-390x844.png` |
| A11 | screenshot | treatments desktop | `./treatments-desktop-1440x900.png` |
| A12 | screenshot | treatments mobile | `./treatments-mobile-390x844.png` |
| A13 | data | all 12 route/viewport metrics, image states, H1, CTA, width/height | `./metrics.json` |
| A14 | screenshot | visit desktop | `./visit-desktop-1440x900.png` |
| A15 | screenshot | visit mobile | `./visit-mobile-390x844.png` |
| A16 | data | expected vs observed brand token/color evidence | `./brand-color-check.json` |
| A17 | integrity | SHA-256 and PNG signature inventory | `./checksums.txt` |
