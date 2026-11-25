import { useQuery } from '@tanstack/react-query';
import { api, ApiError, type Me } from '../lib/api';
import { STATIC_PREVIEW } from '../lib/preview';

export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      if (STATIC_PREVIEW) return null;
      try {
        return await api.get<Me>('/api/me');
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
  });
}

export const needsSetup = (me: Me) => me.role !== 'admin' && me.onboardedAt == null;

export const homeFor = (role: Me['role']) => ({ admin: '/admin', teacher: '/classes', student: '/home' })[role];
