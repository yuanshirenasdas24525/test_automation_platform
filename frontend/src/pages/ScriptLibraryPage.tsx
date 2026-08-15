import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  BookOpen,
  CheckCircle2,
  Code2,
  Copy,
  FileCode2,
  Globe2,
  KeyRound,
  Loader2,
  Play,
  Plus,
  Save,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { CryptoTutorialDrawer } from "@/components/script/CryptoTutorialDrawer";
import { RichTextEditor } from "@/components/editor/RichTextEditor";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, scriptsApi } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import { cn } from "@/lib/utils";
import type { ScriptItem, ScriptKind, ScriptPayload, ScriptScope, ScriptTestPayload } from "@/types/domain";

type ScriptDraft = Omit<ScriptItem, "id" | "created_at" | "updated_at" | "scope"> & {
  id: number | null;
  scope: ScriptScope;
  created_at?: string | null;
  updated_at?: string | null;
};

type CachedScriptDraft = {
  draft: ScriptDraft;
  scriptHtml: string;
  testInput: string;
  testOutput: string;
  savedAt: string;
};

const SCRIPT_DRAFT_CACHE_PREFIX = "script_library.draft";

const KIND_META: Record<ScriptKind, { label: string; icon: typeof Code2; reference: (name: string) => string }> = {
  function: {
    label: "动态函数",
    icon: FileCode2,
    reference: (name) => `function:${name}("QA")`,
  },
  crypto_request: {
    label: "请求加密",
    icon: KeyRound,
    reference: (name) => `custom_request_handler = ${name}`,
  },
  crypto_response: {
    label: "响应解密",
    icon: ShieldCheck,
    reference: (name) => `custom_response_handler = ${name}`,
  },
};

const TEMPLATE: Record<ScriptKind, string> = {
  function: `def handler(prefix="AUTO", vars=None, ctx=None):
    import time
    import random
    return f"{prefix}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
`,
  crypto_request: `def handler(headers, body, config, vars=None):
    import hashlib
    import json
    secret = str(config.get("key") or "")
    payload = json.dumps(body or {}, ensure_ascii=False, separators=(",", ":"))
    headers["X-Sign"] = hashlib.md5(f"{payload}{secret}".encode("utf-8")).hexdigest()
    return headers, body
`,
  crypto_response: `def handler(response_body, config, vars=None):
    if isinstance(response_body, dict) and "data" in response_body:
        return response_body["data"]
    return response_body
`,
};

export function ScriptLibraryPage() {
  const { id } = useParams<{ id?: string }>();
  const projectId = id ? Number(id) : undefined;
  return <ScriptLibraryPanel projectId={Number.isFinite(projectId) ? projectId : undefined} />;
}

