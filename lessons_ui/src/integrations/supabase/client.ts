// Talks to Shan-AI's /lessons/api gateway (PLAN-lessons-module.md §2.3), not to
// Supabase. supabase-js needs an absolute URL, so it is built from the page's
// own origin at runtime. Identity is the Shan-AI session cookie, sent with
// every same-origin request; the key below is a placeholder the gateway drops.
import { createClient } from '@supabase/supabase-js';
import type { Database } from './types';

export const SUPABASE_URL = `${window.location.origin}/lessons/api`;
const SUPABASE_PUBLISHABLE_KEY = "shan-ai-session";

// Import the supabase client like this:
// import { supabase } from "@/integrations/supabase/client";

export const supabase = createClient<Database>(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
  auth: {
    persistSession: false,
    autoRefreshToken: false,
    detectSessionInUrl: false,
  },
});

/**
 * Where the browser goes when the Shan-AI session is missing or expired: the
 * module's own login page, inside the PWA scope (PLAN-lessons-mobile.md M0).
 * Shan-AI's /login is outside /lessons/, and an installed app that leaves its
 * scope to sign in may never get the cookie back (iOS).
 */
export const SHAN_LOGIN_URL = "/lessons/login";
