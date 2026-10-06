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
export default async function Documentation({
  searchParams,
}: {
  searchParams: Promise<{ path?: string }>;
}) {
  const { path } = await searchParams;
  const initialJourney = path === 'build' || path === 'maintain' ? path : 'use';
  return (
    <main id="main" className="guide-article">
      <GettingStarted key={initialJourney} initialJourney={initialJourney} />
    </main>
  );
}
