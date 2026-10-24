import { AbsoluteFill, Track, useVideoConfig } from "remotion";
import type { ProjectBriefData } from "./projectData";
import { Closing } from "./scenes/Closing";
import { ProjectAsk } from "./scenes/ProjectAsk";
import { ProjectHeader } from "./scenes/ProjectHeader";
import { ProjectOpenItems } from "./scenes/ProjectOpenItems";
import { ProjectTargets } from "./scenes/ProjectTargets";
import { ProjectTimeline } from "./scenes/ProjectTimeline";
import { base, colors } from "./theme";

export const ProjectBrief: React.FC<ProjectBriefData> = (d) => {
  const { fps } = useVideoConfig();
  const stamp = d.demo ? "נתוני דוגמה — לא דוח אמיתי" : d.staleNote;

  return (
    <AbsoluteFill style={base}>
      <Track name="Scenes">
        <ProjectHeader
          name="Header"
          durationInFrames={5 * fps}
          id={d.id}
          title={d.name}
          subtitle={d.subtitle}
          stage={d.stage}
          reportDate={d.reportDate}
        />
        <ProjectTargets name="Targets" from={5 * fps} durationInFrames={6 * fps} planDate={d.planDate} forecastDate={d.forecastDate} />
        <ProjectTimeline name="Timeline" from={11 * fps} durationInFrames={13 * fps} timeline={d.timeline} />
        <ProjectOpenItems name="Open items" from={24 * fps} durationInFrames={9 * fps} openItems={d.openItems} />
        <ProjectAsk
          name="Flags"
          from={33 * fps}
          durationInFrames={6 * fps}
          heading="דגלים בדיווח"
          color={colors.amber}
          items={d.flags}
          numbered={false}
        />
        <ProjectAsk
          name="Questions"
          from={39 * fps}
          durationInFrames={6 * fps}
          heading="שאלות למנה״פ"
          color={colors.blue}
          items={d.questions}
          numbered
        />
        <Closing name="Closing" from={45 * fps} durationInFrames={3 * fps} />
      </Track>
      {stamp ? (
        <div
          style={{
            position: "absolute",
            top: 40,
            left: 48,
            fontSize: 38,
            fontWeight: 700,
            color: colors.bg,
            backgroundColor: colors.amber,
            padding: "8px 24px",
            borderRadius: 10,
          }}
        >
          {stamp}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
