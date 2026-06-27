/**
 * 顶栏用户信息。
 *
 * 显示当前登录用户的姓名 + 角色标签，下拉含修改密码和退出登录。
 */
import { ChevronDown, KeyRound, LogOut, UserRound } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useCurrentUser } from "@/lib/current-user";
import { ROLE_LABELS } from "@/types/domain";
import type { RoleCode } from "@/types/domain";

const PROTECTED_ADMIN_USERNAME = "admin";

export function CurrentUserSwitcher() {
  const navigate = useNavigate();
  const { user, activeRole, setUser } = useCurrentUser();

  const label = user ? user.full_name || user.username : "未登录";
  const isProtectedAdmin = user?.username === PROTECTED_ADMIN_USERNAME;

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
      <DropdownMenuContent align="end" className="w-48">
        {user ? (
          <div className="px-2 py-1.5">
            <div className="text-sm font-medium">
              {user.full_name || user.username}
            </div>
            <div className="text-[11px] text-muted-foreground">
              {user.role_codes
                ?.map((c) => ROLE_LABELS[c as RoleCode] ?? c)
                .join(" / ") || "无角色"}
            </div>
          </div>
        ) : null}
        <DropdownMenuSeparator />
        {isProtectedAdmin ? null : (
          <DropdownMenuItem onSelect={() => navigate("/change-password")}>
            <KeyRound className="mr-2 h-3.5 w-3.5" />
            修改密码
          </DropdownMenuItem>
        )}
        <DropdownMenuItem
          onSelect={() => {
            setUser(null);
            navigate("/login", { replace: true });
          }}
          className="text-destructive"
        >
          <LogOut className="mr-2 h-3.5 w-3.5" />
          退出登录
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
