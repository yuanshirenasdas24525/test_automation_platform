/**
 * 管理员工作台。
 *
 * - 成员管理：列出全部用户 + 切换激活状态 + 编辑角色（多选 checkbox）+ 新建用户
 * - 全局看板 / 审计日志：占位卡片，M4 再做
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, UserCog } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { usersApi } from "@/lib/api";
import { ALL_ROLE_CODES, ROLE_LABELS } from "@/types/domain";
import type { RoleCode, User } from "@/types/domain";

export function AdminWorkspace() {
  const queryClient = useQueryClient();
  const usersQuery = useQuery({
    queryKey: ["users", { all: true }],
    queryFn: () => usersApi.list(),
  });
  const users = usersQuery.data ?? [];

  const [editing, setEditing] = useState<User | null>(null);
  const [creating, setCreating] = useState(false);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["users"] });

  return (
    <>
      <Card className="col-span-full">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold">成员管理</h3>
            <div className="mt-0.5 text-xs text-muted-foreground">
              共 {users.length} 人 · 编辑角色与激活状态
            </div>
          </div>
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="mr-1 h-3.5 w-3.5" /> 新建用户
          </Button>
        </div>
        <CardContent className="p-0">
          {usersQuery.isLoading ? (
            <div className="px-4 py-6 text-xs text-muted-foreground">
              加载中…
            </div>
          ) : users.length === 0 ? (
            <div className="px-4 py-6 text-xs text-muted-foreground">
              暂无用户。
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 text-left">用户名</th>
                  <th className="px-4 py-2 text-left">姓名 / 邮箱</th>
                  <th className="px-4 py-2 text-left">角色</th>
                  <th className="px-4 py-2 text-left">状态</th>
                  <th className="px-4 py-2 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {users.map((u) => (
                  <UserRow
                    key={u.id}
                    user={u}
                    onEditRoles={() => setEditing(u)}
                    onChanged={invalidate}
                  />
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      <Card className="border-dashed">
        <div className="px-4 py-3 text-sm font-semibold">全局看板</div>
        <div className="px-4 pb-4 text-xs text-muted-foreground">
          M4 规划中：跨项目的需求/任务聚合视图。
        </div>
      </Card>
      <Card className="border-dashed">
        <div className="px-4 py-3 text-sm font-semibold">审计日志</div>
        <div className="px-4 pb-4 text-xs text-muted-foreground">
          M4 规划中：用户操作流水（创建/验收/发版）。
        </div>
      </Card>

      <EditRolesDialog
        user={editing}
        onClose={() => setEditing(null)}
        onSaved={invalidate}
      />
      <CreateUserDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={invalidate}
      />
    </>
  );
}

function UserRow({
  user,
  onEditRoles,
  onChanged,
}: {
  user: User;
  onEditRoles: () => void;
  onChanged: () => void;
}) {
  const toggleActive = useMutation({
    mutationFn: () => usersApi.update(user.id, { is_active: !user.is_active }),
    onSuccess: () => {
      toast.success(user.is_active ? "已停用" : "已启用");
      onChanged();
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <tr className="text-sm">
      <td className="px-4 py-2 font-mono text-xs">{user.username}</td>
      <td className="px-4 py-2">
        <div className="font-medium">{user.full_name || "—"}</div>
        <div className="text-xs text-muted-foreground">{user.email || "—"}</div>
      </td>
      <td className="px-4 py-2 text-xs">
        {(user.role_codes ?? []).length === 0
          ? "—"
          : user.role_codes
              .map((c) => ROLE_LABELS[c as RoleCode] ?? c)
              .join(" / ")}
      </td>
      <td className="px-4 py-2 text-xs">
        <span
          className={
            user.is_active
              ? "rounded bg-green-100 px-1.5 py-0.5 text-green-800"
              : "rounded bg-muted px-1.5 py-0.5 text-muted-foreground"
          }
        >
          {user.is_active ? "active" : "disabled"}
        </span>
      </td>
      <td className="px-4 py-2 text-right">
        <Button
          size="sm"
          variant="outline"
          className="mr-2"
          onClick={onEditRoles}
        >
          <UserCog className="mr-1 h-3.5 w-3.5" /> 角色
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={toggleActive.isPending}
          onClick={() => toggleActive.mutate()}
        >
          {user.is_active ? "停用" : "启用"}
        </Button>
      </td>
    </tr>
  );
}

function EditRolesDialog({
  user,
  onClose,
  onSaved,
}: {
  user: User | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [selected, setSelected] = useState<Set<RoleCode>>(new Set());

  useEffect(() => {
    if (user) {
      setSelected(
        new Set(
          (user.role_codes ?? []).filter((c) =>
            (ALL_ROLE_CODES as readonly string[]).includes(c),
          ) as RoleCode[],
        ),
      );
    }
  }, [user]);

  const save = useMutation({
    mutationFn: () => {
      if (!user) throw new Error("no user");
      return usersApi.setRoles(user.id, Array.from(selected));
    },
    onSuccess: () => {
      toast.success("已保存角色");
      onSaved();
      onClose();
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <Dialog open={user !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>
            编辑角色 · {user?.full_name || user?.username}
          </DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-2 py-2">
          {ALL_ROLE_CODES.map((code) => {
            const checked = selected.has(code);
            return (
              <label
                key={code}
                className="flex cursor-pointer items-center gap-2 rounded border px-3 py-2 text-sm"
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(e) => {
                    setSelected((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.add(code);
                      else next.delete(code);
                      return next;
                    });
                  }}
                />
                <span>{ROLE_LABELS[code]}</span>
                <span className="ml-auto text-[10px] text-muted-foreground">
                  {code}
                </span>
              </label>
            );
          })}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CreateUserDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [selected, setSelected] = useState<Set<RoleCode>>(new Set());

  useEffect(() => {
    if (open) {
      setUsername("");
      setFullName("");
      setEmail("");
      setSelected(new Set());
    }
  }, [open]);

  const save = useMutation({
    mutationFn: () =>
      usersApi.create({
        username: username.trim(),
        full_name: fullName.trim() || null,
        email: email.trim() || null,
        is_active: true,
        role_codes: Array.from(selected),
      }),
    onSuccess: () => {
      toast.success("已创建用户");
      onCreated();
      onClose();
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>新建用户</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <div>
            <Label htmlFor="username">用户名（必填）</Label>
            <Input
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
          </div>
          <div>
            <Label htmlFor="full_name">姓名</Label>
            <Input
              id="full_name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="email">邮箱</Label>
            <Input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <Label>角色</Label>
            <div className="mt-1 grid grid-cols-2 gap-2">
              {ALL_ROLE_CODES.map((code) => (
                <label
                  key={code}
                  className="flex cursor-pointer items-center gap-2 rounded border px-3 py-2 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(code)}
                    onChange={(e) => {
                      setSelected((prev) => {
                        const next = new Set(prev);
                        if (e.target.checked) next.add(code);
                        else next.delete(code);
                        return next;
                      });
                    }}
                  />
                  <span>{ROLE_LABELS[code]}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={save.isPending || !username.trim()}
            onClick={() => save.mutate()}
          >
            创建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
