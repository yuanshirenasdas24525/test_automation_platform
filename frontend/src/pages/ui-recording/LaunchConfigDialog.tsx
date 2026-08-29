import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Rocket, Info, Plus } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { configApi, devicesApi, appPackagesApi, type ConfigItem } from "@/lib/api";

type Platform = "android" | "ios";

/** 悬停 tips：一个 ⓘ 图标，鼠标指上去显示说明。
 *  align="right" 让气泡向左展开（右对齐），用于靠右的图标，避免超出弹窗被裁掉。 */
function InfoTip({ text, align = "center" }: { text: string; align?: "center" | "right" }) {
  const pos = align === "right" ? "right-0" : "left-1/2 -translate-x-1/2";
  return (
    <span className="group relative ml-1 inline-flex align-middle">
      <Info className="h-3.5 w-3.5 cursor-help text-muted-foreground/60" />
      <span className={`pointer-events-none absolute top-5 z-50 w-56 rounded-md border bg-popover px-2.5 py-1.5 text-[11px] font-normal leading-4 text-popover-foreground opacity-0 shadow-md transition-opacity duration-100 group-hover:opacity-100 ${pos}`}>
        {text}
      </span>
    </span>
  );
}

/** 各字段说明（悬停 tips 文案）。 */
const TIPS: Record<string, string> = {
  device: "选择运行设备。多台设备时指定一台跑这个项目；留空则按设备池自动挑一台空闲的。",
  app: "选择被测 App 安装包。选后自动填 appPackage/bundleId；设备上没装该 App 时用它安装（配合 noReset 只在缺失时装）。",
  appPackage: "Android 应用包名，如 com.example.app。adb shell pm list packages 可查。",
  appActivity: "Android 启动 Activity，如 .MainActivity。留空则用包的默认启动 Activity（更稳）。",
  bundleId: "iOS 应用 Bundle ID，如 com.example.MyApp。",
  automationName: "自动化引擎。留空按平台默认：Android→UiAutomator2，iOS→XCUITest。",
  platformVersion: "系统版本（如 13 / 17.0）。多设备时用来精确匹配目标设备。",
  noReset: "会话结束是否保留 App。开(推荐)：不卸载/不清数据，quit 快、App 不被反复卸装；关：每次回到干净状态但更慢。",
  autoLaunch: "会话建立时是否自动拉起 App。想「首次进入不带 App」的状态就关掉它，并清空包名。",
  extra_caps: "补充任何这里没列出的 Appium capability，JSON 对象；key 会自动加 appium: 前缀。",
};

