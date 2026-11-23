import { api } from './api';

export type TestType = 'ielts_standard' | 'course';
export type SourceType = 'self_authored' | 'licensed' | 'commercial' | 'other';
export type TestStatus = 'draft' | 'published' | 'archived';

export const QUESTION_TYPES = [
  'matching_features',
  'matching_headings',
  'matching_information',
  'sentence_endings',
  'multiple_choice',
  'tfng',
  'ynng',
  'table_completion',
  'note_completion',
  'summary_completion',
  'sentence_completion',
  'short_answer',
  'diagram_label',
] as const;
export type QuestionType = (typeof QUESTION_TYPES)[number];
export type QuestionGroup = 'matching' | 'completion' | 'multiple_choice' | 'tfng';

/** Mirrors the server's grouping. Only completion types carry a word limit. */
export const GROUP_OF: Record<QuestionType, QuestionGroup> = {
  matching_features: 'matching',
  matching_headings: 'matching',
  matching_information: 'matching',
  sentence_endings: 'matching',
  multiple_choice: 'multiple_choice',
  tfng: 'tfng',
  ynng: 'tfng',
  table_completion: 'completion',
  note_completion: 'completion',
  summary_completion: 'completion',
  sentence_completion: 'completion',
  short_answer: 'completion',
  diagram_label: 'completion',
};

export type TestSummary = {
  id: string;
  title: string;
  testType: TestType;
  sourceType: SourceType;
  status: TestStatus;
  durationMinutes: number;
  publishedAt: string | null;
  createdAt: string;
};

export type JobState = { state: 'queued' | 'running' | 'done' | 'failed'; errorCode: string | null };
export type SectionQuestion = { number: number; questionType: QuestionType; wordLimit: number | null };
export type SectionRange = { from: number; to: number; questionType: QuestionType; wordLimit: number | null };
export type Section = {
  id: string;
  position: number;
  title: string;
  suggestedMinutes: number | null;
  passagePages: number[];
  questionPages: number[];
  unitsPrimary: string[];
  unitsSecondary: string[];
  questions: SectionQuestion[];
  ranges: SectionRange[];
};
export type TestDetail = TestSummary & {
  studentPdf: { pageCount: number; hasTextLayer: boolean | null } | null;
  sections: Section[];
  ocr?: JobState | null;
};

export type KeyItem = {
  number: number;
  answers: string[];
  origin: 'manual' | 'text_layer' | 'scan';
  flagged: boolean;
  reviewed: boolean;
  sourcePage: number | null;
  /** [x0, y0, x1, y1] as fractions of the displayed page, origin top-left. */
  sourceBbox: number[] | null;
};
export type KeySourceInfo = { kind: 'key_source'; pageCount: number; hasTextLayer: boolean };
/** `revision` identifies the stored file. It is opaque and unrelated to where the file is kept. */
export type KeySource = KeySourceInfo & { revision: string };
export type KeyData = {
  version: number | null;
  approvedAt: string | null;
  items: KeyItem[];
  scan: JobState | null;
  source: KeySource | null;
};
/** Save responses carry no file identity, so they must never replace the data from `getKey`. */
export type SavedKey = Pick<KeyData, 'version' | 'approvedAt' | 'items'>;
export type ImportCounts = { filled: number; flagged: number; kept: number; unmatched: number; conflicts: number[] };

export type StructureSection = {
  title: string;
  suggestedMinutes: number | null;
  passagePages: number[];
  questionPages: number[];
  unitsPrimary: string[];
  unitsSecondary: string[];
  ranges: { from: number; to: number; questionType: QuestionType; wordLimit?: number }[];
};

export type CreateTest = { title: string; sourceType: SourceType; testType: TestType; durationMinutes: number };

const base = (id: string) => `/api/tests/${id}`;

export const testsApi = {
  list: () => api.get<TestSummary[]>('/api/tests'),
  create: (body: CreateTest) => api.post<TestSummary>('/api/tests', body),
  get: (id: string) => api.get<TestDetail>(base(id)),
  update: (id: string, body: Partial<CreateTest>) => api.patch<TestSummary>(base(id), body),
  putStructure: (id: string, sections: StructureSection[]) => api.put<TestDetail>(`${base(id)}/structure`, { sections }),
  readiness: (id: string) => api.get<{ ready: boolean; blockers: string[] }>(`${base(id)}/readiness`),
  uploadPdf: (id: string, file: File) => api.upload<{ pageCount: number; hasTextLayer: boolean }>(`${base(id)}/pdf`, file),
  pdfUrl: (id: string) => api.get<{ url: string; expiresIn: number }>(`${base(id)}/pdf-url`),
  publish: (id: string) => api.post<TestSummary>(`${base(id)}/publish`),
  archive: (id: string) => api.post<TestSummary>(`${base(id)}/archive`),
  // Answer routes. Only the key editor may call these: preview must never import them.
  getKey: (id: string) => api.get<KeyData>(`${base(id)}/key`),
  putKeyItems: (id: string, body: { items: { number: number; answers: string[] }[] } | { pasted: string }) =>
    api.put<SavedKey>(`${base(id)}/key/items`, body),
  /** Sends the answers on screen: the server refuses the review if the stored ones differ. */
  reviewKey: (id: string, items: { number: number; answers: string[] }[]) => api.post<SavedKey>(`${base(id)}/key/review`, { items }),
  keySourceUrl: (id: string, revision: string) =>
    api.get<{ url: string; expiresIn: number }>(`${base(id)}/key-source-url?revision=${encodeURIComponent(revision)}`),
  uploadKeySource: (id: string, file: File) => api.upload<KeySourceInfo>(`${base(id)}/key-source`, file),
  importKey: (id: string) => api.post<ImportCounts>(`${base(id)}/key/import`),
  scanKey: (id: string) => api.post<JobState>(`${base(id)}/key/scan`),
};
