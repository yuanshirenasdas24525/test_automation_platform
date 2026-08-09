import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  CircleDot,
  Crosshair,
  Database,
  ExternalLink,
  GripVertical,
  Layers3,
  Loader2,
  Monitor,
  MousePointer2,
  Network,
  Pause,
  Pencil,
  Play,
  Plus,
  Search,
  Send,
  Smartphone,
  Square,
  Star,
  Terminal,
  Trash2,
  Undo2,
  RefreshCw,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ApiError,
  appPackagesApi,
  casesApi,
  devicesApi,
  modulesApi,
  uiRecordingsApi,
  type AppPackage,
  type Device,
  type ModulePickerNode,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  UiElement,
  UiOfflineReplay,
  UiPageSnapshot,
  UiPlatform,
  UiReplayActiveElement,
  UiRecordingEvent,
  UiRecordingSession,
  UiRecordingStepDraft,
  UiRecordingStatus,
} from "@/types/domain";
import { UiRecordingResultDialog } from "./UiRecordingResultDialog";

const PLATFORM_META: Record<UiPlatform, { label: string; runtime: string }> = {
  web: { label: "Web", runtime: "离线业务包 · XHR/Fetch Mock" },
  android: { label: "Android", runtime: "Android Emulator · Appium" },
  ios: { label: "iOS", runtime: "iOS Simulator · XCUITest" },
};

const STATUS_META: Record<UiRecordingStatus, { label: string; className: string }> = {
  draft: { label: "待启动", className: "bg-slate-100 text-slate-600" },
  starting: { label: "启动中", className: "bg-blue-100 text-blue-700" },
  recording: { label: "录制中", className: "bg-red-100 text-red-700" },
  paused: { label: "已暂停", className: "bg-amber-100 text-amber-700" },
  stopping: { label: "停止中", className: "bg-slate-100 text-slate-600" },
  processing: { label: "处理中", className: "bg-violet-100 text-violet-700" },
  completed: { label: "已完成", className: "bg-emerald-100 text-emerald-700" },
  failed: { label: "失败", className: "bg-red-100 text-red-700" },
  cancelled: { label: "已取消", className: "bg-slate-100 text-slate-500" },
};

const ACTIVE_STATUSES: UiRecordingStatus[] = [
  "starting",
  "recording",
  "paused",
  "stopping",
  "processing",
];

function messageOf(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return "操作失败";
}

function randomId(prefix: string): string {
  const value = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${value}`;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function isSimulatorDevice(device: Device): boolean {
  const capabilities = device.capabilities ?? {};
  const deviceType = String(capabilities.device_type ?? "").toLocaleLowerCase();
  const udid = device.udid.toLocaleLowerCase();
  return capabilities.is_simulator === true
    || deviceType === "emulator"
    || deviceType === "simulator"
    || udid.startsWith("emulator-")
    || udid.startsWith("simulator-");
}

type UiPageGroup = {
  pageKey: string;
  pageName: string;
  displayName: string;
  route: string;
  elements: UiElement[];
  snapshots: UiPageSnapshot[];
};

const ROUTE_LABELS: Record<string, string> = {
  "/login": "登录页",
  "/workspace": "工作台入口",
  "/workspace/admin": "管理工作台",
  "/workspace/projects": "项目工作台",
  "/projects": "项目列表",
  "/runs": "执行记录",
  "/devices": "设备池",
  "/app-packages": "App 包管理",
  "/scripts": "脚本库",
};

function pageRoute(url: string | null | undefined, pageKey: string): string {
  if (url) {
    try {
      const parsed = new URL(url);
      return `${parsed.pathname || "/"}${parsed.search}`;
    } catch {
      // 兼容历史快照中的非标准 URL，继续从 pageKey 推断。
    }
  }
  const slash = pageKey.indexOf("/");
  return slash >= 0 ? pageKey.slice(slash) : pageKey;
}

function routeDisplayName(route: string, fallback: string): string {
  const [pathname, search = ""] = route.split("?", 2);
  const params = new URLSearchParams(search);
  const identity = [...params.entries()]
    .slice(0, 3)
    .map(([key, value]) => `${key}=${value}`)
    .join(" · ");
  const exact = ROUTE_LABELS[pathname];
  if (exact) return identity ? `${exact} · ${identity}` : exact;
  const projectMatch = /^\/projects\/(\d+)$/.exec(pathname);
  const versionMatch = /^\/projects\/(\d+)\/versions\/(\d+)$/.exec(pathname);
  const boardMatch = /^\/projects\/(\d+)\/versions\/(\d+)\/board$/.exec(pathname);
  const requirementsMatch = /^\/projects\/(\d+)\/requirements$/.exec(pathname);
  const semantic = projectMatch
    ? `项目 #${projectMatch[1]}`
    : versionMatch
      ? `项目 #${versionMatch[1]} · 版本 #${versionMatch[2]}`
      : boardMatch
        ? `项目 #${boardMatch[1]} · 版本 #${boardMatch[2]} 看板`
        : requirementsMatch
          ? `项目 #${requirementsMatch[1]} · 需求`
          : null;
  if (semantic) return identity ? `${semantic} · ${identity}` : semantic;
  const segment = decodeURIComponent(pathname.split("/").filter(Boolean).at(-1) ?? "");
  if (!segment) return fallback;
  const base = segment
    .split(/[-_]/)
    .filter(Boolean)
    .map((item) => item.charAt(0).toLocaleUpperCase() + item.slice(1))
    .join(" ");
  if (!search) return base;
  return identity ? `${base} · ${identity}` : base;
}

function groupPages(elements: UiElement[], snapshots: UiPageSnapshot[]) {
  const pages = new Map<string, UiPageGroup>();
  for (const snapshot of snapshots) {
    const page = pages.get(snapshot.page_key) ?? {
      pageKey: snapshot.page_key,
      pageName: snapshot.page_name,
      displayName: snapshot.page_name,
      route: pageRoute(snapshot.url, snapshot.page_key),
      elements: [],
      snapshots: [],
    };
    page.snapshots.push(snapshot);
    pages.set(snapshot.page_key, page);
  }
  for (const element of elements) {
    const page = pages.get(element.page_key) ?? {
      pageKey: element.page_key,
      pageName: element.page_name,
      displayName: element.page_name,
      route: pageRoute(null, element.page_key),
      elements: [],
      snapshots: [],
    };
    page.elements.push(element);
    pages.set(element.page_key, page);
  }
  return [...pages.values()]
    .map((page) => {
      const orderedSnapshots = page.snapshots.sort(
        (a, b) => b.snapshot_version - a.snapshot_version,
      );
      const latest = orderedSnapshots[0];
      const route = pageRoute(latest?.url, page.pageKey);
      const capturedName = latest?.page_name || page.pageName;
      const displayName = capturedName === "自动化测试平台"
        ? routeDisplayName(route, capturedName)
        : capturedName;
      return {
        ...page,
        pageName: capturedName,
        displayName,
        route,
        snapshots: orderedSnapshots,
      };
    })
    .sort((a, b) => {
      const latestA = a.snapshots[0]?.created_at ?? "";
      const latestB = b.snapshots[0]?.created_at ?? "";
      return latestB.localeCompare(latestA)
        || a.pageName.localeCompare(b.pageName, "zh-CN");
    });
}

function eventLabel(event: UiRecordingEvent): string {
  const labels: Record<string, string> = {
    "agent.connected": "Recorder Agent 已连接",
    "agent.paused": "录制已暂停",
    "agent.resumed": "录制已继续",
    "agent.disconnected": "Recorder Agent 已停止",
    "agent.pick_mode": "拾取模式已切换",
    "page.navigation": "页面跳转",
    "page.ready": "页面加载完成",
    "page.snapshot": "页面快照已归档",
    "offline.package": "离线业务包已生成",
    "environment.snapshot": "运行环境已采集",
    "user.click": "点击元素",
    "user.pick": "拾取元素",
    "user.input": "输入内容",
    "user.change": "修改选项",
    "user.submit": "提交表单",
    "user.scroll": "滚动页面",
    "console.message": "Console",
    "console.pageerror": "页面异常",
    "network.request": "发送请求",
    "network.response": "收到响应",
    "network.failed": "请求失败",
    "screen.capture": "采集画面",
  };
  return labels[event.event_type] ?? event.event_type;
}

type DeleteTarget =
  | { kind: "element"; id: number; name: string; detail: string }
  | { kind: "page"; pageKey: string; name: string; detail: string }
  | { kind: "recording"; id: number; name: string; detail: string };

