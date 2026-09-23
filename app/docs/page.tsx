import type { Metadata } from 'next';
import { GettingStarted } from '../components/getting-started';
export const metadata: Metadata = {
  title: 'Getting started',
  alternates: { canonical: '/docs' },
  openGraph: { url: '/docs' },
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
