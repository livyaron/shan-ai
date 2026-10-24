import { AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import type { WeeklyData } from "../data";
import { base, colors } from "../theme";

type Props = {
  readonly triage: WeeklyData["triage"];
};

const TriageInner: React.FC<Props> = ({ triage }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Owners and rules: insight_triage.py — the level is a rule, never a model.
  const cards = [
    { key: "escalate", n: triage.escalate, color: colors.red, label: "הסלמה", owner: "סמנכ״ל" },
    { key: "thisWeek", n: triage.thisWeek, color: colors.orange, label: "השבוע", owner: "מנהל אגף" },
    { key: "followUp", n: triage.followUp, color: colors.amber, label: "מעקב", owner: "מגזר / מנה״פ" },
    { key: "fyi", n: triage.fyi, color: colors.green, label: "לידיעה", owner: "—" },
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
        דרגות טיפול
      </Interactive.Div>

      <div style={{ display: "flex", gap: 40, flex: 1, maxHeight: 560 }}>
        {cards.map((c, i) => {
          const start = (0.4 + i * 0.35) * fps;
          return (
            <div
              key={c.key}
              style={{
                flex: 1,
                backgroundColor: colors.panel,
                borderRadius: 24,
                borderTop: `16px solid ${c.color}`,
                padding: "48px 44px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                opacity: interpolate(frame, [start, start + 0.5 * fps], [0, 1], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                }),
                translate: interpolate(frame, [start, start + 0.7 * fps], ["0px 60px", "0px 0px"], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                  easing: Easing.bezier(0.16, 1, 0.3, 1),
                }),
              }}
            >
              <div style={{ fontSize: 76, fontWeight: 700, color: c.color }}>{c.label}</div>
              <div style={{ fontSize: 200, fontWeight: 900, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>
                {c.n}
              </div>
              <div style={{ fontSize: 48, color: colors.muted }}>{c.owner}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const Triage = Interactive.create({ Component: TriageInner, layout: "absolute-fill" });
