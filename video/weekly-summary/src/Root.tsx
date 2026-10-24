import { Composition, Folder } from "remotion";
import { Closing } from "./scenes/Closing";
import { Opening } from "./scenes/Opening";
import { Sectors } from "./scenes/Sectors";
import { Signals } from "./scenes/Signals";
import { Triage } from "./scenes/Triage";
import { ProjectBrief } from "./ProjectBrief";
import { WeeklySummary } from "./WeeklySummary";

// Every number below is DEMO data (demo: true stamps the video).
// A real render passes --props=<file>.json built from the live engine.
export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="WeeklySummary"
        component={WeeklySummary}
        durationInFrames={900}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          demo: true,
          reportDate: "01/01/2026",
          active: 200,
          finished: 40,
          sectors: [
            { label: "תכנון", count: 60 },
            { label: "פיקוח", count: 50 },
            { label: "ביצוע", count: 70 },
            { label: "ניהול פרויקטים", count: 20 },
          ],
          triage: { escalate: 6, thisWeek: 18, followUp: 54, fyi: 122 },
          forecastMoved: 15,
          pastDue: 9,
          stuck: 23,
        }}
      />
      <Composition
        id="ProjectBrief"
        component={ProjectBrief}
        durationInFrames={1440}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          demo: true,
          reportDate: "01/01/2026",
          staleNote: "",
          id: "WBX-000",
          name: "תחנה לדוגמה",
          subtitle: "הקמת תחמ״ש",
          stage: "הרכבה חשמלית",
          planDate: "2026-12-31",
          forecastDate: "2027-02-28",
          timeline: [
            { date: "01/01", text: "דיווח לדוגמה", tone: "plain" },
            { date: "08/01", text: "תקלה בציוד", tone: "bad" },
            { date: "15/01", text: "אין דיווח", tone: "missing" },
            { date: "22/01", text: "הבדיקות הושלמו", tone: "good" },
          ],
          openItems: [{ title: "ציוד חסר", since: "מאז 08/01", detail: "פרט לדוגמה" }],
          flags: ["דגל לדוגמה"],
          questions: ["שאלה לדוגמה?"],
        }}
      />
      <Folder name="Scenes">
        <Composition
          id="Opening"
          component={Opening}
          durationInFrames={120}
          fps={30}
          width={1920}
          height={1080}
          defaultProps={{ reportDate: "01/01/2026", active: 200, finished: 40 }}
        />
        <Composition
          id="Sectors"
          component={Sectors}
          durationInFrames={300}
          fps={30}
          width={1920}
          height={1080}
          defaultProps={{
            sectors: [
              { label: "תכנון", count: 60 },
              { label: "פיקוח", count: 50 },
              { label: "ביצוע", count: 70 },
              { label: "ניהול פרויקטים", count: 20 },
            ],
          }}
        />
        <Composition
          id="Triage"
          component={Triage}
          durationInFrames={240}
          fps={30}
          width={1920}
          height={1080}
          defaultProps={{ triage: { escalate: 6, thisWeek: 18, followUp: 54, fyi: 122 } }}
        />
        <Composition
          id="Signals"
          component={Signals}
          durationInFrames={150}
          fps={30}
          width={1920}
          height={1080}
          defaultProps={{ forecastMoved: 15, pastDue: 9, stuck: 23 }}
        />
        <Composition id="Closing" component={Closing} durationInFrames={90} fps={30} width={1920} height={1080} />
      </Folder>
    </>
  );
};
