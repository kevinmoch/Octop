import {
  GlobalWorkerOptions,
  OPS,
  getDocument,
  type PDFDocumentProxy,
  type PDFPageProxy,
} from "pdfjs-dist";
import type {
  PdfDocumentHandle,
  PdfPageImage,
  RenderedImage,
} from "@webskill/sdk/agent";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = workerSrc;

function withDestroy(
  pdf: PDFDocumentProxy,
  task: ReturnType<typeof getDocument>,
): PdfDocumentHandle {
  return {
    pageCount: pdf.numPages,
    async text(page: number): Promise<string> {
      const proxy = await pageProxy(pdf, page);
      const content = await proxy.getTextContent();
      return content.items
        .map((item) => ("str" in item ? item.str : ""))
        .join(" ")
        .replace(/[ \t]+/g, " ")
        .trim();
    },
    async images(page: number): Promise<readonly PdfPageImage[]> {
      const proxy = await pageProxy(pdf, page);
      const operators = await proxy.getOperatorList();
      const hasImage = operators.fnArray.some(
        (fn) =>
          fn === OPS.paintImageXObject ||
          fn === OPS.paintInlineImageXObject ||
          fn === OPS.paintImageMaskXObject ||
          fn === OPS.paintImageXObjectRepeat,
      );
      // PDF.js gives us image operators, not a geometric union. Reporting a
      // full-page ratio only when an image exists is conservative: it tells the
      // reader to render the page rather than silently dropping the image.
      return hasImage ? [{ areaRatio: 1 }] : [];
    },
    async render(page: number): Promise<RenderedImage> {
      const proxy = await pageProxy(pdf, page);
      const viewport = proxy.getViewport({ scale: 1.5 });
      const canvas = globalThis.document.createElement("canvas");
      canvas.width = Math.max(1, Math.ceil(viewport.width));
      canvas.height = Math.max(1, Math.ceil(viewport.height));
      const context = canvas.getContext("2d");
      if (context === null)
        throw new Error("PDF rendering canvas is unavailable");
      await proxy.render({ canvas, canvasContext: context, viewport }).promise;
      return {
        mimeType: "image/png",
        data: canvas.toDataURL("image/png").split(",", 2)[1] ?? "",
      };
    },
    close: () => task.destroy(),
  };
}

async function pageProxy(
  pdf: PDFDocumentProxy,
  page: number,
): Promise<PDFPageProxy> {
  if (!Number.isInteger(page) || page < 0 || page >= pdf.numPages) {
    throw new Error(`PDF page ${page} is out of range`);
  }
  return pdf.getPage(page + 1);
}

export const pdfDocumentReader = {
  async open(bytes: Uint8Array): Promise<PdfDocumentHandle> {
    const task = getDocument({ data: bytes });
    const document = await task.promise;
    return withDestroy(document, task);
  },
};

export async function pdfExtractor(bytes: Uint8Array): Promise<string> {
  const task = getDocument({ data: bytes });
  const document = await task.promise;
  try {
    const pages: string[] = [];
    for (let index = 0; index < document.numPages; index += 1) {
      const page = await document.getPage(index + 1);
      const content = await page.getTextContent();
      pages.push(
        content.items
          .map((item) => ("str" in item ? item.str : ""))
          .join(" ")
          .replace(/[ \t]+/g, " ")
          .trim(),
      );
    }
    return pages.filter(Boolean).join("\n\n");
  } finally {
    await task.destroy();
  }
}
