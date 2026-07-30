import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fetchIngestJobs, uploadBook, fetchBooks, ApiError } from './api';

function mockFetch(response: Partial<Response> & { jsonData?: unknown; textData?: string }) {
  const res = {
    ok: response.ok ?? true,
    status: response.status ?? 200,
    json: async () => response.jsonData,
    text: async () => response.textData ?? '',
  } as unknown as Response;
  return vi.fn().mockResolvedValue(res);
}

describe('api client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch({ jsonData: [] }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('fetchIngestJobs GETs the /api/books/jobs list endpoint', async () => {
    const jobs = [
      { id: 'a', title: 'Book A', status: 'running', stage: 'embedding', progress: 0.5, detail: '', book_id: null, error: null },
    ];
    const fetchSpy = mockFetch({ jsonData: jobs });
    vi.stubGlobal('fetch', fetchSpy);

    const result = await fetchIngestJobs();

    expect(fetchSpy).toHaveBeenCalledWith('/api/books/jobs', undefined);
    expect(result).toEqual(jobs);
  });

  it('fetchBooks hits /api/books', async () => {
    const fetchSpy = mockFetch({ jsonData: [] });
    vi.stubGlobal('fetch', fetchSpy);

    await fetchBooks();

    expect(fetchSpy).toHaveBeenCalledWith('/api/books', undefined);
  });

  it('uploadBook POSTs multipart form data with file, title and author', async () => {
    const fetchSpy = mockFetch({ jsonData: { id: 'j1', title: 'T', status: 'queued' } });
    vi.stubGlobal('fetch', fetchSpy);
    const file = new File(['<html></html>'], 'b.html', { type: 'text/html' });

    const job = await uploadBook(file, 'My Title', 'Me');

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe('/api/books');
    expect(init.method).toBe('POST');
    const form = init.body as FormData;
    expect(form.get('title')).toBe('My Title');
    expect(form.get('author')).toBe('Me');
    expect((form.get('file') as File).name).toBe('b.html');
    expect(job.status).toBe('queued');
  });

  it('surfaces FastAPI `detail` as an ApiError message on non-2xx', async () => {
    const fetchSpy = mockFetch({
      ok: false,
      status: 409,
      textData: JSON.stringify({ detail: "A book titled 'X' already exists." }),
    });
    vi.stubGlobal('fetch', fetchSpy);
    const file = new File(['x'], 'b.html', { type: 'text/html' });

    await expect(uploadBook(file, 'X', 'Y')).rejects.toMatchObject({
      status: 409,
      message: "A book titled 'X' already exists.",
    });
    await expect(uploadBook(file, 'X', 'Y')).rejects.toBeInstanceOf(ApiError);
  });
});
