import { SiteHeader, Footer } from '../components/site-chrome';
import { DocsFrame } from '../components/docs-navigation';
import content from '../../content/docs.json';
import searchIndex from '../../content/search-index.json';
export default function DocsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pages = content.pages.map(
    ({ slug, title, chapter, group }) => ({
      slug,
      title,
      chapter,
      group,
    }),
  );
  return (
    <div className="field-guide">
      <SiteHeader docs />
      <DocsFrame pages={pages} version={content.version} searchUrl={searchIndex.url}>
        {children}
      </DocsFrame>
      <Footer />
    </div>
  );
}
