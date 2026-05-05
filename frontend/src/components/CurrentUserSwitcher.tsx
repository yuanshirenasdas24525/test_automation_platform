/**
 * 顶栏"扮演谁"切换器。
 *
 * 内网平台暂时无 auth：从 /api/users?is_active=true 拉所有活跃用户，下拉里挑一个，
 * 选中后写到 CurrentUserContext + localStorage。多角色用户多出二级 list 让用户切
 * 当前激活角色（也可以从工作台顶部 WorkspaceSwitcher 切，二者同源）。
 */
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, LogOut, UserRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useCurrentUser } from "@/lib/current-user";
import { usersApi } from "@/lib/api";
import { ROLE_LABELS } from "@/types/domain";
import type { RoleCode, User } from "@/types/domain";

export function CurrentUserSwitcher() {
  const { user, activeRole, setUser, setActiveRole } = useCurrentUser();

  const usersQuery = useQuery({
    queryKey: ["users", { is_active: true }],
    queryFn: () => usersApi.list({ is_active: true }),
  });

  const users = usersQuery.data ?? [];
  const selectedRoles = (user?.role_codes ?? []).filter((c) =>
    Object.prototype.hasOwnProperty.call(ROLE_LABELS, c),
  ) as RoleCode[];

  const label = user ? user.full_name || user.username : "未登录";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="h-8 gap-2">
          <UserRound className="h-4 w-4" />
          <span className="max-w-[10rem] truncate">{label}</span>
          {activeRole ? (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
              {ROLE_LABELS[activeRole]}
            </span>
          ) : null}
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <div className="px-2 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          切换扮演用户
        </div>
        {usersQuery.isLoading ? (
          <div className="px-2 py-2 text-xs text-muted-foreground">
            加载中…
          </div>
        ) : users.length === 0 ? (
          <div className="px-2 py-2 text-xs text-muted-foreground">
            暂无用户。先 POST /api/users 建一个。
          </div>
        ) : (
          users.map((u) => (
            <DropdownMenuItem
              key={u.id}
              onSelect={() => setUser(u)}
              className="flex items-center justify-between gap-2"
            >
              <div className="min-w-0">
                <div className="truncate text-sm">
                  {u.full_name || u.username}
                </div>
                <div className="truncate text-[11px] text-muted-foreground">
                  {u.username} · {summariseRoles(u)}
                </div>
              </div>
              {user?.id === u.id ? (
                <span className="text-[11px] text-primary">已选</span>
              ) : null}
            </DropdownMenuItem>
          ))
        )}

        {selectedRoles.length > 1 ? (
          <>
            <DropdownMenuSeparator />
            <div className="px-2 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              当前激活角色
            </div>
            {selectedRoles.map((code) => (
              <DropdownMenuItem
                key={code}
                onSelect={() => setActiveRole(code)}
                className="flex items-center justify-between"
              >
                <span className="text-sm">{ROLE_LABELS[code]}</span>
                {activeRole === code ? (
                  <span className="text-[11px] text-primary">●</span>
                ) : null}
              </DropdownMenuItem>
            ))}
          </>
        ) : null}

        {user ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => setUser(null)}
              className="text-destructive"
            >
              <LogOut className="mr-2 h-3.5 w-3.5" />
              退出扮演
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function summariseRoles(u: User): string {
  if (!u.role_codes || u.role_codes.length === 0) return "无角色";
  return u.role_codes
    .map((c) => ROLE_LABELS[c as RoleCode] ?? c)
    .join(" / ");
}
