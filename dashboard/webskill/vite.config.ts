import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const browserPathShimSource = `{sep:"/",delimiter:":",cwd:function(){return "/"},normalize:function(p){if(!p)return ".";var a=p.charAt(0)==="/",t=p.length>1&&p.charAt(p.length-1)==="/",o=[];p.split("/").forEach(function(e){if(e&&e!=="."){if(e===".."){o.length&&o[o.length-1]!==".."?o.pop():o.push("..")}else o.push(e)}});var r=(a?"/":"")+o.join("/");return r||(r=a?"/":"."),t&&r!=="/"&&(r+="/"),r},basename:function(p,e){var t=p.replace(/\\/+$/,""),o=t.slice(t.lastIndexOf("/")+1);return e&&o.slice(-e.length)===e?o.slice(0,-e.length):o},dirname:function(p){var e=p.replace(/\\/+$/,""),t=e.lastIndexOf("/");return t<0?".":t===0?"/":e.slice(0,t)},extname:function(p){var e=this.basename(p),t=e.lastIndexOf(".");return t<=0?"":e.slice(t)},join:function(){return this.normalize(Array.prototype.slice.call(arguments).filter(Boolean).join("/"))},resolve:function(){return this.normalize(Array.prototype.slice.call(arguments).filter(Boolean).reverse().join("/"))},isAbsolute:function(p){return p.charAt(0)==="/"},relative:function(p,e){return e},parse:function(p){var e=this.basename(p),t=this.extname(p);return{root:p.charAt(0)==="/"?"/":"",dir:this.dirname(p),base:e,ext:t,name:t?e.slice(0,-t.length):e}},format:function(p){return (p.dir?p.dir+"/":"")+(p.base||((p.name||"")+(p.ext||"")))}}`;

function browserExternalPathShim(): Plugin {
  return {
    name: "webskill-browser-external-path",
    generateBundle(_options, bundle) {
      for (const [file, entry] of Object.entries(bundle)) {
        if (
          entry.type === "chunk" &&
          file.includes("__vite-browser-external")
        ) {
          entry.code = entry.code.replace(
            /(\bt\.exports\s*=\s*)\{\}/,
            `$1${browserPathShimSource}`,
          );
        }
      }
    },
  };
}

function browserPathShim(): Plugin {
  return {
    name: "webskill-browser-path",
    enforce: "pre",
    resolveId(source) {
      if (
        source === "path" ||
        source === "node:path" ||
        source === "path/posix"
      ) {
        return resolve(import.meta.dirname, "src/pathBoundary.ts");
      }
      return null;
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [browserPathShim(), react(), browserExternalPathShim()],
  resolve: {
    alias: [
      {
        find: /^buffer$/,
        replacement: resolve(
          import.meta.dirname,
          "node_modules/buffer/index.js",
        ),
      },
      {
        find: /^string_decoder$/,
        replacement: resolve(
          import.meta.dirname,
          "node_modules/string_decoder/lib/string_decoder.js",
        ),
      },
      {
        find: /^node:module$/,
        replacement: resolve(import.meta.dirname, "src/nodeModuleBoundary.ts"),
      },
      {
        find: /^node:path$/,
        replacement: resolve(import.meta.dirname, "src/pathBoundary.ts"),
      },
      {
        find: /^path$/,
        replacement: resolve(import.meta.dirname, "src/pathBoundary.ts"),
      },
    ],
  },
  build: {
    outDir: resolve(import.meta.dirname, "../../src/octop/dashboard/webskill"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
    rolldownOptions: {
      input: {
        index: resolve(import.meta.dirname, "index.html"),
        viewer: resolve(import.meta.dirname, "viewer/index.html"),
        sandbox: resolve(import.meta.dirname, "sandbox/index.html"),
      },
    },
  },
});
