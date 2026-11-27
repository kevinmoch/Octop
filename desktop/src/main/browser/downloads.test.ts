import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  DownloadTracker,
  safeDownloadName,
  uniqueDownloadPath,
  type DownloadItemLike,
} from "./downloads";

class FakeItem implements DownloadItemLike {
  filename: string;
  mimeType: string;
  received = 0;
  total = 0;
  savePath = "";
  private done?: (details: unknown, state: string) => void;

  constructor(filename: string, options: { mimeType?: string; size?: number } = {}) {
    this.filename = filename;
    this.mimeType = options.mimeType ?? "";
    this.total = options.size ?? 0;
  }

  getFilename(): string {
    return this.filename;
  }

  getMimeType(): string {
    return this.mimeType;
  }

  getTotalBytes(): number {
    return this.total;
  }

  getReceivedBytes(): number {
    return this.received;
  }

  setSavePath(path: string): void {
    this.savePath = path;
  }

  once(_event: "done", listener: (details: unknown, state: string) => void): void {
    this.done = listener;
  }

  finish(state = "completed"): void {
    this.done?.({}, state);
  }
}

async function withDirectory(run: (directory: string) => Promise<void>): Promise<void> {
  const directory = await mkdtemp(join(tmpdir(), "octop-downloads-"));
  try {
    await run(directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("safeDownloadName drops directories and control characters", () => {
  assert.equal(safeDownloadName("../../etc/passwd"), "passwd");
  assert.equal(safeDownloadName("report\u0000.pdf"), "report.pdf");
  assert.equal(safeDownloadName("   "), "download");
});

test("uniqueDownloadPath never overwrites an existing file", async () => {
  await withDirectory(async (directory) => {
    await writeFile(join(directory, "report.pdf"), "one");
    await writeFile(join(directory, "report (1).pdf"), "two");
    assert.equal(uniqueDownloadPath(directory, "report.pdf"), join(directory, "report (2).pdf"));
    assert.equal(uniqueDownloadPath(directory, "fresh.txt"), join(directory, "fresh.txt"));
  });
});

test("a completed download is listed, readable, and settles its window", async () => {
  await withDirectory(async (directory) => {
    const tracker = new DownloadTracker({ directory });
    const token = tracker.openWindow();
    const item = new FakeItem("report.pdf", { mimeType: "application/pdf", size: 5 });
    const entry = tracker.track(item);
    assert.equal(item.savePath, join(directory, "report.pdf"));
    assert.deepEqual(tracker.list(), []);
    item.received = 5;
    item.finish();
    await writeFile(join(directory, "report.pdf"), "hello");

    assert.deepEqual(tracker.list(), [entry]);
    const read = await tracker.read(entry.id);
    assert.equal(read.contentType, "application/pdf");
    assert.equal(Buffer.from(read.data, "base64").toString("utf8"), "hello");
    assert.equal(await tracker.settleWindow(token, 100), 1);
  });
});

test("a cancelled download is not listed", async () => {
  await withDirectory(async (directory) => {
    const tracker = new DownloadTracker({ directory });
    const item = new FakeItem("cancelled.zip");
    tracker.track(item);
    item.finish("cancelled");
    assert.deepEqual(tracker.list(), []);
    assert.equal(await tracker.settleWindow("0", 10), 0);
  });
});

test("settleWindow only counts downloads after its token", async () => {
  await withDirectory(async (directory) => {
    const tracker = new DownloadTracker({ directory, sleep: async () => undefined });
    const first = new FakeItem("one.txt");
    tracker.track(first);
    first.finish();
    const token = tracker.openWindow();

    const second = new FakeItem("two.txt");
    tracker.track(second);
    second.finish();
    assert.equal(await tracker.settleWindow(token, 50), 1);
  });
});

test("read refuses unknown, unfinished, and oversized downloads", async () => {
  await withDirectory(async (directory) => {
    const tracker = new DownloadTracker({ directory, maxReadBytes: 2 });
    await assert.rejects(tracker.read("dl_missing"), /not available/);

    const item = new FakeItem("big.bin", { size: 10 });
    const entry = tracker.track(item);
    await assert.rejects(tracker.read(entry.id), /not available/);

    item.received = 10;
    item.finish();
    await writeFile(join(directory, "big.bin"), "0123456789");
    await assert.rejects(tracker.read(entry.id), /too large/);
  });
});

test("entries use the reported mime type and a stable timestamp", async () => {
  await withDirectory(async (directory) => {
    const tracker = new DownloadTracker({
      directory,
      now: () => new Date("2026-01-02T03:04:05.000Z"),
    });
    const item = new FakeItem("data.bin");
    tracker.track(item);
    item.finish();
    const [entry] = tracker.list();
    assert.equal(entry?.contentType, "application/octet-stream");
    assert.equal(entry?.at, "2026-01-02T03:04:05.000Z");
  });
});
