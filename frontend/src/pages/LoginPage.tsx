/**
 * 登录页。
 *
 * 调用 POST /api/auth/login 校验用户名密码，
 * 成功后存 token + user 到 Context 和 localStorage，跳到 /workspace。
 */
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, MouseEvent } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  ArrowRight,
  LoaderCircle,
  LockKeyhole,
  UserRound,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCurrentUser } from "@/lib/current-user";
import { authApi, setRefreshToken, setToken } from "@/lib/api";

const schema = z.object({
  username: z.string().min(1, "请输入用户名"),
  password: z.string().min(1, "请输入密码"),
});

type FormValues = z.infer<typeof schema>;

const BUG_COUNT = 8;
const BUG_RESPAWN_MS = 10_000;
const SPLATTER_MS = 1_500;
const SWATTER_HEAD = {
  centerX: 12,
  centerY: -94,
  radiusX: 60,
  radiusY: 54,
};

type CrawlingBug = {
  id: number;
  x: number;
  y: number;
  angle: number;
  speed: number;
  size: number;
  color: string;
  entered: boolean;
  dead: boolean;
  respawnAt: number | null;
};

type BugSplatter = {
  id: number;
  x: number;
  y: number;
  color: string;
};

const bugColors = ["#e56b00", "#8f1d18", "#7c2d12", "#0f172a", "#b45309"];

function createBug(id: number, fromEdge = false): CrawlingBug {
  const side = Math.floor(Math.random() * 4);
  const x = fromEdge
    ? side === 0
      ? -5
      : side === 1
        ? 105
        : 6 + Math.random() * 88
    : 10 + Math.random() * 80;
  const y = fromEdge
    ? side === 2
      ? -6
      : side === 3
        ? 106
        : 6 + Math.random() * 88
    : 10 + Math.random() * 80;
  const targetX = 18 + Math.random() * 64;
  const targetY = 18 + Math.random() * 64;
  return {
    id,
    x,
    y,
    angle: Math.atan2(targetY - y, targetX - x),
    speed: 0.0022 + Math.random() * 0.0028,
    size: 38 + Math.random() * 14,
    color: bugColors[id % bugColors.length],
    entered: !fromEdge,
    dead: false,
    respawnAt: null,
  };
}

