import type { Copy } from '../locales/vi';
import { ApiError } from './api';

/** Turns an API or network failure into a message in the current language. */
export function errorText(error: unknown, errors: Copy['errors']): string {
  if (error instanceof ApiError) return errors[error.code] ?? errors.fallback;
  return errors.network;
}
