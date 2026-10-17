import { createRoot } from "react-dom/client";
import "./index.css";
import AISummaryView from "@/components/dashboard/AISummaryView";
const txt = `⚠️ **סיכונים**
| רמת סיכון | מספר לקחים | אחוז מהסך |

|-----------|------------|-----------|

| גבוה | 13 | 29 % |

| בינוני | 15 | 33 % |

| נמוך | 7 | 16 % |

| לא מוגדר | 10 | 22 % |

הסיכון הגבוה מרוכז בעיקר בתכנון חפירות, ניהול משאבים ובטיחות באתר (לדוגמה, חפירות תחת קווים חיים, פינוי ניידת, חשיפת כבלי חשמל).
📈 **מגמות מפתח**
- x
ציון ביטחון: 70%`;
createRoot(document.getElementById("root")!).render(<div className="p-6 bg-background max-w-5xl mx-auto"><AISummaryView text={txt} streaming={false} /></div>);
