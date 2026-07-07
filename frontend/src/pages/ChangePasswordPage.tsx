/**
 * 修改密码页。
 *
 * 调用 PUT /api/auth/password，需要旧密码验证。
 */
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { authApi } from "@/lib/api";
import { useCurrentUser } from "@/lib/current-user";

const schema = z
  .object({
    old_password: z.string().min(1, "请输入旧密码"),
    new_password: z.string().min(1, "请输入新密码").max(128),
    confirm_password: z.string().min(1, "请确认新密码"),
  })
  .refine((v) => v.new_password === v.confirm_password, {
    message: "两次输入的新密码不一致",
    path: ["confirm_password"],
  });

type FormValues = z.infer<typeof schema>;

export function ChangePasswordPage() {
  const navigate = useNavigate();
  const { setUser } = useCurrentUser();
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { old_password: "", new_password: "", confirm_password: "" },
  });

  const onSubmit = async (values: FormValues) => {
    setSubmitting(true);
    try {
      await authApi.changePassword({
        old_password: values.old_password,
        new_password: values.new_password,
      });
      setUser(null);
      toast.success("密码修改成功，请重新登录");
      navigate("/login", { replace: true });
    } catch (err) {
      toast.error((err as Error).message || "修改失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex h-full items-start justify-center pt-16 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>修改密码</CardTitle>
          <CardDescription>输入旧密码后设置新密码</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="space-y-3"
            noValidate
          >
            <div className="space-y-1.5">
              <Label htmlFor="old_password">旧密码</Label>
              <Input
                id="old_password"
                type="password"
                autoComplete="current-password"
                {...form.register("old_password")}
              />
              {form.formState.errors.old_password ? (
                <p className="text-xs text-destructive">
                  {form.formState.errors.old_password.message}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="new_password">新密码</Label>
              <Input
                id="new_password"
                type="password"
                autoComplete="new-password"
                {...form.register("new_password")}
              />
              {form.formState.errors.new_password ? (
                <p className="text-xs text-destructive">
                  {form.formState.errors.new_password.message}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="confirm_password">确认新密码</Label>
              <Input
                id="confirm_password"
                type="password"
                autoComplete="new-password"
                {...form.register("confirm_password")}
              />
              {form.formState.errors.confirm_password ? (
                <p className="text-xs text-destructive">
                  {form.formState.errors.confirm_password.message}
                </p>
              ) : null}
            </div>

            <div className="flex gap-2 pt-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => navigate(-1)}
                disabled={submitting}
              >
                取消
              </Button>
              <Button type="submit" className="flex-1" disabled={submitting}>
                {submitting ? "提交中…" : "确认修改"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
