/**
 * /workspace/:role 路由 wrapper。
 *
 * 1. 没选用户 → 提示先选用户
 * 2. role 不在用户的 role_codes 里 → 403 提示 + 链到首个有权限的角色
 * 3. URL 的 :role 与 Provider.activeRole 不同步 → 静默写回 Provider
 *    （让 useActiveRole() 取到的总是当前 URL 对应的角色）
 * 4. 否则按 role 分发到对应工作台组件
 */
import { useEffect } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher";
import { WorkspaceShell } from "@/pages/workspace/_shared";
import { AdminWorkspace } from "@/pages/workspace/AdminWorkspace";
import { DevWorkspace } from "@/pages/workspace/DevWorkspace";
import { OpsWorkspace } from "@/pages/workspace/OpsWorkspace";
import { PmWorkspace } from "@/pages/workspace/PmWorkspace";
import { TestWorkspace } from "@/pages/workspace/TestWorkspace";
import { UiWorkspace } from "@/pages/workspace/UiWorkspace";
import { useCurrentUser } from "@/lib/current-user";
import { ALL_ROLE_CODES, ROLE_LABELS } from "@/types/domain";
import type { RoleCode } from "@/types/domain";

const ROLE_DESCRIPTIONS: Record<RoleCode, string> = {
  dev: "开发自己的看板：在做的、bug 队列、今日完成。",
  test: "测试自己的看板：待测、测试中、提过的 bug。",
  pm: "产品视角：需求池、待验收、本迭代里程碑。",
  ui: "设计走查任务 + 设计稿资产入口。",
  ops: "环境探活、本周发版、上线公告。",
  admin: "成员管理 + 全局看板（占位）。",
};

export function WorkspaceRoute() {
  const params = useParams<{ role: string }>();
  const { user, activeRole, setActiveRole } = useCurrentUser();
  const role = params.role as RoleCode | undefined;

  const userRoles = (user?.role_codes ?? []).filter((c) =>
    (ALL_ROLE_CODES as readonly string[]).includes(c),
  ) as RoleCode[];

  // URL 是真理：把 Provider 的 activeRole 同步成 URL 里的 role
  useEffect(() => {
    if (role && userRoles.includes(role) && activeRole !== role) {
      setActiveRole(role);
    }
  }, [role, userRoles, activeRole, setActiveRole]);

  if (!user) {
    return (
      <WorkspaceShell title="工作台">
        <div className="col-span-full rounded-md border bg-background p-6 text-sm text-muted-foreground">
          请先在右上角选一个用户来"扮演"，工作台会按该用户的角色展示对应数据。
        </div>
      </WorkspaceShell>
    );
  }

  if (!role || !(ALL_ROLE_CODES as readonly string[]).includes(role)) {
    return (
      <WorkspaceShell title="工作台">
        <div className="col-span-full rounded-md border bg-background p-6 text-sm text-destructive">
          未知角色：{role ?? "(空)"}。
        </div>
      </WorkspaceShell>
    );
  }

  if (!userRoles.includes(role)) {
    const fallback = userRoles[0];
    return (
      <WorkspaceShell title="工作台">
        <div className="col-span-full rounded-md border bg-background p-6 text-sm">
          <div className="font-medium text-destructive">
            当前用户没有「{ROLE_LABELS[role]}」角色。
          </div>
          {fallback ? (
            <div className="mt-2 text-muted-foreground">
              可访问的角色：
              <Link
                to={`/workspace/${fallback}`}
                className="ml-1 text-primary hover:underline"
              >
                {ROLE_LABELS[fallback]}
              </Link>
            </div>
          ) : (
            <div className="mt-2 text-muted-foreground">
              当前用户没有任何已识别的角色，让管理员去 /admin 给他/她配上。
            </div>
          )}
        </div>
      </WorkspaceShell>
    );
  }

  const switcher = (
    <WorkspaceSwitcher current={role} available={userRoles} />
  );

  const description = ROLE_DESCRIPTIONS[role];
  const title = `${ROLE_LABELS[role]}工作台`;

  switch (role) {
    case "dev":
      return (
        <WorkspaceShell title={title} description={description} switcher={switcher}>
          <DevWorkspace userId={user.id} />
        </WorkspaceShell>
      );
    case "test":
      return (
        <WorkspaceShell title={title} description={description} switcher={switcher}>
          <TestWorkspace userId={user.id} />
        </WorkspaceShell>
      );
    case "pm":
      return (
        <WorkspaceShell title={title} description={description} switcher={switcher}>
          <PmWorkspace userId={user.id} />
        </WorkspaceShell>
      );
    case "ui":
      return (
        <WorkspaceShell title={title} description={description} switcher={switcher}>
          <UiWorkspace userId={user.id} />
        </WorkspaceShell>
      );
    case "ops":
      return (
        <WorkspaceShell title={title} description={description} switcher={switcher}>
          <OpsWorkspace />
        </WorkspaceShell>
      );
    case "admin":
      return (
        <WorkspaceShell title={title} description={description} switcher={switcher}>
          <AdminWorkspace />
        </WorkspaceShell>
      );
    default:
      return null;
  }
}

/** 进入根 /workspace 时跳到首个可用角色，没有用户/角色给个空态。 */
export function WorkspaceRedirect() {
  const { user, activeRole } = useCurrentUser();

  if (!user) {
    return (
      <WorkspaceShell title="工作台">
        <div className="col-span-full rounded-md border bg-background p-6 text-sm text-muted-foreground">
          请先在右上角选一个用户来"扮演"。
        </div>
      </WorkspaceShell>
    );
  }

  const userRoles = (user.role_codes ?? []).filter((c) =>
    (ALL_ROLE_CODES as readonly string[]).includes(c),
  ) as RoleCode[];

  if (userRoles.length === 0) {
    return (
      <WorkspaceShell title="工作台">
        <div className="col-span-full rounded-md border bg-background p-6 text-sm text-muted-foreground">
          当前用户没有任何角色。
        </div>
      </WorkspaceShell>
    );
  }

  const target =
    activeRole && userRoles.includes(activeRole) ? activeRole : userRoles[0];
  return <Navigate to={`/workspace/${target}`} replace />;
}

