# 서울W내과의원 위례점 — 공개 표면 시각 리뷰

## Verdict

PASS with five follow-up findings. All six requested routes rendered in production at both requested sizes. The 12 accepted PNGs are non-empty, correctly sized, and were visually inspected after capture. No route showed loading, a broken image, horizontal overflow, or a missing H1.

Data under test: hospital name `서울W내과의원 위례점`, 3 contents, 12 treatments, no hospital/hero/logo/brand-color assets, and no doctor photo.

## Exact surface and invocation

Surface: live public site at `https://reputation.motionlabs.kr/seoulwnaegwayiweon-wiryejeom`.

Invocation: `aside repl` → `openTab(route)` → `snapshot(page,{interactive:true})`. Desktop used `page._sendToTarget('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false})` and `page.screenshot({path})`. Mobile used a fresh `openTab(route)` per route, `page._sendToTarget('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true})`, and a viewport clip `{x:0,y:0,width:390,height:844}` so the saved PNG is exactly 390×844.

The content detail route was selected from the first “가장 최근 글” link in `/contents`: `/contents/98f586a8-ea8b-4f21-88f6-b485c0362805`.

## Capture inventory

| Route | Desktop | Mobile | H1 | Desktop document height | Mobile document height |
|---|---|---|---:|---:|---:|
| Home | [home-desktop-1440x900.png](home-desktop-1440x900.png) | [home-mobile-390x844.png](home-mobile-390x844.png) | 1 | 6,715 | 10,159 |
| Contents | [contents-desktop-1440x900.png](contents-desktop-1440x900.png) | [contents-mobile-390x844.png](contents-mobile-390x844.png) | 1 | 1,911 | 2,165 |
| First content detail | [content-detail-desktop-1440x900.png](content-detail-desktop-1440x900.png) | [content-detail-mobile-390x844.png](content-detail-mobile-390x844.png) | 1 | 5,294 | 9,297 |
| Doctor | [doctor-desktop-1440x900.png](doctor-desktop-1440x900.png) | [doctor-mobile-390x844.png](doctor-mobile-390x844.png) | 1 | 2,216 | 2,730 |
| Treatments | [treatments-desktop-1440x900.png](treatments-desktop-1440x900.png) | [treatments-mobile-390x844.png](treatments-mobile-390x844.png) | 1 | 2,590 | 3,776 |
| Visit | [visit-desktop-1440x900.png](visit-desktop-1440x900.png) | [visit-mobile-390x844.png](visit-mobile-390x844.png) | 1 | 1,690 | 2,685 |

All 12 route/viewport pairs reported `scrollWidth === clientWidth` (1,440 desktop; 390 mobile). The three content-bearing routes each had one complete 1,376×768 image; doctor, treatments, and visit intentionally had zero images. No failed image was observed.

## Strengths

- The navy/blue system is consistent and legible. The home hero communicates the clinic name, director, specialties, and phone/directions CTAs without relying on a hospital photo.
- Long Korean headings wrap cleanly. The longest home H1 is four mobile lines; contents, treatments, and visit titles wrap without clipping or sideways scroll.
- One H1 per route and clear route titles/breadcrumbs make the six surfaces easy to orient in.
- The contents page exposes the small data set honestly (`전체 3`, `FAQ 1`, `건강 정보 1`, `지역 특화 1`) rather than leaving an apparently empty index.
- Phone, directions, hours, and official-channel actions are repeated at useful points. On mobile the fixed action bar keeps contact actions available.
- The treatments page keeps all 12 items as readable rows; on mobile descriptions wrap naturally rather than becoming a horizontal card carousel.

## Five findings

### F1 — P1: no-photo doctor fallback repeats the role label

Evidence: [doctor-mobile-390x844.png](doctor-mobile-390x844.png), [doctor-desktop-1440x900.png](doctor-desktop-1440x900.png).

