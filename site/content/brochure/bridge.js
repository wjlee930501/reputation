/*
 * 소개서 열람 계측 — `/brochure/doc`가 소개서 HTML 끝에 붙여 내보낸다.
 *
 * 무엇을 재는가
 *   · 페이지별 체류 시간: 그 페이지가 "지금 보고 있는 페이지"였고 탭이 화면에 떠 있던
 *     시간만 더한다. 탭을 내려 두거나 다른 창을 보고 있던 시간은 세지 않는다.
 *   · 도달·완독: 어느 페이지까지 갔는지, 마지막 페이지에 닿았는지.
 *   · 도입문의 클릭: 소개서 안의 CTA(`data-rp-cta`) 중 어느 것을 눌렀는지.
 *
 * 두 가지 보기 방식
 *   · deck(넓은 화면): 효진님 원본의 슬라이드 넘김. 원본 스크립트가 넘길 때마다
 *     `rp:page` 이벤트를 쏜다.
 *   · flow(좁은 화면, `html.rp-flow`): 페이지를 세로로 흘려 스크롤로 읽는다. 화면
 *     가운데 줄에 걸친 페이지를 "지금 페이지"로 본다.
 *
 * 전송
 *   이벤트를 모아 두었다가 페이지가 바뀔 때·15초마다·탭을 떠날 때 `sendBeacon`으로
 *   보낸다. 계측이 실패해도 소개서 보기에는 아무 영향이 없어야 하므로 전부 try로 감싼다.
 */
(function () {
  var cfg = window.__RP_BROCHURE__ || {};
  var root = document.documentElement;
  var pages = [].slice.call(document.querySelectorAll('.page'));
  var total = pages.length;

  /* ── 보기 방식: 760px 이하에서 세로 흐름 ─────────────────────────── */
  var mq = window.matchMedia ? window.matchMedia('(max-width: 760px)') : null;
  function isFlow() {
    return root.classList.contains('rp-flow');
  }

  /* ── 기기 구분값: 공유 링크를 다른 기기에서 열었는지 가르는 데만 쓴다 ── */
  function deviceId() {
    try {
      var k = 'rp_brochure_device';
      var v = localStorage.getItem(k);
      if (!v) {
        v = Math.random().toString(36).slice(2) + Date.now().toString(36);
        localStorage.setItem(k, v);
      }
      return v;
    } catch (e) {
      return 'na';
    }
  }
  var session = Math.random().toString(36).slice(2, 12);
  var device = deviceId();

  /* ── 이벤트 큐 ─────────────────────────────────────────────────── */
  var queue = [];
  function push(kind, page, ms, extra) {
    if (!cfg.token) return;
    var e = { k: kind, p: page, ms: Math.max(0, Math.round(ms || 0)), m: isFlow() ? 'flow' : 'deck', at: Date.now() };
    if (extra) e.x = String(extra).slice(0, 40);
    queue.push(e);
  }
  function flush() {
    if (!cfg.token || !queue.length) return;
    var body = JSON.stringify({ t: cfg.token, v: cfg.viewer || 'owner', d: device, s: session, e: queue.splice(0, 50) });
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(cfg.endpoint, new Blob([body], { type: 'text/plain' }))) return;
    } catch (e) {}
    try {
      fetch(cfg.endpoint, { method: 'POST', body: body, keepalive: true, headers: { 'Content-Type': 'text/plain' } });
    } catch (e) {}
  }

  /* ── 체류 시간 ─────────────────────────────────────────────────── */
  var current = -1;
  var since = 0; // 현재 페이지를 보기 시작한 시각(화면에 떠 있을 때만)
  var reached = -1;
  var completed = false;

  function visible() {
    return document.visibilityState !== 'hidden';
  }
  function close() {
    if (current < 0 || !since) return;
    push('page', current + 1, Date.now() - since);
    since = 0;
  }
  function enter(index) {
    if (index === current) return;
    close();
    current = index;
    since = visible() ? Date.now() : 0;
    if (index > reached) reached = index;
    if (!completed && index === total - 1) {
      completed = true;
      push('complete', index + 1, 0);
    }
    flush();
  }

  document.addEventListener('rp:page', function (ev) {
    if (isFlow()) return; // 세로 흐름에서는 스크롤 위치가 기준이다
    enter(ev.detail.index);
  });

  document.addEventListener('visibilitychange', function () {
    if (visible()) {
      if (current >= 0) since = Date.now();
    } else {
      close();
      flush();
    }
  });
  window.addEventListener('pagehide', function () {
    close();
    flush();
  });
  setInterval(function () {
    // 한 페이지에 오래 머무는 경우에도 중간 기록을 남긴다(창을 강제로 닫으면 pagehide가
    // 오지 않는 브라우저가 있다).
    if (current >= 0 && since && Date.now() - since > 15000) {
      push('page', current + 1, Date.now() - since);
      since = Date.now();
    }
    flush();
  }, 15000);

  /* ── 세로 흐름: 화면 가운데에 걸친 페이지 + 처음 보일 때 등장 모션 ─── */
  var io = null;
  function watchFlow() {
    if (io || !('IntersectionObserver' in window)) return;
    io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            en.target.classList.add('seen');
            if (isFlow()) enter(pages.indexOf(en.target));
          }
        });
      },
      // 화면 가운데 가로줄에 걸친 페이지 하나만 "보고 있는 페이지"가 된다.
      { rootMargin: '-50% 0px -50% 0px', threshold: 0 },
    );
    pages.forEach(function (p) {
      io.observe(p);
    });
  }
  function applyMode() {
    var flow = !!(mq && mq.matches);
    root.classList.toggle('rp-flow', flow);
    if (flow) watchFlow();
    window.dispatchEvent(new Event('resize')); // 원본 fit()을 다시 돌려 배율을 맞춘다
  }
  applyMode();
  if (mq) {
    if (mq.addEventListener) mq.addEventListener('change', applyMode);
    else if (mq.addListener) mq.addListener(applyMode);
  }

  /* ── 도입문의 클릭 ─────────────────────────────────────────────── */
  document.addEventListener(
    'click',
    function (ev) {
      var a = ev.target.closest && ev.target.closest('[data-rp-cta]');
      if (!a) return;
      push('cta', current + 1, 0, a.getAttribute('data-rp-cta'));
      close();
      flush();
    },
    true,
  );

  push('open', 1, 0);
  // 원본 스크립트가 이미 첫 페이지를 띄운 뒤라, deck 모드의 첫 페이지를 여기서 잡는다.
  if (!isFlow()) {
    var active = pages.findIndex
      ? pages.findIndex(function (p) {
          return p.classList.contains('active');
        })
      : 0;
    enter(active < 0 ? 0 : active);
  }
})();
