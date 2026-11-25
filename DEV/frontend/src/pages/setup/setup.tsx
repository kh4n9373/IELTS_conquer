import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type ChangeEvent, type CSSProperties, type DragEvent, type ReactNode } from 'react';
import { Link, Navigate, useNavigate } from 'react-router';
import { homeFor, useMe } from '../../app/session';
import { api, type Me, type Skill } from '../../lib/api';
import { useI18n } from '../../lib/i18n';
import { clearPending, loadPending, type Pending } from '../../lib/onboarding';
import { ErrorAlert, PageLoader } from '../../components/ui';
import type { Copy } from '../../locales/vi';
import { ReportCard } from './card';
import { SetupIcon, type SetupIconName } from './icons';
import {
  AGE, type Answers, BANDS, bandAt, initialAnswers, LEVELS, levelAt, minutesLabel, MINUTES, minutesAt, OCCUPATIONS,
  normalizeCode, pad, PROFILE_STEPS, setupBody, SOURCES, STYLES, TIMES, toJpeg, type GateStep, type StepKey,
} from './model';
import { Welcome } from './welcome';
import './setup.css';

type Dir = 'fwd' | 'back';
type Set = <K extends keyof Answers>(key: K, value: Answers[K] | ((v: Answers[K]) => Answers[K])) => void;
type StepProps = { a: Answers; set: Set; copy: Copy['setup'] };

/**
 * The "chữ" transition: the question flips in word by word in 3D (top-down when going back).
 * Remounting by step replays the CSS keyframes, so no animation library is involved.
 */
function Kinetic({ text, small, id }: { text: string; small?: boolean; id: string }) {
  return (
    <h1 id={id} tabIndex={-1} className={`q kin${small ? ' sm' : ''}`} aria-label={text}>
      {text.split(' ').map((w, i) => (
        <span key={i} className="wd" aria-hidden="true" style={{ animationDelay: `${(0.28 + i * 0.055).toFixed(3)}s` }}>{w}</span>
      ))}
    </h1>
  );
}

function Tile({ on, icon, title, hint, delay, small, onPick }: {
  on: boolean; icon: SetupIconName; title: string; hint?: string; delay: number; small?: boolean; onPick: () => void;
}) {
  return (
    <button type="button" role="radio" aria-checked={on} className={`tile rise${small ? ' s' : ''}${on ? ' on' : ''}`} style={{ animationDelay: `${delay}s` }} onClick={onPick}>
      <span className="tick"><SetupIcon name="check" /></span>
      <SetupIcon name={icon} className="big" />
      <span className="tl">{title}</span>
      {hint && <span className="ts">{hint}</span>}
    </button>
  );
}

function NameAge({ a, set, copy, teacher, photo, photoBusy, photoError, onPhoto, onEnter }: StepProps & {
  teacher: boolean; photo: string | null; photoBusy: boolean; photoError: boolean;
  onPhoto: (e: ChangeEvent<HTMLInputElement>) => void; onEnter: () => void;
}) {
  const name = a.name.trim();
  const max = teacher ? AGE.teacherMax : AGE.studentMax;
  const setAge = (v: number) => set('age', Math.max(AGE.min, Math.min(max, v)));
  const greet = name ? (teacher ? copy.teacherGreet(name) : copy.greet(name)) : null;
  const ticks = teacher ? [18, 28, 38, 48, 58, 70] : [18, 26, 34, 42, 50, 60];
  return (
    <>
      <p className="sub rise" style={{ animationDelay: '.42s' }}>{teacher ? copy.subs.teacherName : copy.subs.name}</p>
      <div className="namerow rise" style={{ animationDelay: '.5s' }}>
        <label className={`up${photo ? ' has' : ''}`} aria-busy={photoBusy}>
          <input className="upin" type="file" accept="image/*" onChange={onPhoto} aria-label={copy.photoLabel} />
          {photo ? (
            <><img key={photo} className="upimg" src={photo} alt={copy.photoAlt} referrerPolicy="no-referrer" /><i className="upflash" /></>
          ) : (
            <span className="upph"><SetupIcon name="camera" />{copy.photoAdd}</span>
          )}
          <span className="upbadge"><SetupIcon name="plus" /></span>
        </label>
        <div style={{ flex: 1, minWidth: 0 }}>
          <label htmlFor="su-name" className="lab" style={{ display: 'block', marginTop: 0 }}>{copy.nameLabel}</label>
          <input
            id="su-name"
            className="nm"
            type="text"
            value={a.name}
            onChange={(e) => set('name', e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && name) onEnter(); }}
            placeholder={teacher ? copy.teacherNamePh : copy.namePh}
            maxLength={60}
            autoComplete="nickname"
          />
          <div className="greet" aria-live="polite">
            {greet && <span key={name}>{greet[0]}<b>{greet[1]}</b>{greet[2]}</span>}
          </div>
        </div>
      </div>
      {photoError && <p className="err" role="alert">{copy.photoError}</p>}
      <div className="rise" style={{ animationDelay: '.6s' }}>
        <div className="lab" id="su-age-label">{copy.ageLabel}</div>
        <div className="agebox">
          <div className="agenum" aria-hidden="true">{a.age}<small>{copy.ageUnit}</small></div>
          <div className="agectl">
            <div className="steprow">
              <button type="button" className="sq" onClick={() => setAge(a.age - 1)} aria-label={copy.ageDown}><SetupIcon name="minus" /></button>
              <button type="button" className="sq" onClick={() => setAge(a.age + 1)} aria-label={copy.ageUp}><SetupIcon name="plus" /></button>
            </div>
            <input className="rng" type="range" min={AGE.min} max={max} value={a.age} onChange={(e) => setAge(Number(e.target.value))} aria-labelledby="su-age-label" />
            <div className="ticks" aria-hidden="true">{ticks.map((t) => <span key={t}>{t}</span>)}</div>
          </div>
        </div>
        <p className="hint">{copy.ageHint}</p>
      </div>
    </>
  );
}

