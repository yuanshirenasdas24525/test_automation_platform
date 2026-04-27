/**
 * AppPackagesPage：App 安装包仓库。
 *
 * 用户在这里把 .apk / .ipa 上传上来，平台落到 data/app_packages/，
 * 后续 step 编辑器（app_install）就能从下拉框「按名字挑包」，免去手粘
 * 服务器路径或 URL 的麻烦。
 *
 * 操作：
 *   - 上传：弹窗选文件 + 填友好名 + 可选元信息（package id / bundle id / version）
 *   - 列表：按平台 / 项目过滤
 *   - 删除：会同时清磁盘文件
 *   - 下载：方便用户做"二次确认"，确认服务器上的就是自己刚上传的
 */
import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Download,
  Loader2,
  Package,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  appPackagesApi,
  type AppPackage,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";

const PLATFORM_FILTERS = [
  { value: "__all__", label: "全部" },
  { value: "android", label: "Android" },
  { value: "ios", label: "iOS" },
] as const;

export function AppPackagesPage() {
  const queryClient = useQueryClient();
  const [platformFilter, setPlatformFilter] = useState<string>("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<AppPackage | null>(null);

  const filters = useMemo(
    () => (platformFilter ? { platform: platformFilter as "android" | "ios" } : {}),
    [platformFilter],
  );

  const pkgQuery = useQuery({
    queryKey: queryKeys.appPackages(
      platformFilter ? { platform: platformFilter } : undefined,
    ),
    queryFn: () => appPackagesApi.list(filters),
    staleTime: 15 * 1000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["app_packages"] });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => appPackagesApi.remove(id),
    onSuccess: () => {
      toast.success("已删除");
      invalidate();
      setPendingDelete(null);
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "删除失败"),
  });

  const packages = pkgQuery.data ?? [];

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">App 包管理</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            集中管理 .apk / .ipa 安装包。上传后，App 用例的{" "}
            <code className="rounded bg-muted px-1 font-mono text-xs">app_install</code>{" "}
            步骤可以直接从下拉里挑包，不用再手粘服务器路径。
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => pkgQuery.refetch()}
            disabled={pkgQuery.isFetching}
          >
            {pkgQuery.isFetching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            刷新
          </Button>
          <Button onClick={() => setUploadOpen(true)}>
            <Plus className="h-4 w-4" />
            上传安装包
          </Button>
        </div>
      </div>

      {/* 过滤栏 */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Label className="text-xs text-muted-foreground">平台</Label>
          <Select
            value={platformFilter || "__all__"}
            onValueChange={(v) => setPlatformFilter(v === "__all__" ? "" : v)}
          >
            <SelectTrigger className="h-8 w-[140px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PLATFORM_FILTERS.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {pkgQuery.isLoading ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      ) : pkgQuery.isError ? (
        <Card>
          <CardContent className="space-y-3 p-6 text-center">
            <p className="text-sm text-destructive">
              {pkgQuery.error instanceof Error ? pkgQuery.error.message : "加载失败"}
            </p>
            <Button variant="outline" onClick={() => pkgQuery.refetch()}>
              重试
            </Button>
          </CardContent>
        </Card>
      ) : packages.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 p-10 text-center">
            <Package className="h-10 w-10 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              还没上传过任何安装包。点右上角「上传安装包」开始。
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {packages.map((pkg) => (
            <PackageCard
              key={pkg.id}
              pkg={pkg}
              onDelete={() => setPendingDelete(pkg)}
            />
          ))}
        </div>
      )}

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => {
          invalidate();
          setUploadOpen(false);
        }}
      />

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(v) => !v && setPendingDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除安装包</DialogTitle>
            <DialogDescription>
              将同时删除磁盘文件。已经在用例里引用了这个文件路径的步骤，下次执行会找不到文件。
              确定继续？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPendingDelete(null)}
              disabled={deleteMutation.isPending}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={() =>
                pendingDelete && deleteMutation.mutate(pendingDelete.id)
              }
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单个卡片
// ---------------------------------------------------------------------------
function PackageCard({
  pkg,
  onDelete,
}: {
  pkg: AppPackage;
  onDelete: () => void;
}) {
  const platLabel = pkg.platform === "android" ? "Android" : "iOS";
  const platClass =
    pkg.platform === "android"
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
      : "bg-sky-500/15 text-sky-700 dark:text-sky-400";

  return (
    <Card>
      <CardContent className="space-y-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium" title={pkg.name}>
              {pkg.name}
            </div>
            <div className="truncate text-xs text-muted-foreground" title={pkg.file_name}>
              {pkg.file_name}
            </div>
          </div>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${platClass}`}
          >
            {platLabel}
          </span>
        </div>

        <div className="space-y-0.5 text-[11px] text-muted-foreground">
          {pkg.version ? <div>版本: {pkg.version}</div> : null}
          {pkg.platform === "android" && pkg.app_package ? (
            <div className="truncate">包名: {pkg.app_package}</div>
          ) : null}
          {pkg.platform === "ios" && pkg.bundle_id ? (
            <div className="truncate">bundleId: {pkg.bundle_id}</div>
          ) : null}
          <div>大小: {formatBytes(pkg.file_size)}</div>
          {pkg.upload_time ? (
            <div>上传: {new Date(pkg.upload_time).toLocaleString()}</div>
          ) : null}
        </div>

        {pkg.description ? (
          <p className="line-clamp-2 text-xs text-muted-foreground">
            {pkg.description}
          </p>
        ) : null}

        <div className="flex items-center gap-2 pt-1">
          <a
            className="inline-flex h-7 items-center gap-1 rounded-md border px-2 text-xs hover:bg-accent"
            href={appPackagesApi.downloadUrl(pkg.id)}
            download={pkg.file_name}
          >
            <Download className="h-3.5 w-3.5" />
            下载
          </a>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs text-destructive hover:text-destructive"
            onClick={onDelete}
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 上传对话框
// ---------------------------------------------------------------------------
function UploadDialog({
  open,
  onClose,
  onUploaded,
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState<"android" | "ios" | "">("");
  const [appPackage, setAppPackage] = useState("");
  const [bundleId, setBundleId] = useState("");
  const [version, setVersion] = useState("");
  const [description, setDescription] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setFile(null);
    setName("");
    setPlatform("");
    setAppPackage("");
    setBundleId("");
    setVersion("");
    setDescription("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("请选择文件");
      if (!name.trim()) throw new Error("请填写包名（友好名）");
      return appPackagesApi.upload(file, {
        name: name.trim(),
        platform: platform || undefined,
        app_package: appPackage.trim() || undefined,
        bundle_id: bundleId.trim() || undefined,
        version: version.trim() || undefined,
        description: description.trim() || undefined,
      });
    },
    onSuccess: () => {
      toast.success("上传成功");
      reset();
      onUploaded();
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "上传失败"),
  });

  // 选了文件之后，根据扩展名顺手把 platform 和默认 name 填好
  const handleFile = (f: File | null) => {
    setFile(f);
    if (!f) return;
    const lower = f.name.toLowerCase();
    if (lower.endsWith(".apk")) setPlatform("android");
    else if (lower.endsWith(".ipa")) setPlatform("ios");
    if (!name.trim()) setName(f.name.replace(/\.(apk|ipa)$/i, ""));
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          reset();
          onClose();
        }
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>上传安装包</DialogTitle>
          <DialogDescription>
            支持 .apk / .ipa，单个文件 ≤ 1GB。上传完成后即可在 App 用例的「安装 App」步骤里选用。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <div className="space-y-1">
            <Label className="text-xs">文件 *</Label>
            <Input
              ref={fileInputRef}
              type="file"
              accept=".apk,.ipa"
              onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
              className="text-xs"
            />
            {file ? (
              <p className="text-[11px] text-muted-foreground">
                已选: {file.name}（{formatBytes(file.size)}）
              </p>
            ) : null}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">友好名 *</Label>
              <Input
                className="h-8 text-xs"
                placeholder="例：主 App v3.2-rc1"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">平台</Label>
              <Select
                value={platform || "__auto__"}
                onValueChange={(v) =>
                  setPlatform(v === "__auto__" ? "" : (v as "android" | "ios"))
                }
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__auto__">自动（按扩展名）</SelectItem>
                  <SelectItem value="android">Android</SelectItem>
                  <SelectItem value="ios">iOS</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">appPackage (Android)</Label>
              <Input
                className="h-8 text-xs"
                placeholder="com.example.app"
                value={appPackage}
                onChange={(e) => setAppPackage(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">bundleId (iOS)</Label>
              <Input
                className="h-8 text-xs"
                placeholder="com.example.App"
                value={bundleId}
                onChange={(e) => setBundleId(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-1">
            <Label className="text-xs">版本</Label>
            <Input
              className="h-8 text-xs"
              placeholder="3.2.0"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            />
          </div>

          <div className="space-y-1">
            <Label className="text-xs">备注</Label>
            <Textarea
              rows={2}
              className="text-xs"
              placeholder="发版说明 / 测试范围…"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              reset();
              onClose();
            }}
            disabled={uploadMutation.isPending}
          >
            取消
          </Button>
          <Button
            onClick={() => uploadMutation.mutate()}
            disabled={uploadMutation.isPending || !file || !name.trim()}
          >
            {uploadMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Upload className="h-4 w-4" />
            )}
            上传
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function formatBytes(n: number): string {
  if (!n || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}
