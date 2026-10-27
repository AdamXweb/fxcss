/** The standalone SVG is also the source for both independently animated parts. */
export function BrandMark() {
  return (
    <svg
      className="brand-mark"
      width="36"
      height="36"
      viewBox="0 0 512 512"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <use
        className="brand-flames"
        href="/assets/fxcss-logo.svg#fxcss-flames"
      />
      <use
        className="brand-braces"
        href="/assets/fxcss-logo.svg#fxcss-braces"
      />
    </svg>
  );
}
