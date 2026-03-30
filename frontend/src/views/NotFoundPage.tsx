'use client';

import { Compass } from 'lucide-react';
import { Link } from 'react-router-dom';

export default function NotFoundPage() {
  return (
    <div className="min-h-[70vh] flex items-center justify-center p-6">
      <div className="max-w-lg w-full rounded-2xl border border-stone-200 bg-white p-8 text-center space-y-4 shadow-sm">
        <div className="w-12 h-12 mx-auto rounded-xl bg-sky-50 border border-sky-200 flex items-center justify-center">
          <Compass className="w-6 h-6 text-sky-700" />
        </div>
        <h1 className="text-3xl font-bold text-stone-900">404</h1>
        <p className="text-stone-600">This page does not exist.</p>
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
