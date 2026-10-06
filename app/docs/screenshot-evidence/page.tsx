import type { Metadata } from 'next';
import Link from 'next/link';
import evidence from '../../../content/evidence.json';
import { ComparisonDemo } from '../../components/comparison';
import { shareCard } from '../../../lib/share-card';
export const metadata: Metadata = {
  title: 'Screenshot evidence',
  alternates: { canonical: '/docs/screenshot-evidence' },
  ...shareCard({ url: '/docs/screenshot-evidence' }),
  description:
    'How the fxcss website’s screenshots were captured and verified.',
};
export default function EvidencePage() {
  return (
    <main id="main" className="guide-article">
      <div className="breadcrumbs">
        <Link href="/docs">Documentation</Link>
        <span>/</span>Screenshot evidence
      </div>
      <span className="eyebrow">MEASURED FROM FIREFOX</span>
      <h1>
        Real changes.
        <br />
        Traceable comparisons.
      </h1>
      <p className="guide-lead">
        The homepage shows two changes to the bundled fxcss starter theme, each
        captured in a disposable Firefox profile: a small one and an obvious
        one.
      </p>
      <ComparisonDemo eyebrow="TWO CHANGES, ONE BASELINE" />
      <div className="doc-body">
        <h2>What changed</h2>
        <p>
          One light-mode declaration in <code>chrome/userChrome.css</code>:{' '}
          <code>--demo-accent: {evidence.change.before}</code> became{' '}
          <code>--demo-accent: {evidence.change.after}</code>. The dark-mode
          declaration was unchanged.
        </p>
        <h2>How it was measured</h2>
        <p>
          fxcss {evidence.fxcssVersion} captured both versions in Firefox{' '}
          {evidence.browser.version} on macOS on{' '}
          {evidence.generatedAt.slice(0, 10)}. Both original captures are{' '}
          {evidence.dimensions.width} × {evidence.dimensions.height} pixels.
        </p>
        <p>
          The standard comparison normalises each capture to 960 × 360 pixels,
          then ignores per-channel differences below {evidence.noiseThreshold}.
          It found {evidence.comparison.changed_pixels} changed pixels out of{' '}
          {evidence.comparison.total_pixels.toLocaleString('en-US')} —{' '}
          {evidence.comparison.percent}
          %, displayed as {evidence.comparison.percent.toFixed(2)}%.
        </p>
        <p>
          The homepage shows the upper part of each unedited capture at the same
          scale. The percentage covers the full normalised image. Pink
          highlights are slightly enlarged by fxcss so small changes remain
          visible.
        </p>
        <h2>A more obvious change</h2>
        <p>
          The obvious example is a second change from the same baseline:{' '}
          <code>--demo-toolbar: {evidence.toolbar.change.before}</code> became{' '}
          <code>--demo-toolbar: {evidence.toolbar.change.after}</code>, which
          recolours the tab strip, the navigation toolbar and the bookmarks
          toolbar. The same comparison found{' '}
          {evidence.toolbar.comparison.changed_pixels.toLocaleString('en-US')}{' '}
          changed pixels out of{' '}
          {evidence.toolbar.comparison.total_pixels.toLocaleString('en-US')} —{' '}
          {evidence.toolbar.comparison.percent}%.
        </p>
        <h2>Check the source material</h2>
        <ul>
          <li>
            <a href="/evidence/before.png" target="_blank" rel="noreferrer">
              Original before screenshot
            </a>{' '}
            and{' '}
            <a href="/evidence/after.png" target="_blank" rel="noreferrer">
              original after screenshot
            </a>
          </li>
          <li>
            <a href="/evidence/before.css" target="_blank" rel="noreferrer">
              Before stylesheet
            </a>{' '}
            and{' '}
            <a href="/evidence/after.css" target="_blank" rel="noreferrer">
              after stylesheet
            </a>
          </li>
          <li>
            <a
              href="/evidence/comparison-summary.json"
              target="_blank"
              rel="noreferrer"
            >
              Comparison report for all 20 views
            </a>
          </li>
          <li>
            <a
              href="/evidence/before-coverage.json"
              target="_blank"
              rel="noreferrer"
            >
              Before capture coverage
            </a>{' '}
            and{' '}
            <a
              href="/evidence/after-coverage.json"
              target="_blank"
              rel="noreferrer"
            >
              after capture coverage
            </a>
          </li>
          <li>
            <a
              href="/evidence/toolbar-after.png"
              target="_blank"
              rel="noreferrer"
            >
              Toolbar change screenshot
            </a>
            ,{' '}
            <a
              href="/evidence/toolbar-after.css"
              target="_blank"
              rel="noreferrer"
            >
              stylesheet
            </a>
            ,{' '}
            <a
              href="/evidence/toolbar-comparison-summary.json"
              target="_blank"
              rel="noreferrer"
            >
              comparison report
            </a>{' '}
            and{' '}
            <a
              href="/evidence/toolbar-after-coverage.json"
              target="_blank"
              rel="noreferrer"
            >
              capture coverage
            </a>
          </li>
          <li>
            <a href="/evidence/manifest.json" target="_blank" rel="noreferrer">
              Versions, measurements, and asset checksums
            </a>
          </li>
        </ul>
        <p>
          All three capture runs completed all 20 expected views. Both changes
          are light-mode only, so the four standard dark-mode views were
          pixel-identical in both comparisons, as expected.
        </p>
      </div>
    </main>
  );
}
