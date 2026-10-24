import { AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { base, colors } from "../theme";

type Props = {
  readonly reportDate: string;
  readonly active: number;
  readonly finished: number;
};

const OpeningInner: React.FC<Props> = ({ reportDate, active, finished }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  return (
    <AbsoluteFill
      style={{
        ...base,
        padding: "180px 160px",
        justifyContent: "space-between",
        opacity: interpolate(frame, [durationInFrames - 10, durationInFrames], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
      }}
    >
      <div>
        <Interactive.Div
          name="Kicker"
          style={{
            fontSize: 56,
            color: colors.blue,
            fontWeight: 700,
            opacity: interpolate(frame, [0, 0.6 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
          }}
        >
          אגף פרויקטים · תחנות משנה
        </Interactive.Div>
        <Interactive.Div
          name="Title"
          style={{
            fontSize: 180,
            fontWeight: 900,
            lineHeight: 1.05,
            opacity: interpolate(frame, [0.2 * fps, 0.9 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
            translate: interpolate(frame, [0.2 * fps, 0.9 * fps], ["0px 40px", "0px 0px"], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
          }}
        >
          סיכום שבועי
        </Interactive.Div>
        <div style={{ fontSize: 64, color: colors.muted }}>נכון לדוח {reportDate}</div>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: 48 }}>
        <Interactive.Div
          name="Active count"
          style={{ fontSize: 300, fontWeight: 900, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}
        >
          {Math.round(
            interpolate(frame, [0.8 * fps, 2.6 * fps], [0, active], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
          )}
        </Interactive.Div>
        <div>
          <div style={{ fontSize: 88, fontWeight: 700 }}>פרויקטים פעילים</div>
          <div
            style={{
              fontSize: 56,
              color: colors.muted,
              opacity: interpolate(frame, [2.4 * fps, 3 * fps], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
            }}
          >
            ועוד {finished} שהסתיימו
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

export const Opening = Interactive.create({ Component: OpeningInner, layout: "absolute-fill" });
