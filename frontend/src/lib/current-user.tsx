/**
 * 当前用户 Context —— 平台无 auth 的临时方案。
 *
 * 顶部 `CurrentUserSwitcher` 让用户从 /api/users 列表里挑一个"扮演"对象，选中后
 * 完整 User 对象 + 当前激活角色写入 localStorage，供工作台/任务列表等地方拼参数
 * （`assignee_dev_id=me` → 实际 user.id）。后续接真 auth 时只换 Provider 数据源、
 * 不动消费侧。
 *
 * activeRole 处理多角色用户：只有一个角色直接用；多个角色时用户在 WorkspaceSwitcher
 * 里选；保存到 localStorage 让刷新后保持。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";

import type { RoleCode, User } from "@/types/domain";
import { ALL_ROLE_CODES } from "@/types/domain";

const STORAGE_KEY = "pm.currentUser";

interface PersistedState {
  user: User | null;
  activeRole: RoleCode | null;
}

interface CurrentUserContextValue extends PersistedState {
  setUser: (user: User | null) => void;
  setActiveRole: (role: RoleCode | null) => void;
}

const CurrentUserContext = createContext<CurrentUserContextValue | null>(null);

function readStorage(): PersistedState {
  if (typeof window === "undefined") return { user: null, activeRole: null };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { user: null, activeRole: null };
    const parsed = JSON.parse(raw) as PersistedState;
    if (!parsed || typeof parsed !== "object")
      return { user: null, activeRole: null };
    const user = parsed.user ?? null;
    const role = parsed.activeRole ?? null;
    return {
      user,
      activeRole:
        role && (ALL_ROLE_CODES as readonly string[]).includes(role)
          ? (role as RoleCode)
          : null,
    };
  } catch {
    return { user: null, activeRole: null };
  }
}

function writeStorage(state: PersistedState) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // 私密浏览模式下 setItem 会抛；忽略，状态仍在内存里
  }
}

export function CurrentUserProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<PersistedState>(() => readStorage());

  useEffect(() => {
    writeStorage(state);
  }, [state]);

  const setUser = useCallback((user: User | null) => {
    setState((prev) => {
      if (!user) return { user: null, activeRole: null };
      // 选用户时如果之前的 activeRole 不在新用户的角色里，回退到首个有效角色
      const validRoles = (user.role_codes ?? []).filter((c) =>
        (ALL_ROLE_CODES as readonly string[]).includes(c),
      ) as RoleCode[];
      const nextRole =
        prev.activeRole && validRoles.includes(prev.activeRole)
          ? prev.activeRole
          : (validRoles[0] ?? null);
      return { user, activeRole: nextRole };
    });
  }, []);

  const setActiveRole = useCallback((role: RoleCode | null) => {
    setState((prev) => ({ ...prev, activeRole: role }));
  }, []);

  const value = useMemo<CurrentUserContextValue>(
    () => ({ ...state, setUser, setActiveRole }),
    [state, setUser, setActiveRole],
  );

  return (
    <CurrentUserContext.Provider value={value}>
      {children}
    </CurrentUserContext.Provider>
  );
}

export function useCurrentUser() {
  const ctx = useContext(CurrentUserContext);
  if (!ctx) {
    throw new Error(
      "useCurrentUser 必须在 <CurrentUserProvider> 内使用（通常 AppLayout 已经挂上）",
    );
  }
  return ctx;
}

/** 给"我的任务"等地方拼 query string 用：没选用户返 undefined。 */
export function useUserId(): number | undefined {
  const { user } = useCurrentUser();
  return user?.id;
}

/** 当前激活角色的便捷 hook。 */
export function useActiveRole(): RoleCode | null {
  return useCurrentUser().activeRole;
}
