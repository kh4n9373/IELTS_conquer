import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { en } from '../locales/en';
import { vi, type Copy } from '../locales/vi';

export type Lang = 'vi' | 'en';

const STORAGE_KEY = 'panxg-lang';
const COPY: Record<Lang, Copy> = { vi, en };
const DATE_LOCALE: Record<Lang, string> = { vi: 'vi-VN', en: 'en-GB' };

function savedLang(): Lang {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'en' ? 'en' : 'vi';
  } catch {
    return 'vi';
  }
}

type I18n = { lang: Lang; setLang: (lang: Lang) => void; t: Copy; formatDate: (iso: string) => string };

const I18nContext = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(savedLang);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.querySelector('meta[name="description"]')?.setAttribute('content', COPY[lang].meta.description);
  }, [lang]);

  const value = useMemo<I18n>(() => {
    const dates = new Intl.DateTimeFormat(DATE_LOCALE[lang], { dateStyle: 'medium', timeZone: 'Asia/Ho_Chi_Minh' });
    return {
      lang,
      t: COPY[lang],
      formatDate: (iso) => dates.format(new Date(iso)),
      setLang: (next) => {
        setLangState(next);
        try {
          window.localStorage.setItem(STORAGE_KEY, next);
        } catch {
          // Private browsing can block storage. The choice still applies to this visit.
        }
      },
    };
  }, [lang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const value = useContext(I18nContext);
  if (!value) throw new Error('useI18n must be used inside I18nProvider');
  return value;
}
