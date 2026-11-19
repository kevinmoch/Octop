import {
  WebSkillError,
  isValidSkillName,
  parseSkillMarkdown,
} from "@webskill/sdk";
import type {
  GeneratedSkillDraft,
  PageActionUi,
  SkillCandidateSink,
} from "@webskill/sdk/agent";
import { OctopHostBridge, isRecord } from "./bridge";

const MAX_CONTENT_BYTES = 65536;
const DIGEST = /^[a-f0-9]{64}$/;

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "boolean")
    return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value))
      throw new Error("Non-finite numbers are not valid JSON");
    return JSON.stringify(value);
  }
  if (Array.isArray(value))
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  if (isRecord(value)) {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null)
      throw new Error("Only JSON values can be canonicalized");
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  throw new Error("Only JSON values can be canonicalized");
}

function invalid(message: string): never {
  throw new WebSkillError("CANDIDATE_INVALID", message);
}

function assertKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
): void {
  const allowed = new Set([...required, ...optional]);
  if (
    required.some((key) => !(key in value)) ||
    Object.keys(value).some((key) => !allowed.has(key))
  )
    invalid("The recording skill response contains unknown or missing fields");
}

async function sha256(value: string): Promise<string> {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(bytes), (part) =>
    part.toString(16).padStart(2, "0"),
  ).join("");
}

