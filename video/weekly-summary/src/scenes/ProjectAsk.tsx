import { AbsoluteFill, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { base } from "../theme";

type Props = {
  readonly heading: string;
  readonly color: string;
  readonly items: readonly string[];
  readonly numbered: boolean;
};

const ProjectAskInner: React.FC<Props> = ({ heading, color, items, numbered }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

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
      <Interactive.Div name="Heading" style={{ fontSize: 110, fontWeight: 900, color, marginBottom: 56 }}>
        {heading}
      </Interactive.Div>
      {items.map((it, i) => (
        <div
          key={it}
          style={{
            fontSize: 60,
            fontWeight: numbered ? 700 : 400,
            marginBottom: 28,
            opacity: interpolate(frame, [(0.4 + i * 0.5) * fps, (0.8 + i * 0.5) * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
          }}
        >
          {numbered ? `${i + 1}. ` : "• "}
          {it}
        </div>
      ))}
    </AbsoluteFill>
  );
};

export const ProjectAsk = Interactive.create({ Component: ProjectAskInner, layout: "absolute-fill" });
