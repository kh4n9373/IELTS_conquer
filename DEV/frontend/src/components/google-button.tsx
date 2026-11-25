import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { api } from '../lib/api';
import { useI18n } from '../lib/i18n';

type Gsi = {
  accounts: {
    id: {
      initialize: (config: { client_id: string; callback: (res: { credential: string }) => void }) => void;
      renderButton: (el: HTMLElement, options: Record<string, unknown>) => void;
    };
  };
};

const SRC = 'https://accounts.google.com/gsi/client';
let loading: Promise<Gsi> | null = null;

function loadGsi(): Promise<Gsi> {
  loading ??= new Promise<Gsi>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = SRC;
    script.async = true;
    script.onload = () => resolve((window as unknown as { google: Gsi }).google);
    script.onerror = () => {
      loading = null;
      reject(new Error('gsi'));
    };
    document.head.append(script);
  });
  return loading;
}

/** Renders Google's own button. Shows nothing when the server has no Google client ID. */
export function GoogleButton({ onCredential }: { onCredential: (credential: string) => void }) {
  const { lang, t } = useI18n();
  const config = useQuery({
    queryKey: ['auth-config'],
    queryFn: () => api.get<{ googleClientId: string | null }>('/api/auth/config'),
    staleTime: Infinity,
  });
  const clientId = config.data?.googleClientId;
  const slot = useRef<HTMLDivElement>(null);
  const callback = useRef(onCredential);
  callback.current = onCredential;

  useEffect(() => {
    if (!clientId) return;
    let live = true;
    loadGsi()
      .then((google) => {
        if (!live || !slot.current) return;
        google.accounts.id.initialize({ client_id: clientId, callback: (res) => callback.current(res.credential) });
        google.accounts.id.renderButton(slot.current, {
          type: 'standard',
          theme: 'outline',
          size: 'large',
          text: 'continue_with',
          shape: 'rectangular',
          width: Math.min(slot.current.clientWidth || 320, 400),
          locale: lang,
        });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [clientId, lang]);

  if (!clientId) return null;
  return (
    <div className="google-block">
      <p className="google-or" aria-hidden="true"><span>{t.auth.or}</span></p>
      <div ref={slot} className="google-slot" />
    </div>
  );
}
