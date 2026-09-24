'use client';
import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Search, X, ChevronDown } from 'lucide-react';
import { Sidebar, SidebarProvider } from '@/components/ui/sidebar';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import { searchDocs, type SearchableDoc } from '@/lib/docs-search';
export type NavPage = SearchableDoc;

function Highlight({ text, query }: { text: string; query: string }) {
  const terms = [
    ...new Set(
      query
        .trim()
        .split(/\s+/)
        .map((term) => term.toLocaleLowerCase()),
    ),
  ]
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  if (!terms.length) return text;
  const escaped = terms.map((term) =>
    term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
  );
  const parts = text.split(new RegExp(`(${escaped.join('|')})`, 'gi'));
  return parts.map((part, index) =>
    terms.includes(part.toLocaleLowerCase()) ? (
      <mark key={index}>{part}</mark>
    ) : (
      part
    ),
  );
}
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
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const searching = Boolean(query.trim());
  const matches = searchDocs(pages, query);
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
            onClick={() => setQuery('')}
          >
            <X size={15} />
          </Button>
        )}
      </label>
      <Link
        href="/docs"
        className={`getting-started ${pathname === '/docs' ? 'selected' : ''}`}
        aria-current={pathname === '/docs' ? 'page' : undefined}
        onClick={() => setOpen(false)}
      >
        Getting started
      </Link>
      {searching && (
        <output className="search-count">
          {matches.length} matching {matches.length === 1 ? 'page' : 'pages'}
        </output>
      )}
      {searching ? (
        <div className="docs-search-results">
          {matches.map(({ page, href, excerpt }) => (
            <Link
              href={href}
              key={page.slug}
              className="docs-search-result"
              onClick={() => setOpen(false)}
            >
              <span className="docs-search-result-title">
                <Highlight text={page.title} query={query} />
              </span>
              <span className="docs-search-result-excerpt">
                <Highlight text={excerpt} query={query} />
              </span>
              <span className="docs-search-result-chapter">{page.chapter}</span>
            </Link>
          ))}
          {matches.length === 0 && (
            <output className="no-results">
              No matching pages. Try a command or a shorter phrase.
            </output>
          )}
        </div>
      ) : (
        <div className="command-navigation">
          {groups.map((group) => (
            <section className="nav-group" key={group}>
              <h2>{group}</h2>
              {pages
                .filter((p) => p.chapter === group)
                .map((p) => (
                  <Link
                    href={`/docs/${p.slug}`}
                    key={p.slug}
                    className={`guide-command ${pathname === `/docs/${p.slug}` ? 'selected' : ''}`}
                    aria-current={
                      pathname === `/docs/${p.slug}` ? 'page' : undefined
                    }
                    onClick={() => setOpen(false)}
                  >
                    {p.group === 'command' ? (
                      <code>{p.title.replace('fxcss ', '')}</code>
                    ) : p.title === group ? (
                      'Overview'
                    ) : (
                      p.title
                    )}
                  </Link>
                ))}
            </section>
          ))}
        </div>
      )}
      <div className="docs-version">Documentation for fxcss {version}</div>
    </nav>
  );
  return (
    <SidebarProvider className="guide-layout">
      <Sidebar collapsible="none" className="guide-sidebar">
        <div className="desktop-docs-nav">{nav}</div>
        <Collapsible
          open={open}
          onOpenChange={setOpen}
          className="mobile-docs-nav"
        >
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
