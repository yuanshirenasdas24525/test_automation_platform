import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  CircleDot,
  Database,
  ExternalLink,
  Layers3,
  Loader2,
  Monitor,
  MousePointer2,
  Network,
  Pause,
  Play,
  Search,
  Smartphone,
  Square,
  Terminal,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError, uiRecordingsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  UiElement,
  UiPlatform,
  UiRecordingEvent,
  UiRecordingSession,
  UiRecordingStatus,
} from "@/types/domain";

const PLATFORM_META: Record<UiPlatform, { label: string; runtime: string }> = {
  web: { label: "Web", runtime: "离线业务包 · XHR/Fetch Mock" },
  android: { label: "Android", runtime: "Android Emulator · Appium" },
  ios: { label: "iOS", runtime: "iOS Simulator · XCUITest" },
};

const STATUS_META: Record<UiRecordingStatus, { label: string; className: string }> = {
  draft: { label: "待启动", className: "bg-slate-100 text-slate-600" },
  starting: { label: "启动中", className: "bg-blue-100 text-blue-700" },
  recording: { label: "录制中", className: "bg-red-100 text-red-700" },
  paused: { label: "已暂停", className: "bg-amber-100 text-amber-700" },
  stopping: { label: "停止中", className: "bg-slate-100 text-slate-600" },
  processing: { label: "处理中", className: "bg-violet-100 text-violet-700" },
  completed: { label: "已完成", className: "bg-emerald-100 text-emerald-700" },
  failed: { label: "失败", className: "bg-red-100 text-red-700" },
  cancelled: { label: "已取消", className: "bg-slate-100 text-slate-500" },
};

const ACTIVE_STATUSES: UiRecordingStatus[] = [
  "starting",
  "recording",
  "paused",
  "stopping",
  "processing",
];

