import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

// ESLint v9 flat config。
// 之前仓库里没有任何 eslint 配置文件，lint 脚本直接报错跑不起来；这份补齐 TS 解析器 +
// React Hooks / React Refresh 规则，对齐 Vite 官方 React-TS 模板。
export default tseslint.config(
  // 不检查构建产物
  { ignores: ["dist"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // 仅关乎 Vite HMR 开发体验、与运行时正确性无关；本仓库有意在若干文件里
      // 同时导出组件与常量/helper（如 ui/button.tsx 的 buttonVariants），故关闭。
      "react-refresh/only-export-components": "off",
      // 下划线前缀的变量/参数视为“有意未使用”，不报错（约定俗成）
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      // 存量代码里有少量务实的 any；保留为告警信号，但不阻塞 CI
      "@typescript-eslint/no-explicit-any": "warn",
    },
  },
);
