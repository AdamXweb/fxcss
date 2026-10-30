import { marked } from 'marked';
import sanitizeHtml from 'sanitize-html';

export function plainText(html) {
  return sanitizeHtml(html.replace(/<\/(?:p|li|div|tr|h[1-6])>/g, ' '), {
    allowedTags: [],
    allowedAttributes: {},
  })
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

export function describeMarkdown(markdown, fallback) {
  const paragraph = marked
    .lexer(markdown)
    .find(
      (token) =>
        token.type === 'paragraph' &&
        token.tokens?.some((part) => part.type !== 'image'),
    );
  const text = paragraph
    ? plainText(marked.parseInline(paragraph.text))
    : fallback;
  if (text.length <= 160) return text;
  const end = text.lastIndexOf(' ', 159);
  // Keep even an unusually long first word intact instead of splitting it.
  return end > 0 ? `${text.slice(0, end).replace(/[,:;.!?]+$/, '')}…` : text;
}
