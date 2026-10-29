import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const root = resolve(__dirname, "screens");

/** manifest.json лежит рядом с исходниками второго пакета; кладём его в dist как есть, проверив форму. */
function manifest(): Plugin {
  return {
    name: "screens-manifest",
    generateBundle() {
      const source = readFileSync(resolve(root, "manifest.json"), "utf8");
      const parsed = JSON.parse(source) as Record<string, unknown>;
      if (parsed.manifest_version !== 3) this.error("manifest.json: нужен manifest_version 3");
      const permissions = (parsed.permissions as string[] | undefined) ?? [];
      for (const forbidden of ["scripting", "tabs", "<all_urls>"]) {
        if (permissions.includes(forbidden)) this.error(`manifest.json: право ${forbidden} расширению скриншотов не нужно`);
      }
      if (parsed.content_scripts || parsed.host_permissions) this.error("manifest.json: без content_scripts и постоянного доступа к сайтам");
      this.emitFile({ type: "asset", fileName: "manifest.json", source });
    },
  };
}

export default defineConfig({
  root,
  publicDir: false,
  plugins: [manifest()],
  build: {
    outDir: resolve(__dirname, "dist-screens"),
    emptyOutDir: true,
    sourcemap: false,
    // Полифил modulepreload трогает document — в общий с service worker чанк он попасть не должен.
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: {
        background: resolve(root, "src/background.ts"),
        popup: resolve(root, "popup.html"),
        options: resolve(root, "options.html"),
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
    setupFiles: ["test/setup.ts"],
  },
});
