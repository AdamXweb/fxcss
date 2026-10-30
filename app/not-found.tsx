import type { Metadata } from "next";
import Link from "next/link";
import { SiteHeader, Footer } from "./components/site-chrome";
import { shareCard } from "../lib/share-card";

const description = "This page could not be found. Browse the fxcss documentation for Firefox theme commands, guides, and troubleshooting.";
export const metadata: Metadata = {
  title: "Page not found",
  description,
  ...shareCard({ title: "Page not found · fxcss", description }),
};

export default function NotFound() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="not-found content-width">
        <p className="eyebrow">404 · PAGE NOT FOUND</p>
        <h1>Let’s find your way back.</h1>
        <p>This page doesn’t exist. Browse the documentation to find a command or a guide.</p>
        <Link className="button primary" href="/docs">
          Open the documentation
        </Link>
      </main>
      <Footer />
    </>
  );
}
