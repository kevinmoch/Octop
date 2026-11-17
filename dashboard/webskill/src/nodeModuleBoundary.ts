import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as JsxRuntime from "react/jsx-runtime";

const trustedPeers: Record<string, unknown> = {
  react: React,
  "react-dom": ReactDOM,
  "react-dom/client": ReactDOMClient,
  "react/jsx-runtime": JsxRuntime,
};

function unsupported(id: string): never {
  throw new Error(
    `Module "${id}" is not available in the Octop WebSkill browser host`,
  );
}

/** Only the pinned React 19 peers may cross the SDK's generated CommonJS boundary. */
export function createRequire(): (id: string) => unknown {
  return (id) =>
    Object.hasOwn(trustedPeers, id) ? trustedPeers[id] : unsupported(id);
}

export function register(): never {
  return unsupported("module hooks");
}
