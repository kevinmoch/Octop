import { describe, expect, it } from "vitest";
import {
  HOST_CAPABILITY_GROUPS,
  HOST_CAPABILITY_ITEM_COUNT,
} from "../../../../webskill/src/capabilityStatus";

describe("capability status projection", () => {
  it("keeps planned and unsupported ports from being presented as available", () => {
    const items = new Map(
      HOST_CAPABILITY_GROUPS.flatMap((group) =>
        group.items.map((item) => [item.id, item.state] as const),
      ),
    );
    expect(items.get("page-tabs")).toBe("available");
    expect(items.get("page-paging")).toBe("available");
    expect(items.get("pdf-input")).toBe("available");
    expect(items.get("downloads")).toBe("available");
    expect(items.get("remote-image")).toBe("unsupported");
    expect(items.get("web-office")).toBe("unsupported");
    expect(items.get("dwg")).toBe("unsupported");
    expect(items.get("native-skill-generation")).toBe("available");
    expect(HOST_CAPABILITY_ITEM_COUNT).toBeGreaterThan(10);
  });
});
