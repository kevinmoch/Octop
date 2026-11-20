/**
 * Browser-safe POSIX path shim for browser bundles.
 *
 * The WebSkill SDK's Markdown pipeline constructs a vfile. vfile's path
 * helpers call `path.cwd()`; Vite's default `__vite-browser-external` stub
 * omits that method and the Chatbot crashes while rendering a message. Keep
 * the real Node API out of the browser but provide the stable path methods
 * used by renderers.
 */
function split(path: string): string[] {
  return path.split("/");
}

function normalizeParts(parts: string[]): string[] {
  const out: string[] = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (out.length > 0 && out[out.length - 1] !== "..") out.pop();
      else out.push("..");
      continue;
    }
    out.push(part);
  }
  return out;
}

function normalize(path: string): string {
  if (!path) return ".";
  const absolute = path.startsWith("/");
  const trailing = path.length > 1 && path.endsWith("/");
  const parts = normalizeParts(split(path));
  let result = (absolute ? "/" : "") + parts.join("/");
  if (!result) result = absolute ? "/" : ".";
  if (trailing && result !== "/") result += "/";
  return result;
}

function basename(path: string, ext?: string): string {
  const clean = path.replace(/\/+$/, "");
  const base = clean.slice(clean.lastIndexOf("/") + 1);
  if (ext && base.endsWith(ext)) return base.slice(0, -ext.length);
  return base;
}

function dirname(path: string): string {
  const clean = path.replace(/\/+$/, "");
  const index = clean.lastIndexOf("/");
  if (index < 0) return ".";
  if (index === 0) return "/";
  return clean.slice(0, index);
}

function extname(path: string): string {
  const base = basename(path);
  const index = base.lastIndexOf(".");
  return index <= 0 ? "" : base.slice(index);
}

function join(...parts: string[]): string {
  return normalize(parts.filter((part) => part !== "").join("/")) || ".";
}

function resolve(...parts: string[]): string {
  let result = "";
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    const part = parts[index];
    if (!part) continue;
    result = result ? `${part}/${result}` : part;
    if (part.startsWith("/")) break;
  }
  return normalize(result || "/");
}

function isAbsolute(path: string): boolean {
  return path.startsWith("/");
}

function relative(from: string, to: string): string {
  const left = normalizeParts(split(normalize(from)));
  const right = normalizeParts(split(normalize(to)));
  let common = 0;
  while (
    common < left.length &&
    common < right.length &&
    left[common] === right[common]
  ) {
    common += 1;
  }
  return [
    ...Array(left.length - common).fill(".."),
    ...right.slice(common),
  ].join("/");
}

function parse(path: string) {
  const root = path.startsWith("/") ? "/" : "";
  const dir = dirname(path);
  const base = basename(path);
  const ext = extname(path);
  return {
    root,
    dir: dir === "." ? "" : dir,
    base,
    ext,
    name: ext ? base.slice(0, -ext.length) : base,
  };
}

function format(parsed: {
  dir?: string;
  root?: string;
  base?: string;
  name?: string;
  ext?: string;
}): string {
  const base = parsed.base ?? `${parsed.name ?? ""}${parsed.ext ?? ""}`;
  if (parsed.dir) return join(parsed.dir, base);
  if (parsed.root) return `${parsed.root}${base}`;
  return base;
}

const path = {
  sep: "/",
  delimiter: ":",
  cwd: () => "/",
  normalize,
  basename,
  dirname,
  extname,
  join,
  resolve,
  isAbsolute,
  relative,
  parse,
  format,
  posix: undefined as unknown,
  win32: undefined as unknown,
};
path.posix = path;
path.win32 = path;

export default path;
export {
  basename,
  dirname,
  extname,
  format,
  isAbsolute,
  join,
  normalize,
  parse,
  relative,
  resolve,
};
