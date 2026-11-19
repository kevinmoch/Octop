import { pathToFileURL } from "node:url";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";

beforeAll(async () => {
  if (!("DOMMatrix" in globalThis)) {
    (
      globalThis as unknown as { DOMMatrix: unknown }
    ).DOMMatrix = class DOMMatrix {};
  }
  if (
    typeof (Uint8Array.prototype as unknown as { toHex?: unknown }).toHex !==
    "function"
  ) {
    Object.defineProperty(Uint8Array.prototype, "toHex", {
      value(this: Uint8Array) {
        return Array.from(this, (byte) =>
          byte.toString(16).padStart(2, "0"),
        ).join("");
      },
    });
  }
  if (
    typeof (Map.prototype as unknown as { getOrInsertComputed?: unknown })
      .getOrInsertComputed !== "function"
  ) {
    Object.defineProperty(Map.prototype, "getOrInsertComputed", {
      value<K, V>(this: Map<K, V>, key: K, compute: (key: K) => V): V {
        if (!this.has(key)) this.set(key, compute(key));
        return this.get(key) as V;
      },
    });
  }
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = pathToFileURL(
    path.resolve("webskill/node_modules/pdfjs-dist/build/pdf.worker.min.mjs"),
  ).href;
});

const PDF_BASE64 =
  "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Ci9Gb250IDw8Ci9GMSA8PAovVHlwZSAvRm9udAovU3VidHlwZSAvVHlwZTEKL0Jhc2VGb250IC9IZWx2ZXRpY2EKPj4KPj4KPj4KL01lZGlhQm94IFsgMC4wIDAuMCAzMDAgMjAwIF0KL1BhcmVudCAyIDAgUgovQ29udGVudHMgPDwKL0xlbmd0aCA0MAo+PgpzdHJlYW0KQlQgL0YxIDEyIFRmIDIwIDEwMCBUZCAoSGVsbG8gUERGKSBUaiBFVAplbmRzdHJlYW0KPj4KZW5kb2JqCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDU0IDAwMDAwIG4gCjAwMDAwMDAxMTMgMDAwMDAgbiAKMDAwMDAwMDE2MiAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9TaXplIDUKL1Jvb3QgMyAwIFIKL0luZm8gMSAwIFIKPj4Kc3RhcnR4cmVmCjQxMgolJUVPRgo=";

const IMAGE_PDF_BASE64 =
  "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Ci9YT2JqZWN0IDw8Ci9JbTEgPDwKL1R5cGUgL1hPYmplY3QKL1N1YnR5cGUgL0ltYWdlCi9XaWR0aCAxCi9IZWlnaHQgMQovQ29sb3JTcGFjZSAvRGV2aWNlUkdCCi9CaXRzUGVyQ29tcG9uZW50IDgKL0xlbmd0aCAzCj4+CnN0cmVhbQr/AAAKZW5kc3RyZWFtCj4+Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgMzAwIDIwMCBdCi9QYXJlbnQgMiAwIFIKL0NvbnRlbnRzIDw8Ci9MZW5ndGggMzIKPj4Kc3RyZWFtCnEgMTAwIDAgMCAxMDAgNTAgNTAgY20gL0ltMSBEbyBRCmVuZHN0cmVhbQo+PgplbmRvYmoKeHJlZgowIDUKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDE1IDAwMDAwIG4gCjAwMDAwMDAwNTQgMDAwMDAgbiAKMDAwMDAwMDExMyAwMDAwMCBuIAowMDAwMDAwMTYyIDAwMDAwIG4gCnRyYWlsZXIKPDwKL1NpemUgNQovUm9vdCAzIDAgUgovSW5mbyAxIDAgUgo+PgpzdGFydHhyZWYKNDgzCiUlRU9GCg==";

function imageBytes(): Uint8Array {
  return Uint8Array.from(atob(IMAGE_PDF_BASE64), (char) => char.charCodeAt(0));
}

function bytes(): Uint8Array {
  return Uint8Array.from(atob(PDF_BASE64), (char) => char.charCodeAt(0));
}

describe("PDF.js host reader", () => {
  it("extracts text through pdfExtractor", async () => {
    const { pdfExtractor } = await import("../../../../webskill/src/pdf");
    expect(await pdfExtractor(bytes())).toContain("Hello PDF");
  });

  it("reports an image XObject as a page image fact", async () => {
    const { pdfDocumentReader } = await import("../../../../webskill/src/pdf");
    const handle = await pdfDocumentReader.open(imageBytes());
    try {
      const images = await handle.images(0);
      expect(images).toHaveLength(1);
      expect(images[0].areaRatio).toBe(1);
    } finally {
      await handle.close();
    }
  });

  it("exposes page count, text and image facts", async () => {
    const { pdfDocumentReader } = await import("../../../../webskill/src/pdf");
    const handle = await pdfDocumentReader.open(bytes());
    try {
      expect(handle.pageCount).toBe(1);
      expect(await handle.text(0)).toContain("Hello PDF");
      expect(await handle.images(0)).toEqual([]);
    } finally {
      await handle.close();
    }
  });
});
