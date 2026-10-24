import { AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import type { OpenItem } from "../projectData";
import { base, colors } from "../theme";

type Props = {
  readonly openItems: readonly OpenItem[];
};

const ProjectOpenItemsInner: React.FC<Props> = ({ openItems }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  return (
    <AbsoluteFill
      style={{
        ...base,
        padding: "120px 160px",
        opacity: interpolate(frame, [0, 10, durationInFrames - 10, durationInFrames], [0, 1, 1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
      }}
    >
      <Interactive.Div name="Title" style={{ fontSize: 100, fontWeight: 900, marginBottom: 40 }}>
        חסמים פתוחים
      </Interactive.Div>
      <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
        {openItems.map((it, i) => {
          const start = (0.4 + i * 0.8) * fps;
          return (
            <div
              key={it.title}
              style={{
                backgroundColor: colors.panel,
                borderRadius: 20,
                borderRight: `14px solid ${colors.orange}`,
                padding: "24px 44px",
                opacity: interpolate(frame, [start, start + 0.5 * fps], [0, 1], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                }),
                translate: interpolate(frame, [start, start + 0.7 * fps], ["0px 40px", "0px 0px"], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                  easing: Easing.bezier(0.16, 1, 0.3, 1),
                }),
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <div style={{ fontSize: 58, fontWeight: 900 }}>{it.title}</div>
                <div style={{ fontSize: 46, color: colors.orange, fontWeight: 700 }}>{it.since}</div>
              </div>
              <div style={{ fontSize: 44, color: colors.muted, marginTop: 6 }}>{it.detail}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const ProjectOpenItems = Interactive.create({ Component: ProjectOpenItemsInner, layout: "absolute-fill" });
