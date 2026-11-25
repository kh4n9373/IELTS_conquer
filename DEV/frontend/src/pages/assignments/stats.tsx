import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { AnswerResult, AssignmentStats, StatsQuestion, StatsStudent } from '../../lib/exam-api';
import { useI18n } from '../../lib/i18n';
import './stats.css';

export type StatsTab = 'overview' | 'questions' | 'students';
type Tone = 'good' | 'mid' | 'weak';

export function mmss(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '–';
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function toneOf(stats: AssignmentStats, percent: number): Tone {
  const b = stats.summary?.bands ?? { strong: 75, weak: 50 };
  return percent >= b.strong ? 'good' : percent >= b.weak ? 'mid' : 'weak';
}

/** What fits in a small answer cell: a letter as is, longer text as a mark. */
function cellText(given: string, result: AnswerResult): string {
  if (result === 'blank') return '–';
  if (given.length <= 2) return given.toUpperCase();
  return result === 'ok' ? '✓' : '✗';
}

const pct = (v: number) => `${v}%`;
const at = (v: number): CSSProperties => ({ left: `${v}%` });

/** A student who handed in nothing: left off the charts so a 0 does not pass for a score. */
const isEmpty = (st: StatsStudent) => st.answers.every((a) => a.result === 'blank');

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const pick = words.length > 1 ? [words[0], words[words.length - 1]] : words;
  return pick.map((w) => w?.[0] ?? '').join('').toUpperCase().slice(0, 2) || '?';
}

/** Round tick step so an axis up to `max` gets about four marks. */
function tickStep(max: number, steps: number[]): number {
  return steps.find((s) => max / s <= 4) ?? steps[steps.length - 1] ?? 1;
}

function ticks(max: number, step: number): number[] {
  const out: number[] = [];
  for (let v = 0; v < max; v += step) out.push(v);
  return [...out, max];
}

/** Green when everyone got it right, through amber, to red when nobody did. */
function scale(percent: number): CSSProperties {
  const p = Math.max(0, Math.min(100, percent)) / 100;
  const red = [227, 11, 28], amber = [245, 158, 11], green = [22, 163, 74];
  const [a, b, f] = p < 0.5 ? [red, amber, p * 2] : [amber, green, (p - 0.5) * 2];
  const rgb = a.map((v, i) => Math.round(v + ((b[i] ?? v) - v) * f));
  return { background: `rgb(${rgb.join(' ')})`, color: p > 0.3 && p < 0.75 ? '#111112' : '#fff' };
}

function useWidth<T extends HTMLElement>(fallback: number) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(e?.contentRect.width || fallback));
    ro.observe(el);
    return () => ro.disconnect();
  }, [fallback]);
  return [ref, width] as const;
}

function Card({ title, sub, className = '', children }: { title: string; sub?: string; className?: string; children: React.ReactNode }) {
  return (
    <section className={`st-card ${className}`}>
      <div>
        <h2 className="st-title">{title}</h2>
        {sub && <p className="st-sub">{sub}</p>}
      </div>
      {children}
    </section>
  );
}

function Legend({ items }: { items: [string, string][] }) {
  return (
    <div className="st-legend">
      {items.map(([cls, label]) => (
        <span key={label}><i className={`st-sw ${cls}`} aria-hidden="true" />{label}</span>
      ))}
    </div>
  );
}

export function StatsTabs({ tab, onTab }: { tab: StatsTab; onTab: (t: StatsTab) => void }) {
  const { t } = useI18n();
  return (
    <nav className="st-tabs" aria-label={t.stats.tabsLabel}>
      {(['overview', 'questions', 'students'] as const).map((k) => (
        <button key={k} type="button" className={k === tab ? 'on' : ''} aria-current={k === tab ? 'page' : undefined} onClick={() => onTab(k)}>
          {t.stats.tabs[k]}
        </button>
      ))}
    </nav>
  );
}

/* ---------- Overview ---------- */

function useInsights(stats: AssignmentStats) {
  const { t } = useI18n();
  const s = stats.summary;
  if (!s) return [];
  const out: { head: string; body: string }[] = [];
  if (s.strongTopic && s.strongTopic.percent >= s.bands.strong) out.push(t.stats.insStrong(s.strongTopic.name, s.strongTopic.percent));
  if (s.weakTopic && s.weakTopic.percent < s.bands.strong && s.weakTopic.name !== s.strongTopic?.name) {
    out.push(t.stats.insWeak(s.weakTopic.name, s.weakTopic.percent));
  }
  if (s.topTrap) out.push(t.stats.insTrap(s.topTrap.number, s.topTrap.count, stats.graded, s.topTrap.value, s.topTrap.key[0] ?? ''));
  for (const o of s.outOfTime.slice(0, 2)) out.push(t.stats.insTime(o.name, mmss(o.seconds), mmss(stats.durationSeconds), o.blank));
  if (s.flagTotal > 0) out.push(t.stats.insFlags(s.flagTotal, s.flagCorrect));
  if (out.length === 0) out.push(t.stats.insNone);
  return out;
}

