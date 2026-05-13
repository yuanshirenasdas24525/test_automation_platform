import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { usersApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { User } from "@/types/domain";

interface AssigneePickerProps {
  /** 业务角色，决定按哪个 role_code 过滤可选用户 */
  role: "dev" | "test" | "pm" | "ui";
  value: number[];
  onChange: (next: number[]) => void;
  /** 限制选择数量；默认无限 */
  maxCount?: number;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

const ROLE_LABEL: Record<AssigneePickerProps["role"], string> = {
  dev: "开发",
  test: "测试",
  pm: "产品",
  ui: "UI",
};

export function AssigneePicker({
  role,
  value,
  onChange,
  maxCount,
  placeholder,
  disabled,
  className,
}: AssigneePickerProps) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");

  const { data: users = [] } = useQuery({
    queryKey: ["users", "active", role],
    queryFn: () => usersApi.list({ is_active: true, role_code: role }),
    staleTime: 60_000,
  });

  const byId = useMemo(() => {
    const m = new Map<number, User>();
    for (const u of users) m.set(u.id, u);
    return m;
  }, [users]);

  const filtered = useMemo(() => {
    if (!q.trim()) return users;
    const needle = q.trim().toLowerCase();
    return users.filter(
      (u) =>
        u.username.toLowerCase().includes(needle) ||
        (u.full_name ?? "").toLowerCase().includes(needle),
    );
  }, [users, q]);

  const toggle = (uid: number) => {
    if (value.includes(uid)) {
      onChange(value.filter((x) => x !== uid));
      return;
    }
    if (maxCount !== undefined && value.length >= maxCount) {
      // 单选语义：替换
      if (maxCount === 1) {
        onChange([uid]);
      }
      return;
    }
    onChange([...value, uid]);
  };

  const removeChip = (uid: number) => {
    onChange(value.filter((x) => x !== uid));
  };

  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {value.map((uid) => {
        const u = byId.get(uid);
        const label = u?.full_name || u?.username || `#${uid}`;
        return (
          <span
            key={uid}
            className="inline-flex items-center gap-1 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-xs"
          >
            {label}
            {!disabled && (
              <button
                type="button"
                onClick={() => removeChip(uid)}
                className="text-slate-400 hover:text-red-500"
                aria-label="移除"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </span>
        );
      })}

      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled}
            className="h-7 gap-1 text-xs"
          >
            {value.length === 0 ? placeholder ?? `选择${ROLE_LABEL[role]}` : "+ 添加"}
            <ChevronDown className="h-3 w-3" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <div className="px-2 py-1.5">
            <Input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索用户名 / 姓名"
              className="h-7 text-xs"
            />
          </div>
          <div className="max-h-60 overflow-y-auto">
            {filtered.length === 0 && (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                无匹配用户
              </div>
            )}
            {filtered.map((u) => {
              const checked = value.includes(u.id);
              return (
                <DropdownMenuItem
                  key={u.id}
                  className="text-xs"
                  onSelect={(e) => {
                    e.preventDefault();
                    toggle(u.id);
                  }}
                >
                  <Check
                    className={cn(
                      "mr-2 h-3 w-3",
                      checked ? "opacity-100" : "opacity-0",
                    )}
                  />
                  <span className="truncate">
                    {u.full_name || u.username}
                    {u.full_name && (
                      <span className="ml-1 text-muted-foreground">
                        ({u.username})
                      </span>
                    )}
                  </span>
                </DropdownMenuItem>
              );
            })}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