/** capabilities 速查：常用 + 非常用，点「＋」插入 extra_caps。 */
const CAP_CHEATSHEET: { group: string; items: { key: string; value: unknown; desc: string }[] }[] = [
  {
    group: "常用",
    items: [
      { key: "appium:autoGrantPermissions", value: true, desc: "自动授予 App 所有运行时权限，跳过系统权限弹窗。" },
      { key: "appium:disableWindowAnimation", value: true, desc: "关闭窗口动画，找元素更快更稳（强烈建议开）。" },
      { key: "appium:newCommandTimeout", value: 3600, desc: "多少秒无命令后 Appium 自动结束会话（秒）。" },
      { key: "appium:appWaitActivity", value: "*", desc: "启动后等待出现的 Activity；启动页≠主页时用（支持通配 *）。" },
      { key: "appium:dontStopAppOnReset", value: true, desc: "reset 时不强杀 App，保留其运行态。" },
    ],
  },
  {
    group: "非常用",
    items: [
      { key: "appium:autoGrantPermissions", value: true, desc: "见上；权限弹窗挡住流程时很有用。" },
      { key: "appium:uiautomator2ServerLaunchTimeout", value: 60000, desc: "UIA2 服务启动超时（毫秒），设备慢时调大。" },
      { key: "appium:adbExecTimeout", value: 40000, desc: "单条 adb 命令超时（毫秒）。" },
      { key: "appium:skipServerInstallation", value: false, desc: "跳过 UIA2 server 安装（复用已装的，加速；不稳时设 false 重装）。" },
      { key: "appium:ignoreHiddenApiPolicyError", value: true, desc: "忽略隐藏 API 策略报错（部分 ROM/系统应用需要）。" },
      { key: "appium:mjpegServerPort", value: 7810, desc: "实时画面 MJPEG 推流端口，需要镜像时用。" },
    ],
  },
];

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
  const devicesQuery = useQuery({
    queryKey: ["devices", platform],
    queryFn: () => devicesApi.list({ platform }),
    enabled: open,
  });
  const packagesQuery = useQuery({
    queryKey: ["app-packages", platform, projectId],
    queryFn: () => appPackagesApi.list({ platform, project_id: projectId }),
    enabled: open,
  });

  const current = useMemo(() => {
    const map: Record<string, string> = {};
    for (const it of listQuery.data ?? []) {
      if (it.config_group === "launch") map[it.config_key] = it.config_value ?? "";
    }
    return map;
  }, [listQuery.data]);

  const [form, setForm] = useState<Record<string, string>>({});
  const [showCheatsheet, setShowCheatsheet] = useState(false);
  useEffect(() => {
    if (!open) return;
    setForm({
      udid: current.udid ?? "",
      deviceName: current.deviceName ?? "",
      app: current.app ?? "",
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

  // 选设备：填 udid / deviceName / platformVersion
  const onPickDevice = (udid: string) => {
    const dev = (devicesQuery.data ?? []).find((d) => d.udid === udid);
    if (!dev) { set("udid", ""); return; }
    setForm((prev) => ({
      ...prev,
      udid: dev.udid,
      deviceName: dev.device_name || dev.udid,
      platformVersion: dev.platform_version || prev.platformVersion,
    }));
  };
  // 选安装包：填 appPackage/bundleId + app 路径
  const onPickPackage = (idStr: string) => {
    const pkg = (packagesQuery.data ?? []).find((p) => String(p.id) === idStr);
    if (!pkg) return;
    setForm((prev) => ({
      ...prev,
      app: pkg.file_path || prev.app,
      ...(isAndroid
        ? { appPackage: pkg.app_package || prev.appPackage }
        : { bundleId: pkg.bundle_id || prev.bundleId }),
    }));
  };

  const insertCap = (key: string, value: unknown) => {
    setForm((prev) => {
      let obj: Record<string, unknown> = {};
      try {
        const parsed = JSON.parse((prev.extra_caps || "{}").trim() || "{}");
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) obj = parsed;
      } catch { /* 保持空 */ }
      obj[key] = value;
      return { ...prev, extra_caps: JSON.stringify(obj, null, 2) };
    });
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const extra = (form.extra_caps || "").trim() || "{}";
      try {
        const parsed = JSON.parse(extra);
        if (typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      } catch {
        throw new Error("额外 capabilities 不是合法 JSON 对象");
      }
      const keys = [
        "udid", "deviceName", "app", "appPackage", "appActivity", "bundleId",
        "automationName", "platformVersion", "noReset", "autoLaunch", "extra_caps",
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
  const devices = devicesQuery.data ?? [];
  const packages = packagesQuery.data ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] w-full max-w-lg overflow-y-auto overflow-x-hidden">
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
          <div className="min-w-0 space-y-3 py-1 text-sm">
            <p className="rounded-md bg-muted/50 px-3 py-2 text-[11px] leading-5 text-muted-foreground">
              这些 capabilities 全项目用例共享——换设备 / 换 App 改这一处即可，用例的
              app_launch 步骤无需再各带一份。
            </p>

            {/* 设备选择 */}
            <div>
              <Label className="text-xs">运行设备<InfoTip text={TIPS.device} /></Label>
              <Select value={form.udid || "__auto__"} onValueChange={(v) => onPickDevice(v === "__auto__" ? "" : v)}>
                <SelectTrigger className="mt-1"><SelectValue placeholder="按设备池自动挑" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__auto__">按设备池自动挑（不指定）</SelectItem>
                  {devices.map((d) => (
                    <SelectItem key={d.udid} value={d.udid}>
                      {d.device_name || d.udid} · {d.udid}{d.platform_version ? ` · v${d.platform_version}` : ""} · {d.status}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* 安装包选择 */}
            <div>
              <Label className="text-xs">被测安装包<InfoTip text={TIPS.app} /></Label>
              <Select value={packages.find((p) => p.file_path === form.app)?.id?.toString() ?? ""}
                onValueChange={onPickPackage}>
                <SelectTrigger className="mt-1"><SelectValue placeholder="选择已上传的 apk/ipa（可选）" /></SelectTrigger>
                <SelectContent>
                  {packages.length === 0 ? (
                    <div className="px-2 py-1.5 text-[11px] text-muted-foreground">该平台暂无已上传安装包</div>
                  ) : packages.map((p) => (
                    <SelectItem key={p.id} value={String(p.id)}>
                      {p.name || p.file_name}{p.version ? ` · v${p.version}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {form.app ? <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">当前：{form.app}</p> : null}
            </div>

            {isAndroid ? (
              <>
                <Field label="appPackage（应用包名）" tip={TIPS.appPackage} placeholder="com.saucelabs.mydemoapp.rn"
                  value={form.appPackage ?? ""} onChange={(v) => set("appPackage", v)} />
                <Field label="appActivity（启动 Activity，可留空）" tip={TIPS.appActivity} placeholder=".MainActivity"
                  value={form.appActivity ?? ""} onChange={(v) => set("appActivity", v)} />
              </>
            ) : (
              <Field label="bundleId（应用 Bundle ID）" tip={TIPS.bundleId} placeholder="com.example.MyApp"
                value={form.bundleId ?? ""} onChange={(v) => set("bundleId", v)} />
            )}

            <Field label="automationName（留空按平台默认）" tip={TIPS.automationName}
              placeholder={isAndroid ? "UiAutomator2" : "XCUITest"}
              value={form.automationName ?? ""} onChange={(v) => set("automationName", v)} />
            <Field label="platformVersion（系统版本，可选）" tip={TIPS.platformVersion} placeholder={isAndroid ? "13" : "17.0"}
              value={form.platformVersion ?? ""} onChange={(v) => set("platformVersion", v)} />

            <div className="flex gap-6 pt-1">
              <BoolField label="noReset" tip={TIPS.noReset}
                checked={(form.noReset ?? "true") === "true"}
                onChange={(b) => set("noReset", b ? "true" : "false")} />
              <BoolField label="autoLaunch" tip={TIPS.autoLaunch}
                checked={(form.autoLaunch ?? "true") === "true"}
                onChange={(b) => set("autoLaunch", b ? "true" : "false")} />
            </div>

            <div>
              <div className="flex items-center justify-between">
                <Label className="text-xs">额外 capabilities（JSON）<InfoTip text={TIPS.extra_caps} /></Label>
                <button type="button" className="text-[11px] text-primary hover:underline"
                  onClick={() => setShowCheatsheet((v) => !v)}>
                  {showCheatsheet ? "收起速查" : "capabilities 速查"}
                </button>
              </div>
              <Textarea rows={3} className="mt-1 font-mono text-xs"
                placeholder='{"appium:disableWindowAnimation": true}'
                value={form.extra_caps ?? ""} onChange={(e) => set("extra_caps", e.target.value)} />
              {showCheatsheet ? (
                <div className="mt-2 space-y-2 rounded-md border bg-muted/30 p-2">
                  {CAP_CHEATSHEET.map((sec) => (
                    <div key={sec.group}>
                      <div className="mb-1 text-[10px] font-medium text-muted-foreground">{sec.group}</div>
                      <div className="space-y-0.5">
                        {sec.items.map((it) => (
                          <div key={sec.group + it.key} className="flex min-w-0 items-center gap-1.5 text-[11px]">
                            <button type="button" title="插入到额外 caps"
                              className="grid h-4 w-4 shrink-0 place-items-center rounded border hover:bg-background"
                              onClick={() => insertCap(it.key, it.value)}>
                              <Plus className="h-3 w-3" />
                            </button>
                            <code className="min-w-0 flex-1 truncate font-mono text-[10px]">{it.key}</code>
                            <InfoTip text={it.desc} align="right" />
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
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
  label, tip, value, onChange, placeholder,
}: { label: string; tip?: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div>
      <Label className="text-xs">{label}{tip ? <InfoTip text={tip} /> : null}</Label>
      <Input className="mt-1" value={value} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function BoolField({
  label, tip, checked, onChange,
}: { label: string; tip?: string; checked: boolean; onChange: (b: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-xs">
      <input type="checkbox" className="h-4 w-4 accent-primary"
        checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}{tip ? <InfoTip text={tip} /> : null}
    </label>
  );
}
