export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.17"
  }
  public: {
    Tables: {
      activity_logs: {
        Row: {
          action_type: string
          created_at: string
          description: string
          entity_id: string | null
          entity_type: string
          id: number
          object_label: string
          user_email: string
          user_id: string
          user_name: string
          user_role: string
        }
        Insert: {
          action_type: string
          created_at?: string
          description: string
          entity_id?: string | null
          entity_type: string
          id?: number
          object_label?: string
          user_email: string
          user_id: string
          user_name: string
          user_role: string
        }
        Update: {
          action_type?: string
          created_at?: string
          description?: string
          entity_id?: string | null
          entity_type?: string
          id?: number
          object_label?: string
          user_email?: string
          user_id?: string
          user_name?: string
          user_role?: string
        }
        Relationships: []
      }
      ai_candidate_insights: {
        Row: {
          admin_notes: string | null
          confidence: number
          context_type: string
          context_value: string | null
          created_at: string
          frequency: number
          id: number
          insight_text: string
          reviewed_at: string | null
          reviewed_by: string | null
          source_feedback_ids: Json | null
          status: string
        }
        Insert: {
          admin_notes?: string | null
          confidence?: number
          context_type?: string
          context_value?: string | null
          created_at?: string
          frequency?: number
          id?: number
          insight_text: string
          reviewed_at?: string | null
          reviewed_by?: string | null
          source_feedback_ids?: Json | null
          status?: string
        }
        Update: {
          admin_notes?: string | null
          confidence?: number
          context_type?: string
          context_value?: string | null
          created_at?: string
          frequency?: number
          id?: number
          insight_text?: string
          reviewed_at?: string | null
          reviewed_by?: string | null
          source_feedback_ids?: Json | null
          status?: string
        }
        Relationships: []
      }
      ai_feedback: {
        Row: {
          classification: string | null
          context_id: string | null
          context_type: string
          created_at: string
          extracted_mistakes: Json | null
          extracted_preferences: Json | null
          feedback_text: string
          id: number
          processed: boolean
          user_id: string
          user_name: string
          user_override_classification: string | null
        }
        Insert: {
          classification?: string | null
          context_id?: string | null
          context_type?: string
          created_at?: string
          extracted_mistakes?: Json | null
          extracted_preferences?: Json | null
          feedback_text: string
          id?: number
          processed?: boolean
          user_id: string
          user_name: string
          user_override_classification?: string | null
        }
        Update: {
          classification?: string | null
          context_id?: string | null
          context_type?: string
          created_at?: string
          extracted_mistakes?: Json | null
          extracted_preferences?: Json | null
          feedback_text?: string
          id?: number
          processed?: boolean
          user_id?: string
          user_name?: string
          user_override_classification?: string | null
        }
        Relationships: []
      }
      ai_global_insights: {
        Row: {
          approved_by: string
          candidate_id: number | null
          context_type: string
          context_value: string | null
          created_at: string
          id: number
          insight_text: string
        }
        Insert: {
          approved_by: string
          candidate_id?: number | null
          context_type?: string
          context_value?: string | null
          created_at?: string
          id?: number
          insight_text: string
        }
        Update: {
          approved_by?: string
          candidate_id?: number | null
          context_type?: string
          context_value?: string | null
          created_at?: string
          id?: number
          insight_text?: string
        }
        Relationships: [
          {
            foreignKeyName: "ai_global_insights_candidate_id_fkey"
            columns: ["candidate_id"]
            isOneToOne: false
            referencedRelation: "ai_candidate_insights"
            referencedColumns: ["id"]
          },
        ]
      }
      ai_user_preferences: {
        Row: {
          created_at: string
          id: number
          preference_key: string
          preference_value: string
          source_feedback_id: number | null
          updated_at: string
          user_id: string
        }
        Insert: {
          created_at?: string
          id?: number
          preference_key: string
          preference_value: string
          source_feedback_id?: number | null
          updated_at?: string
          user_id: string
        }
        Update: {
          created_at?: string
          id?: number
          preference_key?: string
          preference_value?: string
          source_feedback_id?: number | null
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "ai_user_preferences_source_feedback_id_fkey"
            columns: ["source_feedback_id"]
            isOneToOne: false
            referencedRelation: "ai_feedback"
            referencedColumns: ["id"]
          },
        ]
      }
      equipment: {
        Row: {
          category: string
          id: number
          name: string
        }
        Insert: {
          category: string
          id?: number
          name: string
        }
        Update: {
          category?: string
          id?: number
          name?: string
        }
        Relationships: []
      }
      feedback: {
        Row: {
          admin_notes: string | null
          closed_at: string | null
          created_at: string
          description: string
          id: number
          status: string
          title: string
          type: string
          user_id: string
          user_name: string
        }
        Insert: {
          admin_notes?: string | null
          closed_at?: string | null
          created_at?: string
          description?: string
          id?: number
          status?: string
          title: string
          type?: string
          user_id: string
          user_name: string
        }
        Update: {
          admin_notes?: string | null
          closed_at?: string | null
          created_at?: string
          description?: string
          id?: number
          status?: string
          title?: string
          type?: string
          user_id?: string
          user_name?: string
        }
        Relationships: []
      }
      form_field_configs: {
        Row: {
          created_at: string
          display_name: string
          field_key: string
          form_type: string
          id: number
          is_required: boolean
          is_visible: boolean
          section: string
          sort_order: number
        }
        Insert: {
          created_at?: string
          display_name: string
          field_key: string
          form_type: string
          id?: number
          is_required?: boolean
          is_visible?: boolean
          section?: string
          sort_order?: number
        }
        Update: {
          created_at?: string
          display_name?: string
          field_key?: string
          form_type?: string
          id?: number
          is_required?: boolean
          is_visible?: boolean
          section?: string
          sort_order?: number
        }
        Relationships: []
      }
      form_field_options: {
        Row: {
          created_at: string
          field_config_id: number
          id: number
          is_active: boolean
          label: string
          sort_order: number
          value: string
        }
        Insert: {
          created_at?: string
          field_config_id: number
          id?: number
          is_active?: boolean
          label: string
          sort_order?: number
          value: string
        }
        Update: {
          created_at?: string
          field_config_id?: number
          id?: number
          is_active?: boolean
          label?: string
          sort_order?: number
          value?: string
        }
        Relationships: [
          {
            foreignKeyName: "form_field_options_field_config_id_fkey"
            columns: ["field_config_id"]
            isOneToOne: false
            referencedRelation: "form_field_configs"
            referencedColumns: ["id"]
          },
        ]
      }
      lesson_categories: {
        Row: {
          created_at: string
          id: number
          name: string
        }
        Insert: {
          created_at?: string
          id?: number
          name: string
        }
        Update: {
          created_at?: string
          id?: number
          name?: string
        }
        Relationships: []
      }
      lesson_implementations: {
        Row: {
          id: number
          is_implemented: boolean | null
          is_relevant: boolean | null
          lesson_id: number
          notes: string | null
          priority: string
          project_id: number
          reason: string
          responded_at: string | null
          responded_by: string | null
        }
        Insert: {
          id?: number
          is_implemented?: boolean | null
          is_relevant?: boolean | null
          lesson_id: number
          notes?: string | null
          priority?: string
          project_id: number
          reason?: string
          responded_at?: string | null
          responded_by?: string | null
        }
        Update: {
          id?: number
          is_implemented?: boolean | null
          is_relevant?: boolean | null
          lesson_id?: number
          notes?: string | null
          priority?: string
          project_id?: number
          reason?: string
          responded_at?: string | null
          responded_by?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "lesson_implementations_lesson_id_fkey"
            columns: ["lesson_id"]
            isOneToOne: false
            referencedRelation: "lessons"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "lesson_implementations_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      lesson_workflow_events: {
        Row: {
          actor_id: string
          actor_name: string
          actor_role: string
          created_at: string
          event_type: string
          id: number
          lesson_id: number
          note: string | null
          recovered_from_log_id: number | null
          status_after: string | null
          status_before: string | null
          target_user_id: string | null
          target_user_name: string | null
        }
        Insert: {
          actor_id: string
          actor_name: string
          actor_role: string
          created_at?: string
          event_type: string
          id?: number
          lesson_id: number
          note?: string | null
          recovered_from_log_id?: number | null
          status_after?: string | null
          status_before?: string | null
          target_user_id?: string | null
          target_user_name?: string | null
        }
        Update: {
          actor_id?: string
          actor_name?: string
          actor_role?: string
          created_at?: string
          event_type?: string
          id?: number
          lesson_id?: number
          note?: string | null
          recovered_from_log_id?: number | null
          status_after?: string | null
          status_before?: string | null
          target_user_id?: string | null
          target_user_name?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "lesson_workflow_events_lesson_id_fkey"
            columns: ["lesson_id"]
            isOneToOne: false
            referencedRelation: "lessons"
            referencedColumns: ["id"]
          },
        ]
      }
      lessons: {
        Row: {
          ai_review: Json | null
          approved_by: string | null
          assigned_to: string | null
          category: string
          closed_date: string | null
          created_by: string
          date: string
          description: string
          distributed_to: number[] | null
          distributed_to_referents: string[] | null
          equipment_ids: number[]
          event_date: string | null
          file_url: string | null
          id: number
          impact_budget_cost: number | null
          impact_quality_desc: string | null
          impact_schedule_delay: number | null
          professional_domain: string | null
          project_id: number | null
          project_name: string
          recommendation: string
          referent_start_date: string | null
          return_date: string | null
          return_reason: string | null
          returned_by: string | null
          risk: string
          skip_distribution: boolean
          stage: string
          status: string
          target_date: string | null
          title: string
          updated_at: string
          workflow_status: string
        }
        Insert: {
          ai_review?: Json | null
          approved_by?: string | null
          assigned_to?: string | null
          category: string
          closed_date?: string | null
          created_by: string
          date?: string
          description?: string
          distributed_to?: number[] | null
          distributed_to_referents?: string[] | null
          equipment_ids?: number[]
          event_date?: string | null
          file_url?: string | null
          id?: number
          impact_budget_cost?: number | null
          impact_quality_desc?: string | null
          impact_schedule_delay?: number | null
          professional_domain?: string | null
          project_id?: number | null
          project_name: string
          recommendation?: string
          referent_start_date?: string | null
          return_date?: string | null
          return_reason?: string | null
          returned_by?: string | null
          risk?: string
          skip_distribution?: boolean
          stage: string
          status?: string
          target_date?: string | null
          title: string
          updated_at?: string
          workflow_status?: string
        }
        Update: {
          ai_review?: Json | null
          approved_by?: string | null
          assigned_to?: string | null
          category?: string
          closed_date?: string | null
          created_by?: string
          date?: string
          description?: string
          distributed_to?: number[] | null
          distributed_to_referents?: string[] | null
          equipment_ids?: number[]
          event_date?: string | null
          file_url?: string | null
          id?: number
          impact_budget_cost?: number | null
          impact_quality_desc?: string | null
          impact_schedule_delay?: number | null
          professional_domain?: string | null
          project_id?: number | null
          project_name?: string
          recommendation?: string
          referent_start_date?: string | null
          return_date?: string | null
          return_reason?: string | null
          returned_by?: string | null
          risk?: string
          skip_distribution?: boolean
          stage?: string
          status?: string
          target_date?: string | null
          title?: string
          updated_at?: string
          workflow_status?: string
        }
        Relationships: [
          {
            foreignKeyName: "lessons_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      notifications: {
        Row: {
          description: string
          entity_id: string | null
          entity_type: string | null
          id: number
          read: boolean
          time: string
          title: string
          type: string
          user_id: string
        }
        Insert: {
          description?: string
          entity_id?: string | null
          entity_type?: string | null
          id?: number
          read?: boolean
          time?: string
          title: string
          type: string
          user_id: string
        }
        Update: {
          description?: string
          entity_id?: string | null
          entity_type?: string | null
          id?: number
          read?: boolean
          time?: string
          title?: string
          type?: string
          user_id?: string
        }
        Relationships: []
      }
      profiles: {
        Row: {
          assigned_equipment_ids: number[]
          assigned_projects: number[]
          assigned_stage_indexes: number[]
          email: string
          email_preferences: Json
          id: string
          name: string
          password: string
          role: string
        }
        Insert: {
          assigned_equipment_ids?: number[]
          assigned_projects?: number[]
          assigned_stage_indexes?: number[]
          email: string
          email_preferences?: Json
          id: string
          name: string
          password?: string
          role?: string
        }
        Update: {
          assigned_equipment_ids?: number[]
          assigned_projects?: number[]
          assigned_stage_indexes?: number[]
          email?: string
          email_preferences?: Json
          id?: string
          name?: string
          password?: string
          role?: string
        }
        Relationships: []
      }
      projects: {
        Row: {
          equipment_ids: number[]
          id: number
          lessons_count: number
          manager_id: string
          name: string
          project_type: string
          risk: string
          site: string | null
          stage_index: number
          stage_indexes: number[]
          station_type: string
        }
        Insert: {
          equipment_ids?: number[]
          id?: number
          lessons_count?: number
          manager_id: string
          name: string
          project_type?: string
          risk?: string
          site?: string | null
          stage_index?: number
          stage_indexes?: number[]
          station_type?: string
        }
        Update: {
          equipment_ids?: number[]
          id?: number
          lessons_count?: number
          manager_id?: string
          name?: string
          project_type?: string
          risk?: string
          site?: string | null
          stage_index?: number
          stage_indexes?: number[]
          station_type?: string
        }
        Relationships: []
      }
      referent_reviews: {
        Row: {
          decision: string | null
          id: number
          is_implemented: boolean | null
          is_relevant: boolean | null
          lesson_id: number
          notes: string | null
          priority: string
          reason: string
          referent_id: string
          responded_at: string | null
          response_text: string | null
        }
        Insert: {
          decision?: string | null
          id?: never
          is_implemented?: boolean | null
          is_relevant?: boolean | null
          lesson_id: number
          notes?: string | null
          priority?: string
          reason?: string
          referent_id: string
          responded_at?: string | null
          response_text?: string | null
        }
        Update: {
          decision?: string | null
          id?: never
          is_implemented?: boolean | null
          is_relevant?: boolean | null
          lesson_id?: number
          notes?: string | null
          priority?: string
          reason?: string
          referent_id?: string
          responded_at?: string | null
          response_text?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "referent_reviews_lesson_id_fkey"
            columns: ["lesson_id"]
            isOneToOne: false
            referencedRelation: "lessons"
            referencedColumns: ["id"]
          },
        ]
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      apply_lesson_workflow_event: {
        Args: {
          p_actor_id: string
          p_actor_name: string
          p_actor_role: string
          p_event_type: string
          p_lesson_id: number
          p_lesson_updates?: Json
          p_note?: string
          p_status_after: string
          p_target_user_id?: string
          p_target_user_name?: string
        }
        Returns: {
          actor_id: string
          actor_name: string
          actor_role: string
          created_at: string
          event_type: string
          id: number
          lesson_id: number
          note: string | null
          recovered_from_log_id: number | null
          status_after: string | null
          status_before: string | null
          target_user_id: string | null
          target_user_name: string | null
        }
        SetofOptions: {
          from: "*"
          to: "lesson_workflow_events"
          isOneToOne: true
          isSetofReturn: false
        }
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const
