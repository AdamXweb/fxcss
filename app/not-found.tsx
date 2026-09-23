import Link from "next/link";
import { SiteHeader, Footer } from "./components/site-chrome";
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
