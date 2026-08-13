import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  Download,
  Gauge,
  ListOrdered,
  Save,
  Users,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";

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
import { casesApi, projectsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PerformanceWorkflowSteps } from "@/pages/PerformanceRequirementPage";

type LoadModel = "arrival_rate" | "virtual_users";
type TrafficPattern = "constant" | "ramp" | "step";
type ScenarioMode = "ordered" | "independent";

type LoadRecommendation = {
  targetRps?: number;
  maxRps?: number;
  stepSize?: number;
  stepMinutes?: number;
  holdMinutes?: number;
  trafficPattern?: TrafficPattern;
};

type RequirementDraft = {
  goal?: string;
  p95ThresholdMs?: number;
  errorRateThreshold?: number;
  loadRecommendation?: LoadRecommendation;
};

type ConcurrencyConfig = {
  loadModel: LoadModel;
  trafficPattern: TrafficPattern;
  scenarioMode: ScenarioMode;
  targetRps: number;
  concurrentUsers: number;
  spawnRate: number;
  rampUpMinutes: number;
  holdMinutes: number;
  coolDownMinutes: number;
  thinkTimeSeconds: number;
  maxLoad: number;
  stepSize: number;
  stepMinutes: number;
  generatorCount: number;
  maxConnectionsPerGenerator: number;
};

const EMPTY_CONFIG: ConcurrencyConfig = {
  loadModel: "arrival_rate",
  trafficPattern: "ramp",
  scenarioMode: "ordered",
  targetRps: 0,
  concurrentUsers: 0,
  spawnRate: 0,
  rampUpMinutes: 0,
  holdMinutes: 0,
  coolDownMinutes: 0,
  thinkTimeSeconds: 0,
  maxLoad: 0,
  stepSize: 0,
  stepMinutes: 0,
  generatorCount: 0,
  maxConnectionsPerGenerator: 0,
};

function parseCaseIds(raw: string | null): number[] {
  if (!raw) return [];
  return [
    ...new Set(
      raw
        .split(",")
        .map((value) => Number(value.trim()))
        .filter((value) => Number.isInteger(value) && value > 0),
    ),
  ];
}

function requirementDraftKey(projectId: number, caseIds: number[]): string {
  return `performance-requirement:${projectId}:${caseIds.join("-") || "empty"}`;
}

function concurrencyPlanKey(projectId: number, caseIds: number[]): string {
  return `performance-concurrency:${projectId}:${caseIds.join("-") || "empty"}`;
}

function readJson<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) as T : null;
  } catch {
    return null;
  }
}

