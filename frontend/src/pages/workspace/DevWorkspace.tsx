/**
 * 开发工作台。
 *
 * 三个 widget 都用 tasksApi.list() 做数据源，过滤参数对 me 替换成当前 user.id：
 * - 我在做的 → assignee_dev_id=me, status=dev_doing
 * - 我的 bug → assignee_dev_id=me, type=bug（status 多值过滤后端没做，先客户端排掉 closed）
 * - 今日完成 → assignee_dev_id=me, closed_at_after=今天 00:00
 */
import { useQuery } from "@tanstack/react-query";

import { tasksApi } from "@/lib/api";
import { TaskRow, WidgetCard, startOfTodayIso } from "@/pages/workspace/_shared";
import type { Task } from "@/types/domain";

export function DevWorkspace({ userId }: { userId: number }) {
  const todayIso = startOfTodayIso();

  const doingQuery = useQuery({
    queryKey: ["tasks", "dev-doing", userId],
    queryFn: () =>
      tasksApi.list({ assignee_dev_id: userId, status: "dev_doing" }),
  });

  const bugsQuery = useQuery({
    queryKey: ["tasks", "dev-bugs", userId],
    queryFn: () => tasksApi.list({ assignee_dev_id: userId, type: "bug" }),
  });

  const doneTodayQuery = useQuery({
    queryKey: ["tasks", "dev-done-today", userId, todayIso],
    queryFn: () =>
      tasksApi.list({ assignee_dev_id: userId, closed_at_after: todayIso }),
  });

  const openBugs = (bugsQuery.data ?? []).filter((t) => t.status !== "closed");

  return (
    <>
      <WidgetCard<Task>
        title="我在做的"
        items={doingQuery.data}
        isLoading={doingQuery.isLoading}
        isError={doingQuery.isError}
        errorMessage={(doingQuery.error as Error | undefined)?.message}
        renderItem={(task) => <TaskRow task={task} />}
        emptyText="目前没有进行中的开发任务。"
        viewAllHref={`/tasks?assignee_dev_id=${userId}&status=dev_doing`}
      />
      <WidgetCard<Task>
        title="我的 bug"
        items={bugsQuery.isLoading ? undefined : openBugs}
        isLoading={bugsQuery.isLoading}
        isError={bugsQuery.isError}
        errorMessage={(bugsQuery.error as Error | undefined)?.message}
        renderItem={(task) => <TaskRow task={task} />}
        emptyText="暂无未关闭的 bug。"
        viewAllHref={`/tasks?assignee_dev_id=${userId}&type=bug`}
        subtitle="排除已关闭"
      />
      <WidgetCard<Task>
        title="今日完成"
        items={doneTodayQuery.data}
        isLoading={doneTodayQuery.isLoading}
        isError={doneTodayQuery.isError}
        errorMessage={(doneTodayQuery.error as Error | undefined)?.message}
        renderItem={(task) => <TaskRow task={task} />}
        emptyText="今天还没关闭任何任务。"
        viewAllHref={`/tasks?assignee_dev_id=${userId}&closed_at_after=${encodeURIComponent(todayIso)}`}
      />
    </>
  );
}
