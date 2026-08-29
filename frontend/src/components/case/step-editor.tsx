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
  ChevronRight,
  ChevronUp,
  GripVertical,
  Info,
  Plus,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { HighlightedTextarea } from "@/components/ui/highlighted-textarea";
import {
  JsonValidateButton,
  checkAssertion,
  checkExtract,
  checkHeaders,
  checkJson,
  type JsonCheck,
} from "@/lib/json-field";
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
import { cn, isInteractiveClickTarget, stripHtml } from "@/lib/utils";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { appPackagesApi, scriptsApi } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import { copyClipboard, useCopyClipboard } from "@/lib/case-clipboard";
import { canPasteStep, resolveCopyStepGroup } from "@/lib/copy-clone";
import type { CaseType, ScriptItem, TestStepDraft } from "@/types/domain";
import { UiElementPickerDialog } from "./ui-element-picker-dialog";

// ---------------------------------------------------------------------------
// step_type 规格表 —— 加新类型时只改这里
// ---------------------------------------------------------------------------
type FieldKind =
  | "text" // 单行 Input
  | "number" // 数字 Input
  | "select" // 下拉（options 必填）
  | "highlight" // HighlightedTextarea：支持 ${var} / $. / function: / sql:
  | "bool" // checkbox
  | "app_package" // 已上传 App 包选择器：下拉 = 已上传包，留空走输入框（可手填路径/URL）
  | "workflow_script"; // 当前项目 + 全局的 workflow 脚本

interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  placeholder?: string;
  rows?: number;
  hint?: React.ReactNode;
  required?: boolean;
  options?: { value: string; label: string }[];
  platforms?: Array<"android" | "ios">;
  /** highlight 字段：设了就在标题右侧显示「校验 JSON」按钮（格式化 ↔ 压缩一行） */
  jsonCheck?: (text: string) => JsonCheck;
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
  platforms?: Array<"android" | "ios">;
}

const BY_OPTIONS: FieldSpec["options"] = [
  { value: "css", label: "CSS 选择器" },
  { value: "xpath", label: "XPath" },
  { value: "id", label: "ID" },
  { value: "name", label: "name 属性" },
  { value: "class", label: "class name" },
  { value: "text", label: "文本包含" },
  { value: "link", label: "链接文本" },
  { value: "role", label: "ARIA 角色" },
];

// web_press 常用按键（Playwright key 名）
const KEY_OPTIONS: FieldSpec["options"] = [
  { value: "Escape", label: "Escape（关闭弹层/菜单）" },
  { value: "Enter", label: "Enter（提交/确认）" },
  { value: "Tab", label: "Tab（下一个焦点）" },
  { value: "ArrowDown", label: "↓ ArrowDown" },
  { value: "ArrowUp", label: "↑ ArrowUp" },
  { value: "Backspace", label: "Backspace" },
  { value: " ", label: "Space（空格）" },
];

// 后端 _AppiumBy.LOCATORS 注册了 14 种定位方式（见 core/mobile/finder/finder.py）。
// 这里一一对应，并按"最常用 → Android 专属 → iOS 专属 → 通用"的顺序排，方便用户找。
// ⚠️ value 必须与后端 key 一致 —— 历史上前端用过 'class' 而后端是 'class_name'，
// 直接导致选了"className"后 find 时 KeyError，这次保持一致。
const APP_BY_OPTIONS: FieldSpec["options"] = [
  // 最常用
  { value: "id", label: "resource-id / id" },
  { value: "xpath", label: "XPath" },
  { value: "accessibility_id", label: "accessibility id" },
  { value: "class_name", label: "className" },
  // Android 专属
  { value: "android_uiautomator", label: "Android UiAutomator" },
  { value: "android_viewtag", label: "Android ViewTag" },
  { value: "android_data_matcher", label: "Android DataMatcher" },
  { value: "android_view_matcher", label: "Android ViewMatcher" },
  // iOS 专属
  { value: "ios_predicate", label: "iOS Predicate" },
  { value: "ios_class_chain", label: "iOS ClassChain" },
  // 其它通用
  { value: "image", label: "Image（图像匹配）" },
  { value: "link_text", label: "Link Text" },
  { value: "css_selector", label: "CSS Selector" },
  { value: "name", label: "name 属性" },
];
const ANDROID_APP_BY_VALUES = new Set([
  "id",
  "xpath",
  "accessibility_id",
  "class_name",
  "android_uiautomator",
  "android_viewtag",
  "android_data_matcher",
  "android_view_matcher",
  "image",
  "link_text",
  "css_selector",
  "name",
]);
const IOS_APP_BY_VALUES = new Set([
  "id",
  "xpath",
  "accessibility_id",
  "class_name",
  "ios_predicate",
  "ios_class_chain",
  "image",
  "link_text",
  "css_selector",
  "name",
]);

