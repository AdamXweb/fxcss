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
    <footer className="site-footer content-width">
      <nav aria-label="Project links">
        <a href={REPO} rel="noopener">Source</a>
        <a href={`${REPO}/issues`} rel="noopener">Report a bug</a>
        <a href={`${REPO}/releases`} rel="noopener">Releases</a>
        <a href="https://github.com/adamXbot/.github/blob/main/STATUS.md" rel="noopener">
          Project status
        </a>
        <a href={`${REPO}/blob/main/LICENSE`} rel="noopener">MIT licence</a>
      </nav>
      <p>
        © {new Date().getUTCFullYear()}{' '}
        <a href="https://adam.kostarelas.com" rel="me">Adam Kostarelas</a>.
        {' '}One of the apps at{' '}
        <a href="https://adamxweb.com/">adamxweb.com</a>.
      </p>
      <nav className="footer-site-links" aria-label="Site links">
        <Link href="/docs">Documentation</Link>
        <a href="https://adamxweb.com/contact" rel="noopener">Contact</a>
      </nav>
      <p className="footer-note">No cookies or analytics on this site.</p>
    </footer>
  );
}
