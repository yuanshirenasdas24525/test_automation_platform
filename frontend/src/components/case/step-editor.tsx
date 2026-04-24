/**
 * StepEditor：Web / App 用例的步骤编辑器。
 *
 * 设计目标：
 *  - 一行 = 一个 TestStep。可添加、删除、上下移动；每行都能展开 / 折叠。
 *  - step_type 决定下面的 config 字段集合；切换 step_type 时会把已有 config 的
 *    已知字段保留下来（比如从 web_click 切到 web_input，by/locator 不丢）。
 *  - 所有"像 locator / value / script"这类字段都接 HighlightedTextarea，保留
 *    `${var}` / `$.path` / `function:xxx` 这些约定的语法高亮。
 *  - 不依赖 drag-and-drop 库：上移 / 下移用按钮，视觉最少、实现最稳。
 *
 * 和外部的契约：
 *  - 父组件把 `value: TestStepDraft[]` 传进来，本组件只"提出变更"通过 onChange 回传
 *    一个新数组（典型的受控列表模式）。step_order 由本组件负责在 onChange 时同步成
 *    数组下标，父组件不用管。
 *  - 支持 `category` 限制候选 step_type（"web" / "app" / "mixed"）；mixed 项目三种
 *    前缀都列出来。
 *
 * 为什么把 step_type 选项定义放在这一个文件里？
 *  每个 step_type 的 config 字段集非常零碎，逻辑上是"选项表 + 字段表 + 渲染器"的组合，
 *  跨文件传递会让"加一个新 step_type"变得很繁琐。集中放在这里，后续加步骤类型只要
 *  在 STEP_TYPE_SPECS 里新增一行就行。
 */
import * as React from "react";
import {
  ChevronDown,
  ChevronUp,
  GripVertical,
  Info,
  Plus,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { HighlightedTextarea } from "@/components/ui/highlighted-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { CaseType, TestStepDraft } from "@/types/domain";

// ---------------------------------------------------------------------------
// step_type 规格表 —— 加新类型时只改这里
// ---------------------------------------------------------------------------
type FieldKind =
  | "text" // 单行 Input
  | "number" // 数字 Input
  | "select" // 下拉（options 必填）
  | "highlight" // HighlightedTextarea：支持 ${var} / $. / function: / sql:
  | "bool"; // checkbox

interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  placeholder?: string;
  rows?: number;
  hint?: React.ReactNode;
  required?: boolean;
  options?: { value: string; label: string }[];
}

interface StepTypeSpec {
  value: string;
  group: "web" | "app" | "generic";
  label: string;
  desc?: string;
  /** 默认的 config，新增 step 时会带上，避免必填字段空着 */
  defaultConfig?: Record<string, unknown>;
  /** 这个 step_type 需要填的 config 字段 */
  fields: FieldSpec[];
  /** step_name 的默认值（支持函数，能读到当前 config） */
  defaultName: string | ((config: Record<string, unknown>) => string);
}

const BY_OPTIONS: FieldSpec["options"] = [
  { value: "css", label: "CSS 选择器" },
  { value: "xpath", label: "XPath" },
  { value: "id", label: "ID" },
  { value: "name", label: "name 属性" },
  { value: "class", label: "class name" },
  { value: "text", label: "文本包含" },
  { value: "link", label: "链接文本" },
];

const APP_BY_OPTIONS: FieldSpec["options"] = [
  { value: "id", label: "resource-id" },
  { value: "xpath", label: "XPath" },
  { value: "accessibility_id", label: "accessibility id" },
  { value: "android_uiautomator", label: "Android UiAutomator" },
  { value: "class", label: "className" },
];

const WAIT_STATE_OPTIONS: FieldSpec["options"] = [
  { value: "visible", label: "可见" },
  { value: "attached", label: "挂到 DOM" },
  { value: "hidden", label: "隐藏" },
  { value: "detached", label: "脱离 DOM" },
];

const SWIPE_DIR_OPTIONS: FieldSpec["options"] = [
  { value: "up", label: "向上" },
  { value: "down", label: "向下" },
  { value: "left", label: "向左" },
  { value: "right", label: "向右" },
];

