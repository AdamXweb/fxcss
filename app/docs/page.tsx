import type { Metadata } from 'next';
import { GettingStarted } from '../components/getting-started';
import { shareCard } from '../../lib/share-card';
export const metadata: Metadata = {
  title: 'Getting started',
  alternates: { canonical: '/docs' },
  ...shareCard({ url: '/docs' }),
  description:
    'Install fxcss and start trying, building, or maintaining Firefox themes.',
};
export default function Documentation() {
  return (
    <main id="main" className="guide-article">
      <GettingStarted />
    </main>
  );
}
