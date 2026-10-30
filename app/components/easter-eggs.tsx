'use client';

import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from 'react';
import { Coffee, Minus, RotateCcw, X } from 'lucide-react';
import { usePathname } from 'next/navigation';
import { SECRET_EVENT } from '../../lib/easter-eggs';
import { retroDestinations, storedRetroDestination, type RetroDestination } from '../../lib/time-machine';
import { readTimeMachineDestination, saveTimeMachineDestination } from '../../lib/time-machine-storage';
import { TimeMachineContext } from '../../lib/time-machine-context';

const coffeePieces = Array.from({ length: 36 }, (_, index) => ({
  left: `${(index * 37 + 7) % 100}%`,
  delay: `${(index % 9) * 0.11}s`,
  turn: `${index % 2 ? '' : '-'}${150 + (index * 29) % 210}deg`,
  drift: `${(index % 5 - 2) * 42}px`,
  color: ['#c0ffee', '#ff7139', '#7355dd'][index % 3],
  glyph: index % 7 === 0 ? '☕' : index % 5 === 0 ? '●' : '',
}));

const subscribeToSavedTrip = () => () => {};
const readSavedTrip = (): RetroDestination | 'present' => readTimeMachineDestination() || 'present';
const serverTrip = (): 'loading' => 'loading';