export const STEP_TYPE_SPECS: StepTypeSpec[] = [
  // ---------- Web ----------
  {
    value: "web_goto",
    group: "web",
    label: "打开页面 (web_goto)",
    desc: "导航到指定 URL，会等页面 load 完",
    defaultConfig: { url: "", timeout: 30 },
    defaultName: (c) => `访问 ${c.url || "页面"}`,
    fields: [
      { key: "url", label: "URL", kind: "text", required: true, placeholder: "https://example.com/login" },
      { key: "timeout", label: "超时(秒)", kind: "number", placeholder: "30" },
    ],
  },
  {
    value: "web_click",
    group: "web",
    label: "点击 (web_click)",
    defaultConfig: { by: "css", locator: "", timeout: 10 },
    defaultName: (c) => `点击 ${c.locator || "元素"}`,
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: BY_OPTIONS, required: true },
      { key: "locator", label: "定位表达式", kind: "highlight", rows: 1, required: true,
        placeholder: "button.login", hint: <>支持 <code>$&#123;var&#125;</code> 变量</> },
      { key: "timeout", label: "超时(秒)", kind: "number" },
    ],
  },
  {
    value: "web_input",
    group: "web",
    label: "输入 (web_input)",
    defaultConfig: { by: "css", locator: "", value: "", clear_first: true, timeout: 10 },
    defaultName: (c) => `输入到 ${c.locator || "元素"}`,
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: BY_OPTIONS, required: true },
      { key: "locator", label: "定位表达式", kind: "highlight", rows: 1, required: true,
        placeholder: "#username" },
      { key: "value", label: "输入值", kind: "highlight", rows: 1, required: true,
        placeholder: "${username}",
        hint: <>支持 <code>$&#123;var&#125;</code> 和 <code>function:xxx()</code></> },
      { key: "clear_first", label: "输入前清空", kind: "bool" },
      { key: "timeout", label: "超时(秒)", kind: "number" },
    ],
  },
  {
    value: "web_select",
    group: "web",
    label: "下拉选择 (web_select)",
    desc: "value / label / index 三选一",
    defaultConfig: { by: "css", locator: "", timeout: 10 },
    defaultName: (c) => `选择 ${c.locator || "下拉"}`,
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: BY_OPTIONS, required: true },
      { key: "locator", label: "定位表达式", kind: "text", required: true, placeholder: "select#city" },
      { key: "value", label: "value", kind: "text", placeholder: "BJ" },
      { key: "label", label: "label", kind: "text", placeholder: "北京" },
      { key: "index", label: "index", kind: "number", placeholder: "0" },
      { key: "timeout", label: "超时(秒)", kind: "number" },
    ],
  },
  {
    value: "web_wait",
    group: "web",
    label: "等待 (web_wait)",
    desc: "填 by/locator 等元素状态；不填则纯 sleep",
    defaultConfig: { state: "visible", timeout: 10 },
    defaultName: (c) => (c.locator ? `等待 ${c.locator}` : `sleep ${c.seconds || 1}s`),
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: BY_OPTIONS },
      { key: "locator", label: "定位表达式", kind: "text", placeholder: "（留空则纯 sleep）" },
      { key: "state", label: "目标状态", kind: "select", options: WAIT_STATE_OPTIONS },
      { key: "timeout", label: "超时(秒)", kind: "number" },
      { key: "seconds", label: "纯 sleep 秒数", kind: "number", hint: <>只在没填 locator 时生效</> },
    ],
  },
  {
    value: "web_screenshot",
    group: "web",
    label: "截图 (web_screenshot)",
    defaultConfig: { name: "screenshot.png" },
    defaultName: "截图",
    fields: [
      { key: "name", label: "文件名", kind: "text", placeholder: "home.png" },
      { key: "path", label: "保存路径(可选)", kind: "text",
        placeholder: "data/screenshots/xxx.png" },
    ],
  },
  {
    value: "web_assert_text",
    group: "web",
    label: "断言文本 (web_assert_text)",
    desc: "equals / contains / regex 三选一",
    defaultConfig: { by: "css", locator: "", timeout: 10 },
    defaultName: (c) => `断言 ${c.locator || "元素"} 文本`,
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: BY_OPTIONS, required: true },
      { key: "locator", label: "定位表达式", kind: "text", required: true, placeholder: "h1" },
      { key: "equals", label: "equals", kind: "highlight", rows: 1 },
      { key: "contains", label: "contains", kind: "highlight", rows: 1 },
      { key: "regex", label: "regex", kind: "text" },
      { key: "timeout", label: "超时(秒)", kind: "number" },
    ],
  },
  {
    value: "web_evaluate",
    group: "web",
    label: "执行 JS (web_evaluate)",
    defaultConfig: { script: "" },
    defaultName: "执行 JS",
    fields: [
      { key: "script", label: "脚本", kind: "highlight", rows: 4, required: true,
        placeholder: "return document.title;" },
      { key: "save_as", label: "保存到变量", kind: "text",
        hint: <>填了就把返回值存进变量池，后面的 step 能用 <code>$&#123;xxx&#125;</code></> },
    ],
  },

  // ---------- App ----------
  {
    value: "app_tap",
    group: "app",
    label: "点击 (app_tap)",
    defaultConfig: { by: "id", locator: "", timeout: 10 },
    defaultName: (c) => `点击 ${c.locator || "元素"}`,
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: APP_BY_OPTIONS, required: true },
      { key: "locator", label: "定位表达式", kind: "highlight", rows: 1, required: true },
      { key: "timeout", label: "超时(秒)", kind: "number" },
      // Radix Select 不允许空字符串 value；留空（不选）本身就等价于"不滑动"，
      // 所以只给真正需要滑动的两个选项。
      { key: "sliding_location", label: "滑动查找", kind: "select",
        placeholder: "不滑动（可选）",
        options: [
          { value: "vertical", label: "上下滑动查找" },
          { value: "horizontal", label: "左右滑动查找" },
        ] },
    ],
  },
  {
    value: "app_input",
    group: "app",
    label: "输入 (app_input)",
    defaultConfig: { by: "id", locator: "", value: "", clear_first: true, timeout: 10 },
    defaultName: (c) => `输入到 ${c.locator || "元素"}`,
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: APP_BY_OPTIONS, required: true },
      { key: "locator", label: "定位表达式", kind: "highlight", rows: 1, required: true },
      { key: "value", label: "输入值", kind: "highlight", rows: 1, required: true,
        placeholder: "${phone}" },
      { key: "clear_first", label: "输入前清空", kind: "bool" },
      { key: "timeout", label: "超时(秒)", kind: "number" },
    ],
  },
  {
    value: "app_swipe",
    group: "app",
    label: "滑动 (app_swipe)",
    defaultConfig: { direction: "up", ratio: 0.5, duration: 500 },
    defaultName: (c) => `滑动 ${c.direction || ""}`,
    fields: [
      { key: "direction", label: "方向", kind: "select", options: SWIPE_DIR_OPTIONS },
      { key: "ratio", label: "相对屏幕比例", kind: "number", placeholder: "0.5" },
      { key: "duration", label: "持续 ms", kind: "number", placeholder: "500" },
    ],
  },
  {
    value: "app_wait",
    group: "app",
    label: "等待 (app_wait)",
    defaultName: (c) => (c.locator ? `等待 ${c.locator}` : `sleep ${c.seconds || 1}s`),
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: APP_BY_OPTIONS },
      { key: "locator", label: "定位表达式", kind: "text", placeholder: "（留空则纯 sleep）" },
      { key: "timeout", label: "超时(秒)", kind: "number" },
      { key: "seconds", label: "纯 sleep 秒数", kind: "number" },
    ],
  },
  {
    value: "app_launch",
    group: "app",
    label: "启动 App (app_launch)",
    defaultConfig: { appPackage: "", appActivity: "" },
    defaultName: (c) => `启动 ${c.appPackage || "App"}`,
    fields: [
      { key: "appPackage", label: "appPackage", kind: "text", placeholder: "com.example.app" },
      { key: "appActivity", label: "appActivity", kind: "text", placeholder: ".MainActivity" },
    ],
  },
  { value: "app_close", group: "app", label: "关闭 App (app_close)", defaultName: "关闭 App", fields: [] },
  { value: "app_back", group: "app", label: "返回键 (app_back)", defaultName: "返回", fields: [] },
  {
    value: "app_press",
    group: "app",
    label: "按键码 (app_press)",
    defaultConfig: { keycode: 4 },
    defaultName: (c) => `press ${c.keycode ?? ""}`,
    fields: [
      { key: "keycode", label: "Android keycode", kind: "number", required: true, placeholder: "4" },
    ],
  },
  {
    value: "app_screenshot",
    group: "app",
    label: "截图 (app_screenshot)",
    defaultConfig: { name: "screenshot.png" },
    defaultName: "截图",
    fields: [
      { key: "name", label: "文件名", kind: "text" },
      { key: "path", label: "保存路径(可选)", kind: "text" },
    ],
  },

  // ---------- 通用 ----------
  {
    value: "sleep",
    group: "generic",
    label: "等待 (sleep)",
    defaultConfig: { seconds: 1 },
    defaultName: (c) => `sleep ${c.seconds ?? 1}s`,
    fields: [{ key: "seconds", label: "秒数", kind: "number", required: true }],
  },
];

