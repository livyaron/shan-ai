import { AbsoluteFill, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { base, colors } from "../theme";

type Props = {
  readonly forecastMoved: number;
  readonly pastDue: number;
  readonly stuck: number;
};

const SignalsInner: React.FC<Props> = ({ forecastMoved, pastDue, stuck }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const items = [
    { key: "moved", n: forecastMoved, label: "יעד חשמול זז השבוע" },
    { key: "pastDue", n: pastDue, label: "עברו את היעד" },
    { key: "stuck", n: stuck, label: "תקועים 12+ שבועות בשלב" },
  ];

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
      <Interactive.Div name="Title" style={{ fontSize: 120, fontWeight: 900, marginBottom: 90 }}>
        מה לבדוק השבוע
      </Interactive.Div>
      <div style={{ display: "flex", flexDirection: "column", gap: 56 }}>
        {items.map((it, i) => (
          <div
            key={it.key}
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 56,
              opacity: interpolate(frame, [(0.3 + i * 0.4) * fps, (0.8 + i * 0.4) * fps], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
            }}
          >
            <div
              style={{
                width: 260,
                fontSize: 150,
                fontWeight: 900,
                lineHeight: 1,
                color: colors.orange,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {it.n}
            </div>
            <div style={{ fontSize: 80, fontWeight: 700 }}>{it.label}</div>
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

export const Signals = Interactive.create({ Component: SignalsInner, layout: "absolute-fill" });
