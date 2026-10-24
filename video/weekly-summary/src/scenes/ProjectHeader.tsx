import { AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { base, colors } from "../theme";

type Props = {
  readonly id: string;
  readonly title: string;
  readonly subtitle: string;
  readonly stage: string;
  readonly reportDate: string;
};

const ProjectHeaderInner: React.FC<Props> = ({ id, title, subtitle, stage, reportDate }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  return (
    <AbsoluteFill
      style={{
        ...base,
        padding: "200px 160px",
        justifyContent: "center",
        opacity: interpolate(frame, [durationInFrames - 10, durationInFrames], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
      }}
    >
      <div style={{ fontSize: 56, color: colors.blue, fontWeight: 700, direction: "ltr", textAlign: "right" }}>{id}</div>
      <Interactive.Div
        name="Project name"
        style={{
          fontSize: 190,
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
        {title}
      </Interactive.Div>
      <div style={{ fontSize: 64, color: colors.muted, marginTop: 12 }}>{subtitle}</div>
      <div
        style={{
          display: "flex",
          gap: 32,
          marginTop: 80,
          alignItems: "center",
          opacity: interpolate(frame, [1.2 * fps, 1.8 * fps], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
        }}
      >
        <div style={{ fontSize: 56, fontWeight: 700, backgroundColor: colors.panel, padding: "16px 36px", borderRadius: 16 }}>
          שלב: {stage}
        </div>
        <div style={{ fontSize: 52, color: colors.muted }}>נכון לדוח {reportDate}</div>
      </div>
    </AbsoluteFill>
  );
};

export const ProjectHeader = Interactive.create({ Component: ProjectHeaderInner, layout: "absolute-fill" });
