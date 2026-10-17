import { describe, expect, it } from "vitest";
import { findSite, stationNameFrom } from "@/lib/siteName";

describe("stationNameFrom", () => {
  it("takes what comes before the first dash", () => {
    expect(stationNameFrom("גבעת אורן - התקנת שנאי 524 161/24")).toBe("גבעת אורן");
    expect(stationNameFrom("קרית הגליל-הת' 2 שנאים")).toBe("קרית הגליל");
    expect(stationNameFrom("נחל צבי – הוספת שנאי רביעי")).toBe("נחל צבי");
  });

  it("drops the kind of site", () => {
    expect(stationNameFrom('תחמ"ש רמת אלה')).toBe("רמת אלה");
    expect(stationNameFrom("תחמ״ש רמת אלה - הרחבה")).toBe("רמת אלה");
    expect(stationNameFrom("תחנת משנה עין שקד")).toBe("עין שקד");
  });

  it("keeps a plain name and survives empties", () => {
    expect(stationNameFrom("כפר דגן")).toBe("כפר דגן");
    expect(stationNameFrom("שורה ראשונה\nשורה שנייה")).toBe("שורה ראשונה");
    expect(stationNameFrom(null)).toBe("");
    expect(stationNameFrom("א".repeat(80)).length).toBe(60);
  });
});

describe("findSite", () => {
  it("finds an existing site whatever the spelling noise", () => {
    const sites = [{ label: 'תחמ"ש א' }, { label: "רמת אלה" }];
    expect(findSite(sites, "  רמת   אלה ")?.label).toBe("רמת אלה");
    expect(findSite(sites, "תחמש א")?.label).toBe('תחמ"ש א');
    expect(findSite(sites, "חדש")).toBeUndefined();
  });
});