// iOS / Android 都能用。留空表示"使用设备默认 automationName"（Android→UiAutomator2、iOS→XCUITest）。
const APP_AUTOMATION_OPTIONS: FieldSpec["options"] = [
  { value: "UiAutomator2", label: "UiAutomator2 (Android)" },
  { value: "XCUITest", label: "XCUITest (iOS)" },
  { value: "Espresso", label: "Espresso (Android)" },
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
    desc: "常规按定位器点；也可填坐标(x,y)按屏幕位置点(像手点、不认元素)，或勾强制点击跳过检查",
    defaultConfig: { by: "css", locator: "", timeout: 10 },
    defaultName: (c) =>
      c.x !== undefined && c.x !== "" && c.y !== undefined && c.y !== ""
        ? `点击坐标 (${c.x},${c.y})`
        : `点击 ${c.locator || "元素"}`,
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: BY_OPTIONS },
      { key: "locator", label: "定位表达式", kind: "highlight", rows: 1,
        placeholder: "button.login（与坐标二选一）",
        hint: <>支持 <code>$&#123;var&#125;</code> 变量；填了下方坐标则忽略此项</> },
      { key: "force", label: "强制点击（跳过可见/稳定/命中检查）", kind: "bool" },
      { key: "x", label: "坐标 X（可选）", kind: "number", placeholder: "留空=用定位器" },
      { key: "y", label: "坐标 Y（可选）", kind: "number" },
      { key: "timeout", label: "超时(秒)", kind: "number" },
    ],
  },
  {
    value: "web_press",
    group: "web",
    label: "按键 (web_press)",
    desc: "按键盘按键，如 Escape 关闭下拉/弹层；定位表达式留空则对整页按",
    defaultConfig: { key: "Escape", timeout: 10 },
    defaultName: (c) => `按 ${c.key || "Escape"}`,
    fields: [
      { key: "key", label: "按键", kind: "select", options: KEY_OPTIONS, required: true },
      { key: "by", label: "定位方式(可选)", kind: "select", options: BY_OPTIONS },
      { key: "locator", label: "定位表达式(可选)", kind: "highlight", rows: 1,
        placeholder: "（留空则对整页按键，关弹层通常留空即可）",
        hint: <>填了则先聚焦该元素再按键</> },
      { key: "timeout", label: "超时(秒)", kind: "number" },
    ],
  },
  {
    value: "web_drag",
    group: "web",
    label: "拖动 (web_drag)",
    desc: "从源元素按住拖到：目标元素 / 目标坐标(tx,ty) / 偏移(dx,dy)。用于进度条、滑块、拖拽排序、滑块验证",
    defaultConfig: { by: "css", locator: "", dx: 100, dy: 0, steps: 15, timeout: 10 },
    defaultName: (c) => `拖动 ${c.locator || "元素"}`,
    fields: [
      { key: "by", label: "源·定位方式", kind: "select", options: BY_OPTIONS, required: true },
      { key: "locator", label: "源·定位表达式", kind: "highlight", rows: 1, required: true,
        placeholder: "滑块/进度条把手，如 .slider-thumb" },
      { key: "to_by", label: "目标·定位方式(可选)", kind: "select", options: BY_OPTIONS },
      { key: "to_locator", label: "目标·定位表达式(可选)", kind: "highlight", rows: 1,
        placeholder: "拖到某元素上(拖拽排序)；不填则用下方偏移/坐标" },
      { key: "dx", label: "水平偏移 dx(px)", kind: "number", placeholder: "向右+ / 向左-" },
      { key: "dy", label: "垂直偏移 dy(px)", kind: "number", placeholder: "向下+ / 向上-" },
      { key: "tx", label: "目标坐标 X(可选)", kind: "number" },
      { key: "ty", label: "目标坐标 Y(可选)", kind: "number" },
      { key: "steps", label: "移动步数(越大越平滑)", kind: "number", placeholder: "15" },
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
    value: "web_assert_visual",
    group: "web",
    label: "视觉回归 (web_assert_visual)",
    desc: "固定视口下与人工确认的页面截图基线比较；动态区域可配置 masks。",
    defaultConfig: { baseline_path: "", threshold: 0.02, pixel_tolerance: 24, masks: [] },
    defaultName: "视觉回归断言",
    fields: [
      { key: "baseline_path", label: "基线图片路径", kind: "text", required: true, placeholder: "data/ui_recordings/...png" },
      { key: "threshold", label: "允许差异比例", kind: "number", placeholder: "0.02" },
      { key: "pixel_tolerance", label: "单像素容差", kind: "number", placeholder: "24" },
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
    desc: "按当前用例平台填写启动目标；automationName 留空则按设备平台默认",
    defaultConfig: { appPackage: "", appActivity: "" },
    defaultName: (c) =>
      `启动 ${c.bundleId || c.appPackage || "App"}`,
    fields: [
      // Android
      {
        key: "appPackage",
        label: "appPackage (Android)",
        kind: "text",
        placeholder: "com.example.app",
        platforms: ["android"],
        hint: <>Android 必填</>,
      },
      {
        key: "appActivity",
        label: "appActivity (Android)",
        kind: "text",
        placeholder: ".MainActivity",
        platforms: ["android"],
      },
      // iOS
      {
        key: "bundleId",
        label: "bundleId (iOS)",
        kind: "text",
        placeholder: "com.apple.mobilesafari",
        platforms: ["ios"],
        hint: <>iOS 必填</>,
      },
      // 共用
      {
        key: "automationName",
        label: "automationName",
        kind: "select",
        options: APP_AUTOMATION_OPTIONS,
        placeholder: "（留空 = 按平台默认）",
        hint: <>多数情况下留空即可。Android 默认 UiAutomator2、iOS 默认 XCUITest</>,
      },
      {
        key: "noReset",
        label: "noReset（保留登录态）",
        kind: "bool",
        hint: <>勾上后 Appium 不会清缓存，适合复用设备已有登录</>,
      },
      {
        key: "force_relaunch",
        label: "强制重启（force_relaunch）",
        kind: "bool",
        hint: <>默认关：App 跨用例复用，只首次启动一次（一条用例=一个测试点）。勾上后本步每次都把 App 重启回启动态——登录这类每条都要从登录页开始的用例建议勾上。</>,
      },
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
    platforms: ["android"],
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

  // 扩展能力：安装 / 卸载 / 激活 / 杀进程 / 切后台 / 屏幕方向 / 收键盘
  {
    value: "app_install",
    group: "app",
    label: "安装 App (app_install)",
    desc: "传本地 apk/ipa 路径或 URL（由 Appium server 下载）",
    defaultConfig: { app_path: "" },
    defaultName: (c) => `安装 ${c.app_path || "App"}`,
    fields: [
      { key: "app_path", label: "app_path", kind: "app_package", required: true,
        placeholder: "/path/to/app.apk 或 http://... ",
        hint: <>从「App 包管理」选已上传包，或手填本地路径 / URL；支持 <code>$&#123;var&#125;</code> 和 <code>sql:</code>/<code>function:</code></> },
    ],
  },
  {
    value: "app_uninstall",
    group: "app",
    label: "卸载 App (app_uninstall)",
    desc: "Android 填 appPackage；iOS 填 bundleId。二选一即可",
    defaultConfig: { appPackage: "" },
    defaultName: (c) => `卸载 ${c.bundleId || c.appPackage || "App"}`,
    fields: [
      { key: "appPackage", label: "appPackage (Android)", kind: "text",
        placeholder: "com.example.app", platforms: ["android"] },
      { key: "bundleId", label: "bundleId (iOS)", kind: "text",
        placeholder: "com.apple.mobilesafari", platforms: ["ios"] },
    ],
  },
  {
    value: "app_activate",
    group: "app",
    label: "激活 App (app_activate)",
    desc: "把已安装的应用唤到前台（不重建 session，区别于 app_launch）",
    defaultConfig: { appPackage: "" },
    defaultName: (c) => `激活 ${c.bundleId || c.appPackage || "App"}`,
    fields: [
      { key: "appPackage", label: "appPackage (Android)", kind: "text",
        placeholder: "com.example.app", platforms: ["android"] },
      { key: "bundleId", label: "bundleId (iOS)", kind: "text",
        placeholder: "com.apple.mobilesafari", platforms: ["ios"] },
    ],
  },
  {
    value: "app_terminate",
    group: "app",
    label: "杀进程 (app_terminate)",
    desc: "结束应用进程但不卸载",
    defaultConfig: { appPackage: "" },
    defaultName: (c) => `杀进程 ${c.bundleId || c.appPackage || "App"}`,
    fields: [
      { key: "appPackage", label: "appPackage (Android)", kind: "text",
        placeholder: "com.example.app", platforms: ["android"] },
      { key: "bundleId", label: "bundleId (iOS)", kind: "text",
        placeholder: "com.apple.mobilesafari", platforms: ["ios"] },
    ],
  },
  {
    value: "app_background",
    group: "app",
    label: "切后台 (app_background)",
    desc: "把当前应用切到后台 N 秒后回前台；-1 表示不自动回",
    defaultConfig: { seconds: 3 },
    defaultName: (c) => `切后台 ${c.seconds ?? 3}s`,
    fields: [
      { key: "seconds", label: "秒数", kind: "number", required: true,
        hint: <>建议 ≥ 1；<code>-1</code> 表示永久后台</> },
    ],
  },
  {
    value: "app_orientation",
    group: "app",
    label: "屏幕方向 (app_orientation)",
    defaultConfig: { orientation: "PORTRAIT" },
    defaultName: (c) => `旋转到 ${c.orientation || "PORTRAIT"}`,
    fields: [
      { key: "orientation", label: "方向", kind: "select", required: true,
        options: [
          { value: "PORTRAIT", label: "竖屏 PORTRAIT" },
          { value: "LANDSCAPE", label: "横屏 LANDSCAPE" },
        ] },
    ],
  },
  {
    value: "app_hide_keyboard",
    group: "app",
    label: "收键盘 (app_hide_keyboard)",
    desc: "软键盘已经收起时会被忽略（不判 case 失败）",
    defaultName: "收键盘",
    fields: [
      { key: "key_name", label: "key_name (仅 iOS)", kind: "text",
        placeholder: "Done",
        platforms: ["ios"],
        hint: <>iOS 上点击哪个键盘按钮收起；Android 留空即可</> },
    ],
  },

  {
    value: "app_assert_text",
    group: "app",
    label: "断言文本 (app_assert_text)",
    desc: "equals / contains / not_contains 三选一；三个都填时优先级 equals > contains > not_contains",
    defaultConfig: { by: "id", locator: "", timeout: 10 },
    defaultName: (c) => `断言 ${c.locator || "元素"} 文本`,
    fields: [
      { key: "by", label: "定位方式", kind: "select", options: APP_BY_OPTIONS, required: true },
      { key: "locator", label: "定位表达式", kind: "highlight", rows: 1, required: true,
        placeholder: "com.example:id/title" },
      { key: "equals", label: "equals", kind: "highlight", rows: 1,
        hint: <>与元素文本完全相等，支持 <code>$&#123;var&#125;</code></> },
      { key: "contains", label: "contains", kind: "highlight", rows: 1,
        hint: <>元素文本包含该子串</> },
      { key: "not_contains", label: "not_contains", kind: "highlight", rows: 1,
        hint: <>元素文本不包含该子串</> },
      { key: "timeout", label: "超时(秒)", kind: "number" },
    ],
  },

  // 通用入口：让用户调老 ActionRegistry / AssertionEngine 里的 30+ 动作和 11 种断言
  {
    value: "app_action",
    group: "app",
    label: "通用动作 (app_action)",
    desc: "按 action 名调用 ActionRegistry。30+ 内置动作：click / send_keys / get_attribute / handle_alert / set_orientation / start_screen_recording 等",
    defaultConfig: { action: "click", by: "id", locator: "", timeout: 10 },
    defaultName: (c) => `${c.action || "action"} ${c.locator || ""}`.trim(),
    fields: [
      { key: "action", label: "action 名", kind: "text", required: true,
        placeholder: "click",
        hint: <>注册名见 <code>core/mobile/actions/executor.py</code>；自定义动作可
          通过 <code>ActionRegistry.register</code> 注入</> },
      { key: "by", label: "定位方式", kind: "select", options: APP_BY_OPTIONS,
        placeholder: "（不需要元素时留空）" },
      { key: "locator", label: "定位表达式", kind: "highlight", rows: 1,
        placeholder: "com.example:id/btn" },
      { key: "value", label: "value（传给 action）", kind: "highlight", rows: 1,
        placeholder: "可填字符串 / ${var} / sql:... / function:...",
        hint: <>大多数 action 的第三个参数。无参 action 留空</> },
      { key: "timeout", label: "超时(秒)", kind: "number" },
      { key: "skip_element", label: "不查元素 (element=None)", kind: "bool",
        hint: <>勾上后不会按 by/locator 找元素，适用于 launch_app / get_clipboard
          这类不依赖元素的动作</> },
      { key: "save_as", label: "保存返回值到变量", kind: "text",
        hint: <>填了就把 action 的返回值存进变量池，后面 step 用
          <code>$&#123;xxx&#125;</code> 取</> },
    ],
  },
  {
    value: "app_assert",
    group: "app",
    label: "通用断言 (app_assert)",
    desc: "按 assert_type 走 AssertionEngine。11 种比较：equal / contains / gt / lt / length_*  等。需要数值或长度比较时用，纯文本断言用 app_assert_text 更简洁。",
    defaultConfig: { assert_type: "equal", by: "id", locator: "", attr: "text" },
    defaultName: (c) => `断言 ${c.assert_type || "equal"} ${c.expected ?? ""}`.trim(),
    fields: [
      { key: "assert_type", label: "断言类型", kind: "select", required: true,
        options: [
          { value: "equal", label: "equal（相等）" },
          { value: "not_equal", label: "not_equal（不等）" },
          { value: "gt", label: "gt（大于，按 float 比较）" },
          { value: "lt", label: "lt（小于，按 float 比较）" },
          { value: "contains", label: "contains（包含）" },
          { value: "not_contains", label: "not_contains（不含）" },
          { value: "empty", label: "empty（为空）" },
          { value: "not_empty", label: "not_empty（非空）" },
          { value: "length_equal", label: "length_equal（长度相等）" },
          { value: "length_gt", label: "length_gt（长度大于）" },
          { value: "length_lt", label: "length_lt（长度小于）" },
        ] },
      { key: "expected", label: "expected", kind: "highlight", rows: 1,
        placeholder: "支持 ${var} / sql:... / function:...",
        hint: <>期望值。empty / not_empty 不需要填</> },
      // actual 来源 A：元素属性
      { key: "by", label: "定位方式（A: 取元素属性）", kind: "select",
        options: APP_BY_OPTIONS,
        placeholder: "（如选项 B 直接给值则留空）" },
      { key: "locator", label: "定位表达式", kind: "highlight", rows: 1,
        placeholder: "com.example:id/title" },
      { key: "attr", label: "attr", kind: "text",
        placeholder: "text",
        hint: <>取元素的哪个属性。默认 <code>text</code>；可填
          <code>enabled</code> / <code>displayed</code> 或 Appium 支持的任意
          <code>get_attribute</code> 名</> },
      // actual 来源 B：直接给 value
      { key: "value", label: "value（B: 直接给值）", kind: "highlight", rows: 1,
        placeholder: "${var} / sql:... / function:... / 字面量",
        hint: <>与上面 by/locator 二选一。直接拿一个值跟 expected 比较</> },
      { key: "timeout", label: "超时(秒)", kind: "number" },
    ],
  },

  // ---------- 通用 ----------
  {
    value: "script",
    group: "generic",
    label: "项目脚本 (script)",
    desc: "在独立进程中执行脚本库的“项目逻辑”脚本。适用于账号准备、动态数据、外部系统调用、复杂断言和清理逻辑，API/Web/App 通用。",
    defaultConfig: { script_name: "", input: "{}", script_config: "{}", export_variables: true },
    defaultName: (c) => `运行脚本 ${String(c.script_name || "")}`.trim(),
    fields: [
      { key: "script_name", label: "项目逻辑脚本", kind: "workflow_script", required: true },
      { key: "input", label: "输入 JSON", kind: "highlight", rows: 4, jsonCheck: checkJson,
        placeholder: '{"username": "${username}"}',
        hint: <>支持嵌套 JSON、<code>$&#123;var&#125;</code> 和 <code>function:xxx()</code></> },
      { key: "script_config", label: "脚本配置 JSON", kind: "highlight", rows: 3, jsonCheck: checkJson,
        placeholder: '{"base_url": "https://example.test"}' },
      { key: "export_variables", label: "写回 variables", kind: "bool",
        hint: <>脚本返回 <code>{'{"variables": {"token": "..."}}'}</code> 后可在后续步骤使用 <code>$&#123;token&#125;</code></> },
      { key: "save_result_as", label: "完整结果保存到变量", kind: "text", placeholder: "script_result（可选）" },
    ],
  },
  {
    value: "http_request",
    group: "generic",
    label: "HTTP 请求 (http_request)",
    desc: "发一个接口请求。每个步骤独立填方法/路径/请求头/请求体/提取/断言。提取出的变量后续步骤可用 ${var} 引用。",
    defaultConfig: { method: "GET", data_type: "application/json" },
    defaultName: (c) => `${String(c.method || "GET")} ${String(c.path || "")}`.trim(),
    fields: [
      { key: "method", label: "方法", kind: "select", required: true,
        options: [
          { value: "GET", label: "GET" },
          { value: "POST", label: "POST" },
          { value: "PUT", label: "PUT" },
          { value: "PATCH", label: "PATCH" },
          { value: "DELETE", label: "DELETE" },
        ] },
      { key: "path", label: "路径", kind: "highlight", rows: 1, required: true,
        placeholder: "/api/auth/login（相对路径会拼 base_url，也可填完整 URL）" },
      { key: "data_type", label: "Content-Type", kind: "text",
        placeholder: "application/json" },
      { key: "headers", label: "请求头", kind: "highlight", rows: 2, jsonCheck: checkHeaders,
        placeholder: '{"Authorization": "Bearer ${token1}"}',
        hint: <>JSON 对象。支持 <code>{"${var}"}</code></> },
      { key: "params", label: "请求体 / 参数", kind: "highlight", rows: 3, jsonCheck: checkJson,
        placeholder: '{"username": "${generate_account}", "password": "NewTest@123"}',
        hint: <>JSON 对象。支持 <code>{"${var}"}</code> 和 <code>function:xxx()</code></> },
      { key: "extract_data", label: "提取参数", kind: "highlight", rows: 2, jsonCheck: checkExtract,
        placeholder: '{"token1": "$.data.token"}',
        hint: <>JSON 对象 <code>{"{ 变量名: $.json.path }"}</code>，提取出的变量后续步骤可 <code>{"${变量名}"}</code> 引用</> },
      { key: "assertion", label: "断言", kind: "highlight", rows: 2, jsonCheck: checkAssertion,
        placeholder: '{"status_code": 200, "$.data.token": "not_empty"}',
        hint: <>JSON 对象 <code>{"{ $.code: 0 }"}</code>；非空写 <code>not_empty</code></> },
      { key: "target_db_group", label: "数据库连接", kind: "select", options: [],
        placeholder: "未配置DB链接方式",
        hint: <>SQL 校验及 <code>sql:</code> 表达式使用的数据库连接</> },
      { key: "sql_query", label: "SQL 校验", kind: "highlight", rows: 2,
        placeholder: "select status from orders where id = ${order_id}",
        hint: <>可选。请求前 / 后查库，多条用 <code>;</code> 分隔</> },
    ],
  },
  {
    value: "assert",
    group: "generic",
    label: "断言 (assert)",
    desc: "对前面步骤提取出的变量做二次校验。target / expected 两边都支持 ${var}，可用来对比两次请求的结果（如 ${token1} ≠ ${token2}）。",
    defaultConfig: { type: "not_equal" },
    defaultName: (c) => `断言 ${String(c.type || "not_equal")} ${String(c.target ?? "")}`.trim(),
    fields: [
      { key: "type", label: "断言类型", kind: "select", required: true,
        options: [
          { value: "equal", label: "equal（相等）" },
          { value: "not_equal", label: "not_equal（不等）" },
          { value: "contains", label: "contains（包含）" },
          { value: "is_not_null", label: "is_not_null（非空）" },
          { value: "is_null", label: "is_null（为空）" },
        ] },
      { key: "target", label: "target", kind: "highlight", rows: 1, required: true,
        placeholder: "${token1}（或 $.data.token 取最近一次响应）" },
      { key: "expected", label: "expected", kind: "highlight", rows: 1,
        placeholder: "${token2} / 字面量；is_null / is_not_null 不用填" },
    ],
  },
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

function platformOf(category: CaseType): "android" | "ios" | null {
  if (category === "android" || category === "ios") return category;
  return null;
}

function isAllowedForPlatform(
  platforms: Array<"android" | "ios"> | undefined,
  platform: "android" | "ios" | null,
) {
  return !platforms || !platform || platforms.includes(platform);
}

function fieldsForPlatform(spec: StepTypeSpec, platform: "android" | "ios" | null) {
  return spec.fields.filter((field) => isAllowedForPlatform(field.platforms, platform));
}

function defaultConfigForPlatform(
  spec: StepTypeSpec,
  platform: "android" | "ios" | null,
) {
  const allowedKeys = new Set(fieldsForPlatform(spec, platform).map((field) => field.key));
  return Object.fromEntries(
    Object.entries(spec.defaultConfig || {}).filter(([key]) => allowedKeys.size === 0 || allowedKeys.has(key)),
  );
}

function hasText(value: unknown) {
  return typeof value === "string" ? value.trim().length > 0 : value != null && value !== "";
}

export function validateStepsForCategory(category: CaseType, steps: TestStepDraft[]) {
  const platform = platformOf(category);
  if (!platform) return [];
  const errors: string[] = [];
  steps.forEach((step, index) => {
    const order = index + 1;
    const config = (step.config ?? {}) as Record<string, unknown>;
    if (step.step_type === "app_launch") {
      if (platform === "android" && !hasText(config.appPackage)) {
        errors.push(`步骤 ${order}「启动 App」缺少 appPackage`);
      }
      if (platform === "ios" && !hasText(config.bundleId)) {
        errors.push(`步骤 ${order}「启动 App」缺少 bundleId`);
      }
    }
    if (
      step.step_type === "app_uninstall" ||
      step.step_type === "app_activate" ||
      step.step_type === "app_terminate"
    ) {
      if (platform === "android" && !hasText(config.appPackage)) {
        errors.push(`步骤 ${order}「${step.step_name || step.step_type}」缺少 appPackage`);
      }
      if (platform === "ios" && !hasText(config.bundleId)) {
        errors.push(`步骤 ${order}「${step.step_name || step.step_type}」缺少 bundleId`);
      }
    }
    if (platform === "ios" && step.step_type === "app_press") {
      errors.push(`步骤 ${order}「按键码」仅适用于 Android`);
    }
  });
  return errors;
}

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------
export interface StepEditorProps {
  projectId?: number;
  /** Web / App / Mixed —— 决定下拉里列哪些 step_type */
  category: CaseType;
  value: TestStepDraft[];
  onChange: (next: TestStepDraft[]) => void;
  /** 从父组件传下来的错误提示（比如必填为空，父表单用 form.setError 填进来） */
  error?: string | null;
  databaseConnections?: Array<{ name: string; label: string }>;
}

export function StepEditor({ projectId, category, value, onChange, error, databaseConnections = [] }: StepEditorProps) {
  // android / ios 共用同一套 app_* StepRunner；平台差异由 environment.browser_config /
  // step config 里的 caps 决定。
  const isAppFamily = category === "android" || category === "ios";
  const platform = platformOf(category);

  // 新建 step 时，默认选的 step_type 依赖 category：
  const defaultNewType = React.useMemo(() => {
    if (category === "web") return "web_goto";
    if (isAppFamily) return "app_launch";
    if (category === "api") return "http_request";
    return "web_goto"; // mixed / functional 等先默认 web
  }, [category, isAppFamily]);

  const allowedGroups = React.useMemo(() => {
    if (category === "web") return new Set(["web", "generic"]);
    if (isAppFamily) return new Set(["app", "generic"]);
    if (category === "api") return new Set(["generic"]); // api 多步骤：http_request / assert / sleep
    return new Set(["web", "app", "generic"]); // mixed
  }, [category, isAppFamily]);

  const availableSpecs = STEP_TYPE_SPECS.filter(
    (s) => allowedGroups.has(s.group) && isAllowedForPlatform(s.platforms, platform),
  );

  const setStep = (i: number, next: TestStepDraft) => {
    lastPaste.current = null;
    const arr = value.slice();
    arr[i] = { ...next, step_order: i };
    onChange(arr);
  };
  const removeStep = (i: number) => {
    lastPaste.current = null;
    setCopiedStepIndex(null);
    const arr = value.slice();
    arr.splice(i, 1);
    onChange(arr.map((s, idx) => ({ ...s, step_order: idx })));
  };
  const moveStep = (i: number, dir: -1 | 1) => {
    lastPaste.current = null;
    setCopiedStepIndex(null);
    const j = i + dir;
    if (j < 0 || j >= value.length) return;
    const arr = value.slice();
    [arr[i], arr[j]] = [arr[j], arr[i]];
    onChange(arr.map((s, idx) => ({ ...s, step_order: idx })));
  };

  // 鼠标拖拽：把 from 插到 to 之前（to 是目标行的当前 index）。
  // 注意：当 from < to 时，把 from 取出后剩余数组里 to 的位置会左移 1，所以
  // 实际插入索引要 -1，避免拖到下方一格"看起来没动"。
  const reorderStep = (from: number, to: number) => {
    lastPaste.current = null;
    setCopiedStepIndex(null);
    if (from === to) return;
    if (from < 0 || to < 0 || from >= value.length || to > value.length) return;
    const arr = value.slice();
    const [moved] = arr.splice(from, 1);
    const insertAt = from < to ? to - 1 : to;
    arr.splice(insertAt, 0, moved);
    onChange(arr.map((s, idx) => ({ ...s, step_order: idx })));
  };

  // 当前正在拖动哪一行：父级管理，让所有 row 都能感知，便于做悬停高亮（可选）。
  const [dragIdx, setDragIdx] = React.useState<number | null>(null);

  // ---- 复制/粘贴/撤销 ----
  const [activeStepIndex, setActiveStepIndex] = React.useState<number | null>(null);
  const containerRef = React.useRef<HTMLDivElement>(null);
  // 记录最近一次粘贴（仅单级撤销）；任何其它改动都会清空它。
  const lastPaste = React.useRef<{ index: number } | null>(null);

  // 常驻复制高亮（参考 Excel 蚁行线，不定时消失）：
  // ownerToken 标识"这一步是本编辑器实例复制的"，避免不同用例里同序号误亮。
  const ownerTokenRef = React.useRef<string>(Math.random().toString(36).slice(2));
  const [copiedStepIndex, setCopiedStepIndex] = React.useState<number | null>(null);
  const clip = useCopyClipboard();
  // 剪贴板已不是本编辑器复制的这一步（复制了别的用例/步骤、或被清空）→ 撤掉高亮
  React.useEffect(() => {
    const mine = clip?.kind === "step" && clip.ownerToken === ownerTokenRef.current;
    if (!mine) setCopiedStepIndex(null);
  }, [clip]);

  const doCopyStep = () => {
    if (activeStepIndex == null) return;
    const step = value[activeStepIndex];
    if (!step) return;
    const group = resolveCopyStepGroup(step.step_type, category);
    copyClipboard.set({
      kind: "step",
      platformGroup: group,
      snapshot: structuredClone(step),
      ownerToken: ownerTokenRef.current,
    });
    setCopiedStepIndex(activeStepIndex);
  };

  const doPasteStep = () => {
    const item = copyClipboard.get();
    if (!item || item.kind !== "step") return;
    if (!canPasteStep(item.platformGroup, category)) {
      toast.error("不能跨类型粘贴步骤");
      return;
    }
    const insertAt = activeStepIndex == null ? 0 : activeStepIndex + 1;
    const cloned: TestStepDraft = { ...structuredClone(item.snapshot), id: null };
    const arr = value.slice();
    arr.splice(insertAt, 0, cloned);
    onChange(arr.map((s, idx) => ({ ...s, step_order: idx })));
    lastPaste.current = { index: insertAt };
    setActiveStepIndex(insertAt);
  };

  const undoPasteStep = () => {
    const rec = lastPaste.current;
    if (!rec) return;
    if (rec.index < 0 || rec.index >= value.length) {
      lastPaste.current = null;
      return;
    }
    const arr = value.slice();
    arr.splice(rec.index, 1);
    onChange(arr.map((s, idx) => ({ ...s, step_order: idx })));
    lastPaste.current = null;
    setActiveStepIndex(null);
  };

  const onEditorKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!(e.metaKey || e.ctrlKey)) return;
    const key = e.key.toLowerCase();
    if (key !== "c" && key !== "v" && key !== "z") return;
    const el = document.activeElement as HTMLElement | null;
    const inEditable =
      !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
    if (key === "c") {
      // 输入框 / textarea / contenteditable 内一律让位原生复制
      // （window.getSelection() 看不到表单控件内部选区，不能靠它判断）
      if (inEditable) return;
      // 非编辑区若有选中文本，也让位原生复制
      if ((window.getSelection()?.toString() ?? "").length > 0) return;
      if (activeStepIndex == null) return;
      e.preventDefault();
      doCopyStep();
    } else if (key === "v") {
      if (inEditable) return;
      e.preventDefault();
      doPasteStep();
    } else if (key === "z") {
      if (inEditable) return;
      if (!lastPaste.current) return;
      e.preventDefault();
      undoPasteStep();
    }
  };

  const addStep = () => {
    lastPaste.current = null;
    setCopiedStepIndex(null);
    const spec = findSpec(defaultNewType) ?? availableSpecs[0];
    if (!spec) return;
    const cfg = { ...defaultConfigForPlatform(spec, platform) };
    if (spec.value === "http_request" && databaseConnections[0]) {
      cfg.target_db_group = databaseConnections[0].name;
    }
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
    <div
      ref={containerRef}
      className="space-y-3 outline-none"
      tabIndex={-1}
      onKeyDown={onEditorKeyDown}
      onClick={(event) => {
        // 输入框、下拉和按钮必须保留自己的焦点；点击步骤空白处才激活复制快捷键。
        if (isInteractiveClickTarget(event.target)) return;
        containerRef.current?.focus({ preventScroll: true });
      }}
    >
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
              platform={platform}
              databaseConnections={databaseConnections}
              projectId={projectId}
              onChange={(next) => setStep(i, next)}
              onRemove={() => removeStep(i)}
              onMove={(dir) => moveStep(i, dir)}
              dragIdx={dragIdx}
              onDragStart={() => setDragIdx(i)}
              onDragEnd={() => setDragIdx(null)}
              onDropBefore={(from) => {
                reorderStep(from, i);
                setDragIdx(null);
              }}
              onDropAtEnd={(from) => {
                // 仅最后一行响应 onDropAtEnd（StepRow 内部判断）
                reorderStep(from, value.length);
                setDragIdx(null);
              }}
              active={activeStepIndex === i}
              flash={copiedStepIndex === i}
              onActivate={() => setActiveStepIndex(i)}
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
  platform,
  databaseConnections,
  projectId,
  onChange,
  onRemove,
  onMove,
  dragIdx,
  onDragStart,
  onDragEnd,
  onDropBefore,
  onDropAtEnd,
  active,
  flash,
  onActivate,
}: {
  index: number;
  total: number;
  step: TestStepDraft;
  specs: StepTypeSpec[];
  platform: "android" | "ios" | null;
  databaseConnections: Array<{ name: string; label: string }>;
  projectId?: number;
  onChange: (next: TestStepDraft) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
  dragIdx: number | null;
  onDragStart: () => void;
  onDragEnd: () => void;
  /** 用户把别的行拖到本行"之前"，from 是被拖行的当前 index。 */
  onDropBefore: (from: number) => void;
  /** 用户拖到本行"之后"（仅最后一行有意义，用来排到末尾）。 */
  onDropAtEnd: (from: number) => void;
  active: boolean;
  flash: boolean;
  onActivate: () => void;
}) {
  const [expanded, setExpanded] = React.useState(index === 0 || !step.step_type);
  const [elementPickerOpen, setElementPickerOpen] = React.useState(false);
  const spec = specs.find((item) => item.value === step.step_type) ?? findSpec(step.step_type);
  const visibleFields = spec ? fieldsForPlatform(spec, platform).map((field) => (
    field.key === "target_db_group"
      ? {
          ...field,
          options: databaseConnections.map((connection) => ({
            value: connection.name,
            label: connection.label,
          })),
        }
      : field
  )) : [];

  const handleTypeChange = (newType: string) => {
    const newSpec = findSpec(newType);
    if (!newSpec) return;
    // 保留已有 config 里与新 spec 共有字段的值；不共有的丢掉
    const newFields = fieldsForPlatform(newSpec, platform);
    const keepKeys = new Set(newFields.map((f) => f.key));
    const nextConfig: Record<string, unknown> = { ...defaultConfigForPlatform(newSpec, platform) };
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

  // HTML5 DnD：grip 是真正的拖动手柄；行容器接收 drop 事件 + 上下半区判断
  // 决定是"放到本行之前"还是"放到本行之后"。dataTransfer 用一个 dummy 字符串
  // 让 Firefox 也开始拖动；真正的 from index 走父组件的 dragIdx state。
  const isDragging = dragIdx === index;
  const isDropTarget = dragIdx !== null && dragIdx !== index;
  const locatorCapable = visibleFields.some((field) => field.key === "locator");
  const elementPlatform = step.step_type.startsWith("web_")
    ? "web"
    : platform;

  const handleRowDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    if (dragIdx === null || dragIdx === index) return;
    e.preventDefault();  // 必须 preventDefault 才会触发 onDrop
    e.dataTransfer.dropEffect = "move";
  };
  const handleRowDrop = (e: React.DragEvent<HTMLDivElement>) => {
    if (dragIdx === null || dragIdx === index) return;
    e.preventDefault();
    e.stopPropagation();
    // 落点在本行下半区 + 又是最后一行 → 排到末尾，否则统一"插到本行之前"
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const isBottomHalf = e.clientY - rect.top > rect.height / 2;
    if (isBottomHalf && index === total - 1) {
      onDropAtEnd(dragIdx);
    } else {
      onDropBefore(dragIdx);
    }
  };

  return (
    <div
      className={cn(
        "rounded-md border bg-card p-2 transition-colors",
        step.skip && "opacity-60",
        isDragging && "opacity-50 ring-2 ring-primary/30",
        isDropTarget && "border-primary/40 bg-primary/5",
        active && "ring-2 ring-primary/60",
        flash && "copy-flash",
      )}
      onClick={onActivate}
      onDragOver={handleRowDragOver}
      onDrop={handleRowDrop}
    >
      <div className="flex items-center gap-2">
        {/* 拖动手柄：只有这块本身可拖（draggable=true），点行其它部位
            （Select / Input）不会误触发拖动 */}
        <span
          draggable
          onDragStart={(e) => {
            // 给 Firefox 一个非空 payload，否则不会触发 dragstart 后的 drag
            e.dataTransfer.setData("text/plain", String(index));
            e.dataTransfer.effectAllowed = "move";
            onDragStart();
          }}
          onDragEnd={onDragEnd}
          className="shrink-0 cursor-grab text-muted-foreground hover:text-foreground active:cursor-grabbing"
          title="拖动调整顺序"
        >
          <GripVertical className="h-4 w-4" />
        </span>
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
          {/* 展开/折叠用树状惯用 ChevronDown/ChevronRight：跟上面 上移/下移 的
              ChevronUp/ChevronDown 视觉区分明显，避免用户误点。 */}
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
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

          {locatorCapable && projectId && elementPlatform ? (
            <div className="flex items-center justify-between rounded-md border border-dashed bg-background px-3 py-2">
              <span className="text-xs text-muted-foreground">可直接复用项目元素库中已验证的定位器</span>
              <Button type="button" size="sm" variant="outline" onClick={() => setElementPickerOpen(true)}>
                从元素库选择
              </Button>
            </div>
          ) : null}

          {spec && visibleFields.length > 0 ? (
            <div className="grid grid-cols-2 gap-2">
              {visibleFields.map((field) => (
                <FieldRenderer
                  key={field.key}
                  field={field}
                  projectId={projectId}
                  platform={platform}
                  value={
                    field.key === "target_db_group"
                      ? (
                          databaseConnections.some(
                            (connection) => connection.name === step.config?.[field.key],
                          )
                            ? step.config?.[field.key]
                            : databaseConnections[0]?.name
                        )
                      : step.config?.[field.key]
                  }
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
          {projectId && elementPlatform ? (
            <UiElementPickerDialog
              open={elementPickerOpen}
              projectId={projectId}
              platform={elementPlatform}
              onOpenChange={setElementPickerOpen}
              onSelect={(element, by, locator) => {
                const verb = step.step_type.includes("input") ? "输入到" : step.step_type.includes("assert") ? "断言" : "点击";
                onChange({
                  ...step,
                  step_name: `${verb} ${element.semantic_name}`,
                  config: { ...(step.config || {}), by, locator, element_id: element.id },
                });
              }}
            />
          ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单个字段的渲染器：根据 FieldSpec.kind 分发
// ---------------------------------------------------------------------------
function FieldRenderer({
  field,
  platform,
  projectId,
  value,
  onChange,
}: {
  field: FieldSpec;
  platform: "android" | "ios" | null;
  projectId?: number;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const id = `step-field-${field.key}`;
  const options = React.useMemo(() => {
    if (!field.options) return field.options;
    if (field.options === APP_BY_OPTIONS && platform === "android") {
      return field.options.filter((opt) => ANDROID_APP_BY_VALUES.has(opt.value));
    }
    if (field.options === APP_BY_OPTIONS && platform === "ios") {
      return field.options.filter((opt) => IOS_APP_BY_VALUES.has(opt.value));
    }
    if (field.options === APP_AUTOMATION_OPTIONS && platform === "android") {
      return field.options.filter((opt) => opt.value === "UiAutomator2" || opt.value === "Espresso");
    }
    if (field.options === APP_AUTOMATION_OPTIONS && platform === "ios") {
      return field.options.filter((opt) => opt.value === "XCUITest");
    }
    return field.options;
  }, [field.options, platform]);
  // highlight 可能跨两列
  const wrapperClass = field.kind === "highlight" && (field.rows ?? 1) > 1
    ? "col-span-2 space-y-1"
    : "space-y-1";

  return (
    <div className={wrapperClass}>
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id} className="text-xs">
          {field.label}
          {field.required ? <span className="ml-0.5 text-destructive">*</span> : null}
        </Label>
        {field.kind === "highlight" && field.jsonCheck ? (
          <JsonValidateButton
            value={stringify(value)}
            onChange={onChange}
            check={field.jsonCheck}
          />
        ) : null}
      </div>
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
      {field.kind === "select" && options ? (
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
            {options.map((opt) => (
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
      {field.kind === "app_package" ? (
        <AppPackagePicker
          id={id}
          platform={platform}
          placeholder={field.placeholder}
          value={stringify(value)}
          onChange={onChange}
        />
      ) : null}
      {field.kind === "workflow_script" ? (
        <WorkflowScriptPicker
          id={id}
          projectId={projectId}
          value={stringify(value)}
          onChange={onChange}
        />
      ) : null}
      {field.hint ? (
        <p className="text-[11px] leading-tight text-muted-foreground">{field.hint}</p>
      ) : null}
    </div>
  );
}

function WorkflowScriptPicker({
  id,
  projectId,
  value,
  onChange,
}: {
  id: string;
  projectId?: number;
  value: string;
  onChange: (value: unknown) => void;
}) {
  const scriptsQuery = useQuery({
    queryKey: queryKeys.scripts({ project_id: projectId, scope: "available", kind: "workflow" }),
    queryFn: () => scriptsApi.list({ project_id: projectId, scope: "available", kind: "workflow" }),
    staleTime: 30 * 1000,
  });
  // 运行时同名脚本遵循“项目脚本覆盖全局脚本”，选择器也保持相同语义，
  // 避免下拉里出现两个 value 相同、但用户无法判断实际会执行哪一个的选项。
  const scripts = Array.from(
    (scriptsQuery.data ?? [])
      .filter((script) => script.enabled)
      .reduce((items, script) => {
        const current = items.get(script.name);
        if (!current || script.scope === "project") items.set(script.name, script);
        return items;
      }, new Map<string, ScriptItem>())
      .values(),
  ).sort((left, right) => {
    if (left.scope !== right.scope) return left.scope === "project" ? -1 : 1;
    return left.name.localeCompare(right.name, "zh-CN");
  });
  const selectedScript = scripts.find((script) => script.name === value);

  return (
    <div className="space-y-1.5">
      <Select value={value || undefined} onValueChange={onChange}>
        <SelectTrigger id={id} className="h-9 text-left text-xs">
          <SelectValue
            placeholder={scriptsQuery.isLoading ? "加载脚本中…" : "请选择脚本库中的项目逻辑"}
          >
            {selectedScript ? (
              <span className="flex min-w-0 items-center gap-2">
                <span
                  className={cn(
                    "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium",
                    selectedScript.scope === "project"
                      ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                      : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
                  )}
                >
                  {selectedScript.scope === "project" ? "本项目" : "全局"}
                </span>
                <span className="truncate font-mono font-medium">{selectedScript.name}</span>
              </span>
            ) : value ? (
              <span className="font-mono text-amber-700">{value}（当前不可用）</span>
            ) : undefined}
          </SelectValue>
        </SelectTrigger>
        <SelectContent className="min-w-[28rem]">
          {scriptsQuery.isError ? (
            <div className="px-2 py-3 text-xs text-destructive">脚本列表加载失败，请稍后重试</div>
          ) : scripts.length === 0 && !scriptsQuery.isLoading ? (
            <div className="px-2 py-3 text-xs text-muted-foreground">
              暂无已启用的项目逻辑脚本，请先到项目脚本库创建并保存
            </div>
          ) : (
            scripts.map((script) => (
              <SelectItem
                key={script.id}
                value={script.name}
                className="items-start py-2.5"
              >
                <span className="block min-w-0 space-y-1 pr-2 text-left">
                  <span className="flex items-center gap-2">
                    <span
                      className={cn(
                        "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium",
                        script.scope === "project"
                          ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                          : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
                      )}
                    >
                      {script.scope === "project" ? "本项目" : "全局共享"}
                    </span>
                    <span className="font-mono text-xs font-semibold">{script.name}</span>
                  </span>
                  <span className="block max-w-[34rem] truncate text-[11px] text-muted-foreground">
                    {stripHtml(script.description) || "暂无脚本说明"}
                  </span>
                  <span className="block text-[10px] text-muted-foreground/80">
                    {script.requirements.length > 0
                      ? `依赖 ${script.requirements.join("、")}`
                      : "无额外依赖"}
                    {" · "}
                    {formatScriptUpdatedAt(script.updated_at)}
                  </span>
                </span>
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>

      {selectedScript ? (
        <div className="rounded border bg-background/70 px-2.5 py-2 text-[11px] leading-relaxed">
          <div className="text-foreground">
            {stripHtml(selectedScript.description) || "该脚本暂未填写说明。"}
          </div>
          <div className="mt-1 text-muted-foreground">
            来源：{selectedScript.scope === "project" ? "当前项目脚本库" : "全局共享脚本库"}
            {" · "}
            {selectedScript.requirements.length > 0
              ? `依赖：${selectedScript.requirements.join("、")}`
              : "无额外依赖"}
            {" · "}
            {formatScriptUpdatedAt(selectedScript.updated_at)}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function formatScriptUpdatedAt(value: string | null): string {
  if (!value) return "未记录更新时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return `更新于 ${value}`;
  return `更新于 ${new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date)}`;
}

function stringify(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  // 对象 / 数组（如 AI 生成的 config.headers / config.params 是 dict）转成
  // 格式化 JSON 显示，否则会被 String() 渲染成 "[object Object]"。
  // 编辑后存的是字符串，http_request runner 同时兼容 dict 和 JSON 字符串。
  if (typeof v === "object") {
    try {
      return JSON.stringify(v, null, 2);
    } catch {
      return String(v);
    }
  }
  return String(v);
}

// ---------------------------------------------------------------------------
// App 包选择器：把已上传的 .apk / .ipa 列在下拉里，选中后把 file_path 填进去；
// 同时保留一个文本输入，让用户继续可以手填本地路径或 URL（兼容老配置）。
// ---------------------------------------------------------------------------
function AppPackagePicker({
  id,
  platform,
  placeholder,
  value,
  onChange,
}: {
  id: string;
  platform: "android" | "ios" | null;
  placeholder?: string;
  value: string;
  onChange: (v: unknown) => void;
}) {
  const pkgQuery = useQuery({
    queryKey: queryKeys.appPackages(platform ? { platform } : undefined),
    queryFn: () => appPackagesApi.list(platform ? { platform } : undefined),
    staleTime: 30 * 1000,
  });

  const packages = pkgQuery.data ?? [];
  // 当前 value 如果正好对得上某个包的 file_path，下拉显示那个包；否则显示"自定义"
  const matched = packages.find((p) => p.file_path === value);

  return (
    <div className="space-y-1">
      <Select
        value={matched ? String(matched.id) : "__custom__"}
        onValueChange={(v) => {
          if (v === "__custom__") return; // 不动 value，让用户继续在 input 里填
          const pkg = packages.find((p) => String(p.id) === v);
          if (pkg) onChange(pkg.file_path);
        }}
      >
        <SelectTrigger id={`${id}-picker`} className="h-8 text-xs">
          <SelectValue placeholder={pkgQuery.isLoading ? "加载中…" : "选择已上传的包（可选）"} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__custom__">自定义路径 / URL</SelectItem>
          {packages.map((pkg) => (
            <SelectItem key={pkg.id} value={String(pkg.id)}>
              [{pkg.platform}] {pkg.name}
              {pkg.version ? ` v${pkg.version}` : ""}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Input
        id={id}
        className="h-8 text-xs"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
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
