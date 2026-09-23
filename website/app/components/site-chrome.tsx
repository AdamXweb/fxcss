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
      <Brand />
      <span>Made for people who make Firefox their own.</span>
      <a href={`${REPO}/blob/main/LICENSE`} target="_blank" rel="noreferrer">
        MIT licensed <ArrowUpRight size={14} />
      </a>
    </footer>
  );
}
