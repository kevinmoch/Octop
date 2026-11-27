import { existsSync, mkdirSync } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { basename, extname, join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

/** A finished download, as the WebSkill download reader expects it. */
export interface DownloadEntry {
  id: string;
  name: string;
  contentType: string;
  size: number;
  at: string;
}

/** The subset of `Electron.DownloadItem` the tracker drives. */
export interface DownloadItemLike {
  getFilename(): string;
  getMimeType(): string;
  getTotalBytes(): number;
  getReceivedBytes(): number;
  setSavePath(path: string): void;
  once(event: "done", listener: (details: unknown, state: string) => void): void;
}

interface DownloadRecord {
  seq: number;
  entry: DownloadEntry;
  path: string;
  complete: boolean;
}

export interface DownloadTrackerOptions {
  directory: string;
  now?: () => Date;
  readFile?: (path: string) => Promise<Uint8Array>;
  sleep?: (ms: number) => Promise<void>;
  /** Files above this size are not handed to the WebSkill readers. */
  maxReadBytes?: number;
}

const DEFAULT_MAX_READ_BYTES = 32 * 1024 * 1024;
const SETTLE_POLL_MS = 100;

/**
 * Downloads started by local browser tabs.
 *
 * `will-download` is observed in the main process, so the WebSkill download
 * watcher can see files that never pass through the backend. Files land in the
 * user's download directory; an existing name gets a numeric suffix instead of
 * being overwritten (design doc §8.5).
 */
export class DownloadTracker {
  private readonly records = new Map<string, DownloadRecord>();
  private readonly options: DownloadTrackerOptions;
  private readonly now: () => Date;
  private readonly readFileImpl: (path: string) => Promise<Uint8Array>;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly maxReadBytes: number;
  private seq = 0;

  constructor(options: DownloadTrackerOptions) {
    this.options = options;
    this.now = options.now ?? (() => new Date());
    this.readFileImpl = options.readFile ?? ((path) => readFile(path));
    this.sleep = options.sleep ?? ((ms) => delay(ms));
    this.maxReadBytes = options.maxReadBytes ?? DEFAULT_MAX_READ_BYTES;
    mkdirSync(options.directory, { recursive: true });
  }

  /** Observe one started download. Returns its entry (not yet complete). */
  track(item: DownloadItemLike): DownloadEntry {
    const name = safeDownloadName(item.getFilename());
    const path = uniqueDownloadPath(this.options.directory, name);
    item.setSavePath(path);
    const seq = ++this.seq;
    const entry: DownloadEntry = {
      id: `dl_${seq}_${this.now().getTime().toString(36)}`,
      name,
      contentType: item.getMimeType() || "application/octet-stream",
      size: 0,
      at: this.now().toISOString(),
    };
    const record: DownloadRecord = { seq, entry, path, complete: false };
    this.records.set(entry.id, record);
    item.once("done", (_details, state) => {
      if (state !== "completed") {
        this.records.delete(entry.id);
        return;
      }
      const received = item.getReceivedBytes();
      record.entry.size = received > 0 ? received : item.getTotalBytes();
      record.complete = true;
    });
    return entry;
  }

  /** Completed downloads, oldest first. */
  list(): DownloadEntry[] {
    return [...this.records.values()]
      .filter((record) => record.complete)
      .sort((left, right) => left.seq - right.seq)
      .map((record) => ({ ...record.entry }));
  }

  async read(id: string): Promise<{ contentType: string; data: string }> {
    const record = this.records.get(id);
    if (!record?.complete) throw new Error("The download is not available");
    const info = await stat(record.path);
    if (info.size > this.maxReadBytes) throw new Error("The download is too large to read");
    const bytes = await this.readFileImpl(record.path);
    return {
      contentType: record.entry.contentType,
      data: Buffer.from(bytes).toString("base64"),
    };
  }

  /**
   * Snapshot the download counter before a page action that may trigger a
   * download. Pair with `settleWindow` to learn how many arrived afterwards.
   */
  openWindow(): string {
    return String(this.seq);
  }

  /** Resolve with the number of downloads completed after `token`. */
  async settleWindow(token: string, timeoutMs: number): Promise<number> {
    const from = Number(token);
    const deadline = Date.now() + Math.max(0, timeoutMs);
    while (true) {
      const count = this.countAfter(from);
      if (count > 0) return count;
      if (Date.now() >= deadline) return 0;
      await this.sleep(Math.min(SETTLE_POLL_MS, Math.max(1, deadline - Date.now())));
    }
  }

  private countAfter(seq: number): number {
    return [...this.records.values()].filter((record) => record.complete && record.seq > seq).length;
  }
}

/** Strip any path part and control characters from a download name. */
export function safeDownloadName(filename: string): string {
  const name = basename(filename).replace(/[\u0000-\u001f]/g, "").trim();
  return name || "download";
}

/**
 * Pick a name that does not overwrite an existing file: `report.pdf`,
 * `report (1).pdf`, `report (2).pdf`, …
 */
export function uniqueDownloadPath(directory: string, filename: string): string {
  const name = safeDownloadName(filename);
  const extension = extname(name);
  const stem = extension ? name.slice(0, -extension.length) : name;
  let candidate = join(directory, name);
  let counter = 1;
  while (existsSync(candidate)) {
    candidate = join(directory, `${stem} (${counter})${extension}`);
    counter += 1;
  }
  return candidate;
}
