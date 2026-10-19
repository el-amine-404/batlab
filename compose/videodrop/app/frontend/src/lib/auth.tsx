/**
 * Who uses the page: the gate shows the sign-in screen (or, before anyone
 * exists, the first admin's setup) until someone is signed in, then the page
 * for them. useCan() says what they may do, to show only that; the server
 * checks it again on every request.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect } from "react";

import { ErrorScreen } from "@/components/layout/ErrorScreen";
import { Loading } from "@/components/layout/States";
import { SignInScreen } from "@/pages/auth/SignInScreen";

import { api, type Me, type Permission, SIGNED_OUT } from "./api";
import { queryClient } from "./queries";

export const AUTH = ["auth"] as const;

const MeContext = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe outside the AuthGate");
  return me;
}

/** can("settings"): whether the one signed in may; an admin may all. */
export function useCan(): (permission: Permission) => boolean {
  const me = useMe();
  return useCallback((permission: Permission) => me.permissions.includes("admin") || me.permissions.includes(permission), [me]);
}

/** What each permission is called and means (from the server). */
export function usePermissionInfo() {
  const { data } = useQuery({ queryKey: AUTH, queryFn: api.authState, staleTime: Infinity });
  return data?.permissions ?? [];
}

/** After signing in or out: nothing read as someone else stays. */
export function restart(): void {
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== AUTH[0] });
  void queryClient.invalidateQueries({ queryKey: AUTH });
}

export async function signOut(): Promise<void> {
  try {
    await api.logout();
  } finally {
    restart();
  }
}

export function AuthGate({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const state = useQuery({ queryKey: AUTH, queryFn: api.authState, staleTime: Infinity, retry: 1 });

  useEffect(() => {
    const ended = () => void client.invalidateQueries({ queryKey: AUTH });
    window.addEventListener(SIGNED_OUT, ended);
    return () => window.removeEventListener(SIGNED_OUT, ended);
  }, [client]);

  if (state.isPending) return <div className="flex min-h-dvh items-center justify-center"><Loading /></div>;
  if (state.error) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-4">
        <ErrorScreen error={state.error} retry={() => void state.refetch()} />
      </div>
    );
  }
  if (!state.data.me) return <SignInScreen setup={state.data.setup} />;
  return <MeContext.Provider value={state.data.me}>{children}</MeContext.Provider>;
}
