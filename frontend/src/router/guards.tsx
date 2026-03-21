'use client';

import { useEffect, useMemo } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/providers/AuthProvider';

const TRACKING_KEY = 'wiki_route_tracking';

export function GlobalRouteGuard() {
  const location = useLocation();

  useEffect(() => {
    if (location.pathname === '/login') {
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
    return <div className="min-h-[40vh] flex items-center justify-center text-zinc-300">Checking session...</div>;
  }

  if (!user) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  return <Outlet />;
}

export function RoutePermissionGuard({ requiredRole }: { requiredRole: 'user' | 'admin' }) {
  const { user } = useAuth();
  const role = useMemo<'user' | 'admin'>(() => {
    const candidate = user?.app_metadata?.role;
    return candidate === 'admin' ? 'admin' : 'user';
  }, [user?.app_metadata?.role]);

  if (requiredRole === 'admin' && role !== 'admin') {
    return (
      <div className="h-full min-h-[40vh] flex items-center justify-center">
        <div className="rounded-xl border border-amber-300/30 bg-amber-500/10 px-4 py-3 text-amber-100">
          Your account does not have permission to access this route.
        </div>
      </div>
    );
  }

  return <Outlet />;
}

export function ComponentDataGuard({
  allow,
  children,
}: {
  allow: boolean;
  children: React.ReactNode;
}) {
  if (!allow) {
    return (
      <div className="h-full min-h-[40vh] flex items-center justify-center">
        <div className="rounded-xl border border-rose-300/30 bg-rose-500/10 px-4 py-3 text-rose-100">
          The route is valid, but this task is not ready to show Wiki content yet.
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
