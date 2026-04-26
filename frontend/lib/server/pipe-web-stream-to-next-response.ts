import type { NextApiRequest, NextApiResponse } from "next";

/** Fetch / undici `Response.body` (DOM `ReadableStream` in TS lib). */
type FetchResponseBody = NonNullable<Response["body"]>;

/**
 * Pipe a fetch() Response body (Web ReadableStream) to a Next.js Pages `res` without buffering the archive.
 *
 * Avoids `Readable.fromWeb` + `stream.promises.pipeline` into `ServerResponse`, which often throws
 * `ERR_STREAM_PREMATURE_CLOSE` with undici/fetch-backed bodies even when the backend completes normally.
 */
export async function pipeWebReadableStreamToNextResponse(
  body: FetchResponseBody,
  req: NextApiRequest,
  res: NextApiResponse,
): Promise<void> {
  const reader = body.getReader();

  const onReqAborted = () => {
    reader.cancel(new Error("client aborted")).catch(() => {});
  };
  req.once("aborted", onReqAborted);

  try {
    for (;;) {
      let readResult: Awaited<ReturnType<typeof reader.read>>;
      try {
        readResult = await reader.read();
      } catch (e) {
        const aborted =
          (e instanceof DOMException && e.name === "AbortError") ||
          (e instanceof Error && e.name === "AbortError");
        if (aborted) {
          break;
        }
        throw e;
      }
      const { done, value } = readResult;
      if (done) {
        break;
      }
      if (res.destroyed || res.writableEnded) {
        await reader.cancel(new Error("response closed")).catch(() => {});
        return;
      }
      const ok = res.write(value);
      if (!ok) {
        await new Promise<void>((resolve, reject) => {
          const onDrain = () => {
            cleanup();
            resolve();
          };
          const onError = (err: unknown) => {
            cleanup();
            reject(err instanceof Error ? err : new Error(String(err)));
          };
          const cleanup = () => {
            res.off("drain", onDrain);
            res.off("error", onError);
          };
          res.once("drain", onDrain);
          res.once("error", onError);
        });
      }
    }
    if (!res.destroyed && !res.writableEnded) {
      res.end();
    }
  } finally {
    req.off("aborted", onReqAborted);
    try {
      reader.releaseLock();
    } catch {
      // ignore (already released or cancelled)
    }
  }
}
