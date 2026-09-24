export type SearchPassage = { id: string; text: string };

export type SearchableDoc = {
  slug: string;
  title: string;
  chapter: string;
  group: string;
  description: string;
  search: SearchPassage[];
};

export type DocSearchResult = {
  page: SearchableDoc;
  href: string;
  excerpt: string;
  score: number;
};

function excerptAround(text: string, terms: string[]) {
  const lower = text.toLocaleLowerCase();
  const positions = terms
    .map((term) => lower.indexOf(term))
    .filter((at) => at >= 0);
  const focus = positions.length ? Math.min(...positions) : 0;
  let start = Math.max(0, focus - 48);
  if (start > 0) {
    const nextSpace = text.indexOf(' ', start);
    if (nextSpace >= 0) start = nextSpace + 1;
  }
  let end = Math.min(text.length, start + 166);
  if (end < text.length) {
    const previousSpace = text.lastIndexOf(' ', end);
    if (previousSpace > start) end = previousSpace;
  }
  return `${start ? '…' : ''}${text.slice(start, end).trim()}${end < text.length ? '…' : ''}`;
}

export function searchDocs(
  pages: SearchableDoc[],
  query: string,
): DocSearchResult[] {
  const phrase = query.trim().replace(/\s+/g, ' ').toLocaleLowerCase();
  if (!phrase) return [];
  const terms = [...new Set(phrase.split(' '))];
  const results: DocSearchResult[] = [];

  for (const page of pages) {
    const title = page.title.toLocaleLowerCase();
    const description = page.description.toLocaleLowerCase();
    const passages = page.search.map((passage) => ({
      ...passage,
      lower: passage.text.toLocaleLowerCase(),
    }));
    const best = passages.find((passage) => passage.lower.includes(phrase));
    const titleMatches = title.includes(phrase);
    const descriptionMatches = description.includes(phrase);
    if (!titleMatches && !descriptionMatches && !best) continue;
    const score =
      (titleMatches ? 80 : 0) + (descriptionMatches ? 15 : 0) + (best ? 40 : 0);
    const useDescription = !best;
    const excerptSource = useDescription ? page.description : best.text;
    results.push({
      page,
      href: `/docs/${page.slug}${best ? `#${best.id}` : ''}`,
      excerpt: excerptAround(excerptSource, terms),
      score,
    });
  }

  return results.sort((a, b) => b.score - a.score);
}
