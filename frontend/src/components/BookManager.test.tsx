import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import BookManager from './BookManager';
import type { BookMeta, IngestJobStatus } from '../types';

const book: BookMeta = {
  id: 1, title: 'Little Women', author: 'Alcott',
  key: 'lw', word_count: 1000, chapter_count: 47, summary: null,
};

function job(over: Partial<IngestJobStatus> = {}): IngestJobStatus {
  return {
    id: 'j1', title: 'Pride and Prejudice', status: 'running',
    stage: 'embedding', progress: 0.42, detail: 'Embedding 4/10 chunks',
    book_id: null, error: null, ...over,
  };
}

function renderManager(props: Partial<React.ComponentProps<typeof BookManager>> = {}) {
  return render(
    <BookManager
      books={props.books ?? []}
      jobs={props.jobs ?? []}
      error={props.error ?? null}
      onUpload={props.onUpload ?? vi.fn().mockResolvedValue(true)}
      onClose={props.onClose ?? vi.fn()}
      onChanged={props.onChanged ?? vi.fn()}
    />,
  );
}

describe('BookManager', () => {
  it('renders a labelled progress bar per in-flight job', () => {
    renderManager({ jobs: [job({ title: 'Book A' }), job({ id: 'j2', title: 'Book B' })] });
    expect(screen.getByText('Book A')).toBeInTheDocument();
    expect(screen.getByText('Book B')).toBeInTheDocument();
  });

  it('shows a "Queued" message for a job waiting behind another', () => {
    renderManager({ jobs: [job({ status: 'queued' })] });
    expect(screen.getByText(/Queued — waiting/i)).toBeInTheDocument();
  });

  it('keeps the upload control usable while ingestions run (queueing allowed)', async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn().mockResolvedValue(true);
    const { container } = renderManager({ jobs: [job()], onUpload });

    // The action reframes as "Add to queue" rather than being disabled.
    const button = screen.getByRole('button', { name: /add to queue/i });

    await user.type(screen.getByPlaceholderText(/title/i), 'New Book');
    const fileInput = container.querySelector(
      'input[accept=".html,.htm,text/html"]',
    ) as HTMLInputElement;
    await user.upload(fileInput, new File(['<html></html>'], 'n.html', { type: 'text/html' }));

    expect(button).toBeEnabled();
    await user.click(button);
    // BookManager calls onUpload(file, title, author).
    expect(onUpload).toHaveBeenCalledWith(expect.any(File), 'New Book', '');
  });

  it('disables Remove while an ingestion is running (protects the shared DB)', () => {
    renderManager({ books: [book], jobs: [job()] });
    expect(screen.getByRole('button', { name: /remove/i })).toBeDisabled();
  });

  it('enables Remove when nothing is ingesting', () => {
    renderManager({ books: [book], jobs: [] });
    const remove = screen.getByRole('button', { name: /remove/i });
    expect(remove).toBeEnabled();
  });
});