function DotPlot({ stats }: { stats: AssignmentStats }) {
  const { t } = useI18n();
  const s = stats.summary!;
  const total = stats.total || 1;
  const empty = stats.students.filter(isEmpty);
  // Stack dots that would touch: each lane keeps the x of its last dot.
  const lanes: number[] = [];
  const placed = stats.students.filter((st) => !isEmpty(st)).sort((a, b) => a.correct - b.correct).map((st) => {
    const x = (st.correct / total) * 100;
    let lane = lanes.findIndex((last) => x - last >= 3);
    if (lane < 0) lane = lanes.push(x) - 1;
    else lanes[lane] = x;
    return { st, x, lane };
  });
  const height = Math.max(130, 52 + lanes.length * 32);
  const step = tickStep(total, [1, 2, 5, 10, 20, 25, 50, 100]);
  return (
    <>
      <div className="st-dots" style={{ height: height + 28 }}>
        <div className="st-dots-bands" style={{ height }} aria-hidden="true">
          {t.stats.bands.map((b) => <i key={b}>{b}</i>)}
        </div>
        <div className="st-mean" style={{ ...at((s.mean / total) * 100), height }} aria-hidden="true" />
        <span className="st-mean-label" style={at((s.mean / total) * 100)}>{t.stats.meanShort} {s.mean}</span>
        {placed.map(({ st, x, lane }) => {
          const tip = t.stats.dotTip(st.name, st.correct, stats.total, Math.round((st.correct / total) * 100));
          return (
            <span key={st.id} className={`st-dot st-tip${st.correct * 4 < total ? ' weak' : ''}`} tabIndex={0} data-tip={tip} aria-label={tip}
              style={{ ...at(x), top: height - 40 - lane * 32 }}>
              {initials(st.name)}
            </span>
          );
        })}
        <div className="st-axis" style={{ top: height + 8 }} aria-hidden="true">
          {ticks(total, step).map((v) => <span key={v} style={at((v / total) * 100)}>{v === total ? t.stats.nQ(total) : v}</span>)}
        </div>
      </div>
      {empty.length > 0 && <p className="st-sub">{t.stats.notPlotted(empty.map((x) => x.name).join(', '))}</p>}
    </>
  );
}

function TimeScore({ stats }: { stats: AssignmentStats }) {
  const { t } = useI18n();
  const shown = stats.students.filter((st) => !isEmpty(st));
  const dur = Math.max(stats.durationSeconds, ...shown.map((x) => x.seconds), 1);
  const times = shown.map((x) => x.seconds).sort((a, b) => a - b);
  const half = Math.floor(times.length / 2);
  const mid = times.length === 0 ? 0 : times.length % 2 ? (times[half] ?? 0) : ((times[half - 1] ?? 0) + (times[half] ?? 0)) / 2;
  const midX = (mid / dur) * 100;
  return (
    <Card title={t.stats.scatterTitle} sub={t.stats.scatterSub} className="half">
      <div className="st-plot quad">
        <i className="st-qline v" style={at(midX)} aria-hidden="true" />
        <i className="st-qline h" aria-hidden="true" />
        <span className="st-corner tl">{t.stats.quad.fastRight}</span>
        <span className="st-corner tr">{t.stats.quad.slowSure}</span>
        <span className="st-corner bl red">{t.stats.quad.rushed}</span>
        <span className="st-corner br red">{t.stats.quad.struggling}</span>
        {shown.map((st) => {
          const tip = `${st.name} · ${Math.round(st.percent)}/100 · ${mmss(st.seconds)}`;
          return (
            <span key={st.id} className={`st-dot xy st-tip${st.percent < 25 ? ' weak' : ''}`} tabIndex={0} data-tip={tip} aria-label={tip}
              style={{ left: pct((st.seconds / dur) * 100), bottom: pct(st.percent) }}>
              {initials(st.name)}
            </span>
          );
        })}
        <span className="st-ax y-top">100</span>
        <span className="st-ax y-mid">50</span>
        <span className="st-ax y-bottom">0</span>
        <span className="st-ax x-at" style={at(Math.min(midX, 80))}>{t.stats.medianTime(mmss(mid))}</span>
        <span className="st-ax x-end red">{t.stats.limit(mmss(dur))}</span>
      </div>
    </Card>
  );
}

