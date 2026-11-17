export async function configureMonaco(): Promise<void> {
  const [{ loader }, monaco] = await Promise.all([
    import("@monaco-editor/react"),
    import("monaco-editor"),
  ]);
  self.MonacoEnvironment = {
    getWorker: (_workerId, label) =>
      new Worker(
        new URL(
          label === "json"
            ? "../node_modules/monaco-editor/esm/vs/language/json/json.worker.js"
            : "../node_modules/monaco-editor/esm/vs/editor/editor.worker.js",
          import.meta.url,
        ),
        { type: "module" },
      ),
  };
  loader.config({ monaco });
}
