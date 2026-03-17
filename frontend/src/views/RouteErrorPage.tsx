'use client';

import { AlertTriangle } from 'lucide-react';
import { isRouteErrorResponse, Link, useRouteError } from 'react-router-dom';

export default function RouteErrorPage() {
  const error = useRouteError();
  const detail = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : 'Unknown route error';

  return (
    <div className="min-h-[70vh] flex items-center justify-center p-6">
      <div className="max-w-xl w-full rounded-2xl border border-rose-300/30 bg-rose-500/10 p-6 space-y-4">
        <h1 className="text-xl font-semibold text-rose-100 inline-flex items-center gap-2">
          <AlertTriangle className="w-5 h-5" />
          路由级错误边界
        </h1>
        <p className="text-sm text-rose-200/90 break-all">{detail}</p>
        <Link to="/app/dashboard" className="inline-flex text-sm px-3 py-1.5 rounded-lg border border-white/20 hover:bg-white/10">
          返回 Dashboard
        </Link>
      </div>
    </div>
  );
}
