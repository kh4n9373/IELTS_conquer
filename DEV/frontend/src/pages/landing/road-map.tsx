import type { CSSProperties, RefObject } from 'react';
import { Link, useNavigate } from 'react-router';
import person3 from '../../assets/authors/person3.webp';
import { Icon } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { gsap } from '../../lib/motion';
import './road-map.css';

type Point = readonly [number, number];
type Box = readonly [x: number, y: number, width: number];
type Layout = {
  kind: 'wide' | 'narrow';
  w: number;
  h: number;
  road: number;
  lift: number;
  walker: number;
  anchor: number;
  pin: Point;
  font: number;
  dash: string;
  d: string;
  leadIn: string;
  leadOut: string;
  fracs: readonly number[];
  points: readonly Point[];
  cards: readonly Box[];
  /** Ties a card to its stop when the card cannot sit right beside it. */
  connector?: string;
};

const WIDE: Layout = {
  kind: 'wide', w: 1440, h: 900, road: 88, lift: 24, walker: 118, anchor: 1.18, pin: [31, 19], font: 30, dash: '16 15',
  d: 'M -90 842 C 50 812 170 782 292 722 C 432 652 430 548 338 500 C 250 454 300 356 452 346 C 596 338 660 414 742 476 C 842 552 968 606 1078 556 C 1182 508 1136 414 1040 376 C 958 342 994 262 1106 232 C 1230 200 1370 166 1540 118',
  leadIn: 'M -1490 1142 L -89 842', leadOut: 'M 1539 118 L 3240 -362',
  fracs: [0.125, 0.292, 0.452, 0.668, 0.875],
  points: [[221, 754], [298, 460], [638, 396], [1128, 515], [1227, 201]],
  cards: [[436, 730, 340], [486, 548, 250], [640, 84, 280], [884, 664, 300], [1204, 306, 204]],
  connector: 'M 250 762 L 424 790',
};

const NARROW: Layout = {
  kind: 'narrow', w: 390, h: 1600, road: 46, lift: 12, walker: 74, anchor: 1.16, pin: [18, 11], font: 18, dash: '9 9',
  d: 'M -40 190 C 30 205 58 280 62 360 C 78 460 332 440 340 600 C 346 740 54 700 50 870 C 46 1020 336 980 340 1140 C 344 1290 58 1250 56 1430 C 55 1510 62 1600 96 1760',
  leadIn: 'M -1440 -110 L -39 190', leadOut: 'M 96 1760 L 150 1960',
  fracs: [0.0962, 0.2714, 0.4629, 0.6543, 0.85],
  points: [[62, 360], [340, 600], [50, 870], [340, 1140], [56, 1430]],
  cards: [[112, 214, 258], [24, 520, 226], [126, 822, 244], [24, 1060, 226], [124, 1364, 248]],
};

/** Release dates, and the page a finished stop opens. A stop is finished once it has an href. */
const STOPS: readonly { date: string; href?: string }[] = [
  { date: '2026-11-22', href: '/classes' },
  { date: '2026-12-18' },
  { date: '2027-01-22' },
  { date: '2027-02-26' },
  { date: '2027-04-16' },
];

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatDate(iso: string, lang: string) {
  const [y, m, d] = iso.split('-');
  return lang === 'vi' ? `${d}.${m}.${y}` : `${Number(d)} ${MONTHS[Number(m) - 1]} ${y}`;
}

const lastDone = STOPS.reduce((last, stop, i) => (stop.href ? i : last), -1);

const GLOW = [76, 66, 56, 47, 38, 30, 22, 15, 8];

