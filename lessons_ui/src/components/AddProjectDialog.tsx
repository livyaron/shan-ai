import { useState, useEffect } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useUser, PROJECT_STAGES, roleLabels } from "@/context/UserContext";
import type { ProjectType, StationType } from "@/context/UserContext";
import { toast } from "@/hooks/use-toast";
import { supabase } from "@/integrations/supabase/client";
import SiteCombobox from "@/components/SiteCombobox";
import MultiStageSelect from "@/components/MultiStageSelect";
import SearchableSelect from "@/components/ui/searchable-select";

interface AddProjectDialogProps {
  trigger?: React.ReactNode;
}

const AddProjectDialog = ({ trigger }: AddProjectDialogProps) => {
  const { users, equipment, addProject, currentUser } = useUser();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState<ProjectType>("new_build");
  const [station, setStation] = useState<StationType>("closed");
  const [risk, setRisk] = useState<"high" | "medium" | "low">("low");
  const isProjectManager = currentUser.role === "project_manager";
  const projectManagers = users.filter((u) => u.role === "project_manager");
  const [manager, setManager] = useState(isProjectManager ? currentUser.id : (projectManagers[0]?.id || ""));
  const [stageIndexes, setStageIndexes] = useState<number[]>([0]);
  const [equipmentIds, setEquipmentIds] = useState<number[]>([]);
  const [site, setSite] = useState("");

  // Load site field config to check visibility/required
  const [siteFieldConfig, setSiteFieldConfig] = useState<{ isVisible: boolean; isRequired: boolean; displayName: string } | null>(null);
  useEffect(() => {
    const loadConfig = async () => {
      const { data } = await supabase
        .from("form_field_configs")
        .select("is_visible, is_required, display_name")
        .eq("form_type", "project")
        .eq("field_key", "site")
        .limit(1);
      if (data && data.length > 0) {
        setSiteFieldConfig({ isVisible: data[0].is_visible, isRequired: data[0].is_required, displayName: data[0].display_name });
      }
    };
    loadConfig();
  }, []);

  const toggleEquipment = (eqId: number) => {
    setEquipmentIds((prev) => prev.includes(eqId) ? prev.filter((id) => id !== eqId) : [...prev, eqId]);
  };

  const handleSave = () => {
    if (!name.trim() || !manager) return;
    if (stageIndexes.length === 0) {
      toast({ title: "שגיאה", description: "חובה לבחור לפחות שלב אחד", variant: "destructive" });
      return;
    }
    if (siteFieldConfig?.isRequired && !site) {
      toast({ title: "שגיאה", description: `נא לבחור ${siteFieldConfig.displayName}`, variant: "destructive" });
      return;
    }
    const derivedStageIndex = Math.max(...stageIndexes);
    addProject({
      name: name.trim(),
      stageIndex: derivedStageIndex,
      stageIndexes: [...stageIndexes].sort((a, b) => a - b),
      risk,
      equipmentIds,
      projectType: type,
      stationType: station,
      managerId: manager,
      site: site || undefined,
    });
    toast({ title: "פרויקט חדש נוצר ✓" });
    setOpen(false);
    setName("");
    setType("new_build");
    setStation("closed");
    setRisk("low");
    setStageIndexes([0]);
    setEquipmentIds([]);
    setSite("");
  };

  return (
    <>
      <div onClick={() => setOpen(true)}>
        {trigger || (
          <Button size="sm" className="bg-accent text-accent-foreground hover:bg-accent/90 gap-1.5">
            <Plus className="w-4 h-4" />
            פרויקט חדש
          </Button>
        )}
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent dir="rtl" className="max-w-md max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>פרויקט חדש</DialogTitle>
            <DialogDescription>צור פרויקט חדש במערכת</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>שם הפרויקט</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="הכנס שם פרויקט..." className="mt-1.5" />
            </div>
            <div>
              <Label>סוג פרויקט</Label>
              <SearchableSelect value={type} onValueChange={(v) => setType(v as ProjectType)} className="mt-1.5"
                options={[
                  { value: "new_build", label: "הקמה" },
                  { value: "expansion", label: "הרחבה" },
                  { value: "maintenance", label: "שו\"ש" },
                ]}
              />
            </div>
            <div>
              <Label>סוג תחנה</Label>
              <SearchableSelect value={station} onValueChange={(v) => setStation(v as StationType)} className="mt-1.5"
                options={[
                  { value: "closed", label: "תחנה סגורה" },
                  { value: "open", label: "תחנה פתוחה" },
                  { value: "switching", label: "תחנת מיתוג" },
                  { value: "combined", label: "תחנה משולבת" },
                  { value: "mobile_substation", label: "תחנת משנה ניידת" },
                ]}
              />
            </div>
            {!isProjectManager && (
            <div>
              <Label>מנהל פרויקט</Label>
              <SearchableSelect value={manager} onValueChange={setManager} className="mt-1.5"
                options={[
                  ...projectManagers.map((u) => ({ value: String(u.id), label: u.name })),
                ]}
              />
            </div>
            )}
            <div>
              <Label>רמת סיכון</Label>
              <SearchableSelect value={risk} onValueChange={(v) => setRisk(v as "high" | "medium" | "low")} className="mt-1.5"
                options={[
                  { value: "high", label: "סיכון גבוה" },
                  { value: "medium", label: "סיכון בינוני" },
                  { value: "low", label: "סיכון נמוך" },
                ]}
              />
            </div>
            {siteFieldConfig?.isVisible && (
              <div>
                <Label>{siteFieldConfig.displayName}{siteFieldConfig.isRequired && " *"}</Label>
                <SiteCombobox value={site} onValueChange={setSite} />
              </div>
            )}
            <div>
              <Label>שלבי הפרויקט *</Label>
              <div className="mt-1.5">
                <MultiStageSelect value={stageIndexes} onChange={setStageIndexes} />
              </div>
            </div>
            <div>
              <Label>ציוד / נושאים קשורים</Label>
              <div className="space-y-2 mt-2 max-h-40 overflow-y-auto">
                {equipment.map((eq) => (
                  <label key={eq.id} className="flex items-center gap-2 cursor-pointer">
                    <Checkbox
                      checked={equipmentIds.includes(eq.id)}
                      onCheckedChange={() => toggleEquipment(eq.id)}
                    />
                    <span className="text-sm">{eq.name}</span>
                    <span className="text-xs text-muted-foreground">({eq.category})</span>
                  </label>
                ))}
              </div>
            </div>
            <Button onClick={handleSave} className="w-full" disabled={!name.trim() || !manager}>
              צור פרויקט
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default AddProjectDialog;