function Occupation({ a, set, copy }: StepProps) {
  const icons = { school: 'school', university: 'university', working: 'working', other: 'spark' } as const;
  return (
    <>
      <p className="sub rise" style={{ animationDelay: '.42s' }}>{copy.subs.occupation}</p>
      <div className="grid2" role="radiogroup" aria-labelledby="su-q">
        {OCCUPATIONS.map((o, i) => (
          <Tile key={o} on={a.occupation === o} icon={icons[o]} title={copy.occupations[o][0] ?? o} hint={copy.occupations[o][1]} delay={0.5 + i * 0.06} onPick={() => set('occupation', o)} />
        ))}
      </div>
    </>
  );
}

function Source({ a, set, copy }: StepProps) {
  const icons = { facebook: 'facebook', tiktok: 'tiktok', instagram: 'instagram', youtube: 'youtube', friend: 'friend', other: 'dots' } as const;
  return (
    <>
      <p className="sub rise" style={{ animationDelay: '.42s' }}>{copy.subs.referral}</p>
      <div className="grid3" role="radiogroup" aria-labelledby="su-q">
        {SOURCES.map((s, i) => (
          <Tile key={s} small on={a.source === s} icon={icons[s]} title={copy.sources[s]} delay={0.5 + i * 0.05} onPick={() => set('source', s)} />
        ))}
      </div>
    </>
  );
}

function Target({ a, set, copy }: StepProps) {
  const drumRef = useRef<HTMLDivElement>(null);
  const band = bandAt(a.bandIdx);
  const [en, vi] = copy.bandDesc[Math.floor(band)] ?? ['', ''];
  const move = (d: number) => set('bandIdx', (i) => Math.max(0, Math.min(BANDS.length - 1, i + d)));

  // React's wheel handler is passive, so the page would scroll too; a native listener can stop that.
  useEffect(() => {
    const el = drumRef.current;
    if (!el) return;
    let last = 0;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const now = Date.now();
      if (now - last < 160) return;
      last = now;
      set('bandIdx', (i) => Math.max(0, Math.min(BANDS.length - 1, i + (e.deltaY > 0 ? 1 : -1))));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [set]);

  return (
    <>
      <p className="sub rise" style={{ animationDelay: '.42s' }}>{copy.subs.target}</p>
      <div className="drumrow rise" style={{ animationDelay: '.5s' }}>
        <div className="drum" ref={drumRef} role="radiogroup" aria-labelledby="su-q">
          <div className="dlist" style={{ transform: `translateY(${(2 - a.bandIdx) * 72}px)` }}>
            {BANDS.map((b, i) => (
              <button
                key={b}
                type="button"
                role="radio"
                aria-checked={i === a.bandIdx}
                tabIndex={i === a.bandIdx ? 0 : -1}
                className={`ditem${i === a.bandIdx ? ' on' : ''}`}
                style={{ opacity: Math.max(0.15, 1 - Math.abs(i - a.bandIdx) * 0.3) }}
                onClick={() => set('bandIdx', i)}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                    e.preventDefault();
                    move(e.key === 'ArrowDown' ? 1 : -1);
                  }
                }}
              >
                {b.toFixed(1)}
              </button>
            ))}
          </div>
        </div>
        <div className="dbtns">
          <button type="button" className="sq" onClick={() => move(1)} aria-label={copy.bandUp}><SetupIcon name="up" /></button>
          <button type="button" className="sq" onClick={() => move(-1)} aria-label={copy.bandDown}><SetupIcon name="down" /></button>
        </div>
        <div className="gauge" aria-hidden="true">
          <svg viewBox="0 0 300 170">
            <path className="gtrack" d="M 20 150 A 130 130 0 0 1 280 150" />
            <path className="gfill" d="M 20 150 A 130 130 0 0 1 280 150" style={{ strokeDasharray: 409, strokeDashoffset: 409 * (1 - (band - 4) / 5) }} />
          </svg>
          <div className="gends"><span>4.0</span><span>9.0</span></div>
          <div className="gnum">{band.toFixed(1)}</div>
          <div className="gdesc">{en}<small>{vi}</small></div>
        </div>
      </div>
    </>
  );
}

