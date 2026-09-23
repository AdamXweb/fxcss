import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowUpRight, ArrowRight } from "lucide-react";
import content from "../../../content/docs.json";
import { DocArticle } from "../../components/doc-article";
import { Comparison, AppearancePreview } from "../../components/comparison";
export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const page = content.pages.find((p) => p.slug === slug);
  return {
    title: page?.title || "Page not found",
    description: page?.description,
    ...(page
      ? {
          alternates: { canonical: `/docs/${page.slug}` },
          openGraph: { url: `/docs/${page.slug}` },
        }
      : {}),
  };
}
export default async function DocumentationPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const index = content.pages.findIndex((p) => p.slug === slug);
  if (index < 0) notFound();
  const page = content.pages[index];
  const next = content.pages[index + 1];
  return (
    <>
      <main id="main" className="guide-article">
        <div className="breadcrumbs">
          <Link href="/docs">Documentation</Link>
          <span>/</span>
          {page.group === "command" ? "Command reference" : "Guides"}
        </div>
        <span className="eyebrow">
          {page.group === "command" ? "COMMAND REFERENCE" : "THE FIELD GUIDE"}
        </span>
        <h1>{page.title}</h1>
        <div className="doc-source">
          <span>fxcss {content.version}</span>
          <a
            href={`https://github.com/AdamXweb/fxcss#${page.sourceAnchor}`}
            target="_blank"
            rel="noreferrer"
          >
            Source documentation <ArrowUpRight size={13} />
          </a>
        </div>
        <div className="guide-rule" />
        {slug === "compare" && <Comparison />}
        <DocArticle html={page.html} codes={page.codes} />
        {slug === "watch" && <AppearancePreview />}
        {slug === "catalogue" && (
          <a
            className="button primary"
            href="/catalogue/index.html"
            target="_blank"
            rel="noreferrer"
          >
            Open the captured catalogue <ArrowUpRight size={16} />
          </a>
        )}
        <div className="guide-bottom-links">
          <Link href="/docs">Getting started</Link>
          {next && (
            <Link href={`/docs/${next.slug}`}>
              {next.title}
              <ArrowRight size={15} />
            </Link>
          )}
        </div>
      </main>
      <aside className="guide-toc" aria-label="On this page">
        <span>ON THIS PAGE</span>
        {page.headings.length ? (
          page.headings.map((h) => (
            <a href={`#${h.id}`} key={h.id}>
              {h.text}
            </a>
          ))
        ) : (
          <a href="#main">{page.title}</a>
        )}
        <div>
          <strong>Need a hand?</strong>
          <p>
            Check your local Firefox setup with <code>fxcss doctor</code>.
          </p>
          <Link href="/docs/doctor">
            Troubleshoot your setup <ArrowRight size={14} />
          </Link>
        </div>
      </aside>
    </>
  );
}
