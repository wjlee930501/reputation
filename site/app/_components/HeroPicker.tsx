"use client";

import { useId, useState } from "react";

import {
  buildPatientQuestions,
  heroPicker,
  queryRegions,
  querySpecialties,
} from "@/lib/landing-copy";

/**
 * 히어로의 개인화 훅.
 *
 * ## 왜 이게 첫 화면에 있어야 하는가
 *
 * 이 페이지의 예시는 전부 남 얘기였다 — `○○정형외과의원`, 고정 표본 `수서역 내과`,
 * 평균 `3~4곳`. 원장이 자기 병원을 대입할 지점이 한 곳도 없었다. 정직한 페이지가
 * 밋밋해지는 지점이 정확히 여기다.
 *
 * 진료과·지역을 고르면 **환자가 실제로 던질 질문**이 그 자리에서 만들어진다.
 * 지어내는 값은 없다: 문장 틀은 백엔드가 실제로 쓰는 세 형태 그대로이고, "서너 곳"은
 * 실측값이다. 그런데 화면에는 자기 병원 이야기로 나타난다.
 *
 * ## 결과를 약속하지 않는다
 *
 * 여기서 보여주는 것은 **질문**이지 답변이 아니다. "원장님 병원이 나옵니다"라고 말하는
 * 순간 재보지도 않은 것을 파는 것이 되므로, 문장은 "그 안에 있는지 확인해 드립니다"에서
 * 멈춘다. 확인은 진단이 한다.
 *
 * ## select를 쓰는 이유
 *
 * 칩 버튼이 더 눈에 띄지만 지역이 열두 곳이라 줄이 접히고, 모바일에서는 네이티브
 * 피커가 훨씬 빠르다. 접근성도 공짜로 따라온다.
 */
export default function HeroPicker() {
  const [specialtyIndex, setSpecialtyIndex] = useState(0);
  const [region, setRegion] = useState<string>(queryRegions[0]);
  const specialtyId = useId();
  const regionId = useId();

  const specialty = querySpecialties[specialtyIndex];
  // 셋 중 가운데(증상형)를 보여준다 — 진료과명이 그대로 들어간 첫 형태보다
  // "환자가 자기 말로 묻는" 느낌이 강하고, 그게 이 블록이 말하려는 것이다.
  const question = buildPatientQuestions(specialty, region)[2];

  return (
    <div className="hero-picker">
      <div className="hero-picker-controls">
        <span className="hero-picker-label">{heroPicker.label}</span>

        <span className="hero-picker-field">
          <label htmlFor={specialtyId}>{heroPicker.specialtyLabel}</label>
          <select
            id={specialtyId}
            value={specialtyIndex}
            onChange={(event) => setSpecialtyIndex(Number(event.target.value))}
          >
            {querySpecialties.map((item, index) => (
              <option key={item.name} value={index}>
                {item.name}
              </option>
            ))}
          </select>
        </span>

        <span className="hero-picker-field">
          <label htmlFor={regionId}>{heroPicker.regionLabel}</label>
          <select
            id={regionId}
            value={region}
            onChange={(event) => setRegion(event.target.value)}
          >
            {queryRegions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </span>
      </div>

      {/* 질문이 바뀌는 것을 스크린리더도 알아야 한다 — 시각적으로는 문장이 갈아끼워지지만
          보조기술에는 아무 일도 일어나지 않은 것과 같기 때문이다. */}
      <div className="hero-picker-result" aria-live="polite">
        <p className="hero-picker-lead">{heroPicker.askLead}</p>
        <p className="hero-picker-question">&ldquo;{question}&rdquo;</p>
        <p className="hero-picker-consequence">{heroPicker.consequence}</p>
      </div>
    </div>
  );
}
