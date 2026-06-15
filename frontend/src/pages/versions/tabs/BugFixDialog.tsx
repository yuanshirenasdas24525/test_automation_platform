/**
 * BugFixDialog —— AI 修复 Bug 弹窗。
 *
 * 功能：
 *   1. 展示 Bug 基本信息
 *   2. 加载并展示可用智能体列表（含 CLI 可用性检测）
 *   3. 用户选择智能体 → 提交修复
 *   4. 返回 ai_run_id 供父组件轮询
 */
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, Sparkles, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { bugFixApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Task } from "@/types/domain";
import { useState } from "react";

export function BugFixDialog({
  open,
  onOpenChange,
  bug,
  onTriggered,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  bug: Task | null;
  onTriggered?: (aiRunId: number) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedAgent, setSelectedAgent] = useState<string>("opencode");

  const agentsQuery = useQuery({
    queryKey: ["bug-fix-agents"],
    queryFn: () => bugFixApi.listAgents(),
    enabled: open,
  });

  const agents = agentsQuery.data?.agents ?? [];

  const fixMutation = useMutation({
    mutationFn: () => {
      if (!bug) return Promise.reject(new Error("Bug 不存在"));
      return bugFixApi.fixBug(bug.id, selectedAgent);
    },
    onSuccess: (res) => {
      toast.success("AI 修复任务已提交，正在后台执行");
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      if (bug) {
        queryClient.invalidateQueries({ queryKey: ["task", bug.id] });
      }
      onTriggered?.(res.ai_run_id);
      onOpenChange(false);
    },
    onError: (err: Error) => {
      toast.error(err.message);
    },
  });

  if (!bug) return null;

  const hasGit = agents.length > 0;
  const selected = agents.find((a) => a.name === selectedAgent);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onOpenChange(false)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-500" />
            AI 修复 Bug
          </DialogTitle>
          <DialogDescription>
            选择智能体自动修复 Bug，修改代码并提交 commit
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Bug 信息 */}
          <div className="rounded-lg border bg-muted/30 p-3 text-sm">
            <div className="font-medium truncate">{bug.title}</div>
            <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
              <span>#{bug.id}</span>
              {bug.severity && (
                <span
                  className={cn(
                    "font-semibold",
                    bug.severity === "P0"
                      ? "text-red-600"
                      : bug.severity === "P1"
                        ? "text-orange-500"
                        : "text-amber-600",
                  )}
                >
                  {bug.severity}
                </span>
              )}
            </div>
          </div>

          {/* 智能体选择 */}
          <div>
            <div className="mb-2 text-xs font-medium text-muted-foreground">
              选择智能体
            </div>
            {agentsQuery.isLoading ? (
              <div className="py-4 text-center text-sm text-muted-foreground">
                <Loader2 className="mx-auto h-4 w-4 animate-spin" />
              </div>
            ) : (
              <div className="space-y-1.5">
                {agents.map((agent) => (
                  <button
                    key={agent.name}
                    onClick={() => setSelectedAgent(agent.name)}
                    className={cn(
                      "w-full rounded-lg border p-3 text-left transition-colors",
                      selectedAgent === agent.name
                        ? "border-violet-500 bg-violet-50/60"
                        : "border-transparent bg-muted/40 hover:bg-muted",
                      !agent.available && "opacity-60 cursor-not-allowed",
                    )}
                    disabled={!agent.available}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">
                        {agent.label}
                      </span>
                      {agent.agent_type === "cli" && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                          CLI
                        </span>
                      )}
                      {agent.agent_type === "llm" && (
                        <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-700">
                          LLM
                        </span>
                      )}
                      {agent.available ? (
                        <CheckCircle2 className="ml-auto h-3.5 w-3.5 text-emerald-500" />
                      ) : (
                        <AlertTriangle className="ml-auto h-3.5 w-3.5 text-orange-400" />
                      )}
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {agent.available
                        ? agent.description
                        : "CLI 工具未安装，不可用"}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Git 提示 */}
          {hasGit && (
            <div className="rounded-lg bg-blue-50 p-2.5 text-xs text-blue-700">
              修复后将自动 <strong>git commit + push</strong> 到临时分支
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            size="sm"
          >
            取消
          </Button>
          <Button
            size="sm"
            className="bg-violet-600 hover:bg-violet-700"
            onClick={() => fixMutation.mutate()}
            disabled={
              fixMutation.isPending ||
              !selected?.available
            }
          >
            {fixMutation.isPending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Sparkles className="mr-1 h-3.5 w-3.5" />
            )}
            {fixMutation.isPending ? "提交中…" : "开始修复"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
