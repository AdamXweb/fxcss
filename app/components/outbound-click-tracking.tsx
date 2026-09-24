'use client';

import { useEffect } from 'react';

declare global {
  interface Window {
    sa_loaded?: boolean;
    sa_event?: (
      name: string,
      metadata: Record<string, string>,
      callback?: () => void,
    ) => void;
  }
}

export function OutboundClickTracking() {
  useEffect(() => {
    function trackClick(event: MouseEvent) {
      if (event.defaultPrevented || (event.type === 'auxclick' && event.button !== 1))
        return;
      const target = event.target;
      const element =
        target instanceof Element
          ? target
          : target instanceof Node
            ? target.parentElement
            : null;
      const anchor = element?.closest('a[href]');
      if (!(anchor instanceof HTMLAnchorElement)) return;

      const destination = new URL(anchor.href);
      if (
        !['http:', 'https:'].includes(destination.protocol) ||
        destination.hostname === window.location.hostname ||
        !window.sa_loaded ||
        !window.sa_event
      )
        return;

      // Query strings and fragments can contain user data; keep only the destination path.
      const metadata = { url: destination.origin + destination.pathname };
      const sameTab =
        event.type === 'click' &&
        event.button === 0 &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.shiftKey &&
        !event.altKey &&
        (!anchor.target || anchor.target === '_self') &&
        !anchor.download;
      const name = `outbound_${destination.hostname.replace(/[^a-z0-9]+/gi, '_')}`;
      if (!sameTab) {
        window.sa_event(name, metadata);
        return;
      }

      // The tracker sends an image request. Wait briefly for its completion before
      // leaving this page, with a fallback so a failed request never traps the link.
      event.preventDefault();
      let navigated = false;
      let timeout: number | undefined;
      const navigate = () => {
        if (navigated) return;
        navigated = true;
        if (timeout !== undefined) window.clearTimeout(timeout);
        window.location.assign(anchor.href);
      };
      try {
        window.sa_event(name, metadata, navigate);
        timeout = window.setTimeout(navigate, 800);
      } catch {
        navigate();
      }
    }

    document.addEventListener('click', trackClick);
    document.addEventListener('auxclick', trackClick);
    return () => {
      document.removeEventListener('click', trackClick);
      document.removeEventListener('auxclick', trackClick);
    };
  }, []);

  return null;
}
