import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Check,
  Gauge,
  Search,
  ShieldCheck,
  Target,
  TrendingUp,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { casesApi, projectsApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type PerformanceGoal =
  | "peak"
  | "capacity"
  | "stability"
  | "regression"
  | "explore";

type EstimateMode = "business" | "users" | "history" | "manual";

type EstimateInputs = {
  metric: string;
  currentVolume: number;
  peakShare: number;
  peakStart: string;
  peakEnd: string;
  callsPerTransaction: number;
  futureVolume: number;
  safetyMargin: number;
  currentUsers: number;
  futureUsers: number;
  actionsPerMinute: number;
  callsPerAction: number;
  userPeakMinutes: number;
  pastVolume: number;
  historyCurrentVolume: number;
  forecastPeriods: number;
  historyPeakShare: number;
  historyPeakMinutes: number;
  historyCallsPerTransaction: number;
  manualPeakRps: number;
  manualGrowthFactor: number;
  manualPeakMinutes: number;
};

type CapacityInputs = {
  plannedRps: number;
  holdMinutes: number;
  resourceMargin: number;
};

type StabilityInputs = {
  loadPercent: number;
  durationHours: number;
};

type RegressionInputs = {
  baselineRps: number;
  allowedLatencyRegression: number;
  allowedThroughputRegression: number;
  durationMinutes: number;
};

type ExploreInputs = {
  startRps: number;
  incrementRps: number;
  stepMinutes: number;
  maxRps: number;
  stopP95Ms: number;
  stopErrorRate: number;
};

const GOALS = [
  {
    value: "peak",
    label: "峰值达标",
    description: "验证已知业务峰值下是否满足 SLA",
    detail: "根据生产峰值或业务预测计算目标负载，并验证响应时间、错误率和吞吐量。",
    required: ["峰值依据", "增长预期", "SLA 门槛"],
    icon: Target,
  },
  {
    value: "capacity",
    label: "容量上限",
    description: "验证规划容量和资源余量是否达标",
    detail: "已有规划容量或容量目标时，验证目标容量能否稳定承载并保留资源余量。",
    required: ["规划容量", "资源门槛", "安全余量"],
    icon: Gauge,
  },
  {
    value: "stability",
    label: "长期稳定",
    description: "检查泄漏、堆积与性能衰减",
    detail: "在明确的稳定负载下持续运行，观察资源泄漏、队列堆积和性能衰减。",
    required: ["稳定负载", "运行时长", "监控指标"],
    icon: Activity,
  },
  {
    value: "regression",
    label: "性能回归",
    description: "与历史基线比较关键性能指标",
    detail: "使用相同场景和负载与历史结果比较，识别响应时间或吞吐量退化。",
    required: ["历史基线", "对比指标", "允许偏差"],
    icon: BarChart3,
  },
  {
    value: "explore",
    label: "探索系统极限",
    description: "阶梯升压，发现拐点和最大稳定容量",
    detail: "不要求已知生产峰值，通过阶梯升压寻找性能拐点和最大稳定容量。",
    required: ["最大负载", "停止门槛", "升压阶梯"],
    icon: Search,
  },
] as const;

const ESTIMATE_MODES = [
  { value: "business", label: "业务量", description: "订单、查询、消息等" },
  { value: "users", label: "活跃用户", description: "用户行为型业务" },
  { value: "history", label: "历史数据", description: "按两期数据预测" },
  { value: "manual", label: "专业配置", description: "直接填写技术参数" },
] as const;

const DEFAULT_ESTIMATE_INPUTS: EstimateInputs = {
  metric: "订单",
  currentVolume: 120_000,
  peakShare: 18,
  peakStart: "10:00",
  peakEnd: "10:30",
  callsPerTransaction: 1,
  futureVolume: 180_000,
  safetyMargin: 20,
  currentUsers: 800,
  futureUsers: 1_200,
  actionsPerMinute: 1.2,
  callsPerAction: 3,
  userPeakMinutes: 45,
  pastVolume: 80_000,
  historyCurrentVolume: 100_000,
  forecastPeriods: 2,
  historyPeakShare: 20,
  historyPeakMinutes: 30,
  historyCallsPerTransaction: 4,
  manualPeakRps: 50,
  manualGrowthFactor: 1.4,
  manualPeakMinutes: 30,
};

const DEFAULT_CAPACITY_INPUTS: CapacityInputs = {
  plannedRps: 300,
  holdMinutes: 30,
  resourceMargin: 20,
};

const DEFAULT_STABILITY_INPUTS: StabilityInputs = {
  loadPercent: 80,
  durationHours: 4,
};

const DEFAULT_REGRESSION_INPUTS: RegressionInputs = {
  baselineRps: 200,
  allowedLatencyRegression: 10,
  allowedThroughputRegression: 5,
  durationMinutes: 30,
};

const DEFAULT_EXPLORE_INPUTS: ExploreInputs = {
  startRps: 10,
  incrementRps: 25,
  stepMinutes: 5,
  maxRps: 1_000,
  stopP95Ms: 1_000,
  stopErrorRate: 1,
};

function minutesBetween(start: string, end: string): number {
  const [startHour, startMinute] = start.split(":").map(Number);
  const [endHour, endMinute] = end.split(":").map(Number);
  let minutes = endHour * 60 + endMinute - (startHour * 60 + startMinute);
  if (minutes <= 0) minutes += 24 * 60;
  return minutes;
}

function formatNumber(value: number, digits = 2): string {
  if (!Number.isFinite(value)) return "0";
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: digits,
  }).format(value);
}

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

