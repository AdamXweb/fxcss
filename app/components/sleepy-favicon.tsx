'use client';

import { useEffect } from 'react';
import { watchSleepyFavicon } from '../../lib/sleepy-favicon';

export function SleepyFavicon() {
  useEffect(() => watchSleepyFavicon(document), []);
  return null;
}