function Level({ a, set, copy }: StepProps) {
  const cur = levelAt(a.levelIdx);
  const [, desc, ielts] = copy.levels[cur];
  return (
    <>
      <p className="sub rise" style={{ animationDelay: '.42s' }}>{copy.subs.level}</p>
      <div className="stairs rise" style={{ animationDelay: '.5s' }} role="radiogroup" aria-labelledby="su-q">
        {LEVELS.map((lv, i) => (
          <button
            key={lv}
            type="button"
            role="radio"
            aria-checked={i === a.levelIdx}
            className={`stair${i === a.levelIdx ? ' on' : i < a.levelIdx ? ' done' : ''}`}
            style={{ height: 74 + i * 36 }}
            onClick={() => set('levelIdx', i)}
          >
            {i === a.levelIdx && <span className="flag" aria-hidden="true">{copy.youAreHere}</span>}
            {lv}<small>{copy.levels[lv][0]}</small>
          </button>
        ))}
      </div>
      <div className="lvdesc" key={cur} aria-live="polite">
        <strong>{cur}</strong>
        <p>{desc}<em>{ielts}</em></p>
      </div>
    </>
  );
}

function Skills({ a, set, copy }: StepProps) {
  const [drag, setDrag] = useState<Skill | null>(null);
  const [over, setOver] = useState<Skill | null>(null);
  const [just, setJust] = useState<Skill | null>(null);
  const icons = { reading: 'reading', listening: 'listening', writing: 'writing', speaking: 'speaking' } as const;
  const name = (k: Skill) => k.charAt(0).toUpperCase() + k.slice(1);

  const move = (k: Skill, d: number) => {
    const list = a.skills.slice();
    const i = list.indexOf(k);
    const j = i + d;
    if (j < 0 || j > 3) return;
    [list[i], list[j]] = [list[j]!, list[i]!];
    set('skills', list);
    setJust(k);
  };
  const dropOn = (target: Skill) => {
    if (drag && drag !== target) {
      const list = a.skills.filter((x) => x !== drag);
      list.splice(a.skills.indexOf(target), 0, drag);
      set('skills', list);
      setJust(drag);
    }
    setDrag(null);
    setOver(null);
  };

  return (
    <>
      <p className="sub rise" style={{ animationDelay: '.42s' }}>{copy.subs.skills}</p>
      <ol className="sks rise" style={{ animationDelay: '.5s', listStyle: 'none', margin: 0, padding: 0 }} aria-label={copy.rankList}>
        {a.skills.map((k, i) => (
          <li
            key={k}
            className={['sk', i === 0 && 'top1', drag === k && 'drag', drag && drag !== k && over === k && 'over', just === k && 'just'].filter(Boolean).join(' ')}
            draggable
            onDragStart={(e: DragEvent) => { e.dataTransfer.setData('text/plain', k); e.dataTransfer.effectAllowed = 'move'; setDrag(k); setJust(null); }}
            onDragOver={(e) => { e.preventDefault(); if (over !== k) setOver(k); }}
            onDrop={(e) => { e.preventDefault(); dropOn(k); }}
            onDragEnd={() => { setDrag(null); setOver(null); }}
          >
            <div className="rk">{i + 1}</div>
            <SetupIcon name="grip" className="grip" />
            <SetupIcon name={icons[k]} className="skic" />
            <div className="skname">{name(k)}<small>{copy.skills[k]}</small></div>
            <div className="skbar">
              <i style={{ width: `${100 - i * 25}%` }} />
              {(i === 0 || i === 3) && <span className="sktag">{i === 0 ? copy.strongest : copy.weakest}</span>}
            </div>
            <button type="button" className="mv" onClick={() => move(k, -1)} disabled={i === 0} aria-label={copy.moveUp(name(k))}><SetupIcon name="up" /></button>
            <button type="button" className="mv" onClick={() => move(k, 1)} disabled={i === 3} aria-label={copy.moveDown(name(k))}><SetupIcon name="down" /></button>
          </li>
        ))}
      </ol>
    </>
  );
}

