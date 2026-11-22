import { useRef } from 'react';
import './intro.css';
import person1 from '../../assets/intro/person1.webp';
import person2 from '../../assets/intro/person2.webp';
import person2Outline from '../../assets/intro/person2-outline.webp';
import { useI18n } from '../../lib/i18n';
import { gsap, reducedMotion, useGSAP } from '../../lib/motion';
import { BRAND_BOXES, BRAND_PATHS, BRAND_PORTRAITS } from './intro-brand';

const SEEN_KEY = 'panxg-intro-seen';
const NAME = ['Phương', 'Anh', 'Nguyễn', 'Xinh', 'Gái'];
const LETTERS = ['P', 'A', 'N', 'X', 'G'] as const;
const BASE = 61.2;
const HEART = 'M0 .9C-.9 .25-1 -.2-1 -.45A.55 .55 0 0 1 0 -.62A.55 .55 0 0 1 1 -.45C1 -.2 .9 .25 0 .9Z';
const STAR = 'M0 -1C.1 -.28 .28 -.1 1 0C.28 .1 .1 .28 0 1C-.1 .28 -.28 .1 -1 0C-.28 -.1 -.1 -.28 0 -1Z';
const T = { cap: 1.0, capStep: 0.13, rest: 1.92, go: 1.98, goStep: 0.03, goDur: 0.66, floor: 2.5, p2: 2.62, p1: 2.88, frame: 3.24, snap: 3.48, dive2: 3.78, dive1: 3.88, tm: 4.32, fly: 4.54, flyDur: 0.76, nav: 5.02, logo: 5.5 };
const END = 5.76;
const WIDE = { w: 1440, h: 900, small: 52, name: 90, gap: 18, S: 3.1, base: 680, big: 430, x2: 470, x1: 1120, hop: 120, heart: 22, bw: 5, star: 34, corner: 46, margin: 22, head: 34 };
const TALL = { w: 390, h: 844, small: 27, name: 72, gap: 12, S: 1.09, base: 590, big: 270, x2: 118, x1: 276, hop: 64, heart: 14, bw: 3, star: 22, corner: 26, margin: 8, head: 58 };
const WHITE = [255, 255, 255];
const RED = [227, 11, 28];

let seenThisLoad = false;

function readSeen() {
  try { return sessionStorage.getItem(SEEN_KEY) === '1'; } catch { return false; }
}

function markSeen() {
  seenThisLoad = true;
  try { sessionStorage.setItem(SEEN_KEY, '1'); } catch { /* The in-memory flag still covers this page load. */ }
}

/** Plays once per browser session, never for reduced motion or a deep link into the page. */
export function shouldPlayIntro() {
  return !seenThisLoad && !readSeen() && !reducedMotion() && !window.location.hash;
}

const clamp = (v: number) => Math.max(0, Math.min(1, v));
const expo = (k: number) => (k >= 1 ? 1 : 1 - 2 ** (-10 * k));
const inOut = (k: number) => (k < 0.5 ? 8 * k ** 4 : 1 - (-2 * k + 2) ** 4 / 2);
const back = (k: number) => 1 + 3.2 * (k - 1) ** 3 + 2.2 * (k - 1) ** 2;
const mix = (a: number, b: number, k: number) => a + (b - a) * k;
const bump = (k: number) => Math.sin(Math.PI * k);
const rgb = (a: number[], b: number[], k: number) => `rgb(${a.map((v, i) => Math.round(mix(v, b[i]!, k))).join(' ')})`;
const place = (el: Element, x: number, y: number, sx: number, sy: number, r: number, px: number, py: number) =>
  el.setAttribute('transform', `translate(${x.toFixed(2)} ${y.toFixed(2)}) rotate(${r.toFixed(2)}) scale(${sx.toFixed(4)} ${sy.toFixed(4)}) translate(${(-px).toFixed(2)} ${(-py).toFixed(2)})`);

