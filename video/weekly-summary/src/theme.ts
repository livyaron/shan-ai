import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

// Heebo — the dashboard's --num font (Karantina's 6 and 8 read alike).
// Bundled (OFL, public/fonts/OFL.txt) so a render never depends on the network.
export const fontFamily = "Heebo";
loadFont({ family: fontFamily, url: staticFile("fonts/Heebo-Variable.ttf"), weight: "100 900" });

// Same dark ground + validated slots 1–3 as the project page charts.
export const colors = {
  bg: "#0f1826",
  panel: "#16233a",
  text: "#eef3fb",
  muted: "#8fa3c0",
  blue: "#3987e5",
  orange: "#d95926",
  green: "#199e70",
  violet: "#9b6bd6",
  red: "#e04848",
  amber: "#e0a526",
} as const;

export const base = {
  fontFamily,
  color: colors.text,
  direction: "rtl",
  backgroundColor: colors.bg,
} as const;