export function LoginPage() {
  const navigate = useNavigate();
  const { setUser } = useCurrentUser();
  const [submitting, setSubmitting] = useState(false);
  const playfieldRef = useRef<HTMLElement | null>(null);
  const [bugs, setBugs] = useState<CrawlingBug[]>(() =>
    Array.from({ length: BUG_COUNT }, (_, index) => createBug(index)),
  );
  const bugsRef = useRef<CrawlingBug[]>(bugs);
  const swatTimeoutRef = useRef<number | null>(null);
  const splatterIdRef = useRef(0);
  const [splatters, setSplatters] = useState<BugSplatter[]>([]);
  const [swatter, setSwatter] = useState({
    inside: false,
    x: 0,
    y: 0,
    down: false,
  });

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { username: "admin", password: "" },
  });

  const onSubmit = async (values: FormValues) => {
    setSubmitting(true);
    try {
      const resp = await authApi.login(values);
      setToken(resp.access_token);
      setRefreshToken(resp.refresh_token);
      setUser(resp.user);
      toast.success(`欢迎，${resp.user.full_name || resp.user.username}`);
      navigate("/workspace", { replace: true });
    } catch (err) {
      toast.error((err as Error).message || "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    bugsRef.current = bugs;
  }, [bugs]);

  useEffect(() => {
    let frameId = 0;
    let last = performance.now();

    const tick = (now: number) => {
      const dt = Math.min(48, now - last);
      last = now;
      const next = bugsRef.current.map((bug) => {
        if (bug.dead) {
          if (bug.respawnAt !== null && now >= bug.respawnAt) {
            return createBug(bug.id, true);
          }
          return bug;
        }

        let angle = bug.angle;
        if (Math.random() < 0.018) {
          angle += (Math.random() - 0.5) * 0.9;
        }

        let x = bug.x + Math.cos(angle) * bug.speed * dt;
        let y = bug.y + Math.sin(angle) * bug.speed * dt;

        const entered = bug.entered || (x >= 5 && x <= 95 && y >= 6 && y <= 94);
        const minX = entered ? 4 : -7;
        const maxX = entered ? 96 : 107;
        const minY = entered ? 5 : -8;
        const maxY = entered ? 95 : 108;

        if (x < minX || x > maxX) {
          angle = Math.PI - angle;
          x = Math.min(maxX, Math.max(minX, x));
        }
        if (y < minY || y > maxY) {
          angle = -angle;
          y = Math.min(maxY, Math.max(minY, y));
        }

        return { ...bug, x, y, angle, entered };
      });

      bugsRef.current = next;
      setBugs(next);
      frameId = requestAnimationFrame(tick);
    };

    frameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameId);
  }, []);

  useEffect(() => {
    return () => {
      if (swatTimeoutRef.current !== null) {
        window.clearTimeout(swatTimeoutRef.current);
      }
    };
  }, []);

  const updateSwatter = (event: MouseEvent<HTMLElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setSwatter((prev) => ({
      ...prev,
      inside: true,
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    }));
  };

  const swatBug = (event: MouseEvent<HTMLElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    setSwatter({ inside: true, x, y, down: true });
    if (swatTimeoutRef.current !== null) {
      window.clearTimeout(swatTimeoutRef.current);
    }
    swatTimeoutRef.current = window.setTimeout(() => {
      setSwatter((prev) => ({ ...prev, down: false }));
    }, 150);

    const headCenterX = x + SWATTER_HEAD.centerX;
    const headCenterY = y + SWATTER_HEAD.centerY;
    const liveBugs = bugsRef.current.filter((bug) => !bug.dead);
    const hit = liveBugs
      .map((bug) => {
        const bx = (bug.x / 100) * rect.width;
        const by = (bug.y / 100) * rect.height;
        const normalized =
          ((bx - headCenterX) ** 2) / SWATTER_HEAD.radiusX ** 2 +
          ((by - headCenterY) ** 2) / SWATTER_HEAD.radiusY ** 2;
        return {
          bug,
          distance: Math.hypot(bx - headCenterX, by - headCenterY),
          normalized,
        };
      })
      .filter((item) => item.normalized <= 1)
      .sort((a, b) => a.distance - b.distance)[0];

    if (!hit) return;

    const now = performance.now();
    const splatter: BugSplatter = {
      id: splatterIdRef.current++,
      x: hit.bug.x,
      y: hit.bug.y,
      color: hit.bug.color,
    };
    setSplatters((prev) => [...prev, splatter]);
    window.setTimeout(() => {
      setSplatters((prev) => prev.filter((item) => item.id !== splatter.id));
    }, SPLATTER_MS);

    const next = bugsRef.current.map((bug) =>
      bug.id === hit.bug.id
        ? { ...bug, dead: true, respawnAt: now + BUG_RESPAWN_MS }
        : bug,
    );
    bugsRef.current = next;
    setBugs(next);
  };

  return (
    <div className="min-h-screen bg-[#f6f8fb] text-slate-950">
      <div className="grid min-h-screen lg:grid-cols-[minmax(380px,0.72fr)_minmax(680px,1.28fr)]">
        <section className="flex min-h-screen items-center justify-center px-5 py-8 sm:px-8 lg:px-12">
          <div className="w-full max-w-[420px]">
            <div className="mb-7">
              <div className="flex items-center gap-3">
                <img
                  src="/brand-mark.svg"
                  alt=""
                  className="h-[58px] w-[58px] rounded-lg border border-slate-200 bg-white p-2 shadow-sm"
                />
                <div>
                  <div className="mb-1 whitespace-nowrap text-[13px] font-medium leading-none text-slate-500">
                    Automation Test Platform
                  </div>
                  <h2 className="whitespace-nowrap text-3xl font-semibold leading-tight tracking-normal text-slate-950">
                    自动化测试平台
                  </h2>
                </div>
              </div>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                进入项目、需求、用例、执行报告和 AI 协作流程。
              </p>
            </div>

            <form
              onSubmit={form.handleSubmit(onSubmit)}
              className="space-y-4"
              noValidate
            >
              <div className="space-y-2">
                <Label htmlFor="username" className="text-sm text-slate-700">
                  用户名
                </Label>
                <div className="relative">
                  <UserRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <Input
                    id="username"
                    data-testid="login-username"
                    autoComplete="username"
                    className="h-11 rounded-md border-slate-200 bg-white pl-9 shadow-sm transition focus-visible:ring-2 focus-visible:ring-blue-500"
                    {...form.register("username")}
                  />
                </div>
                {form.formState.errors.username ? (
                  <p className="text-xs text-destructive">
                    {form.formState.errors.username.message}
                  </p>
                ) : null}
              </div>

              <div className="space-y-2">
                <Label htmlFor="password" className="text-sm text-slate-700">
                  密码
                </Label>
                <div className="relative">
                  <LockKeyhole className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <Input
                    id="password"
                    data-testid="login-password"
                    type="password"
                    autoComplete="current-password"
                    className="h-11 rounded-md border-slate-200 bg-white pl-9 shadow-sm transition focus-visible:ring-2 focus-visible:ring-blue-500"
                    {...form.register("password")}
                  />
                </div>
                {form.formState.errors.password ? (
                  <p className="text-xs text-destructive">
                    {form.formState.errors.password.message}
                  </p>
                ) : null}
              </div>

              <Button
                type="submit"
                data-testid="login-submit"
                className="h-11 w-full gap-2 rounded-md bg-slate-950 text-white shadow-sm transition hover:bg-slate-800"
                disabled={submitting}
              >
                {submitting ? (
                  <>
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                    登录中
                  </>
                ) : (
                  <>
                    登录
                    <ArrowRight className="h-4 w-4" />
                  </>
                )}
              </Button>
            </form>
          </div>
        </section>

        <section
          ref={playfieldRef}
          className="relative hidden min-h-screen cursor-none overflow-hidden border-l border-slate-200 bg-[#eef6ff] lg:block"
          onClick={swatBug}
          onMouseEnter={updateSwatter}
          onMouseLeave={() => setSwatter((prev) => ({ ...prev, inside: false }))}
          onMouseMove={updateSwatter}
        >
          <div className="relative h-screen w-full">
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_12%,rgba(56,189,248,0.34),transparent_30%),radial-gradient(circle_at_78%_80%,rgba(52,211,153,0.24),transparent_34%),linear-gradient(135deg,#f8fbff_0%,#e8f3ff_48%,#f5fff8_100%)]" />
            <div className="absolute inset-0 opacity-40 [background-image:linear-gradient(rgba(15,23,42,0.05)_1px,transparent_1px),linear-gradient(90deg,rgba(15,23,42,0.05)_1px,transparent_1px)] [background-size:42px_42px]" />

            <div className="pointer-events-none absolute left-10 top-10 rounded-lg border border-white/80 bg-white/70 px-5 py-4 shadow-sm backdrop-blur">
              <div className="text-xs font-medium uppercase text-slate-400">
                Interactive Bug Hunt
              </div>
              <div className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">
                找到并拍掉 Bug
              </div>
              <div className="mt-1 text-sm text-slate-500">
                最多 8 只，10 秒后重新出现
              </div>
            </div>

            <div className="absolute inset-0">
              {splatters.map((splatter) => (
                <div
                  key={splatter.id}
                  className="login-bug-splatter"
                  style={{
                    "--bug-splatter": splatter.color,
                    left: `${splatter.x}%`,
                    top: `${splatter.y}%`,
                  } as CSSProperties}
                >
                  {Array.from({ length: 6 }, (_, index) => (
                    <span key={index} className={`login-bug-splatter-dot dot-${index + 1}`} />
                  ))}
                </div>
              ))}

              {bugs.map((bug) => (
                <div
                  key={bug.id}
                  className={`login-bug ${bug.dead ? "login-bug-dead" : ""}`}
                  style={{
                    height: bug.size,
                    left: `${bug.x}%`,
                    top: `${bug.y}%`,
                    transform: `translate(-50%, -50%) rotate(${bug.angle + Math.PI / 2}rad)`,
                    width: bug.size,
                  }}
                >
                  <span className="login-bug-antler login-bug-antler-left" />
                  <span className="login-bug-antler login-bug-antler-right" />
                  <span className="login-bug-leg login-bug-leg-1" />
                  <span className="login-bug-leg login-bug-leg-2" />
                  <span className="login-bug-leg login-bug-leg-3" />
                  <span className="login-bug-leg login-bug-leg-4" />
                  <span className="login-bug-leg login-bug-leg-5" />
                  <span className="login-bug-leg login-bug-leg-6" />
                  <span
                    className="login-bug-body"
                    style={{ "--bug-shell": bug.color } as CSSProperties}
                  />
                  <span className="login-bug-head" />
                </div>
              ))}
            </div>

            {swatter.inside ? (
              <div
                className={`login-swatter ${swatter.down ? "login-swatter-hit" : ""}`}
                style={{ left: swatter.x, top: swatter.y }}
              >
                <span className="login-swatter-head" />
                <span className="login-swatter-handle" />
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
