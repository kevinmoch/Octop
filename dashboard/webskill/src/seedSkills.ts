/**
 * Built-in skill seeding.
 *
 * The four content-authoring skills ship as plain files under
 * `webskill/skills/builtin/` and are fetched at first run, then written into
 * OPFS under `/builtin` - one of the skill roots the host hands to discovery.
 *
 * Seeding is content-addressed: a file whose fingerprint matches the last seed
 * is left alone, so a user's edit of a built-in skill survives while files that
 * changed in a release are replaced. A skill the user deleted is remembered as
 * a tombstone and is not resurrected by the next update.
 */

/** Must be one of the host's skill roots; `/skills/builtin` would nest wrongly. */
export const BUILTIN_ROOT = "/builtin";

/** The pre-0.20 layout carried an extra directory with no SKILL.md. */
const LEGACY_ROOT = "/skills/builtin";
const LEGACY_STAMP = `${BUILTIN_ROOT}/.seeded-v1`;
const STATE_PATH = `${BUILTIN_ROOT}/.seed-state.json`;

/** Directory -> files. The extension host has no directory listing primitive. */
const MANIFEST: Record<string, readonly string[]> = {
  "authored-bulletin": [
    "SKILL.md",
    "references/authoring.md",
    "scripts/publish.js",
  ],
  "authored-screen": [
    "SKILL.md",
    "references/authoring.md",
    "scripts/publish.js",
  ],
  "authored-slides": [
    "SKILL.md",
    "references/authoring.md",
    "scripts/publish.js",
  ],
  "dwg-view": ["SKILL.md"],
};

interface SeedState {
  /** key = `${skill}/${file}` */
  files: Record<string, string>;
  tombstones: string[];
}

/** The slice of `FileSystemProvider` seeding needs. */
export interface SkillSeedFs {
  exists(path: string): Promise<boolean>;
  readText(path: string): Promise<string>;
  writeText(path: string, content: string): Promise<void>;
  mkdir(path: string): Promise<void>;
  remove(path: string, options?: { recursive?: boolean }): Promise<void>;
}

export type SkillAssetResolver = (path: string) => string;

/** Resolve against the iframe document so `/webskill/skills/...` is fetched. */
function resolveAsset(path: string): string {
  return new URL(path, document.baseURI).toString();
}

/** Cheap content fingerprint; only used to detect changes, not integrity. */
function fingerprint(text: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `${hash.toString(16)}-${text.length}`;
}

async function readState(fs: SkillSeedFs): Promise<SeedState> {
  if (await fs.exists(LEGACY_STAMP)) {
    await fs.remove(LEGACY_STAMP).catch(() => undefined);
    return { files: {}, tombstones: [] };
  }
  if (!(await fs.exists(STATE_PATH))) return { files: {}, tombstones: [] };
  try {
    const parsed: unknown = JSON.parse(await fs.readText(STATE_PATH));
    const record = parsed as Partial<SeedState>;
    return {
      files:
        typeof record.files === "object" && record.files !== null
          ? (record.files as Record<string, string>)
          : {},
      tombstones: Array.isArray(record.tombstones)
        ? record.tombstones.filter((id): id is string => typeof id === "string")
        : [],
    };
  } catch {
    // A corrupt ledger just means: seed everything again.
    return { files: {}, tombstones: [] };
  }
}

async function fetchAsset(
  resolve: SkillAssetResolver,
  skill: string,
  file: string,
): Promise<string> {
  const url = resolve(`skills/builtin/${skill}/${file}`);
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(
      `Builtin skill asset missing: ${url} (HTTP ${response.status})`,
    );
  }
  const text = await response.text();
  if (file === "SKILL.md" && !text.startsWith("---")) {
    throw new Error(`Builtin skill asset is not a SKILL.md contract: ${url}`);
  }
  return text;
}

/**
 * Copy the bundled skills into OPFS. Returns true when anything changed, so a
 * caller can refresh an already-running skill index.
 */
export async function seedBuiltinSkills(
  fs: SkillSeedFs,
  resolve: SkillAssetResolver = resolveAsset,
): Promise<boolean> {
  await fs.remove(LEGACY_ROOT, { recursive: true }).catch(() => undefined);
  const state = await readState(fs);
  const next: SeedState = { files: {}, tombstones: [...state.tombstones] };
  let changed = false;

  for (const [skill, files] of Object.entries(MANIFEST)) {
    if (next.tombstones.includes(skill)) continue;
    const root = `${BUILTIN_ROOT}/${skill}`;
    const seededBefore = Object.keys(state.files).some((key) =>
      key.startsWith(`${skill}/`),
    );
    // Seeded before, SKILL.md gone now: the user deleted it. Remember that
    // instead of bringing it back on the next release.
    if (seededBefore && !(await fs.exists(`${root}/SKILL.md`))) {
      next.tombstones.push(skill);
      await fs.remove(root, { recursive: true }).catch(() => undefined);
      changed = true;
      continue;
    }

    await fs.mkdir(root).catch(() => undefined);
    for (const file of files) {
      const text = await fetchAsset(resolve, skill, file);
      const key = `${skill}/${file}`;
      const digest = fingerprint(text);
      next.files[key] = digest;
      if (state.files[key] === digest && (await fs.exists(`${root}/${file}`)))
        continue;
      await fs.writeText(`${root}/${file}`, text);
      changed = true;
    }

    // Drop files this release no longer ships (stale scripts still registered
    // as tools otherwise). Only files this seeder wrote are considered.
    for (const key of Object.keys(state.files)) {
      if (!key.startsWith(`${skill}/`) || key in next.files) continue;
      await fs.remove(`${BUILTIN_ROOT}/${key}`).catch(() => undefined);
      changed = true;
    }
  }

  for (const key of Object.keys(state.files)) {
    const skill = key.slice(0, key.indexOf("/"));
    if (skill in MANIFEST) continue;
    await fs
      .remove(`${BUILTIN_ROOT}/${skill}`, { recursive: true })
      .catch(() => undefined);
    changed = true;
  }

  const serialized = JSON.stringify(next);
  if (serialized !== JSON.stringify(state))
    await fs.writeText(STATE_PATH, serialized);
  return changed;
}