With no doctor photo, the gray initial fallback is stable and intentional, but the mobile composition shows `대표원장` beside the fallback and again directly under the name. This reads like duplicated metadata and weakens hierarchy. Keep one role label and use the second slot for career/specialty or remove it.

### F2 — P1: content detail media slot creates desktop dead air

Evidence: [content-detail-desktop-1440x900.png](content-detail-desktop-1440x900.png), [content-detail-mobile-390x844.png](content-detail-mobile-390x844.png).

The abstract generated image is complete, but desktop leaves a large pale-blue empty band below the visible image before the category/title. Mobile reduces the gap but still shows a media-slot band. For no-photo content, either crop the asset into a single predictable aspect-ratio slot or intentionally label the band as metadata/background so it does not look like a failed image.

### F3 — P1: generic content imagery is the only visual identity on editorial routes

Evidence: [contents-desktop-1440x900.png](contents-desktop-1440x900.png), [content-detail-desktop-1440x900.png](content-detail-desktop-1440x900.png), [home-desktop-1440x900.png](home-desktop-1440x900.png).

The content image is an abstract navy-and-cream graphic, while the hospital has no logo, brand color asset, hero photo, or hospital photo. The result is coherent but generic and does not visually distinguish this clinic from another no-asset clinic. The system should provide a deliberate per-hospital fallback token/illustration family, not just a generic generated image.

### F4 — P2: mobile home spends most of the first viewport on the long H1

Evidence: [home-mobile-390x844.png](home-mobile-390x844.png).

The home H1 takes four lines and pushes the director panel below the fold; the phone and directions buttons remain visible, so this is not a broken layout. Consider a mobile-specific maximum line length or a shorter display title while retaining all specialties in supporting metadata. This would surface trust/context faster without changing the source data.

### F5 — P2: 12 treatments are readable but require a long undifferentiated scroll

Evidence: [treatments-mobile-390x844.png](treatments-mobile-390x844.png), [treatments-desktop-1440x900.png](treatments-desktop-1440x900.png).

The list has no overflow problem and each row is clear, but only the first 2–3 of 12 items are visible in the mobile first viewport and the page is 3,776px tall. Add lightweight grouping, an in-page index, or prioritization for the highest-value services. Do not introduce a nested horizontal scroller; the current vertical reading behavior is more accessible.

## Accessibility observations

- Positive visual evidence: one H1 per route, readable heading hierarchy, strong navy/blue contrast, labeled mobile action buttons, and no horizontal overflow at either viewport.
- The three editorial images expose an empty `alt` string. That is correct if they are decorative; if the image is intended to convey article meaning, provide a concise Korean alt description. The no-photo doctor state has no broken `<img>` and instead renders a meaningful initial fallback.
- The mobile sticky action bar uses icon-plus-label controls (`전화`, `진료시간`, `진료안내`, `길찾기`), which is preferable to icon-only controls.
- Screenshot evidence cannot establish keyboard focus order, focus visibility, screen-reader announcements, semantic landmark quality, contrast ratios by instrument, or touch target dimensions. These remain untested rather than inferred as compliant.

## System implications

1. Keep the current data-driven guards: long hospital names, one-to-four-line Korean headings, zero images, 3-content indexes, and 12-treatment lists all render without clipping.
2. Formalize three no-asset states: doctor initial fallback, navy hero fallback, and editorial-image fallback. Give each a purposeful variant and alt policy.
3. Make the content hero media slot aspect-ratio behavior explicit; the desktop dead-air band is the only screenshot-level visual ambiguity found.
4. Keep the global mobile action bar and vertical treatment rows as shared components. They survived this long-name/no-photo case without overflow.
5. Consider optional compact display copy for mobile H1s while preserving full specialty and locality data in supporting text/schema.

## Limits

This was a visual/DOM capture audit of the six public routes only. No admin route, form submission, external map destination, keyboard traversal, screen reader, performance, or network-failure simulation was run. The source application was not modified.
