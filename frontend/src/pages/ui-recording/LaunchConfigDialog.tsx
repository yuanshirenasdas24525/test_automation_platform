import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Rocket } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { configApi, type ConfigItem } from "@/lib/api";

type Platform = "android" | "ios";

/** 项目级 App 启动配置——全用例共享的启动 capabilities，存到 app·launch 配置组。 */
export function LaunchConfigDialog({
  open,
  projectId,
  platform,
  onOpenChange,
}: {
  open: boolean;
  projectId: number;
  platform: Platform;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const isAndroid = platform === "android";

  const listQuery = useQuery({
    queryKey: ["project-config", projectId, "app"],
    queryFn: () => configApi.list("app", projectId),
    enabled: open,
  });

  // 拉到的 launch 组现值
  const current = useMemo(() => {
    const map: Record<string, string> = {};
    for (const it of listQuery.data ?? []) {
      if (it.config_group === "launch") map[it.config_key] = it.config_value ?? "";
    }
    return map;
  }, [listQuery.data]);

  const [form, setForm] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!open) return;
    setForm({
      appPackage: current.appPackage ?? "",
      appActivity: current.appActivity ?? "",
      bundleId: current.bundleId ?? "",
      automationName: current.automationName ?? "",
      platformVersion: current.platformVersion ?? "",
      noReset: current.noReset ?? "true",
      autoLaunch: current.autoLaunch ?? "true",
      extra_caps: current.extra_caps ?? "{}",
    });
  }, [open, current]);

  const set = (k: string, v: string) => setForm((prev) => ({ ...prev, [k]: v }));

  const saveMutation = useMutation({
    mutationFn: async () => {
      // 校验 extra_caps 是合法 JSON
      const extra = (form.extra_caps || "").trim() || "{}";
      try {
        const parsed = JSON.parse(extra);
        if (typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("额外 caps 必须是 JSON 对象");
        }
      } catch {
        throw new Error("额外 capabilities 不是合法 JSON 对象");
      }
      const keys = [
        "appPackage",
        "appActivity",
        "bundleId",
        "automationName",
        "platformVersion",
        "noReset",
        "autoLaunch",
        "extra_caps",
      ];
      for (const key of keys) {
        const body: Omit<ConfigItem, "id"> = {
          config_group: "launch",
          config_key: key,
          config_value: key === "extra_caps" ? extra : (form[key] ?? ""),
          category: "app",
          project_id: projectId,
        };
        await configApi.save(body);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project-config", projectId, "app"] });
      toast.success("启动配置已保存（全用例共享，下次运行生效）");
      onOpenChange(false);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "保存失败"),
  });

  const label = isAndroid ? "Android" : "iOS";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Rocket className="h-4 w-4 text-violet-600" />
            {label} 启动配置（项目级）
          </DialogTitle>
        </DialogHeader>

        {listQuery.isLoading ? (
          <div className="grid place-items-center py-10 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : (
          <div className="space-y-3 py-1 text-sm">
            <p className="rounded-md bg-muted/50 px-3 py-2 text-[11px] leading-5 text-muted-foreground">
              这些 capabilities 全项目用例共享——换被测 App / Activity 改这一处即可，
              用例的 app_launch 步骤无需再各带一份。想「首次进入不带 App」的状态：关掉
              <b> autoLaunch </b>并清空 {isAndroid ? "appPackage" : "bundleId"}。
            </p>

            {isAndroid ? (
              <>
                <Field label="appPackage（应用包名）" placeholder="com.saucelabs.mydemoapp.rn"
                  value={form.appPackage ?? ""} onChange={(v) => set("appPackage", v)} />
                <Field label="appActivity（启动 Activity，可留空用默认）" placeholder=".MainActivity"
                  value={form.appActivity ?? ""} onChange={(v) => set("appActivity", v)} />
              </>
            ) : (
              <Field label="bundleId（应用 Bundle ID）" placeholder="com.example.MyApp"
                value={form.bundleId ?? ""} onChange={(v) => set("bundleId", v)} />
            )}

            <Field label="automationName（留空按平台默认）"
              placeholder={isAndroid ? "UiAutomator2" : "XCUITest"}
              value={form.automationName ?? ""} onChange={(v) => set("automationName", v)} />
            <Field label="platformVersion（系统版本，可选）" placeholder={isAndroid ? "13" : "17.0"}
              value={form.platformVersion ?? ""} onChange={(v) => set("platformVersion", v)} />

            <div className="flex gap-6 pt-1">
              <BoolField label="noReset（退出不卸 App，推荐）"
                checked={(form.noReset ?? "true") === "true"}
                onChange={(b) => set("noReset", b ? "true" : "false")} />
              <BoolField label="autoLaunch（自动拉起 App）"
                checked={(form.autoLaunch ?? "true") === "true"}
                onChange={(b) => set("autoLaunch", b ? "true" : "false")} />
            </div>

            <div>
              <Label className="text-xs">额外 capabilities（JSON，key 自动加 appium: 前缀）</Label>
              <Textarea rows={3} className="mt-1 font-mono text-xs"
                placeholder='{"appium:dontStopAppOnReset": true}'
                value={form.extra_caps ?? ""} onChange={(e) => set("extra_caps", e.target.value)} />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>取消</Button>
          <Button size="sm" disabled={saveMutation.isPending || listQuery.isLoading}
            onClick={() => saveMutation.mutate()}>
            {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label, value, onChange, placeholder,
}: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div>
      <Label className="text-xs">{label}</Label>
      <Input className="mt-1" value={value} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function BoolField({
  label, checked, onChange,
}: { label: string; checked: boolean; onChange: (b: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-xs">
      <input type="checkbox" className="h-4 w-4 accent-primary"
        checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}
