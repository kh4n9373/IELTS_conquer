import type { ReactNode } from 'react';

/** Line icons drawn for the setup screens; each is the inside of a 24×24 stroke SVG. */
const PATHS = {
  check: <path d="M5 12l5 5 9-10" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  up: <path d="M6 15l6-6 6 6" />,
  down: <path d="M6 9l6 6 6-6" />,
  next: <path d="M5 12h14M13 6l6 6-6 6" />,
  back: <path d="M19 12H5M11 6l-6 6 6 6" />,
  replay: <><path d="M4 12a8 8 0 1 0 2.5-5.8" /><path d="M4 4v4h4" /></>,
  camera: <><path d="M3 8h4l2-3h6l2 3h4v12H3z" /><circle cx="12" cy="13.5" r="3.5" /></>,
  grip: <path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01" />,
  bell: <><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z" /><path d="M10 20a2 2 0 0 0 4 0" /></>,
  school: <><path d="M6 10a6 6 0 0 1 12 0v10H6z" /><path d="M9 4.5V3h6v1.5" /><path d="M9 14h6v3H9z" /></>,
  university: <><path d="M2 9l10-5 10 5-10 5z" /><path d="M6 11v5c3 2.5 9 2.5 12 0v-5" /><path d="M22 9v6" /></>,
  working: <><rect x="3" y="7" width="18" height="13" rx="1" /><path d="M8 7V4h8v3" /><path d="M3 13h18" /></>,
  spark: <path d="M12 3v5M12 16v5M3 12h5M16 12h5M6 6l2.5 2.5M15.5 15.5L18 18M18 6l-2.5 2.5M8.5 15.5L6 18" />,
  facebook: <><rect x="3" y="3" width="18" height="18" rx="4" /><path d="M15.5 7.5h-2a2.5 2.5 0 0 0-2.5 2.5v11" /><path d="M8.5 13h6" /></>,
  tiktok: <><path d="M14 3v11.5a4 4 0 1 1-4-4" /><path d="M14 3c.6 2.6 2.6 4.3 5.5 4.5" /></>,
  instagram: <><rect x="3" y="3" width="18" height="18" rx="5.5" /><circle cx="12" cy="12" r="4" /><circle cx="17.3" cy="6.7" r=".8" fill="currentColor" /></>,
  youtube: <><rect x="2" y="5" width="20" height="14" rx="4" /><path d="M10 9l5 3-5 3z" fill="currentColor" /></>,
  friend: <><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c.5-3.5 3.2-6 6.5-6s6 2.5 6.5 6" /><circle cx="17" cy="9" r="2.5" /><path d="M16.5 14c2.8.2 4.6 2.2 5 5" /></>,
  dots: <><circle cx="5" cy="12" r="1.6" fill="currentColor" /><circle cx="12" cy="12" r="1.6" fill="currentColor" /><circle cx="19" cy="12" r="1.6" fill="currentColor" /></>,
  reading: <><path d="M3 5h6a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H3z" /><path d="M21 5h-6a3 3 0 0 0-3 3v12a2 2 0 0 1 2-2h7z" /></>,
  listening: <><path d="M4 15v-3a8 8 0 0 1 16 0v3" /><rect x="3" y="14" width="4" height="6" rx="1" /><rect x="17" y="14" width="4" height="6" rx="1" /></>,
  writing: <><path d="M4 20l4-1 11-11-3-3L5 16z" /><path d="M14 7l3 3" /></>,
  speaking: <><path d="M4 5h16v11H9l-5 4z" /><path d="M8 10h8" /></>,
  morning: <path d="M3 18h18M6 18a6 6 0 0 1 12 0M12 4v3M4.5 9.5l2 1.5M19.5 9.5l-2 1.5" />,
  noon: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" /></>,
  evening: <path d="M3 18h18M7 18a5 5 0 0 1 10 0M12 9V6M9 7l3-3 3 3" />,
  night: <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />,
  video: <><rect x="3" y="5" width="14" height="14" rx="1" /><path d="M17 10l4-2v8l-4-2" /></>,
  exercise: <><path d="M9 4h6v3H9zM6 5.5H5v15h14v-15h-1" /><path d="M8.5 13l2.5 2.5 4.5-5" /></>,
  flashcard: <><rect x="7" y="3" width="13" height="16" rx="1" /><path d="M4 7v13a1 1 0 0 0 1 1h11" /></>,
  tutor: <><circle cx="12" cy="8" r="4" /><path d="M4 21c.8-4 4-6.5 8-6.5s7.2 2.5 8 6.5" /></>,
} satisfies Record<string, ReactNode>;

export type SetupIconName = keyof typeof PATHS;

export function SetupIcon({ name, className = '' }: { name: SetupIconName; className?: string }) {
  return (
    <svg className={`ic ${className}`} viewBox="0 0 24 24" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

export const HEART = 'M12 21s-7.5-4.6-9.6-9.3C1 8.4 3 5 6.4 5c2.1 0 3.6 1.2 4.6 2.6l1 1.4 1-1.4C14 6.2 15.5 5 17.6 5 21 5 23 8.4 21.6 11.7 19.5 16.4 12 21 12 21z';
export const STAR = 'M12 2l2.2 7.8L22 12l-7.8 2.2L12 22l-2.2-7.8L2 12l7.8-2.2z';
