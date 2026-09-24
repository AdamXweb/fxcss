import { SiteHeader } from '../components/site-chrome';
import { DocsFrame } from '../components/docs-navigation';
import content from '../../content/docs.json';
export default function DocsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pages = content.pages.map(
    ({ slug, title, description, chapter, group, search }) => ({
      slug,
      title,
      description,
      chapter,
      group,
      search,
    }),
  );
  return (
    <div className="field-guide">
      <SiteHeader docs />
      <DocsFrame pages={pages} version={content.version}>
        {children}
      </DocsFrame>
    </div>
  );
}
