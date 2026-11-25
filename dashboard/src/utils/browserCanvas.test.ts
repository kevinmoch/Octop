import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCanvasCoords, paintBase64JpegToCanvas } from "./browserCanvas";

class FakeImage {
  static instances: FakeImage[] = [];
  width = 0;
  height = 0;
  src = "";
  onload: (() => void) | null = null;

  constructor() {
    FakeImage.instances.push(this);
  }
}

beforeEach(() => {
  FakeImage.instances = [];
  vi.stubGlobal("Image", FakeImage);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

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

function fakePaintCanvas() {
  const drawImage = vi.fn();
  const context = {
    drawImage,
    imageSmoothingEnabled: false,
    imageSmoothingQuality: "high",
  };
  const canvas = {
    width: 300,
    height: 150,
    getContext: () => context,
  } as unknown as HTMLCanvasElement;
  return { canvas, drawImage };
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

describe("paintBase64JpegToCanvas", () => {
  it("does not let an older decode overwrite the latest frame", () => {
    const { canvas, drawImage } = fakePaintCanvas();
    paintBase64JpegToCanvas(canvas, "old", { frameId: 1, streamId: 1 });
    paintBase64JpegToCanvas(canvas, "new", { frameId: 2, streamId: 1 });
    const [oldImage, newImage] = FakeImage.instances;
    oldImage!.width = 160;
    oldImage!.height = 90;
    newImage!.width = 160;
    newImage!.height = 90;

    newImage!.onload?.();
    oldImage!.onload?.();

    expect(drawImage).toHaveBeenCalledTimes(1);
    expect(drawImage.mock.calls[0]?.[0]).toBe(newImage);
  });

  it("keeps the canvas size when only the screenshot pixel ratio changes", () => {
    const { canvas, drawImage } = fakePaintCanvas();
    paintBase64JpegToCanvas(canvas, "1x", { frameId: 1, streamId: 1 });
    const first = FakeImage.instances[0]!;
    first.width = 160;
    first.height = 90;
    first.onload?.();

    paintBase64JpegToCanvas(canvas, "2x", { frameId: 2, streamId: 1 });
    const second = FakeImage.instances[1]!;
    second.width = 320;
    second.height = 180;
    second.onload?.();

    expect(canvas.width).toBe(160);
    expect(canvas.height).toBe(90);
    expect(drawImage).toHaveBeenLastCalledWith(second, 0, 0, 160, 90);
  });
});
