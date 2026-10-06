import type { Metadata } from 'next';
import Link from 'next/link';
import { SETUP_COMMAND } from '../lib/site';
import { shareCard } from '../lib/share-card';
export const metadata: Metadata = {
  alternates: { canonical: '/' },
  ...shareCard({ url: '/' }),
};
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  MousePointer2,
  Play,
  ShieldCheck,
} from 'lucide-react';
import { SiteHeader, Footer } from './components/site-chrome';
import { CopyCommand } from './components/copy-command';
import { ComparisonDemo } from './components/comparison';
const INSTALL = SETUP_COMMAND;
function Journeys() {
  const paths = [
    {
      icon: <Play />,
      n: '01',
      title: 'Try',
      text: 'Explore a new look in a temporary Firefox profile before installing it in your everyday browser.',
      path: 'use',
      action: 'Try a theme',
    },
    {
      icon: <MousePointer2 />,
      n: '02',
      title: 'Build',
      text: 'Create your own theme. Find the right selectors and see your CSS changes in Firefox as you save.',
      path: 'build',
      action: 'Build a theme',
    },
    {
      icon: <ShieldCheck />,
      n: '03',
      title: 'Maintain',
      text: 'Keep your theme working across Firefox releases with screenshot comparisons and compatibility checks.',
      path: 'maintain',
      action: 'Maintain a theme',
    },
  ];
  return (
    <div className="journey-grid">
      {paths.map((path) => (
        <article key={path.n}>
          <div className="journey-icon">
            {path.icon}
            <span>{path.n}</span>
          </div>
          <h3>{path.title}</h3>
          <p>{path.text}</p>
          <Link className="text-link" href={`/docs?path=${path.path}`}>
            {path.action} <ArrowUpRight size={16} />
          </Link>
        </article>
      ))}
    </div>
  );
}
export default function Home() {
  return (
    <div className="showcase">
      <SiteHeader />
      <main id="main">
        <div className="hero-band">
          <div className="hero-grain" aria-hidden="true" />
          <section className="showcase-hero content-width">
            <div className="eyebrow">
              <span className="status-dot" /> THE FIREFOX THEME TOOLKIT
            </div>
            <h1>
              Your browser.
              <br />
              <span>Your kind of Firefox.</span>
            </h1>
            <p className="hero-description">
              fxcss is a command-line toolkit for Firefox themes. Preview a new
              look safely, edit your own theme live, and catch changes as
              Firefox evolves.
            </p>
            <div className="hero-actions">
              <a className="button primary" href="#start">
                Find your starting point <ArrowRight size={18} />
              </a>
              <a className="text-link" href="#demo">
                See the difference <ArrowDown size={16} />
              </a>
            </div>
            <p className="requirements">
              Open source <span>·</span> macOS, Windows & Linux <span>·</span>{' '}
              Python + Firefox
            </p>
          </section>
          <section
            className="film content-width"
            aria-label="fxcss in under a minute"
          >
            {/* The film has no audio track; its words are on screen. */}
            <video
              controls
              muted
              playsInline
              preload="none"
              poster="/media/fxcss-film-poster.jpg"
              width={1920}
              height={1080}
            >
              <source src="/media/fxcss-film.mp4" type="video/mp4" />
            </video>
          </section>
        </div>
        <section className="journeys content-width" id="start">
          <div className="section-heading" id="explore">
            <h2>What do you want to do?</h2>
            <p>From your first theme to your next release.</p>
          </div>
          <Journeys />
        </section>
        <section className="showcase-demo content-width" id="demo">
          <ComparisonDemo />
        </section>
        <section className="install-band content-width">
          <div>
            <span className="eyebrow">READY WHEN YOU ARE</span>
            <h2>A small install. A lot of possibility.</h2>
            <p>
              Run guided setup on macOS or Linux. Choose what you want to do,
              then get the tools and next steps.
            </p>
          </div>
          <div>
            <CopyCommand command={INSTALL} />
            <p className="setup-caption">
              Already have pipx? <code>{'pipx install "fxcss[images]"'}</code>
            </p>
            <Link
              className="text-link"
              href="/install.sh"
              target="_blank"
              rel="noreferrer"
              prefetch={false}
            >
              Read the setup script <ArrowUpRight size={15} />
            </Link>
            <Link
              href="/docs/installation"

              className="text-link"
            >
              Installation guide <ArrowUpRight size={15} />
            </Link>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
