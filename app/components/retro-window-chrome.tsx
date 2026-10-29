'use client';

import { useContext } from 'react';
import { Minus, Square, X } from 'lucide-react';
import { TimeMachineContext } from '../../lib/time-machine-context';
import { retroDestinations } from '../../lib/time-machine';

export function RetroWindowChrome() {
  const trip = useContext(TimeMachineContext);
  if (!trip?.destination) return null;
  const { minimised, panelId, minimise, restore, close } = trip;
  const destination = retroDestinations[trip.destination];
  const actions = {
    close: { label: 'Close time machine and return to the present', icon: X, action: close, disabled: false },
    minimise: { label: 'Minimise theme controls', icon: Minus, action: minimise, disabled: minimised },
    restore: { label: 'Restore theme controls', icon: Square, action: restore, disabled: !minimised },
  };
  const order = trip.destination === 'mac' ? ['close', 'minimise', 'restore'] as const : ['minimise', 'restore', 'close'] as const;

  return (
    <div className="retro-window-chrome" data-minimised={minimised || undefined}>
      <span className="retro-window-title">{minimised
        ? `${destination.label} · ${destination.year}`
        : `fxcss.com — ${trip.destination === 'mac' ? 'Mozilla ' : ''}${destination.browser}`}</span>
      {minimised && <button className="retro-controls-restore" onClick={restore} aria-controls={panelId} aria-expanded={false}>Theme controls ↑</button>}
      <fieldset className="retro-window-controls" aria-label="Theme window controls">
        {order.map((name) => {
          const { label, icon: Icon, action, disabled } = actions[name];
          return <button key={name} type="button" className={`retro-window-button retro-window-${name}`} onClick={action} disabled={disabled} aria-label={label} aria-controls={panelId} aria-expanded={name === 'close' ? undefined : !minimised} data-retro-restore={name === 'restore' || undefined}>
            <span className="retro-window-button-face"><Icon size={12} strokeWidth={2.5} aria-hidden="true" /></span>
            <span className="retro-window-tooltip" aria-hidden="true">{label}</span>
          </button>;
        })}
      </fieldset>
    </div>
  );
}
