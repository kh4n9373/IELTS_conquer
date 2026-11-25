import { api } from './api';
import type { QuestionType } from './tests-api';

export type AssignmentMode = 'exam' | 'practice';
export type ReleasePolicy = 'after_submit' | 'after_last' | 'scheduled';
export type AssignmentStatus = 'upcoming' | 'open' | 'closed' | 'cancelled';
export type AttemptStatus = 'in_progress' | 'submitted' | 'auto_submitted' | string;

/** Teacher view of an assignment. */
export type Assignment = {
  id: string;
  testId: string;
  testTitle: string;
  classId: string;
  className: string;
  opensAt: string;
  dueAt: string;
  maxAttempts: number;
  mode: AssignmentMode;
  releasePolicy: ReleasePolicy;
  releaseAt: string | null;
  cancelledAt: string | null;
  createdAt: string;
  isTrial: boolean;
  recipientCount: number;
  status: AssignmentStatus;
};
export type RecipientState = 'not_started' | 'in_progress' | 'submitted';
export type Recipient = {
  id: string;
  name: string;
  state: RecipientState;
  attempts: number;
  lateCount: number;
  /** Teacher only: what the student typed after time ran out. Never graded. */
  late: { number: number; value: string }[];
};
export type AssignmentDetail = Assignment & { students: Recipient[] };

export type AnswerResult = 'ok' | 'bad' | 'blank';
export type StatsChoice = { value: string; text: string; isKey: boolean; students: string[]; count: number };
export type StatsQuestion = {
  number: number;
  type: QuestionType;
  topic: string;
  prompt: string;
  key: string[];
  percent: number;
  correct: number;
  /** Estimated from the gaps between saves. */
  avgSeconds: number | null;
  flags: number;
  choices: StatsChoice[];
  blank: string[];
  trap: { value: string; count: number } | null;
  discrimination: 'good' | 'flat' | 'reverse' | null;
  explanation: { text: string; source: 'ai' | 'teacher' } | null;
};
export type StatsTopic = { name: string; percent: number; avgSeconds: number | null; questions: number[] };
export type StatsAnswer = { number: number; given: string; result: AnswerResult; flagged: boolean; seconds: number | null; othersMissed: number };
export type StatsStudent = {
  id: string;
  name: string;
  attemptId: string;
  rank: number;
  correct: number;
  total: number;
  percent: number;
  band: string | null;
  seconds: number;
  blank: number;
  trailingBlank: number;
  /** Percent correct per topic, in the order of `topics`. */
  topics: number[];
  mistakes: { shared: number; own: number; blank: number };
  /** [seconds from start, answers saved so far] */
  pace: [number, number][];
  answers: StatsAnswer[];
};
export type StatsSummary = {
  mean: number;
  median: number;
  best: { name: string; correct: number };
  worst: { name: string; correct: number };
  avgSeconds: number;
  blank: number;
  blankStudents: string[];
  passed: number;
  allCorrect: number;
  allWrong: number;
  flagTotal: number;
  flagCorrect: number;
  strongTopic: { name: string; percent: number } | null;
  weakTopic: { name: string; percent: number } | null;
  topTrap: { number: number; value: string; count: number; key: string[] } | null;
  outOfTime: { name: string; blank: number; seconds: number }[];
  trapCount: number;
  bands: { strong: number; weak: number };
};
export type AssignmentStats = {
  graded: number;
  total: number;
  durationSeconds: number;
  summary: StatsSummary | null;
  topics: StatsTopic[];
  questions: StatsQuestion[];
  students: StatsStudent[];
};

export type CreateAssignment = {
  testId: string;
  classId: string;
  /** Leave out to give the whole class. */
  studentIds?: string[];
  opensAt: string;
  dueAt: string;
  maxAttempts: number;
  mode: AssignmentMode;
  releasePolicy?: ReleasePolicy;
  releaseAt?: string | null;
};
export type PatchAssignment = Partial<Omit<CreateAssignment, 'testId' | 'classId' | 'studentIds'>>;

/** Student view: an allowlist, with no test id, key or release setting. */
export type MyAssignment = {
  id: string;
  className: string | null;
  testTitle: string | null;
  opensAt: string;
  dueAt: string;
  maxAttempts: number;
  mode: AssignmentMode;
  attemptsUsed: number;
  status: AssignmentStatus;
  durationMinutes: number | null;
  inProgress: boolean;
  latestAttemptId: string | null;
};

