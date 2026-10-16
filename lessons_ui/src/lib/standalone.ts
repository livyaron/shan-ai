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

/** A phone-width screen (below Tailwind's `md`) — where the wizard replaces the dialog. */
export const isPhoneWidth = (): boolean => {
  try {
    return window.matchMedia?.("(max-width: 767px)").matches ?? false;
  } catch {
    return false;
  }
};

const DESKTOP_KEY = "lessons.preferDesktop";

/** The user asked for the computer layout on this phone ("תצוגת מחשב"). */
export const prefersDesktop = (): boolean => {
  try {
    return localStorage.getItem(DESKTOP_KEY) === "1";
  } catch {
    return false;
  }
};

export const setPrefersDesktop = (on: boolean): void => {
  try {
    if (on) localStorage.setItem(DESKTOP_KEY, "1");
    else localStorage.removeItem(DESKTOP_KEY);
  } catch {
    /* private mode — the choice lasts this visit only */
  }
};

/** Phones open the phone screens unless the user chose the computer layout. */
export const opensPhoneHome = (): boolean => isPhoneWidth() && !prefersDesktop();

/** The phone screens with a bottom tab bar — the wizard keeps the whole phone. */
export const showsTabBar = (pathname: string): boolean =>
  !/^\/m\/new\/?$/.test(pathname) && !/^\/m\/lessons\/\d+\/edit\/?$/.test(pathname);
