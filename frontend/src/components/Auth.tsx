'use client';

import { useState } from 'react';
import { supabase } from '@/lib/supabase';
import { Mail, Phone, Lock, Loader2, LogIn, UserPlus } from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export default function Auth() {
  const [loading, setLoading] = useState(false);
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [isSignUp, setIsSignUp] = useState(false);
  const [authMethod, setAuthMethod] = useState<'email' | 'phone'>('email');

  const handleAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      if (isSignUp) {
        const { error } = authMethod === 'email' 
          ? await supabase.auth.signUp({ email, password })
          : await supabase.auth.signUp({ phone, password });
        if (error) throw error;
        alert('Check your email/phone for the confirmation link!');
      } else {
        const { error } = authMethod === 'email'
          ? await supabase.auth.signInWithPassword({ email, password })
          : await supabase.auth.signInWithPassword({ phone, password });
        if (error) throw error;
      }
    } catch (error: any) {
      alert(error.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="w-full max-w-md mx-auto p-6 space-y-8 bg-secondary/30 backdrop-blur-xl border border-white/10 rounded-2xl">
      <div className="text-center space-y-2">
        <h2 className="text-2xl font-bold text-white">
          {isSignUp ? 'Create an Account' : 'Welcome Back'}
        </h2>
        <p className="text-muted-foreground text-sm">
          {isSignUp ? 'Sign up to start generating wikis' : 'Login to view your history'}
        </p>
      </div>

      <div className="flex p-1 bg-black/20 rounded-lg">
        <button
          onClick={() => setAuthMethod('email')}
          className={cn(
            "flex-1 py-2 text-sm font-medium rounded-md transition-all",
            authMethod === 'email' ? "bg-white text-black" : "text-muted-foreground hover:text-white"
          )}
        >
          Email
        </button>
        <button
          onClick={() => setAuthMethod('phone')}
          className={cn(
            "flex-1 py-2 text-sm font-medium rounded-md transition-all",
            authMethod === 'phone' ? "bg-white text-black" : "text-muted-foreground hover:text-white"
          )}
        >
          Phone
        </button>
      </div>

      <form onSubmit={handleAuth} className="space-y-4">
        {authMethod === 'email' ? (
          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground ml-1">Email Address</label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input
                type="email"
                required
                className="w-full bg-black/20 border border-white/10 rounded-xl py-3 pl-10 pr-4 text-white focus:outline-none focus:ring-1 focus:ring-blue-500/50"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground ml-1">Phone Number</label>
            <div className="relative">
              <Phone className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input
                type="tel"
                required
                className="w-full bg-black/20 border border-white/10 rounded-xl py-3 pl-10 pr-4 text-white focus:outline-none focus:ring-1 focus:ring-blue-500/50"
                placeholder="+1234567890"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
          </div>
        )}

        <div className="space-y-2">
          <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground ml-1">Password</label>
          <div className="relative">
            <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              type="password"
              required
              className="w-full bg-black/20 border border-white/10 rounded-xl py-3 pl-10 pr-4 text-white focus:outline-none focus:ring-1 focus:ring-blue-500/50"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full bg-blue-600 hover:bg-blue-500 text-white font-medium py-3 rounded-xl transition-colors flex items-center justify-center gap-2"
        >
          {loading ? (
            <Loader2 className="w-5 h-5 animate-spin" />
          ) : isSignUp ? (
            <>
              <UserPlus className="w-5 h-5" />
              Sign Up
            </>
          ) : (
            <>
              <LogIn className="w-5 h-5" />
              Login
            </>
          )}
        </button>
      </form>

      <div className="text-center">
        <button
          onClick={() => setIsSignUp(!isSignUp)}
          className="text-sm text-muted-foreground hover:text-white transition-colors"
        >
          {isSignUp ? 'Already have an account? Login' : "Don't have an account? Sign Up"}
        </button>
      </div>
    </div>
  );
}
