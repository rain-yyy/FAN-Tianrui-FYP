'use client';

import { Compass } from 'lucide-react';
import { Link } from 'react-router-dom';

export default function NotFoundPage() {
  return (
    <div className="min-h-[70vh] flex items-center justify-center p-6">
      <div className="max-w-lg w-full rounded-2xl border border-white/15 bg-black/25 backdrop-blur-xl p-8 text-center space-y-4">
        <div className="w-12 h-12 mx-auto rounded-xl bg-fuchsia-500/15 border border-fuchsia-300/25 flex items-center justify-center">
          <Compass className="w-6 h-6 text-fuchsia-200" />
        </div>
        <h1 className="text-3xl font-bold text-white">404</h1>
        <p className="text-zinc-300">This page does not exist.</p>
        <Link to="/app/dashboard" className="inline-flex text-sm px-3 py-1.5 rounded-lg border border-white/20 hover:bg-white/10">
          Back to Dashboard
        </Link>
      </div>
    </div>
  );
}
