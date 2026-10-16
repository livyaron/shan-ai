/**
 * The phone app (PLAN-lessons-mobile.md M0): is the module running as an
 * installed app, and is this the in-app login page?
 */

/** True when opened from the home-screen icon (Android/desktop PWA or iOS). */
export const isStandalone = (): boolean => {
  try {
    if (window.matchMedia?.("(display-mode: standalone)").matches) return true;
  } catch {
    /* old browser */
  }
  return (window.navigator as Navigator & { standalone?: boolean }).standalone === true;
};

/** `/lessons/login` with or without a trailing slash. */
export const isLoginPath = (pathname: string): boolean => /^\/lessons\/login\/?$/.test(pathname);