export async function parseRecordedSkillDraft(
  value: unknown,
): Promise<{ draft: GeneratedSkillDraft; recordingId: string }> {
  try {
    if (!isRecord(value))
      invalid("The recording skill response is not an object");
    assertKeys(value, ["draft", "provenance"]);
    if (!isRecord(value.draft) || !isRecord(value.provenance))
      invalid("The recording skill response is malformed");
    const raw = value.draft;
    const provenance = value.provenance;
    assertKeys(raw, ["name", "description", "content"], ["files"]);
    assertKeys(provenance, [
      "recordingId",
      "sourceDigest",
      "draftDigest",
      "evidence",
    ]);
    if (
      typeof raw.name !== "string" ||
      !isValidSkillName(raw.name) ||
      typeof raw.description !== "string" ||
      raw.description.trim() === "" ||
      raw.description.length > 2048 ||
      typeof raw.content !== "string" ||
      new TextEncoder().encode(raw.content).length > MAX_CONTENT_BYTES
    )
      invalid("The recording skill draft is invalid or too large");
    if (raw.files !== undefined) {
      if (!Array.isArray(raw.files) || raw.files.length !== 0)
        invalid("A fixed recording template cannot contain additional files");
    }
    const metadata = parseSkillMarkdown(raw.content).metadata;
    if (metadata.name !== raw.name || metadata.description !== raw.description)
      invalid("The recording skill frontmatter does not match its draft");
    if (
      typeof provenance.recordingId !== "string" ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(provenance.recordingId) ||
      typeof provenance.sourceDigest !== "string" ||
      !DIGEST.test(provenance.sourceDigest) ||
      typeof provenance.draftDigest !== "string" ||
      !DIGEST.test(provenance.draftDigest) ||
      provenance.evidence !== "recording"
    )
      invalid("The recording skill provenance is invalid");
    const draft: GeneratedSkillDraft = {
      name: raw.name,
      description: raw.description,
      content: raw.content,
    };
    if ((await sha256(canonicalJson(draft))) !== provenance.draftDigest)
      invalid("The recording skill draft digest does not match");
    return { draft, recordingId: provenance.recordingId };
  } catch (error) {
    if (error instanceof WebSkillError) throw error;
    invalid(
      `The recording skill draft could not be validated: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
}

export async function submitRecordedSkill(input: {
  bridge: OctopHostBridge;
  ui: PageActionUi | undefined;
  sink: SkillCandidateSink;
  locale: "zh" | "en";
  recordingId?: string;
}): Promise<
  { status: "submitted"; candidateId: string } | { status: "declined" }
> {
  if (!input.ui)
    throw new WebSkillError(
      "SKILL_GENERATION_DISABLED",
      "The assistant confirmation interface is not available",
    );
  const envelope = await input.bridge.call("skill-draft", {
    ...(input.recordingId !== undefined
      ? { recordingId: input.recordingId }
      : {}),
  });
  const { draft, recordingId } = await parseRecordedSkillDraft(envelope);
  const response = await input.ui.request({
    type: "authorize",
    id: `recording-skill-${crypto.randomUUID()}`,
    capability: "confirm",
    message:
      input.locale === "zh"
        ? `将人工录制 ${recordingId} 保存为候选技能“${draft.name}”？它不会自动启用，请检查完整预览。`
        : `Save recording ${recordingId} as candidate skill "${draft.name}"? It will not be activated; review the complete preview.`,
    details: { preview: draft.content },
  });
  if (response.cancelled === true || response.value === false)
    return { status: "declined" };
  const { id } = await input.sink.submit({
    draft,
    confirmedAt: new Date().toISOString(),
  });
  return { status: "submitted", candidateId: id };
}

export interface RecordedReplayResult {
  replayId: string;
  recordingId: string;
  status: "passed" | "failed";
  steps: readonly { id: string; kind: string; status: string }[];
  startedAt?: string;
  endedAt?: string;
  errorCode?: "REPLAY_FAILED";
}

function invalidReplay(message: string): never {
  throw new WebSkillError("TOOL_EXECUTION_FAILED", message);
}

export function parseRecordedReplay(value: unknown): RecordedReplayResult {
  if (!isRecord(value)) invalidReplay("The replay response is not an object");
  const allowed = new Set([
    "replayId",
    "recordingId",
    "status",
    "steps",
    "startedAt",
    "endedAt",
    "errorCode",
  ]);
  if (
    Object.keys(value).some((key) => !allowed.has(key)) ||
    typeof value.replayId !== "string" ||
    !/^[A-Za-z0-9_-]{1,128}$/.test(value.replayId) ||
    typeof value.recordingId !== "string" ||
    !/^[A-Za-z0-9_-]{1,128}$/.test(value.recordingId) ||
    (value.status !== "passed" && value.status !== "failed") ||
    !Array.isArray(value.steps) ||
    value.steps.length > 200
  )
    invalidReplay("The replay response is invalid");
  for (const [field, timestamp] of [
    ["startedAt", value.startedAt],
    ["endedAt", value.endedAt],
  ] as const) {
    if (
      timestamp !== undefined &&
      (typeof timestamp !== "string" ||
        timestamp.length > 64 ||
        Number.isNaN(Date.parse(timestamp)))
    )
      invalidReplay(`The replay ${field} is invalid`);
  }
  if (value.status === "failed" && value.errorCode !== "REPLAY_FAILED")
    invalidReplay("A failed replay must have a stable error code");
  if (value.status === "passed" && value.errorCode !== undefined)
    invalidReplay("A passed replay cannot have an error code");
  const steps = value.steps.map((step) => {
    if (!isRecord(step)) invalidReplay("The replay step is invalid");
    if (
      Object.keys(step).some(
        (key) => !["id", "kind", "status"].includes(key),
      ) ||
      typeof step.id !== "string" ||
      step.id.length > 128 ||
      typeof step.kind !== "string" ||
      step.kind.length > 64 ||
      typeof step.status !== "string" ||
      !["passed", "failed", "skipped"].includes(step.status)
    )
      invalidReplay("The replay step is invalid");
    return { id: step.id, kind: step.kind, status: step.status };
  });
  return {
    replayId: value.replayId,
    recordingId: value.recordingId,
    status: value.status,
    steps,
    ...(typeof value.startedAt === "string"
      ? { startedAt: value.startedAt }
      : {}),
    ...(typeof value.endedAt === "string" ? { endedAt: value.endedAt } : {}),
    ...(value.errorCode === "REPLAY_FAILED"
      ? { errorCode: value.errorCode }
      : {}),
  };
}

export async function replayRecordedSteps(input: {
  bridge: OctopHostBridge;
  ui: PageActionUi | undefined;
  locale: "zh" | "en";
  recordingId?: string;
}): Promise<RecordedReplayResult | { status: "declined" }> {
  if (!input.ui)
    throw new WebSkillError(
      "TOOL_EXECUTION_FAILED",
      "The assistant confirmation interface is not available",
    );
  const response = await input.ui.request({
    type: "authorize",
    id: `recording-replay-${crypto.randomUUID()}`,
    capability: "confirm",
    message:
      input.locale === "zh"
        ? "回放最近一段已停止的录制？本次授权只覆盖该录制的来源、目标和动作范围，每一步仍需服务端授权；接管或取消后不会继续后续步骤。"
        : "Replay the latest stopped recording? This authorization is limited to its origins, targets and actions. Each step still needs a server grant; takeover or cancellation stops subsequent steps.",
    details: { source: "recording" },
  });
  if (response.cancelled === true || response.value === false)
    return { status: "declined" };
  const result = await input.bridge.call("replay", {
    ...(input.recordingId !== undefined
      ? { recordingId: input.recordingId }
      : {}),
  });
  return parseRecordedReplay(result);
}

export async function deleteRecordedSteps(input: {
  bridge: OctopHostBridge;
  ui: PageActionUi | undefined;
  locale: "zh" | "en";
  recordingId?: string;
}): Promise<
  { status: "deleted"; recordingId: string } | { status: "declined" }
> {
  if (!input.ui)
    throw new WebSkillError(
      "TOOL_EXECUTION_FAILED",
      "The assistant confirmation interface is not available",
    );
  const response = await input.ui.request({
    type: "authorize",
    id: `recording-delete-${crypto.randomUUID()}`,
    capability: "confirm",
    message:
      input.locale === "zh"
        ? "永久删除最近一段录制的原始步骤、输入、截图和回放报告？已发布技能不受影响。删除后不能回放或转换该录制。"
        : "Permanently delete the latest recording's source steps, inputs, screenshots and replay reports? Published skills are not affected. The recording cannot be replayed or converted after deletion.",
    details: { source: "recording" },
  });
  if (response.cancelled === true || response.value === false)
    return { status: "declined" };
  const result = await input.bridge.call("recording-delete", {
    ...(input.recordingId !== undefined
      ? { recordingId: input.recordingId }
      : {}),
  });
  if (
    !isRecord(result) ||
    Object.keys(result).some(
      (key) => !["deleted", "recordingId"].includes(key),
    ) ||
    result.deleted !== true ||
    typeof result.recordingId !== "string" ||
    !/^[A-Za-z0-9_-]{1,128}$/.test(result.recordingId)
  )
    throw new WebSkillError(
      "TOOL_EXECUTION_FAILED",
      "The recording deletion response is invalid",
    );
  return { status: "deleted", recordingId: result.recordingId };
}