function messageOf(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return "操作失败";
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function groupPages(elements: UiElement[]) {
  const pages = new Map<string, { pageKey: string; pageName: string; elements: UiElement[] }>();
  for (const element of elements) {
    const page = pages.get(element.page_key) ?? {
      pageKey: element.page_key,
      pageName: element.page_name,
      elements: [],
    };
    page.elements.push(element);
    pages.set(element.page_key, page);
  }
  return [...pages.values()].sort((a, b) => a.pageName.localeCompare(b.pageName, "zh-CN"));
}

function eventLabel(event: UiRecordingEvent): string {
  const labels: Record<string, string> = {
    "agent.connected": "Recorder Agent 已连接",
    "agent.paused": "录制已暂停",
    "agent.resumed": "录制已继续",
    "agent.disconnected": "Recorder Agent 已停止",
    "page.navigation": "页面跳转",
    "page.ready": "页面加载完成",
    "user.click": "点击元素",
    "user.input": "输入内容",
    "user.change": "修改选项",
    "user.submit": "提交表单",
    "user.scroll": "滚动页面",
    "console.message": "Console",
    "console.pageerror": "页面异常",
    "network.request": "发送请求",
    "network.response": "收到响应",
    "network.failed": "请求失败",
    "screen.capture": "采集画面",
  };
  return labels[event.event_type] ?? event.event_type;
}

export function UiElementLibraryWorkspace({
  open,
  projectId,
  initialPlatform,
  onClose,
}: {
  open: boolean;
  projectId: number;
  initialPlatform: UiPlatform;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [platform, setPlatform] = useState<UiPlatform>(initialPlatform);
  const [keyword, setKeyword] = useState("");
  const [pageKey, setPageKey] = useState<string | null>(null);
  const [selectedElementId, setSelectedElementId] = useState<number | null>(null);
  const [sessionOverride, setSessionOverride] = useState<UiRecordingSession | null>(null);
  const [startOpen, setStartOpen] = useState(false);
  const [targetUrl, setTargetUrl] = useState(() => window.location.origin);
  const [browser, setBrowser] = useState("chromium");

  useEffect(() => {
    if (!open) return;
    setPlatform(initialPlatform);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [initialPlatform, open]);

  useEffect(() => {
    setPageKey(null);
    setSelectedElementId(null);
    setSessionOverride(null);
  }, [platform]);

  const elementsQuery = useQuery({
    queryKey: ["ui-elements", projectId, platform],
    queryFn: () => uiRecordingsApi.listElements({ projectId, platform }),
    enabled: open && Number.isFinite(projectId),
    refetchInterval: open ? 2000 : false,
  });
  const recordingsQuery = useQuery({
    queryKey: ["ui-recordings", projectId, platform],
    queryFn: () => uiRecordingsApi.list(projectId, platform),
    enabled: open && Number.isFinite(projectId),
    refetchInterval: (query) => {
      const sessions = query.state.data ?? [];
      return sessions.some((session) => ACTIVE_STATUSES.includes(session.status)) ? 3000 : false;
    },
  });

  const elements = useMemo(() => elementsQuery.data ?? [], [elementsQuery.data]);
  const normalizedKeyword = keyword.trim().toLocaleLowerCase();
  const filteredElements = useMemo(
    () => elements.filter((element) => {
      if (!normalizedKeyword) return true;
      return [element.semantic_name, element.page_name, ...element.locators.map((item) => item.locator)]
        .some((value) => value.toLocaleLowerCase().includes(normalizedKeyword));
    }),
    [elements, normalizedKeyword],
  );
  const pages = useMemo(() => groupPages(filteredElements), [filteredElements]);
  const activePageKey = pageKey ?? pages[0]?.pageKey ?? null;
  const activePage = pages.find((page) => page.pageKey === activePageKey) ?? null;
  const visibleElements = activePage?.elements ?? [];
  const selectedElement =
    elements.find((element) => element.id === selectedElementId) ?? visibleElements[0] ?? null;

  const latestServerSession = recordingsQuery.data?.[0] ?? null;
  const activeServerSession = recordingsQuery.data?.find((session) =>
    ACTIVE_STATUSES.includes(session.status),
  ) ?? null;
  const serverSession = activeServerSession ?? latestServerSession;
  const session = sessionOverride && serverSession?.id === sessionOverride.id
    ? { ...sessionOverride, ...serverSession }
    : sessionOverride ?? serverSession;
  const eventsQuery = useQuery({
    queryKey: ["ui-recording-events", session?.id],
    queryFn: () => uiRecordingsApi.listEvents(session!.id),
    enabled: open && session != null && ACTIVE_STATUSES.includes(session.status),
    refetchInterval: 1000,
  });
  const events = eventsQuery.data ?? [];

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["ui-recordings", projectId, platform] });
  };

  const startMutation = useMutation({
    mutationFn: async (input: { targetUrl: string; browser: string }) => {
      const draft = await uiRecordingsApi.create({
        project_id: projectId,
        platform,
        name: `${PLATFORM_META[platform].label} 录制 ${new Date().toLocaleString("zh-CN")}`,
        source_url: input.targetUrl,
        capture_config: {
          browser: input.browser,
          headless: false,
          viewport: { width: 1440, height: 900 },
          offline_level: 3,
          reuse_existing_assertions: true,
        },
      });
      return uiRecordingsApi.control(draft.id, "start");
    },
    onSuccess: async (next) => {
      setSessionOverride(next);
      setStartOpen(false);
      await refresh();
      toast.success("受控浏览器已打开，录制已开始");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const controlMutation = useMutation({
    mutationFn: ({ sessionId, action }: {
      sessionId: number;
      action: "pause" | "resume" | "stop";
    }) => uiRecordingsApi.control(sessionId, action),
    onSuccess: async (next) => {
      setSessionOverride(next);
      await refresh();
      toast.success(STATUS_META[next.status].label);
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const openPopout = () => {
    const url = new URL(window.location.href);
    url.searchParams.set("uiElements", platform);
    url.searchParams.set("presentation", "popout");
    const popup = window.open(url.toString(), `ui-elements-${projectId}`, "popup,width=1320,height=820");
    if (!popup) toast.error("浏览器阻止了独立窗口，请允许本站打开弹窗");
  };

  const copyLocator = async (locator: string) => {
    await navigator.clipboard.writeText(locator);
    toast.success("定位器已复制");
  };

  if (!open) return null;

  const sessionBusy = startMutation.isPending || controlMutation.isPending;
  const platformRuntime = PLATFORM_META[platform];
  const statusMeta = session ? STATUS_META[session.status] : null;

  return (
    <div className="fixed inset-0 z-50 flex min-w-[980px] flex-col bg-background text-foreground">
      <header className="flex h-[72px] shrink-0 items-center justify-between border-b px-5">
        <div className="flex min-w-0 items-center gap-4">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-primary text-primary-foreground">
            <Layers3 className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold">可视化元素库</h1>
              <span className="rounded-full border bg-muted/60 px-2 py-0.5 text-[11px] text-muted-foreground">
                离线业务回放 Level 3
              </span>
            </div>
            <p className="text-xs text-muted-foreground">在页面或模拟器场景中查找、验证和维护元素</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="inline-flex rounded-lg border bg-muted/50 p-1" aria-label="选择录制平台">
            {(["web", "android", "ios"] as UiPlatform[]).map((item) => (
              <button
                key={item}
                type="button"
                aria-pressed={platform === item}
                onClick={() => setPlatform(item)}
                className={cn(
                  "rounded-md px-4 py-1.5 text-xs font-medium transition",
                  platform === item ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
                )}
              >
                {PLATFORM_META[item].label}
              </button>
            ))}
          </div>
          <div className="hidden items-center gap-2 rounded-lg border px-3 py-2 text-xs xl:flex">
            <span className={cn("h-2 w-2 rounded-full", session?.capabilities.recorder_agent_connected ? "bg-emerald-500" : "bg-amber-500")} />
            {session?.capabilities.recorder_agent_connected ? "Recorder Agent 已连接" : "Recorder Agent 待连接"}
          </div>
          {statusMeta ? (
            <span className={cn("rounded-full px-2.5 py-1 text-xs font-medium", statusMeta.className)}>
              {statusMeta.label}
            </span>
          ) : null}
          {!session || !ACTIVE_STATUSES.includes(session.status) ? (
            <Button
              size="sm"
              disabled={sessionBusy}
              onClick={() => {
                if (platform !== "web") {
                  toast.info("Android/iOS 模拟器录制将在下一阶段开放");
                  return;
                }
                setStartOpen(true);
              }}
            >
              {startMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CircleDot className="h-4 w-4" />}
              开始录制
            </Button>
          ) : null}
          {session?.status === "recording" ? (
            <Button
              size="sm"
              variant="outline"
              disabled={sessionBusy}
              onClick={() => controlMutation.mutate({ sessionId: session.id, action: "pause" })}
            >
              <Pause className="h-4 w-4" />暂停
            </Button>
          ) : null}
          {session?.status === "paused" ? (
            <Button
              size="sm"
              disabled={sessionBusy}
              onClick={() => controlMutation.mutate({ sessionId: session.id, action: "resume" })}
            >
              <Play className="h-4 w-4" />继续
            </Button>
          ) : null}
          {session && ["recording", "paused"].includes(session.status) ? (
            <Button
              size="sm"
              variant="outline"
              disabled={sessionBusy}
              onClick={() => controlMutation.mutate({ sessionId: session.id, action: "stop" })}
            >
              <Square className="h-3.5 w-3.5" />停止
            </Button>
          ) : null}
          <Button size="icon" variant="ghost" title="打开独立窗口" onClick={openPopout}>
            <ExternalLink className="h-4 w-4" />
          </Button>
          <Button size="icon" variant="ghost" title="关闭" onClick={onClose}>
            <X className="h-5 w-5" />
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[248px_minmax(430px,1fr)_330px]">
        <aside className="flex min-h-0 flex-col border-r bg-muted/15">
          <div className="border-b p-3">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索页面、元素或定位器"
                className="h-9 pl-9 text-xs"
              />
            </div>
          </div>
          <div className="flex items-center justify-between px-4 pb-2 pt-4 text-xs text-muted-foreground">
            <span>页面导航</span>
            <span>{pages.length} 个页面</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
            {elementsQuery.isLoading ? (
              <div className="grid place-items-center py-12 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : pages.length === 0 ? (
              <div className="mx-2 mt-2 rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">
                暂无页面资产
                <div className="mt-1">完成首次录制后自动按页面归档</div>
              </div>
            ) : (
              pages.map((page) => (
                <button
                  type="button"
                  key={page.pageKey}
                  onClick={() => {
                    setPageKey(page.pageKey);
                    setSelectedElementId(page.elements[0]?.id ?? null);
                  }}
                  className={cn(
                    "mb-1 flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left text-sm",
                    activePageKey === page.pageKey ? "bg-primary/10 text-primary" : "hover:bg-muted",
                  )}
                >
                  <span className="truncate">{page.pageName}</span>
                  <span className="rounded bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {page.elements.length}
                  </span>
                </button>
              ))
            )}
          </div>
          <div className="border-t p-3">
            <div className="mb-2 text-[11px] text-muted-foreground">最近录制</div>
            {recordingsQuery.isLoading ? (
              <div className="text-xs text-muted-foreground">加载中…</div>
            ) : session ? (
              <div className="rounded-lg border bg-background p-3">
                <div className="truncate text-xs font-medium">{session.name}</div>
                <div className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground">
                  <span>{session.event_count} 个事件</span>
                  <span>{formatTime(session.updated_at)}</span>
                </div>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">尚无录制会话</div>
            )}
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col bg-slate-50/70 dark:bg-slate-950/20">
          <div className="flex h-[58px] shrink-0 items-center justify-between border-b bg-background px-4">
            <div>
              <div className="text-sm font-semibold">{activePage?.pageName ?? "等待首次页面快照"}</div>
              <div className="text-[11px] text-muted-foreground">{platformRuntime.runtime}</div>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <span className="rounded-md border bg-background px-2 py-1">浏览页面</span>
              <span className="rounded-md border border-primary/30 bg-primary/5 px-2 py-1 text-primary">拾取元素</span>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-auto p-5">
            <div className="mx-auto flex h-full min-h-[460px] max-w-5xl items-center justify-center">
              {platform === "web" ? (
                <div className="flex h-[78%] min-h-[420px] w-[90%] flex-col overflow-hidden rounded-xl border bg-background shadow-sm">
                  <div className="flex h-10 shrink-0 items-center gap-2 border-b bg-muted/50 px-3">
                    <span className="h-2.5 w-2.5 rounded-full bg-red-400" />
                    <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                    <div className="ml-2 flex-1 rounded bg-background px-3 py-1 text-[11px] text-muted-foreground">
                      {activePage ? `offline://project/${projectId}/${activePage.pageKey}` : "offline://等待页面快照"}
                    </div>
                  </div>
                  <ElementStage
                    platform={platform}
                    elements={visibleElements}
                    selectedElementId={selectedElement?.id ?? null}
                    onSelect={setSelectedElementId}
                  />
                </div>
              ) : (
                <div className="flex items-center gap-8">
                  <div className="h-[520px] w-[260px] overflow-hidden rounded-[34px] border-[7px] border-slate-800 bg-background shadow-xl dark:border-slate-700">
                    <div className="mx-auto mt-2 h-5 w-24 rounded-full bg-slate-800 dark:bg-slate-700" />
                    <ElementStage
                      platform={platform}
                      elements={visibleElements}
                      selectedElementId={selectedElement?.id ?? null}
                      onSelect={setSelectedElementId}
                    />
                  </div>
                  <div className="w-64 space-y-3">
                    <div className="rounded-xl border bg-background p-4">
                      <div className="flex items-center gap-2 text-sm font-semibold">
                        <Smartphone className="h-4 w-4 text-primary" />
                        {platform === "android" ? "Android Emulator" : "iOS Simulator"}
                      </div>
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">
                        首期只连接模拟器。页面画面、点击输入和元素树由 Appium 转发。
                      </p>
                    </div>
                    <ContextCapability icon={<Database className="h-4 w-4" />} label="UI Tree" value="等待 Agent" />
                    <ContextCapability icon={<Terminal className="h-4 w-4" />} label="设备日志" value="等待 Agent" />
                    <ContextCapability icon={<Network className="h-4 w-4" />} label="Network" value="能力预检" />
                  </div>
                </div>
              )}
            </div>
          </div>

          {events.length > 0 ? (
            <div className="shrink-0 border-t bg-background px-4 py-2">
              <div className="mb-1 flex items-center justify-between text-[10px] text-muted-foreground">
                <span>实时事件时间线</span>
                <span>{events.length} 个事件</span>
              </div>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {events.slice(-8).map((event) => (
                  <div
                    key={event.id}
                    className={cn(
                      "min-w-[138px] rounded-md border px-2.5 py-2 text-[10px]",
                      event.severity === "error" ? "border-red-200 bg-red-50 text-red-700" : "bg-muted/30",
                    )}
                  >
                    <div className="truncate font-medium">{eventLabel(event)}</div>
                    <div className="mt-1 truncate text-muted-foreground">{event.page_key ?? event.source}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="flex h-11 shrink-0 items-center justify-between border-t bg-background px-4 text-[11px] text-muted-foreground">
            <div className="flex items-center gap-4">
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-violet-500" />已验证</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-amber-500" />待入库</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-red-500" />可能失效</span>
            </div>
            <span>画面 · Console · Network · 用户事件 · 环境信息</span>
          </div>
        </main>

        <aside className="min-h-0 overflow-y-auto border-l bg-background">
          {selectedElement ? (
            <div className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="font-semibold">{selectedElement.semantic_name}</h2>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {selectedElement.page_name} · {selectedElement.element_type}
                  </p>
                </div>
                <span className={cn(
                  "rounded-full px-2 py-1 text-[10px]",
                  selectedElement.status === "verified" && "bg-emerald-100 text-emerald-700",
                  selectedElement.status === "pending" && "bg-amber-100 text-amber-700",
                  selectedElement.status === "stale" && "bg-red-100 text-red-700",
                  selectedElement.status === "archived" && "bg-slate-100 text-slate-500",
                )}>
                  {selectedElement.status === "verified" ? "已验证" : selectedElement.status === "pending" ? "待入库" : selectedElement.status === "stale" ? "可能失效" : "已归档"}
                </span>
              </div>

              <div className="mt-5 text-xs font-medium">定位器候选</div>
              <div className="mt-2 space-y-2">
                {selectedElement.locators.length > 0 ? selectedElement.locators.map((locator) => (
                  <button
                    type="button"
                    key={locator.id}
                    onClick={() => copyLocator(locator.locator)}
                    className="grid w-full grid-cols-[68px_minmax(0,1fr)_32px] items-center gap-2 rounded-lg border px-3 py-2 text-left hover:border-primary/40 hover:bg-primary/5"
                  >
                    <span className="text-[10px] font-semibold uppercase text-muted-foreground">{locator.strategy}</span>
                    <code className="truncate text-[11px]">{locator.locator}</code>
                    <span className="text-right text-[11px] font-semibold text-emerald-600">{locator.score}</span>
                  </button>
                )) : (
                  <div className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">
                    暂无定位器候选
                  </div>
                )}
              </div>

              <div className="mt-6 text-xs font-medium">元素证据</div>
              <dl className="mt-2 divide-y rounded-lg border text-xs">
                <div className="flex justify-between gap-3 px-3 py-2.5"><dt className="text-muted-foreground">页面版本</dt><dd>#{selectedElement.last_snapshot_id ?? "--"}</dd></div>
                <div className="flex justify-between gap-3 px-3 py-2.5"><dt className="text-muted-foreground">用例引用</dt><dd>{selectedElement.usage_count}</dd></div>
                <div className="flex justify-between gap-3 px-3 py-2.5"><dt className="text-muted-foreground">最近验证</dt><dd>{formatTime(selectedElement.last_verified_at)}</dd></div>
              </dl>

              <Button className="mt-5 w-full" disabled={selectedElement.locators.length === 0}>
                <MousePointer2 className="h-4 w-4" />插入用例步骤
              </Button>
            </div>
          ) : (
            <div className="grid h-full place-items-center p-8 text-center">
              <div>
                <MousePointer2 className="mx-auto h-8 w-8 text-muted-foreground/50" />
                <div className="mt-3 text-sm font-medium">尚未选中元素</div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  启动录制并在页面或模拟器画面中拾取元素后，这里会展示 ID、CSS、XPath、Accessibility 等候选定位器。
                </p>
              </div>
            </div>
          )}
        </aside>
      </div>

      <Dialog open={startOpen} onOpenChange={(next) => !startMutation.isPending && setStartOpen(next)}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle>开始 Web 录制</DialogTitle>
            <DialogDescription>
              Recorder Agent 将打开一个独立的可见浏览器。请在该浏览器中正常操作被测系统。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <label className="block space-y-1.5 text-sm">
              <span className="font-medium">目标地址</span>
              <Input
                autoFocus
                value={targetUrl}
                onChange={(event) => setTargetUrl(event.target.value)}
                placeholder="https://staging.example.com/login"
              />
              <span className="block text-xs text-muted-foreground">必须填写完整的 http/https URL。</span>
            </label>
            <label className="block space-y-1.5 text-sm">
              <span className="font-medium">浏览器内核</span>
              <Select value={browser} onValueChange={setBrowser}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="chromium">Chromium（推荐）</SelectItem>
                  <SelectItem value="firefox">Firefox</SelectItem>
                  <SelectItem value="webkit">WebKit</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <div className="rounded-lg border bg-muted/30 p-3 text-xs leading-5 text-muted-foreground">
              自动采集：点击与输入、页面跳转、Console、页面异常、XHR/Fetch、操作截图、URL、浏览器和视口信息。密码输入默认脱敏。
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={startMutation.isPending} onClick={() => setStartOpen(false)}>取消</Button>
            <Button
              disabled={startMutation.isPending || !/^https?:\/\//i.test(targetUrl.trim())}
              onClick={() => startMutation.mutate({ targetUrl: targetUrl.trim(), browser })}
            >
              {startMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CircleDot className="h-4 w-4" />}
              {startMutation.isPending ? "正在启动浏览器…" : "打开浏览器并录制"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ElementStage({
  platform,
  elements,
  selectedElementId,
  onSelect,
}: {
  platform: UiPlatform;
  elements: UiElement[];
  selectedElementId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-xs font-semibold tracking-wide text-primary">STRUCTURE VIEW</div>
          <div className="mt-1 text-lg font-semibold">{elements[0]?.page_name ?? "等待采集页面"}</div>
        </div>
        {platform === "web" ? <Monitor className="h-5 w-5 text-muted-foreground" /> : null}
      </div>
      {elements.length > 0 ? (
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {elements.slice(0, 8).map((element) => (
            <button
              type="button"
              key={element.id}
              onClick={() => onSelect(element.id)}
              className={cn(
                "flex items-center justify-between rounded-lg border bg-background px-3 py-3 text-left",
                selectedElementId === element.id ? "border-primary ring-2 ring-primary/15" : "hover:border-primary/40",
              )}
            >
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium">{element.semantic_name}</span>
                <span className="mt-1 block truncate text-[10px] text-muted-foreground">{element.element_type}</span>
              </span>
              <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
            </button>
          ))}
        </div>
      ) : (
        <div className="grid flex-1 place-items-center text-center">
          <div className="max-w-sm">
            <MousePointer2 className="mx-auto h-9 w-9 text-muted-foreground/40" />
            <div className="mt-3 text-sm font-medium">等待 Recorder Agent 上报页面快照</div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              控制面已就绪。接入采集端后，这里将显示可交互离线页面或模拟器画面，并支持直接拾取元素。
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function ContextCapability({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center justify-between rounded-lg border bg-background px-3 py-2.5 text-xs">
      <span className="flex items-center gap-2 text-muted-foreground">{icon}{label}</span>
      <span>{value}</span>
    </div>
  );
}