function Habits({ a, set, copy }: StepProps) {
  const minutes = minutesAt(a.minutesIdx);
  const time = copy.times[a.time];
  return (
    <div className="hgrid">
      <div className="hb rise" style={{ animationDelay: '.46s' }} role="radiogroup" aria-labelledby="su-h-min">
        <h3><span id="su-h-min">{copy.perDay}</span></h3>
        <div className="minrow">
          <div className="ring" aria-hidden="true">
            <svg viewBox="0 0 84 84"><circle className="rtrack" cx="42" cy="42" r="34" /><circle className="rfill" cx="42" cy="42" r="34" style={{ strokeDasharray: 213.6, strokeDashoffset: 213.6 * (1 - minutes / 60) }} /></svg>
            <b><span>{minutesLabel(minutes)}′</span></b>
          </div>
          <div className="minchips">
            {MINUTES.map((m, i) => (
              <button key={m} type="button" role="radio" aria-checked={i === a.minutesIdx} aria-label={copy.minutes(minutesLabel(m))} className={`chip${i === a.minutesIdx ? ' on' : ''}`} onClick={() => set('minutesIdx', i)}>{minutesLabel(m)}′</button>
            ))}
          </div>
        </div>
      </div>
      <div className="hb rise" style={{ animationDelay: '.52s' }} role="radiogroup" aria-labelledby="su-h-time">
        <h3><span id="su-h-time">{copy.bestTime}</span></h3>
        <div className="chips">
          {TIMES.map((t) => (
            <button key={t} type="button" role="radio" aria-checked={a.time === t} className={`chip${a.time === t ? ' on' : ''}`} onClick={() => set('time', t)}>
              <SetupIcon name={t} />{copy.times[t][0]}
            </button>
          ))}
        </div>
      </div>
      <div className="hb rise" style={{ animationDelay: '.58s' }} role="group" aria-labelledby="su-h-style">
        <h3><span id="su-h-style">{copy.learnBy}</span><span style={{ color: '#8a8a90' }}>{copy.pickMany}</span></h3>
        <div className="chips">
          {STYLES.map((s) => {
            const on = a.styles.includes(s);
            return (
              <button key={s} type="button" aria-pressed={on} className={`chip${on ? ' on' : ''}`} onClick={() => set('styles', (v) => (on ? v.filter((x) => x !== s) : [...v, s]))}>
                <SetupIcon name={s} />{copy.styles[s]}
              </button>
            );
          })}
        </div>
      </div>
      <div className="hb rise" style={{ animationDelay: '.64s' }}>
        <h3><span>{copy.reminders}</span></h3>
        <div className="rem">
          <SetupIcon name="bell" className={`bell${a.reminders ? ' on' : ''}`} />
          <div className="remtxt" style={{ flex: 1 }}>
            {a.reminders ? copy.remindOn(time[1]!) : copy.remindOff}
            <small>{a.reminders ? copy.remindOnSub : copy.remindOffSub}</small>
          </div>
          <button type="button" role="switch" aria-checked={a.reminders} className={`sw${a.reminders ? ' on' : ''}`} onClick={() => set('reminders', (v) => !v)} aria-label={copy.remindToggle}><i /></button>
        </div>
      </div>
    </div>
  );
}


type Role = 'student' | 'teacher';
type AgeGroup = '18_plus' | 'under_18';
type Gate = { role: Role | null; code: string; age: AgeGroup | null; core: boolean; ai: boolean; phone: string };
type InviteStatus = 'idle' | 'checking' | 'ok' | 'bad';

