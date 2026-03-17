'use client';

import { Suspense, lazy, useMemo } from 'react';
import {
  Navigate,
  Outlet,
  RouterProvider,
  createBrowserRouter,
  useLocation,
} from 'react-router-dom';
import AppLayout from '@/layouts/AppLayout';
import RootLayoutView from '@/layouts/RootLayoutView';
import { AuthProvider } from '@/providers/AuthProvider';
import { useAuth } from '@/providers/AuthProvider';
import { AuthGuard, GlobalRouteGuard, RoutePermissionGuard } from '@/router/guards';
import RouteErrorPage from '@/views/RouteErrorPage';
import NotFoundPage from '@/views/NotFoundPage';

const LoginRoute = lazy(() => import(/* webpackChunkName: "route-login" */ '@/router/routes/LoginRoute'));
const DashboardRoute = lazy(
  () => import(/* webpackChunkName: "route-dashboard" */ '@/router/routes/DashboardRoute')
);
const HistoryRoute = lazy(() => import(/* webpackChunkName: "route-history" */ '@/router/routes/HistoryRoute'));
const WikiRoute = lazy(() => import(/* webpackChunkName: "route-wiki" */ '@/router/routes/WikiRoute'));

function SuspenseOutlet() {
  return (
    <Suspense fallback={<div className="min-h-[40vh] flex items-center justify-center">Loading route...</div>}>
      <Outlet />
    </Suspense>
  );
}

function RestoreLastPath() {
  const location = useLocation();
  const { user } = useAuth();
  const tracked = localStorage.getItem('wiki_route_tracking');
  if (location.pathname !== '/' || !tracked) {
    return <Navigate to="/app/dashboard" replace />;
  }

  try {
    const payload = JSON.parse(tracked) as { path?: string };
    if (payload.path?.startsWith('/')) {
      if (user && payload.path.startsWith('/login')) {
        return <Navigate to="/app/dashboard" replace />;
      }
      return <Navigate to={payload.path} replace />;
    }
  } catch {
    // ignore malformed tracking payload
  }

  return <Navigate to="/app/dashboard" replace />;
}

export default function RouterApp() {
  const router = useMemo(
    () =>
      createBrowserRouter([
        {
          path: '/',
          element: <RootLayoutView />,
          errorElement: <RouteErrorPage />,
          children: [
            {
              element: <GlobalRouteGuard />,
              children: [
                {
                  index: true,
                  element: <RestoreLastPath />,
                },
                {
                  element: <SuspenseOutlet />,
                  children: [
                    { path: 'login', element: <LoginRoute /> },
                  ],
                },
                {
                  element: <AuthGuard />,
                  children: [
                    { path: 'dashboard', element: <Navigate to="/app/dashboard" replace /> },
                    {
                      path: 'app',
                      element: <AppLayout />,
                      children: [
                        { index: true, element: <Navigate to="/app/dashboard" replace /> },
                        {
                          element: <SuspenseOutlet />,
                          children: [{ path: 'dashboard', element: <DashboardRoute /> }],
                        },
                        {
                          element: <RoutePermissionGuard requiredRole="user" />,
                          children: [
                            {
                              element: <SuspenseOutlet />,
                              children: [{ path: 'history', element: <HistoryRoute /> }],
                            },
                          ],
                        },
                        {
                          element: <SuspenseOutlet />,
                          children: [{ path: 'wiki/:taskId', element: <WikiRoute /> }],
                        },
                      ],
                    },
                  ],
                },
                { path: '*', element: <NotFoundPage /> },
              ],
            },
          ],
        },
      ]),
    []
  );

  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}