export function PerformanceConcurrencyPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const caseIds = useMemo(
    () => parseCaseIds(searchParams.get("case_ids")),
    [searchParams],
  );
  const requirementKey = useMemo(
    () => requirementDraftKey(projectId, caseIds),
    [caseIds, projectId],
  );
  const planKey = useMemo(
    () => concurrencyPlanKey(projectId, caseIds),
    [caseIds, projectId],
  );
  const [config, setConfig] = useState<ConcurrencyConfig>(EMPTY_CONFIG);
  const [requirementDraft, setRequirementDraft] = useState<RequirementDraft | null>(null);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => projectsApi.get(projectId),
    enabled: Number.isFinite(projectId),
  });
  const casesQuery = useQuery({
    queryKey: ["performance-concurrency-cases", projectId, caseIds],
    queryFn: () => Promise.all(caseIds.map((caseId) => casesApi.get(caseId))),
    enabled: Number.isFinite(projectId) && caseIds.length > 0,
  });

  useEffect(() => {
    const requirement = readJson<RequirementDraft>(requirementKey);
    const savedPlan = readJson<ConcurrencyConfig>(planKey);
    setRequirementDraft(requirement);
    if (savedPlan) {
      setConfig({ ...EMPTY_CONFIG, ...savedPlan });
      return;
    }
    const recommendation = requirement?.loadRecommendation;
    setConfig({
      ...EMPTY_CONFIG,
      targetRps: Math.ceil(Number(recommendation?.targetRps) || 0),
      maxLoad: Math.ceil(Number(recommendation?.maxRps) || 0),
      stepSize: Math.ceil(Number(recommendation?.stepSize) || 0),
      stepMinutes: Number(recommendation?.stepMinutes) || 0,
      holdMinutes: Number(recommendation?.holdMinutes) || 0,
      trafficPattern: recommendation?.trafficPattern ?? "ramp",
    });
  }, [planKey, requirementKey]);

  const update = <K extends keyof ConcurrencyConfig>(
    key: K,
    value: ConcurrencyConfig[K],
  ) => setConfig((current) => ({ ...current, [key]: value }));

  const loadUnit = config.loadModel === "arrival_rate" ? "RPS" : "用户";
  const startLoad =
    config.loadModel === "arrival_rate"
      ? config.targetRps
      : config.concurrentUsers;
  const validationIssues = useMemo(() => {
    const issues: string[] = [];
    if (caseIds.length === 0) issues.push("至少选择一条 API 用例");
    if (config.loadModel === "arrival_rate" && config.targetRps <= 0) {
      issues.push("目标 RPS 必须大于 0");
    }
    if (config.loadModel === "virtual_users") {
      if (config.concurrentUsers <= 0) issues.push("并发用户数必须大于 0");
      if (config.spawnRate <= 0) issues.push("每秒启动用户数必须大于 0");
    }
    if (config.trafficPattern === "ramp" && config.rampUpMinutes <= 0) {
      issues.push("渐进升压时间必须大于 0");
    }
    if (config.trafficPattern !== "step" && config.holdMinutes <= 0) {
      issues.push("目标负载保持时间必须大于 0");
    }
    if (config.trafficPattern === "step") {
      if (config.maxLoad <= startLoad) issues.push(`阶梯最大负载必须大于起始${loadUnit}`);
      if (config.stepSize <= 0) issues.push("每阶增量必须大于 0");
      if (config.stepMinutes <= 0) issues.push("每阶保持时间必须大于 0");
    }
    return issues;
  }, [caseIds.length, config, loadUnit, startLoad]);

  const stepCount =
    config.trafficPattern === "step" && config.stepSize > 0
      ? Math.max(0, Math.ceil((config.maxLoad - startLoad) / config.stepSize))
      : 0;
  const plannedMinutes =
    config.trafficPattern === "step"
      ? (stepCount + 1) * config.stepMinutes + config.coolDownMinutes
      : config.rampUpMinutes + config.holdMinutes + config.coolDownMinutes;

  const buildPlan = () => ({
    projectId,
    projectName: projectQuery.data?.name ?? "",
    caseIds,
    orderedCases: (casesQuery.data ?? []).map((testCase, index) => ({
      order: index + 1,
      caseId: testCase.id,
      name: testCase.name,
      httpSteps: testCase.steps
        .filter((step) => step.step_type === "http_request")
        .map((step) => ({
          method: String(step.config.method || "GET").toUpperCase(),
          path: String(step.config.path || ""),
        })),
    })),
    requirement: requirementDraft,
    concurrency: config,
    plannedMinutes,
    savedAt: new Date().toISOString(),
  });

  const savePlan = (notify = true): boolean => {
    if (validationIssues.length > 0) {
      toast.error(validationIssues[0]);
      return false;
    }
    window.localStorage.setItem(planKey, JSON.stringify(config));
    if (notify) toast.success("并发压测方案已保存");
    return true;
  };

  const exportPlan = () => {
    if (!savePlan(false)) return;
    const blob = new Blob([JSON.stringify(buildPlan(), null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `performance-plan-${projectId}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    toast.success("压测配置已导出，可交给脚本生成器");
  };

  const requirementUrl =
    `/projects/${projectId}/performance?case_ids=${caseIds.join(",")}`;

  if (!Number.isFinite(projectId)) {
    return <div className="p-8 text-sm text-destructive">非法的项目 ID。</div>;
  }

  return (
    <div className="space-y-5 p-6 pb-24">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => navigate(requirementUrl)}
            title="返回压测需求"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold">配置并发压测</h1>
            <p className="truncate text-sm text-muted-foreground">
              {projectQuery.data?.name ?? "项目"} · 业务目标决定负载，机器配置决定如何分发
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => savePlan()}>
            <Save className="h-4 w-4" />
            保存并发方案
          </Button>
          <Button onClick={exportPlan}>
            <Download className="h-4 w-4" />
            导出压测配置
          </Button>
        </div>
      </div>

      <PerformanceWorkflowSteps current={2} />

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <ListOrdered className="h-4 w-4" />
            场景与接口顺序
          </CardTitle>
          <CardDescription>
            顺序模式会按接口池顺序执行，可表达“初始化 → 业务操作 → 查询确认”；独立模式用于互不依赖的接口。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <ChoiceCard
              selected={config.scenarioMode === "ordered"}
              title="顺序业务场景"
              description="每个虚拟用户按池中顺序执行，允许后一步使用前一步提取的数据。"
              onClick={() => update("scenarioMode", "ordered")}
            />
            <ChoiceCard
              selected={config.scenarioMode === "independent"}
              title="独立接口混合"
              description="接口互不依赖并发执行；没有登录流程的业务线通常选这个。"
              onClick={() => update("scenarioMode", "independent")}
            />
          </div>
          <div className="overflow-hidden rounded-lg border">
            {(casesQuery.data ?? []).map((testCase, index) => (
              <div
                key={testCase.id}
                className="grid grid-cols-[42px_minmax(180px,1fr)_100px] items-center gap-3 border-b px-3 py-2.5 text-sm last:border-b-0"
              >
                <span className="font-mono text-xs text-muted-foreground">{index + 1}</span>
                <span className="truncate">{testCase.name}</span>
                <span className="text-right text-xs text-muted-foreground">
                  {testCase.steps.filter((step) => step.step_type === "http_request").length} 个请求
                </span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Gauge className="h-4 w-4" />
            负载模型
          </CardTitle>
          <CardDescription>
            不再默认“100 用户”。按业务目标选择 RPS 或并发用户，两者含义不同，不能直接互换。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <ChoiceCard
              selected={config.loadModel === "arrival_rate"}
              title="到达率（RPS）"
              description="控制每秒请求到达量，适合无登录、短请求和 API 容量验证。"
              icon={Activity}
              onClick={() =>
                setConfig((current) => ({
                  ...current,
                  loadModel: "arrival_rate",
                  maxLoad:
                    current.trafficPattern === "step"
                      ? Math.ceil(
                          Number(requirementDraft?.loadRecommendation?.maxRps) || 0,
                        )
                      : current.maxLoad,
                }))
              }
            />
            <ChoiceCard
              selected={config.loadModel === "virtual_users"}
              title="并发用户"
              description="控制同时在线的会话数量，适合登录态、长事务或带思考时间的业务。"
              icon={Users}
              onClick={() =>
                setConfig((current) => ({
                  ...current,
                  loadModel: "virtual_users",
                  maxLoad: current.trafficPattern === "step" ? 0 : current.maxLoad,
                }))
              }
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {config.loadModel === "arrival_rate" ? (
              <ConfigNumberField
                id="target-rps"
                label={config.trafficPattern === "step" ? "起始 RPS" : "目标 RPS"}
                value={config.targetRps}
                min={1}
                hint="来自上一步业务目标，可人工调整"
                onChange={(value) => update("targetRps", value)}
              />
            ) : (
              <>
                <ConfigNumberField
                  id="concurrent-users"
                  label={config.trafficPattern === "step" ? "起始并发用户" : "目标并发用户"}
                  value={config.concurrentUsers}
                  min={1}
                  hint="由真实在线会话或业务并发目标填写"
                  onChange={(value) => update("concurrentUsers", value)}
                />
                <ConfigNumberField
                  id="spawn-rate"
                  label="每秒启动用户数"
                  value={config.spawnRate}
                  min={0.1}
                  step={0.1}
                  onChange={(value) => update("spawnRate", value)}
                />
                <ConfigNumberField
                  id="think-time"
                  label="用户思考时间（秒）"
                  value={config.thinkTimeSeconds}
                  min={0}
                  step={0.1}
                  hint="纯 API 冲击可填 0"
                  onChange={(value) => update("thinkTimeSeconds", value)}
                />
              </>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Workflow className="h-4 w-4" />
            加压过程
          </CardTitle>
          <CardDescription>
            选择恒定、渐进或阶梯加压。探索系统极限建议使用阶梯模式。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <ChoiceCard
              selected={config.trafficPattern === "constant"}
              title="恒定负载"
              description="直接进入目标负载并持续保持。"
              onClick={() => update("trafficPattern", "constant")}
            />
            <ChoiceCard
              selected={config.trafficPattern === "ramp"}
              title="渐进升压"
              description="在指定时间内平滑升到目标负载。"
              onClick={() => update("trafficPattern", "ramp")}
            />
            <ChoiceCard
              selected={config.trafficPattern === "step"}
              title="阶梯升压"
              description="每阶增加固定负载，用于寻找性能拐点。"
              onClick={() => update("trafficPattern", "step")}
            />
          </div>

          {config.trafficPattern === "step" ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <ConfigNumberField
                id="max-load"
                label={`最大${loadUnit}`}
                value={config.maxLoad}
                min={1}
                onChange={(value) => update("maxLoad", value)}
              />
              <ConfigNumberField
                id="step-size"
                label={`每阶增加${loadUnit}`}
                value={config.stepSize}
                min={1}
                onChange={(value) => update("stepSize", value)}
              />
              <ConfigNumberField
                id="step-minutes"
                label="每阶保持（分钟）"
                value={config.stepMinutes}
                min={1}
                onChange={(value) => update("stepMinutes", value)}
              />
              <ConfigNumberField
                id="step-cool-down"
                label="降压观察（分钟）"
                value={config.coolDownMinutes}
                min={0}
                onChange={(value) => update("coolDownMinutes", value)}
              />
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {config.trafficPattern === "ramp" ? (
                <ConfigNumberField
                  id="ramp-up"
                  label="升到目标负载（分钟）"
                  value={config.rampUpMinutes}
                  min={0.1}
                  step={0.1}
                  onChange={(value) => update("rampUpMinutes", value)}
                />
              ) : null}
              <ConfigNumberField
                id="hold-minutes"
                label="目标负载保持（分钟）"
                value={config.holdMinutes}
                min={1}
                onChange={(value) => update("holdMinutes", value)}
              />
              <ConfigNumberField
                id="cool-down"
                label="降压观察（分钟）"
                value={config.coolDownMinutes}
                min={0}
                onChange={(value) => update("coolDownMinutes", value)}
              />
            </div>
          )}

          <div className="grid gap-3 rounded-xl border bg-muted/20 p-4 sm:grid-cols-3">
            <SummaryItem label="起始/目标负载" value={`${startLoad || "--"} ${loadUnit}`} />
            <SummaryItem
              label="预计总时长"
              value={plannedMinutes > 0 ? `${plannedMinutes} 分钟` : "待补充"}
            />
            <SummaryItem
              label="停止门槛"
              value={`P95 ${requirementDraft?.p95ThresholdMs ?? "--"} ms / 错误率 ${requirementDraft?.errorRateThreshold ?? "--"}%`}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">压测机资源校准</CardTitle>
          <CardDescription>
            业务目标和机器能力分开填写。先确定要验证的负载，再通过单机预检决定需要多少压测机。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <ConfigNumberField
              id="generator-count"
              label="计划压测机数量（0 表示待校准）"
              value={config.generatorCount}
              min={0}
              onChange={(value) => update("generatorCount", value)}
            />
            <ConfigNumberField
              id="connections-per-generator"
              label="单机连接能力（0 表示待预检）"
              value={config.maxConnectionsPerGenerator}
              min={0}
              onChange={(value) => update("maxConnectionsPerGenerator", value)}
            />
          </div>
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            执行前先用目标负载的 10% 做单机预检，观察压测机 CPU、内存、网络和连接数。
            如果压测机先到瓶颈，应增加 worker，而不是降低业务目标或凭机器配置猜并发用户数。
          </div>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm text-muted-foreground">
          {validationIssues.length > 0
            ? `还需补充：${validationIssues.join("；")}`
            : "并发方案完整，可保存或导出给后续脚本生成器。"}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => navigate(requirementUrl)}>
            上一步
          </Button>
          <Button variant="outline" onClick={() => savePlan()}>
            <Save className="h-4 w-4" />
            保存并发方案
          </Button>
          <Button onClick={exportPlan}>
            <Download className="h-4 w-4" />
            导出压测配置
          </Button>
        </div>
      </div>
    </div>
  );
}

function ChoiceCard({
  selected,
  title,
  description,
  icon: Icon,
  onClick,
}: {
  selected: boolean;
  title: string;
  description: string;
  icon?: React.ComponentType<{ className?: string }>;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "rounded-lg border p-3 text-left transition-colors",
        selected
          ? "border-primary bg-primary/5 ring-1 ring-primary/20"
          : "hover:border-primary/40",
      )}
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        {Icon ? <Icon className="h-4 w-4" /> : null}
        {title}
      </div>
      <div className="mt-1 text-xs leading-5 text-muted-foreground">{description}</div>
    </button>
  );
}

function ConfigNumberField({
  id,
  label,
  value,
  onChange,
  min,
  step,
  hint,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  step?: number;
  hint?: string;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={Number.isFinite(value) ? value : 0}
        min={min}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 font-semibold">{value}</div>
    </div>
  );
}