/** Measures the live page and returns a function that draws any moment of the intro. */
function createStage(root: HTMLElement, logo: DOMRect, navHeight: number) {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const stacked = w < 900 && w < h;
  const ref = stacked ? TALL : WIDE;
  const u = Math.min(w / ref.w, h / ref.h, 1.6);
  const pad = Math.max(16, logo.left);
  root.dataset.stacked = String(stacked);
  root.style.setProperty('--ix-pad', `${pad}px`);
  root.style.setProperty('--ix-gap', `${ref.gap * u}px`);
  const setType = (k: number) => {
    root.style.setProperty('--ix-small', `${ref.small * u * k}px`);
    root.style.setProperty('--ix-name', `${ref.name * u * k}px`);
  };
  setType(1);
  const q = <E extends Element>(s: string) => Array.from(root.querySelectorAll<E>(s));
  const words = q<HTMLElement>('.ix-wi');
  const widest = Math.max(...words.map((e) => e.getBoundingClientRect().right));
  if (widest > w - pad) setType((w - 2 * pad) / (widest - pad));

  const rests = q<HTMLElement>('.ix-rest');
  const caps = q<HTMLElement>('.ix-cap');
  const ticks = q<HTMLElement>('.ix-tick');
  const one = <E extends Element>(s: string) => root.querySelector<E>(s)!;
  const flyG = one<SVGGElement>('[data-fly]');
  const letters = q<SVGGElement>('[data-l]');
  const tm = one<SVGGElement>('[data-tm]');
  const p1 = one<SVGImageElement>('[data-p="1"]');
  const p2 = one<SVGImageElement>('[data-p="2"]');
  const p2o = one<SVGImageElement>('[data-o]');
  const floor = one<SVGRectElement>('[data-floor]');
  const floorClip = one<SVGRectElement>('[data-floor-clip]');
  const star = one<SVGPathElement>('[data-star]');
  const hearts = q<SVGPathElement>('[data-h]');
  const frame = one<SVGPathElement>('[data-frame]');
  const wipe = one<SVGRectElement>('[data-wipe]');
  const skip = one<HTMLElement>('.ix-skip');
  const nav = root.closest('.landing')!.querySelector<HTMLElement>('.l-nav')!;
  const brandImg = nav.querySelector<HTMLElement>('.brand img')!;

  const S = ref.S * u;
  const Se = logo.width / 320;
  const base = h / 2 + (ref.base - ref.h / 2) * u;
  const tcx = (w - 320 * S) / 2;
  const tcy = base - BASE * S;
  const big = ref.big * u;
  const x2 = w / 2 + (ref.x2 - ref.w / 2) * u;
  const x1 = w / 2 + (ref.x1 - ref.w / 2) * u;
  const hop = ref.hop * u;
  const heart = ref.heart * u;
  const { p1: P1, p2: P2 } = BRAND_PORTRAITS;
  const boxes = LETTERS.map((c) => BRAND_BOXES[c]);
  const TM = BRAND_BOXES.TM;
  const margin = ref.margin * u;
  const fx = Math.max(margin, Math.min(tcx, x2 - big * 0.36) - margin);
  const fr = Math.min(w - margin, Math.max(tcx + (P1.x + P1.s) * S, x1 + big * 0.38) + margin);
  const fy = Math.max(margin, base - big * 0.99 - margin - ref.head * u);
  const fb = base + margin;
  const corner = ref.corner * u;

  floor.setAttribute('x', tcx.toFixed(2));
  floor.setAttribute('y', (base - 1.5).toFixed(2));
  floor.setAttribute('width', (320 * S).toFixed(2));
  floorClip.setAttribute('width', String(w + 1000));
  floorClip.setAttribute('height', (base + 500).toFixed(2));
  frame.setAttribute('stroke-width', String(ref.bw * u));
  root.querySelector('.ix-svg')!.setAttribute('viewBox', `0 0 ${w} ${h}`);

  const font = parseFloat(getComputedStyle(caps[0]!).fontSize);
  const capH = font * 0.727;
  const src = caps.map((cap, i) => {
    const r = cap.getBoundingClientRect();
    const b = boxes[i]!;
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 + capH / 2, sx: (r.width * 0.92) / (b[2] - b[0]), sy: capH / (b[3] - b[1]) };
  });
  one<HTMLElement>('.ix-copy').style.visibility = 'visible';

  return (t: number) => {
    const seg = (a: number, d: number) => clamp((t - a) / d);
    const land = (a: number, d: number) => { const k = seg(a, d); return bump(k) * (1 - k); };

    words.forEach((el, i) => { const k = expo(seg(0.12 + i * 0.055, 0.95)); el.style.transform = `translateY(${(1 - k) * 118}%) rotate(${(1 - k) * 6}deg)`; });
    const dim = expo(seg(T.cap, 0.6));
    rests.forEach((el, i) => { const off = inOut(seg(T.rest + i * 0.007, 0.4)); el.style.opacity = String(mix(1, 0.2, dim) * (1 - off)); el.style.transform = `translateY(${off * 0.42}em)`; });
    caps.forEach((el, i) => {
      const at = T.cap + i * T.capStep;
      const k = seg(at, 0.34);
      el.style.color = rgb(WHITE, RED, expo(seg(at, 0.2)));
      el.style.transform = `scale(${1 + 0.22 * bump(k)}) rotate(${-3 * bump(k)}deg)`;
      el.style.opacity = String(1 - seg(T.go, 0.12));
      el.style.visibility = t < T.go + 0.14 ? 'visible' : 'hidden';
      ticks[i]!.style.transform = `scaleX(${expo(seg(at + 0.04, 0.26)) * (1 - seg(T.rest - 0.04, 0.14))})`;
    });

    const b = inOut(seg(T.fly, T.flyDur));
    const ant = bump(seg(T.fly - 0.16, 0.22));
    const r = Se / S;
    const k = mix(1, r, b);
    const tx = mix(0, logo.left - tcx * r, b);
    const ty = mix(0, logo.top - tcy * r, b) + 8 * u * ant * (1 - b);
    const cx = w / 2;
    const cy = base - 24 * S;
    flyG.setAttribute('transform', `translate(${tx.toFixed(2)} ${ty.toFixed(2)}) rotate(${(-2.6 * bump(b)).toFixed(2)} ${(cx * k).toFixed(1)} ${(cy * k).toFixed(1)}) scale(${k.toFixed(5)})`);
    const brandOn = 1 - seg(T.logo + 0.1, 0.14);
    flyG.setAttribute('opacity', String(brandOn));
    flyG.style.visibility = brandOn > 0 && t >= T.go ? 'visible' : 'hidden';

    letters.forEach((g, i) => {
      const B = boxes[i]!;
      const f = src[i]!;
      const start = T.go + i * T.goStep;
      const a = inOut(seg(start, T.goDur));
      const px = (B[0] + B[2]) / 2;
      const ripple = T.dive2 + 0.42;
      const sq = land(start + T.goDur - 0.02, 0.28) + 0.45 * land(T.snap + i * 0.02, 0.24) + 0.7 * land(ripple + Math.abs(i - 1) * 0.035, 0.28);
      const wave = bump(seg(ripple + Math.abs(i - 1) * 0.04, 0.3)) * 5 * S;
      place(g, mix(f.x, tcx + px * S, a), mix(f.y, base, a) - bump(a) * 26 * u - wave, mix(f.sx, S, a) * (1 + 0.1 * sq), mix(f.sy, S, a) * (1 - 0.15 * sq), bump(a) * (i - 2) * 5, px, BASE);
      g.setAttribute('opacity', String(seg(T.go, 0.12)));
      if (i !== 1 && i !== 3) g.querySelector('.ix-f')!.setAttribute('fill', rgb(RED, WHITE, expo(seg(T.snap + i * 0.03, 0.22))));
    });

    const floorK = expo(seg(T.floor, 0.34)) * (1 - inOut(seg(T.dive1 + 0.1, 0.32)));
    floor.setAttribute('opacity', floorK > 0.001 ? '1' : '0');
    floor.setAttribute('transform', `translate(${tcx * (1 - floorK)} 0) scale(${floorK} 1)`);

    const person = (el: Element, p: { x: number; y: number; s: number }, at: number, diveAt: number, bx: number, jump: number, wob: number) => {
      const rise = expo(seg(at, 0.5));
      const stretch = 0.09 * bump(seg(at + 0.04, 0.42));
      const d = inOut(seg(diveAt, 0.46));
      const px = p.x + p.s / 2;
      const scale = mix(big / p.s, S, d);
      const x = mix(bx, tcx + px * S, d);
      const sq = land(diveAt + 0.44, 0.3) + 0.5 * land(T.snap, 0.26);
      const end = jump && t > T.fly ? bump(seg(T.fly + T.flyDur - 0.05, 0.32)) * 10 * S : 0;
      const y = base + (1 - rise) * big * 1.02 - bump(d) * jump - end;
      const idle = Math.sin((t - at) * 6.2) * wob * clamp((t - at - 0.3) / 0.25) * (1 - seg(diveAt - 0.1, 0.16));
      const rot = idle + bump(d) * (jump ? 14 : -7) + (jump ? bump(b) * 10 : 0);
      place(el, x, y, scale * (1 - stretch * 0.6 + 0.09 * sq), scale * (1 + stretch - 0.12 * sq), rot, px, BASE);
      el.setAttribute('opacity', t >= at ? '1' : '0');
      return { x, y, scale, px };
    };
    const a1 = person(p1, P1, T.p1, T.dive1, x1, hop, 2.6);
    person(p2, P2, T.p2, T.dive2, x2, 0, 4.5);
    p2o.setAttribute('transform', p2.getAttribute('transform')!);
    p2o.setAttribute('opacity', String(t >= T.p2 ? 1 - seg(T.dive2 + 0.02, 0.24) : 0));

    hearts.forEach((el, i) => {
      const at = T.p2 + 0.3 + i * 0.075;
      const q2 = seg(at, 0.92);
      if (q2 <= 0 || q2 >= 1) { el.setAttribute('opacity', '0'); return; }
      const ang = -2.55 + i * 0.42;
      const reach = big * (0.42 + 0.1 * (i % 2));
      const hx = Math.max(fx + heart + 12 * u, x2 + Math.cos(ang) * reach * (0.55 + q2 * 0.45) + Math.sin(q2 * 7 + i) * 7 * u);
      const hy = base - big * 0.6 + Math.sin(ang) * reach * (0.55 + q2 * 0.45) - q2 * 34 * u;
      const size = heart * (0.75 + (i % 3) * 0.2) * back(clamp(q2 / 0.26));
      el.setAttribute('transform', `translate(${hx.toFixed(1)} ${hy.toFixed(1)}) rotate(${(Math.sin(q2 * 5 + i) * 16).toFixed(1)}) scale(${size.toFixed(2)})`);
      el.setAttribute('opacity', String((1 - seg(at + 0.6, 0.32)) * (1 - seg(T.dive2 - 0.08, 0.16))));
    });

    const fk = expo(seg(T.frame, 0.24));
    const fo = seg(T.frame, 0.08) * (1 - seg(T.dive2 - 0.04, 0.16));
    frame.setAttribute('opacity', String(fo));
    if (fo > 0) {
      const gap = ((1 - fk) * 42 - 7 * bump(seg(T.snap, 0.2))) * u;
      const l = fx - gap, rr = fr + gap, tt = fy - gap, bb = fb + gap, c = corner;
      frame.setAttribute('d', `M${l} ${tt + c}V${tt}H${l + c}M${rr - c} ${tt}H${rr}V${tt + c}M${rr} ${bb - c}V${bb}H${rr - c}M${l + c} ${bb}H${l}V${bb - c}`);
    }

    const sk = seg(T.snap - 0.02, 0.36);
    if (sk > 0 && sk < 1) {
      const sx = a1.x + (P1.x + 0.6 * P1.s - a1.px) * a1.scale;
      const sy = a1.y + (P1.y + 0.245 * P1.s - BASE) * a1.scale;
      star.setAttribute('transform', `translate(${sx.toFixed(1)} ${sy.toFixed(1)}) rotate(${(sk * 90).toFixed(1)}) scale(${(ref.star * u * bump(sk)).toFixed(2)})`);
      star.setAttribute('opacity', '1');
    } else star.setAttribute('opacity', '0');

    const tmx = (TM[0] + TM[2]) / 2;
    const tmy = (TM[1] + TM[3]) / 2;
    const tk = back(seg(T.tm, 0.3));
    place(tm, tcx + tmx * S, tcy + tmy * S, S * tk, S * tk, (1 - seg(T.tm, 0.3)) * -18, tmx, tmy);
    tm.setAttribute('opacity', String(seg(T.tm, 0.05)));

    // The white nav slides down, and the logo's dark ink appears exactly along its edge.
    const navK = expo(seg(T.nav, 0.46));
    nav.style.transform = `translateY(${(navK - 1) * 100}%)`;
    const edge = ((navK * navHeight - ty) / k - tcy) / S;
    wipe.setAttribute('height', navK > 0 ? Math.max(0, edge + 60).toFixed(2) : '0');
    brandImg.style.opacity = String(seg(T.logo, 0.12));
    root.style.background = t < T.nav ? '#0c0c0d' : 'transparent';
    root.style.pointerEvents = t < T.nav ? 'auto' : 'none';
    skip.style.opacity = String(1 - seg(T.fly - 0.1, 0.22));
    skip.style.visibility = t < T.fly + 0.14 ? 'visible' : 'hidden';
  };
}

