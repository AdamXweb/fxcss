'use client';

import Link from 'next/link';
import {
  useEffect,
  useEffectEvent,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { Check, Copy, X } from 'lucide-react';
import { labelPosition } from '../../lib/logo-picker';
import { reportLines, type PickReport } from '../../lib/pick-report';

/**
 * What a dropped logo picked, printed the way `fxcss pick` prints a click.
 * A non-modal dialog beside the element: Escape, the close button, a press
 * anywhere else or leaving the page closes it.
 */
export function PickCard({
  element,
  report,
  onClose,
}: {
  element: Element;
  report: PickReport;
  /** `true` when focus should go back to the logo. */
  onClose: (restoreFocus: boolean) => void;
}) {
  const card = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const [status, setStatus] = useState('');
  const statusTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const close = useEffectEvent(onClose);
  const [header, ...details] = reportLines(report);

  // Beside the element, as the label was, and still beside it after scrolling.
  useLayoutEffect(() => {
    const node = card.current!;
    const view = node.ownerDocument.defaultView!;
    const root = node.ownerDocument.documentElement;
    let frame = 0;
    function place() {
      frame = 0;
      if (!element.isConnected) return close(false);
      const { left, top } = labelPosition(
        element.getBoundingClientRect(),
        { width: node.offsetWidth, height: node.offsetHeight },
        { width: root.clientWidth, height: root.clientHeight },
        6,
        8,
      );
      node.style.translate = `${left}px ${top}px`;
    }
    function schedule() {
      frame ||= view.requestAnimationFrame(place);
    }
    place();
    node.focus({ preventScroll: true });
    view.addEventListener('scroll', schedule, { capture: true, passive: true });
    view.addEventListener('resize', schedule);
    return () => {
      view.cancelAnimationFrame(frame);
      view.removeEventListener('scroll', schedule, { capture: true });
      view.removeEventListener('resize', schedule);
    };
  }, [element]);

  useEffect(() => {
    const node = card.current!;
    const doc = node.ownerDocument;
    function pressElsewhere(event: PointerEvent) {
      if (!node.contains(event.target as Node)) close(false);
    }
    // Only while focus is in the card; search and the time machine keep theirs.
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      close(true);
    }
    doc.addEventListener('pointerdown', pressElsewhere, true);
    node.addEventListener('keydown', escape);
    return () => {
      doc.removeEventListener('pointerdown', pressElsewhere, true);
      node.removeEventListener('keydown', escape);
      clearTimeout(statusTimer.current);
    };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(report.selector);
      setStatus('Copied');
    } catch {
      setStatus('Select the selector and copy it manually.');
    }
    clearTimeout(statusTimer.current);
    statusTimer.current = setTimeout(() => setStatus(''), 3000);
  }

  return createPortal(
    // Open without showModal: the rest of the page stays usable.
    <dialog
      ref={card}
      open
      className="pick-card"
      aria-labelledby={heading}
      tabIndex={-1}
    >
      <div className="pick-card-bar">
        <span id={heading}>
          {/* The prompt changes with the time machine: $, C:\> or %. */}
          <span className="pick-card-prompt" aria-hidden="true" />
          fxcss pick
        </span>
        <button
          type="button"
          className="pick-card-close"
          aria-label="Close fxcss pick"
          onClick={() => onClose(true)}
        >
          <X size={15} aria-hidden="true" />
        </button>
      </div>
      <pre className="pick-card-report">
        {/* One block per line, so a wrapped rule hangs under its own start. */}
        <strong>{header}</strong>
        {details.map((line, index) => (
          <span key={index}>{line}</span>
        ))}
      </pre>
      <div className="pick-card-actions">
        <button type="button" onClick={copy}>
          {status === 'Copied' ? (
            <Check size={14} aria-hidden="true" />
          ) : (
            <Copy size={14} aria-hidden="true" />
          )}
          Copy selector
        </button>
        <Link href="/docs/pick" onClick={() => onClose(false)}>
          Pick Firefox’s UI with fxcss pick
        </Link>
        <output className="pick-card-status">{status}</output>
      </div>
    </dialog>,
    element.ownerDocument.body,
  );
}
