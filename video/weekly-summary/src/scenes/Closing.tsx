import { AbsoluteFill, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { base, colors } from "../theme";

const ClosingInner: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill
      style={{
        ...base,
        justifyContent: "center",
        alignItems: "center",
        textAlign: "center",
        opacity: interpolate(frame, [0, 0.5 * fps], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
      }}
    >
      <div style={{ fontSize: 72, color: colors.muted }}>התמונה המלאה, פרויקט אחר פרויקט</div>
      <Interactive.Div name="Destination" style={{ fontSize: 120, fontWeight: 900, marginTop: 24 }}>
        Shan-AI ← דפוסים וסיכונים
      </Interactive.Div>
    </AbsoluteFill>
  );
};

export const Closing = Interactive.create({ Component: ClosingInner, layout: "absolute-fill" });
