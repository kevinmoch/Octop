import { readFile, stat } from "node:fs/promises";
import { basename, extname } from "node:path";

/** A file the user picked for a page `attach` action. */
export interface PickedFile {
  name: string;
  type: string;
  /** Base64 of the file contents. */
  data: string;
}

export interface PickedFileDeps {
  stat?: (path: string) => Promise<{ size: number }>;
  readFile?: (path: string) => Promise<Uint8Array>;
}

export const MAX_PICKED_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_PICKED_TOTAL_BYTES = 28 * 1024 * 1024;

const MIME_BY_EXTENSION: Record<string, string> = {
  ".txt": "text/plain",
  ".csv": "text/csv",
  ".json": "application/json",
  ".pdf": "application/pdf",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".zip": "application/zip",
};

function mimeTypeFor(path: string): string {
  return MIME_BY_EXTENSION[extname(path).toLowerCase()] ?? "application/octet-stream";
}

/**
 * Read files the user picked in the native dialog for a page `attach` action.
 *
 * The bytes travel to the tab preload as base64, which turns them back into
 * `File` objects for the page. Limits mirror the chat attachment picker so a
 * page cannot receive more than the WebSkill UI would allow.
 */
export async function readPickedFiles(
  paths: readonly string[],
  deps: PickedFileDeps = {},
): Promise<PickedFile[]> {
  const statFile = deps.stat ?? stat;
  const readFileImpl = deps.readFile ?? readFile;
  let total = 0;
  const files: PickedFile[] = [];
  for (const path of paths) {
    const info = await statFile(path);
    if (info.size > MAX_PICKED_FILE_BYTES) {
      throw new Error(`${basename(path)} is larger than 10 MB`);
    }
    total += info.size;
    if (total > MAX_PICKED_TOTAL_BYTES) {
      throw new Error("The selected files are larger than 28 MB in total");
    }
    const bytes = await readFileImpl(path);
    files.push({
      name: basename(path),
      type: mimeTypeFor(path),
      data: Buffer.from(bytes).toString("base64"),
    });
  }
  return files;
}
