export type RetroDestination = 'windows' | 'mac' | 'linux';

export const retroDestinations = {
  windows: {
    label: 'Windows', year: '1999', browser: 'The pre-Firefox web',
    description: 'The original Windows desktop detour. Phoenix and Firefox were still in the future.',
    source: 'https://blog.mozilla.org/community/2013/05/13/milestone-phoenix-0-1-released-first-version-of-firefox/',
  },
  mac: {
    label: 'Mac OS X', year: '2003', browser: 'Firebird 0.6',
    description: 'Firebird 0.6 brought the Firefox family to Mac OS X in 2003.',
    source: 'https://website-archive.mozilla.org/www.mozilla.org/firefox_releasenotes/en-us/firefox/releases/0.6',
  },
  linux: {
    label: 'Linux', year: '2002', browser: 'Phoenix 0.1',
    description: 'Phoenix 0.1 arrived on Linux in 2002, at the start of the Firefox family.',
    source: 'https://website-archive.mozilla.org/www.mozilla.org/firefox_releasenotes/en-us/firefox/releases/0.1',
  },
} as const;

export function retroDestinationForQuery(query: string): RetroDestination | null {
  switch (query.trim().toLowerCase()) {
    case '1999': return 'windows';
    case 'firebird':
    case '2003': return 'mac';
    case 'phoenix':
    case '2002': return 'linux';
    default: return null;
  }
}

export function storedRetroDestination(value: string | null): RetroDestination | null {
  if (value === 'windows' || value === 'mac' || value === 'linux') return value;
  // Preserve a trip started with the original 1999-only implementation.
  return value === '1999' ? 'windows' : null;
}
