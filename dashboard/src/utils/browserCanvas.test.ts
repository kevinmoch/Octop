import { describe, expect, it } from "vitest";
import { getCanvasCoords } from "./browserCanvas";

function fakeCanvas(
  width: number,
  height: number,
  rect: DOMRect,
): HTMLCanvasElement {
  return {
    width,
    height,
    getBoundingClientRect: () => rect,
  } as unknown as HTMLCanvasElement;
}

describe("getCanvasCoords", () => {
  it("maps a letterboxed canvas by its displayed contain box", () => {
    // 1440x900 image inside a 1362x1053 box: displayed 1362x851, offset 101.
    const rect = {
      left: 100,
      top: 50,
      width: 1362,
      height: 1053,
    } as DOMRect;
    const canvas = fakeCanvas(1440, 900, rect);
    const point = getCanvasCoords(canvas, {
      clientX: 100 + 1362 / 2,
      clientY: 50 + 1053 / 2,
    });
    expect(point.x).toBe(720);
    expect(point.y).toBe(450);
    const topLeft = getCanvasCoords(canvas, {
      clientX: 100,
      clientY: 50 + 101,
    });
    expect(topLeft.x).toBe(0);
    expect(topLeft.y).toBe(0);
  });

  it("clamps pointer positions in the letterbox bars", () => {
    const canvas = fakeCanvas(100, 100, {
      left: 0,
      top: 0,
      width: 200,
      height: 100,
    } as DOMRect);
    expect(getCanvasCoords(canvas, { clientX: 10, clientY: 50 })).toEqual({
      x: 0,
      y: 50,
    });
    expect(getCanvasCoords(canvas, { clientX: 190, clientY: 50 })).toEqual({
      x: 100,
      y: 50,
    });
  });
});
