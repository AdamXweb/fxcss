import type { Metadata } from 'next';

// The site's share card, public/assets/og-card.png (1200 x 630). A page that
// sets its own openGraph or twitter fields replaces the root layout's whole
// object, so every page passes the image again through shareCard. Titles and
// descriptions left out fall back to the page's own; relative URLs resolve
// against the root layout's metadataBase.
const image = {
  url: '/assets/og-card.png',
  width: 1200,
  height: 630,
  alt: 'fxcss, the Firefox theme toolkit: Your browser. Your kind of Firefox.',
};

export function shareCard({
  title,
  description,
  url,
}: {
  title?: string;
  description?: string;
  url?: string;
} = {}): Pick<Metadata, 'openGraph' | 'twitter'> {
  return {
    openGraph: {
      type: 'website',
      siteName: 'fxcss',
      title,
      description,
      url,
      images: [image],
    },
    twitter: {
      card: 'summary_large_image',
      title,
      description,
      images: [image],
    },
  };
}
