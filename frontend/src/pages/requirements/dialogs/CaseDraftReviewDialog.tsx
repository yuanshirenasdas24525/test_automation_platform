/**
 * M7 · AI 用例草稿 Review。
 *
 * 用法：Launcher 触发完，把 batches 传进来；Dialog 内：
 *   - 每个 batch 一个 tab；轮询对应 ai_run 状态
 *   - run 进入 success → 拉 listDrafts({batch_id}) 渲染草稿列表
 *   - 行内编辑 / 删 / 勾选；底部 "批量入库" → commit({draft_ids, target_module_id?})
 *
 * 也支持"无 batches"模式 —— 直接传 requirement_ids 当独立的 review 入口
 * （未来从需求详情侧拉历史草稿用，目前先保留接口）。
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, ListChecks, Loader2, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { aiCaseGenerationApi, ApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  AiCaseDraft,
  AiCaseDraftUpdatePayload,
  CaseGenerationBatch,
  CaseGenerationRun,
} from "@/types/domain";

import { CaseDraftRow } from "../components/CaseDraftRow";


interface Props {
  open: boolean;
  onClose: () => void;
  batches: CaseGenerationBatch[];
  /** 入库成功后的回调 —— 父组件用来刷新 cases 列表。 */
  onCommitted?: (result: {
    created_case_ids: number[];
    skipped: Array<{ draft_id: number; reason: string }>;
  }) => void;
}


/** 用 useQueries 给每个 batch 单独轮询其 run。 */
function useRunsPolling(batches: CaseGenerationBatch[], enabled: boolean) {
  return useQueries({
    queries: batches.map((b) => ({
      queryKey: queryKeys.aiCaseGenerationRun(b.run_id),
      queryFn: () => aiCaseGenerationApi.getRun(b.run_id),
      enabled,
      refetchInterval: (q: { state: { data?: CaseGenerationRun } }) => {
        const status = q.state.data?.status;
        // 跑完了就停
        if (status && ["success", "failed", "cancelled"].includes(status)) {
          return false;
        }
        return 2000;
      },
    })),
  });
}