function RoleStep({ gate, setGate, copy }: { gate: Gate; setGate: (g: Partial<Gate>) => void; copy: Copy['setup'] }) {
  const roles = [['student', 'reading', copy.gate.student], ['teacher', 'university', copy.gate.teacher]] as const;
  return (
    <>
      <p className="sub rise" style={{ animationDelay: '.42s' }}>{copy.gate.subs.role}</p>
      <div className="roles" role="radiogroup" aria-labelledby="su-q">
        {roles.map(([r, icon, [title, hint, tag]], i) => (
          <button
            key={r}
            type="button"
            role="radio"
            aria-checked={gate.role === r}
            className={`role rise${gate.role === r ? ' on' : ''}`}
            style={{ animationDelay: `${0.5 + i * 0.08}s` }}
            onClick={() => setGate({ role: r })}
          >
            <span className="rn" aria-hidden="true">{pad(i + 1)}</span>
            <SetupIcon name={icon} className="big" />
            <span className="rt">{title}</span>
            <span className="rs">{hint}</span>
            <span className="rtag">{tag}</span>
          </button>
        ))}
      </div>
    </>
  );
}

function InviteStep({ code, onCode, status, className, copy }: {
  code: string; onCode: (v: string) => void; status: InviteStatus; className: string | null; copy: Copy['setup'];
}) {
  const g = copy.gate;
  return (
    <>
      <p className="sub rise" style={{ animationDelay: '.42s' }}>{g.subs.invite}</p>
      <div className={`codewrap rise${status === 'ok' ? ' ok' : status === 'bad' ? ' bad' : ''}`} style={{ animationDelay: '.5s' }}>
        <div className="codebox" aria-hidden="true">
          {Array.from({ length: 8 }, (_, i) => {
            const ch = code[i] ?? '';
            return (
              <div key={i} className={`cb${ch ? ' full' : ''}${i === code.length ? ' cur' : ''}`} style={{ '--i': i } as CSSProperties}>
                {ch && <span key={ch}>{ch}</span>}
              </div>
            );
          })}
        </div>
        <input
          className="codein"
          type="text"
          value={code}
          onChange={(e) => onCode(normalizeCode(e.target.value))}
          onPaste={(e) => { e.preventDefault(); onCode(normalizeCode(e.clipboardData.getData('text'))); }}
          aria-label={g.codeLabel}
          aria-invalid={status === 'bad'}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
        />
      </div>
      <div className="codehint" aria-live="polite">
        {status === 'idle' && <span>{g.codeIdle[0]}<b>{g.codeIdle[1]}</b>{g.codeIdle[2]}</span>}
        {status === 'checking' && <span className="scan"><i /><i /><i />{g.codeChecking}</span>}
        {status === 'bad' && <span className="bad">{g.codeBad}</span>}
      </div>
      {status === 'ok' && className && (
        <div className="ticket">
          <div className="tk-main"><small>{g.joining}</small><strong>{className}</strong></div>
          <div className="tk-stub"><span className="okstamp">{g.valid}</span></div>
        </div>
      )}
    </>
  );
}

function ConsentStep({ gate, setGate, t }: { gate: Gate; setGate: (g: Partial<Gate>) => void; t: Copy }) {
  const g = t.setup.gate;
  const ages = [['18_plus', t.join.adult], ['under_18', t.join.minor]] as const;
  const checks = [['core', g.coreTitle, g.required, t.consent.terms], ['ai', g.aiTitle, g.optional, t.consent.ai]] as const;
  return (
    <>
      <div className="lab rise" id="su-age" style={{ animationDelay: '.42s', marginTop: 4 }}>{g.ageLabel}</div>
      <div className="agerow rise" role="radiogroup" aria-labelledby="su-age" style={{ animationDelay: '.48s' }}>
        {ages.map(([v, label]) => (
          <button key={v} type="button" role="radio" aria-checked={gate.age === v} className={`opt${gate.age === v ? ' on' : ''}`} onClick={() => setGate({ age: v })}>
            <span className="radio" aria-hidden="true" />{label}
          </button>
        ))}
      </div>
      {gate.age === 'under_18' && <p className="err" role="alert" style={{ margin: '0 0 18px' }}>{t.join.minorBlocked}</p>}
      <div className="rise" style={{ animationDelay: '.56s' }}>
        {checks.map(([k, title, tag, body]) => (
          <button key={k} type="button" role="checkbox" aria-checked={gate[k]} className={`chk${gate[k] ? ' on' : ''}`} onClick={() => setGate({ [k]: !gate[k] })}>
            <span className="box" aria-hidden="true"><SetupIcon name="check" /></span>
            <span><b>{title}<em>{tag}</em></b><span>{body}</span></span>
          </button>
        ))}
      </div>
    </>
  );
}

