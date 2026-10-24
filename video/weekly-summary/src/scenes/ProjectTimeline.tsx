import { AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import type { TimelineEvent, Tone } from "../projectData";
import { base, colors } from "../theme";

const TONE: Record<Tone, string> = {
  bad: colors.orange,
  good: colors.green,
  missing: colors.muted,
  plain: colors.blue,
};

type Props = {
  readonly timeline: readonly TimelineEvent[];
};

const ProjectTimelineInner: React.FC<Props> = ({ timeline }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const step = Math.min(1.4, (durationInFrames / fps - 2) / Math.max(1, timeline.length));

  return (
    <AbsoluteFill
      style={{
        ...base,
        padding: "110px 160px",
        opacity: interpolate(frame, [0, 10, durationInFrames - 10, durationInFrames], [0, 1, 1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
      }}
    >
      <Interactive.Div name="Title" style={{ fontSize: 96, fontWeight: 900, marginBottom: 36 }}>
        מה קרה, שבוע אחרי שבוע
      </Interactive.Div>
      {/* One template on purpose: the rows are data. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14, borderRight: `6px solid ${colors.panel}`, paddingRight: 48 }}>
        {timeline.map((e, i) => {
          const start = (0.4 + i * step) * fps;
          return (
            <div
              key={`${e.date}-${i}`}
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 40,
                opacity: interpolate(frame, [start, start + 0.4 * fps], [0, 1], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                }),
                translate: interpolate(frame, [start, start + 0.6 * fps], ["-40px 0px", "0px 0px"], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                  easing: Easing.bezier(0.16, 1, 0.3, 1),
                }),
              }}
            >
              <div
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 14,
                  backgroundColor: TONE[e.tone],
                  marginRight: -65,
                  flexShrink: 0,
                  alignSelf: "center",
                }}
              />
              <div style={{ width: 150, fontSize: 48, fontWeight: 700, color: TONE[e.tone], direction: "ltr", textAlign: "right", flexShrink: 0 }}>
                {e.date}
              </div>
              <div style={{ fontSize: 48, fontStyle: e.tone === "missing" ? "italic" : "normal", color: e.tone === "missing" ? colors.muted : colors.text }}>
                {e.text}
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const ProjectTimeline = Interactive.create({ Component: ProjectTimelineInner, layout: "absolute-fill" });
