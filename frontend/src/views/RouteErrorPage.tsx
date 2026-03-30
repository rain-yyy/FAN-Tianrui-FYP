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
      <div className="max-w-xl w-full rounded-2xl border border-rose-200 bg-rose-50 p-6 space-y-4 shadow-sm">
        <h1 className="text-xl font-semibold text-rose-900 inline-flex items-center gap-2">
          <AlertTriangle className="w-5 h-5" />
          Route error
        </h1>
        <p className="text-sm text-rose-800 break-all">{detail}</p>
        <Link
          to="/app/dashboard"
          className="inline-flex text-sm px-3 py-1.5 rounded-lg border border-stone-200 bg-white text-stone-800 hover:bg-stone-50"
        >
          Back to Dashboard
        </Link>
      </div>
    </div>
  );
}
