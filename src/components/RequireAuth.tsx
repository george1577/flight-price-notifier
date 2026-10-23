import { useEffect, useState } from "react";
import { Navigate, Outlet, useOutletContext } from "react-router";
import type { User } from "@supabase/supabase-js";

import { supabase } from "@/integrations/supabase/client";

type AuthContext = { user: User };

// Renders nothing until the session is checked, then either the protected page or a redirect.
export function RequireAuth() {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => setUser(data.user));
    // Skip INITIAL_SESSION: it comes from local storage, while getUser() validates with the server.
    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      if (event !== "INITIAL_SESSION") setUser(session?.user ?? null);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  if (user === undefined) return null;
  if (!user) return <Navigate to="/signin" replace />;
  return <Outlet context={{ user } satisfies AuthContext} />;
}

export function useAuthUser() {
  return useOutletContext<AuthContext>().user;
}
