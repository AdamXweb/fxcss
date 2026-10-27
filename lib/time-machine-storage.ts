import { storedRetroDestination, type RetroDestination } from './time-machine';

// This tab-only preference is listed on /open. No search text is stored.
export function readTimeMachineDestination(): RetroDestination | null {
  try {
    return storedRetroDestination(window.sessionStorage.getItem('fxcss-time-machine'));
  } catch {
    return null;
  }
}

export function saveTimeMachineDestination(destination: RetroDestination | null) {
  try {
    if (destination) window.sessionStorage.setItem('fxcss-time-machine', destination);
    else window.sessionStorage.removeItem('fxcss-time-machine');
  } catch {
    // Theme switching and returning to the present work without browser storage.
  }
}
