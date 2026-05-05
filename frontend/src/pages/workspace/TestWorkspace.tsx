/**
 * 测试工作台。
 *
 * - 待测 → assignee_test_id=me, status=dev_done
 * - 测试中 → assignee_test_id=me, status=test_doing
 * - 我创建的 bug → created_by_id=me, type=bug
 */
import { useQuery } from "@tanstack/react-query";

import { tasksApi } from "@/lib/api";
import { TaskRow, WidgetCard } from "@/pages/workspace/_shared";
import type { Task } from "@/types/domain";

export function TestWorkspace({ userId }: { userId: number }) {
  const pendingQuery = useQuery({
    queryKey: ["tasks", "test-pending", userId],
    queryFn: () =>
      tasksApi.list({ assignee_test_id: userId, status: "dev_done" }),
  });

  const doingQuery = useQuery({
    queryKey: ["tasks", "test-doing", userId],
    queryFn: () =>
      tasksApi.list({ assignee_test_id: userId, status: "test_doing" }),
  });

  const myBugsQuery = useQuery({
    queryKey: ["tasks", "test-mybugs", userId],
    queryFn: () => tasksApi.list({ created_by_id: userId, type: "bug" }),
  });

  return (
    <>
      <WidgetCard<Task>
        title="待测"
        items={pendingQuery.data}
        isLoading={pendingQuery.isLoading}
        isError={pendingQuery.isError}
        errorMessage={(pendingQuery.error as Error | undefined)?.message}
        renderItem={(task) => <TaskRow task={task} />}
        emptyText="没有等待测试的任务。"
        viewAllHref={`/tasks?assignee_test_id=${userId}&status=dev_done`}
      />
      <WidgetCard<Task>
        title="测试中"
        items={doingQuery.data}
        isLoading={doingQuery.isLoading}
        isError={doingQuery.isError}
        errorMessage={(doingQuery.error as Error | undefined)?.message}
        renderItem={(task) => <TaskRow task={task} />}
        emptyText="目前没有正在测试的任务。"
        viewAllHref={`/tasks?assignee_test_id=${userId}&status=test_doing`}
      />
      <WidgetCard<Task>
        title="我创建的 bug"
        items={myBugsQuery.data}
        isLoading={myBugsQuery.isLoading}
        isError={myBugsQuery.isError}
        errorMessage={(myBugsQuery.error as Error | undefined)?.message}
        renderItem={(task) => <TaskRow task={task} />}
        emptyText="还没创建过 bug。"
        viewAllHref={`/tasks?created_by_id=${userId}&type=bug`}
      />
    </>
  );
}
