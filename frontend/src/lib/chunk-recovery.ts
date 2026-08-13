const CHUNK_RELOAD_KEY = "ui_chunk_reload_attempted_at";
const RELOAD_GUARD_MS = 15_000;

const CHUNK_ERROR_PATTERN = /failed to fetch dynamically imported module|importing a module script failed|error loading dynamically imported module|unable to preload css|loading chunk .* failed|chunkloaderror/i;
const DOM_RECONCILIATION_ERROR_PATTERN = /failed to execute ['"]?(removechild|insertbefore)['"]? on ['"]?node['"]?|node to be removed is not a child|not a child of this node/i;

let memoryReloadAttemptedAt = 0;

function errorMessage(value: unknown): string {
  if (value instanceof Error) return value.message;
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && "message" in value) {
    return String(value.message ?? "");
  }
  return "";
}

export function isChunkLoadError(value: unknown): boolean {
  return CHUNK_ERROR_PATTERN.test(errorMessage(value));
}

export function isDomReconciliationError(value: unknown): boolean {
  return DOM_RECONCILIATION_ERROR_PATTERN.test(errorMessage(value));
}

function storedReloadAttempt(): number {
  try {
    return Number(window.sessionStorage.getItem(CHUNK_RELOAD_KEY) || 0);
  } catch {
    return memoryReloadAttemptedAt;
  }
}

function rememberReloadAttempt(now: number): void {
  memoryReloadAttemptedAt = now;
  try {
    window.sessionStorage.setItem(CHUNK_RELOAD_KEY, String(now));
  } catch {
    // 隐私模式禁用 sessionStorage 时仍使用内存保护，避免刷新循环。
  }
}

function clearReloadGuard(): void {
  memoryReloadAttemptedAt = 0;
  try {
    window.sessionStorage.removeItem(CHUNK_RELOAD_KEY);
  } catch {
    // sessionStorage 不可用不影响页面继续运行。
  }
}

function reloadLatestFrontend(): boolean {
  const now = Date.now();
  if (now - storedReloadAttempt() < RELOAD_GUARD_MS) return false;
  rememberReloadAttempt(now);
  window.location.reload();
  return true;
}

export function recoverFrontendError(value: unknown): boolean {
  if (!isChunkLoadError(value) && !isDomReconciliationError(value)) return false;
  return reloadLatestFrontend();
}

/**
 * Vite 构建更新后，旧页面可能继续引用已经删除的哈希 Chunk。
 * 首次失败自动刷新获取新 index；短时间再次失败则交给路由错误页，避免死循环。
 */
export function installChunkLoadRecovery(): void {
  window.addEventListener("vite:preloadError", (event) => {
    event.preventDefault();
    reloadLatestFrontend();
  });
  window.addEventListener("unhandledrejection", (event) => {
    if (!isChunkLoadError(event.reason) && !isDomReconciliationError(event.reason)) return;
    event.preventDefault();
    reloadLatestFrontend();
  });
  window.addEventListener("error", (event) => {
    const error = event.error || event.message;
    if (!isChunkLoadError(error) && !isDomReconciliationError(error)) return;
    event.preventDefault();
    reloadLatestFrontend();
  });

  // 新版本稳定加载后允许下一次发布再次自动恢复；保留短窗口防止服务端异常时循环刷新。
  window.setTimeout(clearReloadGuard, RELOAD_GUARD_MS);
}