function Road({ layout, onOpen }: { layout: Layout; onOpen: (href: string) => void }) {
  const { w, h, d, road, lift, pin, font, points, fracs, walker, anchor } = layout;
  const [rx, ry] = pin;
  const start = points[0]!;
  const doneTo = lastDone >= 0 ? fracs[lastDone]! * 100 : 0;
  const line = (stroke: string) => (
    <>
      <path d={layout.leadIn} stroke={stroke} />
      <path d={d} stroke={stroke} className="rm-trail" pathLength={100} />
    </>
  );
  return (
    <svg className={`rm-road is-${layout.kind}`} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {lastDone >= 0 && (
        <g className="rm-glow" fill="none" transform={`translate(0 ${lift / 2})`}>
          {GLOW.map((extra) => (
            <g key={extra} strokeWidth={road + lift + extra * (layout.kind === 'wide' ? 1 : 0.55)} stroke="#e30b1c" strokeOpacity={0.045}>
              <path d={layout.leadIn} />
              <path d={d} pathLength={100} strokeLinecap="round" strokeDasharray={`${doneTo} 100`} />
            </g>
          ))}
        </g>
      )}
      <g transform={`translate(0 ${lift})`} fill="none" strokeWidth={road}>
        <path d={layout.leadOut} stroke="#0c0c0d" />
        <path d={d} stroke="#0c0c0d" />
        {line('#8d0611')}
      </g>
      <g fill="none" strokeWidth={road}>
        <path d={layout.leadOut} stroke="#2b2b2f" />
        <path d={d} stroke="#2b2b2f" className="rm-line" />
        {line('#e30b1c')}
      </g>
      <path d={d} fill="none" stroke="#fff" strokeOpacity={0.72} strokeWidth={layout.kind === 'wide' ? 3 : 2.5} strokeDasharray={layout.dash} />
      {layout.connector && <path className="rm-connector" d={layout.connector} />}
      {points.map(([x, y], i) => {
        const href = STOPS[i]?.href;
        const lift2 = Math.max(3, ry * 0.28);
        return (
          <g key={i} className={`rm-pin${href ? ' is-done' : ''}`} style={{ '--s': `var(--s${i + 1})` } as CSSProperties} onClick={href ? () => onOpen(href) : undefined}>
            {href && (
              <g className="rm-halo">
                <ellipse className="rm-ring" cx={x} cy={y} rx={rx} ry={ry} />
                <ellipse className="rm-ring is-late" cx={x} cy={y} rx={rx} ry={ry} />
                {[[-1.5, -1.5], [1.55, -1.2], [1.35, 1.45], [-1.6, 1.1]].map(([sx, sy], k) => (
                  <path key={k} className="rm-spark" style={{ animationDelay: `${k * 0.32}s` }} d={`M ${x + sx! * rx} ${y + sy! * ry - rx * 0.3} l ${rx * 0.09} ${rx * 0.21} l ${rx * 0.21} ${rx * 0.09} l ${-rx * 0.21} ${rx * 0.09} l ${-rx * 0.09} ${rx * 0.21} l ${-rx * 0.09} ${-rx * 0.21} l ${-rx * 0.21} ${-rx * 0.09} l ${rx * 0.21} ${-rx * 0.09} z`} />
                ))}
              </g>
            )}
            <ellipse cx={x} cy={y + lift2} rx={rx} ry={ry} fill="#0c0c0d" />
            <ellipse cx={x} cy={y} rx={rx} ry={ry} fill="#fff" />
            <ellipse className="rm-pin-on" cx={x} cy={y} rx={rx * 0.74} ry={ry * 0.72} fill="#e30b1c" />
            {href ? (
              <>
                <text className="rm-pin-off" x={x} y={y + font * 0.34} textAnchor="middle" fontSize={font} fontWeight={900} fill="#0c0c0d">{i + 1}</text>
                <path className="rm-check" d={`M ${x - rx * 0.32} ${y} l ${rx * 0.22} ${ry * 0.36} l ${rx * 0.44} ${-ry * 0.72}`} />
              </>
            ) : (
              <>
                <text x={x} y={y + font * 0.34} textAnchor="middle" fontSize={font} fontWeight={900} fill="#0c0c0d">{i + 1}</text>
                <text className="rm-pin-on" x={x} y={y + font * 0.34} textAnchor="middle" fontSize={font} fontWeight={900} fill="#fff">{i + 1}</text>
              </>
            )}
          </g>
        );
      })}
      <g className="rm-walker" transform={`translate(${start[0]} ${start[1]})`}>
        <image className="rm-bob" href={person3} x={-walker / 2} y={-walker * anchor} width={walker} height={walker} />
      </g>
    </svg>
  );
}

