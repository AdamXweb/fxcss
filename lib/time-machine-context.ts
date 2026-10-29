'use client';

import { createContext } from 'react';
import type { RetroDestination } from './time-machine';

export const TimeMachineContext = createContext<{
  destination: RetroDestination | null;
  minimised: boolean;
  panelId: string;
  minimise: () => void;
  restore: () => void;
  close: () => void;
} | null>(null);
