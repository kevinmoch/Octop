export interface CanvasPoint {
  x: number;
  y: number;
}

export interface BrowserFrameMetadata {
  frameId?: number;
  streamId?: number;
}

interface CanvasFrameState {
  generation: number;
  lastFrameId: number;
  hasFrame: boolean;
  streamId?: number;
}

const canvasFrameStates = new WeakMap<HTMLCanvasElement, CanvasFrameState>();

/** Map a screen pointer position to canvas pixel coordinates. */
export function getCanvasCoords(
  canvas: HTMLCanvasElement | null,
  e: { clientX: number; clientY: number },
): CanvasPoint {
  if (!canvas) return { x: 0, y: 0 };
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };
  // The canvas is rendered with object-fit: contain. Its CSS box can have a
  // different aspect ratio from the streamed JPEG, so mapping by independent
  // X/Y scales sends hover and drag coordinates to the wrong remote pixel
  // (notably table headers, filter icons and horizontal scrollbars).
  const scale = Math.min(
    rect.width / canvas.width,
    rect.height / canvas.height,
  );
  const displayWidth = canvas.width * scale;
  const displayHeight = canvas.height * scale;
  const offsetX = (rect.width - displayWidth) / 2;
  const offsetY = (rect.height - displayHeight) / 2;
  const x = Math.min(
    canvas.width,
    Math.max(0, (e.clientX - rect.left - offsetX) / scale),
  );
  const y = Math.min(
    canvas.height,
    Math.max(0, (e.clientY - rect.top - offsetY) / scale),
  );
  return { x: Math.round(x), y: Math.round(y) };
}

/** Paint a JPEG base64 frame onto a canvas (WebSocket stream). */
export function paintBase64JpegToCanvas(
  canvas: HTMLCanvasElement | null,
  base64Data: string,
  metadata: BrowserFrameMetadata = {},
): void {
  if (!canvas) return;
  const previous = canvasFrameStates.get(canvas);
  const streamChanged =
    metadata.streamId !== undefined && metadata.streamId !== previous?.streamId;
  if (
    !streamChanged &&
    metadata.frameId !== undefined &&
    metadata.frameId <= (previous?.lastFrameId ?? 0)
  ) {
    return;
  }
  const state: CanvasFrameState = {
    generation: (previous?.generation ?? 0) + 1,
    lastFrameId:
      metadata.frameId ?? (streamChanged ? 0 : previous?.lastFrameId ?? 0),
    hasFrame: streamChanged ? false : previous?.hasFrame ?? false,
    streamId: metadata.streamId ?? previous?.streamId,
  };
  canvasFrameStates.set(canvas, state);

  const img = new Image();
  img.onload = () => {
    if (canvasFrameStates.get(canvas) !== state) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const nextRatio = img.width / img.height;
    const currentRatio = canvas.width / canvas.height;
    const ratioChanged = Math.abs(1 - currentRatio / nextRatio) > 0.005;
    if (!state.hasFrame || ratioChanged) {
      canvas.width = img.width;
      canvas.height = img.height;
    }
    ctx.imageSmoothingEnabled = true;
    if ("imageSmoothingQuality" in ctx) {
      ctx.imageSmoothingQuality = "high";
    }
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    state.hasFrame = true;
  };
  img.src = `data:image/jpeg;base64,${base64Data}`;
}

/** Paint a screenshot blob onto a canvas (HTTP polling). */
export async function paintBlobToCanvas(
  canvas: HTMLCanvasElement | null,
  blob: Blob,
): Promise<boolean> {
  if (!canvas) return false;
  const bitmap = await createImageBitmap(blob);
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0);
  bitmap.close();
  return true;
}

export function clearCanvas(canvas: HTMLCanvasElement | null): void {
  if (!canvas) return;
  const previous = canvasFrameStates.get(canvas);
  canvasFrameStates.set(canvas, {
    generation: (previous?.generation ?? 0) + 1,
    lastFrameId: 0,
    hasFrame: false,
  });
  const ctx = canvas.getContext("2d");
  if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
}
