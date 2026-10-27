'use client';

import Link from 'next/link';
import { useEffect, useState, type ReactNode } from 'react';

export function BrandLink({ children }: { children: ReactNode }) {
  // Keep an active turn latched when pointer or keyboard focus leaves.
  const [spinning, setSpinning] = useState(false);

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const stopForReducedMotion = () => {
      if (reducedMotion.matches) setSpinning(false);
    };
    reducedMotion.addEventListener('change', stopForReducedMotion);
    return () =>
      reducedMotion.removeEventListener('change', stopForReducedMotion);
  }, []);

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
      data-spinning={spinning || undefined}
      onPointerEnter={(event) => {
        if (event.pointerType !== 'touch') startSpin();
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
