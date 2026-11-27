import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_PICKED_FILE_BYTES,
  readPickedFiles,
} from "./picked-files";

const encoder = new TextEncoder();

function deps(sizes: Record<string, number>) {
  return {
    stat: async (path: string) => ({ size: sizes[path] ?? 0 }),
    readFile: async (path: string) => encoder.encode(`contents:${path}`),
  };
}

test("reads picked files as base64 with a mime type", async () => {
  const files = await readPickedFiles(
    ["C:\\docs\\report.pdf", "C:\\docs\\notes.txt"],
    deps({ "C:\\docs\\report.pdf": 12, "C:\\docs\\notes.txt": 3 }),
  );
  assert.deepEqual(
    files.map((file) => ({ name: file.name, type: file.type })),
    [
      { name: "report.pdf", type: "application/pdf" },
      { name: "notes.txt", type: "text/plain" },
    ],
  );
  assert.equal(
    Buffer.from(files[0]?.data ?? "", "base64").toString("utf8"),
    "contents:C:\\docs\\report.pdf",
  );
});

test("unknown extensions fall back to octet-stream", async () => {
  const [file] = await readPickedFiles(["C:\\docs\\payload.bin"], deps({}));
  assert.equal(file?.type, "application/octet-stream");
});

test("rejects a file above the per-file limit", async () => {
  await assert.rejects(
    readPickedFiles(["C:\\docs\\big.bin"], deps({ "C:\\docs\\big.bin": MAX_PICKED_FILE_BYTES + 1 })),
    /larger than 10 MB/,
  );
});

test("rejects a selection above the total limit", async () => {
  // Three 10 MB files pass the per-file check but exceed the 28 MB cap.
  await assert.rejects(
    readPickedFiles(
      ["C:\\docs\\a.bin", "C:\\docs\\b.bin", "C:\\docs\\c.bin"],
      deps({
        "C:\\docs\\a.bin": MAX_PICKED_FILE_BYTES,
        "C:\\docs\\b.bin": MAX_PICKED_FILE_BYTES,
        "C:\\docs\\c.bin": MAX_PICKED_FILE_BYTES,
      }),
    ),
    /larger than 28 MB/,
  );
});

test("an empty selection stays empty", async () => {
  assert.deepEqual(await readPickedFiles([], deps({})), []);
});