export function CaseDraftReviewDialog({
  open,
  onClose,
  batches,
  onCommitted,
}: Props) {
  const qc = useQueryClient();
  const [activeBatchId, setActiveBatchId] = useState<string>("");

  // 每次重新打开重置
  useEffect(() => {
    if (open && batches.length > 0) {
      setActiveBatchId(batches[0].batch_id);
    }
  }, [open, batches]);

  const runs = useRunsPolling(batches, open);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <ListChecks className="h-5 w-5 text-violet-600" />
              AI 用例草稿 Review
            </span>
          </DialogTitle>
          <DialogDescription>
            勾选 / 编辑后点底部「批量入库」即可写入 test_cases；未勾选的草稿仍保留为 pending。
          </DialogDescription>
        </DialogHeader>

        {batches.length === 0 ? (
          <div className="rounded border border-dashed p-4 text-center text-sm text-muted-foreground">
            暂无生成批次。请先在 Launcher 弹窗触发生成。
          </div>
        ) : (
          <Tabs
            value={activeBatchId}
            onValueChange={setActiveBatchId}
            className="w-full"
          >
            <TabsList className="flex flex-wrap gap-1">
              {batches.map((b, idx) => {
                const run = runs[idx]?.data as CaseGenerationRun | undefined;
                return (
                  <TabsTrigger key={b.batch_id} value={b.batch_id}>
                    <span className="inline-flex items-center gap-1.5">
                      <BatchStatusDot status={run?.status} />
                      <span className="text-xs">#{b.requirement_id}</span>
                      <span className="text-xs text-muted-foreground">
                        {b.model_label || b.model_name}
                      </span>
                    </span>
                  </TabsTrigger>
                );
              })}
            </TabsList>

            {batches.map((b, idx) => (
              <TabsContent key={b.batch_id} value={b.batch_id}>
                <BatchPanel
                  batch={b}
                  run={runs[idx]?.data as CaseGenerationRun | undefined}
                  onAfterCommit={(result) => {
                    onCommitted?.(result);
                    qc.invalidateQueries({
                      queryKey: queryKeys.aiCaseDrafts({
                        batch_id: b.batch_id,
                      }),
                    });
                  }}
                />
              </TabsContent>
            ))}
          </Tabs>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


function BatchStatusDot({ status }: { status?: string | null }) {
  if (status === "success")
    return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />;
  if (status === "failed" || status === "cancelled")
    return <XCircle className="h-3.5 w-3.5 text-rose-500" />;
  return <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />;
}


function BatchPanel({
  batch,
  run,
  onAfterCommit,
}: {
  batch: CaseGenerationBatch;
  run?: CaseGenerationRun;
  onAfterCommit: (result: {
    created_case_ids: number[];
    skipped: Array<{ draft_id: number; reason: string }>;
  }) => void;
}) {
  const qc = useQueryClient();
  const draftsQuery = useQuery({
    queryKey: queryKeys.aiCaseDrafts({ batch_id: batch.batch_id }),
    queryFn: () =>
      aiCaseGenerationApi.listDrafts({ batch_id: batch.batch_id }),
    // run 成功后才有意义，但失败/取消时也轮一下避免遗漏（后端可能已有部分草稿）
    enabled: run != null,
    refetchInterval: run?.status === "running" || run?.status === "pending" ? 3000 : false,
  });

  const drafts = useMemo(() => draftsQuery.data ?? [], [draftsQuery.data]);
  const pendingDrafts = useMemo(
    () => drafts.filter((d) => d.status === "pending"),
    [drafts],
  );

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // 草稿首次加载后默认全选 pending
  useEffect(() => {
    if (pendingDrafts.length > 0) {
      setSelectedIds(new Set(pendingDrafts.map((d) => d.id)));
    }
  }, [draftsQuery.dataUpdatedAt, pendingDrafts]);

  const updateMutation = useMutation({
    mutationFn: async (args: { id: number; patch: AiCaseDraftUpdatePayload }) =>
      aiCaseGenerationApi.updateDraft(args.id, args.patch),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: queryKeys.aiCaseDrafts({ batch_id: batch.batch_id }),
      });
      toast.success("已保存");
    },
    onError: (err) => {
      toast.error(
        err instanceof ApiError ? err.message : "保存失败",
      );
    },
  });

  const rejectMutation = useMutation({
    mutationFn: (id: number) => aiCaseGenerationApi.rejectDraft(id),
    onSuccess: (_, id) => {
      setSelectedIds((s) => {
        const n = new Set(s);
        n.delete(id);
        return n;
      });
      qc.invalidateQueries({
        queryKey: queryKeys.aiCaseDrafts({ batch_id: batch.batch_id }),
      });
    },
    onError: (err) => {
      toast.error(
        err instanceof ApiError ? err.message : "操作失败",
      );
    },
  });

  const commitMutation = useMutation({
    mutationFn: () =>
      aiCaseGenerationApi.commit({
        draft_ids: Array.from(selectedIds),
      }),
    onSuccess: (data) => {
      const created = data.created_case_ids.length;
      const skipped = data.skipped.length;
      if (created > 0) {
        toast.success(`已入库 ${created} 条用例${skipped ? `（跳过 ${skipped}）` : ""}`);
      } else if (skipped > 0) {
        toast.warning(`全部 ${skipped} 条被跳过：${data.skipped[0]?.reason ?? ""}`);
      }
      setSelectedIds(new Set());
      onAfterCommit(data);
    },
    onError: (err) => {
      toast.error(
        err instanceof ApiError ? err.message : "入库失败",
      );
    },
  });

  if (!run) {
    return <div className="py-6 text-sm text-muted-foreground">加载中…</div>;
  }

  if (run.status === "pending" || run.status === "running") {
    return (
      <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        AI 正在生成（{run.status}）… 完成后自动刷新草稿。
      </div>
    );
  }

  if (run.status === "failed" || run.status === "cancelled") {
    return (
      <div className="space-y-2 py-4">
        <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          任务{run.status === "cancelled" ? "已取消" : "失败"}：{run.error || "未知错误"}
        </div>
        {drafts.length > 0 ? (
          <DraftList
            drafts={drafts}
            selectedIds={selectedIds}
            setSelectedIds={setSelectedIds}
            onSave={(id, patch) => updateMutation.mutateAsync({ id, patch })}
            onReject={(id) => rejectMutation.mutateAsync(id)}
            saving={updateMutation.isPending}
            rejecting={rejectMutation.isPending}
          />
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-3 py-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          共 {drafts.length} 条草稿（pending {pendingDrafts.length} / 已勾选 {selectedIds.size}）
        </span>
        <span>
          {run.tokens_in != null ? (
            <>
              tokens in {run.tokens_in} / out {run.tokens_out}
            </>
          ) : null}
        </span>
      </div>

      {drafts.length === 0 ? (
        <div className="rounded border border-dashed p-4 text-center text-sm text-muted-foreground">
          AI 未产出任何草稿。可在「补充说明」里给出更明确的指令后重试。
        </div>
      ) : (
        <DraftList
          drafts={drafts}
          selectedIds={selectedIds}
          setSelectedIds={setSelectedIds}
          onSave={(id, patch) => updateMutation.mutateAsync({ id, patch })}
          onReject={(id) => rejectMutation.mutateAsync(id)}
          saving={updateMutation.isPending}
          rejecting={rejectMutation.isPending}
        />
      )}

      <div className="flex justify-end gap-2">
        <Button
          onClick={() => commitMutation.mutate()}
          disabled={commitMutation.isPending || selectedIds.size === 0}
        >
          {commitMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <CheckCircle2 className="h-4 w-4" />
          )}
          批量入库（{selectedIds.size}）
        </Button>
      </div>
    </div>
  );
}


function DraftList({
  drafts,
  selectedIds,
  setSelectedIds,
  onSave,
  onReject,
  saving,
  rejecting,
}: {
  drafts: AiCaseDraft[];
  selectedIds: Set<number>;
  setSelectedIds: (next: Set<number>) => void;
  onSave: (id: number, patch: AiCaseDraftUpdatePayload) => Promise<unknown>;
  onReject: (id: number) => Promise<unknown>;
  saving?: boolean;
  rejecting?: boolean;
}) {
  return (
    <div className="max-h-[55vh] space-y-2 overflow-y-auto pr-1">
      {drafts.map((d) => (
        <CaseDraftRow
          key={d.id}
          draft={d}
          checked={selectedIds.has(d.id)}
          onCheckedChange={(next) => {
            const n = new Set(selectedIds);
            if (next) n.add(d.id);
            else n.delete(d.id);
            setSelectedIds(n);
          }}
          onSave={async (patch) => {
            await onSave(d.id, patch);
          }}
          onReject={async () => {
            await onReject(d.id);
          }}
          saving={saving}
          rejecting={rejecting}
        />
      ))}
    </div>
  );
}
