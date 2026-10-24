import { AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import type { Sector } from "../data";
import { base, colors } from "../theme";

const BAR_COLORS = [colors.blue, colors.orange, colors.green, colors.violet];

type Props = {
  readonly sectors: readonly Sector[];
};

const SectorsInner: React.FC<Props> = ({ sectors }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const max = Math.max(1, ...sectors.map((s) => s.count));

  return (
    <AbsoluteFill
      style={{
        ...base,
        padding: "150px 160px",
        opacity: interpolate(frame, [0, 10, durationInFrames - 10, durationInFrames], [0, 1, 1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
      }}
    >
      <Interactive.Div name="Title" style={{ fontSize: 120, fontWeight: 900, marginBottom: 40 }}>
        לפי מגזר
      </Interactive.Div>

      {/* One template on purpose: the rows are data, not separately edited layers. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
        {sectors.map((s, i) => {
          const start = (0.5 + i * 0.45) * fps;
          const grow = interpolate(frame, [start, start + 1.4 * fps], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: Easing.bezier(0.16, 1, 0.3, 1),
          });
          return (
            <div key={s.label} style={{ display: "flex", alignItems: "center", gap: 40 }}>
              <div style={{ width: 560, fontSize: 68, fontWeight: 700, whiteSpace: "nowrap" }}>{s.label}</div>
              <div style={{ flex: 1, height: 84, display: "flex", alignItems: "center", gap: 32 }}>
                <div
                  style={{
                    height: "100%",
                    width: `${(s.count / max) * 82 * grow}%`,
                    backgroundColor: BAR_COLORS[i % BAR_COLORS.length],
                    borderRadius: 12,
                  }}
                />
                <div style={{ fontSize: 96, fontWeight: 900, fontVariantNumeric: "tabular-nums" }}>
                  {Math.round(s.count * grow)}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 90,
          right: 160,
          left: 160,
          fontSize: 44,
          color: colors.muted,
          opacity: interpolate(frame, [3 * fps, 3.6 * fps], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
        }}
      >
        עבודה אזרחית והרכבות נספרת גם בפיקוח וגם בביצוע — הסכום אינו סך האגף
      </div>
    </AbsoluteFill>
  );
};

export const Sectors = Interactive.create({ Component: SectorsInner, layout: "absolute-fill" });
