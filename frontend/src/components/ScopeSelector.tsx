import type { BookMeta } from '../types';

interface Props {
  books: BookMeta[];
  selectedBookId: number | null;
  onSelect: (bookId: number | null) => void;
}

export default function ScopeSelector({ books, selectedBookId, onSelect }: Props) {
  return (
    <select
      value={selectedBookId ?? ''}
      onChange={(e) => onSelect(e.target.value ? parseInt(e.target.value, 10) : null)}
      style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid #ccc', fontSize: 14 }}
    >
      <option value="">All books</option>
      {books.map((b) => (
        <option key={b.id} value={b.id}>
          {b.title}
        </option>
      ))}
    </select>
  );
}
