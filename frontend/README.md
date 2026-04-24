# frontend（React 重写版）

老的 `client/` 里那套 jQuery + CDN 拼出来的页面维护起来太难受，这里用 **Vite + React 19 + TypeScript + Tailwind + shadcn/ui** 重写。后端接口不变（依旧是 FastAPI 在 `127.0.0.1:54351`），只是界面走新的这套。

---

## MVP 范围（当前版本）

- 脚手架：Vite / SWC / Tailwind / shadcn 组件库（Button、Dialog、Tabs、Select、DropdownMenu 等已内嵌到 `src/components/ui/`）
- 全局：TanStack Query + React Router + Sonner（toast）
- 页面：
  - `/`　　　　 工作台首页（跳板卡片）
  - `/projects`  项目管理（**增删改查**，按 api / web / app 分 Tab）
  - `/runs`、`/devices`、`/config`：占位页，后续迭代再实装

其它页面（模块树 / 用例编辑器 / 执行 / 报告 / 配置中心）暂未实现。

---

## 快速开始

```bash
cd frontend
npm install
npm run dev           # 默认在 5173
```

后端需要先跑起来：

```bash
# 在项目根目录
python main.py        # 监听 127.0.0.1:54351
```

Vite dev server 已经配了 `/api` 和 `/reports` 两个反向代理，把请求打到后端上，不会有 CORS 问题。想改后端地址可以设环境变量：

```bash
VITE_API_TARGET=http://192.168.1.10:54351 npm run dev
```

---

## 常用脚本

| 命令 | 说明 |
| --- | --- |
| `npm run dev` | 起 Vite dev server（带 HMR + `/api` 代理） |
| `npm run build` | `tsc -b` 类型检查 + Vite 打包，产物在 `dist/` |
| `npm run preview` | 本地预览 `dist/` 构建产物 |
| `npm run typecheck` | 只做 TS 类型检查，不打包 |
| `npm run lint` | ESLint（零警告要求） |

---

## 目录结构

```
frontend/
├── index.html             # Vite 入口 HTML
├── src/
│   ├── main.tsx           # ReactDOM 挂载 + QueryClientProvider + RouterProvider + Toaster
│   ├── routes.tsx         # react-router 路由表
│   ├── index.css          # Tailwind 指令 + shadcn CSS 变量
│   ├── components/
│   │   ├── AppLayout.tsx  # 侧边栏 + <Outlet/>
│   │   └── ui/            # 内嵌的 shadcn 基础组件
│   ├── pages/
│   │   ├── HomePage.tsx
│   │   └── ProjectsPage.tsx
│   ├── lib/
│   │   ├── api.ts         # fetch 封装（自动拆 {status,data,message} 信封）
│   │   ├── query.ts       # QueryClient + queryKeys 工厂
│   │   └── utils.ts       # cn() 合并 tailwind class
│   └── types/
│       └── domain.ts      # 与后端对齐的领域类型
├── tailwind.config.js
├── tsconfig.*.json
└── vite.config.ts         # 路径别名 @/* → src/* + /api 代理
```

---

## 跟后端一起部署

`npm run build` 产出 `frontend/dist/`。想让 FastAPI 一起把前端也托管了，往 `main.py` 里加：

```python
from fastapi.staticfiles import StaticFiles

app.mount(
    "/",
    StaticFiles(directory="frontend/dist", html=True),
    name="frontend",
)
```

注意 **这一行要放在所有 `@app.xxx(...)` 路由注册之后**，否则 `/` 会被 StaticFiles 抢走把 `/api/*` 也盖掉。

---

## 约定 & 坑位备忘

- 后端对 `name`/`description` 有硬长度限制（10 / 50 字），ProjectsPage 的 zod schema 已经对齐。
- 后端 `POST /api/projects` 里 `len(description)` 不判空，所以**必须传字符串**（表单默认空串）。
- `/api/projects/list` 有聚合字段（`case_count`、`pass_rate`、`last_status`、`last_run_time`），单条详情接口没有——UI 上别混用。
- 信封格式：绝大多数接口 `{status, data, message}`，少数（如 `/api/content/{id}`）直接裸返回数组。`lib/api.ts` 两种都能处理。

---

## 下一步路线图

- 项目详情页：模块树 + 用例列表
- 用例编辑器：请求配置 / 步骤 / 断言 / 关联
- 执行 & 报告：触发 run、轮询状态、查看 Allure
- 设备池管理
- 配置中心
- 深色主题（CSS 变量已就绪，只差切换器）