function Overview({ stats }: { stats: AssignmentStats }) {
  const { t } = useI18n();
  const s = stats.summary!;
  const insights = useInsights(stats);
  const total = stats.total;
  const n = stats.graded;
  const kpis: [string, string, string, boolean][] = [
    [String(s.mean), t.stats.mean, t.stats.meanNote(Math.round((s.mean / (total || 1)) * 100)), true],
    [String(s.median), t.stats.median, t.stats.outOf(total), false],
    [String(s.best.correct), t.stats.best, s.best.name, false],
    [String(s.worst.correct), t.stats.worst, s.worst.name, false],
    [mmss(s.avgSeconds), t.stats.avgTime, t.stats.outOfTime(mmss(stats.durationSeconds)), false],
    [String(s.blank), t.stats.blank, s.blankStudents.join(', ') || t.stats.nobody, false],
  ];
  const sortedTopics = [...stats.topics].sort((a, b) => a.percent - b.percent);
  const traps = stats.questions.filter((q) => q.trap).sort((a, b) => b.trap!.count - a.trap!.count);
  return (
    <div className="st-stack">
      <div className="st-kpis">
        {kpis.map(([v, label, note, dark]) => (
          <div key={label} className={`st-kpi${dark ? ' dark' : ''}`}>
            <strong>{v}</strong>
            <span>{label}</span>
            <small>{note}</small>
          </div>
        ))}
      </div>

      <div className="st-row">
        <Card title={t.stats.distTitle} sub={t.stats.distSub} className="grow">
          <DotPlot stats={stats} />
          <div className="st-facts">
            <div><small>{t.stats.spread}</small><strong>{t.stats.nQ(s.best.correct - s.worst.correct)}</strong><span>{t.stats.spreadNote}</span></div>
            <div><small>{t.stats.passed}</small><strong>{s.passed}/{n}</strong><span>{t.stats.passedNote}</span></div>
            <div><small>{t.stats.allOk}</small><strong>{t.stats.nQ(s.allCorrect)}</strong><span>{t.stats.allOkNote(n)}</span></div>
            <div><small>{t.stats.allBad}</small><strong className="red">{t.stats.nQ(s.allWrong)}</strong><span>{t.stats.allBadNote}</span></div>
          </div>
        </Card>
        <section className="st-insights">
          <p className="st-kick">{t.stats.insightsKicker}</p>
          <h2 className="st-title">{t.stats.insightsTitle(insights.length)}</h2>
          <ol>
            {insights.map((it, i) => (
              <li key={i}><b>{i + 1}</b><p><strong>{it.head}</strong> {it.body}</p></li>
            ))}
          </ol>
        </section>
      </div>

      {stats.topics.length >= 2 && <div className="st-row">
        <Card title={t.stats.topicTitle} sub={t.stats.topicSub} className="half">
          <div className="st-bars">
            {sortedTopics.map((tp) => (
              <div key={tp.name} className="st-hbar">
                <span>{tp.name}</span>
                <div><i className={`tone-${toneOf(stats, tp.percent)}`} style={{ width: pct(Math.max(tp.percent, 2)) }} /></div>
                <strong>{pct(tp.percent)}</strong>
              </div>
            ))}
          </div>
        </Card>
        <Card title={t.stats.heatTitle} sub={t.stats.heatSub} className="grow">
          <div className="st-scroll">
            <table className="st-heat" style={{ minWidth: 140 + stats.topics.length * 96 }}>
              <thead><tr><th scope="col"><span className="sr-only">{t.assignments.student}</span></th>{stats.topics.map((tp) => <th key={tp.name} scope="col">{tp.name}</th>)}</tr></thead>
              <tbody>
                {stats.students.map((st) => (
                  <tr key={st.id}>
                    <th scope="row">{st.name}</th>
                    {st.topics.map((v, i) => <td key={i} className={`tone-${toneOf(stats, v)}`}>{pct(v)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Legend items={[['tone-good', t.stats.toneGood], ['tone-mid', t.stats.toneMid], ['tone-weak', t.stats.toneWeak]]} />
        </Card>
      </div>}

      <Card title={t.stats.gridTitle} sub={t.stats.gridSub}>
        <Legend items={[['ans-ok', t.stats.ok], ['ans-bad', t.stats.bad], ['ans-blank', t.stats.blankShort], ['ans-flag', t.stats.flagged]]} />
        <div className="st-scroll">
          <div className="st-grid" style={{ gridTemplateColumns: `130px repeat(${stats.questions.length}, minmax(30px, 1fr)) 56px`, minWidth: 200 + stats.questions.length * 33 }}>
            <span className="st-ax">{t.stats.qShort}</span>
            {stats.questions.map((q) => <span key={q.number} className="st-cell head">{q.number}</span>)}
            <span className="st-ax end">{t.stats.score}</span>
            <span className="st-ax">{t.stats.keyRow}</span>
            {stats.questions.map((q) => <span key={q.number} className="st-cell key" title={q.key.join(' / ')}>{(q.key[0] ?? '').length <= 2 ? q.key[0]?.toUpperCase() : '•'}</span>)}
            <span />
            {stats.students.map((st) => (
              <GridRow key={st.id} st={st} />
            ))}
            <span className="st-ax">{t.stats.pctRow}</span>
            {stats.questions.map((q) => <span key={q.number} className={`st-cell small tone-${toneOf(stats, q.percent)}`}>{pct(q.percent)}</span>)}
            <span />
          </div>
        </div>
      </Card>

      <div className="st-row">
        <TimeScore stats={stats} />
        <Pace stats={stats} />
      </div>

      <div className="st-row">
        <Card title={t.stats.flagTitle} sub={t.stats.flagSub} className="third">
          <div className="st-big"><strong>{s.flagCorrect}/{s.flagTotal}</strong><span>{t.stats.flagOk}</span></div>
          {s.flagTotal > 0 && (
            <div className="st-split" aria-hidden="true">
              <i className="tone-good" style={{ width: pct((s.flagCorrect / s.flagTotal) * 100) }} />
              <i className="tone-weak" style={{ flex: 1 }} />
            </div>
          )}
          <p className="st-sub">{t.stats.flagNote}</p>
          <ul className="st-flaglist">
            {stats.students.filter((st) => st.answers.some((a) => a.flagged)).map((st) => (
              <li key={st.id}>
                <strong>{st.name}</strong>
                <span>{st.answers.filter((a) => a.flagged).map((a) => `${t.stats.qShort} ${a.number} ${a.result === 'ok' ? '✓' : '✗'}`).join(' · ')}</span>
              </li>
            ))}
          </ul>
        </Card>
      <Card title={t.stats.trapTitle} sub={t.stats.trapSub} className="grow">
        {traps.length === 0 ? <p className="st-sub">{t.stats.trapNone}</p> : (
          <div className="st-traps">
            {traps.map((q) => (
              <div key={q.number} className="st-trap">
                <div className="top"><strong>{t.stats.qLong(q.number)}</strong><span>{q.topic}</span></div>
                <p><b>{t.stats.trapPick(q.trap!.count, n, q.trap!.value)}</b> · {t.stats.trapKey(q.key[0] ?? '')}</p>
                <ChoiceBar q={q} n={n} />
              </div>
            ))}
          </div>
        )}
      </Card>
      </div>
    </div>
  );
}

function Pace({ stats }: { stats: AssignmentStats }) {
  const { t } = useI18n();
  const dur = Math.max(stats.durationSeconds, ...stats.students.map((x) => x.seconds), 1);
  const out = new Map(stats.summary!.outOfTime.map((o) => [o.name, o.blank]));
  const rows = [...stats.students].sort((a, b) => Number(isEmpty(a)) - Number(isEmpty(b)) || a.seconds - b.seconds);
  const mins = Math.round(dur / 60);
  const step = tickStep(mins, [1, 5, 10, 15, 30, 60]);
  return (
    <Card title={t.stats.paceTitle} sub={t.stats.paceSub} className="half">
      <div className="st-pace">
        <div className="st-prow head" aria-hidden="true">
          <span />
          <div>
            {ticks(mins, step).map((m) => (
              <span key={m} className={`st-ax${m === mins ? ' red end' : ''}`} style={at((m / (mins || 1)) * 100)}>{m === mins ? `${m} ${t.stats.minutes}` : m}</span>
            ))}
          </div>
          <span />
        </div>
        {rows.map((st) => {
          const empty = isEmpty(st);
          const blank = out.get(st.name);
          const note = empty ? t.stats.paceEmpty : blank !== undefined ? t.stats.paceOut(blank) : `${mmss(st.seconds)} · ${st.blank ? t.stats.paceBlank(st.blank) : t.stats.paceAll}`;
          return (
            <div key={st.id} className="st-prow">
              <strong>{st.name}</strong>
              <div className="st-ptrack"><i className={blank !== undefined ? 'up' : ''} style={{ width: pct(empty ? 0 : Math.min(100, (st.seconds / dur) * 100)) }} /></div>
              <span className={blank !== undefined ? 'red' : ''}>{note}</span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function GridRow({ st }: { st: StatsStudent }) {
  return (
    <>
      <span className="st-name">{st.name}</span>
      {st.answers.map((a) => (
        <span key={a.number} className={`st-cell ans-${a.result}`} title={a.given || undefined}>
          {cellText(a.given, a.result)}
          {a.flagged && <i className="st-flag" aria-label="⚑" />}
        </span>
      ))}
      <span className="st-cell score">{st.correct}</span>
    </>
  );
}

function ChoiceBar({ q, n }: { q: StatsQuestion; n: number }) {
  const { t } = useI18n();
  const segs = q.choices.filter((c) => c.count > 0);
  return (
    <div className="st-choicebar" aria-hidden="true">
      {segs.map((c) => (
        <span key={c.value} className={c.isKey ? 'tone-good' : c.value === q.trap?.value ? 'tone-weak' : 'tone-mid'} style={{ width: pct((c.count / n) * 100) }}>
          {c.value.length <= 3 ? c.value : ''}
        </span>
      ))}
      {q.blank.length > 0 && <span className="ans-blank" style={{ width: pct((q.blank.length / n) * 100) }}>{t.stats.blankShort}</span>}
    </div>
  );
}

/* ---------- Questions ---------- */

type Sort = 'number' | 'hard' | 'time';

/** Questions by share right (up) and average time (across), coloured green to red. */
function HardSlow({ stats, selected, onSelect }: { stats: AssignmentStats; selected: number; onSelect: (n: number) => void }) {
  const { t } = useI18n();
  const [ref, width] = useWidth<HTMLDivElement>(560);
  const H = 320;
  const strong = stats.summary?.bands.strong ?? 75;
  const top = Math.max(...stats.questions.map((x) => x.avgSeconds ?? 0), 1);
  const step = tickStep(top, [5, 10, 15, 30, 60, 120, 300]);
  const max = Math.ceil(top / step) * step;
  // Problem questions are placed first so they keep their true spot; the rest dodge around them.
  const order = [...stats.questions].sort((a, b) => Number(a.percent >= strong) - Number(b.percent >= strong) || a.percent - b.percent);
  const placed: { x: number; y: number; r: number }[] = [];
  const weak = stats.summary?.bands.weak ?? 50;
  const dots = order.map((q) => {
    const big = q.percent < strong || q.number === selected;
    const r = !big ? 7 : q.percent < weak || q.number === selected ? 15 : 12;
    const x0 = ((q.avgSeconds ?? 0) / max) * width;
    const y0 = (q.percent / 100) * H;
    const free = (x: number, y: number) => placed.every((p) => Math.hypot(p.x - x, p.y - y) >= p.r + r + 2);
    // Spiral out from the true spot until the dot fits inside the plot without touching another.
    let [x, y] = [x0, y0];
    for (let k = 1; k < 400 && !free(x, y); k++) {
      const d = (r + 2) * 0.6 * Math.sqrt(k);
      const cx = x0 + d * Math.cos(k * 2.4);
      const cy = y0 + d * Math.sin(k * 2.4);
      if (cx < 0 || cx > width || cy < 0 || cy > H) continue;
      [x, y] = [cx, cy];
    }
    placed.push({ x, y, r });
    return { q, big, r, left: x, bottom: y };
  });
  return (
    <Card title={t.stats.hardTitle} sub={t.stats.hardSub} className="grow">
      <div className="st-scale" aria-hidden="true">
        <span>{t.stats.hardScale[0]}</span><i /><span>{t.stats.hardScale[1]}</span>
      </div>
      <div ref={ref} className="st-plot hs" style={{ height: H }}>
        <i className="st-zone" aria-hidden="true" />
        <span className="st-zone-label">{t.stats.stuck}</span>
        {dots.map(({ q, big, r, left, bottom }) => (
          <button
            key={q.number}
            type="button"
            className={`st-qdot${big ? '' : ' small'}${q.number === selected ? ' on' : ''}`}
            style={{ ...scale(q.percent), left, bottom, width: r * 2, height: r * 2, fontSize: r < 15 ? '0.625rem' : undefined }}
            onClick={() => onSelect(q.number)}
            aria-label={t.stats.barLabel(q.number, q.percent)}
            title={`${t.stats.qLong(q.number)} · ${pct(q.percent)} · ${mmss(q.avgSeconds)}`}
          >
            {big ? q.number : ''}
          </button>
        ))}
        <span className="st-ax y-top">100%</span>
        <span className="st-ax y-mid">50%</span>
        <span className="st-ax y-bottom">0%</span>
        {ticks(max, step).map((v) => (
          <span key={v} className="st-ax x-tick" style={at((v / max) * 100)}>{mmss(v)}</span>
        ))}
      </div>
      <p className="st-axis-title">{t.stats.perQuestion}</p>
    </Card>
  );
}

function Questions({ stats, selected, onSelect }: { stats: AssignmentStats; selected: number; onSelect: (n: number) => void }) {
  const { t } = useI18n();
  const [sort, setSort] = useState<Sort>('number');
  const qs = stats.questions;
  const q = qs.find((x) => x.number === selected) ?? [...qs].sort((x, y) => x.percent - y.percent || x.number - y.number)[0];
  const n = stats.graded;
  const idx = q ? qs.indexOf(q) : -1;
  const avgPct = stats.summary ? Math.round((stats.summary.mean / (stats.total || 1)) * 100) : 0;
  const stuck = qs
    .filter((x) => x.percent < 100 && (x.avgSeconds ?? 0) > 0)
    .sort((a, b) => (100 - b.percent) * (b.avgSeconds ?? 0) - (100 - a.percent) * (a.avgSeconds ?? 0) || a.number - b.number)
    .slice(0, 6);
  const rows = [...qs];
  if (sort === 'hard') rows.sort((a, b) => a.percent - b.percent || a.number - b.number);
  if (sort === 'time') rows.sort((a, b) => (b.avgSeconds ?? 0) - (a.avgSeconds ?? 0));
  const topicMax = Math.max(1, ...stats.topics.map((tp) => tp.avgSeconds ?? 0));
  const disc = { good: t.stats.discGood, flat: t.stats.discFlat, reverse: t.stats.discReverse };
  if (!q) return null;
  return (
    <div className="st-stack">
      <Card title={t.stats.diffTitle} sub={t.stats.diffSub}>
        <Legend items={[['tone-good', t.stats.toneGood], ['tone-mid', t.stats.toneMid], ['tone-weak', t.stats.toneWeak]]} />
        <div className="st-scroll">
          <div style={{ minWidth: qs.length * 34 }}>
            <div className="st-vbars">
              <i className="st-vavg" style={{ bottom: pct(avgPct) }} aria-hidden="true"><span>{t.stats.meanShort} {pct(avgPct)}</span></i>
              {qs.map((x) => (
                <button key={x.number} type="button" aria-pressed={x.number === q.number} aria-label={t.stats.barLabel(x.number, x.percent)} onClick={() => onSelect(x.number)}>
                  <small>{pct(x.percent)}</small>
                  <i className={`tone-${toneOf(stats, x.percent)}`} style={{ height: pct(Math.max(x.percent, 3)) }} />
                </button>
              ))}
            </div>
            <div className="st-vlabels">
              {qs.map((x) => <span key={x.number} className={x.number === q.number ? 'on' : ''}>{x.number}</span>)}
            </div>
            <div className="st-vtopics" style={{ gridTemplateColumns: stats.topics.map((tp) => `${tp.questions.length}fr`).join(' ') }}>
              {stats.topics.map((tp) => <span key={tp.name}>{tp.name} · {pct(tp.percent)}</span>)}
            </div>
          </div>
        </div>
      </Card>

      <div className="st-row">
        <HardSlow stats={stats} selected={q.number} onSelect={onSelect} />
        <Card title={t.stats.stuckTitle} sub={t.stats.stuckSub} className="third">
          <div className="st-stuck">
            {stuck.map((x) => (
              <button key={x.number} type="button" className={`secondary${x.number === q.number ? ' on' : ''}`} onClick={() => onSelect(x.number)}>
                <span className="n" style={scale(x.percent)}>{x.number}</span>
                <span>
                  <strong>{t.stats.stuckRow(x.number, mmss(x.avgSeconds))}</strong>
                  <small>{x.trap ? t.stats.stuckWhy(x.trap.value, x.trap.count) : t.stats.stuckSpread}</small>
                </span>
                <b>{pct(x.percent)}</b>
              </button>
            ))}
          </div>
          {stats.topics.length >= 2 && (
            <div className="st-bars st-sep">
              <h3>{t.stats.topicTime}</h3>
              {stats.topics.map((tp) => (
                <div key={tp.name} className="st-hbar">
                  <span>{tp.name}</span>
                  <div><i className="tone-good" style={{ width: pct(((tp.avgSeconds ?? 0) / topicMax) * 100) }} /></div>
                  <strong>{mmss(tp.avgSeconds)}</strong>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="st-row">
        <section className="st-card grow">
          <div className="st-qhead">
            <div>
              <p className="st-kick">{q.topic}</p>
              <h2 className="st-qnum">{t.stats.qLong(q.number)}</h2>
            </div>
            <div className="st-nav">
              <button type="button" className="secondary" disabled={idx <= 0} onClick={() => onSelect(qs[idx - 1]?.number ?? 1)} aria-label={t.stats.prevQ}>‹</button>
              <button type="button" className="secondary" disabled={idx >= qs.length - 1} onClick={() => onSelect(qs[idx + 1]?.number ?? 1)} aria-label={t.stats.nextQ}>›</button>
            </div>
          </div>
          {q.prompt.trim() && <p className="st-prompt">{q.prompt}</p>}
          <div className="st-opts">
            <h3>{t.stats.whoPicked}</h3>
            {q.choices.map((c) => {
              const top = !c.isKey && c.value === q.trap?.value;
              return (
                <div key={c.value} className="st-opt">
                  <span className={`badge${c.isKey ? ' key' : top ? ' trap' : ''}`}>{c.value.length <= 3 ? c.value : c.isKey ? '✓' : '✗'}</span>
                  <div>
                    <div className="line"><span>{c.text || c.value}{c.isKey ? ` · ${t.stats.isKey}` : top ? ` · ${t.stats.mostPicked}` : ''}</span><strong>{t.stats.pickCount(c.count)}</strong></div>
                    <div className="track"><i className={c.isKey ? 'tone-good' : 'tone-weak'} style={{ width: pct(n ? (c.count / n) * 100 : 0) }} /></div>
                    <small>{c.students.length ? c.students.join(', ') : t.stats.nobodyPicked}</small>
                  </div>
                </div>
              );
            })}
            {q.blank.length > 0 && (
              <div className="st-opt">
                <span className="badge blank">–</span>
                <div>
                  <div className="line"><span>{t.stats.blankShort}</span><strong>{t.stats.pickCount(q.blank.length)}</strong></div>
                  <div className="track"><i className="ans-blank" style={{ width: pct((q.blank.length / n) * 100) }} /></div>
                  <small>{q.blank.join(', ')}</small>
                </div>
              </div>
            )}
          </div>
        </section>

        <section className="st-card third">
          <div className="st-mini3">
            <div><strong className={toneOf(stats, q.percent) === 'weak' ? 'red' : ''}>{pct(q.percent)}</strong><span>{t.stats.gotRight}</span></div>
            <div><strong>{mmss(q.avgSeconds)}</strong><span>{t.stats.avgTimeQ}</span></div>
            <div><strong>{q.flags}</strong><span>{t.stats.flagCount}</span></div>
          </div>
          {q.percent === 0 && q.trap && (
            <div className="st-alarm">
              <p className="st-kick on-ink">{t.stats.nobodyRight}</p>
              <p>{t.stats.nobodyRightBody(q.trap.count, n, q.trap.value)}</p>
            </div>
          )}
          <div className="st-why">
            <div className="line">
              <p className="st-kick ink">{t.stats.explanation}</p>
              {q.explanation && <span className="st-tag">{q.explanation.source === 'ai' ? t.stats.byAi : t.stats.byTeacher}</span>}
            </div>
            <p>{q.explanation?.text ?? t.stats.noExplanation}</p>
          </div>
          {q.discrimination && (
            <div className="st-disc st-sep">
              <p className="st-kick">{t.stats.discLabel}</p>
              <p><strong className={`disc-${q.discrimination}`}>{disc[q.discrimination]}.</strong> {t.stats.discNote[q.discrimination]}</p>
            </div>
          )}
        </section>
      </div>

      <Card title={t.stats.allQTitle} sub={t.stats.allQSub}>
        <div className="seg st-sort" role="group" aria-label={t.stats.sortLabel}>
          {(['number', 'hard', 'time'] as const).map((k) => (
            <button key={k} type="button" className="secondary" aria-pressed={sort === k} onClick={() => setSort(k)}>{t.stats.sorts[k]}</button>
          ))}
        </div>
        <div className="st-scroll">
          <table className="asg-table st-qtable">
            <thead>
              <tr>
                <th scope="col">{t.stats.qShort}</th>
                <th scope="col">{t.stats.topic}</th>
                <th scope="col">{t.stats.keyRow}</th>
                <th scope="col" className="w22">{t.stats.pctRow}</th>
                <th scope="col" className="w18">{t.stats.spreadPicks}</th>
                <th scope="col">{t.stats.avgTimeQ}</th>
                <th scope="col">{t.stats.flagCount}</th>
                <th scope="col">{t.stats.disc}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((x) => (
                <tr key={x.number} className={x.number === q.number ? 'on' : ''}>
                  <td><button type="button" className="secondary st-qbtn" onClick={() => onSelect(x.number)}>{x.number}</button></td>
                  <td>{x.topic}</td>
                  <td className="strong">{x.key.join(' / ')}</td>
                  <td><div className="st-inline"><div className="track"><i className={`tone-${toneOf(stats, x.percent)}`} style={{ width: pct(Math.max(x.percent, 2)) }} /></div><strong>{pct(x.percent)}</strong></div></td>
                  <td><ChoiceBar q={x} n={n} /></td>
                  <td>{mmss(x.avgSeconds)}</td>
                  <td>{x.flags}</td>
                  <td className={`disc-${x.discrimination ?? 'none'}`}>{x.discrimination ? disc[x.discrimination] : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

/* ---------- Student ---------- */

/** A ring split into arcs; `parts` are [value, colour] in order from 12 o'clock. */
function Ring({ parts, size, stroke, children, label, mark }: {
  parts: [number, string][];
  size: number;
  stroke: number;
  children?: React.ReactNode;
  label: string;
  mark?: number;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const sum = parts.reduce((x, [v]) => x + v, 0) || 1;
  let done = 0;
  const tick = mark === undefined ? null : (() => {
    const a = (mark / 100) * 2 * Math.PI - Math.PI / 2;
    const inner = r - stroke / 2 - 4;
    const outer = r + stroke / 2 + 4;
    const o = size / 2;
    return <line x1={o + inner * Math.cos(a)} y1={o + inner * Math.sin(a)} x2={o + outer * Math.cos(a)} y2={o + outer * Math.sin(a)} stroke="#e30b1c" strokeWidth={4} />;
  })();
  return (
    <div className="st-ring" style={{ width: size, height: size }}>
      <svg viewBox={`-6 -6 ${size + 12} ${size + 12}`} role="img" aria-label={label}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#efeeec" strokeWidth={stroke} />
        {parts.map(([v, colour], i) => {
          const len = (v / sum) * c;
          const el = v > 0 ? (
            <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={colour} strokeWidth={stroke}
              strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-done} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
          ) : null;
          done += len;
          return el;
        })}
        {tick}
      </svg>
      <div className="st-ring-mid">{children}</div>
    </div>
  );
}

/** Grey when the whole class missed it too, deep red when only this student did. */
function missColour(othersMissed: number, others: number): string {
  const solo = others > 0 ? 1 - othersMissed / others : 1;
  return `color-mix(in srgb, #e30b1c ${Math.round(solo * 100)}%, #c9c7c3)`;
}

function Student({ stats, studentId, onPick, onOpen }: { stats: AssignmentStats; studentId: string | null; onPick: (id: string) => void; onOpen: (n: number) => void }) {
  const { t } = useI18n();
  const st = stats.students.find((x) => x.id === studentId) ?? stats.students[stats.students.length - 1];
  if (!st) return null;
  const s = stats.summary!;
  const total = stats.total || 1;
  const others = stats.graded - 1;
  const manyTopics = stats.topics.length >= 2;
  const delta = st.correct - s.mean;
  const rows = stats.topics.map((tp, k) => ({ name: tp.name, v: st.topics[k] ?? 0, cls: tp.percent, d: (st.topics[k] ?? 0) - tp.percent, questions: tp.questions }));
  const strong = rows.filter((r) => r.v >= s.bands.strong).sort((a, b) => b.v - a.v);
  const weak = rows.filter((r) => r.v < s.bands.strong).sort((a, b) => a.v - b.v);
  const byHigh = [...rows].sort((a, b) => b.v - a.v);
  const qAvg = new Map(stats.questions.map((q) => [q.number, q.avgSeconds ?? 0]));
  const maxSec = Math.max(30, ...st.answers.map((a) => a.seconds ?? 0), ...qAvg.values());
  const wrong = st.answers.filter((a) => a.result === 'bad').sort((a, b) => a.othersMissed - b.othersMissed || a.number - b.number);
  const solo = wrong.filter((a) => a.othersMissed === 0).length;
  const mistakes = st.answers.filter((a) => a.result !== 'ok');
  const keyOf = new Map(stats.questions.map((q) => [q.number, q]));
  const lanes = others > 0
    ? [
      { key: 'own', head: t.stats.laneOwn(st.name), note: t.stats.laneOwnNote, items: wrong.filter((a) => a.othersMissed === 0) },
      { key: 'some', head: t.stats.laneSome, note: t.stats.laneSomeNote(others), items: wrong.filter((a) => a.othersMissed > 0 && a.othersMissed < others) },
      { key: 'all', head: t.stats.laneAll, note: t.stats.laneAllNote, items: wrong.filter((a) => a.othersMissed >= others) },
    ]
    : [{ key: 'some', head: '', note: '', items: wrong }];
  const signed = (d: number) => `${d > 0 ? '+' : d < 0 ? '−' : '±'}${Math.abs(d)}`;
  const mine = Math.round((st.correct / total) * 100);
  const classPct = Math.round((s.mean / total) * 100);
  const tips: string[] = [];
  const out = s.outOfTime.find((o) => o.name === st.name);
  if (out) tips.push(t.stats.tipTime(out.blank));
  for (const w of weak.filter((r) => manyTopics && r.v < s.bands.weak).slice(0, 3)) {
    const missed = w.questions.filter((n) => st.answers.find((a) => a.number === n)?.result !== 'ok');
    tips.push(t.stats.tipTopic(w.name, missed.join(', ')));
  }
  if (others > 0 && solo > 0) tips.push(t.stats.tipOwn(solo));
  if (tips.length === 0) tips.push(t.stats.tipNone);
  return (
    <div className="st-stack">
      <div className="st-picker" role="group" aria-label={t.stats.pickStudent}>
        {stats.students.map((x) => (
          <button key={x.id} type="button" className="secondary" aria-pressed={x.id === st.id} onClick={() => onPick(x.id)}>
            <strong>{x.name}</strong>
            <small>{x.correct}/{x.total}</small>
          </button>
        ))}
      </div>

      <section className="st-hero">
        <div className="who">
          <p className="st-kick on-ink">{t.stats.rank(st.rank, stats.graded)}</p>
          <h2>{st.name}</h2>
          <p>
            {manyTopics && t.stats.topicLine(byHigh[0]?.name ?? '', byHigh[0]?.v ?? 0, byHigh[byHigh.length - 1]?.name ?? '', byHigh[byHigh.length - 1]?.v ?? 0)}
            {t.stats.missLine(wrong.length, solo, st.blank)}
          </p>
        </div>
        <div className="nums">
          <div><strong>{st.correct}/{st.total}</strong><span>{t.stats.score}{st.band ? ` · band ${st.band}` : ''}</span></div>
          <div><strong className={delta < 0 ? 'red' : ''}>{signed(Math.round(delta * 10) / 10)}</strong><span>{t.stats.vsMean}</span></div>
          <div><strong>{mmss(st.seconds)}</strong><span>{t.stats.outOfTime(mmss(stats.durationSeconds))}</span></div>
          <div><strong>{st.blank}</strong><span>{t.stats.blank}</span></div>
        </div>
      </section>

      <div className="st-row">
        <Card title={t.stats.profileTitle} sub={t.stats.resultSub} className="third">
          <div className="st-result">
            <Ring
              size={180}
              stroke={22}
              mark={classPct}
              label={t.stats.dotTip(st.name, st.correct, stats.total, mine)}
              parts={[[st.correct, '#0c0c0d'], [wrong.length, '#e30b1c'], [st.blank, '#c9c7c3']]}
            >
              <strong>{mine}%</strong>
              <small>{t.stats.classAvg} {classPct}%</small>
            </Ring>
            <ul className="st-result-key">
              <li><i style={{ background: '#0c0c0d' }} />{t.stats.ok}<b>{st.correct}</b></li>
              <li><i style={{ background: '#e30b1c' }} />{t.stats.bad}<b>{wrong.length}</b></li>
              <li><i style={{ background: '#c9c7c3' }} />{t.stats.blankShort}<b>{st.blank}</b></li>
            </ul>
          </div>
          {manyTopics && (
            <div className="st-chips st-sep">
              <h3>{t.stats.byTopic}</h3>
              <div className="st-rings">
                {rows.map((r) => (
                  <div key={r.name} className="st-ring-item">
                    <Ring size={76} stroke={10} mark={r.cls} label={`${r.name}: ${r.v}%`} parts={[[r.v, r.v >= s.bands.strong ? '#0c0c0d' : r.v >= s.bands.weak ? '#8a8a90' : '#e30b1c'], [100 - r.v, 'transparent']]}>
                      <strong className="sm">{r.v}%</strong>
                    </Ring>
                    <span>{r.name}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="st-cmp st-sep">
            <p className="st-kick ink">{t.stats.cmpTitle}</p>
            {([[st.name, mine, '#0c0c0d'], [t.stats.avgLabel, classPct, '#e30b1c'], [t.stats.median, Math.round((s.median / total) * 100), '#c9c7c3']] as const).map(([label, v, colour]) => (
              <div key={label} className="st-cmp-row">
                <span>{label}</span>
                <div><i style={{ width: pct(v), background: colour }} /></div>
                <strong>{pct(v)}</strong>
              </div>
            ))}
          </div>
        </Card>
        <Card title={t.stats.missTitle} sub={wrong.length ? t.stats.missSub : undefined} className="grow">
          {wrong.length === 0 ? <p className="st-sub">{t.stats.noMistakes}</p> : (
            <div className="st-lanes">
              {lanes.filter((l) => l.items.length > 0).map((l) => (
                <div key={l.key} className={`st-lane ${l.key}`}>
                  {l.head && <div><h3>{l.head}</h3><p>{l.note}</p></div>}
                  <div className="st-mchips">
                    {l.items.map((a) => (
                      <button key={a.number} type="button" className="secondary" onClick={() => onOpen(a.number)} aria-label={`${t.stats.qLong(a.number)}: ${t.stats.chipPick(a.given, keyOf.get(a.number)?.key[0] ?? '')}`}>
                        <b>{a.number}</b>
                        <span>{t.stats.chipPick(a.given, keyOf.get(a.number)?.key[0] ?? '')}{others > 0 && l.key === 'some' ? ` · ${a.othersMissed}/${others}` : ''}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          {st.blank > 0 && <p className="st-sub">{t.stats.missBlank(st.blank)}</p>}
          {manyTopics && (
            <div className="st-sep st-sw-cols">
              <div className="st-chips">
                <p className="st-kick ink">{t.stats.strong}</p>
                {strong.map((r) => <div key={r.name} className="st-chip"><span>{r.name}</span><span><strong>{pct(r.v)}</strong> <small>{signed(r.d)}</small></span></div>)}
                {strong.length === 0 && <p className="st-sub">{t.stats.noStrong(s.bands.strong)}</p>}
              </div>
              <div className="st-chips">
                <p className="st-kick">{t.stats.weak}</p>
                {weak.map((r) => <div key={r.name} className="st-chip weak"><span>{r.name}</span><span><strong>{pct(r.v)}</strong> <small>{signed(r.d)}</small></span></div>)}
                {weak.length === 0 && <p className="st-sub">{t.stats.noWeak}</p>}
              </div>
            </div>
          )}
          <div className="st-tips">
            <div className="line">
              <p className="st-kick on-ink">{t.stats.tipsTitle(st.name)}</p>
              <span className="st-tag">{t.stats.tipsTag}</span>
            </div>
            <ol>{tips.map((tip, i) => <li key={i}><b>{i + 1}</b><span>{tip}</span></li>)}</ol>
          </div>
        </Card>
      </div>

      <Card title={t.stats.eachTitle} sub={t.stats.eachSub(st.name)}>
        <Legend items={[['ans-ok', t.stats.ok], ['ans-bad', t.stats.bad], ['ans-blank', t.stats.blankShort], ['line', t.stats.classAvg], ['ans-flag', t.stats.flagged]]} />
        <div className="st-scroll">
          <div className="st-grid" style={{ gridTemplateColumns: `70px repeat(${st.answers.length}, minmax(30px, 1fr))`, minWidth: 80 + st.answers.length * 33 }}>
            <span className="st-ax bottom">{t.stats.seconds}</span>
            {st.answers.map((a) => (
              <div key={a.number} className="st-tbar" title={`${mmss(a.seconds)} / ${t.stats.classAvg} ${mmss(qAvg.get(a.number))}`}>
                <i className={`ans-${a.result} soft`} style={{ height: pct(((a.seconds ?? 0) / maxSec) * 100) }} />
                <b style={{ bottom: pct(((qAvg.get(a.number) ?? 0) / maxSec) * 100) }} />
              </div>
            ))}
            <span className="st-ax">{t.stats.picked}</span>
            {st.answers.map((a) => (
              <span key={a.number} className={`st-cell ans-${a.result}`} title={a.given || undefined}>
                {cellText(a.given, a.result)}
                {a.flagged && <i className="st-flag" aria-label="⚑" />}
              </span>
            ))}
            <span className="st-ax">{t.stats.keyRow}</span>
            {st.answers.map((a) => {
              const k = keyOf.get(a.number)?.key[0] ?? '';
              return <span key={a.number} className="st-cell key thin" title={k}>{k.length <= 2 ? k.toUpperCase() : '•'}</span>;
            })}
            <span className="st-ax">{t.stats.qShort}</span>
            {st.answers.map((a) => <span key={a.number} className="st-cell head">{a.number}</span>)}
          </div>
        </div>
      </Card>

      <Card title={t.stats.mistakesTitle} sub={others > 0 ? t.stats.mistakesSub : undefined}>
        {mistakes.length === 0 ? <p className="st-sub">{t.stats.noMistakes}</p> : (
          <div className="st-scroll">
            <table className="asg-table">
              <thead>
                <tr>
                  <th scope="col">{t.stats.qShort}</th>
                  {manyTopics && <th scope="col">{t.stats.topic}</th>}
                  <th scope="col">{t.stats.picked}</th>
                  <th scope="col">{t.stats.keyRow}</th>
                  <th scope="col">{t.stats.timeCol}</th>
                  {others > 0 && <th scope="col">{t.stats.othersCol}</th>}
                </tr>
              </thead>
              <tbody>
                {mistakes.map((a) => {
                  const q = keyOf.get(a.number);
                  return (
                    <tr key={a.number}>
                      <td className="strong">{a.number}</td>
                      {manyTopics && <td>{q?.topic}</td>}
                      <td className="late">{a.result === 'blank' ? t.stats.blankShort : a.given}</td>
                      <td className="strong">{q?.key.join(' / ')}</td>
                      <td>{mmss(a.seconds)}</td>
                      {others > 0 && (
                        <td>
                          <span className="st-tag" style={{ background: missColour(a.othersMissed, others), color: a.othersMissed / others > 0.5 ? '#111112' : '#fff' }}>
                            {a.othersMissed}/{others}
                          </span>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

    </div>
  );
}

export function StatsPanel({ stats, tab, question, student, onQuestion, onStudent, onOpenQuestion }: {
  stats: AssignmentStats;
  tab: StatsTab;
  question: number;
  student: string | null;
  onQuestion: (n: number) => void;
  onStudent: (id: string) => void;
  /** Switch to the question tab on question `n`. */
  onOpenQuestion?: (n: number) => void;
}) {
  if (!stats.summary) return null;
  if (tab === 'questions') return <Questions stats={stats} selected={question} onSelect={onQuestion} />;
  if (tab === 'students') return <Student stats={stats} studentId={student} onPick={onStudent} onOpen={onOpenQuestion ?? onQuestion} />;
  return <Overview stats={stats} />;
}

/** One row per student, for a spreadsheet. */
export function statsCsv(stats: AssignmentStats): string {
  const esc = (v: string | number) => `"${String(v).replaceAll('"', '""')}"`;
  const head = ['student', 'correct', 'total', 'percent', 'time', ...stats.topics.map((tp) => tp.name), ...stats.questions.map((q) => `Q${q.number}`)];
  const lines = stats.students.map((s) => [s.name, s.correct, s.total, s.percent, mmss(s.seconds), ...s.topics, ...s.answers.map((a) => (a.result === 'blank' ? '' : a.given))]);
  return [head, ...lines].map((r) => r.map(esc).join(',')).join('\n');
}
