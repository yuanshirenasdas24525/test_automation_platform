import "@/index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";

import { CurrentUserProvider } from "@/lib/current-user";
import { queryClient } from "@/lib/query";
import { router } from "@/routes";

const container = document.getElementById("root");
if (!container) {
  throw new Error("root element not found; 检查 index.html 里的 <div id=\"root\">");
}

// CurrentUserProvider 提到 RouterProvider 之外：/login（无 chrome）和 AppLayout
// 内的所有页面都通过同一份 Context 共享当前用户。
createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <CurrentUserProvider>
        <RouterProvider router={router} />
      </CurrentUserProvider>
      <Toaster position="top-right" richColors closeButton />
    </QueryClientProvider>
  </StrictMode>,
);
