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
  recipientCount: number;
  status: AssignmentStatus;
};
export type RecipientState = 'not_started' | 'in_progress' | 'submitted';
export type Recipient = { id: string; name: string; state: RecipientState; attempts: number; lateCount: number };
export type AssignmentDetail = Assignment & { students: Recipient[] };

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
  durationMinutes: number;
  pageCount: number | null;
  questionCount: number;
  mode: AssignmentMode;
  status: AttemptStatus;
  deadlineAt: string;
  serverNow: string;
  sections: { position: number; title: string; passagePages: number[]; questionPages: number[]; numbers: number[] }[];
  questions: { number: number; type: QuestionType; wordLimit: number | null; group: number | null }[];
  answers: { number: number; value: string; marked: boolean; seq: number }[];
};
export type AnswerItem = { number: number; value: string; markedForReview: boolean; seq: number };
export type SaveResult = { accepted: { number: number; seq: number }[]; late: number[]; serverNow: string };

export type ResultPending = { released: false; status: AttemptStatus; expectedAt: string };
export type ResultReleased = {
  released: true;
  status: AttemptStatus;
  attemptNumber: number;
  testTitle: string;
  /** ISO time the attempt was handed in. */
  submittedAt: string;
  correct: number;
  total: number;
  percent: number;
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

const a = '/api/assignments';
const at = (id: string) => `/api/attempts/${id}`;

export const examApi = {
  // Teacher
  listAssignments: () => api.get<Assignment[]>(a),
  createAssignment: (body: CreateAssignment) => api.post<Assignment>(a, body),
  getAssignment: (id: string) => api.get<AssignmentDetail>(`${a}/${id}`),
  updateAssignment: (id: string, body: PatchAssignment) => api.patch<Assignment>(`${a}/${id}`, body),
  cancelAssignment: (id: string) => api.post(`${a}/${id}/cancel`),
  // Student
  myAssignments: () => api.get<MyAssignment[]>('/api/my/assignments'),
  startAttempt: (assignmentId: string, device: string) =>
    api.post<{ attemptId: string }>(`/api/my/assignments/${assignmentId}/attempts`, { device }),
  claim: (attemptId: string) => api.post<{ writerToken: string }>(`${at(attemptId)}/claim`),
  room: (attemptId: string) => api.get<ExamRoom>(at(attemptId)),
  pdfUrl: (attemptId: string) => api.get<{ url: string; expiresIn: number }>(`${at(attemptId)}/pdf-url`),
  saveAnswers: (attemptId: string, writerToken: string, items: AnswerItem[]) =>
    api.put<SaveResult>(`${at(attemptId)}/answers`, { writerToken, items }),
  submit: (attemptId: string) => api.post<{ status: AttemptStatus; finalizedAt: string | null }>(`${at(attemptId)}/submit`),
  result: (attemptId: string) => api.get<ResultView>(`${at(attemptId)}/result`),
};

/** Best-effort device class for the attempt record. */
export function deviceKind(): 'desktop' | 'tablet' | 'mobile' {
  const w = window.innerWidth;
  return w < 640 ? 'mobile' : w < 1024 ? 'tablet' : 'desktop';
}
