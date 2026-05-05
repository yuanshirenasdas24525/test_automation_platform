/**
 * 多角色用户在工作台顶部切角色用的 tabs。
 *
 * 单角色用户不渲染（外层判断 role_codes.length > 1）。
 * 切换角色 = navigate('/workspace/<role>')，不直接动 activeRole；
 * activeRole 由 WorkspaceRoute 根据 :role 反向同步到 Provider。
 */
import { useNavigate } from "react-router-dom";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ROLE_LABELS } from "@/types/domain";
import type { RoleCode } from "@/types/domain";

interface WorkspaceSwitcherProps {
  current: RoleCode;
  available: RoleCode[];
}

export function WorkspaceSwitcher({ current, available }: WorkspaceSwitcherProps) {
  const navigate = useNavigate();

  if (available.length <= 1) return null;

  return (
    <Tabs
      value={current}
      onValueChange={(value) => navigate(`/workspace/${value}`)}
    >
      <TabsList>
        {available.map((code) => (
          <TabsTrigger key={code} value={code}>
            {ROLE_LABELS[code]}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
