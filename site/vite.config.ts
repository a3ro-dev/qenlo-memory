import { defineConfig } from "vite"
import solid from "vite-plugin-solid"
import tailwindcss from "@tailwindcss/vite"
import { fileURLToPath } from "node:url"

// served from https://a3ro-dev.github.io/qenlo-memory/
export default defineConfig({
  base: "/qenlo-memory/",
  plugins: [solid(), tailwindcss()],
  resolve: { alias: { "@dither-kit": fileURLToPath(new URL("./src/dither-kit/solid", import.meta.url)) } },
  build: { target: "es2022", outDir: "dist", assetsInlineLimit: 0 },
})