export function EasterEggs({ children }: { children: ReactNode }) {
  // The server snapshot keeps hydration stable; later choices work even when
  // browser storage is unavailable.
  const savedTrip = useSyncExternalStore<RetroDestination | 'present' | 'loading'>(subscribeToSavedTrip, readSavedTrip, serverTrip);
  const [choice, setChoice] = useState<{ destination: RetroDestination | null } | null>(null);
  const ready = savedTrip !== 'loading';
  const retro = choice ? choice.destination : savedTrip === 'loading' || savedTrip === 'present' ? null : savedTrip;
  const [minimised, setMinimised] = useState(false);
  const setRetro = useCallback((value: RetroDestination | null) => {
    setChoice({ destination: value });
    setMinimised(false);
  }, []);
  const [coffee, setCoffee] = useState(0);
  const [celebrating, setCelebrating] = useState(false);
  const [coffeeVisible, setCoffeeVisible] = useState(false);
  const timeBar = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const pathname = usePathname();
  const destination = retro ? retroDestinations[retro] : null;
  const minimise = useCallback(() => {
    setMinimised(true);
    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('[data-retro-restore]')?.focus());
  }, []);
  const restore = useCallback(() => {
    setMinimised(false);
    requestAnimationFrame(() => timeBar.current?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')?.focus());
  }, []);
  const close = useCallback(() => {
    setRetro(null);
    requestAnimationFrame(() => document.querySelector<HTMLAnchorElement>('.site-header .brand')?.focus());
  }, [setRetro]);
  const brewCoffee = useCallback(() => {
    setCoffee((count) => count + 1);
    setCoffeeVisible(true);
    setCelebrating(true);
  }, []);

  useEffect(() => {
    function reveal(event: Event) {
      const detail = (event as CustomEvent).detail;
      if (detail?.secret === 'retro') setRetro(storedRetroDestination(detail.destination) || 'windows');
      if (detail?.secret === 'coffee') {
        brewCoffee();
      }
    }
    window.addEventListener(SECRET_EVENT, reveal);
    return () => window.removeEventListener(SECRET_EVENT, reveal);
  }, [brewCoffee, setRetro]);

  useEffect(() => {
    if (!ready) return;
    if (retro) {
      document.documentElement.dataset.era = retroDestinations[retro].year;
      document.documentElement.dataset.retro = retro;
    } else {
      delete document.documentElement.dataset.era;
      delete document.documentElement.dataset.retro;
    }
    saveTimeMachineDestination(retro);
    return () => {
      delete document.documentElement.dataset.era;
      delete document.documentElement.dataset.retro;
    };
  }, [retro, ready]);

  useEffect(() => {
    if (!retro || !timeBar.current) return;
    const bar = timeBar.current;
    const header = document.querySelector('.site-header');
    const measure = () => {
      document.documentElement.style.setProperty('--time-machine-height', `${bar.getBoundingClientRect().height}px`);
      document.documentElement.style.setProperty('--retro-header-height', `${header?.getBoundingClientRect().height || 90}px`);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(bar);
    if (header) observer.observe(header);
    measure();
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty('--time-machine-height');
      document.documentElement.style.removeProperty('--retro-header-height');
    };
  }, [retro, pathname]);

  useEffect(() => {
    if (!coffee) return;
    const timer = window.setTimeout(() => setCelebrating(false), 4400);
    return () => window.clearTimeout(timer);
  }, [coffee]);

  useEffect(() => {
    function dismiss(event: KeyboardEvent) {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      setCoffeeVisible(false);
      setCelebrating(false);
      if (retro) close();
    }
    document.addEventListener('keydown', dismiss);
    return () => document.removeEventListener('keydown', dismiss);
  }, [retro, close]);

  return (
    <TimeMachineContext.Provider value={{ destination: retro, minimised, panelId, minimise, restore, close }}>
      {destination && <div ref={timeBar} className="time-machine-panel" data-minimised={minimised || undefined}>
        <div id={panelId} className="time-machine-panel-content" inert={minimised} aria-hidden={minimised}>
        <aside className="time-machine-bar" aria-label="Theme time machine">
        <div className="time-machine-heading"><strong>◷ {destination.year} · {destination.browser}</strong><span>Choose your desktop.</span></div>
        <fieldset className="time-machine-destinations" aria-label="Time machine destinations">
          {(Object.keys(retroDestinations) as RetroDestination[]).map((key) => <button key={key} aria-pressed={retro === key} onClick={() => setRetro(key)}>{retroDestinations[key].label} <span>{retroDestinations[key].year}</span></button>)}
        </fieldset>
        <div className="time-machine-actions">
          <button onClick={minimise}><Minus size={15} aria-hidden="true" /> Minimise controls</button>
          <button className="time-machine-return" onClick={close}><RotateCcw size={15} aria-hidden="true" /> Return to the present</button>
        </div>
        <details className="time-machine-history"><summary>About this stop</summary><p>{destination.description} <a href={destination.source} target="_blank" rel="noreferrer">Mozilla archive ↗</a><span>Period-inspired styling. The Firefox name arrived in 2004.</span></p></details>
        </aside>
        </div>
      </div>}
      <output className="time-machine-announcement">{retro && minimised ? 'Theme controls minimised. Your theme is still active. Restore them from the window bar.' : ''}</output>
      {children}
      {celebrating && coffeeVisible && <div className="coffee-confetti" key={coffee} aria-hidden="true">
        {coffeePieces.map((piece, index) => <i key={index} className={piece.glyph ? 'coffee-glyph' : ''} style={{
          left: piece.left, color: piece.color, backgroundColor: piece.glyph ? 'transparent' : piece.color,
          '--delay': piece.delay, '--turn': piece.turn, '--drift': piece.drift,
        } as CSSProperties}>{piece.glyph}</i>)}
      </div>}
      {coffeeVisible && <aside className="coffee-toast" aria-label="Coffee break celebration">
        <Coffee className="coffee-cup" size={29} aria-hidden="true" />
        <div><output key={coffee}><strong>Coffee break!</strong> You found <code>#c0ffee</code>.</output><span>A little mint. A lot of caffeine.</span>
          <button className="coffee-replay" onClick={brewCoffee}>Another cup? <RotateCcw size={13} /></button>
        </div>
        <button className="coffee-dismiss" aria-label="Dismiss coffee party" onClick={() => { setCoffeeVisible(false); setCelebrating(false); }}><X size={17} /></button>
      </aside>}
    </TimeMachineContext.Provider>
  );
}