export function RoadMap() {
  const { t, lang } = useI18n();
  const copy = t.landing;
  const navigate = useNavigate();
  const open = (href: string) => { void navigate(href, { viewTransition: true }); };
  return (
    <section className="l-road rm" id="road" aria-labelledby="road-title">
      <div className="rm-stage">
        <Road layout={WIDE} onOpen={open} />
        <Road layout={NARROW} onOpen={open} />
        <div className="road-head rm-head">
          <p className="l-kicker">{copy.roadKicker}</p>
          <h2 id="road-title" data-split data-accent={copy.roadAccent}>{copy.roadTitle}</h2>
        </div>
        <ol className="rm-list">
          {copy.road.map(({ title, body, status, tone }, i) => {
            const stop = STOPS[i]!;
            const [wx, wy, ww] = WIDE.cards[i]!;
            const [nx, ny, nw] = NARROW.cards[i]!;
            const vars = { '--s': `var(--s${i + 1})`, '--wx': wx, '--wy': wy, '--ww': ww, '--nx': nx, '--ny': ny, '--nw': nw } as CSSProperties;
            const inner = (
              <>
                <span className="rm-when">
                  <time dateTime={stop.date}>{formatDate(stop.date, lang)}</time>
                  {!stop.href && <span>{copy.roadPlanned}</span>}
                </span>
                <h3>{title}</h3>
                <p>{body}</p>
                <span className="rm-foot">
                  <span className={`rm-chip is-${tone}`}>{status}</span>
                  {stop.href && <span className="rm-open">{copy.roadOpen}<Icon name="arrow" size={15} /></span>}
                </span>
              </>
            );
            return (
              <li className={`rm-card${stop.href ? ' is-done' : ''}`} style={vars} key={title}>
                {stop.href ? <Link className="rm-link" to={stop.href} viewTransition>{inner}</Link> : <div className="rm-plain">{inner}</div>}
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}

/** Scroll walks the author along the road. Call before the text reveals so the pin is measured first. */
export function addRoadMotion(mm: gsap.MatchMedia, root: RefObject<HTMLElement | null>) {
  mm.add({ motion: '(prefers-reduced-motion: no-preference)', wide: '(min-width: 1180px)', narrow: '(max-width: 1179px)' }, (ctx) => {
    const { motion, wide } = ctx.conditions as { motion: boolean; wide: boolean };
    const section = root.current?.querySelector<HTMLElement>('.l-road');
    const stage = section?.querySelector<HTMLElement>('.rm-stage');
    const layout = wide ? WIDE : NARROW;
    const svg = stage?.querySelector<SVGSVGElement>(`.rm-road.is-${layout.kind}`);
    const path = svg?.querySelector<SVGPathElement>('.rm-line');
    const walker = svg?.querySelector<SVGGElement>('.rm-walker');
    if (!section || !stage || !path || !walker) return;

    const length = path.getTotalLength();
    const last = layout.fracs[layout.fracs.length - 1]!;
    const paint = (p: number) => {
      stage.style.setProperty('--p', p.toFixed(4));
      layout.fracs.forEach((frac, i) => {
        const v = gsap.utils.clamp(0, 1, (p - frac + 0.022) / 0.022);
        stage.style.setProperty(`--s${i + 1}`, (v * v * (3 - 2 * v)).toFixed(3));
      });
      const at = path.getPointAtLength(length * p);
      walker.setAttribute('transform', `translate(${at.x.toFixed(1)} ${at.y.toFixed(1)})`);
    };

    if (!motion) {
      paint(last);
      return;
    }

    const walk = { p: 0.035 };
    const update = () => paint(walk.p);
    update();
    if (wide) {
      const tl = gsap.timeline({
        scrollTrigger: { trigger: section, start: 'top top', end: () => `+=${Math.round(window.innerHeight * 2.8)}`, pin: true, scrub: 0.6, anticipatePin: 1, invalidateOnRefresh: true },
      });
      // The walker pauses at each stop so its card can be read.
      layout.fracs.forEach((frac) => {
        tl.to(walk, { p: frac, duration: 1, ease: 'power1.inOut', onUpdate: update }).to({}, { duration: 0.42 });
      });
    } else {
      gsap.to(walk, { p: last, ease: 'none', onUpdate: update, scrollTrigger: { trigger: stage, start: 'top 58%', end: 'bottom 82%', scrub: 0.5, invalidateOnRefresh: true } });
    }
  });
}
