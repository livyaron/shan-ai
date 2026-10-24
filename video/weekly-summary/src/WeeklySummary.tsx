import { AbsoluteFill, Track, useVideoConfig } from "remotion";
import type { WeeklyData } from "./data";
import { Closing } from "./scenes/Closing";
import { Opening } from "./scenes/Opening";
import { Sectors } from "./scenes/Sectors";
import { Signals } from "./scenes/Signals";
import { Triage } from "./scenes/Triage";
import { base, colors } from "./theme";

export const WeeklySummary: React.FC<WeeklyData> = (d) => {
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill style={base}>
      <Track name="Scenes">
        <Opening
          name="Opening"
          durationInFrames={4 * fps}
          reportDate={d.reportDate}
          active={d.active}
          finished={d.finished}
        />
        <Sectors name="Sectors" from={4 * fps} durationInFrames={10 * fps} sectors={d.sectors} />
        <Triage name="Triage" from={14 * fps} durationInFrames={8 * fps} triage={d.triage} />
        <Signals
          name="Signals"
          from={22 * fps}
          durationInFrames={5 * fps}
          forecastMoved={d.forecastMoved}
          pastDue={d.pastDue}
          stuck={d.stuck}
        />
        <Closing name="Closing" from={27 * fps} durationInFrames={3 * fps} />
      </Track>
      {d.demo ? (
        <div
          style={{
            position: "absolute",
            top: 40,
            left: 48,
            fontSize: 40,
            fontWeight: 700,
            color: colors.bg,
            backgroundColor: colors.amber,
            padding: "8px 24px",
            borderRadius: 10,
          }}
        >
          נתוני דוגמה — לא דוח אמיתי
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
