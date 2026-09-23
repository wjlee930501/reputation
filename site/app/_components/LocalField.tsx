import type { CSSProperties } from "react";

import { localSection } from "@/lib/landing-copy";

/**
 * 우리 동네 점 지도 — 소개서 3쪽의 "지역의 병원들 중 AI가 고른 서너 곳"을 평면으로 옮겼다.
 *
 * 회색 점은 동네의 병원들, 번호 붙은 점은 AI 답변에 나온 병원, 점선 원은 우리 병원 자리다.
 * AI 답변에 나온 주황 점은 심장 박동처럼 뛰며 파형을 천천히 퍼뜨린다(`--i` = 뛰는 순서).
 * 바탕은 회색 단색의 동네 지도 그림이다.
 * 점은 SVG, 이름표는 HTML로 그린다 — 이름표를 SVG 안에 두면 좁은 화면에서 지도와 함께
 * 줄어 글자가 6px까지 작아진다. HTML 이름표는 % 좌표로 지도 위에 얹고 글자 크기는 고정이다.
 *
 * 지도 칸은 viewBox와 같은 비율(780:440)로 고정한다. 그래야 % 좌표의 이름표와 SVG 점의
 * 빈자리가 어느 폭에서나 겹친다. 점 배치는 고정 시드로 만든다. 렌더마다 바뀌면 서버·클라이언트 HTML이 달라진다.
 */

const VIEW_W = 780;
const VIEW_H = 440;

/** 지도 위 표시 위치(%). 이름표가 서로 닿지 않게 390px 폭에서 확인한 값이다. */
const PICKED_AT = [
  { x: 22, y: 30 },
  { x: 68, y: 22 },
  { x: 40, y: 64 },
];
const OURS_AT = { x: 80, y: 68 };

function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildDots(): { x: number; y: number }[] {
  const rand = mulberry32(20260923);
  const marks = [...PICKED_AT, OURS_AT].map((m) => ({ x: (m.x / 100) * VIEW_W, y: (m.y / 100) * VIEW_H }));
  const dots: { x: number; y: number }[] = [];
  for (let y = 22; y < VIEW_H - 10; y += 30) {
    for (let x = 18; x < VIEW_W - 10; x += 30) {
      if (rand() < 0.38) continue;
      const px = x + (rand() - 0.5) * 16;
      const py = y + (rand() - 0.5) * 16;
      // 표시 점과 이름표 자리는 비워 둔다.
      if (marks.some((m) => Math.abs(px - m.x) < 70 && py - m.y > -28 && py - m.y < 58)) continue;
      dots.push({ x: Math.round(px * 10) / 10, y: Math.round(py * 10) / 10 });
    }
  }
  return dots;
}

const DOTS = buildDots();

export default function LocalField() {
  return (
    <figure className="local-field">
      <ul className="local-legend">
        <li>
          <i data-kind="area" aria-hidden="true" />
          {localSection.legend.area}
        </li>
        <li>
          <i data-kind="picked" aria-hidden="true" />
          {localSection.legend.picked}
        </li>
        <li>
          <i data-kind="ours" aria-hidden="true" />
          {localSection.legend.ours}
        </li>
      </ul>

      <div className="local-map">
        <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} preserveAspectRatio="none" aria-hidden="true">
          {DOTS.map((d, i) => (
            <circle key={i} cx={d.x} cy={d.y} r="4.5" />
          ))}
        </svg>

        <ol className="local-picks">
          {localSection.picked.map((name, i) => (
            <li
              key={name}
              style={{ "--x": `${PICKED_AT[i].x}%`, "--y": `${PICKED_AT[i].y}%`, "--i": i } as CSSProperties}
            >
              <span className="local-pin" aria-hidden="true">
                {i + 1}
              </span>
              <span className="local-name">{name}</span>
            </li>
          ))}
        </ol>

        <p className="local-ours" style={{ "--x": `${OURS_AT.x}%`, "--y": `${OURS_AT.y}%` } as CSSProperties}>
          <span className="local-pin" aria-hidden="true">
            ?
          </span>
          <span className="local-name">{localSection.ours}</span>
        </p>
      </div>

      <figcaption>{localSection.mapNote}</figcaption>
    </figure>
  );
}
