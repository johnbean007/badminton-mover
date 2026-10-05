import "server-only";

import { createClient } from "@supabase/supabase-js";

// Bypasses row-level security. Only call after checking the caller is the admin (see requireAdmin).
export function createAdminClient() {
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SECRET_KEY!, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}
