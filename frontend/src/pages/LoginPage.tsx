/**
 * 登录页。
 *
 * 调用 POST /api/auth/login 校验用户名密码，
 * 成功后存 token + user 到 Context 和 localStorage，跳到 /workspace。
 */
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCurrentUser } from "@/lib/current-user";
import { authApi, setToken } from "@/lib/api";

const schema = z.object({
  username: z.string().min(1, "请输入用户名"),
  password: z.string().min(1, "请输入密码"),
});

type FormValues = z.infer<typeof schema>;

export function LoginPage() {
  const navigate = useNavigate();
  const { setUser } = useCurrentUser();
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { username: "admin", password: "" },
  });

  const onSubmit = async (values: FormValues) => {
    setSubmitting(true);
    try {
      const resp = await authApi.login(values);
      setToken(resp.token);
      setUser(resp.user);
      toast.success(`欢迎，${resp.user.full_name || resp.user.username}`);
      navigate("/workspace", { replace: true });
    } catch (err) {
      toast.error((err as Error).message || "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <div className="w-full max-w-sm rounded-lg border bg-background p-6 shadow-sm">
        <div className="mb-5 space-y-1 text-center">
          <h1 className="text-xl font-semibold">自动化测试平台</h1>
          <p className="text-xs text-muted-foreground">请输入用户名和密码</p>
        </div>

        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-3"
          noValidate
        >
          <div className="space-y-1.5">
            <Label htmlFor="username">用户名</Label>
            <Input
              id="username"
              autoComplete="username"
              {...form.register("username")}
            />
            {form.formState.errors.username ? (
              <p className="text-xs text-destructive">
                {form.formState.errors.username.message}
              </p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="password">密码</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              {...form.register("password")}
            />
            {form.formState.errors.password ? (
              <p className="text-xs text-destructive">
                {form.formState.errors.password.message}
              </p>
            ) : null}
          </div>

          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting ? "登录中…" : "登录"}
          </Button>
        </form>

        <div className="mt-4 text-[11px] leading-relaxed text-muted-foreground">
          默认账号 <code className="font-mono">admin</code>，密码{" "}
          <code className="font-mono">123456</code>。
        </div>
      </div>
    </div>
  );
}