export type ExamRoom = {
  title: string;
  authoring: 'pdf' | 'manual';
  durationMinutes: number;
  pageCount: number | null;
  questionCount: number;
  mode: AssignmentMode;
  status: AttemptStatus;
  deadlineAt: string;
  serverNow: string;
  sections: { position: number; title: string; body: string; passagePages: number[]; questionPages: number[]; numbers: number[] }[];
  questions: { number: number; type: QuestionType; wordLimit: number | null; group: number | null; prompt: string; options: string[] }[];
  answers: { number: number; value: string; marked: boolean; seq: number }[];
};
export type AnswerItem = { number: number; value: string; markedForReview: boolean; seq: number };
export type SaveResult = { accepted: { number: number; seq: number }[]; late: number[]; serverNow: string };

export type ResultPending = { released: false; status: AttemptStatus; expectedAt: string; testTitle: string; submittedAt: string | null };
export type ResultReleased = {
  released: true;
  status: AttemptStatus;
  attemptNumber: number;
  maxAttempts: number;
  testTitle: string;
  /** ISO time the attempt was handed in. */
  submittedAt: string;
  correct: number;
  total: number;
  percent: number;
  isTrial: boolean;
  countsForProgress: boolean;
  /** Present only for tests with a band table. */
  band?: string;
  bandLabel?: 'estimated';
  bandTableVersion?: string;
  sections: { position: number; title: string; correct: number; total: number }[];
  items: { number: number; answer: string | null; correct: boolean; key: string[] }[];
};
/** A teacher can also read a graded attempt that is not yet released to students. */
export type ResultPreview = Omit<ResultReleased, 'released'> & { released: false };
export type ResultView = ResultPending | ResultReleased | ResultPreview;

export type ReviewItem = {
  number: number;
  type: string;
  sectionTitle: string;
  prompt: string;
  options: string[];
  /** Pickable values marked by the server; null for free-text answers. */
  choices: { label: string; text: string; mark: 'key' | 'wrong' | null }[] | null;
  answer: string | null;
  correct: boolean;
  key: string[];
  explanation: { correct: string; chosen: string | null } | null;
  explanationState: 'ready' | 'pending' | 'none';
};
export type ReviewView = ResultPending | { released: true; testTitle: string; items: ReviewItem[] };

const a = '/api/assignments';
const at = (id: string) => `/api/attempts/${id}`;

export const examApi = {
  // Teacher
  listAssignments: () => api.get<Assignment[]>(a),
  createAssignment: (body: CreateAssignment) => api.post<Assignment>(a, body),
  getAssignment: (id: string) => api.get<AssignmentDetail>(`${a}/${id}`),
  updateAssignment: (id: string, body: PatchAssignment) => api.patch<Assignment>(`${a}/${id}`, body),
  cancelAssignment: (id: string) => api.post(`${a}/${id}/cancel`),
  assignmentStats: (id: string) => api.get<AssignmentStats>(`${a}/${id}/stats`),
  // Student
  myAssignments: () => api.get<MyAssignment[]>('/api/my/assignments'),
  startAttempt: (assignmentId: string, device: string) =>
    api.post<{ attemptId: string }>(`/api/my/assignments/${assignmentId}/attempts`, { device }),
  claim: (attemptId: string) => api.post<{ writerToken: string }>(`${at(attemptId)}/claim`),
  room: (attemptId: string) => api.get<ExamRoom>(at(attemptId)),
  pdfUrl: (attemptId: string) => api.get<{ url: string; expiresIn: number }>(`${at(attemptId)}/pdf-url`),
  saveAnswers: (attemptId: string, writerToken: string, items: AnswerItem[]) =>
    api.put<SaveResult>(`${at(attemptId)}/answers`, { writerToken, items }),
  submit: (attemptId: string, writerToken: string) =>
    api.post<{ status: AttemptStatus; finalizedAt: string | null }>(`${at(attemptId)}/submit`, { writerToken }),
  result: (attemptId: string) => api.get<ResultView>(`${at(attemptId)}/result`),
  review: (attemptId: string) => api.get<ReviewView>(`${at(attemptId)}/review`),
};

/** Best-effort device class for the attempt record. */
export function deviceKind(): 'desktop' | 'tablet' | 'mobile' {
  const w = window.innerWidth;
  return w < 640 ? 'mobile' : w < 1024 ? 'tablet' : 'desktop';
}
