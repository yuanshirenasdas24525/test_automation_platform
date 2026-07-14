/**
 * 用例覆盖率视图：缺口需求（0 用例）排前面，点"生成用例"直接去补——
 * 把"补全面"从抽象目标变成可点击的动作。
 */
import { useQuery } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { requirementsApi } from "@/lib/api";

interface Props {
  open: boolean;
  projectId: number;
  onClose: () => void;
  onGenerateFor: (requirementId: number) => void;
}

const COVERAGE_LABEL: Record<string, { text: string; cls: string }> = {
  gap: { text: "缺口", cls: "bg-red-100 text-red-700" },
  has_drafts: { text: "有草稿待评审", cls: "bg-amber-100 text-amber-700" },
  covered: { text: "已覆盖", cls: "bg-emerald-100 text-emerald-700" },
};

export function CoverageDialog({ open, projectId, onClose, onGenerateFor }: Props) {
  const q = useQuery({
    queryKey: ["coverage", projectId],
    queryFn: () => requirementsApi.coverage(projectId),
    enabled: open && Number.isFinite(projectId),
  });

  const byReq = q.data?.by_requirement;
  const rate = byReq?.coverage_rate;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">用例覆盖率</DialogTitle>
          <DialogDescription>
            按需求统计用例数。缺口（0 用例）且高优先级的排在最前，点"生成用例"直接去补。
          </DialogDescription>
        </DialogHeader>

        {q.isLoading ? (
          <div className="py-10 text-center text-sm text-muted-foreground">加载中…</div>
        ) : !byReq || byReq.total === 0 ? (
          <div className="py-10 text-center text-sm text-muted-foreground">该项目暂无需求。</div>
        ) : (
          <>
            <div className="flex items-center gap-4 rounded-md border bg-muted/20 px-4 py-3 text-sm">
              <div>
                覆盖率{" "}
                <span className="text-lg font-semibold">
                  {rate != null ? `${Math.round(rate * 100)}%` : "—"}
                </span>
              </div>
              <div className="text-muted-foreground">
                共 {byReq.total} 个需求 · 已覆盖 {byReq.covered} · 缺口 {byReq.uncovered}
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-background text-xs text-muted-foreground">
                  <tr className="border-b">
                    <th className="px-2 py-2 text-left">需求</th>
                    <th className="px-2 py-2 text-left">模块</th>
                    <th className="px-2 py-2 text-center">用例</th>
                    <th className="px-2 py-2 text-center">草稿</th>
                    <th className="px-2 py-2 text-center">状态</th>
                    <th className="px-2 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {byReq.requirements.map((r) => {
                    const badge = COVERAGE_LABEL[r.coverage] ?? COVERAGE_LABEL.gap;
                    return (
                      <tr key={r.requirement_id} className="border-b hover:bg-accent/30">
                        <td className="px-2 py-2">
                          <span className="text-[11px] text-muted-foreground">
                            REQ-{r.requirement_id}
                          </span>{" "}
                          {r.title}
                        </td>
                        <td className="px-2 py-2 text-xs text-muted-foreground">
                          {r.module ?? "—"}
                        </td>
                        <td className="px-2 py-2 text-center font-medium">{r.case_count}</td>
                        <td className="px-2 py-2 text-center text-muted-foreground">
                          {r.pending_draft_count || "—"}
                        </td>
                        <td className="px-2 py-2 text-center">
                          <span className={`rounded px-1.5 py-0.5 text-[10px] ${badge.cls}`}>
                            {badge.text}
                          </span>
                        </td>
                        <td className="px-2 py-2 text-right">
                          {r.coverage === "gap" ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-7 text-xs text-emerald-600"
                              onClick={() => onGenerateFor(r.requirement_id)}
                            >
                              <Sparkles className="mr-1 h-3.5 w-3.5" />
                              生成用例
                            </Button>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}

        <div className="flex justify-end border-t pt-3">
          <Button variant="outline" onClick={onClose}>
            {q.isFetching ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
            关闭
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