function findSpec(stepType: string | undefined | null): StepTypeSpec | undefined {
  if (!stepType) return undefined;
  return STEP_TYPE_SPECS.find((s) => s.value === stepType);
}

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------
export interface StepEditorProps {
  /** Web / App / Mixed —— 决定下拉里列哪些 step_type */
  category: CaseType;
  value: TestStepDraft[];
  onChange: (next: TestStepDraft[]) => void;
  /** 从父组件传下来的错误提示（比如必填为空，父表单用 form.setError 填进来） */
  error?: string | null;
}

export function StepEditor({ category, value, onChange, error }: StepEditorProps) {
  // 新建 step 时，默认选的 step_type 依赖 category：
  const defaultNewType = React.useMemo(() => {
    if (category === "web") return "web_goto";
    if (category === "app") return "app_launch";
    return "web_goto"; // mixed 时先默认 web
  }, [category]);

  const allowedGroups = React.useMemo(() => {
    if (category === "web") return new Set(["web", "generic"]);
    if (category === "app") return new Set(["app", "generic"]);
    return new Set(["web", "app", "generic"]); // mixed
  }, [category]);

  const availableSpecs = STEP_TYPE_SPECS.filter((s) => allowedGroups.has(s.group));

  const setStep = (i: number, next: TestStepDraft) => {
    const arr = value.slice();
    arr[i] = { ...next, step_order: i };
    onChange(arr);
  };
  const removeStep = (i: number) => {
    const arr = value.slice();
    arr.splice(i, 1);
    onChange(arr.map((s, idx) => ({ ...s, step_order: idx })));
  };
  const moveStep = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= value.length) return;
    const arr = value.slice();
    [arr[i], arr[j]] = [arr[j], arr[i]];
    onChange(arr.map((s, idx) => ({ ...s, step_order: idx })));
  };
  const addStep = () => {
    const spec = findSpec(defaultNewType) ?? availableSpecs[0];
    if (!spec) return;
    const cfg = { ...(spec.defaultConfig || {}) };
    const newStep: TestStepDraft = {
      step_order: value.length,
      step_type: spec.value,
      step_name: typeof spec.defaultName === "function" ? spec.defaultName(cfg) : spec.defaultName,
      skip: false,
      config: cfg,
      wait_before: 0,
      timeout: 30,
      retry: 0,
      on_failure: "stop",
    };
    onChange([...value, newStep]);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Label className="text-sm">
          步骤（{value.length}）
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            按从上到下的顺序执行
          </span>
        </Label>
        <Button type="button" size="sm" variant="outline" onClick={addStep}>
          <Plus className="mr-1 h-4 w-4" />
          添加步骤
        </Button>
      </div>

      {error ? (
        <p className="rounded border border-destructive/40 bg-destructive/5 px-2 py-1 text-xs text-destructive">
          {error}
        </p>
      ) : null}

      {value.length === 0 ? (
        <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
          还没有步骤 —— 点击"添加步骤"开始编排
        </div>
      ) : (
        <div className="space-y-2">
          {value.map((step, i) => (
            <StepRow
              key={i}
              index={i}
              total={value.length}
              step={step}
              specs={availableSpecs}
              onChange={(next) => setStep(i, next)}
              onRemove={() => removeStep(i)}
              onMove={(dir) => moveStep(i, dir)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单行
// ---------------------------------------------------------------------------
function StepRow({
  index,
  total,
  step,
  specs,
  onChange,
  onRemove,
  onMove,
}: {
  index: number;
  total: number;
  step: TestStepDraft;
  specs: StepTypeSpec[];
  onChange: (next: TestStepDraft) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
}) {
  const [expanded, setExpanded] = React.useState(index === 0 || !step.step_type);
  const spec = findSpec(step.step_type);

  const handleTypeChange = (newType: string) => {
    const newSpec = findSpec(newType);
    if (!newSpec) return;
    // 保留已有 config 里与新 spec 共有字段的值；不共有的丢掉
    const keepKeys = new Set(newSpec.fields.map((f) => f.key));
    const nextConfig: Record<string, unknown> = { ...(newSpec.defaultConfig || {}) };
    for (const [k, v] of Object.entries(step.config || {})) {
      if (keepKeys.has(k) && v !== undefined) nextConfig[k] = v;
    }
    // step_name：如果用户没改过（看起来像旧 spec 的默认名就重算）；简化：总是重算
    const nextName = typeof newSpec.defaultName === "function"
      ? newSpec.defaultName(nextConfig)
      : newSpec.defaultName;
    onChange({
      ...step,
      step_type: newType,
      step_name: step.step_name?.trim() ? step.step_name : nextName,
      config: nextConfig,
    });
  };

  const setConfig = (key: string, val: unknown) => {
    onChange({ ...step, config: { ...(step.config || {}), [key]: val } });
  };

  return (
    <div className={cn(
      "rounded-md border bg-card p-2",
      step.skip && "opacity-60",
    )}>
      <div className="flex items-center gap-2">
        <GripVertical className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
          #{index + 1}
        </span>

        {/* step_type 选择 */}
        <div className="w-48 shrink-0">
          <Select value={step.step_type} onValueChange={handleTypeChange}>
            <SelectTrigger className="h-8 text-xs">
              <SelectValue placeholder="选择类型" />
            </SelectTrigger>
            <SelectContent>
              {groupSpecsByGroup(specs).map(([groupName, gs]) => (
                <SelectGroup key={groupName}>
                  <SelectLabel>{labelOfGroup(groupName)}</SelectLabel>
                  {gs.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* step_name */}
        <Input
          className="h-8 flex-1 text-xs"
          placeholder="步骤名称（可选）"
          value={step.step_name ?? ""}
          onChange={(e) => onChange({ ...step, step_name: e.target.value })}
        />

        {/* 操作按钮 */}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          title="上移"
          onClick={() => onMove(-1)}
          disabled={index === 0}
        >
          <ChevronUp className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          title="下移"
          onClick={() => onMove(1)}
          disabled={index === total - 1}
        >
          <ChevronDown className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          title={expanded ? "折叠" : "展开"}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? (
            <ChevronUp className="h-4 w-4 rotate-180" />
          ) : (
            <ChevronDown className="h-4 w-4 rotate-180" />
          )}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-destructive hover:text-destructive"
          title="删除"
          onClick={onRemove}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      {expanded ? (
        <div className="mt-2 space-y-2 rounded bg-muted/30 p-2">
          {spec?.desc ? (
            <p className="flex items-start gap-1 text-xs text-muted-foreground">
              <Info className="mt-0.5 h-3 w-3 shrink-0" />
              {spec.desc}
            </p>
          ) : null}

          {spec && spec.fields.length > 0 ? (
            <div className="grid grid-cols-2 gap-2">
              {spec.fields.map((field) => (
                <FieldRenderer
                  key={field.key}
                  field={field}
                  value={step.config?.[field.key]}
                  onChange={(v) => setConfig(field.key, v)}
                />
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">这一步没有可配置的参数</p>
          )}

          {/* 高级控制：timeout / retry / on_failure / skip / wait_before */}
          <details className="mt-2">
            <summary className="cursor-pointer select-none text-xs text-muted-foreground hover:text-foreground">
              高级选项（超时 / 重试 / 失败处理 / 跳过）
            </summary>
            <div className="mt-2 grid grid-cols-4 gap-2">
              <div className="space-y-1">
                <Label className="text-xs">等待(秒)</Label>
                <Input
                  type="number"
                  min={0}
                  value={step.wait_before ?? 0}
                  onChange={(e) =>
                    onChange({ ...step, wait_before: Number(e.target.value) || 0 })
                  }
                  className="h-8 text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">超时</Label>
                <Input
                  type="number"
                  min={1}
                  value={step.timeout ?? 30}
                  onChange={(e) =>
                    onChange({ ...step, timeout: Number(e.target.value) || 30 })
                  }
                  className="h-8 text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">重试次数</Label>
                <Input
                  type="number"
                  min={0}
                  value={step.retry ?? 0}
                  onChange={(e) => onChange({ ...step, retry: Number(e.target.value) || 0 })}
                  className="h-8 text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">失败处理</Label>
                <Select
                  value={step.on_failure ?? "stop"}
                  onValueChange={(v) =>
                    onChange({ ...step, on_failure: v as TestStepDraft["on_failure"] })
                  }
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="stop">stop 中断</SelectItem>
                    <SelectItem value="continue">continue 继续</SelectItem>
                    <SelectItem value="retry">retry 重试</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <label className="mt-2 flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={!!step.skip}
                onChange={(e) => onChange({ ...step, skip: e.target.checked })}
              />
              跳过执行 (skip=true)
            </label>
          </details>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单个字段的渲染器：根据 FieldSpec.kind 分发
// ---------------------------------------------------------------------------
function FieldRenderer({
  field,
  value,
  onChange,
}: {
  field: FieldSpec;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const id = `step-field-${field.key}`;
  // highlight 可能跨两列
  const wrapperClass = field.kind === "highlight" && (field.rows ?? 1) > 1
    ? "col-span-2 space-y-1"
    : "space-y-1";

  return (
    <div className={wrapperClass}>
      <Label htmlFor={id} className="text-xs">
        {field.label}
        {field.required ? <span className="ml-0.5 text-destructive">*</span> : null}
      </Label>
      {field.kind === "text" ? (
        <Input
          id={id}
          className="h-8 text-xs"
          placeholder={field.placeholder}
          value={stringify(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : null}
      {field.kind === "number" ? (
        <Input
          id={id}
          type="number"
          className="h-8 text-xs"
          placeholder={field.placeholder}
          value={value == null || value === "" ? "" : String(value)}
          onChange={(e) =>
            onChange(e.target.value === "" ? undefined : Number(e.target.value))
          }
        />
      ) : null}
      {field.kind === "select" && field.options ? (
        <Select
          // Radix Select 对空字符串非常敏感 —— 我们把空/undefined 作为"未选"，
          // 给 Select 一个 undefined（而不是 ""）触发 placeholder 状态。
          value={value == null || value === "" ? undefined : stringify(value)}
          onValueChange={(v) => onChange(v)}
        >
          <SelectTrigger id={id} className="h-8 text-xs">
            <SelectValue placeholder={field.placeholder ?? "请选择"} />
          </SelectTrigger>
          <SelectContent>
            {field.options.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}
      {field.kind === "bool" ? (
        <label className="flex h-8 items-center gap-2 text-xs">
          <input
            id={id}
            type="checkbox"
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
          />
          {field.placeholder ?? "是"}
        </label>
      ) : null}
      {field.kind === "highlight" ? (
        <HighlightedTextarea
          rows={field.rows ?? 1}
          placeholder={field.placeholder}
          value={stringify(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : null}
      {field.hint ? (
        <p className="text-[11px] leading-tight text-muted-foreground">{field.hint}</p>
      ) : null}
    </div>
  );
}

function stringify(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  return String(v);
}

// ---------------------------------------------------------------------------
// 杂项
// ---------------------------------------------------------------------------
function groupSpecsByGroup(specs: StepTypeSpec[]): [string, StepTypeSpec[]][] {
  const map = new Map<string, StepTypeSpec[]>();
  for (const s of specs) {
    const arr = map.get(s.group) || [];
    arr.push(s);
    map.set(s.group, arr);
  }
  // 固定顺序：web → app → generic
  const order = ["web", "app", "generic"];
  return order
    .filter((g) => map.has(g))
    .map((g) => [g, map.get(g)!]);
}

function labelOfGroup(g: string): string {
  if (g === "web") return "Web 步骤";
  if (g === "app") return "App 步骤";
  return "通用";
}
