export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    credentials: 'same-origin',
    ...(body === undefined ? {} : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.error ?? 'internal_error');
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T = void>(url: string, body?: unknown) => request<T>('POST', url, body),
  patch: <T = void>(url: string, body: unknown) => request<T>('PATCH', url, body),
  put: <T = void>(url: string, body: unknown) => request<T>('PUT', url, body),
  /** Sends a file as the raw request body, typed by the file itself. */
  upload: async <T>(url: string, file: File): Promise<T> => {
    const res = await fetch(url, { method: 'PUT', credentials: 'same-origin', headers: { 'content-type': file.type }, body: file });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, data?.error ?? 'internal_error');
    return data as T;
  },
  delete: (url: string) => request<void>('DELETE', url),
};

export type Referral = 'facebook' | 'tiktok' | 'instagram' | 'youtube' | 'friend' | 'teacher' | 'search' | 'other';
export type Occupation = 'school' | 'university' | 'working' | 'other';
export type Cefr = 'A1' | 'A2' | 'B1' | 'B2' | 'C1' | 'C2';
export type Skill = 'listening' | 'reading' | 'writing' | 'speaking';
export type StudyTime = 'morning' | 'noon' | 'evening' | 'night';
export type StudyStyle = 'video' | 'exercise' | 'flashcard' | 'tutor';
export type Me = {
  id: string;
  email: string;
  displayName: string;
  role: 'student' | 'teacher' | 'admin';
  avatarUrl: string | null;
  phone: string | null;
  referralSource: Referral | null;
  currentBand: number | null;
  targetBand: number | null;
  signInMethod: 'google' | 'email';
  createdAt: string;
  age: number | null;
  occupation: Occupation | null;
  cefrLevel: Cefr | null;
  skillRank: Skill[] | null;
  studyMinutes: number | null;
  studyTime: StudyTime | null;
  studyStyles: StudyStyle[] | null;
  reminders: boolean | null;
  /** Null until the first-run setup screens are done. */
  onboardedAt: string | null;
};
/** Answer from POST /api/auth/google: signed in, or a ticket to finish onboarding with. */
export type GoogleResult =
  | { next: 'home'; id: string }
  | { next: 'onboarding'; ticket: string; email: string; name: string; avatarUrl: string | null };
export type Teacher = {
  id: string;
  email: string;
  displayName: string;
  status: 'active' | 'locked' | 'pending';
  avatarUrl?: string | null;
  phone?: string | null;
  referralSource?: Referral | null;
  activationSent?: boolean;
};
export type ClassSummary = { id: string; name: string; status: 'active' | 'archived'; createdAt: string };
export type Member = { id: string; email: string; displayName: string; joinedAt: string };
export type Invite = { id: string; code: string; expiresAt: string };
export type ClassDetail = ClassSummary & { members: Member[]; invites: Invite[] };
export type Consents = {
  termsVersion: string;
  current: { core_learning: boolean; ai_processing: boolean };
};
