import type { Metadata } from 'next';
import { SiteHeader, Footer } from '../components/site-chrome';
import { SpecificityBattle } from '../components/specificity-battle';
import { shareCard } from '../../lib/share-card';

export const metadata: Metadata = {
  title: 'Specificity boss battle',
  description: 'A small, playable CSS lesson. Pick the winning rule and learn why the cascade chose it.',
  alternates: { canonical: '/important' },
  robots: { index: false, follow: true },
  ...shareCard({ url: '/important' }),
};

export default function ImportantPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="battle-page content-width">
        <div className="battle-intro">
          <span className="eyebrow">SECRET LEVEL UNLOCKED</span>
          <h1>Specificity<br /><span>boss battle.</span></h1>
          <p>Two rules enter. One colour wins. Learn the CSS cascade, one tiny battle at a time.</p>
        </div>
        <SpecificityBattle />
      </main>
      <Footer />
    </>
  );
}
