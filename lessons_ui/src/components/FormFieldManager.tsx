import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Pencil, Plus, Trash2, GripVertical, Settings, Eye, EyeOff, Loader2 } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { toast } from "@/hooks/use-toast";

interface FieldConfig {
  id: number;
  fieldKey: string;
  displayName: string;
  formType: string;
  isRequired: boolean;
  isVisible: boolean;
  sortOrder: number;
  section: string;
}

interface FieldOption {
  id: number;
  fieldConfigId: number;
  label: string;
  value: string;
  isActive: boolean;
  sortOrder: number;
}

interface FormFieldManagerProps {
  formType: "lesson" | "project";
  title: string;
  description: string;
}

/** Admin component for managing dynamic form field configuration */
const FormFieldManager = ({ formType, title, description }: FormFieldManagerProps) => {
  const [fields, setFields] = useState<FieldConfig[]>([]);
  const [options, setOptions] = useState<FieldOption[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [editingOptionFieldId, setEditingOptionFieldId] = useState<number | null>(null);
  const [newOptionLabel, setNewOptionLabel] = useState("");
  const [editingFieldId, setEditingFieldId] = useState<number | null>(null);
  const [editDisplayName, setEditDisplayName] = useState("");

  // Load field configs and options
  useEffect(() => {
    const load = async () => {
      setIsLoading(true);
      try {
        const [fieldsRes, optionsRes] = await Promise.all([
          supabase.from("form_field_configs").select("*").eq("form_type", formType).order("sort_order"),
          supabase.from("form_field_options").select("*").order("sort_order"),
        ]);
        if (fieldsRes.data) {
          setFields(fieldsRes.data.map((r: any) => ({
            id: r.id,
            fieldKey: r.field_key,
            displayName: r.display_name,
            formType: r.form_type,
            isRequired: r.is_required,
            isVisible: r.is_visible,
            sortOrder: r.sort_order,
            section: r.section,
          })));
        }
        if (optionsRes.data) {
          setOptions(optionsRes.data.map((r: any) => ({
            id: r.id,
            fieldConfigId: r.field_config_id,
            label: r.label,
            value: r.value,
            isActive: r.is_active,
            sortOrder: r.sort_order,
          })));
        }
      } catch (e) {
        console.error("Error loading form config:", e);
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, [formType]);

  // Toggle field visibility
  const toggleVisibility = async (fieldId: number, currentValue: boolean) => {
    await supabase.from("form_field_configs").update({ is_visible: !currentValue }).eq("id", fieldId);
    setFields((prev) => prev.map((f) => f.id === fieldId ? { ...f, isVisible: !currentValue } : f));
  };

  // Toggle field required
  const toggleRequired = async (fieldId: number, currentValue: boolean) => {
    await supabase.from("form_field_configs").update({ is_required: !currentValue }).eq("id", fieldId);
    setFields((prev) => prev.map((f) => f.id === fieldId ? { ...f, isRequired: !currentValue } : f));
  };

  // Update display name
  const saveDisplayName = async () => {
    if (!editingFieldId || !editDisplayName.trim()) return;
    await supabase.from("form_field_configs").update({ display_name: editDisplayName.trim() }).eq("id", editingFieldId);
    setFields((prev) => prev.map((f) => f.id === editingFieldId ? { ...f, displayName: editDisplayName.trim() } : f));
    setEditingFieldId(null);
    toast({ title: "שם התצוגה עודכן ✓" });
  };

  // Move field up/down
  const moveField = async (fieldId: number, direction: "up" | "down") => {
    const sorted = [...fields].sort((a, b) => a.sortOrder - b.sortOrder);
    const index = sorted.findIndex((f) => f.id === fieldId);
    if ((direction === "up" && index === 0) || (direction === "down" && index === sorted.length - 1)) return;

    const swapIndex = direction === "up" ? index - 1 : index + 1;
    const tempOrder = sorted[index].sortOrder;
    sorted[index].sortOrder = sorted[swapIndex].sortOrder;
    sorted[swapIndex].sortOrder = tempOrder;

    await Promise.all([
      supabase.from("form_field_configs").update({ sort_order: sorted[index].sortOrder }).eq("id", sorted[index].id),
      supabase.from("form_field_configs").update({ sort_order: sorted[swapIndex].sortOrder }).eq("id", sorted[swapIndex].id),
    ]);
    setFields([...sorted]);
  };

  // Add option to a list field
  const addOption = async () => {
    if (!editingOptionFieldId || !newOptionLabel.trim()) return;
    const fieldOptions = options.filter((o) => o.fieldConfigId === editingOptionFieldId);
    const maxOrder = fieldOptions.length > 0 ? Math.max(...fieldOptions.map((o) => o.sortOrder)) : 0;

    const { data } = await supabase.from("form_field_options").insert({
      field_config_id: editingOptionFieldId,
      label: newOptionLabel.trim(),
      value: newOptionLabel.trim(),
      sort_order: maxOrder + 1,
      is_active: true,
    }).select().single();

    if (data) {
      setOptions((prev) => [...prev, {
        id: data.id,
        fieldConfigId: data.field_config_id,
        label: data.label,
        value: data.value,
        isActive: data.is_active,
        sortOrder: data.sort_order,
      }]);
    }
    setNewOptionLabel("");
    toast({ title: "ערך נוסף ✓" });
  };

  // Toggle option active/inactive
  const toggleOptionActive = async (optionId: number, currentValue: boolean) => {
    await supabase.from("form_field_options").update({ is_active: !currentValue }).eq("id", optionId);
    setOptions((prev) => prev.map((o) => o.id === optionId ? { ...o, isActive: !currentValue } : o));
  };

  // Delete option
  const deleteOption = async (optionId: number) => {
    await supabase.from("form_field_options").delete().eq("id", optionId);
    setOptions((prev) => prev.filter((o) => o.id !== optionId));
    toast({ title: "ערך הוסר ✓" });
  };

  // Group fields by section
  const sections = [...new Set(fields.map((f) => f.section))];

  if (isLoading) {
    return (
      <Card className="border-0 shadow-sm">
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    );
  }

  if (fields.length === 0) {
    return (
      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base font-heading flex items-center gap-2">
            <Settings className="w-5 h-5 text-primary" />
            {title}
          </CardTitle>
          <p className="text-sm text-muted-foreground">{description}</p>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground text-center py-8">
            אין הגדרות שדות. יש להוסיף שדות דרך מסד הנתונים.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-0 shadow-sm">
      <CardHeader>
        <CardTitle className="text-base font-heading flex items-center gap-2">
          <Settings className="w-5 h-5 text-primary" />
          {title}
        </CardTitle>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardHeader>
      <CardContent className="space-y-6">
        {sections.map((section) => (
          <div key={section || "general"}>
            {section && (
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3 border-b border-border pb-1">
                {section}
              </h4>
            )}
            <div className="space-y-2">
              {fields
                .filter((f) => f.section === section)
                .sort((a, b) => a.sortOrder - b.sortOrder)
                .map((field) => {
                  const fieldOptions = options.filter((o) => o.fieldConfigId === field.id);
                  const hasOptions = fieldOptions.length > 0;

                  return (
                    <div key={field.id} className="p-3 rounded-xl bg-muted/50 space-y-2">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2 min-w-0">
                          <GripVertical className="w-4 h-4 text-muted-foreground/50 shrink-0" />
                          <div className="min-w-0">
                            <p className="text-sm font-medium truncate">{field.displayName}</p>
                            <p className="text-[10px] text-muted-foreground font-mono">{field.fieldKey}</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-3 shrink-0">
                          {/* Visible toggle */}
                          <div className="flex items-center gap-1.5">
                            {field.isVisible ? (
                              <Eye className="w-3.5 h-3.5 text-success" />
                            ) : (
                              <EyeOff className="w-3.5 h-3.5 text-muted-foreground" />
                            )}
                            <Switch
                              checked={field.isVisible}
                              onCheckedChange={() => toggleVisibility(field.id, field.isVisible)}
                            />
                          </div>
                          {/* Required badge */}
                          <Badge
                            variant="outline"
                            className={`cursor-pointer text-[10px] ${field.isRequired ? "bg-destructive/10 text-destructive border-destructive/20" : "bg-muted text-muted-foreground"}`}
                            onClick={() => toggleRequired(field.id, field.isRequired)}
                          >
                            {field.isRequired ? "חובה" : "אופציונלי"}
                          </Badge>
                          {/* Edit name */}
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() => { setEditingFieldId(field.id); setEditDisplayName(field.displayName); }}
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </Button>
                          {/* Move buttons */}
                          <div className="flex flex-col gap-0.5">
                            <Button variant="ghost" size="icon" className="h-5 w-5" onClick={() => moveField(field.id, "up")}>
                              <span className="text-[10px]">▲</span>
                            </Button>
                            <Button variant="ghost" size="icon" className="h-5 w-5" onClick={() => moveField(field.id, "down")}>
                              <span className="text-[10px]">▼</span>
                            </Button>
                          </div>
                        </div>
                      </div>

                      {/* Options management for list fields */}
                      {hasOptions && (
                        <div className="mr-6 space-y-1.5">
                          <div className="flex items-center justify-between">
                            <span className="text-xs text-muted-foreground">ערכי רשימה ({fieldOptions.filter((o) => o.isActive).length} פעילים)</span>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 text-xs gap-1"
                              onClick={() => setEditingOptionFieldId(field.id)}
                            >
                              <Plus className="w-3 h-3" />
                              הוסף ערך
                            </Button>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {fieldOptions
                              .sort((a, b) => a.sortOrder - b.sortOrder)
                              .map((opt) => (
                                <Badge
                                  key={opt.id}
                                  variant="outline"
                                  className={`text-[10px] cursor-pointer ${opt.isActive ? "" : "opacity-40 line-through"}`}
                                  onClick={() => toggleOptionActive(opt.id, opt.isActive)}
                                >
                                  {opt.label}
                                </Badge>
                              ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
            </div>
          </div>
        ))}
      </CardContent>

      {/* Edit Display Name Dialog */}
      <Dialog open={!!editingFieldId} onOpenChange={(open) => !open && setEditingFieldId(null)}>
        <DialogContent dir="rtl" className="max-w-sm">
          <DialogHeader>
            <DialogTitle>עריכת שם תצוגה</DialogTitle>
            <DialogDescription>שנה את השם שיוצג למשתמש בטופס</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div>
              <Label>שם תצוגה</Label>
              <Input value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} className="mt-1.5" />
            </div>
            <Button onClick={saveDisplayName} className="w-full">שמור</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Add Option Dialog */}
      <Dialog open={!!editingOptionFieldId} onOpenChange={(open) => {
        if (!open) {
          setEditingOptionFieldId(null);
          setNewOptionLabel("");
        }
      }}>
        <DialogContent dir="rtl" className="max-w-md">
          <DialogHeader>
            <DialogTitle>ניהול ערכי רשימה</DialogTitle>
            <DialogDescription>
              {editingOptionFieldId && fields.find((f) => f.id === editingOptionFieldId)?.displayName}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div className="flex gap-2">
              <Input
                value={newOptionLabel}
                onChange={(e) => setNewOptionLabel(e.target.value)}
                placeholder="ערך חדש"
                onKeyDown={(e) => e.key === "Enter" && addOption()}
              />
              <Button onClick={addOption} size="sm" className="gap-1 whitespace-nowrap">
                <Plus className="w-3.5 h-3.5" />
                הוסף
              </Button>
            </div>
            <div className="space-y-1.5 max-h-60 overflow-y-auto">
              {options
                .filter((o) => o.fieldConfigId === editingOptionFieldId)
                .sort((a, b) => a.sortOrder - b.sortOrder)
                .map((opt) => (
                  <div key={opt.id} className="flex items-center justify-between p-2 rounded-lg bg-muted/50">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={opt.isActive}
                        onCheckedChange={() => toggleOptionActive(opt.id, opt.isActive)}
                      />
                      <span className={`text-sm ${!opt.isActive ? "line-through text-muted-foreground" : ""}`}>
                        {opt.label}
                      </span>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-destructive"
                      onClick={() => deleteOption(opt.id)}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                ))}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  );
};

export default FormFieldManager;
