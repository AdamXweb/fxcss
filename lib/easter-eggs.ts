import type { RetroDestination } from './time-machine';

export type Secret = 'coffee' | 'retro' | 'battle' | null;

export function searchSecret(query: string): Secret {
  const value = query.trim().toLowerCase();
  if (value === 'c0ffee' || value === '#c0ffee') return 'coffee';
  if (['1999', '2002', '2003', 'firebird', 'phoenix'].includes(value)) return 'retro';
  if (value === '!important') return 'battle';
  return null;
}

export const SECRET_EVENT = 'fxcss-secret';

export function revealSecret(secret: Exclude<Secret, null>, destination?: RetroDestination) {
  window.dispatchEvent(new CustomEvent(SECRET_EVENT, { detail: { secret, destination } }));
}
