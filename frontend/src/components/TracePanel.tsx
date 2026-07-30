import type { BookMeta, ToolCall } from '../types';

interface Props {
  trace: ToolCall[];
  books: BookMeta[];
}

/** Turn an internal tool call into a plain-language description for editors. */
function describe(tc: ToolCall, books: BookMeta[]): string | null {
  const bookTitle = (id: unknown): string | null => {
    const b = books.find((x) => x.id === id);
    return b ? b.title : null;
  };

  switch (tc.tool) {
    case 'list_books':
      return 'Reviewed the available books';
    case 'get_outline': {
      const t = bookTitle(tc.args.book_id);
      return t ? `Consulted the chapter outline of ${t}` : 'Consulted the chapter outline';
    }
    case 'search': {
      // The model's raw query is keyword-stuffed for retrieval, not readable —
      // describe the action instead of echoing it, but keep a chapter reference
      // if the query targeted one (that's genuinely useful context).
      const t = bookTitle(tc.args.book_id);
      const q = typeof tc.args.query === 'string' ? tc.args.query : '';
      const ch = q.match(/chapter\s+(\d+)/i);
      const chapter = ch ? ` (Chapter ${ch[1]})` : '';
      const where = t ? `${t}${chapter}` : `the books${chapter}`;
      return `Searched ${where} for relevant passages`;
    }
    case 'get_context':
      return 'Pulled up the surrounding passage for context';
    case 'find_similar_passages':
      return 'Compared passages across two books';
    default:
      return null; // unknown tool → don't surface dev noise
  }
}

export default function TracePanel({ trace, books }: Props) {
  const steps = trace
    .map((tc) => describe(tc, books))
    .filter((s): s is string => s !== null)
    // Collapse consecutive identical steps (e.g. several searches in a row).
    .filter((s, i, arr) => s !== arr[i - 1]);

  if (steps.length === 0) return null;

  return (
    <div style={{ padding: 16 }}>
      <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
        Sources consulted
      </h3>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
        {steps.map((text, i) => (
          <li
            key={i}
            style={{
              display: 'flex',
              gap: 8,
              padding: '8px 0',
              borderBottom: '1px solid #f0f0f0',
              fontSize: 14,
              lineHeight: 1.5,
              color: '#444',
            }}
          >
            <span style={{ color: '#1976d2', flexShrink: 0 }}>•</span>
            <span>{text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
