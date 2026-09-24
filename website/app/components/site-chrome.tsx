import Link from "next/link";
import Image from "next/image";
import { ArrowUpRight, GitFork } from "lucide-react";

export const REPO = "https://github.com/AdamXweb/fxcss";
export function Brand() {
  return (
    <Link className="brand" href="/" aria-label="fxcss home">
      <Image unoptimized src="/assets/icon.png" width={36} height={36} alt="" />
      <span>
        fxcss<span className="brand-dot">.</span>
      </span>
    </Link>
  );
}
export function SiteHeader({ docs = false }: { docs?: boolean }) {
  return (
    <header className={`site-header ${docs ? "docs-header" : ""}`}>
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
          <p>No cookies or analytics on this site.</p>
        </div>
      </div>
    </footer>
  );
}
