"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type Props = {
  url: string;
  className?: string;
};

export function PdfViewer({ url, className }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const renderTaskRef = useRef<{ cancel?: () => void } | null>(null);

  const [numPages, setNumPages] = useState<number>(0);
  const [page, setPage] = useState<number>(1);
  const [scale, setScale] = useState<number>(1.2);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const clampedPage = useMemo(() => {
    if (numPages <= 0) return 1;
    return Math.max(1, Math.min(page, numPages));
  }, [page, numPages]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNumPages(0);
    setPage(1);

    async function run() {
      try {
        const pdfjs = await import("pdfjs-dist/build/pdf.mjs");
        const workerUrl = new URL(
          "pdfjs-dist/build/pdf.worker.mjs",
          import.meta.url,
        ).toString();
        // eslint-disable-next-line @typescript-eslint/no-unsafe-member-access
        (pdfjs as any).GlobalWorkerOptions.workerSrc = workerUrl;

        const doc = await pdfjs.getDocument({ url }).promise;
        if (cancelled) return;
        setNumPages(doc.numPages || 0);
        setLoading(false);

        async function renderPage(pageNum: number, pageScale: number) {
          const pdfPage = await doc.getPage(pageNum);
          if (cancelled) return;
          const viewport = pdfPage.getViewport({ scale: pageScale });
          const canvas = canvasRef.current;
          if (!canvas) return;
          const ctx = canvas.getContext("2d");
          if (!ctx) return;
          canvas.width = Math.floor(viewport.width);
          canvas.height = Math.floor(viewport.height);

          try {
            renderTaskRef.current?.cancel?.();
          } catch {
            // ignore
          }
          const task = pdfPage.render({ canvasContext: ctx, viewport });
          renderTaskRef.current = task;
          await task.promise;
        }

        await renderPage(1, scale);

        return { doc, renderPage };
      } catch (e: unknown) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      }
    }

    let helpers: { doc: any; renderPage: (p: number, s: number) => Promise<void> } | undefined;
    run().then((h) => {
      helpers = h;
    });

    return () => {
      cancelled = true;
      try {
        renderTaskRef.current?.cancel?.();
      } catch {
        // ignore
      }
      try {
        // eslint-disable-next-line @typescript-eslint/no-unsafe-call
        (helpers as any)?.doc?.destroy?.();
      } catch {
        // ignore
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  useEffect(() => {
    let cancelled = false;
    async function rerender() {
      if (loading || error) return;
      try {
        const pdfjs = await import("pdfjs-dist/build/pdf.mjs");
        const doc = await pdfjs.getDocument({ url }).promise;
        if (cancelled) return;
        const pdfPage = await doc.getPage(clampedPage);
        if (cancelled) return;
        const viewport = pdfPage.getViewport({ scale });
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        try {
          renderTaskRef.current?.cancel?.();
        } catch {
          // ignore
        }
        const task = pdfPage.render({ canvasContext: ctx, viewport });
        renderTaskRef.current = task;
        await task.promise;
      } catch {
        // ignore
      }
    }
    void rerender();
    return () => {
      cancelled = true;
    };
  }, [clampedPage, scale, url, loading, error]);

  return (
    <div className={className}>
      <div className="flex items-center justify-between gap-3 border-b border-zinc-200 bg-white px-3 py-2">
        <div className="flex items-center gap-2 text-xs text-zinc-600">
          <button
            type="button"
            className="rounded border border-zinc-200 bg-white px-2 py-1 disabled:opacity-50"
            disabled={clampedPage <= 1 || loading}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            上一页
          </button>
          <div className="tabular-nums">
            {clampedPage} / {numPages || "—"}
          </div>
          <button
            type="button"
            className="rounded border border-zinc-200 bg-white px-2 py-1 disabled:opacity-50"
            disabled={numPages <= 0 || clampedPage >= numPages || loading}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </div>

        <div className="flex items-center gap-2 text-xs text-zinc-600">
          <button
            type="button"
            className="rounded border border-zinc-200 bg-white px-2 py-1 disabled:opacity-50"
            disabled={scale <= 0.6 || loading}
            onClick={() => setScale((s) => Math.max(0.6, Number((s - 0.1).toFixed(2))))}
          >
            -
          </button>
          <div className="w-14 text-center tabular-nums">{Math.round(scale * 100)}%</div>
          <button
            type="button"
            className="rounded border border-zinc-200 bg-white px-2 py-1 disabled:opacity-50"
            disabled={scale >= 2.0 || loading}
            onClick={() => setScale((s) => Math.min(2.0, Number((s + 0.1).toFixed(2))))}
          >
            +
          </button>
        </div>
      </div>

      <div className="h-[60vh] overflow-auto bg-zinc-100 p-3">
        {loading ? (
          <div className="text-sm text-zinc-500">PDF 加载中…</div>
        ) : error ? (
          <div className="text-sm text-red-600">PDF 加载失败：{error}</div>
        ) : (
          <div className="mx-auto w-fit rounded bg-white p-2 shadow">
            <canvas ref={canvasRef} />
          </div>
        )}
      </div>
    </div>
  );
}
