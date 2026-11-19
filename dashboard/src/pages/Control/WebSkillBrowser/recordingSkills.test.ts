// @vitest-environment jsdom
import { createHash, webcrypto } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopHostBridge } from "../../../../webskill/src/bridge";
import {
  canonicalJson,
  deleteRecordedSteps,
  parseRecordedReplay,
  parseRecordedSkillDraft,
  replayRecordedSteps,
  submitRecordedSkill,
} from "../../../../webskill/src/recordingSkills";

const draft = {
  name: "recorded-example-test",
  description: "Repeat a reviewed recording.",
  content:
    "---\nname: recorded-example-test\ndescription: Repeat a reviewed recording.\n---\n\n# Reviewed recording\n",
};

function envelope() {
  return {
    draft,
    provenance: {
      recordingId: "rec_1",
      sourceDigest: "a".repeat(64),
      draftDigest: createHash("sha256")
        .update(canonicalJson(draft))
        .digest("hex"),
      evidence: "recording",
    },
  };
}

describe("recording skill candidate boundary", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", webcrypto);
  });

  it("recomputes the digest and recursively sorts object keys", async () => {
    const value = envelope();
    const parsed = await parseRecordedSkillDraft(value);
    expect(parsed).toEqual({ draft, recordingId: "rec_1" });
    expect(canonicalJson({ z: 1, a: { b: [2, true], a: null } })).toBe(
      '{"a":{"a":null,"b":[2,true]},"z":1}',
    );
    expect(() => canonicalJson({ invalid: Number.NaN })).toThrow("Non-finite");
    expect(() => canonicalJson({ invalid: undefined })).toThrow("Only JSON");
    expect(() => canonicalJson({ invalid: new Date(0) })).toThrow("Only JSON");
  });

  it.each([
    ["steps", { ...envelope(), draft: { ...draft, steps: [] } }],
    ["unknown", { ...envelope(), extra: true }],
    [
      "files",
      {
        ...envelope(),
        draft: { ...draft, files: [{ path: "x", content: "x" }] },
      },
    ],
    [
      "digest",
      {
        ...envelope(),
        provenance: { ...envelope().provenance, draftDigest: "b".repeat(64) },
      },
    ],
  ])("rejects an untrusted %s field", async (_name, value) => {
    await expect(parseRecordedSkillDraft(value)).rejects.toMatchObject({
      code: "CANDIDATE_INVALID",
    });
  });

  it("submits only the verified draft after explicit confirmation", async () => {
    const call = vi.fn().mockResolvedValue(envelope());
    const request = vi.fn().mockResolvedValue({ value: true });
    const submit = vi.fn().mockResolvedValue({ id: "candidate-1" });
    const result = await submitRecordedSkill({
      bridge: { call } as unknown as OctopHostBridge,
      ui: { request },
      sink: { submit },
      locale: "zh",
    });

    expect(result).toEqual({ status: "submitted", candidateId: "candidate-1" });
    expect(call).toHaveBeenCalledWith("skill-draft", {});
    expect(request.mock.calls[0]?.[0].details.preview).toBe(draft.content);
    expect(submit).toHaveBeenCalledWith({
      draft,
      confirmedAt: expect.any(String),
    });
    expect(submit.mock.calls[0]?.[0]).not.toHaveProperty("sessionId");
    expect(JSON.stringify(submit.mock.calls[0]?.[0])).not.toContain(
      "sourceDigest",
    );
  });

  it("does not submit a declined candidate", async () => {
    const submit = vi.fn();
    const result = await submitRecordedSkill({
      bridge: {
        call: vi.fn().mockResolvedValue(envelope()),
      } as unknown as OctopHostBridge,
      ui: { request: vi.fn().mockResolvedValue({ cancelled: true }) },
      sink: { submit },
      locale: "en",
    });
    expect(result).toEqual({ status: "declined" });
    expect(submit).not.toHaveBeenCalled();
  });
});

describe("recording replay boundary", () => {
  const replay = {
    replayId: "replay_1",
    recordingId: "rec_1",
    status: "passed" as const,
    steps: [{ id: "step_1", kind: "click", status: "passed" }],
    startedAt: "2026-11-19T00:00:00Z",
    endedAt: "2026-11-19T00:01:00Z",
  };

  beforeEach(() => {
    vi.stubGlobal("crypto", webcrypto);
  });

  it("accepts only the public replay result shape", () => {
    expect(parseRecordedReplay(replay)).toEqual(replay);
    expect(() =>
      parseRecordedReplay({ ...replay, inputs: { secret: "x" } }),
    ).toThrow("invalid");
    expect(() =>
      parseRecordedReplay({ ...replay, reportPath: "/private/path" }),
    ).toThrow("invalid");
    expect(() => parseRecordedReplay({ ...replay, status: "failed" })).toThrow(
      "stable error code",
    );
    expect(() =>
      parseRecordedReplay({
        ...replay,
        steps: [{ id: "step_1", kind: "click", status: "running" }],
      }),
    ).toThrow("step is invalid");
  });

  it("requests one scope confirmation before calling the host", async () => {
    const request = vi.fn().mockResolvedValue({ value: true });
    const call = vi.fn().mockResolvedValue(replay);
    const result = await replayRecordedSteps({
      bridge: { call } as unknown as OctopHostBridge,
      ui: { request },
      locale: "zh",
    });
    expect(result).toEqual(replay);
    expect(call).toHaveBeenCalledWith("replay", {});
    expect(request.mock.calls[0]?.[0].message).toContain("每一步");
  });

  it("does not call the host when replay is declined", async () => {
    const call = vi.fn();
    const result = await replayRecordedSteps({
      bridge: { call } as unknown as OctopHostBridge,
      ui: { request: vi.fn().mockResolvedValue({ cancelled: true }) },
      locale: "en",
    });
    expect(result).toEqual({ status: "declined" });
    expect(call).not.toHaveBeenCalled();
  });
});

describe("recording deletion boundary", () => {
  it("deletes source data only after explicit confirmation", async () => {
    const call = vi
      .fn()
      .mockResolvedValue({ deleted: true, recordingId: "rec_1" });
    const request = vi.fn().mockResolvedValue({ value: true });
    const result = await deleteRecordedSteps({
      bridge: { call } as unknown as OctopHostBridge,
      ui: { request },
      locale: "zh",
    });
    expect(result).toEqual({ status: "deleted", recordingId: "rec_1" });
    expect(call).toHaveBeenCalledWith("recording-delete", {});
    expect(request.mock.calls[0]?.[0].message).toContain("已发布技能不受影响");
  });

  it("does not delete without confirmation and rejects malformed responses", async () => {
    const call = vi.fn();
    const declined = await deleteRecordedSteps({
      bridge: { call } as unknown as OctopHostBridge,
      ui: { request: vi.fn().mockResolvedValue({ cancelled: true }) },
      locale: "en",
    });
    expect(declined).toEqual({ status: "declined" });
    expect(call).not.toHaveBeenCalled();
    call.mockResolvedValue({ deleted: true, recordingId: "rec_1", inputs: {} });
    await expect(
      deleteRecordedSteps({
        bridge: { call } as unknown as OctopHostBridge,
        ui: { request: vi.fn().mockResolvedValue({ value: true }) },
        locale: "en",
      }),
    ).rejects.toMatchObject({ code: "TOOL_EXECUTION_FAILED" });
  });
});
