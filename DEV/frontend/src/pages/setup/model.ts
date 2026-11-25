import type { Cefr, Me, Occupation, Skill, StudyStyle, StudyTime } from '../../lib/api';

export const BANDS = [5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9];
export const LEVELS: Cefr[] = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'];
export const OCCUPATIONS: Occupation[] = ['school', 'university', 'working', 'other'];
export const SOURCES = ['facebook', 'tiktok', 'instagram', 'youtube', 'friend', 'other'] as const;
export const MINUTES = [10, 20, 30, 45, 60];
export const TIMES: StudyTime[] = ['morning', 'noon', 'evening', 'night'];
export const STYLES: StudyStyle[] = ['video', 'exercise', 'flashcard', 'tutor'];
export const SKILLS: Skill[] = ['reading', 'listening', 'writing', 'speaking'];
export const AGE = { min: 18, studentMax: 60, teacherMax: 70 };

export type Source = (typeof SOURCES)[number];

export type Answers = {
  name: string;
  age: number;
  occupation: Occupation | null;
  source: Source | null;
  bandIdx: number;
  levelIdx: number;
  skills: Skill[];
  minutesIdx: number;
  time: StudyTime;
  styles: StudyStyle[];
  reminders: boolean;
};

/** Index lookups that always land on a real entry (the arrays are fixed and never empty). */
export const bandAt = (i: number) => BANDS[i] ?? 6.5;
export const levelAt = (i: number) => LEVELS[i] ?? 'B1';
export const minutesAt = (i: number) => MINUTES[i] ?? 30;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Starts from whatever the account already holds, so a Google name or an earlier answer carries over. */
export function initialAnswers(me: Partial<Me> & Pick<Me, 'displayName' | 'role'>): Answers {
  const teacher = me.role === 'teacher';
  const band = me.targetBand != null ? BANDS.indexOf(me.targetBand) : -1;
  const level = me.cefrLevel ? LEVELS.indexOf(me.cefrLevel) : -1;
  const minutes = me.studyMinutes != null ? MINUTES.indexOf(me.studyMinutes) : -1;
  const source = SOURCES.find((s) => s === me.referralSource) ?? null;
  return {
    name: me.displayName,
    age: clamp(me.age ?? (teacher ? 28 : 20), AGE.min, teacher ? AGE.teacherMax : AGE.studentMax),
    occupation: me.occupation ?? null,
    source,
    bandIdx: band >= 0 ? band : 3,
    levelIdx: level >= 0 ? level : 2,
    skills: me.skillRank?.length === 4 ? me.skillRank : SKILLS,
    minutesIdx: minutes >= 0 ? minutes : 2,
    time: me.studyTime ?? 'evening',
    styles: me.studyStyles ?? ['exercise', 'flashcard'],
    reminders: me.reminders ?? true,
  };
}

export function setupBody(a: Answers, teacher: boolean) {
  const base = { displayName: a.name.trim(), age: a.age };
  if (teacher) return base;
  return {
    ...base,
    occupation: a.occupation,
    referralSource: a.source,
    targetBand: bandAt(a.bandIdx),
    cefrLevel: levelAt(a.levelIdx),
    skillRank: a.skills,
    studyMinutes: minutesAt(a.minutesIdx),
    studyTime: a.time,
    studyStyles: a.styles,
    reminders: a.reminders,
  };
}

export const PROFILE_STEPS = ['name', 'occupation', 'source', 'target', 'level', 'skills', 'habits'] as const;
export type GateStep = 'role' | 'invite' | 'consent' | 'phone';
export type StepKey = GateStep | (typeof PROFILE_STEPS)[number];

/** Keeps only the 8 code characters, from a bare code or a pasted /join/ link. */
export function normalizeCode(raw: string) {
  const fromLink = /\/join\/([^/?#\s]+)/.exec(raw);
  return (fromLink?.[1] ?? raw).replace(/[^a-z0-9]/gi, '').toUpperCase().slice(0, 8);
}

export const pad = (n: number) => String(n).padStart(2, '0');
export const minutesLabel = (m: number) => (m === 60 ? '60+' : String(m));

/** Per-skill targets around the overall band: strongest a half band up, weakest a half band down. */
export function skillBand(band: number, skills: Skill[], skill: Skill) {
  const offset = [0.5, 0, 0, -0.5][skills.indexOf(skill)] ?? 0;
  return clamp(band + offset, 4, 9).toFixed(1);
}

/** Shrinks and center-crops a picked photo to a 4:5 JPEG small enough for the API. */
export async function toJpeg(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file);
  const W = 480;
  const H = 600;
  const scale = Math.max(W / bitmap.width, H / bitmap.height);
  const sw = W / scale;
  const sh = H / scale;
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('no_canvas');
  ctx.drawImage(bitmap, (bitmap.width - sw) / 2, (bitmap.height - sh) / 2, sw, sh, 0, 0, W, H);
  bitmap.close();
  for (const quality of [0.86, 0.72, 0.6]) {
    const blob = await new Promise<Blob | null>((ok) => canvas.toBlob(ok, 'image/jpeg', quality));
    if (blob && blob.size <= 280 * 1024) return blob;
  }
  throw new Error('too_large');
}
