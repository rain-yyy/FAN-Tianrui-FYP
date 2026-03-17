const routeImports = {
  dashboard: () => import(/* webpackChunkName: "route-dashboard" */ '@/router/routes/DashboardRoute'),
  history: () => import(/* webpackChunkName: "route-history" */ '@/router/routes/HistoryRoute'),
  wiki: () => import(/* webpackChunkName: "route-wiki" */ '@/router/routes/WikiRoute'),
  login: () => import(/* webpackChunkName: "route-login" */ '@/router/routes/LoginRoute'),
};

type RouteKey = keyof typeof routeImports;

const prefetched = new Set<RouteKey>();

export const prefetchRouteModule = (routeKey: RouteKey) => {
  if (prefetched.has(routeKey)) return;
  prefetched.add(routeKey);
  void routeImports[routeKey]();
};
