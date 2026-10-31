'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { followLogoPress } from '../../lib/logo-picker';
import { pickReport, type PickReport } from '../../lib/pick-report';
import { PickCard } from './pick-card';

export function BrandLink({ children }: { children: ReactNode }) {
  // Keep an active turn latched when pointer or keyboard focus leaves.
  const [spinning, setSpinning] = useState(false);
  // Ends a logo drag that outlives the link, such as across navigation.
  const stopPicking = useRef<() => void>(undefined);
  const link = useRef<HTMLAnchorElement>(null);
  // The element a dropped logo picked, reported as `fxcss pick` would.
  const [picked, setPicked] = useState<{
    element: Element;
    report: PickReport;
    /** A report describes one page, so it closes when the page changes. */
    pathname: string;
  }>();
  const pathname = usePathname();
  // Leaving the page drops its report, so coming back does not revive it.
  if (picked && picked.pathname !== pathname) setPicked(undefined);

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const stopForReducedMotion = () => {
      if (reducedMotion.matches) setSpinning(false);
    };
    reducedMotion.addEventListener('change', stopForReducedMotion);
    return () =>
      reducedMotion.removeEventListener('change', stopForReducedMotion);
  }, []);

  useEffect(() => () => stopPicking.current?.(), []);

  function startSpin() {
    if (
      spinning ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    )
      return;
    setSpinning(true);
  }

  // The card is the link's sibling, not its child: React events bubble
  // through portals, and the link would treat a press on the card as its own.
  return (
    <>
      <Link
        ref={link}
        className="brand"
        href="/"
        aria-label="fxcss home"
        draggable={false}
        data-spinning={spinning || undefined}
        onPointerEnter={(event) => {
          if (event.pointerType !== 'touch') startSpin();
        }}
        onPointerDown={(event) => {
          // Mouse and pen can pick up the mark. Touch keeps scrolling and
          // tapping, and modified clicks keep opening new tabs and windows.
          if (
            event.pointerType === 'touch' ||
            !event.isPrimary ||
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey
          )
            return;
          stopPicking.current?.();
          stopPicking.current = followLogoPress(
            event.currentTarget,
            event.nativeEvent,
            (element) =>
              setPicked({ element, report: pickReport(element), pathname }),
          );
        }}
        onFocus={(event) => {
          if (event.currentTarget.matches(':focus-visible')) startSpin();
        }}
        onAnimationEnd={(event) => {
          if (event.animationName === 'fxcss-flame-spin') setSpinning(false);
        }}
      >
        {children}
      </Link>
      {picked && (
        <PickCard
          element={picked.element}
          report={picked.report}
          onClose={(restoreFocus) => {
            setPicked(undefined);
            if (restoreFocus) link.current?.focus({ preventScroll: true });
          }}
        />
      )}
    </>
  );
}
