export type LessonDrilldownTab = "repository" | "board";

export const buildLessonDrilldownPath = (
  lessonIds: number[],
  tab: LessonDrilldownTab = "repository",
) => {
  const params = new URLSearchParams({ tab });
  const uniqueIds = [...new Set(lessonIds.filter((id) => Number.isInteger(id)))];

  if (uniqueIds.length > 0) {
    params.set("ids", uniqueIds.join(","));
  }

  return `/lessons?${params.toString()}`;
};

export const buildFeedbackDrilldownPath = (feedbackId: number | string) =>
  `/admin?tab=feedback&feedbackId=${encodeURIComponent(String(feedbackId))}`;