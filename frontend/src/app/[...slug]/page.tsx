'use client';

import dynamic from 'next/dynamic';

const RouterApp = dynamic(() => import('../../router/RouterApp'), { ssr: false });

export default function CatchAllPage() {
  return <RouterApp />;
}
