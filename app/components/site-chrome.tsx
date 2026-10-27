import Link from "next/link";
import Image from "next/image";
import { ArrowUpRight, GitFork } from "lucide-react";
import { BrandMark } from "./brand-mark";
import { BrandLink } from "./brand-link";

export const REPO = "https://github.com/AdamXweb/fxcss";
export function Brand() {
  return (
    <BrandLink>
      <BrandMark />
      <span>
        fxcss<span className="brand-dot">.</span>
      </span>
    </BrandLink>
  );
}
export function SiteHeader({ docs = false }: { docs?: boolean }) {
  return (
    <header className={`site-header ${docs ? "docs-header" : ""}`}>
      <div className="retro-window-chrome" aria-hidden="true">
        <span className="retro-window-lights"><i /><i /><i /></span>
        <span className="retro-window-title"><span className="retro-title-windows">fxcss.com — The pre-Firefox web</span><span className="retro-title-mac">fxcss.com — Mozilla Firebird 0.6</span><span className="retro-title-linux">fxcss.com — Phoenix 0.1</span></span>
        <span className="retro-window-controls">_ □ ×</span>
      </div>
      <Brand />
      <nav aria-label="Main navigation">
        <Link href="/docs" aria-current={docs ? "page" : undefined}>
          Documentation
        </Link>
        <Link href="/docs/installation" className="header-install">
          Install
        </Link>
        <a
          href={REPO}
          target="_blank"
          rel="noreferrer"
          className="github-link"
          aria-label="GitHub repository"
        >
          <GitFork size={17} />
          <span>GitHub</span>
          <ArrowUpRight size={13} />
        </a>
      </nav>
    </header>
  );
}
export function Footer() {
  return (
    <footer className="site-footer">
      <div className="content-width">
        <div className="footer-main">
          <div className="footer-intro">
            <Brand />
            <p>Made for people who make Firefox their own.</p>
            <Link href="/important" className="footer-secret" aria-label="!important: discover the specificity boss battle" title="Think you know which rule wins?">{'{ !important }'}</Link>
          </div>
          <nav aria-label="Product links">
            <h2>Product</h2>
            <Link href="/docs">Documentation</Link>
            <Link href="/docs/installation">Install</Link>
            <Link href="/docs/screenshot-evidence">Screenshot evidence</Link>
          </nav>
          <nav aria-label="Source and community">
            <h2>Source &amp; community</h2>
            <a href={REPO} rel="noopener">Source code</a>
            <a href={`${REPO}/issues`} rel="noopener">Report a bug</a>
            <a href={`${REPO}/releases`} rel="noopener">Releases</a>
            <a href={`${REPO}/blob/main/LICENSE`} rel="noopener">MIT licence</a>
            <Link href="/open">Website data</Link>
            <a href="https://adamxweb.com/contact" rel="noopener">Contact</a>
          </nav>
        </div>
        <div className="footer-credit">
          <p>
            © {new Date().getUTCFullYear()} fxcss · made by{' '}
            <a
              className="footer-maker"
              href="https://github.com/AdamXweb"
              rel="me noopener"
              aria-label="adamxweb on GitHub"
            >
              <Image
                unoptimized
                src="/assets/adamxweb-avatar.jpg"
                width={16}
                height={16}
                alt=""
              />
              adamxweb
            </a>
          </p>
          <p>
            No cookies.{' '}
            <a href="https://www.simpleanalytics.com/privacy-policy" rel="noopener">
              Simple Analytics
            </a>{' '}
            counts page views and outbound clicks.
          </p>
        </div>
      </div>
    </footer>
  );
}
