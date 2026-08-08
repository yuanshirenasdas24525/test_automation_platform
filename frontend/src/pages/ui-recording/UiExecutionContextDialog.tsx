import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Loader2, Network, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { uiRecordingsApi } from "@/lib/api";
import { cn } from "@/lib/utils";

function ArtifactImage({ artifactId }: { artifactId: number | null }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let disposed = false;
    let objectUrl: string | null = null;
    setUrl(null);
    if (artifactId == null) return undefined;
    void uiRecordingsApi.contextArtifact(artifactId).then((blob) => {
      if (disposed) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => undefined);
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifactId]);
  return (
    <div className="grid aspect-video place-items-center overflow-hidden rounded-lg border bg-muted/40">
      {url ? <img src={url} alt="步骤画面" className="h-full w-full object-contain" /> : <span className="text-xs text-muted-foreground">无画面</span>}
    </div>
  );
}

export function UiExecutionContextButton({ contextSessionId }: { contextSessionId: number }) {
  const [open, setOpen] = useState(false);
  const [selectedLinkId, setSelectedLinkId] = useState<number | null>(null);
  const [source, setSource] = useState<"all" | "console" | "network" | "runner" | "environment">("all");
  const query = useQuery({
    queryKey: ["ui-execution-context", contextSessionId],
    queryFn: () => uiRecordingsApi.executionContext(contextSessionId),
    enabled: open,
  });
  const steps = useMemo(() => query.data?.steps ?? [], [query.data?.steps]);
  const selected = steps.find((item) => item.link_id === selectedLinkId) ?? steps[0] ?? null;
  useEffect(() => {
    if (open && selectedLinkId == null && steps[0]) setSelectedLinkId(steps[0].link_id);
  }, [open, selectedLinkId, steps]);
  const events = (query.data?.events ?? []).filter((event) => {
    if (selected?.event_from_seq != null && event.sequence_no < selected.event_from_seq) return false;
    if (selected?.event_to_seq != null && event.sequence_no > selected.event_to_seq) return false;
    return source === "all" || event.source === source;
  });

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <Activity className="mr-1.5 h-3.5 w-3.5" />技术上下文
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex h-[88vh] max-w-[92vw] flex-col overflow-hidden p-0">
          <DialogHeader className="border-b px-6 py-4">
            <DialogTitle>执行技术上下文</DialogTitle>
            <DialogDescription>步骤画面、Network、Console、日志和环境信息使用同一时间窗</DialogDescription>
          </DialogHeader>
          {query.isLoading ? <div className="grid flex-1 place-items-center"><Loader2 className="h-6 w-6 animate-spin" /></div> : (
            <div className="grid min-h-0 flex-1 grid-cols-[280px_minmax(520px,1fr)]">
              <aside className="overflow-y-auto border-r bg-muted/15 p-3">
                {steps.map((step, index) => (
                  <button
                    key={step.link_id}
                    type="button"
                    onClick={() => setSelectedLinkId(step.link_id)}
                    className={cn(
                      "mb-1 w-full rounded-lg border px-3 py-2.5 text-left",
                      selected?.link_id === step.link_id ? "border-primary bg-primary/10" : "border-transparent hover:bg-muted",
                    )}
                  >
                    <div className="flex items-center gap-2 text-xs font-medium"><span>{index + 1}.</span><span className="truncate">{step.step_name ?? "未命名步骤"}</span></div>
                    <div className={cn("mt-1 text-[10px]", step.status === "passed" ? "text-emerald-600" : "text-destructive")}>{step.step_type} · {step.status} · #{step.event_from_seq ?? "-"}–{step.event_to_seq ?? "-"}</div>
                  </button>
                ))}
              </aside>
              <main className="flex min-h-0 flex-col">
                <div className="grid grid-cols-2 gap-4 border-b p-4">
                  <div><div className="mb-2 text-xs text-muted-foreground">步骤前</div><ArtifactImage artifactId={selected?.screenshot_before_id ?? null} /></div>
                  <div><div className="mb-2 text-xs text-muted-foreground">步骤后</div><ArtifactImage artifactId={selected?.screenshot_after_id ?? null} /></div>
                </div>
                <div className="flex items-center gap-1 border-b px-4 py-2">
                  {(["all", "console", "network", "runner", "environment"] as const).map((item) => (
                    <Button key={item} size="sm" variant={source === item ? "secondary" : "ghost"} onClick={() => setSource(item)}>
                      {item === "network" ? <Network className="h-3.5 w-3.5" /> : item === "environment" ? <Activity className="h-3.5 w-3.5" /> : <Terminal className="h-3.5 w-3.5" />}{item === "all" ? "全部" : item === "environment" ? "环境" : item}
                    </Button>
                  ))}
                  <span className="ml-auto text-xs text-muted-foreground">{events.length} 条事件</span>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-3">
                  {events.map((event) => (
                    <div key={event.id} className="mb-2 rounded-lg border p-3 text-xs">
                      <div className="flex items-center gap-2"><code className="rounded bg-muted px-1.5 py-0.5">{event.event_type}</code><span className="text-muted-foreground">#{event.sequence_no}</span><span className="ml-auto">{event.severity}</span></div>
                      <pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/50 p-2 text-[10px]">{JSON.stringify(event.payload, null, 2)}</pre>
                    </div>
                  ))}
                  {!events.length ? <div className="py-12 text-center text-xs text-muted-foreground">当前步骤没有此类上下文</div> : null}
                </div>
              </main>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
