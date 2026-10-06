'use client';
import { useId, useRef, useState } from 'react';
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
// Rendered twice, in the desktop sidebar and in the mobile disclosure, so each
// copy gets its own ids.
function DocsNavigation({
  pages,
  version,
  query,
  setQuery,
  onNavigate,
}: {
  pages: NavPage[];
  version: string;
  query: string;
  setQuery: (query: string) => void;
  onNavigate: () => void;
}) {
  const pathname = usePathname();
  const searchId = useId();
  const searchField = useRef<HTMLInputElement>(null);
  const searching = Boolean(query.trim());
  const matches = searchDocs(pages, query);
  const groups = [...new Set(pages.map((p) => p.chapter))];
  return (
    <nav aria-label="Documentation">
      <div className="guide-search">
        <label htmlFor={searchId}>Search documentation</label>
        <div className="guide-search-field">
          <Search size={17} aria-hidden="true" />
          <Input
            id={searchId}
            ref={searchField}
            placeholder="Command, option or guide"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(event) => {
              // Escape clears a search first; a second Escape reaches the menu.
              if (event.key !== 'Escape' || !query) return;
              event.preventDefault();
              event.stopPropagation();
              setQuery('');
            }}
          />
          {query && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Clear search"
              onClick={() => {
                setQuery('');
                searchField.current?.focus();
              }}
            >
              <X size={15} />
            </Button>
          )}
        </div>
      </div>
      <Link
        href="/docs"
        className={`getting-started ${pathname === '/docs' ? 'selected' : ''}`}
        aria-current={pathname === '/docs' ? 'page' : undefined}
        onClick={onNavigate}
      >
        Getting started
      </Link>
      {/* Always mounted, so screen readers announce each new count. */}
      <output className="search-count">
        {!searching
          ? ''
          : matches.length
            ? `${matches.length} matching ${matches.length === 1 ? 'page' : 'pages'}`
            : 'No matching pages. Try a command or a shorter phrase.'}
      </output>
      {searching ? (
        <div className="docs-search-results">
          {matches.map(({ page, href, excerpt }) => (
            <Link
              href={href}
              key={page.slug}
              className="docs-search-result"
              onClick={onNavigate}
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
                    onClick={onNavigate}
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
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const navigation = {
    pages,
    version,
    query,
    setQuery,
    onNavigate: () => setOpen(false),
  };
  return (
    <SidebarProvider className="guide-layout">
      <Sidebar collapsible="none" className="guide-sidebar">
        <div className="desktop-docs-nav">
          <DocsNavigation {...navigation} />
        </div>
        <Collapsible
          open={open}
          onOpenChange={setOpen}
          className="mobile-docs-nav"
          onKeyDown={(event) => {
            // Escape closes the open menu and returns focus to its button.
            if (event.key !== 'Escape' || !open) return;
            setOpen(false);
            menuButton.current?.focus();
          }}
        >
          <CollapsibleTrigger ref={menuButton} className="mobile-nav-trigger">
            Browse documentation <ChevronDown size={17} aria-hidden="true" />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <DocsNavigation {...navigation} />
          </CollapsibleContent>
        </Collapsible>
      </Sidebar>
      {children}
    </SidebarProvider>
  );
}
