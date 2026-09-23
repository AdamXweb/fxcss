"use client";
import Link from "next/link";
import { Button } from "@/components/ui/button";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main id="main" className="not-found content-width">
      <h1>This page couldn’t load.</h1>
      <p>Try again, or return to the homepage.</p>
      <Button onClick={reset}>Try again</Button>
      <Link href="/">Back to fxcss</Link>
    </main>
  );
}