function PhoneStep({ gate, setGate, copy, onEnter }: { gate: Gate; setGate: (g: Partial<Gate>) => void; copy: Copy['setup']; onEnter: () => void }) {
  const g = copy.gate;
  return (
    <>
      <p className="sub rise" style={{ animationDelay: '.42s' }}>{g.subs.phone}</p>
      <div className="rise" style={{ animationDelay: '.5s' }}>
        <label htmlFor="su-phone" className="lab" style={{ display: 'block', marginTop: 0 }}>{g.phoneLabel}</label>
        <input
          id="su-phone"
          className="nm"
          type="tel"
          value={gate.phone}
          onChange={(e) => setGate({ phone: e.target.value })}
          onKeyDown={(e) => { if (e.key === 'Enter') onEnter(); }}
          placeholder={g.phonePh}
          maxLength={20}
          autoComplete="tel"
        />
        <p className="hint">{g.phoneHint}</p>
      </div>
    </>
  );
}

/** Where a Google teacher lands: the account exists but waits for an admin. */
function PendingScreen({ copy }: { copy: Copy['setup'] }) {
  const g = copy.gate;
  const headRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => headRef.current?.focus(), []);
  return (
    <div className="su">
      <header className="hd"><div className="logo"><i />PANXG</div></header>
      <main className="pend">
        <div className="eyebrow rise" style={{ animationDelay: '.2s' }}>{g.pendingEye}</div>
        <h1 ref={headRef} tabIndex={-1} className="big1 kin" aria-label={g.pendingTitle.join(' ')}>
          {g.pendingTitle.map((w, i) => (
            <span key={i} className="wd" aria-hidden="true" style={{ animationDelay: `${(0.25 + i * 0.08).toFixed(2)}s` }}>{i === 1 ? <em>{w}</em> : w}</span>
          ))}
        </h1>
        <ol className="tline">
          {g.timeline.map(([title, body], i) => (
            <li key={title} className={`tn rise${i === 0 ? ' done' : i === 1 ? ' live' : ''}`} style={{ animationDelay: `${0.55 + i * 0.1}s` }}>
              <div className="tdot">{i === 0 ? <SetupIcon name="check" /> : i + 1}</div>
              <div><b>{title}</b><span>{body}</span></div>
            </li>
          ))}
        </ol>
        <Link className="btn-back rise" style={{ animationDelay: '.9s', display: 'inline-flex', textDecoration: 'none' }} to="/login">
          <SetupIcon name="back" />{g.toLogin}
        </Link>
      </main>
    </div>
  );
}

/**
 * One flow for every first run. A signed-in account (email sign-up) answers the profile steps;
 * a Google sign-up without an account first picks a role, enters an invite code and agrees to the
 * terms, and the account is only created when the last step is sent.
 */
