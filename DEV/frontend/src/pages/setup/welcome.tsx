import { useEffect, useRef, useState } from 'react';
import author from '../../assets/authors/person1.webp';
import type { Copy } from '../../locales/vi';
import { HEART, SetupIcon, STAR } from './icons';

const COLORS = ['#e30b1c', '#ffffff', '#ff5a66', '#e30b1c'];

/** Fixed seed, so the confetti falls the same way on every render and replay. */
function confetti() {
  let seed = 11;
  const rnd = () => {
    seed = (seed * 9301 + 49297) % 233280;
    return seed / 233280;
  };
  return Array.from({ length: 46 }, (_, i) => ({
    left: `${(rnd() * 100).toFixed(1)}%`,
    width: Math.round(6 + rnd() * 10),
    height: Math.round(8 + rnd() * 16),
    background: COLORS[i % 4],
    animationDelay: `${(0.9 + rnd() * 3).toFixed(2)}s`,
    animationDuration: `${(3 + rnd() * 3).toFixed(2)}s`,
  }));
}
const CONFETTI = confetti();

const HEARTS = [[18, 40, 30, 3.1], [70, 10, 22, 3.6], [200, 30, 34, 3.35], [150, 0, 20, 4], [230, 110, 24, 4.3]];
const SPARKS = [[-6, 120, 26, 2.9, ''], [236, 60, 20, 3.3, ''], [110, -10, 22, 3.7, ' red'], [40, 200, 16, 4.1, '']] as const;

type Props = {
  copy: Copy['setup']['welcome'];
  teacher: boolean;
  name: string;
  photo: string | null;
  chips: { k: string; v: string }[];
  onDone: () => void;
};

export function Welcome({ copy, teacher, name, photo, chips, onDone }: Props) {
  const [run, setRun] = useState(0);
  const headRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => headRef.current?.focus(), [run]);

  return (
    <section key={run} className="wel" aria-labelledby="su-welcome">
      <div className="slab" style={{ top: '8%', background: '#e30b1c' }} />
      <div className="slab" style={{ top: '38%', background: '#f5f4f2', animationDelay: '.1s' }} />
      <div className="slab" style={{ top: '66%', background: '#e30b1c', animationDelay: '.2s' }} />
      {[0.35, 0.55, 0.75].map((d) => <div key={d} className="ringb" style={{ animationDelay: `${d}s` }} />)}
      {CONFETTI.map((style, i) => <i key={i} className="conf" style={style} />)}
      <button type="button" className="replay" onClick={() => setRun((r) => r + 1)}>
        <SetupIcon name="replay" />{copy.replay}
      </button>
      <div className="wc">
        <div className="weye">{teacher ? copy.teacherEye : copy.eye}</div>
        <div className="wmark" role="img" aria-label="PANXG">
          {['P', 'A', 'N', 'X', 'G'].map((ch, i) => (
            <span key={ch} className={ch === 'X' ? 'x' : undefined} style={{ animationDelay: `${(0.55 + i * 0.07).toFixed(2)}s` }}>{ch}</span>
          ))}
        </div>
        <div className="wband">
          <h1 id="su-welcome" ref={headRef} tabIndex={-1} className="wname">
            {teacher ? copy.teacherName(name) : copy.name(name)}
          </h1>
        </div>
        <p className="wsub">{teacher ? copy.teacherSub : copy.sub}</p>
        <div className="wchips">
          {chips.map((c, i) => (
            <span key={c.k} className="wchip" style={{ animationDelay: `${(1.85 + i * 0.12).toFixed(2)}s` }}><b>{c.k}</b>{c.v}</span>
          ))}
        </div>
        <button type="button" className="wcta" onClick={onDone}>
          {teacher ? copy.teacherCta : copy.cta}<SetupIcon name="next" />
        </button>
      </div>
      {photo && <div className="polaroid" aria-hidden="true"><img src={photo} alt="" referrerPolicy="no-referrer" /><span>{name}</span></div>}
      <div className="author">
        <div className="author-pop">
          <div className="author-wig"><img className="sticker" src={author} alt={copy.authorAlt} /></div>
        </div>
        <div className="bubble">{copy.bubble}</div>
        {HEARTS.map(([left, top, width, delay]) => (
          <svg key={`${left}-${top}`} className="heart" style={{ left, top, width, animationDelay: `${delay}s` }} viewBox="0 0 24 24" aria-hidden="true"><path d={HEART} /></svg>
        ))}
        {SPARKS.map(([left, top, width, delay, red]) => (
          <svg key={`${left}-${top}`} className={`spark${red}`} style={{ left, top, width, animationDelay: `${delay}s` }} viewBox="0 0 24 24" aria-hidden="true"><path d={STAR} /></svg>
        ))}
      </div>
      <i className="camflash" />
      <div className="tickwrap" aria-hidden="true">
        <div className="ticker">
          {[0, 1, 2, 3].map((i) => <span key={i}>READING · LISTENING · WRITING · SPEAKING ·</span>)}
        </div>
      </div>
    </section>
  );
}
