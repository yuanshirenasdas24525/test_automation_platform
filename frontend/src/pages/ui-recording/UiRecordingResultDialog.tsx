import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ChevronRight,
  EyeOff,
  Loader2,
  Network,
  RotateCcw,
  Save,
  Terminal,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ApiError, uiRecordingsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { UiRecordedAction, UiRecordingEvent, UiRecordingSession } from "@/types/domain";

type ContextTab = "console" | "network" | "user" | "environment";

function messageOf(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return "操作失败";
}

function eventSummary(event: UiRecordingEvent): string {
  const payload = event.payload;
  return String(
    payload.message
      ?? payload.url
      ?? payload.method
      ?? payload.reason
      ?? payload.page_name
      ?? event.event_type,
  );
}

function eventMatchesTab(event: UiRecordingEvent, tab: ContextTab): boolean {
  if (tab === "console") return event.source === "console";
  if (tab === "network") return event.source === "network";
  if (tab === "user") return event.source === "user";
  return ["browser", "agent", "device", "screen"].includes(event.source)
    || event.event_type === "environment.snapshot";
}

function SnapshotPreview({ snapshotId, label }: { snapshotId: number | null; label: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let disposed = false;
    let objectUrl: string | null = null;
    setUrl(null);
    if (snapshotId == null) return undefined;
    void uiRecordingsApi.snapshotImage(snapshotId).then((blob) => {
      if (disposed) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => undefined);
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [snapshotId]);

  return (
    <div className="min-w-0 flex-1">
      <div className="mb-2 text-xs font-medium text-muted-foreground">{label}</div>
      <div className="grid aspect-video place-items-center overflow-hidden rounded-lg border bg-slate-100">
        {url ? <img src={url} alt={label} className="h-full w-full object-contain" /> : (
          <span className="text-xs text-muted-foreground">无可用画面</span>
        )}
      </div>
    </div>
  );
}

function ContextEventsPanel({
  tab,
  onTabChange,
  keyword,
  onKeywordChange,
  events,
  loading,
  emptyText,
}: {
  tab: ContextTab;
  onTabChange: (tab: ContextTab) => void;
  keyword: string;
  onKeywordChange: (keyword: string) => void;
  events: UiRecordingEvent[];
  loading: boolean;
  emptyText: string;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1 border-b px-4 py-2">
        {([
          ["console", "Console", Terminal],
          ["network", "Network", Network],
          ["user", "用户事件", CheckCircle2],
          ["environment", "环境/设备", RotateCcw],
        ] as const).map(([value, label, Icon]) => (
          <Button key={value} size="sm" variant={tab === value ? "secondary" : "ghost"} onClick={() => onTabChange(value)}>
            <Icon className="h-3.5 w-3.5" />{label}
          </Button>
        ))}
        <Input value={keyword} onChange={(event) => onKeywordChange(event.target.value)} placeholder="筛选技术上下文" className="ml-auto h-8 w-56 text-xs" />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {loading ? <Loader2 className="mx-auto mt-8 h-5 w-5 animate-spin" /> : events.map((event) => (
          <div key={event.id} className="mb-2 rounded-lg border bg-card p-3 text-xs">
            <div className="flex items-center gap-2">
              <span className={cn("rounded px-1.5 py-0.5 font-mono", event.severity === "error" ? "bg-red-100 text-red-700" : "bg-muted")}>{event.event_type}</span>
              <span className="truncate font-medium">{eventSummary(event)}</span>
              <span className="ml-auto text-muted-foreground">#{event.sequence_no}</span>
            </div>
            <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/50 p-2 text-[10px] text-muted-foreground">{JSON.stringify(event.payload, null, 2)}</pre>
          </div>
        ))}
        {!loading && !events.length ? <div className="py-10 text-center text-xs text-muted-foreground">{emptyText}</div> : null}
      </div>
    </div>
  );
}

export function UiRecordingResultDialog({
  open,
  session,
  onOpenChange,
}: {
  open: boolean;
  session: UiRecordingSession | null;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedActionId, setSelectedActionId] = useState<number | null>(null);
  const [tab, setTab] = useState<ContextTab>("console");
  const [keyword, setKeyword] = useState("");
  const [nameDraft, setNameDraft] = useState("");

  const contextQuery = useQuery({
    queryKey: ["ui-recording-context", session?.id],
    queryFn: () => uiRecordingsApi.context(session!.id),
    enabled: open && session != null,
  });
  const actions = useMemo(() => contextQuery.data?.actions ?? [], [contextQuery.data?.actions]);
  const selectedAction = actions.find((item) => item.id === selectedActionId) ?? actions[0] ?? null;

  useEffect(() => {
    if (!open) return;
    setSelectedActionId((current) => actions.some((item) => item.id === current) ? current : actions[0]?.id ?? null);
  }, [actions, open]);
  useEffect(() => {
    if (open && contextQuery.data && actions.length === 0) setTab("user");
  }, [actions.length, contextQuery.data, open]);
  useEffect(() => setNameDraft(selectedAction?.name ?? ""), [selectedAction?.id, selectedAction?.name]);

  const eventsQuery = useQuery({
    queryKey: [
      "ui-recording-context-events",
      session?.id,
      selectedAction?.id ?? "all",
    ],
    queryFn: () => uiRecordingsApi.listEvents(
      session!.id,
      selectedAction
        ? Math.max(0, selectedAction.context_event_from_seq - 1)
        : 0,
      {
        toSequence: selectedAction?.context_event_to_seq ?? undefined,
        limit: 1000,
      },
    ),
    enabled: open && session != null,
  });
  const visibleEvents = useMemo(() => (eventsQuery.data ?? []).filter((event) => {
    if (!eventMatchesTab(event, tab)) return false;
    return !keyword.trim()
      || `${event.event_type} ${eventSummary(event)} ${JSON.stringify(event.payload)}`
        .toLocaleLowerCase()
        .includes(keyword.trim().toLocaleLowerCase());
  }), [eventsQuery.data, keyword, tab]);

  const updateMutation = useMutation({
    mutationFn: ({ actionId, patch }: {
      actionId: number;
      patch: Parameters<typeof uiRecordingsApi.updateAction>[2];
    }) => uiRecordingsApi.updateAction(session!.id, actionId, patch),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["ui-recording-context", session?.id] });
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const moveAction = async (action: UiRecordedAction, direction: -1 | 1) => {
    const index = actions.findIndex((item) => item.id === action.id);
    const neighbor = actions[index + direction];
    if (!neighbor || !session) return;
    try {
      await Promise.all([
        uiRecordingsApi.updateAction(session.id, action.id, { sequence_no: neighbor.sequence_no }),
        uiRecordingsApi.updateAction(session.id, neighbor.id, { sequence_no: action.sequence_no }),
      ]);
      await queryClient.invalidateQueries({ queryKey: ["ui-recording-context", session.id] });
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const payloadElement = selectedAction?.payload.element;
  const element = payloadElement && typeof payloadElement === "object"
    ? payloadElement as Record<string, unknown>
    : null;
  const locators = Array.isArray(element?.locators)
    ? element.locators.filter((item): item is Record<string, unknown> => item != null && typeof item === "object")
    : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[92vh] max-w-[96vw] flex-col overflow-hidden p-0">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle>录制结果与技术上下文</DialogTitle>
          <DialogDescription>
            {session?.name ?? "录制会话"} · 动作、画面、Console、Network、用户事件和环境信息已按同一时间窗关联
          </DialogDescription>
        </DialogHeader>
        {contextQuery.isLoading ? (
          <div className="grid flex-1 place-items-center"><Loader2 className="h-6 w-6 animate-spin" /></div>
        ) : contextQuery.isError ? (
          <div className="grid flex-1 place-items-center text-sm text-destructive">{messageOf(contextQuery.error)}</div>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-[300px_minmax(520px,1fr)_330px]">
            <aside className="min-h-0 overflow-y-auto border-r bg-muted/15 p-3">
              <div className="mb-3 flex items-center justify-between px-1">
                <span className="text-sm font-semibold">动作时间线</span>
                <span className="text-xs text-muted-foreground">{actions.length} 条</span>
              </div>
              {actions.map((action, index) => (
                <button
                  key={action.id}
                  type="button"
                  onClick={() => setSelectedActionId(action.id)}
                  className={cn(
                    "mb-1 flex w-full items-start gap-2 rounded-lg border px-3 py-2.5 text-left",
                    selectedAction?.id === action.id ? "border-primary bg-primary/10" : "border-transparent hover:bg-muted",
                    action.status === "ignored" && "opacity-50",
                  )}
                >
                  <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-background text-[10px] font-semibold">{index + 1}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium">{action.name}</span>
                    <span className="mt-1 block text-[10px] text-muted-foreground">
                      #{action.context_event_from_seq}–{action.context_event_to_seq ?? "…"} · {action.duration_ms ?? 0}ms
                    </span>
                  </span>
                  <ChevronRight className="mt-0.5 h-3.5 w-3.5" />
                </button>
              ))}
              {!actions.length ? <div className="rounded-lg border border-dashed p-5 text-center text-xs text-muted-foreground">没有识别到用户动作</div> : null}
            </aside>

            <main className="flex min-h-0 flex-col">
              {selectedAction ? (
                <>
                  <div className="border-b p-4">
                    <div className="flex items-center gap-2">
                      <Input value={nameDraft} onChange={(event) => setNameDraft(event.target.value)} className="h-8" />
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={!nameDraft.trim() || updateMutation.isPending}
                        onClick={() => updateMutation.mutate({ actionId: selectedAction.id, patch: { name: nameDraft.trim(), status: "confirmed" } })}
                      ><Save className="h-3.5 w-3.5" />保存</Button>
                      <Button size="icon" variant="ghost" title="上移" disabled={actions[0]?.id === selectedAction.id} onClick={() => void moveAction(selectedAction, -1)}><ArrowUp className="h-4 w-4" /></Button>
                      <Button size="icon" variant="ghost" title="下移" disabled={actions.at(-1)?.id === selectedAction.id} onClick={() => void moveAction(selectedAction, 1)}><ArrowDown className="h-4 w-4" /></Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => updateMutation.mutate({ actionId: selectedAction.id, patch: { status: selectedAction.status === "ignored" ? "captured" : "ignored" } })}
                      >{selectedAction.status === "ignored" ? <RotateCcw className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}{selectedAction.status === "ignored" ? "恢复" : "忽略噪声"}</Button>
                    </div>
                  </div>
                  <div className="flex gap-4 border-b p-4">
                    <SnapshotPreview snapshotId={selectedAction.snapshot_before_id} label="动作前" />
                    <SnapshotPreview snapshotId={selectedAction.snapshot_after_id} label="动作后" />
                  </div>
                  <ContextEventsPanel
                    tab={tab}
                    onTabChange={setTab}
                    keyword={keyword}
                    onKeywordChange={setKeyword}
                    events={visibleEvents}
                    loading={eventsQuery.isLoading}
                    emptyText="当前动作时间窗没有此类上下文"
                  />
                </>
              ) : (
                <>
                  <div className="border-b border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
                    本次录制没有产生可执行的点击或输入动作。只读拾取不会进入动作时间线，
                    但下方仍可查看本次采集的 Console、Network、用户事件和环境信息。
                  </div>
                  <ContextEventsPanel
                    tab={tab}
                    onTabChange={setTab}
                    keyword={keyword}
                    onKeywordChange={setKeyword}
                    events={visibleEvents}
                    loading={eventsQuery.isLoading}
                    emptyText="本次录制没有采集到此类技术上下文"
                  />
                </>
              )}
            </main>

            <aside className="min-h-0 overflow-y-auto border-l p-4">
              <div className="text-sm font-semibold">目标元素与定位证据</div>
              <div className="mt-4 rounded-lg border p-3">
                <div className="text-sm font-medium">{String(element?.semantic_name ?? "未关联元素")}</div>
                <div className="mt-1 text-xs text-muted-foreground">{String(element?.element_type ?? selectedAction?.action_type ?? "")}</div>
              </div>
              <div className="mt-4 space-y-2">
                {locators.map((locator, index) => (
                  <div key={`${String(locator.strategy)}-${String(locator.locator)}-${index}`} className="rounded-lg border p-3">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-semibold uppercase">{String(locator.strategy)}</span>
                      <span className={cn("rounded px-1.5 py-0.5", locator.is_unique === true ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700")}>{locator.is_unique === true ? "唯一" : `${String(locator.match_count ?? "未验证")} 个匹配`}</span>
                    </div>
                    <code className="mt-2 block break-all rounded bg-muted p-2 text-[10px]">{String(locator.locator)}</code>
                  </div>
                ))}
                {!locators.length ? <div className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">当前动作没有定位证据</div> : null}
              </div>
              <div className="mt-5 text-sm font-semibold">采集能力</div>
              <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted p-3 text-[10px]">{JSON.stringify(contextQuery.data?.context.capabilities ?? {}, null, 2)}</pre>
              {(contextQuery.data?.context.limitations.length ?? 0) > 0 ? (
                <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                  {contextQuery.data?.context.limitations.map((item) => <div key={item}>• {item}</div>)}
                </div>
              ) : null}
            </aside>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
