'use client';

import Link from 'next/link';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { followLogoPress } from '../../lib/logo-picker';

export function BrandLink({ children }: { children: ReactNode }) {
  // Keep an active turn latched when pointer or keyboard focus leaves.
  const [spinning, setSpinning] = useState(false);
  // Ends a logo drag that outlives the link, such as across navigation.
  const stopPicking = useRef<() => void>(undefined);

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

  return (
    <Link
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
  );
}
