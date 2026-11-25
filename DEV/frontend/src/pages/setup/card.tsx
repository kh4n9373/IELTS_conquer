import type { Copy } from '../../locales/vi';
import { type Answers, bandAt, levelAt, minutesLabel, minutesAt, pad, skillBand } from './model';

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

function today() {
  const d = new Date();
  return `${pad(d.getDate())}/${MONTHS[d.getMonth()]}/${d.getFullYear()}`;
}

function learnerNo(id: string, teacher: boolean) {
  let h = 7;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) % 1_000_000;
  return (teacher ? 'TC' : 'PX') + String(h).padStart(6, '0');
}

/** A field that inks itself in the first time it gets a value; `now` marks the one being answered. */
function Box({ value, now, kind = 'fbox', hot }: { value: string | null; now: boolean; kind?: 'fbox' | 'sbox'; hot?: boolean }) {
  return (
    <div className={`${kind}${hot ? ' hot' : ''}${now ? ' now' : ''}`}>
      {value && <span key={value} className="fv">{value}</span>}
    </div>
  );
}

type Props = {
  copy: Copy['setup'];
  answers: Answers;
  step: number;
  teacher: boolean;
  userId: string;
  photo: string | null;
  /** Sign-up only: the class the invite code points at, filled on the invite step. */
  klass?: { name: string | null; now: boolean };
};

/** The goal profile on the right. It fills in as each step is answered; it is not a test result. */
export function ReportCard({ copy, answers: a, step, teacher, userId, photo, klass }: Props) {
  const c = copy.card;
  const name = a.name.trim();
  const initial = name.split(/\s+/).pop()?.charAt(0).toUpperCase() || '?';
  const band = bandAt(a.bandIdx);
  const reached = (i: number) => teacher || step >= i;
  const at = (i: number) => !teacher && step === i;
  const time = copy.times[a.time];
  const styles = a.styles.map((s) => copy.styles[s]).join(', ') || c.notPicked;
  const notes = teacher
    ? c.teacherNotes
    : [c.plan(minutesLabel(minutesAt(a.minutesIdx)), time[0]!.toLowerCase()), c.likes(styles), a.reminders ? c.remindAt(time[1]!) : copy.remindOff];

  return (
    <aside className="cardwrap" aria-label={c.label}>
      <div className="trf">
        <div className="trf-h">
          <div>
            <div className="trf-logo">PAN<span>X</span>G</div>
            <div className="trf-title">{teacher ? c.teacherTitle : c.learnerTitle}</div>
          </div>
          <div className="trf-mod">{teacher ? c.teacherMod : c.learnerMod}</div>
        </div>
        <div className="trf-note"><b>NOTE</b><span>{teacher ? c.teacherNote : c.learnerNote}</span></div>
        <div className="trf-row">
          <span className="trf-l">Date</span><div className="fbox w" style={{ width: 108 }}>{today()}</div>
          <span className="trf-l" style={{ marginLeft: 'auto' }}>{teacher ? c.teacherNo : c.learnerNo}</span>
          <div className="fbox w" style={{ width: 92 }}>{learnerNo(userId, teacher)}</div>
        </div>
        {klass && !teacher && (
          <div className="trf-row">
            <span className="trf-l">{copy.gate.classLabel}</span>
            <div className={`fbox w${klass.now ? ' now' : ''}`} style={{ flex: 1 }}>
              {klass.name && <span key={klass.name} className="fv">{klass.name.toUpperCase()}</span>}
            </div>
          </div>
        )}
        <div className="trf-sec">{teacher ? c.teacherDetails : c.learnerDetails}</div>
        <div className="trf-det">
          <div className="trf-fields">
            <span className="trf-l">Name</span><Box value={name ? name.toUpperCase() : null} now={step === 0} />
            <span className="trf-l">Age</span><Box value={step >= 0 ? String(a.age) : null} now={step === 0} />
            <span className="trf-l">{teacher ? c.role : c.occupation}</span>
            <Box value={teacher ? c.teacherRole : reached(1) && a.occupation ? copy.occupations[a.occupation][0]!.toUpperCase() : null} now={at(1)} />
            <span className="trf-l">{teacher ? c.joined : c.foundVia}</span>
            <Box value={teacher ? today() : reached(2) && a.source ? copy.sources[a.source].toUpperCase() : null} now={at(2)} />
          </div>
          <div className={`trf-photo${step === 0 ? ' now' : ''}`}>
            {photo ? <img key={photo} className="shot" src={photo} alt="" referrerPolicy="no-referrer" /> : <span className="trf-ini">{initial}</span>}
          </div>
        </div>
        {!teacher && (
          <>
            <div className="trf-sec">{c.targets} <small>{c.targetsSub}</small></div>
            <div className="trf-res">
              {(['listening', 'reading', 'writing', 'speaking'] as const).map((s) => (
                <div className="rc" key={s}>
                  <span className="trf-l">{s.charAt(0).toUpperCase() + s.slice(1)}</span>
                  <Box kind="sbox" value={reached(5) ? skillBand(band, a.skills, s) : null} now={at(5)} />
                </div>
              ))}
              <div className="rc"><span className="trf-l">Overall<br />Band</span><Box kind="sbox" hot value={reached(3) ? band.toFixed(1) : null} now={at(3)} /></div>
              <div className="rc"><span className="trf-l">CEFR</span><Box kind="sbox" value={reached(4) ? levelAt(a.levelIdx) : null} now={at(4)} /></div>
            </div>
          </>
        )}
        <div className="trf-bot">
          <div className={`trf-com${at(6) ? ' now' : ''}`}>
            <div className="trf-ct">{teacher ? c.notesTitle : c.planTitle}</div>
            {reached(6) && <div className="fv">{notes.map((n) => <div key={n}>{n}</div>)}</div>}
          </div>
          <div className="trf-stampbox">
            <span>{c.stamp}</span>
            {(teacher ? !!name : step >= 3) && (
              <div className="pstamp"><strong>PANXG</strong><small>{teacher ? copy.card.teacherMod : c.targetSet}</small></div>
            )}
          </div>
        </div>
        <div className="trf-foot"><span>{teacher ? c.teacherFoot : c.learnerFoot}</span><div className="bars" aria-hidden="true" /></div>
      </div>
    </aside>
  );
}
