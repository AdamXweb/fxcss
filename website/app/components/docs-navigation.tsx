"use client";
import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, X, ChevronDown } from "lucide-react";
import { Sidebar, SidebarProvider } from "@/components/ui/sidebar";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "@/components/ui/collapsible";
export type NavPage = {
  slug: string;
  title: string;
  description: string;
  chapter: string;
  group: string;
};
export function DocsFrame({
  pages,
  version,
  children,
}: {
  pages: NavPage[];
  version: string;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const matches = pages.filter((p) =>
    `${p.title} ${p.description} ${p.chapter}`.toLowerCase().includes(query.toLowerCase().trim()),
  );
  const groups = [...new Set(pages.map((p) => p.chapter))];
  const nav = (
    <nav aria-label="Documentation">
      <label className="guide-search">
        <Search size={17} />
        <Input
          aria-label="Search documentation"
          placeholder="Search docs…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Clear search"
            onClick={() => setQuery("")}
          >
            <X size={15} />
          </Button>
        )}
      </label>
      <Link
        href="/docs"
        className={`getting-started ${pathname === "/docs" ? "selected" : ""}`}
        aria-current={pathname === "/docs" ? "page" : undefined}
        onClick={() => setOpen(false)}
      >
        Getting started
      </Link>
      {query && (
        <output className="search-count">
          {matches.length} matching {matches.length === 1 ? "page" : "pages"}
        </output>
      )}
      <div className="command-navigation">
        {groups.map((group) => {
          const list = matches.filter((p) => p.chapter === group);
          if (!list.length) return null;
          return (
            <section className="nav-group" key={group}>
              <h2>{group}</h2>
              {list.map((p) => (
                <Link
                  href={`/docs/${p.slug}`}
                  key={p.slug}
                  className={`guide-command ${pathname === `/docs/${p.slug}` ? "selected" : ""}`}
                  aria-current={pathname === `/docs/${p.slug}` ? "page" : undefined}
                  onClick={() => setOpen(false)}
                >
                  {p.group === "command" ? (
                    <code>{p.title.replace("fxcss ", "")}</code>
                  ) : p.title === group ? (
                    "Overview"
                  ) : (
                    p.title
                  )}
                </Link>
              ))}
            </section>
          );
        })}
        {matches.length === 0 && (
          <output className="no-results">No matching pages. Try “theme” or “capture”.</output>
        )}
      </div>
      <div className="docs-version">Documentation for fxcss {version}</div>
    </nav>
  );
  return (
    <SidebarProvider className="guide-layout">
      <Sidebar collapsible="none" className="guide-sidebar">
        <div className="desktop-docs-nav">{nav}</div>
        <Collapsible open={open} onOpenChange={setOpen} className="mobile-docs-nav">
          <CollapsibleTrigger className="mobile-nav-trigger">
            Browse documentation <ChevronDown size={17} />
          </CollapsibleTrigger>
          <CollapsibleContent>{nav}</CollapsibleContent>
        </Collapsible>
      </Sidebar>
      {children}
    </SidebarProvider>
  );
}
