import { AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { gapDays, ilDate } from "../projectFormat";
import { base, colors } from "../theme";

type Props = {
  readonly planDate: string;
  readonly forecastDate: string;
};

const ProjectTargetsInner: React.FC<Props> = ({ planDate, forecastDate }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const gap = gapDays(planDate, forecastDate);
  const late = gap > 0;
  const n = Math.abs(gap);
  const span = n === 1 ? "יום אחד" : `${n} ימים`;
  const gapText = gap === 0 ? "בדיוק ביעד התכנית" : `${span} ${late ? "אחרי" : "לפני"} התכנית`;

  const cards = [
    { key: "plan", label: "יעד תכנית פיתוח", value: ilDate(planDate), color: colors.blue },
    { key: "forecast", label: "יעד חשמול מסתמן", value: ilDate(forecastDate), color: late ? colors.orange : colors.green },
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
      <Interactive.Div name="Title" style={{ fontSize: 120, fontWeight: 900, marginBottom: 80 }}>
        יעדים
      </Interactive.Div>
      <div style={{ display: "flex", gap: 48 }}>
        {cards.map((c, i) => (
          <div
            key={c.key}
            style={{
              flex: 1,
              backgroundColor: colors.panel,
              borderRadius: 24,
              borderTop: `16px solid ${c.color}`,
              padding: "48px 56px",
              opacity: interpolate(frame, [(0.3 + i * 0.4) * fps, (0.8 + i * 0.4) * fps], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
            }}
          >
            <div style={{ fontSize: 60, color: colors.muted }}>{c.label}</div>
            <div style={{ fontSize: 116, fontWeight: 900, fontVariantNumeric: "tabular-nums", direction: "ltr", textAlign: "right" }}>
              {c.value}
            </div>
          </div>
        ))}
      </div>
      <div
        style={{
          marginTop: 80,
          fontSize: 88,
          fontWeight: 700,
          color: late ? colors.orange : colors.green,
          opacity: interpolate(frame, [1.6 * fps, 2.2 * fps], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
          translate: interpolate(frame, [1.6 * fps, 2.2 * fps], ["0px 30px", "0px 0px"], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: Easing.bezier(0.16, 1, 0.3, 1),
          }),
        }}
      >
        התחזית: {gapText}
      </div>
    </AbsoluteFill>
  );
};

export const ProjectTargets = Interactive.create({ Component: ProjectTargetsInner, layout: "absolute-fill" });
