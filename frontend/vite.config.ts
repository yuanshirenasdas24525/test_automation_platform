import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";

// 后端 FastAPI 默认跑在 127.0.0.1:54351；如果改了端口请同步修改 VITE_API_TARGET 环境变量。
const API_TARGET = process.env.VITE_API_TARGET ?? "http://127.0.0.1:54351";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      // 所有 /api/** 请求代理到 FastAPI
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
      // Allure 报告静态资源（后端把它 mount 在 /reports）
      "/reports": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