function Word({ text, cap }: { text: string; cap?: boolean }) {
  return (
    <span className="ix-w">
      <span className="ix-wi">
        {cap && <span className="ix-cap">{text[0]}<i className="ix-tick" /></span>}
        <span className="ix-rest">{cap ? text.slice(1) : text}</span>
      </span>
    </span>
  );
}

export function LandingIntro({ onHandoff, onDone }: { onHandoff: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const copy = t.landing.intro;
  const root = useRef<HTMLDivElement>(null);
  const skipNow = useRef<() => void>(() => undefined);
  const { p1, p2 } = BRAND_PORTRAITS;
  const pad = (24 * p2.s) / 512;
  const outline = (p2.s * 560) / 512;

  useGSAP(() => {
    const el = root.current!;
    const page = el.closest<HTMLElement>('.landing')!;
    const nav = page.querySelector<HTMLElement>('.l-nav')!;
    const brandImg = nav.querySelector<HTMLElement>('.brand img')!;
    const behind = Array.from(page.children).filter((c): c is HTMLElement => c !== el && c instanceof HTMLElement);
    const html = document.documentElement;
    let clock: gsap.core.Tween | null = null;
    let handed = false;
    let ended = false;
    let cancelled = false;
    const width = window.innerWidth;

    window.scrollTo(0, 0);
    const logo = brandImg.getBoundingClientRect();
    const navHeight = nav.offsetHeight;
    html.classList.add('intro-lock');
    behind.forEach((c) => { c.inert = true; });
    nav.style.transform = 'translateY(-100%)';
    brandImg.style.opacity = '0';

    const restore = () => {
      html.classList.remove('intro-lock');
      behind.forEach((c) => { c.inert = false; });
      nav.style.removeProperty('transform');
      brandImg.style.removeProperty('opacity');
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onResize);
    };
    const handoff = () => {
      if (handed) return;
      handed = true;
      onHandoff();
    };
    const finish = () => {
      if (ended) return;
      ended = true;
      clock?.kill();
      markSeen();
      handoff();
      restore();
      onDone();
    };
    const skip = () => {
      if (ended || cancelled) return;
      clock?.kill();
      clock = null;
      markSeen();
      nav.style.removeProperty('transform');
      brandImg.style.removeProperty('opacity');
      handoff();
      el.style.pointerEvents = 'none';
      gsap.to(el, { autoAlpha: 0, duration: 0.3, ease: 'power2.out', onComplete: finish });
    };
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') skip(); }
    function onResize() { if (window.innerWidth !== width) skip(); }
    skipNow.current = skip;
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', onResize);

    const assets = Array.from(el.querySelectorAll('image')).map((img) => {
      const probe = new Image();
      probe.src = img.getAttribute('href')!;
      return probe.decode().catch(() => undefined);
    });
    const fonts = document.fonts?.load('900 80px "Inter Tight Variable"').catch(() => undefined);
    const wait = new Promise((done) => { window.setTimeout(done, 1500); });

    void Promise.race([Promise.all([...assets, fonts]), wait]).then(() => {
      if (cancelled || ended) return;
      try {
        const draw = createStage(el, logo, navHeight);
        const state = { t: 0 };
        draw(0);
        clock = gsap.to(state, {
          t: END,
          duration: END,
          ease: 'none',
          onUpdate: () => {
            draw(state.t);
            if (state.t >= T.nav) handoff();
          },
          onComplete: finish,
        });
      } catch {
        finish();
      }
    });

    return () => {
      cancelled = true;
      clock?.kill();
      gsap.killTweensOf(el);
      if (!ended) restore();
    };
  }, { scope: root });

  return (
    <div className="ix-root" ref={root}>
      <p className="sr-only">{`${copy.lead} ${NAME.join(' ')} ${copy.tail}`}</p>
      <div className="ix-copy" aria-hidden="true">
        <span className="ix-small">{copy.lead.split(' ').map((w) => <Word key={w} text={w} />)}</span>
        <span className="ix-name">
          {NAME.map((w, i) => <span key={w}><Word text={w} cap />{i < 3 && <br />}</span>)}
        </span>
        <span className="ix-small">{copy.tail.split(' ').map((w) => <Word key={w} text={w} />)}</span>
      </div>
      <svg className="ix-svg" aria-hidden="true">
        <defs>
          <clipPath id="ix-floor"><rect data-floor-clip x="-500" y="-500" /></clipPath>
          <clipPath id="ix-wipe"><rect data-wipe x="-60" y="-60" width="460" height="0" /></clipPath>
        </defs>
        <g data-fly style={{ visibility: 'hidden' }}>
          <g clipPath="url(#ix-floor)">
            <image data-p="1" href={person1} x={p1.x} y={p1.y} width={p1.s} height={p1.s} opacity="0" />
            <image data-o href={person2Outline} x={p2.x - pad} y={p2.y - pad} width={outline} height={outline} opacity="0" />
            <image data-p="2" href={person2} x={p2.x} y={p2.y} width={p2.s} height={p2.s} opacity="0" />
          </g>
          <rect data-floor height="3" fill="#e30b1c" opacity="0" />
          {LETTERS.map((c, i) => (
            <g key={c} data-l={i} opacity="0">
              <path className="ix-f" d={BRAND_PATHS[c]} fill="#e30b1c" />
              {c !== 'A' && c !== 'X' && <g clipPath="url(#ix-wipe)"><path d={BRAND_PATHS[c]} fill="#1a1a1c" /></g>}
            </g>
          ))}
          <g data-tm opacity="0">
            <path className="ix-f" d={BRAND_PATHS.TM} fill="#fff" />
            <g clipPath="url(#ix-wipe)"><path d={BRAND_PATHS.TM} fill="#1a1a1c" /></g>
          </g>
          <path data-star d={STAR} fill="#fff" opacity="0" />
        </g>
        {Array.from({ length: 6 }, (_, i) => <path key={i} data-h d={HEART} fill={i % 3 ? '#e30b1c' : '#fff'} opacity="0" />)}
        <path data-frame fill="none" stroke="#fff" strokeLinecap="square" opacity="0" />
      </svg>
      <button type="button" className="ix-skip" onClick={() => skipNow.current()}>{copy.skip}</button>
    </div>
  );
}
