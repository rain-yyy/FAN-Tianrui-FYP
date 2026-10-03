"use client";

import { useEffect, useMemo } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/providers/AuthProvider";

const TRACKING_KEY = "wiki_route_tracking";

export function GlobalRouteGuard() {
  const location = useLocation();

  useEffect(() => {
    // "/" only redirects; recording it would make RestoreLastPath loop on itself.
    if (location.pathname === "/" || location.pathname === "/login") {
      return;
    }
    const payload = {
      path: location.pathname + location.search,
      ts: Date.now(),
    };
    localStorage.setItem(TRACKING_KEY, JSON.stringify(payload));
  }, [location.pathname, location.search]);

  return <Outlet />;
}

export function AuthGuard() {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center text-fg-muted">
        Checking session...
      </div>
    );
  }

  if (!user) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  return <Outlet />;
}

export function RoutePermissionGuard({
  requiredRole,
}: {
  requiredRole: "user" | "admin";
}) {
  const { user } = useAuth();
  const role = useMemo<"user" | "admin">(() => {
    const candidate = user?.app_metadata?.role;
    return candidate === "admin" ? "admin" : "user";
  }, [user?.app_metadata?.role]);

  if (requiredRole === "admin" && role !== "admin") {
    return (
      <div className="h-full min-h-[40vh] flex items-center justify-center">
        <div className="rounded-2xl border border-warn/40 bg-panel px-5 py-4 text-fg">
          Your account does not have permission to access this route.
        </div>
      </div>
    );
  }

  return <Outlet />;
}
