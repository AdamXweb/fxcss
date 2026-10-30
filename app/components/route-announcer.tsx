'use client';

import { useEffect, useRef, useState } from 'react';
import { usePathname } from 'next/navigation';

export function RouteAnnouncer() {
  const pathname = usePathname();
  const previousPath = useRef(pathname);
  const [announcement, setAnnouncement] = useState('');

  useEffect(() => {
    if (pathname === previousPath.current) return;
    previousPath.current = pathname;

    // Navigation and metadata can commit separately. Wait for the DOM, and
    // observe later title changes without moving focus out of the navigation.
    let frame = 0;
    const announce = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setAnnouncement(document.title));
    };
    const observer = new MutationObserver(announce);
    observer.observe(document.head, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    announce();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [pathname]);

  return <output className="sr-only" aria-label="Page navigation" aria-live="polite" aria-atomic="true">{announcement}</output>;
}