export function ScriptLibraryPanel({ projectId }: { projectId?: number }) {
  const queryClient = useQueryClient();
  const initialScope: ScriptScope = projectId ? "project" : "global";
  const [scope, setScope] = useState<ScriptScope>(initialScope);
  const [kind, setKind] = useState<ScriptKind | "all">("all");
  const [keyword, setKeyword] = useState("");
  const [selectedId, setSelectedId] = useState<number | "draft" | null>(null);
  const [draft, setDraft] = useState<ScriptDraft | null>(null);
  const [scriptHtml, setScriptHtml] = useState(htmlFromCode(TEMPLATE.function));
  const [testInput, setTestInput] = useState(defaultTestInput("function"));
  const [testOutput, setTestOutput] = useState("{\n  \"status\": \"pending\"\n}");
  const [hasCachedDraft, setHasCachedDraft] = useState(false);
  const [newDraftCache, setNewDraftCache] = useState<CachedScriptDraft | null>(null);
  const [tutorialOpen, setTutorialOpen] = useState(false);
  const skipNextCacheWrite = useRef(false);

  const queryParams = { project_id: projectId, scope, kind: kind === "all" ? undefined : kind };
  const scriptsQuery = useQuery({
    queryKey: queryKeys.scripts(queryParams),
    queryFn: () => scriptsApi.list(queryParams),
  });

  const scripts = useMemo(() => {
    const items = scriptsQuery.data ?? [];
    if (!keyword.trim()) return items;
    const lower = keyword.trim().toLowerCase();
    return items.filter((script) => script.name.toLowerCase().includes(lower));
  }, [keyword, scriptsQuery.data]);

  const selected = useMemo(() => {
    if (selectedId === "draft") return draft;
    return scripts.find((script) => script.id === selectedId) ?? scripts[0] ?? null;
  }, [draft, scripts, selectedId]);

  useEffect(() => {
    if (!selected && scripts.length > 0) {
      setSelectedId(scripts[0].id);
    }
  }, [scripts, selected]);

  useEffect(() => {
    const cache = readDraftCache(cacheKeyForNew(projectId, scope));
    setNewDraftCache(cache);
  }, [projectId, scope]);

  useEffect(() => {
    if (!selected || selectedId === "draft") return;
    const selectedDraft = toDraft(selected);
    const cache = readDraftCache(cacheKeyForDraft(projectId, selectedDraft));
    if (cache) {
      setDraft(cache.draft);
      setScriptHtml(cache.scriptHtml);
      setTestInput(cache.testInput);
      setTestOutput(cache.testOutput);
      setHasCachedDraft(true);
      return;
    }
    skipNextCacheWrite.current = true;
    setDraft(selectedDraft);
    setScriptHtml(htmlFromCode(selected.code));
    setTestInput(defaultTestInput(selected.kind));
    setTestOutput("{\n  \"status\": \"pending\"\n}");
    setHasCachedDraft(false);
  }, [projectId, selected, selectedId]);

  useEffect(() => {
    if (!draft) return;
    if (skipNextCacheWrite.current) {
      skipNextCacheWrite.current = false;
      return;
    }
    writeDraftCache(cacheKeyForDraft(projectId, draft), {
      draft,
      scriptHtml,
      testInput,
      testOutput,
      savedAt: new Date().toISOString(),
    });
    if (draft.id == null) {
      setNewDraftCache({ draft, scriptHtml, testInput, testOutput, savedAt: new Date().toISOString() });
    }
    setHasCachedDraft(true);
  }, [draft, projectId, scriptHtml, testInput, testOutput]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["scripts"] });

  const handleError = (err: unknown) => {
    const msg = err instanceof ApiError ? err.message : err instanceof Error ? err.message : "操作失败";
    toast.error(msg);
  };

  const saveMutation = useMutation({
    mutationFn: (body: ScriptPayload & { id?: number | null }) => {
      if (body.id) return scriptsApi.update(body.id, body);
      return scriptsApi.create(body);
    },
    onSuccess: (saved) => {
      toast.success("保存成功");
      clearDraftCache(cacheKeyForSaved(projectId, saved));
      clearDraftCache(cacheKeyForNew(projectId, saved.scope));
      setNewDraftCache(null);
      setHasCachedDraft(false);
      setSelectedId(saved.id);
      invalidate();
    },
    onError: handleError,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => scriptsApi.remove(id),
    onSuccess: (_unused, deletedId) => {
      toast.success("已删除");
      clearDraftCache(cacheKeyForDeleted(projectId, scope, deletedId));
      queryClient.setQueriesData<ScriptItem[]>({ queryKey: ["scripts"] }, (old) =>
        old ? old.filter((script) => script.id !== deletedId) : old,
      );
      const next = scripts.find((script) => script.id !== deletedId);
      setSelectedId(next?.id ?? null);
      setDraft(null);
      invalidate();
    },
    onError: handleError,
  });

  const testMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: ScriptTestPayload }) => scriptsApi.test(id, payload),
    onSuccess: (result) => {
      setTestOutput(JSON.stringify(result, null, 2));
      if (result.ok) toast.success("测试通过");
      else toast.error(result.error ?? "测试失败");
    },
    onError: handleError,
  });

  const current = draft ?? (selected ? toDraft(selected) : null);
  const meta = current ? KIND_META[current.kind] : KIND_META.function;
  const Icon = meta.icon;
  const duplicateScript = current ? findDuplicateScript(scriptsQuery.data ?? [], current, projectId) : null;
  const currentName = current?.name.trim() ?? "";

  const createDraft = () => {
    const nextKind: ScriptKind = kind === "all" ? "function" : kind;
    const next: ScriptDraft = {
      id: null,
      name: nextKind === "function" ? "new_function" : nextKind === "crypto_request" ? "sign_request" : "decrypt_response",
      kind: nextKind,
      code: TEMPLATE[nextKind],
      enabled: true,
      project_id: scope === "project" ? projectId ?? null : null,
      scope,
      description: "",
    };
    setSelectedId("draft");
    setDraft(next);
    setScriptHtml(htmlFromCode(next.code));
    setTestInput(defaultTestInput(nextKind));
    setTestOutput("{\n  \"status\": \"pending\"\n}");
    setHasCachedDraft(true);
  };

  const openNewDraftCache = () => {
    if (!newDraftCache) return;
    setSelectedId("draft");
    setDraft(newDraftCache.draft);
    setScriptHtml(newDraftCache.scriptHtml);
    setTestInput(newDraftCache.testInput);
    setTestOutput(newDraftCache.testOutput);
    setHasCachedDraft(true);
  };

  const saveCurrent = () => {
    if (!current) return;
    const name = current.name.trim();
    if (!name) {
      toast.error("脚本名称不能为空");
      return;
    }
    if (duplicateScript) {
      toast.error("脚本名称重复", {
        description: `当前${current.scope === "project" ? "项目" : "全局"}的${meta.label}已存在：${duplicateScript.name}`,
      });
      return;
    }
    const code = codeFromRichHtml(scriptHtml);
    saveMutation.mutate({
      id: current.id,
      name,
      kind: current.kind,
      code,
      enabled: current.enabled,
      project_id: current.scope === "project" ? projectId ?? current.project_id : null,
      description: current.description,
    });
    setDraft({ ...current, code });
  };

  const runTest = () => {
    if (!current?.id) {
      toast.error("请先保存脚本再测试");
      return;
    }
    try {
      testMutation.mutate({ id: current.id, payload: JSON.parse(testInput) as ScriptTestPayload });
    } catch {
      toast.error("测试输入不是合法 JSON");
    }
  };

  const copyReference = async () => {
    if (!current) return;
    await navigator.clipboard.writeText(meta.reference(current.name));
    toast.success("已复制引用");
  };

  return (
    <div className="flex h-full min-h-[640px] flex-col bg-background">
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {projectId ? <Code2 className="h-5 w-5 text-slate-600" /> : <Globe2 className="h-5 w-5 text-slate-600" />}
            <h1 className="text-lg font-semibold">{projectId ? "项目脚本库" : "全局脚本库"}</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {projectId ? `项目 #${projectId} 可用脚本` : "全项目通用脚本"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setTutorialOpen(true)}>
            <BookOpen className="mr-2 h-4 w-4" />
            加密教程
          </Button>
          <Button size="sm" onClick={createDraft}>
            <Plus className="mr-2 h-4 w-4" />
            新建脚本
          </Button>
        </div>
      </div>

      <CryptoTutorialDrawer open={tutorialOpen} onClose={() => setTutorialOpen(false)} />

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-80 shrink-0 flex-col border-r bg-muted/20">
          <div className="space-y-3 border-b p-4">
            <Tabs value={scope} onValueChange={(value) => { setScope(value as ScriptScope); setSelectedId(null); setDraft(null); }}>
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="global">
                  <Globe2 className="mr-1.5 h-3.5 w-3.5" />
                  全局
                </TabsTrigger>
                <TabsTrigger value="project" disabled={!projectId}>
                  <Code2 className="mr-1.5 h-3.5 w-3.5" />
                  本项目
                </TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input className="pl-9" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索脚本" />
            </div>
            <Select value={kind} onValueChange={(value) => { setKind(value as ScriptKind | "all"); setSelectedId(null); setDraft(null); }}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部类型</SelectItem>
                <SelectItem value="function">动态函数</SelectItem>
                <SelectItem value="crypto_request">请求加密</SelectItem>
                <SelectItem value="crypto_response">响应解密</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {scriptsQuery.isLoading ? (
              <div className="flex items-center justify-center py-10 text-sm text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                加载中
              </div>
            ) : scripts.length === 0 && !newDraftCache && selectedId !== "draft" ? (
              <div className="rounded-md border border-dashed bg-background px-4 py-8 text-center text-sm text-muted-foreground">
                暂无脚本
              </div>
            ) : (
              <div className="space-y-2">
                {newDraftCache ? (
                  <ScriptListItem
                    script={selectedId === "draft" && current ? current : newDraftCache.draft}
                    active={selectedId === "draft"}
                    onClick={openNewDraftCache}
                  />
                ) : null}
                {scripts.map((script) => (
                  <ScriptListItem key={script.id} script={script} active={current?.id === script.id} onClick={() => setSelectedId(script.id)} />
                ))}
              </div>
            )}
          </div>
        </aside>

        {!current ? (
          <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
            选择或新建一个脚本
          </div>
        ) : (
          <section className="flex min-w-0 flex-1 flex-col">
            <div className="flex items-center justify-between border-b px-6 py-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4 text-muted-foreground" />
                  <h2 className="truncate text-base font-semibold">{current.name || "未命名脚本"}</h2>
                  <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{meta.label}</span>
                  {current.enabled ? (
                    <span className="inline-flex items-center rounded bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">
                      <CheckCircle2 className="mr-1 h-3 w-3" />
                      启用
                    </span>
                  ) : null}
                  {hasCachedDraft ? (
                    <span className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
                      未保存
                    </span>
                  ) : null}
                </div>
                {hasCachedDraft ? (
                  <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    当前内容只保存在本地草稿中，点击右侧“保存”后才会写入脚本库。
                  </div>
                ) : null}
                <div className="mt-2 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                  <code className="truncate rounded bg-muted px-2 py-1 font-mono">{meta.reference(current.name || "handler_name")}</code>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={copyReference}>
                    <Copy className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {current.id ? (
                  <Button variant="outline" size="sm" onClick={() => deleteMutation.mutate(current.id as number)}>
                    <Trash2 className="mr-2 h-4 w-4" />
                    删除
                  </Button>
                ) : null}
                <Button variant="outline" size="sm" onClick={runTest} disabled={testMutation.isPending}>
                  {testMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                  测试运行
                </Button>
                <Button
                  size="sm"
                  onClick={saveCurrent}
                  disabled={saveMutation.isPending || !currentName || duplicateScript != null}
                  className={hasCachedDraft ? "bg-amber-500 text-white hover:bg-amber-600" : undefined}
                >
                  {saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                  {hasCachedDraft ? "保存未保存更改" : "保存"}
                </Button>
              </div>
            </div>

            <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_320px]">
              <div className="min-h-0 p-6">
                <Label className="mb-2 block">Python 脚本</Label>
                <RichTextEditor
                  key={`script-${current.id ?? "draft"}-${current.kind}`}
                  value={scriptHtml}
                  onChange={(value) => {
                    setScriptHtml(value);
                    setDraft({ ...current, code: codeFromRichHtml(value) });
                  }}
                  height={520}
                  toolbar="none"
                  variant="code"
                  placeholder="用代码块编写 Python handler"
                />
              </div>
              <aside className="border-l bg-muted/20 p-5">
                <div className="space-y-5">
                  <div>
                    <Label className="mb-2 block">脚本名称</Label>
                    <Input
                      value={current.name}
                      onChange={(event) => setDraft({ ...current, name: event.target.value })}
                      className={cn(duplicateScript ? "border-destructive focus-visible:ring-destructive" : undefined)}
                    />
                    {!currentName ? (
                      <p className="mt-1 text-xs text-destructive">脚本名称不能为空。</p>
                    ) : duplicateScript ? (
                      <p className="mt-1 text-xs text-destructive">
                        当前{current.scope === "project" ? "项目" : "全局"}的{meta.label}已存在同名脚本：{duplicateScript.name}
                      </p>
                    ) : null}
                  </div>
                  <div>
                    <Label className="mb-2 block">类型</Label>
                    <Select
                      value={current.kind}
                      onValueChange={(value) => {
                        const nextKind = value as ScriptKind;
                        const nextCode = current.id ? codeFromRichHtml(scriptHtml) : TEMPLATE[nextKind];
                        setDraft({ ...current, kind: nextKind, code: nextCode });
                        setScriptHtml(htmlFromCode(nextCode));
                        setTestInput(defaultTestInput(nextKind));
                      }}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="function">动态函数</SelectItem>
                        <SelectItem value="crypto_request">请求加密</SelectItem>
                        <SelectItem value="crypto_response">响应解密</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="mb-2 block">状态</Label>
                    <Select
                      value={current.enabled ? "enabled" : "disabled"}
                      onValueChange={(value) => setDraft({ ...current, enabled: value === "enabled" })}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="enabled">启用</SelectItem>
                        <SelectItem value="disabled">停用</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="mb-2 block">说明</Label>
                    <RichTextEditor
                      key={`desc-${current.id ?? "draft"}`}
                      value={current.description ?? ""}
                      onChange={(value) => setDraft({ ...current, description: value })}
                      height={180}
                      toolbar="full"
                      placeholder="支持表格、代码块、列表等富文本说明"
                    />
                  </div>
                  <div>
                    <Label className="mb-2 block">测试输入</Label>
                    <Textarea className="h-32 resize-none font-mono text-xs" value={testInput} onChange={(event) => setTestInput(event.target.value)} />
                  </div>
                  <div>
                    <Label className="mb-2 block">测试输出</Label>
                    <pre className="h-28 overflow-auto rounded-md border bg-background p-3 text-xs text-muted-foreground">{testOutput}</pre>
                  </div>
                </div>
              </aside>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function ScriptListItem({
  script,
  active,
  onClick,
}: {
  script: ScriptDraft | ScriptItem;
  active: boolean;
  onClick: () => void;
}) {
  const meta = KIND_META[script.kind];
  const Icon = meta.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded-md border bg-background p-3 text-left transition-colors",
        active ? "border-slate-400 shadow-sm" : "border-border hover:border-slate-300",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="truncate text-sm font-medium">{script.name || "未命名脚本"}</span>
        </div>
        <span className={cn(
          "shrink-0 rounded px-1.5 py-0.5 text-[10px]",
          script.id == null
            ? "bg-amber-50 text-amber-700"
            : script.enabled
              ? "bg-emerald-50 text-emerald-700"
              : "bg-slate-100 text-slate-500",
        )}>
          {script.id == null ? "未保存" : script.enabled ? "启用" : "停用"}
        </span>
      </div>
      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>{meta.label}</span>
        <span>{"updated_at" in script && script.updated_at ? formatTime(script.updated_at) : "未保存"}</span>
      </div>
    </button>
  );
}

function toDraft(script: ScriptItem | ScriptDraft): ScriptDraft {
  return {
    id: script.id,
    name: script.name,
    kind: script.kind,
    code: script.code,
    enabled: script.enabled,
    project_id: script.project_id,
    scope: script.scope,
    description: script.description,
    created_at: script.created_at,
    updated_at: script.updated_at,
  };
}

function findDuplicateScript(items: ScriptItem[], current: ScriptDraft, projectId: number | undefined): ScriptItem | null {
  const name = current.name.trim();
  if (!name) return null;
  const expectedProjectId = current.scope === "project" ? projectId ?? current.project_id : null;
  return (
    items.find((script) => {
      const scriptProjectId = script.scope === "project" ? script.project_id : null;
      return (
        script.id !== current.id &&
        script.kind === current.kind &&
        script.scope === current.scope &&
        scriptProjectId === expectedProjectId &&
        script.name.trim() === name
      );
    }) ?? null
  );
}

function cacheKeyForDraft(projectId: number | undefined, draft: ScriptDraft): string {
  if (draft.id != null) return cacheKeyForScriptId(draft.id);
  return cacheKeyForNew(projectId, draft.scope);
}

function cacheKeyForScript(projectId: number | undefined, script: ScriptItem): string {
  return script.id != null ? cacheKeyForScriptId(script.id) : cacheKeyForNew(projectId, script.scope);
}

function cacheKeyForSaved(projectId: number | undefined, script: ScriptItem): string {
  return cacheKeyForScript(projectId, script);
}

function cacheKeyForDeleted(_projectId: number | undefined, _scope: ScriptScope, scriptId: number): string {
  return cacheKeyForScriptId(scriptId);
}

function cacheKeyForScriptId(scriptId: number): string {
  return `${SCRIPT_DRAFT_CACHE_PREFIX}:script:${scriptId}`;
}

function cacheKeyForNew(projectId: number | undefined, scope: ScriptScope): string {
  const scopeKey = scope === "project" ? `project:${projectId ?? "unknown"}` : "global";
  return `${SCRIPT_DRAFT_CACHE_PREFIX}:new:${scopeKey}`;
}

function readDraftCache(key: string): CachedScriptDraft | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CachedScriptDraft;
    if (!parsed?.draft || typeof parsed.scriptHtml !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeDraftCache(key: string, value: CachedScriptDraft): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage 满了不影响正常编辑。
  }
}

function clearDraftCache(key: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(key);
}

function defaultTestInput(kind: ScriptKind): string {
  if (kind === "function") {
    return JSON.stringify({ args: ["QA"], vars: {} }, null, 2);
  }
  if (kind === "crypto_request") {
    return JSON.stringify({ headers: {}, body: { username: "admin" }, config: { key: "secret" }, vars: {} }, null, 2);
  }
  return JSON.stringify({ body: { data: { ok: true } }, config: {}, vars: {} }, null, 2);
}

function htmlFromCode(code: string): string {
  return `<pre><code class="language-python">${escapeHtml(code.trimEnd())}</code></pre>`;
}

function codeFromRichHtml(html: string): string {
  if (typeof DOMParser === "undefined") {
    return html.replace(/<[^>]+>/g, "").trimEnd() + "\n";
  }
  const doc = new DOMParser().parseFromString(html, "text/html");
  const codeNode = doc.querySelector("pre code") ?? doc.querySelector("pre");
  const text = codeNode?.textContent ?? doc.body.textContent ?? "";
  return text.trimEnd() + "\n";
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}
