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
  const tokens = marked.lexer(markdown).filter((token) => token.type !== 'space');
  const inlineText = (value) => plainText(marked.parseInline(value));
  const listText = (token) => token.items.map((item) => plainText(marked.parse(item.text)));
  let text;
  for (const [index, token] of tokens.entries()) {
    if (token.type !== 'paragraph') continue;
    const paragraph = inlineText(token.text);
    if (!paragraph) continue;
    if (!paragraph.endsWith(':')) {
      text = paragraph;
      break;
    }
    // A lead-in to a list needs the first item to make a useful snippet.
    if (tokens[index + 1]?.type === 'list') {
      text = `${paragraph} ${listText(tokens[index + 1])[0]}`;
      break;
    }
    // Substantial prose before an example also works as a standalone summary
    // with a full stop. Skip short stubs such as "The steps are:" instead.
    if (paragraph.length >= 70) {
      text = `${paragraph.slice(0, -1)}.`;
      break;
    }
  }
  if (!text) {
    const list = tokens.find((token) => token.type === 'list');
    const table = tokens.find((token) => token.type === 'table');
    text = list
      ? listText(list).join('; ')
      : table
        ? table.rows.map((row) => row.map((cell) => inlineText(cell.text)).join(': ')).join('; ')
        : fallback;
  }
  if (text.length <= 160) return text;
  const end = text.lastIndexOf(' ', 159);
  // Keep even an unusually long first word intact instead of splitting it.
  return end > 0 ? `${text.slice(0, end).replace(/[,:;.!?]+$/, '')}…` : text;
}
