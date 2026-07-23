import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api, token, type Me } from "./api";

interface AuthState {
  me: Me | null;
  loading: boolean;
  signIn: (accessToken: string) => Promise<void>;
  signOut: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    if (!token.get()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      setMe(await api.me());
    } catch {
      token.clear();
      setMe(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const signIn = async (accessToken: string) => {
    token.set(accessToken);
    setLoading(true);
    await load();
  };

  const signOut = () => {
    token.clear();
    setMe(null);
  };

  return (
    <AuthContext.Provider value={{ me, loading, signIn, signOut, refresh: load }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

/**
 * Convenience flags for the *interface*, never for security.
 *
 * Hiding a button is a courtesy to the user, not a control: every one of these
 * decisions is made again on the server, and the client-side answer is allowed
 * to be wrong without consequence.
 */
export function useRole() {
  const { me } = useAuth();
  return {
    isAdmin: me?.role === "agency_admin",
    isStaff: me?.role === "agency_admin" || me?.role === "agency_member",
    isClient: me?.role === "client_user",
  };
}
