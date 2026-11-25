export async function assertSecureRuntime(): Promise<void> {
  if (!window.isSecureContext) {
    throw new Error(
      "WebSkill requires HTTPS or localhost. Open the CCS Buddy dashboard in a secure context; plain remote HTTP is not supported.",
    );
  }
  if (
    !globalThis.crypto?.subtle ||
    typeof navigator.storage?.getDirectory !== "function"
  ) {
    throw new Error(
      "This browser does not provide WebCrypto and OPFS. Use a current browser with persistent storage enabled.",
    );
  }
  await Promise.all([
    navigator.storage.getDirectory(),
    crypto.subtle.digest("SHA-256", new Uint8Array()),
  ]);
}
