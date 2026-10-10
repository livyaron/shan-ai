import { useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ArrowUpDown, ArrowUp, ArrowDown, Search, Download } from "lucide-react";
import { useUser } from "@/context/UserContext";
import { computeAllLessonQuality, type LessonQualityMetrics } from "@/lib/lessonQuality";

type SortKey =
  | "title"
  | "distributedCount"
  | "respondedCount"
  | "implementedCount"
  | "relevantNotImplementedCount"
  | "notRelevantCount"
  | "totalScore"
  | "avgScore"
  | "implementationRate";

const AdminLessonQualityTab = () => {
  const { lessons, implementations } = useUser();
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("avgScore");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const rows = useMemo(() => {
    const ids = lessons.map((l) => l.id);
    const metricsMap = computeAllLessonQuality(ids, implementations);
    const list = lessons.map((l) => ({
      lesson: l,
      m: metricsMap.get(l.id) as LessonQualityMetrics,
    }));

    const filtered = search.trim()
      ? list.filter((r) => r.lesson.title.toLowerCase().includes(search.toLowerCase()))
      : list;

    const sorted = [...filtered].sort((a, b) => {
      const dir = sortDir === "asc" ? 1 : -1;
      if (sortKey === "title") return a.lesson.title.localeCompare(b.lesson.title, "he") * dir;
      const av = (a.m as any)[sortKey];
      const bv = (b.m as any)[sortKey];
      // nulls always last
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return (av - bv) * dir;
    });

    return sorted;
  }, [lessons, implementations, search, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const SortIcon = ({ k }: { k: SortKey }) =>
    sortKey !== k ? <ArrowUpDown className="w-3 h-3 inline mr-1 opacity-50" /> :
    sortDir === "asc" ? <ArrowUp className="w-3 h-3 inline mr-1" /> :
    <ArrowDown className="w-3 h-3 inline mr-1" />;

  const fmtNum = (n: number | null, digits = 2) =>
    n === null ? "—" : n.toFixed(digits);
  const fmtPct = (n: number | null) =>
    n === null ? "—" : `${(n * 100).toFixed(0)}%`;

  const totals = useMemo(() => {
    const responded = rows.filter((r) => r.m.respondedCount > 0);
    const avgOfAvg =
      responded.length > 0
        ? responded.reduce((s, r) => s + (r.m.avgScore || 0), 0) / responded.length
        : null;
    const implRate =
      responded.length > 0
        ? responded.reduce((s, r) => s + (r.m.implementationRate || 0), 0) / responded.length
        : null;
    return { totalLessons: rows.length, withResponses: responded.length, avgOfAvg, implRate };
  }, [rows]);

  const handleExportCSV = () => {
    const headers = [
      "כותרת","הופץ","תגובות","יושם","רלוונטי לא יושם","לא רלוונטי","סך נקודות","ניקוד ממוצע","אחוז יישום",
    ];
    const escape = (v: string) => `"${v.replace(/"/g, '""')}"`;
    const lines = [headers.map(escape).join(",")];
    for (const { lesson, m } of rows) {
      lines.push([
        escape(lesson.title),
        m.distributedCount,
        m.respondedCount,
        m.implementedCount,
        m.relevantNotImplementedCount,
        m.notRelevantCount,
        m.totalScore,
        m.avgScore === null ? "" : m.avgScore.toFixed(2),
        m.implementationRate === null ? "" : `${(m.implementationRate * 100).toFixed(0)}%`,
      ].join(","));
    }
    const csv = "\uFEFF" + lines.join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const date = new Date().toISOString().slice(0, 10);
    a.href = url;
    a.download = `lesson-quality-${date}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card><CardContent className="p-4"><div className="text-xs text-muted-foreground">סה"כ לקחים</div><div className="text-2xl font-bold">{totals.totalLessons}</div></CardContent></Card>
        <Card><CardContent className="p-4"><div className="text-xs text-muted-foreground">לקחים עם תגובות</div><div className="text-2xl font-bold">{totals.withResponses}</div></CardContent></Card>
        <Card><CardContent className="p-4"><div className="text-xs text-muted-foreground">ניקוד ממוצע כללי</div><div className="text-2xl font-bold">{fmtNum(totals.avgOfAvg)}</div></CardContent></Card>
        <Card><CardContent className="p-4"><div className="text-xs text-muted-foreground">אחוז יישום ממוצע</div><div className="text-2xl font-bold">{fmtPct(totals.implRate)}</div></CardContent></Card>
      </div>

      <Card className="border-0 shadow-sm">
        <CardHeader className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <CardTitle className="text-base font-heading">איכות לקחים</CardTitle>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <div className="relative flex-1 sm:w-64">
              <Search className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="חיפוש לפי כותרת..."
                className="pr-8"
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={handleExportCSV}
              disabled={rows.length === 0}
              className="gap-1 shrink-0"
            >
              <Download className="w-4 h-4" />
              ייצוא CSV
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-right cursor-pointer" onClick={() => toggleSort("title")}><SortIcon k="title" />כותרת</TableHead>
                  <TableHead className="text-center cursor-pointer" onClick={() => toggleSort("distributedCount")}><SortIcon k="distributedCount" />הופץ</TableHead>
                  <TableHead className="text-center cursor-pointer" onClick={() => toggleSort("respondedCount")}><SortIcon k="respondedCount" />תגובות</TableHead>
                  <TableHead className="text-center cursor-pointer" onClick={() => toggleSort("implementedCount")}><SortIcon k="implementedCount" />יושם</TableHead>
                  <TableHead className="text-center cursor-pointer" onClick={() => toggleSort("relevantNotImplementedCount")}><SortIcon k="relevantNotImplementedCount" />רלוונטי לא יושם</TableHead>
                  <TableHead className="text-center cursor-pointer" onClick={() => toggleSort("notRelevantCount")}><SortIcon k="notRelevantCount" />לא רלוונטי</TableHead>
                  <TableHead className="text-center cursor-pointer" onClick={() => toggleSort("totalScore")}><SortIcon k="totalScore" />סך נקודות</TableHead>
                  <TableHead className="text-center cursor-pointer" onClick={() => toggleSort("avgScore")}><SortIcon k="avgScore" />ניקוד ממוצע</TableHead>
                  <TableHead className="text-center cursor-pointer" onClick={() => toggleSort("implementationRate")}><SortIcon k="implementationRate" />אחוז יישום</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.length === 0 && (
                  <TableRow><TableCell colSpan={9} className="text-center text-muted-foreground py-8">אין נתונים להצגה</TableCell></TableRow>
                )}
                {rows.map(({ lesson, m }) => {
                  const avgBadge =
                    m.avgScore === null ? null :
                    m.avgScore >= 1.5 ? "bg-success/10 text-success border-success/20" :
                    m.avgScore >= 1.0 ? "bg-warning/10 text-warning border-warning/20" :
                    "bg-destructive/10 text-destructive border-destructive/20";
                  return (
                    <TableRow key={lesson.id}>
                      <TableCell className="font-medium max-w-xs truncate" title={lesson.title}>{lesson.title}</TableCell>
                      <TableCell className="text-center">{m.distributedCount}</TableCell>
                      <TableCell className="text-center">{m.respondedCount}</TableCell>
                      <TableCell className="text-center">{m.implementedCount}</TableCell>
                      <TableCell className="text-center">{m.relevantNotImplementedCount}</TableCell>
                      <TableCell className="text-center">{m.notRelevantCount}</TableCell>
                      <TableCell className="text-center">{m.totalScore}</TableCell>
                      <TableCell className="text-center">
                        {m.avgScore === null ? <span className="text-muted-foreground">—</span> : (
                          <Badge variant="outline" className={avgBadge || ""}>{m.avgScore.toFixed(2)}</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-center">{fmtPct(m.implementationRate)}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <p className="text-xs text-muted-foreground mt-3">
            * ניקוד: רלוונטי ויושם = 2, רלוונטי אך לא יושם = 1, לא רלוונטי = 0. לקחים שהופצו ללא תגובה נכללים ב"הופץ" בלבד ואינם משפיעים על הממוצע.
          </p>
        </CardContent>
      </Card>
    </div>
  );
};

export default AdminLessonQualityTab;