export function PerformanceRequirementPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const caseIds = useMemo(
    () => parseCaseIds(searchParams.get("case_ids")),
    [searchParams],
  );
  const draftKey = useMemo(
    () => `performance-requirement:${projectId}:${caseIds.join("-") || "empty"}`,
    [caseIds, projectId],
  );

  const [goal, setGoal] = useState<PerformanceGoal>("peak");
  const [estimateMode, setEstimateMode] = useState<EstimateMode>("business");
  const [estimateInputs, setEstimateInputs] = useState(DEFAULT_ESTIMATE_INPUTS);
  const [capacityInputs, setCapacityInputs] = useState(DEFAULT_CAPACITY_INPUTS);
  const [stabilityInputs, setStabilityInputs] = useState(DEFAULT_STABILITY_INPUTS);
  const [regressionInputs, setRegressionInputs] = useState(DEFAULT_REGRESSION_INPUTS);
  const [exploreInputs, setExploreInputs] = useState(DEFAULT_EXPLORE_INPUTS);
  const [p95ThresholdMs, setP95ThresholdMs] = useState(500);
  const [errorRateThreshold, setErrorRateThreshold] = useState(0.1);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => projectsApi.get(projectId),
    enabled: Number.isFinite(projectId),
  });

  const casesQuery = useQuery({
    queryKey: ["performance-source-cases", projectId, caseIds],
    queryFn: () => Promise.all(caseIds.map((caseId) => casesApi.get(caseId))),
    enabled: Number.isFinite(projectId) && caseIds.length > 0,
  });

  const httpStepCount = useMemo(
    () =>
      (casesQuery.data ?? []).reduce(
        (total, testCase) =>
          total +
          testCase.steps.filter((step) => step.step_type === "http_request").length,
        0,
      ),
    [casesQuery.data],
  );

  const detectedCallsPerCase = useMemo(() => {
    const count = casesQuery.data?.length ?? 0;
    return count > 0 ? Math.max(1, Math.round(httpStepCount / count)) : 1;
  }, [casesQuery.data?.length, httpStepCount]);

  useEffect(() => {
    if (detectedCallsPerCase <= 1 || estimateInputs.callsPerTransaction !== 1) return;
    setEstimateInputs((current) => ({
      ...current,
      callsPerTransaction: detectedCallsPerCase,
    }));
  }, [detectedCallsPerCase, estimateInputs.callsPerTransaction]);

  useEffect(() => {
    const raw = window.localStorage.getItem(draftKey);
    if (!raw) return;
    try {
      const draft = JSON.parse(raw) as {
        goal?: PerformanceGoal;
        estimateMode?: EstimateMode;
        estimateInputs?: Partial<EstimateInputs>;
        capacityInputs?: Partial<CapacityInputs>;
        stabilityInputs?: Partial<StabilityInputs>;
        regressionInputs?: Partial<RegressionInputs>;
        exploreInputs?: Partial<ExploreInputs>;
        p95ThresholdMs?: number;
        errorRateThreshold?: number;
      };
      if (GOALS.some((item) => item.value === draft.goal)) {
        setGoal(draft.goal!);
      }
      if (ESTIMATE_MODES.some((item) => item.value === draft.estimateMode)) {
        setEstimateMode(draft.estimateMode!);
      }
      if (draft.estimateInputs) {
        setEstimateInputs((current) => ({ ...current, ...draft.estimateInputs }));
      }
      if (draft.capacityInputs) {
        setCapacityInputs((current) => ({ ...current, ...draft.capacityInputs }));
      }
      if (draft.stabilityInputs) {
        setStabilityInputs((current) => ({ ...current, ...draft.stabilityInputs }));
      }
      if (draft.regressionInputs) {
        setRegressionInputs((current) => ({ ...current, ...draft.regressionInputs }));
      }
      if (draft.exploreInputs) {
        setExploreInputs((current) => ({ ...current, ...draft.exploreInputs }));
      }
      if (draft.p95ThresholdMs != null) {
        setP95ThresholdMs(draft.p95ThresholdMs);
      }
      if (draft.errorRateThreshold != null) {
        setErrorRateThreshold(draft.errorRateThreshold);
      }
    } catch {
      window.localStorage.removeItem(draftKey);
    }
  }, [draftKey]);

  const estimate = useMemo(() => {
    if (estimateMode === "business") {
      const peakDurationMinutes = minutesBetween(
        estimateInputs.peakStart,
        estimateInputs.peakEnd,
      );
      const peakBusinessVolume =
        estimateInputs.currentVolume * (estimateInputs.peakShare / 100);
      const businessTps =
        peakDurationMinutes > 0
          ? peakBusinessVolume / (peakDurationMinutes * 60)
          : 0;
      const peakRps = businessTps * estimateInputs.callsPerTransaction;
      const growthFactor =
        estimateInputs.currentVolume > 0
          ? estimateInputs.futureVolume / estimateInputs.currentVolume
          : 0;
      return {
        peakRps,
        growthFactor,
        peakDurationMinutes,
        targetRps:
          peakRps *
          growthFactor *
          (1 + estimateInputs.safetyMargin / 100),
        source: `${formatNumber(estimateInputs.currentVolume, 0)} ${estimateInputs.metric} × ${estimateInputs.peakShare}% × ${estimateInputs.callsPerTransaction} 次 API`,
      };
    }
    if (estimateMode === "users") {
      const peakRps =
        (estimateInputs.currentUsers *
          estimateInputs.actionsPerMinute *
          estimateInputs.callsPerAction) /
        60;
      const growthFactor =
        estimateInputs.currentUsers > 0
          ? estimateInputs.futureUsers / estimateInputs.currentUsers
          : 0;
      return {
        peakRps,
        growthFactor,
        peakDurationMinutes: estimateInputs.userPeakMinutes,
        targetRps:
          peakRps *
          growthFactor *
          (1 + estimateInputs.safetyMargin / 100),
        source: `${formatNumber(estimateInputs.currentUsers, 0)} 活跃用户 × ${estimateInputs.actionsPerMinute} 次操作/分钟`,
      };
    }
    if (estimateMode === "history") {
      const periodGrowth =
        estimateInputs.pastVolume > 0
          ? estimateInputs.historyCurrentVolume / estimateInputs.pastVolume
          : 0;
      const growthFactor = Math.pow(
        periodGrowth,
        estimateInputs.forecastPeriods,
      );
      const peakRps =
        estimateInputs.historyPeakMinutes > 0
          ? (estimateInputs.historyCurrentVolume *
              (estimateInputs.historyPeakShare / 100) *
              estimateInputs.historyCallsPerTransaction) /
            (estimateInputs.historyPeakMinutes * 60)
          : 0;
      return {
        peakRps,
        growthFactor,
        peakDurationMinutes: estimateInputs.historyPeakMinutes,
        targetRps:
          peakRps *
          growthFactor *
          (1 + estimateInputs.safetyMargin / 100),
        source: `${formatNumber(estimateInputs.pastVolume, 0)} → ${formatNumber(estimateInputs.historyCurrentVolume, 0)}，预测 ${estimateInputs.forecastPeriods} 期`,
      };
    }
    return {
      peakRps: estimateInputs.manualPeakRps,
      growthFactor: estimateInputs.manualGrowthFactor,
      peakDurationMinutes: estimateInputs.manualPeakMinutes,
      targetRps:
        estimateInputs.manualPeakRps *
        estimateInputs.manualGrowthFactor *
        (1 + estimateInputs.safetyMargin / 100),
      source: "专业用户直接配置",
    };
  }, [estimateInputs, estimateMode]);

  const selectedGoal = GOALS.find((item) => item.value === goal) ?? GOALS[0];

  const loadRecommendation = useMemo(() => {
    if (goal === "capacity") {
      return {
        targetRps: capacityInputs.plannedRps,
        holdMinutes: capacityInputs.holdMinutes,
        trafficPattern: "ramp",
      };
    }
    if (goal === "stability") {
      return {
        targetRps: estimate.targetRps * (stabilityInputs.loadPercent / 100),
        holdMinutes: stabilityInputs.durationHours * 60,
        trafficPattern: "constant",
      };
    }
    if (goal === "regression") {
      return {
        targetRps: regressionInputs.baselineRps,
        holdMinutes: regressionInputs.durationMinutes,
        trafficPattern: "constant",
      };
    }
    if (goal === "explore") {
      const stepCount =
        exploreInputs.incrementRps > 0
          ? Math.max(
              0,
              Math.ceil(
                (exploreInputs.maxRps - exploreInputs.startRps) /
                  exploreInputs.incrementRps,
              ),
            )
          : 0;
      return {
        targetRps: exploreInputs.startRps,
        maxRps: exploreInputs.maxRps,
        stepSize: exploreInputs.incrementRps,
        stepMinutes: exploreInputs.stepMinutes,
        holdMinutes: (stepCount + 1) * exploreInputs.stepMinutes,
        trafficPattern: "step",
      };
    }
    return {
      targetRps: estimate.targetRps,
      holdMinutes: estimate.peakDurationMinutes,
      trafficPattern: "ramp",
    };
  }, [
    capacityInputs,
    estimate,
    exploreInputs,
    goal,
    regressionInputs,
    stabilityInputs,
  ]);

  const persistDraft = (notify: boolean) => {
    window.localStorage.setItem(
      draftKey,
      JSON.stringify({
        goal,
        estimateMode,
        estimateInputs,
        capacityInputs,
        stabilityInputs,
        regressionInputs,
        exploreInputs,
        p95ThresholdMs,
        errorRateThreshold,
        caseIds,
        loadRecommendation,
        savedAt: new Date().toISOString(),
      }),
    );
    if (notify) toast.success("压测需求草稿已保存在当前浏览器");
  };

  const saveDraft = () => persistDraft(true);

  const goToConcurrency = () => {
    if (caseIds.length === 0) {
      toast.error("请先返回 API 项目根目录选择压测用例");
      return;
    }
    persistDraft(false);
    navigate(
      `/projects/${projectId}/performance/concurrency?case_ids=${caseIds.join(",")}`,
    );
  };

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
            onClick={() => navigate(`/projects/${projectId}?stack=api`)}
            title="返回 API 用例"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold">设计压测需求</h1>
            <p className="truncate text-sm text-muted-foreground">
              {projectQuery.data?.name ?? "项目"} · 从业务目标推导负载参数
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={saveDraft}>保存草稿</Button>
          <Button onClick={goToConcurrency}>
            下一步：配置并发
            <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <PerformanceWorkflowSteps current={1} />

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">来源用例</CardTitle>
          <CardDescription>
            只读取已选择的 API 用例，用于识别业务请求数量；不会立即执行压测。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {caseIds.length === 0 ? (
            <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
              尚未选择 API 用例，请返回 API 用例工作台勾选后再进入。
            </div>
          ) : casesQuery.isLoading ? (
            <div className="text-sm text-muted-foreground">正在读取用例…</div>
          ) : casesQuery.isError ? (
            <div className="text-sm text-destructive">读取来源用例失败。</div>
          ) : (
            <>
              <div className="flex flex-wrap gap-2">
                {(casesQuery.data ?? []).map((testCase) => (
                  <span
                    key={testCase.id}
                    className="rounded-full border bg-muted/40 px-2.5 py-1 text-xs"
                  >
                    {testCase.name}
                  </span>
                ))}
              </div>
              <div className="text-xs text-muted-foreground">
                已选 {caseIds.length} 条用例 · 共 {httpStepCount} 个 HTTP
                请求 · 平均每条业务链路 {detectedCallsPerCase} 个请求
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold">测试目标</h2>
          <p className="text-sm text-muted-foreground">
            选择本次压测最主要的目标。
          </p>
        </div>
        <div
          role="radiogroup"
          aria-label="测试目标"
          className="grid gap-3 md:grid-cols-2 xl:grid-cols-3"
        >
          {GOALS.map((item) => {
            const Icon = item.icon;
            const selected = goal === item.value;
            return (
              <button
                key={item.value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => setGoal(item.value)}
                className={cn(
                  "min-h-28 rounded-xl border bg-card p-4 text-left shadow-sm transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  selected
                    ? "border-primary ring-1 ring-primary/30"
                    : "hover:border-primary/50 hover:bg-accent/20",
                )}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2 font-medium">
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </span>
                  {selected ? (
                    <Check className="h-4 w-4 text-primary" />
                  ) : (
                    <span className="h-4 w-4 rounded-full border" />
                  )}
                </div>
                <div className="mt-2 text-sm text-muted-foreground">
                  {item.description}
                </div>
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3">
          <div>
            <div className="font-medium">{selectedGoal.label}</div>
            <div className="text-sm text-muted-foreground">
              {selectedGoal.detail}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {selectedGoal.required.map((item) => (
              <span
                key={item}
                className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground"
              >
                {item}
              </span>
            ))}
          </div>
        </div>
      </section>

      {goal === "peak" ? (
        <PeakGoalSection
          mode={estimateMode}
          onModeChange={setEstimateMode}
          inputs={estimateInputs}
          onInputsChange={setEstimateInputs}
          estimate={estimate}
          detectedCallsPerCase={detectedCallsPerCase}
        />
      ) : null}

      {goal === "capacity" ? (
        <CapacityGoalSection
          value={capacityInputs}
          onChange={setCapacityInputs}
        />
      ) : null}

      {goal === "stability" ? (
        <StabilityGoalSection
          value={stabilityInputs}
          onChange={setStabilityInputs}
          referenceRps={estimate.targetRps}
        />
      ) : null}

      {goal === "regression" ? (
        <RegressionGoalSection
          value={regressionInputs}
          onChange={setRegressionInputs}
        />
      ) : null}

      {goal === "explore" ? (
        <ExploreGoalSection
          value={exploreInputs}
          onChange={setExploreInputs}
        />
      ) : null}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-4 w-4" />
            停止与通过门槛
          </CardTitle>
          <CardDescription>
            峰值、容量和稳定性目标用于判定通过；探索目标用于自动停止。
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <NumberField
            id="p95-threshold"
            label="P95 响应时间上限（ms）"
            value={p95ThresholdMs}
            min={1}
            onChange={setP95ThresholdMs}
          />
          <NumberField
            id="error-rate-threshold"
            label="最大错误率（%）"
            value={errorRateThreshold}
            min={0}
            step={0.01}
            onChange={setErrorRateThreshold}
          />
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm text-muted-foreground">
          下一步配置到达率或并发用户、升压过程和持续时间；此处不会发送压力流量。
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={saveDraft}>保存草稿</Button>
          <Button onClick={goToConcurrency}>
            下一步：配置并发
            <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

export function PerformanceWorkflowSteps({ current }: { current: 1 | 2 }) {
  const steps = [
    { index: 1 as const, label: "压测需求", description: "目标、负载依据、通过门槛" },
    { index: 2 as const, label: "并发模型", description: "RPS/用户、升压、持续时间" },
  ];
  return (
    <div className="grid overflow-hidden rounded-xl border bg-card sm:grid-cols-2">
      {steps.map((step) => {
        const active = current === step.index;
        const done = current > step.index;
        return (
          <div
            key={step.index}
            className={cn(
              "flex items-center gap-3 border-b px-4 py-3 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0",
              active && "bg-primary/5",
            )}
          >
            <span
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                active && "border-primary bg-primary text-primary-foreground",
                done && "border-emerald-500 bg-emerald-500 text-white",
              )}
            >
              {done ? <Check className="h-4 w-4" /> : step.index}
            </span>
            <div>
              <div className="text-sm font-medium">{step.label}</div>
              <div className="text-xs text-muted-foreground">{step.description}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PeakGoalSection({
  mode,
  onModeChange,
  inputs,
  onInputsChange,
  estimate,
  detectedCallsPerCase,
}: {
  mode: EstimateMode;
  onModeChange: (value: EstimateMode) => void;
  inputs: EstimateInputs;
  onInputsChange: (value: EstimateInputs) => void;
  estimate: {
    peakRps: number;
    growthFactor: number;
    peakDurationMinutes: number;
    targetRps: number;
    source: string;
  };
  detectedCallsPerCase: number;
}) {
  const update = <K extends keyof EstimateInputs>(
    key: K,
    value: EstimateInputs[K],
  ) => {
    onInputsChange({ ...inputs, [key]: value });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">负载参数推算</CardTitle>
        <CardDescription>
          用户填写熟悉的业务信息，系统实时推算生产峰值 RPS、增长系数和峰值持续时间。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-2 md:grid-cols-4">
          {ESTIMATE_MODES.map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={mode === item.value}
              onClick={() => onModeChange(item.value)}
              className={cn(
                "rounded-lg border px-3 py-3 text-left transition-colors",
                mode === item.value
                  ? "border-primary bg-primary/5"
                  : "hover:border-primary/40",
              )}
            >
              <div className="text-sm font-medium">{item.label}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                {item.description}
              </div>
            </button>
          ))}
        </div>

        {mode === "business" ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div className="space-y-2">
              <Label>业务指标</Label>
              <Select value={inputs.metric} onValueChange={(value) => update("metric", value)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["订单", "查询", "消息", "任务"].map((item) => (
                    <SelectItem key={item} value={item}>{item}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <NumberField
              id="current-volume"
              label="当前每日业务量"
              value={inputs.currentVolume}
              min={1}
              onChange={(value) => update("currentVolume", value)}
            />
            <NumberField
              id="peak-share"
              label="高峰期业务占比（%）"
              value={inputs.peakShare}
              min={0.1}
              max={100}
              step={0.1}
              onChange={(value) => update("peakShare", value)}
            />
            <TextField
              id="peak-start"
              label="高峰开始时间"
              type="time"
              value={inputs.peakStart}
              onChange={(value) => update("peakStart", value)}
            />
            <TextField
              id="peak-end"
              label="高峰结束时间"
              type="time"
              value={inputs.peakEnd}
              onChange={(value) => update("peakEnd", value)}
            />
            <NumberField
              id="calls-per-transaction"
              label="每次业务调用 API 数"
              value={inputs.callsPerTransaction}
              min={1}
              hint={`所选用例平均识别为 ${detectedCallsPerCase}，可调整`}
              onChange={(value) => update("callsPerTransaction", value)}
            />
            <NumberField
              id="future-volume"
              label="未来目标业务量"
              value={inputs.futureVolume}
              min={1}
              hint="来自业务规划，不与安全余量混合"
              onChange={(value) => update("futureVolume", value)}
            />
            <NumberField
              id="safety-margin"
              label="安全余量（%）"
              value={inputs.safetyMargin}
              min={0}
              hint="独立于业务增长系数"
              onChange={(value) => update("safetyMargin", value)}
            />
          </div>
        ) : null}

        {mode === "users" ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <NumberField id="current-users" label="当前高峰活跃用户" value={inputs.currentUsers} min={1} onChange={(value) => update("currentUsers", value)} />
            <NumberField id="actions-per-minute" label="每用户每分钟操作数" value={inputs.actionsPerMinute} min={0.1} step={0.1} onChange={(value) => update("actionsPerMinute", value)} />
            <NumberField id="calls-per-action" label="每次操作调用 API 数" value={inputs.callsPerAction} min={1} onChange={(value) => update("callsPerAction", value)} />
            <NumberField id="future-users" label="未来目标活跃用户" value={inputs.futureUsers} min={1} onChange={(value) => update("futureUsers", value)} />
            <NumberField id="user-peak-minutes" label="峰值持续时间（分钟）" value={inputs.userPeakMinutes} min={1} onChange={(value) => update("userPeakMinutes", value)} />
            <NumberField id="user-safety-margin" label="安全余量（%）" value={inputs.safetyMargin} min={0} onChange={(value) => update("safetyMargin", value)} />
          </div>
        ) : null}

        {mode === "history" ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <NumberField id="past-volume" label="上一期业务量" value={inputs.pastVolume} min={1} onChange={(value) => update("pastVolume", value)} />
            <NumberField id="history-current-volume" label="当前业务量" value={inputs.historyCurrentVolume} min={1} onChange={(value) => update("historyCurrentVolume", value)} />
            <NumberField id="forecast-periods" label="预测未来多少期" value={inputs.forecastPeriods} min={1} onChange={(value) => update("forecastPeriods", value)} />
            <NumberField id="history-peak-share" label="高峰期业务占比（%）" value={inputs.historyPeakShare} min={0.1} max={100} onChange={(value) => update("historyPeakShare", value)} />
            <NumberField id="history-peak-minutes" label="峰值持续时间（分钟）" value={inputs.historyPeakMinutes} min={1} onChange={(value) => update("historyPeakMinutes", value)} />
            <NumberField id="history-calls" label="每次业务调用 API 数" value={inputs.historyCallsPerTransaction} min={1} onChange={(value) => update("historyCallsPerTransaction", value)} />
            <NumberField id="history-safety-margin" label="安全余量（%）" value={inputs.safetyMargin} min={0} onChange={(value) => update("safetyMargin", value)} />
          </div>
        ) : null}

        {mode === "manual" ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <NumberField id="manual-peak-rps" label="生产峰值 RPS" value={inputs.manualPeakRps} min={1} onChange={(value) => update("manualPeakRps", value)} />
            <NumberField id="manual-growth-factor" label="未来增长系数" value={inputs.manualGrowthFactor} min={0.1} step={0.1} onChange={(value) => update("manualGrowthFactor", value)} />
            <NumberField id="manual-peak-minutes" label="峰值持续时间（分钟）" value={inputs.manualPeakMinutes} min={1} onChange={(value) => update("manualPeakMinutes", value)} />
            <NumberField id="manual-safety-margin" label="安全余量（%）" value={inputs.safetyMargin} min={0} onChange={(value) => update("safetyMargin", value)} />
          </div>
        ) : null}

        <div className="grid gap-3 rounded-xl border bg-muted/20 p-4 sm:grid-cols-2 xl:grid-cols-4">
          <ResultItem label="生产峰值 RPS" value={formatNumber(estimate.peakRps)} />
          <ResultItem label="未来增长系数" value={`${formatNumber(estimate.growthFactor)}×`} />
          <ResultItem label="峰值持续时间" value={`${formatNumber(estimate.peakDurationMinutes, 0)} 分钟`} />
          <ResultItem label="建议测试到达率" value={`${Math.ceil(estimate.targetRps)} RPS`} primary />
          <div className="text-xs text-muted-foreground sm:col-span-2 xl:col-span-4">
            计算依据：{estimate.source}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function CapacityGoalSection({
  value,
  onChange,
}: {
  value: CapacityInputs;
  onChange: (value: CapacityInputs) => void;
}) {
  return (
    <GoalConfigCard
      title="规划容量验证"
      description="容量上限用于验证一个已经存在的容量目标，不负责寻找未知极限。"
    >
      <NumberField id="planned-rps" label="规划容量（RPS）" value={value.plannedRps} min={1} onChange={(plannedRps) => onChange({ ...value, plannedRps })} />
      <NumberField id="capacity-hold" label="目标负载保持时间（分钟）" value={value.holdMinutes} min={1} onChange={(holdMinutes) => onChange({ ...value, holdMinutes })} />
      <NumberField id="resource-margin" label="最低资源余量（%）" value={value.resourceMargin} min={0} max={100} onChange={(resourceMargin) => onChange({ ...value, resourceMargin })} />
    </GoalConfigCard>
  );
}

function StabilityGoalSection({
  value,
  onChange,
  referenceRps,
}: {
  value: StabilityInputs;
  onChange: (value: StabilityInputs) => void;
  referenceRps: number;
}) {
  const loadRps = referenceRps * (value.loadPercent / 100);
  return (
    <GoalConfigCard
      title="长期稳定配置"
      description={`以当前峰值推算结果为参考，稳定负载约 ${Math.ceil(loadRps)} RPS。`}
    >
      <NumberField id="stability-load" label="参考负载比例（%）" value={value.loadPercent} min={1} max={100} onChange={(loadPercent) => onChange({ ...value, loadPercent })} />
      <NumberField id="stability-hours" label="持续运行时间（小时）" value={value.durationHours} min={1} onChange={(durationHours) => onChange({ ...value, durationHours })} />
    </GoalConfigCard>
  );
}

function RegressionGoalSection({
  value,
  onChange,
}: {
  value: RegressionInputs;
  onChange: (value: RegressionInputs) => void;
}) {
  return (
    <GoalConfigCard
      title="性能回归配置"
      description="使用与历史基线一致的负载，判断延迟和吞吐量是否发生退化。"
    >
      <NumberField id="baseline-rps" label="历史基线负载（RPS）" value={value.baselineRps} min={1} onChange={(baselineRps) => onChange({ ...value, baselineRps })} />
      <NumberField id="latency-regression" label="允许延迟退化（%）" value={value.allowedLatencyRegression} min={0} onChange={(allowedLatencyRegression) => onChange({ ...value, allowedLatencyRegression })} />
      <NumberField id="throughput-regression" label="允许吞吐量退化（%）" value={value.allowedThroughputRegression} min={0} onChange={(allowedThroughputRegression) => onChange({ ...value, allowedThroughputRegression })} />
      <NumberField id="regression-duration" label="对比运行时间（分钟）" value={value.durationMinutes} min={1} onChange={(durationMinutes) => onChange({ ...value, durationMinutes })} />
    </GoalConfigCard>
  );
}

function ExploreGoalSection({
  value,
  onChange,
}: {
  value: ExploreInputs;
  onChange: (value: ExploreInputs) => void;
}) {
  const steps =
    value.incrementRps > 0
      ? Math.max(0, Math.ceil((value.maxRps - value.startRps) / value.incrementRps))
      : 0;
  const maxDuration = (steps + 1) * value.stepMinutes;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <TrendingUp className="h-4 w-4" />
          探索式阶梯升压
        </CardTitle>
        <CardDescription>
          不填写生产峰值；从安全起点逐级增加，到达停止门槛或最大负载后结束。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <NumberField id="explore-start" label="起始负载（RPS）" value={value.startRps} min={1} onChange={(startRps) => onChange({ ...value, startRps })} />
          <NumberField id="explore-increment" label="每阶增加（RPS）" value={value.incrementRps} min={1} onChange={(incrementRps) => onChange({ ...value, incrementRps })} />
          <NumberField id="explore-step-minutes" label="每阶保持（分钟）" value={value.stepMinutes} min={1} onChange={(stepMinutes) => onChange({ ...value, stepMinutes })} />
          <NumberField id="explore-max-rps" label="最大允许负载（RPS）" value={value.maxRps} min={1} onChange={(maxRps) => onChange({ ...value, maxRps })} />
          <NumberField id="explore-stop-p95" label="P95 停止门槛（ms）" value={value.stopP95Ms} min={1} onChange={(stopP95Ms) => onChange({ ...value, stopP95Ms })} />
          <NumberField id="explore-stop-error" label="错误率停止门槛（%）" value={value.stopErrorRate} min={0} step={0.1} onChange={(stopErrorRate) => onChange({ ...value, stopErrorRate })} />
        </div>
        <div className="rounded-lg border bg-muted/20 px-4 py-3 text-sm">
          最多约 {steps + 1} 个负载阶梯，若未提前触发停止条件，最长运行约{" "}
          {formatNumber(maxDuration, 0)} 分钟。
        </div>
      </CardContent>
    </Card>
  );
}

function GoalConfigCard({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {children}
      </CardContent>
    </Card>
  );
}

function NumberField({
  id,
  label,
  value,
  onChange,
  min,
  max,
  step,
  hint,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

function TextField({
  id,
  label,
  type,
  value,
  onChange,
}: {
  id: string;
  label: string;
  type: "time";
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function ResultItem({
  label,
  value,
  primary = false,
}: {
  label: string;
  value: string;
  primary?: boolean;
}) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("mt-1 text-xl font-semibold tabular-nums", primary && "text-primary")}>
        {value}
      </div>
    </div>
  );
}
