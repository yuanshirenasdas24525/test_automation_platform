/**
 * ProjectConfigTab —— 项目配置主面板。
 *
 * 含 5 个子 Tab：API / Web / App / AI / 其他
 * 挂载在 ProjectManagementPage 的"项目配置" Tab 内。
 */
import { useState } from "react";
import { Globe, Monitor, Smartphone, Brain, Settings } from "lucide-react";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ProjectConfigSubTab } from "@/pages/config/ProjectConfigSubTab";
import { ProjectAiConfigTab } from "@/pages/config/ProjectAiConfigTab";

type ConfigCategory = "api" | "web" | "app" | "ai" | "other";

const CATEGORIES: { value: ConfigCategory; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "api", label: "API", icon: Globe },
  { value: "web", label: "Web", icon: Monitor },
  { value: "app", label: "App", icon: Smartphone },
  { value: "ai", label: "AI", icon: Brain },
  { value: "other", label: "其他", icon: Settings },
];

export function ProjectConfigTab({ projectId, initialCategory }: { projectId: number; initialCategory?: ConfigCategory }) {
  const [active, setActive] = useState<ConfigCategory>(initialCategory ?? "api");

  return (
    <Tabs value={active} onValueChange={(v) => setActive(v as ConfigCategory)}>
      <div className="border-b px-4 pt-2">
        <TabsList>
          {CATEGORIES.map((cat) => (
            <TabsTrigger key={cat.value} value={cat.value}>
              <cat.icon className="mr-1 h-3.5 w-3.5" />
              {cat.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>

      {active === "ai" ? (
        <ProjectAiConfigTab projectId={projectId} />
      ) : (
        <ProjectConfigSubTab key={active} projectId={projectId} category={active} />
      )}
    </Tabs>
  );
}
