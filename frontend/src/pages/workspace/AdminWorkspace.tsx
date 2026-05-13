/**
 * 管理员工作台。
 *
 * - 成员管理：列出活跃用户 + 编辑（姓名/邮箱/密码/角色）+ 停用 + 新建用户
 * - 停用后自动拼接随机后缀的用户名，从列表隐藏
 * - 全局看板 / 审计日志：占位卡片，M4 再做
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Pencil, Plus } from "lucide-react";

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
  const allUsers = usersQuery.data ?? [];
  const users = allUsers.filter((u) => u.is_active);

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
              共 {users.length} 人 · 编辑与停用
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
              暂无活跃用户。
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 text-left">用户名</th>
                  <th className="px-4 py-2 text-left">姓名 / 邮箱</th>
                  <th className="px-4 py-2 text-left">角色</th>
                  <th className="px-4 py-2 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {users.map((u) => (
                  <UserRow
                    key={u.id}
                    user={u}
                    onEdit={() => setEditing(u)}
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

      <EditUserDialog
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
  onEdit,
  onChanged,
}: {
  user: User;
  onEdit: () => void;
  onChanged: () => void;
}) {
  const toggleActive = useMutation({
    mutationFn: () => usersApi.update(user.id, { is_active: false }),
    onSuccess: () => {
      toast.success(`已停用 ${user.username}`);
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
      <td className="px-4 py-2 text-right">
        <Button
          size="sm"
          variant="outline"
          className="mr-2"
          onClick={onEdit}
        >
          <Pencil className="mr-1 h-3.5 w-3.5" /> 编辑
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={toggleActive.isPending}
          onClick={() => toggleActive.mutate()}
        >
          停用
        </Button>
      </td>
    </tr>
  );
}

function EditUserDialog({
  user,
  onClose,
  onSaved,
}: {
  user: User | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [selected, setSelected] = useState<Set<RoleCode>>(new Set());

  useEffect(() => {
    if (user) {
      setFullName(user.full_name || "");
      setEmail(user.email || "");
      setPassword("");
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
    mutationFn: async () => {
      if (!user) throw new Error("no user");
      const body: Record<string, unknown> = {
        full_name: fullName.trim() || null,
        email: email.trim() || null,
      };
      if (password) body.password = password;
      await usersApi.update(user.id, {
        full_name: fullName.trim() || null,
        email: email.trim() || null,
        password: password || null,
      });
      await usersApi.setRoles(user.id, Array.from(selected));
    },
    onSuccess: () => {
      toast.success("已保存");
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
            编辑用户 · {user?.full_name || user?.username}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <div>
            <Label htmlFor="edit_full_name">姓名</Label>
            <Input
              id="edit_full_name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="edit_email">邮箱</Label>
            <Input
              id="edit_email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="edit_password">密码（留空不修改）</Label>
            <Input
              id="edit_password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="输入新密码"
            />
          </div>
          <div>
            <Label>角色</Label>
            <div className="mt-1 grid grid-cols-2 gap-2">
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
          </div>
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
  const [password, setPassword] = useState("");
  const [selected, setSelected] = useState<Set<RoleCode>>(new Set());

  useEffect(() => {
    if (open) {
      setUsername("");
      setFullName("");
      setEmail("");
      setPassword("");
      setSelected(new Set());
    }
  }, [open]);

  const save = useMutation({
    mutationFn: () =>
      usersApi.create({
        username: username.trim(),
        full_name: fullName.trim() || null,
        email: email.trim() || null,
        password: password || null,
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
            <Label htmlFor="password">密码（可选）</Label>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="不填写则无法登录"
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
