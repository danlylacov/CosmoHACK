import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  publicDir: "public",
  server: {
    port: 5173,
    open: false,
    watch: {
      ignored: ["**/public/textures/**"],
    },
    proxy: {
      "/orbit-api": {
        target: "http://92.255.110.133:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/orbit-api/, ""),
      },
      "/eva-api": {
        target: "http://92.255.110.133:8001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/eva-api/, ""),
      },
      "/forecast-api": {
        target: "http://92.255.110.133:8002",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/forecast-api/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
