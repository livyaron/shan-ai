import { useEffect, useMemo, useState } from "react";
import { History, ShieldAlert } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUser, roleLabels } from "@/context/UserContext";
import SearchableSelect from "@/components/ui/searchable-select";
import {
  activityActionLabels,
  activityEntityLabels,
  filterActivityLogs,
  formatActivityDateTime,
  mapActivityLogRow,
  type ActivityLogEntry,
} from "@/lib/activity-log";

const activityBadgeStyles: Record<string, string> = {
  login: "bg-success/10 text-success border-success/20",
  logout: "bg-muted text-muted-foreground border-muted-foreground/20",
  create: "bg-primary/10 text-primary border-primary/20",
  update: "bg-warning/10 text-warning border-warning/20",
  delete: "bg-destructive/10 text-destructive border-destructive/20",
  password_change: "bg-accent/10 text-accent border-accent/20",
};

const ActivityHistoryTab = () => {
  const { currentUser } = useUser();
  const [logs, setLogs] = useState<ActivityLogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedUserId, setSelectedUserId] = useState("");
  const [selectedActionType, setSelectedActionType] = useState("");
  const [selectedEntityType, setSelectedEntityType] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  useEffect(() => {
    if (currentUser.role !== "admin") {
      setIsLoading(false);
      return;
    }

    const loadLogs = async () => {
      setIsLoading(true);
      const { data, error } = await supabase
        .from("activity_logs" as any)
        .select("*")
        .order("created_at", { ascending: false });

      if (error) {
        console.error("Failed to load activity logs:", error);
        setLogs([]);
      } else {
        setLogs((data || []).map(mapActivityLogRow));
      }

      setIsLoading(false);
    };

    void loadLogs();
  }, [currentUser.role]);

  const filteredLogs = useMemo(
    () =>
      filterActivityLogs(logs, {
        searchTerm,
        userId: selectedUserId,
        actionType: selectedActionType,
        entityType: selectedEntityType,
        startDate,
        endDate,
      }),
    [logs, searchTerm, selectedUserId, selectedActionType, selectedEntityType, startDate, endDate],
  );

  const uniqueUsers = useMemo(() => {
    const seen = new Map<string, { id: string; name: string }>();

    logs.forEach((log) => {
      if (!seen.has(log.userId)) {
        seen.set(log.userId, { id: log.userId, name: log.userName });
      }
    });

    return Array.from(seen.values());
  }, [logs]);

  if (currentUser.role !== "admin") {
    return (
      <Card className="border-0 shadow-sm">
        <CardContent className="flex min-h-40 flex-col items-center justify-center gap-3 text-center">
          <ShieldAlert className="h-8 w-8 text-muted-foreground" />
          <div>
            <p className="font-medium">אין לך הרשאה לצפות בהיסטוריית פעילות</p>
            <p className="text-sm text-muted-foreground">המסך זמין למנהלי מערכת בלבד.</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-0 shadow-sm">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base font-heading">
          <History className="h-4 w-4" />
          היסטוריית פעילות משתמשים
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <Input
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
            placeholder="חיפוש לפי משתמש, אובייקט או תיאור"
          />

          <SearchableSelect value={selectedUserId || "all"} onValueChange={(value) => setSelectedUserId(value === "all" ? "" : value)} placeholder="כל המשתמשים"
            options={[
              { value: "all", label: "כל המשתמשים" },
              ...uniqueUsers.map((user) => ({ value: String(user.id), label: user.name })),
            ]}
          />

          <SearchableSelect value={selectedActionType || "all"} onValueChange={(value) => setSelectedActionType(value === "all" ? "" : value)} placeholder="כל הפעולות"
            options={[
              { value: "all", label: "כל הפעולות" },
              ...Object.entries(activityActionLabels).map(([value, label]) => ({ value: String(value), label: label })),
            ]}
          />

          <SearchableSelect value={selectedEntityType || "all"} onValueChange={(value) => setSelectedEntityType(value === "all" ? "" : value)} placeholder="כל האובייקטים"
            options={[
              { value: "all", label: "כל האובייקטים" },
              ...Object.entries(activityEntityLabels).map(([value, label]) => ({ value: String(value), label: label })),
            ]}
          />

          <Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          <Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        </div>

        <div className="rounded-xl border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>תאריך ושעה</TableHead>
                <TableHead>שם משתמש</TableHead>
                <TableHead>תפקיד</TableHead>
                <TableHead>פעולה</TableHead>
                <TableHead>אובייקט</TableHead>
                <TableHead>תיאור</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-muted-foreground">
                    טוען היסטוריית פעילות...
                  </TableCell>
                </TableRow>
              ) : filteredLogs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-muted-foreground">
                    לא נמצאו רשומות עבור הסינון שנבחר.
                  </TableCell>
                </TableRow>
              ) : (
                filteredLogs.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="whitespace-nowrap">{formatActivityDateTime(log.createdAt)}</TableCell>
                    <TableCell>
                      <div className="font-medium">{log.userName}</div>
                      <div className="text-xs text-muted-foreground">{log.userEmail}</div>
                    </TableCell>
                    <TableCell>{roleLabels[log.userRole as keyof typeof roleLabels] || log.userRole}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={activityBadgeStyles[log.actionType] || "bg-muted text-muted-foreground border-muted-foreground/20"}>
                        {activityActionLabels[log.actionType] || log.actionType}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="font-medium">{log.objectLabel || "—"}</div>
                      <div className="text-xs text-muted-foreground">{activityEntityLabels[log.entityType] || log.entityType}</div>
                    </TableCell>
                    <TableCell className="max-w-md whitespace-normal">{log.description}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
};

export default ActivityHistoryTab;