import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Sparkles,
  ChevronDown,
  LayoutList,
  ClipboardList,
  FileText,
  SlidersHorizontal,
  Smartphone,
  Info,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { aiModelsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { FeatureChecklistPanel, PromptPreviewPanel, ConfigPreviewPanel } from "@/components/case/ai-gen-panels";
import { SideDrawer } from "@/components/ui/side-drawer";

/** 各平台与 Web 的差异文案（生成时的证据链、定位器、启动方式都不同）。 */
const PLATFORM_META = {
  android: {
    label: "Android",
    device: "Android 模拟器",
    app: "App 包名 / apk + 启动 Activity",
    locators: "resource-id、accessibility-id、UiAutomator、XPath",
    tips: [
      "首期仅支持模拟器，不连接真实设备。",
      "需先确认 App 包名、启动 Activity、登录状态与测试数据。",
      "系统权限弹窗、软键盘、手势、WebView 需要独立 Runner 支持。",
    ],
  },
  ios: {
    label: "iOS",
    device: "iOS 模拟器",
    app: "App Bundle ID + 启动配置",
    locators: "accessibility-id、Predicate、Class Chain、XPath",
    tips: [
      "首期仅支持模拟器，不连接真实设备。",
      "需先确认 App Bundle ID、启动配置、登录状态与测试数据。",
      "系统权限弹窗、软键盘、手势、WebView 需要独立 Runner 支持。",
    ],
  },
} as const;

export function MobileUiCaseGenerationDialog({
  open,
  projectId,
  initialModuleId,
  platform,
  onOpenChange,
}: {
  open: boolean;
  projectId: number;
  initialModuleId: number | null;
  platform: "android" | "ios";
  onOpenChange: (open: boolean) => void;
}) {
  const meta = PLATFORM_META[platform];
  const [wpanel, setWpanel] = useState<"cases" | "checklist" | "prompt" | "config">("cases");
  const [modelName, setModelName] = useState("");
  const [structureAssertions, setStructureAssertions] = useState(true);
  const [userPrompt, setUserPrompt] = useState("");

  const modelsQuery = useQuery({
    queryKey: ["ai-models", projectId],
    queryFn: () => aiModelsApi.list(projectId),
    enabled: open,
  });
  const enabledModels = useMemo(
    () => (modelsQuery.data ?? []).filter((m) => m.enabled),
    [modelsQuery.data],
  );
  useEffect(() => {
    if (modelName || enabledModels.length === 0) return;
    const preferred = enabledModels.find((m) => m.is_default) ?? enabledModels[0];
    setModelName(preferred.name);
  }, [enabledModels, modelName]);

  const navItem = (key: typeof wpanel, icon: React.ReactNode, label: string, chevron = false) => (
    <button
      type="button"
      onClick={() => setWpanel(key)}
      className={cn(
        "flex items-center gap-2.5 border-b border-l-2 px-4 py-3 text-left text-sm font-medium transition-colors",
        wpanel === key
          ? "border-l-primary bg-primary/5 text-primary"
          : "border-l-transparent text-muted-foreground hover:bg-muted/60",
      )}
    >
      {icon}
      <span className="flex-1">{label}</span>
      {chevron ? (
        <ChevronDown className={cn("h-4 w-4 shrink-0 transition-transform", wpanel === "cases" ? "rotate-180" : "")} />
      ) : null}
    </button>
  );

  return (
    <SideDrawer
      open={open}
      onClose={() => onOpenChange(false)}
      storageKey="mobile-ui-gen-drawer-width"
      defaultWidth={1120}
      minWidth={900}
      title={
        <>
          <Sparkles className="h-[17px] w-[17px] text-violet-600" />
          AI 生成 {meta.label} UI 自动化用例
        </>
      }
    >
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* 左栏：手风琴导航 —「用例」展开配置，其余点了在右侧显示 */}
        <div className="flex w-[340px] shrink-0 flex-col overflow-y-auto border-r bg-muted/20">
          {navItem("cases", <LayoutList className="h-4 w-4 shrink-0" />, "用例", true)}
          {wpanel === "cases" ? (
            <section className="border-b bg-background p-5">
              <div className="space-y-5">
                <div>
                  <Label>AI 模型</Label>
                  <Select value={modelName} onValueChange={setModelName}>
                    <SelectTrigger className="mt-1.5"><SelectValue placeholder="选择模型" /></SelectTrigger>
                    <SelectContent>
                      {enabledModels.map((item) => (
                        <SelectItem key={item.name} value={item.name}>
                          {item.name} · {item.provider}/{item.model}{item.is_default ? "（默认）" : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="rounded-lg border border-violet-200 bg-violet-50/70 p-3 text-xs text-violet-950">
                  <p className="font-medium">与 Web 的差异（{meta.label}）</p>
                  <ul className="mt-2 space-y-1.5 pl-1 text-[11px] leading-5">
                    <li>· 设备：{meta.device}（非浏览器）</li>
                    <li>· 启动：{meta.app}（非页面 URL）</li>
                    <li>· 定位器：{meta.locators}（非 CSS/Role）</li>
                    <li>· 证据链：基于页面树 / 控件属性，非 DOM 快照</li>
                  </ul>
                </div>

                <div className="space-y-2">
                  <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2.5 text-xs">
                    <input
                      type="checkbox"
                      checked={structureAssertions}
                      onChange={(e) => setStructureAssertions(e.target.checked)}
                      className="mt-0.5 h-4 w-4 accent-primary"
                    />
                    <span>
                      <span className="block font-medium">生成关键结构断言</span>
                      <span className="mt-0.5 block text-[11px] text-muted-foreground">对关键控件/文本生成可见性/文本断言。</span>
                    </span>
                  </label>
                </div>

                <div>
                  <Label htmlFor="mobile-ui-prompt">业务范围补充（可选）</Label>
                  <Textarea
                    id="mobile-ui-prompt"
                    value={userPrompt}
                    onChange={(e) => setUserPrompt(e.target.value)}
                    placeholder="例如：优先登录、首页导航；不要生成删除或支付流程。"
                    className="mt-1.5 min-h-20"
                  />
                </div>

                <div className="rounded-lg border border-amber-300 bg-amber-50/70 p-3 text-xs text-amber-900">
                  <p className="flex items-center gap-1.5 font-medium"><Info className="h-3.5 w-3.5" />移动端自动生成开放中</p>
                  <p className="mt-1 text-[11px] leading-5">
                    {meta.label} 需要基于模拟器页面树、控件 ID 和坐标稳定性重新建立证据门禁，生成能力将在模拟器证据链完成后开放。
                    现可先用右侧「配置预览 / 用例分类预览 / 提示词」了解将怎么测。
                  </p>
                  <ul className="mt-2 list-disc space-y-1 pl-4 text-[11px] leading-5 text-amber-800">
                    {meta.tips.map((t) => <li key={t}>{t}</li>)}
                  </ul>
                </div>

                <Button className="w-full" disabled title="移动端自动生成开放中">
                  <Sparkles className="h-4 w-4" />一键生成可执行草稿（开放中）
                </Button>
              </div>
            </section>
          ) : null}
          {navItem("checklist", <ClipboardList className="h-4 w-4 shrink-0" />, "用例分类预览")}
          {navItem("prompt", <FileText className="h-4 w-4 shrink-0" />, "提示词")}
          {navItem("config", <SlidersHorizontal className="h-4 w-4 shrink-0" />, "配置预览")}
        </div>

        {/* 右栏内容区 */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {wpanel === "config" ? (
            <div className="min-h-0 flex-1 p-5">
              <ConfigPreviewPanel projectId={projectId} category="app" modelName={modelName} />
            </div>
          ) : wpanel === "checklist" ? (
            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              <FeatureChecklistPanel moduleId={initialModuleId ?? null} modelName={modelName} requirementText={userPrompt} caseSignature="" mode={platform} />
            </div>
          ) : wpanel === "prompt" ? (
            <div className="min-h-0 flex-1 p-5">
              <PromptPreviewPanel moduleId={initialModuleId ?? null} mode={platform} coverage="standard" dimensions="" requirementText={userPrompt} />
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-8 text-center text-muted-foreground">
              <Smartphone className="h-10 w-10 text-violet-300" />
              <p className="max-w-md text-sm">
                左侧「用例」展开的是 {meta.label} 生成配置。移动端自动生成开放中；
                你可以先切到「配置预览 / 用例分类预览 / 提示词」了解设备/App 配置、该测哪些方面和将用的提示词。
              </p>
            </div>
          )}
        </div>
      </div>
    </SideDrawer>
  );
}