export function UiElementLibraryWorkspace({
  open,
  projectId,
  initialPlatform,
  onClose,
}: {
  open: boolean;
  projectId: number;
  initialPlatform: UiPlatform;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [clientInstanceId] = useState(() => randomId("ui-recorder"));
  const isPopout = new URLSearchParams(window.location.search).get("presentation") === "popout";
  const [platform, setPlatform] = useState<UiPlatform>(initialPlatform);
  const [keyword, setKeyword] = useState("");
  const [pageKey, setPageKey] = useState<string | null>(null);
  const [snapshotId, setSnapshotId] = useState<number | null>(null);
  const [selectedElementId, setSelectedElementId] = useState<number | null>(null);
  const [sessionOverride, setSessionOverride] = useState<UiRecordingSession | null>(null);
  const [startOpen, setStartOpen] = useState(false);
  const [targetUrl, setTargetUrl] = useState(() => window.location.origin);
  const [browser, setBrowser] = useState("chromium");
  const [selectedDeviceId, setSelectedDeviceId] = useState("");
  const [selectedPackageId, setSelectedPackageId] = useState("none");
  const [mobileInput, setMobileInput] = useState("");
  const [webInput, setWebInput] = useState("");
  const [replayInputDirty, setReplayInputDirty] = useState(false);
  const [embeddedReplay, setEmbeddedReplay] = useState<UiOfflineReplay | null>(null);
  const [replayRevision, setReplayRevision] = useState(0);
  const [offlinePickMode, setOfflinePickMode] = useState(false);
  const replayRef = useRef<UiOfflineReplay | null>(null);
  const [stepDraft, setStepDraft] = useState<UiRecordingStepDraft | null>(null);
  const [resultOpen, setResultOpen] = useState(false);
  const [draftModuleId, setDraftModuleId] = useState("");
  const [draftCaseName, setDraftCaseName] = useState("");
  const [elementNameDraft, setElementNameDraft] = useState("");
  const [elementAliasesDraft, setElementAliasesDraft] = useState("");
  const [newLocatorStrategy, setNewLocatorStrategy] = useState("css");
  const [newLocatorValue, setNewLocatorValue] = useState("");
  const [editingLocatorId, setEditingLocatorId] = useState<number | null>(null);
  const [editingLocatorValue, setEditingLocatorValue] = useState("");
  const [pageNameEditing, setPageNameEditing] = useState(false);
  const [pageNameDraft, setPageNameDraft] = useState("");
  const [floatingVisible, setFloatingVisible] = useState(true);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const lastMaterializedEventRef = useRef(0);
  const eventCursorRef = useRef(0);
  const [events, setEvents] = useState<UiRecordingEvent[]>([]);
  useEffect(() => {
    if (!open) return;
    setPlatform(initialPlatform);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [initialPlatform, open]);

  useEffect(() => {
    setPageKey(null);
    setSnapshotId(null);
    setSelectedElementId(null);
    setSessionOverride(null);
    setSelectedDeviceId("");
    setSelectedPackageId("none");
    setMobileInput("");
    setWebInput("");
    setReplayInputDirty(false);
    setOfflinePickMode(false);
    setStepDraft(null);
    setDraftModuleId("");
    setDraftCaseName("");
  }, [platform]);

  const elementsQuery = useQuery({
    queryKey: ["ui-elements", projectId, platform],
    queryFn: () => uiRecordingsApi.listElements({ projectId, platform }),
    enabled: open && Number.isFinite(projectId),
    staleTime: 30_000,
  });
  const snapshotsQuery = useQuery({
    queryKey: ["ui-page-snapshots", projectId, platform],
    queryFn: () => uiRecordingsApi.listSnapshots({ projectId, platform }),
    enabled: open && Number.isFinite(projectId),
    staleTime: 30_000,
  });
  const recordingsQuery = useQuery({
    queryKey: ["ui-recordings", projectId, platform],
    queryFn: () => uiRecordingsApi.list(projectId, platform),
    enabled: open && Number.isFinite(projectId),
    refetchInterval: (query) => {
      const sessions = query.state.data ?? [];
      return sessions.some((session) => ACTIVE_STATUSES.includes(session.status)) ? 1500 : false;
    },
  });
  const devicesQuery = useQuery({
    queryKey: ["ui-recording-devices", platform],
    queryFn: () => devicesApi.list({ platform: platform === "android" ? "Android" : "iOS" }),
    enabled: open && platform !== "web",
  });
  const appPackagesQuery = useQuery({
    queryKey: ["ui-recording-app-packages", projectId, platform],
    queryFn: () => appPackagesApi.list({
      platform: platform as "android" | "ios",
      project_id: projectId,
    }),
    enabled: open && platform !== "web",
  });
  const mobilePreflightQuery = useQuery({
    queryKey: ["ui-recording-mobile-preflight"],
    queryFn: () => uiRecordingsApi.mobilePreflight(),
    enabled: open && platform !== "web",
    refetchInterval: startOpen && platform !== "web" ? 3000 : false,
    retry: false,
  });
  const draftModulesQuery = useQuery({
    queryKey: ["ui-recording-draft-modules", projectId],
    queryFn: () => modulesApi.listForPicker(projectId),
    enabled: open && stepDraft != null,
  });
  const draftModules = useMemo<ModulePickerNode[]>(
    () => draftModulesQuery.data ?? [],
    [draftModulesQuery.data],
  );

  useEffect(() => {
    if (stepDraft == null || draftModuleId || draftModules.length === 0) return;
    setDraftModuleId(String(draftModules[0].id));
  }, [draftModuleId, draftModules, stepDraft]);
  const simulatorDevices = useMemo(
    () => (devicesQuery.data ?? []).filter(isSimulatorDevice),
    [devicesQuery.data],
  );
  const mobilePackages = useMemo<AppPackage[]>(
    () => appPackagesQuery.data ?? [],
    [appPackagesQuery.data],
  );

  useEffect(() => {
    if (platform === "web" || selectedDeviceId || simulatorDevices.length === 0) return;
    setSelectedDeviceId(String(simulatorDevices[0].id));
  }, [platform, selectedDeviceId, simulatorDevices]);

  const elements = useMemo(() => elementsQuery.data ?? [], [elementsQuery.data]);
  const normalizedKeyword = keyword.trim().toLocaleLowerCase();
  const filteredElements = useMemo(
    () => elements.filter((element) => {
      if (!normalizedKeyword) return true;
      return [element.semantic_name, element.page_name, ...element.locators.map((item) => item.locator)]
        .some((value) => value.toLocaleLowerCase().includes(normalizedKeyword));
    }),
    [elements, normalizedKeyword],
  );
  const filteredSnapshots = useMemo(
    () => (snapshotsQuery.data ?? []).filter((snapshot) => {
      if (!normalizedKeyword) return true;
      return [snapshot.page_name, snapshot.page_key, snapshot.url ?? ""]
        .some((value) => value.toLocaleLowerCase().includes(normalizedKeyword));
    }),
    [normalizedKeyword, snapshotsQuery.data],
  );
  const pages = useMemo(
    () => groupPages(filteredElements, filteredSnapshots),
    [filteredElements, filteredSnapshots],
  );
  const activePageKey = pageKey ?? pages[0]?.pageKey ?? null;
  const activePage = pages.find((page) => page.pageKey === activePageKey) ?? null;
  const activeSnapshot = activePage?.snapshots.find((item) => item.id === snapshotId)
    ?? activePage?.snapshots[0]
    ?? null;
  const visibleFingerprints = activeSnapshot?.resource_manifest.visible_element_fingerprints;
  const visibleElements = Array.isArray(visibleFingerprints)
    ? (activePage?.elements ?? []).filter((element) => visibleFingerprints.includes(element.fingerprint))
    : activePage?.elements ?? [];
  const selectedElement =
    visibleElements.find((element) => element.id === selectedElementId)
    ?? visibleElements[0]
    ?? null;

  useEffect(() => {
    setElementNameDraft(selectedElement?.semantic_name ?? "");
    const aliases = selectedElement?.attributes.aliases;
    setElementAliasesDraft(Array.isArray(aliases) ? aliases.join("，") : "");
  }, [selectedElement?.id, selectedElement?.semantic_name, selectedElement?.attributes.aliases]);

  useEffect(() => {
    setPageNameDraft(activePage?.displayName ?? "");
    setPageNameEditing(false);
  }, [activePage?.pageKey, activePage?.displayName]);

  const latestServerSession = recordingsQuery.data?.[0] ?? null;
  const activeServerSession = recordingsQuery.data?.find((session) =>
    ACTIVE_STATUSES.includes(session.status),
  ) ?? null;
  const serverSession = activeServerSession ?? latestServerSession;
  const session = sessionOverride && serverSession?.id === sessionOverride.id
    ? { ...sessionOverride, ...serverSession }
    : sessionOverride ?? serverSession;
  useEffect(() => {
    lastMaterializedEventRef.current = 0;
    eventCursorRef.current = 0;
    setEvents([]);
  }, [session?.id]);
  const eventsQuery = useQuery({
    queryKey: ["ui-recording-events", session?.id],
    queryFn: () => uiRecordingsApi.listEvents(session!.id, eventCursorRef.current),
    enabled: open && session != null,
    refetchInterval: session && ACTIVE_STATUSES.includes(session.status) ? 600 : false,
  });
  const eventBatch = eventsQuery.data;
  const refetchEvents = eventsQuery.refetch;

  useEffect(() => {
    const incoming = eventBatch ?? [];
    if (incoming.length === 0) return;
    eventCursorRef.current = Math.max(
      eventCursorRef.current,
      ...incoming.map((event) => event.sequence_no),
    );
    setEvents((current) => {
      const byId = new Map(current.map((event) => [event.id, event]));
      for (const event of incoming) byId.set(event.id, event);
      return [...byId.values()]
        .sort((a, b) => a.sequence_no - b.sequence_no)
        .slice(-2_000);
    });
    if (incoming.length >= 500) {
      window.setTimeout(() => void refetchEvents(), 0);
    }
  }, [eventBatch, refetchEvents]);

  useEffect(() => {
    const freshEvents = events.filter(
      (event) => event.sequence_no > lastMaterializedEventRef.current,
    );
    if (freshEvents.length === 0) return;
    lastMaterializedEventRef.current = Math.max(
      ...freshEvents.map((event) => event.sequence_no),
    );
    if (freshEvents.some((event) => event.event_type === "page.snapshot")) {
      void queryClient.invalidateQueries({
        queryKey: ["ui-page-snapshots", projectId, platform],
      });
      void queryClient.invalidateQueries({
        queryKey: ["ui-elements", projectId, platform],
      });
    } else if (freshEvents.some((event) => event.element_id != null)) {
      void queryClient.invalidateQueries({
        queryKey: ["ui-elements", projectId, platform],
      });
    }
  }, [events, platform, projectId, queryClient]);
  const controlLease = (session?.capabilities.control_lease ?? null) as {
    owner_id?: string;
    expires_at?: string;
  } | null;
  const hasControl = !session || !ACTIVE_STATUSES.includes(session.status)
    || controlLease?.owner_id === clientInstanceId;
  const pickMode = session?.capabilities.pick_mode === true;
  const offlineReplay = (session?.capabilities.offline_replay ?? null) as {
    ready?: boolean;
    page_count?: number;
    resource_count?: number;
    mock_count?: number;
    archive_bytes?: number;
    integrity_verified?: boolean;
    limitations?: string[];
  } | null;
  const mobileScenario = (session?.capabilities.mobile_scenario ?? null) as {
    ready?: boolean;
    snapshot_count?: number;
    limitations?: string[];
  } | null;
  const leaseSessionId = session?.id ?? null;
  const leaseSessionStatus = session?.status ?? null;
  const recorderActive = session != null && ACTIVE_STATUSES.includes(session.status);
  const staticInspectMode = platform === "web" && !recorderActive && embeddedReplay == null;
  const effectivePickMode = recorderActive
    ? pickMode
    : embeddedReplay
      ? offlinePickMode
      : staticInspectMode;

  useEffect(() => {
    replayRef.current = embeddedReplay;
  }, [embeddedReplay]);

  useEffect(() => {
    if (open && platform === "web") return;
    const replay = replayRef.current;
    if (!replay) return;
    replayRef.current = null;
    setEmbeddedReplay(null);
    void uiRecordingsApi.stopReplay(replay.session_id, replay.replay_id).catch(() => undefined);
  }, [open, platform]);

  useEffect(() => () => {
    const replay = replayRef.current;
    if (replay) {
      void uiRecordingsApi.stopReplay(replay.session_id, replay.replay_id).catch(() => undefined);
    }
  }, []);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["ui-recordings", projectId, platform] });
  };

  const startMutation = useMutation({
    mutationFn: async (input: {
      targetUrl?: string;
      browser?: string;
      deviceId?: number;
      appPackageId?: number;
    }) => {
      const draft = await uiRecordingsApi.create({
        project_id: projectId,
        platform,
        name: `${PLATFORM_META[platform].label} 录制 ${new Date().toLocaleString("zh-CN")}`,
        source_url: platform === "web" ? input.targetUrl : undefined,
        device_id: input.deviceId,
        app_package_id: input.appPackageId,
        capture_config: {
          browser: input.browser ?? "chromium",
          headless: false,
          viewport: { width: 1440, height: 900 },
          offline_level: 3,
          reuse_existing_assertions: true,
        },
      });
      return uiRecordingsApi.control(draft.id, "start", {
        client_instance_id: clientInstanceId,
        command_id: randomId("start"),
        takeover: true,
      });
    },
    onSuccess: async (next) => {
      setSessionOverride(next);
      setStartOpen(false);
      await refresh();
      toast.success(platform === "web" ? "受控浏览器已打开，录制已开始" : "模拟器已连接，录制已开始");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const reopenMobileMutation = useMutation({
    mutationFn: async (previous: UiRecordingSession) => {
      if (!previous.device_id) throw new Error("原录制没有绑定模拟器，无法重开场景");
      const draft = await uiRecordingsApi.create({
        project_id: projectId,
        platform: previous.platform,
        name: `${previous.name} · 场景重开`,
        device_id: previous.device_id,
        app_package_id: previous.app_package_id ?? undefined,
        capture_config: {
          ...previous.capture_config,
          restore_scenario: mobileScenario,
          source_session_id: previous.id,
        },
      });
      return uiRecordingsApi.control(draft.id, "start", {
        client_instance_id: clientInstanceId,
        command_id: randomId("mobile-reopen"),
        takeover: true,
      });
    },
    onSuccess: async (next) => {
      setSessionOverride(next);
      await refresh();
      toast.success("已按原模拟器和应用版本重开场景");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const controlMutation = useMutation({
    mutationFn: ({ sessionId, action }: {
      sessionId: number;
      action: "pause" | "resume" | "stop";
    }) => uiRecordingsApi.control(sessionId, action, {
      client_instance_id: clientInstanceId,
      command_id: randomId(action),
    }),
    onSuccess: async (next) => {
      setSessionOverride(next);
      await refresh();
      toast.success(STATUS_META[next.status].label);
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const pickModeMutation = useMutation({
    mutationFn: ({ sessionId, enabled }: { sessionId: number; enabled: boolean }) =>
      uiRecordingsApi.setPickMode(sessionId, {
        client_instance_id: clientInstanceId,
        command_id: randomId("pick"),
        enabled,
      }),
    onSuccess: async (next) => {
      setSessionOverride(next);
      await refresh();
      toast.success(next.capabilities.pick_mode ? "已进入非破坏性拾取" : "已退出拾取模式");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const mobileActionMutation = useMutation({
    mutationFn: ({
      sessionId,
      action,
      ...payload
    }: {
      sessionId: number;
      action: "tap" | "input" | "swipe" | "back" | "refresh";
      x?: number;
      y?: number;
      end_x?: number;
      end_y?: number;
      duration_ms?: number;
      text?: string;
    }) => uiRecordingsApi.performMobileAction(sessionId, {
      client_instance_id: clientInstanceId,
      command_id: randomId(`mobile-${action}`),
      action,
      ...payload,
    }),
    onSuccess: async (next) => {
      setSessionOverride(next);
      await Promise.all([
        refresh(),
        queryClient.invalidateQueries({ queryKey: ["ui-recording-events", next.id] }),
        queryClient.invalidateQueries({ queryKey: ["ui-page-snapshots", projectId, platform] }),
        queryClient.invalidateQueries({ queryKey: ["ui-elements", projectId, platform] }),
      ]);
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const webActionMutation = useMutation({
    mutationFn: ({
      sessionId,
      action,
      ...payload
    }: {
      sessionId: number;
      action: "click" | "pick" | "input" | "scroll" | "back" | "refresh";
      x?: number;
      y?: number;
      text?: string;
      delta_x?: number;
      delta_y?: number;
    }) => uiRecordingsApi.performWebAction(sessionId, {
      client_instance_id: clientInstanceId,
      command_id: randomId(`web-${action}`),
      action,
      ...payload,
    }),
    onSuccess: async (next) => {
      setSessionOverride(next);
      await Promise.all([
        refresh(),
        queryClient.invalidateQueries({ queryKey: ["ui-recording-events", next.id] }),
        queryClient.invalidateQueries({ queryKey: ["ui-page-snapshots", projectId, platform] }),
        queryClient.invalidateQueries({ queryKey: ["ui-elements", projectId, platform] }),
      ]);
      setSnapshotId(null);
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const replayMutation = useMutation({
    mutationFn: async ({
      sessionId,
      snapshot,
      headless = true,
    }: {
      sessionId: number;
      snapshot: UiPageSnapshot | null;
      headless?: boolean;
    }) => {
      const previous = replayRef.current;
      if (previous) {
        await uiRecordingsApi.stopReplay(previous.session_id, previous.replay_id).catch(() => undefined);
      }
      const viewport = snapshot?.environment.viewport as { width?: number; height?: number } | undefined;
      return uiRecordingsApi.startReplay(sessionId, browser, {
        headless,
        entry_url: snapshot?.url ?? undefined,
        page_fingerprint: snapshot?.fingerprint,
        viewport: {
          width: Number(viewport?.width || 1440),
          height: Number(viewport?.height || 900),
        },
      });
    },
    onSuccess: (replay) => {
      const activeReplay = { ...replay, url: replay.url ?? replay.entry_url };
      replayRef.current = activeReplay;
      setEmbeddedReplay(activeReplay);
      setWebInput("");
      setReplayInputDirty(false);
      setReplayRevision((value) => value + 1);
      setOfflinePickMode(false);
      toast.success(
        `离线交互已启动：${replay.page_count} 个页面、${replay.mock_count} 组接口 Mock`,
      );
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const replayActionMutation = useMutation({
    mutationFn: ({
      action,
      ...payload
    }: {
      action: "click" | "pick" | "input" | "scroll" | "back" | "refresh";
      x?: number;
      y?: number;
      text?: string;
      delta_x?: number;
      delta_y?: number;
    }) => {
      if (!embeddedReplay) throw new Error("请先启动离线交互");
      return uiRecordingsApi.performReplayAction(
        embeddedReplay.session_id,
        embeddedReplay.replay_id,
        { action, ...payload },
      );
    },
    onSuccess: (next, variables) => {
      const merged = { ...embeddedReplay, ...next } as UiOfflineReplay;
      replayRef.current = merged;
      setEmbeddedReplay(merged);
      setReplayRevision((value) => value + 1);
      if (variables.action !== "input") {
        setWebInput("");
        setReplayInputDirty(false);
      }
      if (next.url) {
        const matchedPage = pages.find((page) =>
          page.snapshots.some((snapshot) => snapshot.url === next.url),
        );
        if (matchedPage) {
          setPageKey(matchedPage.pageKey);
          setSnapshotId(null);
        }
      }
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const stopReplayMutation = useMutation({
    mutationFn: async () => {
      const replay = replayRef.current;
      if (!replay) return;
      await uiRecordingsApi.stopReplay(replay.session_id, replay.replay_id);
    },
    onSettled: () => {
      replayRef.current = null;
      setEmbeddedReplay(null);
      setOfflinePickMode(false);
      setWebInput("");
      setReplayInputDirty(false);
      setReplayRevision((value) => value + 1);
    },
  });

  const refreshElementAssets = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["ui-elements", projectId, platform] }),
      queryClient.invalidateQueries({ queryKey: ["ui-page-snapshots", projectId, platform] }),
    ]);
  };

  const staticPickMutation = useMutation({
    mutationFn: ({ targetSnapshotId, x, y }: {
      targetSnapshotId: number;
      x: number;
      y: number;
    }) => uiRecordingsApi.pickSnapshot(targetSnapshotId, { x, y }),
    onSuccess: async (element) => {
      await refreshElementAssets();
      setSelectedElementId(element.id);
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const elementUpdateMutation = useMutation({
    mutationFn: ({
      elementId,
      semanticName,
      aliases,
      status,
    }: {
      elementId: number;
      semanticName?: string;
      aliases?: string[];
      status?: UiElement["status"];
    }) => uiRecordingsApi.updateElement(elementId, {
      semantic_name: semanticName,
      aliases,
      status,
    }),
    onSuccess: async () => {
      await refreshElementAssets();
      toast.success("元素信息已更新");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const locatorCreateMutation = useMutation({
    mutationFn: ({ elementId, strategy, locator }: { elementId: number; strategy: string; locator: string }) =>
      uiRecordingsApi.createLocator(elementId, { strategy, locator, score: 80 }),
    onSuccess: async () => {
      setNewLocatorValue("");
      await refreshElementAssets();
      toast.success("定位器已添加");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const locatorUpdateMutation = useMutation({
    mutationFn: ({
      elementId,
      locatorId,
      isPrimary,
      locator,
    }: {
      elementId: number;
      locatorId: number;
      isPrimary?: boolean;
      locator?: string;
    }) => uiRecordingsApi.updateLocator(elementId, locatorId, {
      is_primary: isPrimary,
      locator,
    }),
    onSuccess: async () => {
      setEditingLocatorId(null);
      await refreshElementAssets();
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const locatorDeleteMutation = useMutation({
    mutationFn: ({ elementId, locatorId }: { elementId: number; locatorId: number }) =>
      uiRecordingsApi.deleteLocator(elementId, locatorId),
    onSuccess: async () => {
      await refreshElementAssets();
      toast.success("定位器已删除");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const locatorValidateMutation = useMutation({
    mutationFn: ({ elementId, locatorId }: { elementId: number; locatorId: number }) =>
      uiRecordingsApi.validateLocator(elementId, locatorId, activeSnapshot?.id),
    onSuccess: async () => {
      await refreshElementAssets();
      toast.success("定位器验证完成");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const pageUpdateMutation = useMutation({
    mutationFn: ({ snapshotId: targetSnapshotId, pageName }: { snapshotId: number; pageName: string }) =>
      uiRecordingsApi.updateSnapshot(targetSnapshotId, {
        page_name: pageName,
        apply_page_name_to_group: true,
      }),
    onSuccess: async () => {
      setPageNameEditing(false);
      await refreshElementAssets();
      toast.success("页面名称已更新");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: async (target: DeleteTarget) => {
      if (target.kind === "element") return uiRecordingsApi.deleteElement(target.id);
      if (target.kind === "page") {
        return uiRecordingsApi.deletePageGroup({ projectId, platform, pageKey: target.pageKey });
      }
      const replay = replayRef.current;
      if (replay?.session_id === target.id) {
        await uiRecordingsApi.stopReplay(replay.session_id, replay.replay_id);
      }
      return uiRecordingsApi.deleteRecording(target.id);
    },
    onSuccess: async (_data, target) => {
      if (target.kind === "element") setSelectedElementId(null);
      if (target.kind === "page") {
        setPageKey(null);
        setSnapshotId(null);
        setSelectedElementId(null);
      }
      if (target.kind === "recording") {
        replayRef.current = null;
        setEmbeddedReplay(null);
        setSessionOverride(null);
        setEvents([]);
      }
      setDeleteTarget(null);
      await Promise.all([
        refreshElementAssets(),
        queryClient.invalidateQueries({ queryKey: ["ui-recordings", projectId, platform] }),
      ]);
      toast.success(target.kind === "element" ? "元素已删除" : target.kind === "page" ? "页面及其元素已删除" : "录制记录已删除");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const stepDraftMutation = useMutation({
    mutationFn: (sessionId: number) => uiRecordingsApi.stepDraft(sessionId),
    onSuccess: (draft) => {
      setStepDraft(draft);
      setDraftCaseName(draft.suggested_name);
      setDraftModuleId("");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const commitDraftMutation = useMutation({
    mutationFn: async () => {
      if (!stepDraft || !draftModuleId) throw new Error("请选择用例所属模块");
      const steps = stepDraft.steps.map(({ source_event_id: _sourceEventId, ...step }) => step);
      return casesApi.create({
        module_id: Number(draftModuleId),
        name: draftCaseName.trim() || stepDraft.suggested_name,
        description: `由 UI 录制会话 #${stepDraft.session_id} 生成，已保留原始技术上下文。`,
        case_type: stepDraft.case_type,
        priority: 2,
        steps,
        source: "manual",
        generation_metadata: {
          source: "ui_recording",
          ui_recording_session_id: stepDraft.session_id,
          source_event_count: stepDraft.source_event_count,
          warnings: stepDraft.warnings,
        },
      });
    },
    onSuccess: async ({ id }) => {
      setStepDraft(null);
      await queryClient.invalidateQueries({ queryKey: ["content", projectId] });
      toast.success(`用例草稿已保存（#${id}），可在用例编辑器中继续补充断言`);
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  useEffect(() => {
    if (!open || leaseSessionId == null || leaseSessionStatus == null
      || !ACTIVE_STATUSES.includes(leaseSessionStatus)) return;
    let disposed = false;
    const heartbeat = async (first: boolean) => {
      try {
        const next = await uiRecordingsApi.updateLease(leaseSessionId, {
          client_instance_id: clientInstanceId,
          action: first && isPopout ? "takeover" : first ? "claim" : "heartbeat",
        });
        if (!disposed) setSessionOverride(next);
      } catch {
        if (!disposed) {
          await queryClient.invalidateQueries({ queryKey: ["ui-recordings", projectId, platform] });
        }
      }
    };
    void heartbeat(true);
    const timer = window.setInterval(() => void heartbeat(false), 3000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [
    clientInstanceId,
    isPopout,
    leaseSessionId,
    leaseSessionStatus,
    open,
    platform,
    projectId,
    queryClient,
  ]);

  const openPopout = () => {
    const url = new URL(window.location.href);
    url.searchParams.set("uiElements", platform);
    url.searchParams.set("presentation", "popout");
    if (session) url.searchParams.set("uiSession", String(session.id));
    const popup = window.open(url.toString(), `ui-elements-${projectId}`, "popup,width=1320,height=820");
    if (!popup) toast.error("浏览器阻止了独立窗口，请允许本站打开弹窗");
  };

  const copyLocator = async (locator: string) => {
    await navigator.clipboard.writeText(locator);
    toast.success("定位器已复制");
  };

  const performWebToolbarAction = (
    action: "input" | "scroll" | "back" | "refresh",
    payload: { text?: string; delta_x?: number; delta_y?: number } = {},
  ) => {
    if (embeddedReplay) {
      const active = embeddedReplay.active_element;
      const inputCoordinates = action === "input" && active?.editable
        ? {
            x: Math.round(active.bounds.x + active.bounds.width / 2),
            y: Math.round(active.bounds.y + active.bounds.height / 2),
          }
        : {};
      replayActionMutation.mutate({ action, ...payload, ...inputCoordinates });
    } else if (session && recorderActive) {
      webActionMutation.mutate({ sessionId: session.id, action, ...payload });
    }
  };

  const performReplayCanvasAction = async (
    x: number,
    y: number,
    action: "click" | "pick",
  ) => {
    const replay = replayRef.current;
    const active = replay?.active_element;
    if (replay && replayInputDirty && active?.editable) {
      try {
        await replayActionMutation.mutateAsync({
          action: "input",
          text: webInput,
          x: Math.round(active.bounds.x + active.bounds.width / 2),
          y: Math.round(active.bounds.y + active.bounds.height / 2),
        });
        setWebInput("");
        setReplayInputDirty(false);
      } catch {
        return;
      }
    }
    replayActionMutation.mutate({ action, x, y });
  };

  const submitReplayInput = () => {
    const active = replayRef.current?.active_element;
    if (!active?.editable) return;
    replayActionMutation.mutate({
      action: "input",
      text: webInput,
      x: Math.round(active.bounds.x + active.bounds.width / 2),
      y: Math.round(active.bounds.y + active.bounds.height / 2),
    });
    setWebInput("");
    setReplayInputDirty(false);
  };

  if (!open) return null;

  const sessionBusy = startMutation.isPending
    || reopenMobileMutation.isPending
    || controlMutation.isPending
    || pickModeMutation.isPending
    || webActionMutation.isPending
    || mobileActionMutation.isPending;
  const platformRuntime = PLATFORM_META[platform];
  const statusMeta = session ? STATUS_META[session.status] : null;
  const mobileSessionActive = session != null && ACTIVE_STATUSES.includes(session.status);
  const mobilePreflight = mobilePreflightQuery.data;
  const mobilePreflightReady = platform === "android"
    ? mobilePreflight?.platform_ready.android === true
    : platform === "ios" && mobilePreflight?.platform_ready.ios === true;

  return (
    <div className="fixed inset-0 z-50 flex min-w-[980px] flex-col bg-background text-foreground">
      <header className="flex h-[72px] shrink-0 items-center justify-between border-b px-5">
        <div className="flex min-w-0 items-center gap-4">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-primary text-primary-foreground">
            <Layers3 className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold">{isPopout ? "录制独立控制窗口" : "可视化元素库"}</h1>
              <span className="rounded-full border bg-muted/60 px-2 py-0.5 text-[11px] text-muted-foreground">
                离线业务回放 Level 3
              </span>
            </div>
            <p className="text-xs text-muted-foreground">在页面或模拟器场景中查找、验证和维护元素</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="inline-flex rounded-lg border bg-muted/50 p-1" aria-label="选择录制平台">
            {(["web", "android", "ios"] as UiPlatform[]).map((item) => (
              <button
                key={item}
                type="button"
                aria-pressed={platform === item}
                onClick={() => setPlatform(item)}
                className={cn(
                  "rounded-md px-4 py-1.5 text-xs font-medium transition",
                  platform === item ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
                )}
              >
                {PLATFORM_META[item].label}
              </button>
            ))}
          </div>
          <div className="hidden items-center gap-2 rounded-lg border px-3 py-2 text-xs xl:flex">
            <span className={cn("h-2 w-2 rounded-full", session?.capabilities.recorder_agent_connected ? "bg-emerald-500" : "bg-amber-500")} />
            {session?.capabilities.recorder_agent_connected ? "Recorder Agent 已连接" : "Recorder Agent 待连接"}
          </div>
          {statusMeta ? (
            <span className={cn("rounded-full px-2.5 py-1 text-xs font-medium", statusMeta.className)}>
              {statusMeta.label}
            </span>
          ) : null}
          {!session || !ACTIVE_STATUSES.includes(session.status) ? (
            <Button
              size="sm"
              disabled={sessionBusy}
              onClick={() => setStartOpen(true)}
            >
              {startMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CircleDot className="h-4 w-4" />}
              开始录制
            </Button>
          ) : null}
          {session?.status === "completed" && offlineReplay?.ready ? (
            <Button
              size="sm"
              variant="outline"
              disabled={replayMutation.isPending || stopReplayMutation.isPending}
              onClick={() => {
                if (embeddedReplay) {
                  stopReplayMutation.mutate();
                } else {
                  replayMutation.mutate({ sessionId: session.id, snapshot: activeSnapshot });
                }
              }}
            >
              {replayMutation.isPending || stopReplayMutation.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : embeddedReplay ? <Square className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              {embeddedReplay ? "结束离线交互" : "离线交互"}
            </Button>
          ) : null}
          {session?.status === "completed" && platform !== "web" && mobileScenario?.ready ? (
            <Button
              size="sm"
              variant="outline"
              disabled={reopenMobileMutation.isPending}
              onClick={() => reopenMobileMutation.mutate(session)}
            >
              {reopenMobileMutation.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <RefreshCw className="h-4 w-4" />}
              重开场景
            </Button>
          ) : null}
          {session?.status === "completed" ? (
            <Button size="sm" variant="outline" onClick={() => setResultOpen(true)}>
              <Terminal className="h-4 w-4" />
              录制结果
            </Button>
          ) : null}
          {session?.status === "completed" ? (
            <Button
              size="sm"
              variant="outline"
              disabled={stepDraftMutation.isPending}
              onClick={() => stepDraftMutation.mutate(session.id)}
            >
              {stepDraftMutation.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <ChevronRight className="h-4 w-4" />}
              生成用例草稿
            </Button>
          ) : null}
          {session && ACTIVE_STATUSES.includes(session.status) && !floatingVisible ? (
            <Button size="sm" variant="outline" onClick={() => setFloatingVisible(true)}>
              显示录制条
            </Button>
          ) : null}
          <Button size="icon" variant="ghost" title="打开独立窗口" onClick={openPopout}>
            <ExternalLink className="h-4 w-4" />
          </Button>
          <Button size="icon" variant="ghost" title="关闭" onClick={isPopout ? () => window.close() : onClose}>
            <X className="h-5 w-5" />
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[248px_minmax(430px,1fr)_330px]">
        <aside className="flex min-h-0 flex-col border-r bg-muted/15">
          <div className="border-b p-3">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索页面、元素或定位器"
                className="h-9 pl-9 text-xs"
              />
            </div>
          </div>
          <div className="flex items-center justify-between px-4 pb-2 pt-4 text-xs text-muted-foreground">
            <span>页面导航</span>
            <span>{pages.length} 个页面</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
            {elementsQuery.isLoading ? (
              <div className="grid place-items-center py-12 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : pages.length === 0 ? (
              <div className="mx-2 mt-2 rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">
                暂无页面资产
                <div className="mt-1">完成首次录制后自动按页面归档</div>
              </div>
            ) : (
              pages.map((page) => (
                <button
                  type="button"
                  key={page.pageKey}
                  onClick={() => {
                    setPageKey(page.pageKey);
                    setSnapshotId(null);
                    setSelectedElementId(page.elements[0]?.id ?? null);
                    if (embeddedReplay && session) {
                      replayMutation.mutate({
                        sessionId: session.id,
                        snapshot: page.snapshots[0] ?? null,
                      });
                    }
                  }}
                  className={cn(
                    "mb-1 flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2.5 text-left text-sm",
                    activePageKey === page.pageKey ? "bg-primary/10 text-primary" : "hover:bg-muted",
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{page.displayName}</span>
                    <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">
                      {page.route}
                    </span>
                  </span>
                  <span className="shrink-0 rounded bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {page.elements.length} 元素 · {page.snapshots.length} 状态
                  </span>
                </button>
              ))
            )}
          </div>
          <div className="border-t p-3">
            <div className="mb-2 text-[11px] text-muted-foreground">最近录制</div>
            {recordingsQuery.isLoading ? (
              <div className="text-xs text-muted-foreground">加载中…</div>
            ) : session ? (
              <div className="rounded-lg border bg-background p-3">
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1 truncate text-xs font-medium">{session.name}</div>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-6 w-6 shrink-0 text-red-600"
                    title={ACTIVE_STATUSES.includes(session.status) ? "请先停止录制" : "删除录制记录"}
                    disabled={ACTIVE_STATUSES.includes(session.status)}
                    onClick={() => setDeleteTarget({
                      kind: "recording",
                      id: session.id,
                      name: session.name,
                      detail: `将删除 ${session.event_count} 个事件、${session.snapshot_count} 个页面状态、离线包和技术上下文；已沉淀到项目元素库的元素会保留。`,
                    })}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                <div className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground">
                  <span>{session.event_count} 个事件</span>
                  <span>{formatTime(session.updated_at)}</span>
                </div>
                {offlineReplay?.ready ? (
                  <>
                    <div className="mt-2 rounded-md bg-emerald-50 px-2 py-1.5 text-[10px] text-emerald-700">
                      离线包可用 · {offlineReplay.page_count ?? 0} 页 · {offlineReplay.resource_count ?? 0} 资源 · {offlineReplay.mock_count ?? 0} Mock
                      {offlineReplay.integrity_verified ? " · SHA-256 已校验" : ""}
                    </div>
                    {offlineReplay.limitations?.length ? (
                      <details className="mt-2 text-[10px] text-muted-foreground">
                        <summary className="cursor-pointer">查看能力限制（{offlineReplay.limitations.length}）</summary>
                        <ul className="mt-1 list-disc space-y-1 pl-4">
                          {offlineReplay.limitations.map((item) => <li key={item}>{item}</li>)}
                        </ul>
                      </details>
                    ) : null}
                  </>
                ) : mobileScenario?.ready ? (
                  <>
                    <div className="mt-2 rounded-md bg-emerald-50 px-2 py-1.5 text-[10px] text-emerald-700">
                      模拟器场景可重开 · {mobileScenario.snapshot_count ?? session.snapshot_count} 个画面版本
                    </div>
                    {mobileScenario.limitations?.length ? (
                      <details className="mt-2 text-[10px] text-muted-foreground">
                        <summary className="cursor-pointer">查看场景恢复限制</summary>
                        <ul className="mt-1 list-disc space-y-1 pl-4">
                          {mobileScenario.limitations.map((item) => <li key={item}>{item}</li>)}
                        </ul>
                      </details>
                    ) : null}
                  </>
                ) : session.status === "completed" ? (
                  <div className="mt-2 rounded-md bg-amber-50 px-2 py-1.5 text-[10px] text-amber-700">
                    {platform === "web" ? "本次录制未生成可交互离线包" : "本次录制未生成可重开场景"}
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">尚无录制会话</div>
            )}
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col bg-slate-50/70 dark:bg-slate-950/20">
          <div className="flex h-[58px] shrink-0 items-center justify-between border-b bg-background px-4">
            <div>
              <div className="flex items-center gap-1.5">
                {pageNameEditing ? (
                  <>
                    <Input
                      value={pageNameDraft}
                      onChange={(event) => setPageNameDraft(event.target.value)}
                      className="h-7 w-56 text-sm"
                      autoFocus
                    />
                    <Button
                      size="sm"
                      className="h-7 px-2 text-[10px]"
                      disabled={!activeSnapshot || !pageNameDraft.trim() || pageUpdateMutation.isPending}
                      onClick={() => activeSnapshot && pageUpdateMutation.mutate({
                        snapshotId: activeSnapshot.id,
                        pageName: pageNameDraft.trim(),
                      })}
                    >保存</Button>
                    <Button size="sm" variant="ghost" className="h-7 px-2 text-[10px]" onClick={() => setPageNameEditing(false)}>取消</Button>
                  </>
                ) : (
                  <>
                    <div className="text-sm font-semibold">{activePage?.displayName ?? "等待首次页面快照"}</div>
                    {activeSnapshot ? (
                      <Button size="icon" variant="ghost" className="h-6 w-6" title="修改页面名称" onClick={() => setPageNameEditing(true)}>
                        <Pencil className="h-3 w-3" />
                      </Button>
                    ) : null}
                  </>
                )}
              </div>
              <div className="text-[11px] text-muted-foreground">
                {activePage?.route ?? platformRuntime.runtime}
              </div>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              {activePage && activePage.snapshots.length > 1 ? (
                <Select
                  value={String(activeSnapshot?.id ?? "")}
                  onValueChange={(value) => {
                    const nextSnapshot = activePage.snapshots.find((item) => item.id === Number(value)) ?? null;
                    setSnapshotId(Number(value));
                    setSelectedElementId(null);
                    if (embeddedReplay && session) {
                      replayMutation.mutate({ sessionId: session.id, snapshot: nextSnapshot });
                    }
                  }}
                >
                  <SelectTrigger className="h-8 w-[170px] text-[11px]">
                    <SelectValue placeholder="选择页面状态" />
                  </SelectTrigger>
                  <SelectContent>
                    {activePage.snapshots.map((item) => (
                      <SelectItem key={item.id} value={String(item.id)}>
                        {item.state_name || `状态 #${item.snapshot_version}`}
                        {item.id === activePage.snapshots[0]?.id ? " · 最新" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : null}
              {platform === "web" ? (
                staticInspectMode ? (
                  <div className="inline-flex rounded-md border bg-muted/40 p-0.5" title="点击页面元素只读取定位信息，不会执行页面操作">
                    <span className="rounded bg-background px-2 py-1 font-medium text-primary shadow-sm">
                      只读拾取
                    </span>
                  </div>
                ) : (
                  <div className="inline-flex rounded-md border bg-muted/40 p-0.5">
                    <button
                      type="button"
                      disabled={pickModeMutation.isPending || (!recorderActive && !embeddedReplay)}
                      onClick={() => {
                        if (recorderActive && session && pickMode) {
                          pickModeMutation.mutate({ sessionId: session.id, enabled: false });
                        } else if (embeddedReplay) {
                          setOfflinePickMode(false);
                        }
                      }}
                      className={cn(
                        "rounded px-2 py-1 transition disabled:cursor-not-allowed disabled:opacity-50",
                        !effectivePickMode ? "bg-background text-primary shadow-sm" : "text-muted-foreground",
                      )}
                    >
                      浏览页面
                    </button>
                    <button
                      type="button"
                      disabled={pickModeMutation.isPending || (!recorderActive && !embeddedReplay)}
                      onClick={() => {
                        if (recorderActive && session && !pickMode) {
                          pickModeMutation.mutate({ sessionId: session.id, enabled: true });
                        } else if (embeddedReplay) {
                          setOfflinePickMode(true);
                        }
                      }}
                      className={cn(
                        "rounded px-2 py-1 transition disabled:cursor-not-allowed disabled:opacity-50",
                        effectivePickMode ? "bg-background text-primary shadow-sm" : "text-muted-foreground",
                      )}
                    >
                      拾取元素
                    </button>
                  </div>
                )
              ) : null}
              {activePage ? (
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-red-600"
                  title={recorderActive ? "请先停止录制" : "删除当前页面"}
                  disabled={recorderActive}
                  onClick={() => setDeleteTarget({
                    kind: "page",
                    pageKey: activePage.pageKey,
                    name: activePage.displayName,
                    detail: `将删除该页面的 ${activePage.snapshots.length} 个状态和 ${activePage.elements.length} 个元素，录制会话中的共享资源仍会保留。`,
                  })}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              ) : null}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-auto p-5">
            <div className="mx-auto flex h-full min-h-[460px] max-w-5xl items-center justify-center">
              {platform === "web" ? (
                <div className="flex h-[78%] min-h-[420px] w-[90%] flex-col overflow-hidden rounded-xl border bg-background shadow-sm">
                  <div className="flex h-10 shrink-0 items-center gap-2 border-b bg-muted/50 px-3">
                    <span className="h-2.5 w-2.5 rounded-full bg-red-400" />
                    <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                    <div className="ml-2 flex-1 rounded bg-background px-3 py-1 text-[11px] text-muted-foreground">
                      {embeddedReplay?.url
                        ?? (activePage ? `offline://project/${projectId}/${activePage.pageKey}` : "offline://等待页面快照")}
                    </div>
                  </div>
                  {(recorderActive || embeddedReplay) && !effectivePickMode ? (
                    <div className="flex h-11 shrink-0 items-center gap-2 border-b bg-background px-3">
                      <Input
                        type={embeddedReplay?.active_element?.input_type === "password" ? "password" : "text"}
                        autoComplete="off"
                        value={webInput}
                        onChange={(event) => {
                          setWebInput(event.target.value);
                          if (embeddedReplay?.active_element?.editable) setReplayInputDirty(true);
                        }}
                        onKeyDown={(event) => {
                          if (event.key !== "Enter" || (!webInput && !replayInputDirty)) return;
                          if (embeddedReplay?.active_element?.editable) submitReplayInput();
                          else {
                            performWebToolbarAction("input", { text: webInput });
                            setWebInput("");
                          }
                        }}
                        placeholder={embeddedReplay?.active_element?.editable
                          ? `已选中${embeddedReplay.active_element.placeholder || embeddedReplay.active_element.aria_label || "页面输入框"}，可在画面中直接输入`
                          : "先点击页面输入框，再输入内容"}
                        className="h-7 min-w-0 flex-1 text-[11px]"
                      />
                      <Button
                        size="icon"
                        className="h-7 w-7"
                        title="发送输入"
                        disabled={(!webInput && !replayInputDirty) || webActionMutation.isPending || replayActionMutation.isPending}
                        onClick={() => {
                          if (embeddedReplay?.active_element?.editable) submitReplayInput();
                          else {
                            performWebToolbarAction("input", { text: webInput });
                            setWebInput("");
                          }
                        }}
                      >
                        <Send className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="icon" variant="outline" className="h-7 w-7" title="返回" onClick={() => performWebToolbarAction("back")}>
                        <Undo2 className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="icon" variant="outline" className="h-7 w-7" title="刷新" onClick={() => performWebToolbarAction("refresh")}>
                        <RefreshCw className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="outline" className="h-7 px-2 text-[10px]" title="向下滚动" onClick={() => performWebToolbarAction("scroll", { delta_y: 560 })}>
                        向下滚动
                      </Button>
                    </div>
                  ) : null}
                  {activeSnapshot?.has_screenshot ? (
                    <SnapshotStage
                      snapshot={activeSnapshot}
                      elements={visibleElements}
                      selectedElementId={selectedElement?.id ?? null}
                      onSelect={setSelectedElementId}
                      pickMode={effectivePickMode}
                      inspectOnly={staticInspectMode}
                      actionPending={webActionMutation.isPending || replayActionMutation.isPending || staticPickMutation.isPending}
                      canInteract={
                        staticInspectMode || (recorderActive && hasControl) || embeddedReplay != null
                      }
                      replaySessionId={embeddedReplay?.session_id ?? null}
                      replayId={embeddedReplay?.replay_id ?? null}
                      imageRevision={replayRevision}
                      activeElement={embeddedReplay?.active_element ?? null}
                      inputValue={webInput}
                      onInputChange={(value) => {
                        setWebInput(value);
                        setReplayInputDirty(true);
                      }}
                      onInputSubmit={submitReplayInput}
                      onCanvasAction={(x, y) => {
                        if (embeddedReplay) {
                          void performReplayCanvasAction(
                            x,
                            y,
                            effectivePickMode ? "pick" : "click",
                          );
                        } else if (staticInspectMode && activeSnapshot) {
                          staticPickMutation.mutate({
                            targetSnapshotId: activeSnapshot.id,
                            x,
                            y,
                          });
                        } else if (session) {
                          webActionMutation.mutate({
                            sessionId: session.id,
                            action: effectivePickMode ? "pick" : "click",
                            x,
                            y,
                          });
                        }
                      }}
                    />
                  ) : (
                    <ElementStage
                      platform={platform}
                      elements={visibleElements}
                      selectedElementId={selectedElement?.id ?? null}
                      onSelect={setSelectedElementId}
                    />
                  )}
                </div>
              ) : (
                <div className="flex items-center gap-8">
                  <div className="h-[560px] w-[300px] overflow-hidden rounded-[34px] border-[7px] border-slate-800 bg-background shadow-xl dark:border-slate-700">
                    <div className="mx-auto mt-2 h-5 w-24 rounded-full bg-slate-800 dark:bg-slate-700" />
                    {activeSnapshot?.has_screenshot ? (
                      <MobileSnapshotStage
                        snapshot={activeSnapshot}
                        disabled={
                          !mobileSessionActive
                          || !hasControl
                          || mobileActionMutation.isPending
                        }
                        pickMode={pickMode}
                        onGesture={(gesture) => {
                          if (!session) return;
                          mobileActionMutation.mutate({ sessionId: session.id, ...gesture });
                        }}
                      />
                    ) : (
                      <ElementStage
                        platform={platform}
                        elements={visibleElements}
                        selectedElementId={selectedElement?.id ?? null}
                        onSelect={setSelectedElementId}
                      />
                    )}
                  </div>
                  <div className="w-64 space-y-3">
                    <div className="rounded-xl border bg-background p-4">
                      <div className="flex items-center gap-2 text-sm font-semibold">
                        <Smartphone className="h-4 w-4 text-primary" />
                        {platform === "android" ? "Android Emulator" : "iOS Simulator"}
                      </div>
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">
                        直接点击或拖动画面即可经 Appium 操作模拟器；拾取模式下只识别元素，不执行点击。
                      </p>
                    </div>
                    <ContextCapability
                      icon={<Database className="h-4 w-4" />}
                      label="UI Tree"
                      value={activeSnapshot?.has_document ? "已采集" : "等待 Agent"}
                    />
                    <ContextCapability
                      icon={<Terminal className="h-4 w-4" />}
                      label="设备日志"
                      value={session?.capabilities.device_logs === "best_effort" ? "尽力采集" : "等待 Agent"}
                    />
                    <ContextCapability
                      icon={<Network className="h-4 w-4" />}
                      label="Native Network"
                      value={session?.capabilities.native_network === false ? "已降级" : "能力预检"}
                    />
                    <div className="rounded-xl border bg-background p-3">
                      <div className="mb-2 text-xs font-medium">向当前焦点输入</div>
                      <div className="flex gap-2">
                        <Input
                          value={mobileInput}
                          onChange={(event) => setMobileInput(event.target.value)}
                          placeholder="先点击输入框"
                          className="h-8 text-xs"
                        />
                        <Button
                          size="icon"
                          className="h-8 w-8 shrink-0"
                          disabled={!mobileSessionActive || !hasControl || !mobileInput || mobileActionMutation.isPending}
                          onClick={() => {
                            if (!session) return;
                            mobileActionMutation.mutate({
                              sessionId: session.id,
                              action: "input",
                              text: mobileInput,
                            });
                            setMobileInput("");
                          }}
                        >
                          <Send className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!mobileSessionActive || !hasControl || mobileActionMutation.isPending}
                          onClick={() => session && mobileActionMutation.mutate({ sessionId: session.id, action: "back" })}
                        >
                          <Undo2 className="h-3.5 w-3.5" />返回
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!mobileSessionActive || !hasControl || mobileActionMutation.isPending}
                          onClick={() => session && mobileActionMutation.mutate({ sessionId: session.id, action: "refresh" })}
                        >
                          <RefreshCw className="h-3.5 w-3.5" />刷新
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {events.length > 0 ? (
            <div className="shrink-0 border-t bg-background px-4 py-2">
              <div className="mb-1 flex items-center justify-between text-[10px] text-muted-foreground">
                <span>实时事件时间线</span>
                <span>{events.length} 个事件</span>
              </div>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {events.slice(-8).map((event) => (
                  <div
                    key={event.id}
                    className={cn(
                      "min-w-[138px] rounded-md border px-2.5 py-2 text-[10px]",
                      event.severity === "error" ? "border-red-200 bg-red-50 text-red-700" : "bg-muted/30",
                    )}
                  >
                    <div className="truncate font-medium">{eventLabel(event)}</div>
                    <div className="mt-1 truncate text-muted-foreground">{event.page_key ?? event.source}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="flex h-11 shrink-0 items-center justify-between border-t bg-background px-4 text-[11px] text-muted-foreground">
            <div className="flex items-center gap-4">
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-violet-500" />已验证</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-amber-500" />待入库</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-red-500" />可能失效</span>
            </div>
            <span>画面 · Console · Network · 用户事件 · 环境信息</span>
          </div>
        </main>

        <aside className="min-h-0 overflow-y-auto border-l bg-background">
          {selectedElement ? (
            <div className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="font-semibold">{selectedElement.semantic_name}</h2>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {selectedElement.page_name} · {selectedElement.element_type}
                  </p>
                </div>
                <span className={cn(
                  "rounded-full px-2 py-1 text-[10px]",
                  selectedElement.status === "verified" && "bg-emerald-100 text-emerald-700",
                  selectedElement.status === "pending" && "bg-amber-100 text-amber-700",
                  selectedElement.status === "stale" && "bg-red-100 text-red-700",
                  selectedElement.status === "archived" && "bg-slate-100 text-slate-500",
                )}>
                  {selectedElement.status === "verified" ? "已验证" : selectedElement.status === "pending" ? "待入库" : selectedElement.status === "stale" ? "可能失效" : "已归档"}
                </span>
              </div>

              <div className="mt-4 space-y-2 rounded-lg border bg-muted/20 p-3">
                <div className="text-[11px] font-medium">元素维护</div>
                <Input
                  value={elementNameDraft}
                  onChange={(event) => setElementNameDraft(event.target.value)}
                  placeholder="元素语义名称"
                  className="h-8 text-xs"
                />
                <Input
                  value={elementAliasesDraft}
                  onChange={(event) => setElementAliasesDraft(event.target.value)}
                  placeholder="别名，用逗号分隔"
                  className="h-8 text-xs"
                />
                <div className="flex gap-2">
                  <Select
                    value={selectedElement.status}
                    onValueChange={(value) => elementUpdateMutation.mutate({
                      elementId: selectedElement.id,
                      status: value as UiElement["status"],
                    })}
                  >
                    <SelectTrigger className="h-8 flex-1 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="pending">待审核</SelectItem>
                      <SelectItem value="verified">已验证</SelectItem>
                      <SelectItem value="stale">可能失效</SelectItem>
                      <SelectItem value="archived">已归档</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button
                    size="sm"
                    className="h-8"
                    disabled={!elementNameDraft.trim() || elementUpdateMutation.isPending}
                    onClick={() => elementUpdateMutation.mutate({
                      elementId: selectedElement.id,
                      semanticName: elementNameDraft.trim(),
                      aliases: elementAliasesDraft.split(/[，,]/).map((item) => item.trim()).filter(Boolean),
                    })}
                  >保存</Button>
                </div>
              </div>

              <div className="mt-5 text-xs font-medium">定位器候选</div>
              <div className="mt-2 space-y-2">
                {selectedElement.locators.length > 0 ? selectedElement.locators.map((locator) => (
                  <div
                    key={locator.id}
                    className={cn(
                      "grid w-full grid-cols-[60px_minmax(0,1fr)_auto] items-center gap-2 rounded-lg border px-2 py-2",
                      locator.is_primary && "border-primary/40 bg-primary/5",
                    )}
                  >
                    <span className="text-[10px] font-semibold uppercase text-muted-foreground">{locator.strategy}</span>
                    <div className="min-w-0">
                      {editingLocatorId === locator.id ? (
                        <div className="flex gap-1">
                          <Input value={editingLocatorValue} onChange={(event) => setEditingLocatorValue(event.target.value)} className="h-7 min-w-0 text-[10px]" />
                          <Button size="sm" className="h-7 px-2 text-[10px]" disabled={!editingLocatorValue.trim()} onClick={() => locatorUpdateMutation.mutate({ elementId: selectedElement.id, locatorId: locator.id, locator: editingLocatorValue.trim() })}>保存</Button>
                        </div>
                      ) : (
                        <button type="button" onClick={() => copyLocator(locator.locator)} className="block w-full min-w-0 text-left" title="复制定位器">
                          <code className="block truncate text-[11px]">{locator.locator}</code>
                        </button>
                      )}
                      <span className={cn(
                        "mt-0.5 block text-[9px]",
                        locator.is_unique === true ? "text-emerald-600" : locator.is_unique === false ? "text-red-600" : "text-muted-foreground",
                      )}>
                        {locator.is_unique === true
                          ? "唯一匹配"
                          : locator.is_unique === false
                            ? `${locator.match_count ?? 0} 个匹配`
                            : "尚未验证"} · 评分 {locator.score}
                      </span>
                    </div>
                    <span className="flex items-center gap-0.5">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-6 w-6"
                        title="编辑定位器"
                        onClick={() => {
                          setEditingLocatorId(locator.id);
                          setEditingLocatorValue(locator.locator);
                        }}
                      >
                        <Pencil className="h-3 w-3" />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-6 w-6"
                        title="在当前页面状态验证"
                        disabled={locatorValidateMutation.isPending}
                        onClick={() => locatorValidateMutation.mutate({
                          elementId: selectedElement.id,
                          locatorId: locator.id,
                        })}
                      >
                        <RefreshCw className={cn("h-3 w-3", locatorValidateMutation.isPending && "animate-spin")} />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-6 w-6"
                        title="设为主定位器"
                        disabled={locator.is_primary || locatorUpdateMutation.isPending}
                        onClick={() => locatorUpdateMutation.mutate({
                          elementId: selectedElement.id,
                          locatorId: locator.id,
                          isPrimary: true,
                        })}
                      >
                        <Star className={cn("h-3 w-3", locator.is_primary && "fill-current text-amber-500")} />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-6 w-6 text-red-600"
                        title="删除定位器"
                        disabled={locatorDeleteMutation.isPending}
                        onClick={() => locatorDeleteMutation.mutate({ elementId: selectedElement.id, locatorId: locator.id })}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </span>
                  </div>
                )) : (
                  <div className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">
                    暂无定位器候选
                  </div>
                )}
              </div>

              <div className="mt-3 rounded-lg border border-dashed p-2.5">
                <div className="mb-2 text-[10px] font-medium text-muted-foreground">添加人工定位器</div>
                <div className="flex gap-2">
                  <Select value={newLocatorStrategy} onValueChange={setNewLocatorStrategy}>
                    <SelectTrigger className="h-8 w-24 text-[10px]"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {["id", "css", "name", "text", "link", "xpath", "role", "accessibility_id", "android_uiautomator", "ios_predicate", "ios_class_chain"].map((strategy) => (
                        <SelectItem key={strategy} value={strategy}>{strategy}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    value={newLocatorValue}
                    onChange={(event) => setNewLocatorValue(event.target.value)}
                    placeholder="定位器内容"
                    className="h-8 min-w-0 flex-1 text-[10px]"
                  />
                  <Button
                    size="icon"
                    className="h-8 w-8"
                    disabled={!newLocatorValue.trim() || locatorCreateMutation.isPending}
                    onClick={() => locatorCreateMutation.mutate({
                      elementId: selectedElement.id,
                      strategy: newLocatorStrategy,
                      locator: newLocatorValue.trim(),
                    })}
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>

              <div className="mt-6 text-xs font-medium">元素证据</div>
              <dl className="mt-2 divide-y rounded-lg border text-xs">
                <div className="flex justify-between gap-3 px-3 py-2.5"><dt className="text-muted-foreground">页面版本</dt><dd>#{selectedElement.last_snapshot_id ?? "--"}</dd></div>
                <div className="flex justify-between gap-3 px-3 py-2.5"><dt className="text-muted-foreground">用例引用</dt><dd>{selectedElement.usage_count}</dd></div>
                <div className="flex justify-between gap-3 px-3 py-2.5"><dt className="text-muted-foreground">最近验证</dt><dd>{formatTime(selectedElement.last_verified_at)}</dd></div>
              </dl>

              <Button
                className="mt-5 w-full"
                disabled={selectedElement.locators.length === 0}
                onClick={async () => {
                  const preferred = selectedElement.locators.find((item) => item.is_primary)
                    ?? selectedElement.locators[0];
                  await navigator.clipboard.writeText(JSON.stringify({
                    element_id: selectedElement.id,
                    by: preferred.strategy,
                    locator: preferred.locator,
                  }, null, 2));
                  toast.success("步骤配置已复制；用例编辑器也可直接从元素库选择");
                }}
              >
                <MousePointer2 className="h-4 w-4" />复制为步骤配置
              </Button>
              <Button
                variant="outline"
                className="mt-2 w-full border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700"
                disabled={recorderActive}
                title={recorderActive ? "请先停止录制" : "删除元素"}
                onClick={() => setDeleteTarget({
                  kind: "element",
                  id: selectedElement.id,
                  name: selectedElement.semantic_name,
                  detail: `将删除该元素、${selectedElement.locators.length} 个定位器及页面出现证据。已复制到用例步骤中的定位器配置不会被删除。`,
                })}
              >
                <Trash2 className="h-4 w-4" />删除元素
              </Button>
            </div>
          ) : (
            <div className="grid h-full place-items-center p-8 text-center">
              <div>
                <MousePointer2 className="mx-auto h-8 w-8 text-muted-foreground/50" />
                <div className="mt-3 text-sm font-medium">尚未选中元素</div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  启动录制并在页面或模拟器画面中拾取元素后，这里会展示 ID、CSS、XPath、Accessibility 等候选定位器。
                </p>
              </div>
            </div>
          )}
        </aside>
      </div>

      {session && ACTIVE_STATUSES.includes(session.status) && floatingVisible && !stopConfirmOpen ? (
        <RecorderFloatingBar
          session={session}
          busy={sessionBusy}
          hasControl={hasControl}
          pickMode={pickMode}
          onPause={() => controlMutation.mutate({ sessionId: session.id, action: "pause" })}
          onResume={() => controlMutation.mutate({ sessionId: session.id, action: "resume" })}
          onStop={() => setStopConfirmOpen(true)}
          onTogglePick={() => pickModeMutation.mutate({ sessionId: session.id, enabled: !pickMode })}
          onPopout={openPopout}
          onMinimize={() => setFloatingVisible(false)}
        />
      ) : null}

      <Dialog
        open={deleteTarget != null}
        onOpenChange={(next) => !next && !deleteMutation.isPending && setDeleteTarget(null)}
      >
        <DialogContent className="sm:max-w-[460px]">
          <DialogHeader>
            <DialogTitle>确认删除“{deleteTarget?.name}”</DialogTitle>
            <DialogDescription>
              删除不可恢复，系统会记录删除人、时间、对象和级联范围。
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm leading-6 text-red-800">
            {deleteTarget?.detail}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={deleteMutation.isPending} onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={!deleteTarget || deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget)}
            >
              {deleteMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={startOpen} onOpenChange={(next) => !startMutation.isPending && setStartOpen(next)}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle>开始 {PLATFORM_META[platform].label} 录制</DialogTitle>
            <DialogDescription>
              {platform === "web"
                ? "Recorder Agent 将打开一个独立的可见浏览器。请在该浏览器中正常操作被测系统。"
                : "Recorder Agent 将连接已启动的模拟器和 Appium Server，后续操作都在平台远程画面中完成。"}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            {platform === "web" ? (
              <>
                <label className="block space-y-1.5 text-sm">
                  <span className="font-medium">目标地址</span>
                  <Input
                    autoFocus
                    value={targetUrl}
                    onChange={(event) => setTargetUrl(event.target.value)}
                    placeholder="https://staging.example.com/login"
                  />
                  <span className="block text-xs text-muted-foreground">必须填写完整的 http/https URL。</span>
                </label>
                <label className="block space-y-1.5 text-sm">
                  <span className="font-medium">浏览器内核</span>
                  <Select value={browser} onValueChange={setBrowser}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="chromium">Chromium（推荐）</SelectItem>
                      <SelectItem value="firefox">Firefox</SelectItem>
                      <SelectItem value="webkit">WebKit</SelectItem>
                    </SelectContent>
                  </Select>
                </label>
              </>
            ) : (
              <>
                <div className={cn(
                  "rounded-lg border px-3 py-2.5 text-xs",
                  mobilePreflightReady
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : "border-amber-200 bg-amber-50 text-amber-800",
                )}>
                  {mobilePreflightQuery.isLoading
                    ? "正在检查 Appium 与已启动模拟器…"
                    : mobilePreflightReady
                      ? `宿主机已就绪 · Appium ${mobilePreflight?.appium.version || "running"} · ${
                        platform === "android"
                          ? mobilePreflight?.android_devices.length ?? 0
                          : mobilePreflight?.ios_devices.length ?? 0
                      } 台已启动`
                      : mobilePreflightQuery.error
                        ? `Recorder Agent 预检失败：${messageOf(mobilePreflightQuery.error)}`
                        : platform === "ios" && mobilePreflight?.ios_issues.length
                          ? `iOS 环境未就绪：${mobilePreflight.ios_issues.join("；")}`
                          : "宿主机未就绪：请启动 Appium、对应驱动和所选平台模拟器。"}
                </div>
                <label className="block space-y-1.5 text-sm">
                  <span className="font-medium">模拟器</span>
                  <Select value={selectedDeviceId} onValueChange={setSelectedDeviceId}>
                    <SelectTrigger><SelectValue placeholder="选择已注册模拟器" /></SelectTrigger>
                    <SelectContent>
                      {simulatorDevices.map((device) => (
                        <SelectItem key={device.id} value={String(device.id)}>
                          {device.device_name || device.udid} · {device.platform_version || "未知版本"} · {device.status}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {devicesQuery.isLoading ? (
                    <span className="block text-xs text-muted-foreground">正在读取设备池…</span>
                  ) : simulatorDevices.length === 0 ? (
                    <span className="block text-xs text-amber-700">
                      没有已注册模拟器。请先在设备管理中注册，并设置 capabilities.is_simulator=true。
                    </span>
                  ) : null}
                </label>
                <label className="block space-y-1.5 text-sm">
                  <span className="font-medium">应用包（可选）</span>
                  <Select value={selectedPackageId} onValueChange={setSelectedPackageId}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">连接当前已打开的应用</SelectItem>
                      {mobilePackages.map((item) => (
                        <SelectItem key={item.id} value={String(item.id)}>
                          {item.name} · {item.version || "未知版本"}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
              </>
            )}
            <div className="rounded-lg border bg-muted/30 p-3 text-xs leading-5 text-muted-foreground">
              {platform === "web"
                ? "自动采集：点击与输入、页面跳转、Console、页面异常、XHR/Fetch、操作截图、URL、浏览器和视口信息。密码输入默认脱敏。"
                : "自动采集：模拟器画面、UI Tree、点击/输入/滑动、Accessibility/ID/XPath 定位器、设备与应用环境。Native Network 未配置代理或 SDK 时会明确标记降级。"}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={startMutation.isPending} onClick={() => setStartOpen(false)}>取消</Button>
            <Button
              disabled={
                startMutation.isPending
                || (platform === "web" ? !/^https?:\/\//i.test(targetUrl.trim()) : !selectedDeviceId)
              }
              onClick={() => startMutation.mutate(
                platform === "web"
                  ? { targetUrl: targetUrl.trim(), browser }
                  : {
                      deviceId: Number(selectedDeviceId),
                      appPackageId: selectedPackageId === "none" ? undefined : Number(selectedPackageId),
                    },
              )}
            >
              {startMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CircleDot className="h-4 w-4" />}
              {startMutation.isPending
                ? platform === "web" ? "正在启动浏览器…" : "正在连接模拟器…"
                : platform === "web" ? "打开浏览器并录制" : "连接模拟器并录制"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={stepDraft != null}
        onOpenChange={(next) => {
          if (!next && !commitDraftMutation.isPending) setStepDraft(null);
        }}
      >
        <DialogContent className="max-h-[86vh] sm:max-w-[720px]">
          <DialogHeader>
            <DialogTitle>确认录制生成的用例草稿</DialogTitle>
            <DialogDescription>
              动作已转换为现有 v2 Runner 步骤；本期不自动新增断言，保存后可在原用例编辑器中继续补充。
            </DialogDescription>
          </DialogHeader>
          {stepDraft ? (
            <div className="min-h-0 space-y-4 overflow-y-auto py-1">
              <div className="grid grid-cols-2 gap-3">
                <label className="space-y-1.5 text-sm">
                  <span className="font-medium">用例名称</span>
                  <Input value={draftCaseName} onChange={(event) => setDraftCaseName(event.target.value)} />
                </label>
                <label className="space-y-1.5 text-sm">
                  <span className="font-medium">所属模块</span>
                  <Select value={draftModuleId} onValueChange={setDraftModuleId}>
                    <SelectTrigger><SelectValue placeholder="选择模块" /></SelectTrigger>
                    <SelectContent>
                      {draftModules.map((module) => (
                        <SelectItem key={module.id} value={String(module.id)}>{module.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between text-xs font-medium">
                  <span>执行步骤</span>
                  <span className="text-muted-foreground">{stepDraft.steps.length} 步</span>
                </div>
                <div className="max-h-[330px] space-y-2 overflow-y-auto rounded-lg border bg-muted/20 p-2">
                  {stepDraft.steps.length ? stepDraft.steps.map((step) => (
                    <div key={`${step.step_order}-${step.source_event_id ?? step.step_type}`} className="rounded-md border bg-background px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className="grid h-5 w-5 place-items-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary">
                          {step.step_order}
                        </span>
                        <span className="text-xs font-medium">{step.step_name}</span>
                        <code className="ml-auto text-[10px] text-muted-foreground">{step.step_type}</code>
                      </div>
                      {step.config.locator || step.config.url || step.config.value ? (
                        <div className="mt-1.5 truncate pl-7 text-[10px] text-muted-foreground">
                          {step.config.by ? `${String(step.config.by)}=` : ""}
                          {String(step.config.locator ?? step.config.url ?? step.config.value ?? "")}
                        </div>
                      ) : null}
                    </div>
                  )) : (
                    <div className="p-6 text-center text-xs text-amber-700">
                      本次录制没有可转换的用户动作，请返回页面补录点击或输入。
                    </div>
                  )}
                </div>
              </div>

              {stepDraft.warnings.length ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                  <div className="font-medium">需要人工确认</div>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4">
                    {stepDraft.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" disabled={commitDraftMutation.isPending} onClick={() => setStepDraft(null)}>
              取消
            </Button>
            <Button
              disabled={
                commitDraftMutation.isPending
                || !stepDraft?.steps.length
                || !draftModuleId
                || !draftCaseName.trim()
              }
              onClick={() => commitDraftMutation.mutate()}
            >
              {commitDraftMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              保存到用例库
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <UiRecordingResultDialog
        open={resultOpen}
        session={session ?? null}
        onOpenChange={setResultOpen}
      />

      <Dialog open={stopConfirmOpen} onOpenChange={setStopConfirmOpen}>
        <DialogContent className="sm:max-w-[430px]">
          <DialogHeader>
            <DialogTitle>停止本次录制？</DialogTitle>
            <DialogDescription>
              {platform === "web"
                ? "停止后受控浏览器会关闭，并开始整理页面快照、元素和离线回放数据。"
                : "停止后 Appium 会话会关闭、模拟器租约会释放，并保留画面、UI Tree、动作和定位器证据。"}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setStopConfirmOpen(false)}>继续录制</Button>
            <Button
              variant="destructive"
              disabled={!session || controlMutation.isPending}
              onClick={() => {
                if (!session) return;
                setStopConfirmOpen(false);
                controlMutation.mutate({ sessionId: session.id, action: "stop" });
              }}
            >
              确认停止
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function RecorderFloatingBar({
  session,
  busy,
  hasControl,
  pickMode,
  onPause,
  onResume,
  onStop,
  onTogglePick,
  onPopout,
  onMinimize,
}: {
  session: UiRecordingSession;
  busy: boolean;
  hasControl: boolean;
  pickMode: boolean;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onTogglePick: () => void;
  onPopout: () => void;
  onMinimize: () => void;
}) {
  const [position, setPosition] = useState(() => ({
    x: Math.max(12, window.innerWidth - 520),
    y: 94,
  }));
  const [dragOffset, setDragOffset] = useState<{ x: number; y: number } | null>(null);
  const status = STATUS_META[session.status];

  const move = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (!dragOffset) return;
    setPosition({
      x: Math.min(Math.max(8, event.clientX - dragOffset.x), Math.max(8, window.innerWidth - 500)),
      y: Math.min(Math.max(8, event.clientY - dragOffset.y), Math.max(8, window.innerHeight - 72)),
    });
  };

  return (
    <div
      className="fixed z-[80] flex h-[58px] w-[488px] items-center gap-1 rounded-2xl border bg-background/95 px-2 shadow-2xl backdrop-blur"
      style={{ left: position.x, top: position.y }}
    >
      <button
        type="button"
        className="grid h-10 w-7 touch-none cursor-grab place-items-center rounded-lg text-muted-foreground hover:bg-muted active:cursor-grabbing"
        title="拖动录制条"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          setDragOffset({ x: event.clientX - position.x, y: event.clientY - position.y });
        }}
        onPointerMove={move}
        onPointerUp={(event) => {
          event.currentTarget.releasePointerCapture(event.pointerId);
          setDragOffset(null);
        }}
      >
        <GripVertical className="h-4 w-4" />
      </button>
      <div className="mr-1 min-w-[78px] border-r pr-2">
        <div className="flex items-center gap-1.5 text-[11px] font-semibold">
          <span className={cn("h-2 w-2 rounded-full", session.status === "recording" ? "animate-pulse bg-red-500" : "bg-amber-500")} />
          {status.label}
        </div>
        <div className="mt-0.5 text-[9px] text-muted-foreground">{hasControl ? "本窗口控制" : "其他窗口控制"}</div>
      </div>
      {session.status === "recording" ? (
        <Button size="sm" variant="ghost" disabled={busy || !hasControl} onClick={onPause}>
          <Pause className="h-4 w-4" />暂停
        </Button>
      ) : (
        <Button size="sm" variant="ghost" disabled={busy || !hasControl} onClick={onResume}>
          <Play className="h-4 w-4" />继续
        </Button>
      )}
      <Button
        size="sm"
        variant={pickMode ? "default" : "ghost"}
        disabled={busy || !hasControl}
        onClick={onTogglePick}
        title="拾取时点击只选择元素，不触发业务动作"
      >
        <Crosshair className="h-4 w-4" />拾取
      </Button>
      <Button size="icon" variant="ghost" onClick={onPopout} title="弹出独立窗口">
        <ExternalLink className="h-4 w-4" />
      </Button>
      <Button size="icon" variant="ghost" disabled={busy || !hasControl} onClick={onStop} title="停止录制">
        <Square className="h-3.5 w-3.5" />
      </Button>
      <Button size="icon" variant="ghost" onClick={onMinimize} title="收起录制条">
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

function MobileSnapshotStage({
  snapshot,
  disabled,
  pickMode,
  onGesture,
}: {
  snapshot: UiPageSnapshot;
  disabled: boolean;
  pickMode: boolean;
  onGesture: (gesture:
    | { action: "tap"; x: number; y: number }
    | { action: "swipe"; x: number; y: number; end_x: number; end_y: number; duration_ms: number }
  ) => void;
}) {
  const imageRef = useRef<HTMLImageElement | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [gestureStart, setGestureStart] = useState<{
    clientX: number;
    clientY: number;
    x: number;
    y: number;
    startedAt: number;
  } | null>(null);

  useEffect(() => {
    let disposed = false;
    let objectUrl: string | null = null;
    setImageUrl(null);
    setImageError(null);
    void uiRecordingsApi.snapshotImage(snapshot.id)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (disposed) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        setImageUrl(objectUrl);
      })
      .catch((error: unknown) => {
        if (!disposed) setImageError(messageOf(error));
      });
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [snapshot.id]);

  const imagePoint = (clientX: number, clientY: number) => {
    const image = imageRef.current;
    if (!image) return null;
    const rect = image.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: Math.max(0, Math.min(
        image.naturalWidth - 1,
        Math.round((clientX - rect.left) * image.naturalWidth / rect.width),
      )),
      y: Math.max(0, Math.min(
        image.naturalHeight - 1,
        Math.round((clientY - rect.top) * image.naturalHeight / rect.height),
      )),
    };
  };

  if (imageError) {
    return <div className="grid h-[515px] place-items-center px-4 text-center text-xs text-red-600">{imageError}</div>;
  }
  if (!imageUrl) {
    return <div className="grid h-[515px] place-items-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="relative mt-2 flex h-[515px] touch-none items-center justify-center overflow-hidden bg-black">
      <img
        ref={imageRef}
        src={imageUrl}
        alt={snapshot.page_name}
        draggable={false}
        className={cn(
          "max-h-full max-w-full select-none object-contain",
          disabled ? "cursor-not-allowed opacity-80" : pickMode ? "cursor-crosshair" : "cursor-pointer",
        )}
        onPointerDown={(event) => {
          if (disabled) return;
          const point = imagePoint(event.clientX, event.clientY);
          if (!point) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          setGestureStart({
            clientX: event.clientX,
            clientY: event.clientY,
            x: point.x,
            y: point.y,
            startedAt: performance.now(),
          });
        }}
        onPointerUp={(event) => {
          if (!gestureStart || disabled) return;
          const end = imagePoint(event.clientX, event.clientY);
          setGestureStart(null);
          if (!end) return;
          const distance = Math.hypot(
            event.clientX - gestureStart.clientX,
            event.clientY - gestureStart.clientY,
          );
          if (distance < 12 || pickMode) {
            onGesture({ action: "tap", x: gestureStart.x, y: gestureStart.y });
            return;
          }
          onGesture({
            action: "swipe",
            x: gestureStart.x,
            y: gestureStart.y,
            end_x: end.x,
            end_y: end.y,
            duration_ms: Math.max(
              150,
              Math.min(1500, Math.round(performance.now() - gestureStart.startedAt)),
            ),
          });
        }}
        onPointerCancel={() => setGestureStart(null)}
      />
      <div className="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full bg-black/65 px-3 py-1 text-[9px] text-white">
        {disabled ? "等待录制控制权" : pickMode ? "拾取模式：点击不会执行" : "点击操作 · 拖动滑屏"}
      </div>
    </div>
  );
}

function SnapshotStage({
  snapshot,
  elements,
  selectedElementId,
  onSelect,
  pickMode,
  inspectOnly,
  actionPending,
  canInteract,
  replaySessionId,
  replayId,
  imageRevision,
  activeElement,
  inputValue,
  onInputChange,
  onInputSubmit,
  onCanvasAction,
}: {
  snapshot: UiPageSnapshot;
  elements: UiElement[];
  selectedElementId: number | null;
  onSelect: (id: number) => void;
  pickMode: boolean;
  inspectOnly: boolean;
  actionPending: boolean;
  canInteract: boolean;
  replaySessionId: number | null;
  replayId: string | null;
  imageRevision: number;
  activeElement: UiReplayActiveElement | null;
  inputValue: string;
  onInputChange: (value: string) => void;
  onInputSubmit: () => void;
  onCanvasAction: (x: number, y: number) => void;
}) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [clickPoint, setClickPoint] = useState<{ left: number; top: number } | null>(null);

  useEffect(() => {
    let disposed = false;
    let objectUrl: string | null = null;
    setImageUrl(null);
    setImageError(null);
    const imageRequest = replaySessionId && replayId
      ? uiRecordingsApi.replayImage(replaySessionId, replayId)
      : uiRecordingsApi.snapshotImage(snapshot.id);
    void imageRequest
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (disposed) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        setImageUrl(objectUrl);
      })
      .catch((error: unknown) => {
        if (!disposed) setImageError(messageOf(error));
      });
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [imageRevision, replayId, replaySessionId, snapshot.id]);

  const viewport = snapshot.environment.viewport as {
    width?: number;
    height?: number;
  } | undefined;
  const viewportWidth = Number(viewport?.width || 1);
  const viewportHeight = Number(viewport?.height || 1);
  const snapshotBounds = (
    snapshot.resource_manifest.visible_element_bounds ?? {}
  ) as Record<string, { x?: number; y?: number; width?: number; height?: number }>;

  if (imageError) {
    return <div className="grid flex-1 place-items-center text-xs text-red-600">{imageError}</div>;
  }
  if (!imageUrl) {
    return <div className="grid flex-1 place-items-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-slate-100 p-4 dark:bg-slate-950">
      <div
        className={cn(
          "relative inline-block max-h-full max-w-full overflow-hidden rounded-lg border bg-background shadow-sm",
          canInteract && (pickMode ? "cursor-crosshair" : "cursor-pointer"),
        )}
        onClick={(event) => {
          if (!canInteract || actionPending) return;
          const rect = event.currentTarget.getBoundingClientRect();
          const left = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
          const top = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
          setClickPoint({ left: left * 100, top: top * 100 });
          window.setTimeout(() => setClickPoint(null), 800);
          onCanvasAction(Math.round(left * viewportWidth), Math.round(top * viewportHeight));
        }}
      >
        <img src={imageUrl} alt={snapshot.page_name} className="block max-h-[560px] max-w-full" />
        {elements.map((element) => {
          const bounds = snapshotBounds[element.fingerprint] ?? element.attributes.bounds as {
            x?: number;
            y?: number;
            width?: number;
            height?: number;
          } | undefined;
          if (!bounds || bounds.width == null || bounds.height == null) return null;
          const left = Math.max(0, Math.min(100, (Number(bounds.x || 0) / viewportWidth) * 100));
          const top = Math.max(0, Math.min(100, (Number(bounds.y || 0) / viewportHeight) * 100));
          const width = Math.max(1, Math.min(100 - left, (Number(bounds.width) / viewportWidth) * 100));
          const height = Math.max(1, Math.min(100 - top, (Number(bounds.height) / viewportHeight) * 100));
          return (
            <button
              key={element.id}
              type="button"
              aria-label={`选择元素：${element.semantic_name}`}
              title={element.semantic_name}
              onClick={(event) => {
                event.stopPropagation();
                onSelect(element.id);
              }}
              className={cn(
                "absolute rounded border-2 transition",
                pickMode ? "pointer-events-auto" : "pointer-events-none",
                selectedElementId === element.id
                  ? "border-primary bg-primary/10 ring-2 ring-primary/30"
                  : "border-transparent bg-transparent hover:border-primary hover:bg-primary/10",
              )}
              style={{ left: `${left}%`, top: `${top}%`, width: `${width}%`, height: `${height}%` }}
            />
          );
        })}
        {activeElement?.editable ? (
          <input
            key={`${activeElement.tag}:${activeElement.id ?? activeElement.name ?? "input"}:${activeElement.bounds.x}:${activeElement.bounds.y}`}
            autoFocus
            type={activeElement.input_type === "password" ? "password" : "text"}
            value={inputValue}
            aria-label={activeElement.aria_label || activeElement.placeholder || "离线页面输入框"}
            placeholder={activeElement.placeholder || undefined}
            title="直接输入，按回车提交到离线页面"
            disabled={actionPending}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || actionPending) return;
              event.preventDefault();
              onInputSubmit();
            }}
            className="absolute z-30 min-h-6 rounded border-2 border-primary bg-background/95 px-2 text-xs text-foreground shadow-md outline-none ring-2 ring-primary/20"
            style={{
              left: `${Math.max(0, Math.min(100, (activeElement.bounds.x / viewportWidth) * 100))}%`,
              top: `${Math.max(0, Math.min(100, (activeElement.bounds.y / viewportHeight) * 100))}%`,
              width: `${Math.max(2, Math.min(100, (activeElement.bounds.width / viewportWidth) * 100))}%`,
              height: `${Math.max(2, Math.min(100, (activeElement.bounds.height / viewportHeight) * 100))}%`,
            }}
          />
        ) : null}
        {clickPoint ? (
          <span
            className="pointer-events-none absolute h-7 w-7 -translate-x-1/2 -translate-y-1/2 animate-ping rounded-full border-2 border-primary bg-primary/20"
            style={{ left: `${clickPoint.left}%`, top: `${clickPoint.top}%` }}
          />
        ) : null}
        {actionPending ? (
          <div className="pointer-events-none absolute inset-x-0 top-2 flex justify-center">
            <span className="flex items-center gap-1.5 rounded-full bg-slate-950/75 px-3 py-1 text-[10px] text-white shadow">
              <Loader2 className="h-3 w-3 animate-spin" />正在同步新页面状态…
            </span>
          </div>
        ) : null}
        {inspectOnly && !actionPending ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center">
            <span className="rounded-full bg-slate-950/75 px-3 py-1 text-[10px] text-white shadow">
              只读拾取：点击按钮、输入框或文字查看定位方式
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ElementStage({
  platform,
  elements,
  selectedElementId,
  onSelect,
}: {
  platform: UiPlatform;
  elements: UiElement[];
  selectedElementId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-xs font-semibold tracking-wide text-primary">STRUCTURE VIEW</div>
          <div className="mt-1 text-lg font-semibold">{elements[0]?.page_name ?? "等待采集页面"}</div>
        </div>
        {platform === "web" ? <Monitor className="h-5 w-5 text-muted-foreground" /> : null}
      </div>
      {elements.length > 0 ? (
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {elements.slice(0, 8).map((element) => (
            <button
              type="button"
              key={element.id}
              onClick={() => onSelect(element.id)}
              className={cn(
                "flex items-center justify-between rounded-lg border bg-background px-3 py-3 text-left",
                selectedElementId === element.id ? "border-primary ring-2 ring-primary/15" : "hover:border-primary/40",
              )}
            >
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium">{element.semantic_name}</span>
                <span className="mt-1 block truncate text-[10px] text-muted-foreground">{element.element_type}</span>
              </span>
              <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
            </button>
          ))}
        </div>
      ) : (
        <div className="grid flex-1 place-items-center text-center">
          <div className="max-w-sm">
            <MousePointer2 className="mx-auto h-9 w-9 text-muted-foreground/40" />
            <div className="mt-3 text-sm font-medium">等待 Recorder Agent 上报页面快照</div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              控制面已就绪。接入采集端后，这里将显示可交互离线页面或模拟器画面，并支持直接拾取元素。
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function ContextCapability({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center justify-between rounded-lg border bg-background px-3 py-2.5 text-xs">
      <span className="flex items-center gap-2 text-muted-foreground">{icon}{label}</span>
      <span>{value}</span>
    </div>
  );
}