function SetupFlow({ me, pending }: { me: Me | null; pending: Pending | null }) {
  const { t, lang } = useI18n();
  const copy = t.setup;
  const navigate = useNavigate();
  const client = useQueryClient();
  const signup = !me && !!pending;

  const [gate, setGateState] = useState<Gate>(() => ({
    role: pending?.inviteCode ? 'student' : null,
    code: normalizeCode(pending?.inviteCode ?? ''),
    age: null,
    core: false,
    ai: false,
    phone: '',
  }));
  const setGate = (g: Partial<Gate>) => setGateState((s) => ({ ...s, ...g }));
  const role: Role = me ? (me.role === 'teacher' ? 'teacher' : 'student') : gate.role ?? 'student';
  const teacher = role === 'teacher';

  const [answers, setAnswers] = useState(() => initialAnswers(me ?? { displayName: pending?.name ?? '', role: 'student' }));
  const [step, setStep] = useState(0);
  const [dir, setDir] = useState<Dir>('fwd');
  const [done, setDone] = useState<'welcome' | 'pending' | null>(null);
  const startPhoto = me?.avatarUrl ?? pending?.avatarUrl ?? null;
  const [photo, setPhoto] = useState<string | null>(startPhoto);
  const [photoError, setPhotoError] = useState(false);
  const photoFile = useRef<File | null>(null);
  const created = useRef(false);
  const moved = useRef(false);

  const set = useRef<Set>((key, value) =>
    setAnswers((s) => ({ ...s, [key]: typeof value === 'function' ? (value as (v: unknown) => unknown)(s[key]) : value })),
  ).current;

  const keys: StepKey[] = signup
    ? teacher ? ['role', 'phone'] : ['role', 'invite', 'consent', ...PROFILE_STEPS]
    : teacher ? ['name'] : [...PROFILE_STEPS];
  const total = keys.length;
  const key = keys[Math.min(step, total - 1)] ?? 'name';
  const profileStep = (PROFILE_STEPS as readonly string[]).indexOf(key);

  const fullCode = gate.code.length === 8;
  const invite = useQuery({
    queryKey: ['invite-preview', gate.code],
    queryFn: () => api.get<{ className: string }>(`/api/invites/${encodeURIComponent(gate.code)}`),
    enabled: signup && fullCode,
    retry: false,
    staleTime: 60_000,
  });
  const inviteStatus: InviteStatus = !fullCode ? 'idle' : invite.isPending ? 'checking' : invite.data ? 'ok' : 'bad';

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const jpeg = await toJpeg(file);
      setPhoto(URL.createObjectURL(jpeg));
      const out = new File([jpeg], 'photo.jpg', { type: 'image/jpeg' });
      // No account yet during a Google sign-up: keep the file and send it once the account exists.
      if (signup) {
        photoFile.current = out;
        return null;
      }
      return api.upload<Me>('/api/me/photo', out);
    },
    onMutate: () => setPhotoError(false),
    onSuccess: (data) => { if (data) client.setQueryData(['me'], data); },
    onError: () => {
      setPhotoError(true);
      setPhoto(startPhoto);
    },
  });

  const save = useMutation({
    mutationFn: async (): Promise<Me | null> => {
      if (signup && pending && !created.current) {
        const common = { ticket: pending.ticket, role, phone: gate.phone.trim() || null };
        await api.post('/api/onboarding', teacher
          ? { ...common, displayName: pending.name }
          : {
              ...common,
              displayName: answers.name.trim(),
              inviteCode: gate.code,
              ageGroup: gate.age,
              consents: { core_learning: gate.core, ai_processing: gate.ai },
            });
        // The ticket is spent: a retry after a later failure must not send it again.
        created.current = true;
        clearPending();
        if (teacher) return null;
        if (photoFile.current) await api.upload<Me>('/api/me/photo', photoFile.current).catch(() => undefined);
      }
      return api.post<Me>('/api/me/setup', setupBody(answers, teacher));
    },
    onSuccess: (data) => {
      if (data) client.setQueryData(['me'], data);
      setDone(data ? 'welcome' : 'pending');
    },
  });

  // Move focus to the new question so keyboard and screen reader users land on it.
  useEffect(() => {
    if (!moved.current) return;
    document.getElementById('su-q')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [step]);

  const go = (d: number) => {
    if (d > 0 && step === total - 1) {
      save.mutate();
      return;
    }
    moved.current = true;
    setDir(d > 0 ? 'fwd' : 'back');
    setStep((s) => Math.max(0, Math.min(total - 1, s + d)));
  };

  const a = answers;
  const name = a.name.trim();
  const blocked = {
    role: !gate.role,
    invite: inviteStatus !== 'ok',
    consent: gate.age !== '18_plus' || !gate.core,
    phone: false,
    name: !name,
    occupation: !a.occupation,
    source: !a.source,
    target: false,
    level: false,
    skills: false,
    habits: false,
  }[key];

  const firstName = (pending?.name ?? '').trim().split(/\s+/).pop() || (lang === 'vi' ? 'bạn' : 'there');
  const q = copy.questions;
  const QUESTIONS: Record<StepKey, string> = {
    role: copy.gate.questions.role(firstName),
    invite: copy.gate.questions.invite,
    consent: copy.gate.questions.consent,
    phone: copy.gate.questions.phone,
    name: teacher ? q.teacherName : q.name,
    occupation: q.occupation,
    source: q.referral,
    target: q.target,
    level: q.level,
    skills: q.skills,
    habits: q.habits,
  };
  const label = profileStep >= 0 ? copy.steps[profileStep] ?? '' : copy.gate.steps[key as GateStep];
  const eyebrow = teacher && !signup ? copy.teacherEyebrow : copy.eyebrow(pad(step + 1), pad(total), label);

  const props: StepProps = { a, set, copy };
  const body: Record<StepKey, ReactNode> = {
    role: <RoleStep gate={gate} setGate={setGate} copy={copy} />,
    invite: <InviteStep code={gate.code} onCode={(code) => setGate({ code })} status={inviteStatus} className={invite.data?.className ?? null} copy={copy} />,
    consent: <ConsentStep gate={gate} setGate={setGate} t={t} />,
    phone: <PhoneStep gate={gate} setGate={setGate} copy={copy} onEnter={() => go(1)} />,
    name: (
      <NameAge
        {...props}
        teacher={teacher}
        photo={photo}
        photoBusy={upload.isPending}
        photoError={photoError}
        onEnter={() => go(1)}
        onPhoto={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file && file.type.startsWith('image/')) upload.mutate(file);
        }}
      />
    ),
    occupation: <Occupation {...props} />,
    source: <Source {...props} />,
    target: <Target {...props} />,
    level: <Level {...props} />,
    skills: <Skills {...props} />,
    habits: <Habits {...props} />,
  };

  if (done === 'pending') return <PendingScreen copy={copy} />;
  if (done === 'welcome') {
    const band = bandAt(a.bandIdx);
    const chips = teacher
      ? copy.welcome.teacherChips.map((v, i) => ({ k: pad(i + 1), v }))
      : [
          { k: copy.welcome.chips.target, v: band.toFixed(1) },
          { k: copy.welcome.chips.level, v: levelAt(a.levelIdx) },
          { k: copy.welcome.chips.daily, v: copy.minutes(minutesLabel(minutesAt(a.minutesIdx))) },
          { k: copy.welcome.chips.strongest, v: (a.skills[0] ?? 'reading').replace(/^./, (c) => c.toUpperCase()) },
        ];
    // A Google sign-up holds the new session cookie but no cached data yet, so load home fresh.
    const leave = () => (signup ? window.location.assign(homeFor(role)) : navigate(homeFor(role), { replace: true }));
    return (
      <div className="su">
        <Welcome copy={copy.welcome} teacher={teacher} name={name} photo={photo} chips={chips} onDone={leave} />
      </div>
    );
  }

  const last = step === total - 1;
  const nextLabel = save.isPending ? copy.saving : last ? (signup && teacher ? copy.gate.send : copy.finish) : copy.next;
  return (
    <div className="su">
      <header className="hd">
        <div className="logo"><i />PANXG</div>
        <div className="segs" role="progressbar" aria-valuemin={1} aria-valuemax={total} aria-valuenow={step + 1} aria-label={eyebrow}>
          {Array.from({ length: total }, (_, i) => (
            <div key={i} className="seg"><div className="segf" style={{ transform: `scaleX(${i <= step ? 1 : 0})` }} /></div>
          ))}
        </div>
        <div className="count" aria-hidden="true">{pad(step + 1)} / {pad(total)}</div>
      </header>

      <div className="body">
        <main className="left">
          <div key={key} className={`stage ${dir}`}>
            <div className="ghost" aria-hidden="true">
              <div className="odo"><span>{pad(dir === 'fwd' ? step : step + 2)}</span><span>{pad(step + 1)}</span></div>
            </div>
            <div className="eyebrow rise" style={{ animationDelay: '.3s' }}>{eyebrow}</div>
            <Kinetic id="su-q" text={QUESTIONS[key]} small={key === 'habits' || key === 'consent'} />
            {body[key]}
            {last && <div style={{ marginTop: 16 }}><ErrorAlert error={save.error} /></div>}
          </div>
        </main>
        <ReportCard
          copy={copy}
          answers={a}
          step={profileStep}
          teacher={teacher}
          userId={me?.id ?? pending?.email ?? ''}
          photo={photo}
          klass={signup ? { name: invite.data?.className ?? null, now: key === 'invite' } : undefined}
        />
      </div>

      <footer className="ft">
        <div>
          {step > 0 && (
            <button type="button" className="btn-back" onClick={() => go(-1)} disabled={save.isPending}><SetupIcon name="back" />{copy.back}</button>
          )}
        </div>
        <div className="nextwrap">
          {key === 'name' && <span className="kbd">{copy.enterHint}</span>}
          <button type="button" className="btn-next" onClick={() => go(1)} disabled={blocked || save.isPending || upload.isPending}>
            {nextLabel}<SetupIcon name="next" />
          </button>
        </div>
      </footer>
    </div>
  );
}

/**
 * First run for a new account. A signed-in account that already finished it goes home; with no
 * session, a Google sign-up waiting in this tab runs the full flow, and anyone else goes to sign in.
 */
export function SetupPage() {
  const me = useMe();
  if (me.isPending) return <PageLoader bare />;
  if (me.isError) return <main className="page-error"><ErrorAlert error={me.error} /></main>;
  return <SetupGate me={me.data ?? null} />;
}

/** Decides once, so the welcome screen stays up after saving even though the account changed underneath. */
function SetupGate({ me }: { me: Me | null }) {
  const [start] = useState(() => ({ me, fresh: me?.onboardedAt == null, pending: me ? null : loadPending() }));
  if (!start.me) return start.pending ? <SetupFlow me={null} pending={start.pending} /> : <Navigate to="/login" replace />;
  if (start.me.role === 'admin' || !start.fresh) return <Navigate to={homeFor(start.me.role)} replace />;
  return <SetupFlow me={start.me} pending={null} />;
}
