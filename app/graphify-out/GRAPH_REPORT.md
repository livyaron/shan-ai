# Graph Report - app  (2026-11-17)

## Corpus Check
- 110 files · ~306,308 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3314 nodes · 7548 edges · 192 communities (152 shown, 40 thin omitted)
- Extraction: 82% EXTRACTED · 18% INFERRED · 0% AMBIGUOUS · INFERRED: 1369 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c671fff7`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Dashboard Router & Report Models
- Lessons Gateway & Access
- Pattern & Forecast Analytics
- Core Decision Data Models
- Lessons AI & Blockers
- logging
- Login & Auth
- RACI Suggestion Service
- Eval Cron & Weekly Reports
- insight_ai.py
- Executive Summary Parser
- Lessons Admin
- Master File Project Sync
- Decisions Page UI
- Mission
- answer_with_full_context
- Gold Answers & Feedback
- Decision
- War Room Router
- Project Risk Learning
- Telegram Bot Callbacks
- Project Query Tools
- Answer Agent & Routing
- War Room Motto
- Missions API & KPIs
- Eval Loop Pages
- Feedback Optimization
- Project Analysis Pages
- Knowledge File Management
- Projects Telegram Menu
- Telegram Command Handlers
- Logs & Knowledge Inspector
- Project Report Service
- Judge Backfill Service
- Lessons Project Link
- app_database
- Pending Queue Service
- Missions Report Service
- Extraction Service
- Learning Rules
- Lessons Import
- Per Question Loop Service
- Contracts Sheet
- War Room
- Project Links
- user_ai_analysis
- Logs
- Telegram Polling
- telegram.py
- Projects #2
- projects.py
- Dashboard Domains
- Insight Access
- _masters
- Llm Health
-  War Room Actions
- War Room Chain
- Dossier Service
-  Project Visual
- Video Report Service
- war_room_kpis.py
- Decisions Menu Service
- Memory Service
- Ask
- Lessons Project Link #3
- Dashboard #2
- Lessons Link Sheet
- Memory Service #2
- Project Reports
- Project Chart
- Claude Service
- Schemas
- Ask #2
- Gemma Client
- _ensure_eval_caches
- Per Question Loop Service #2
- extract_text
- Missions Report Service #2
- Ask Router
- Target Reading
- Telegram State
- Session Summary Service
- Knowledge Service #2
- Lessons Project Link #4
- Lessons Project Link #5
- missions_menu_service.py
- Telegram Service
- Main
- Config
- Project Link Telegram
- _get_learned_instructions
- build_mission_card
- Eval Curate
-  Project Charts
- Groq Client
- auth.py
- Lessons Process Metrics
- Missions Report Service #3
- Missions Report Service #4
- Project Lessons
- Logs #2
- Project Visual
- Insight Triage
- Main #2
- Lessons Project Link #6
- Project Kpis
- Files
- Quality
- Main #3
- Knowledge Service #4
- Lessons Spa
- Distinct Eval Service
- File Preview
- Knowledge Service #5
- Missions Report Service #5
- asyncio
- Lessons Project Link #7
- Login
- Learning Rules #2
- Contracts Sheet #3
- Analysis Cache
- il_format.py
- Stage Sectors
- llm_router.py
- ManagerAlias
- blockers.py
- learning.html (Learning & calibration center)
- ProjectSnapshot
- Project Sync
- Projects Menu Service
- War Room Styles
-  Intro Splash
-  Project Picker
- Project Delays
- name_projects
- Missions Report Service #6
- Project Tools
- feedback_service.py
- Main #5
- inventory.py
- Llm Config #2
- Main #6
- generate_areas
- Job Titles
- telegram_routing.py
- llm_chat
- Dashboard #4
- Project Blockers
- Users
- analyse
- generate_project
- AsyncSession
- Project Contracts (apply() table filter)
- Users (openAiAnalysis())
- War Room Wall (every() interval registry)
- _weekly_eval_summary
- Profile (profile.html (Personal pr)
- Project Blockers (countUp() (blockers))
- Users (showQr())
-  War Room Actions (openSummary(refresh) at-r)
- Decision Review (combineProblem(e))
- Report Detail (toggle() section collapse)
- War Room Table (sortBy() in-group sorting)
- _worth_extracting
- _apply_learned_synonyms
- gather_report_data
- _project_picker.html (optional project link dialog)
- war_room_wall.html (TV wall display)
- record_answer_comment
- _ask
- Keyboard shortcuts (J/K/Space/E/D/Esc//)
- _sync_and_summarize
- restore_names
- check_routing
- claim_autostart

## God Nodes (most connected - your core abstractions)
1. `User` - 266 edges
2. `get()` - 100 edges
3. `Decision` - 76 edges
4. `Mission` - 76 edges
5. `TelegramPollingBot` - 64 edges
6. `Project` - 52 edges
7. `llm_chat()` - 49 edges
8. `_navbar.html (shared SHAN-AI / DSS navbar)` - 32 edges
9. `KnowledgeFile` - 31 edges
10. `decisions.html (Decision Management page)` - 31 edges

## Surprising Connections (you probably didn't know these)
- `Hourly motto band (quote + AI line, capped in Python)` --references--> `shorten()`  [AMBIGUOUS]
  templates/war_room_wall.html → app/services/war_room_wall.py
- `_warm_embeddings()` --calls--> `embed()`  [INFERRED]
  app/main.py → app/services/embedding_service.py
- `_tidy_learned_rules()` --calls--> `tidy_stored_instructions()`  [INFERRED]
  app/main.py → app/services/optimization_service.py
- `decision_text()` --uses--> `Decision`  [INFERRED]
  app/services/project_links.py → app/models.py
- `mission_text()` --uses--> `Mission`  [INFERRED]
  app/services/project_links.py → app/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Avatar generation wizard (prompt -> generate -> preview -> save)** — templates_users_generateavatar, templates_users_dogenerateavatar, templates_users_dosaveavatar, templates_users_showavatarstep, templates_users_backtoprompt [EXTRACTED 1.00]
- **Decision RACI editing & validation flow** — templates_decisions_openraciedit, templates_decisions_validateraci, templates_decisions_saveraci, templates_decisions_oneditsave, templates_decisions_aisuggestraci, templates_decisions_raci_single_accountable_rule [EXTRACTED 1.00]
- **Knowledge Inspector edit/refresh flow** — templates_logs_openinspector, templates_logs_deleteentry, templates_logs_savesynonym, templates_logs_saveinstruction, templates_logs_reorganizeknowledge [EXTRACTED 1.00]
- **Project analysis pages linked from projects list** — templates_projects, templates_project_blockers, templates_project_delays, templates_project_insights, templates_project_contracts [EXTRACTED 1.00]
- **War Room layouts sharing data, endpoints and partials** — templates_war_room, templates_war_room_focus, templates_war_room_table, templates_war_room_wall, templates__war_room_style_switcher [EXTRACTED 1.00]
- **War-room shared widgets painted from --wrs-bg/--wrs-fg** — templates__war_room_style_switcher, templates__war_room_actions_due_postponement_dialog, templates__project_picker, templates__war_room_style_switcher_wrs_theme_vars [EXTRACTED 1.00]
- **Decision confirm submission (POST decisions/confirm with raci_r/a/c/i)** — templates_ask_submitdecisionfromask, templates_dashboard_submitdecision, templates_decision_review_prepareformdata [INFERRED 0.85]
- **Decision feedback → lessons → calibration** — templates_decisions_decision_feedback, templates_learning_lesson_extraction, templates_learning_ai_calibration [INFERRED 0.85]
- **LLM-judge eval flow (backfill, rejudge, gold seeding, live measure)** — templates_quality_startbackfill, templates_quality_startrejudge, templates_quality_startrejudgedistinct, templates_quality_seedgold, templates_quality_livemeasure, templates_quality_gold_answer_set [INFERRED 0.85]
- **Rules decide levels/numbers, AI only writes validated prose** — templates_triage_levels, templates_project_blockers_ai_recommendations, templates_project_page_ai_deep_dive, templates_project_insights_area [INFERRED 0.85]
- **Shan-AI self-learning & evaluation loop** — templates_eval_eval_cycle_sse, templates_eval_curate_gold_answers, templates_learning_rules_pending_rule_approval, templates_learning_rules_learned_rules, templates_learning_learning_loop_effectiveness [INFERRED 0.85]
- **RACI one-Accountable validation across decision entry points** — templates_ask_validateraci, templates_dashboard_validateraci, templates_decision_review_validateracitable, templates_decision_review_single_accountable_rule [INFERRED 0.95]

## Communities (192 total, 40 thin omitted)

### Community 0 - "Dashboard Router & Report Models"
Cohesion: 0.04
Nodes (66): RACIRule, ReportHistory, RoleEnum, User, analyze_decision(), analyze_edit_reasons_into_rules(), analyze_preview(), _apply_avatar_style() (+58 more)

### Community 1 - "Lessons Gateway & Access"
Cohesion: 0.05
Nodes (46): _acting_id(), ai_functions(), bootstrap(), read(), _cached_summary(), _classify_feedback(), _client(), current_identity() (+38 more)

### Community 2 - "Pattern & Forecast Analytics"
Cohesion: 0.06
Nodes (41): analyse(), _bucket(), compute(), _horizon(), _rate(), snapshot(), visible_ids(), _cached() (+33 more)

### Community 3 - "Core Decision Data Models"
Cohesion: 0.06
Nodes (37): AnswerFeedback, DecisionDistribution, DecisionFeedback, DecisionRaciRole, DecisionStatusEnum, DecisionTypeEnum, DistributionStatusEnum, DistributionTypeEnum (+29 more)

### Community 4 - "Lessons AI & Blockers"
Cohesion: 0.13
Nodes (21): build_admin_insights(), build_analyze(), build_review(), build_suggest_categories(), build_summarize(), _by_name(), clip_lines(), insights_block() (+13 more)

### Community 5 - "logging"
Cohesion: 0.10
Nodes (4): _embed_sync(), format_past_context(), _get_model(), claim()

### Community 6 - "Login & Auth"
Cohesion: 0.11
Nodes (22): AppLogin, _check_credentials(), get_current_user(), _is_initial_password(), _is_locked(), login(), login_app(), login_page() (+14 more)

### Community 7 - "RACI Suggestion Service"
Cohesion: 0.04
Nodes (30): RACISuggestion, RACISuggestionStatusEnum, save_raci(), get_raci_patterns(), accountable_error(), build_new_user_role_picker(), build_raci_context(), build_raci_list_message() (+22 more)

### Community 8 - "Eval Cron & Weekly Reports"
Cohesion: 0.09
Nodes (16): _batch_eval_run(), _dossier_drip(), _feedback_optimization_run(), _memory_extraction_run(), _memory_weekly_digest(), _missions_cache_watchdog(), _missions_daily_digest(), _missions_daily_prewarm() (+8 more)

### Community 9 - "insight_ai.py"
Cohesion: 0.27
Nodes (11): _clean(), division_context(), _hot_block(), _level_counts(), _norm_num(), _nums(), _pack(), project_context() (+3 more)

### Community 10 - "Executive Summary Parser"
Cohesion: 0.09
Nodes (24): _as_date(), _cell(), _col_index(), _criterion(), _filled(), _ident_col(), _is_header(), _is_num() (+16 more)

### Community 11 - "Lessons Admin"
Cohesion: 0.12
Nodes (26): add_member(), _change(), group_login(), group_problems(), groups_page(), is_linkable(), link_problem(), links_list() (+18 more)

### Community 12 - "Master File Project Sync"
Cohesion: 0.08
Nodes (18): backfill_files(), _build_column_map(), _extract_weekly_report(), _file_report_date(), _mk_date(), _parse_bool(), _parse_date(), pick_master_sheet() (+10 more)

### Community 13 - "Decisions Page UI"
Cohesion: 0.12
Nodes (29): decisions.html (Decision Management page), Decision AI analysis panel cache (~15 min, refresh=1), aiFooter(), aiSuggestRaci(), badge(), buildRecipientRow(), canEditRaci(), checkAutoOpenRaci() (+21 more)

### Community 14 - "Mission"
Cohesion: 0.12
Nodes (14): Mission, MissionUpdate, add_mission_update(), get_board_counts(), get_done_history(), get_mission(), get_users_with_active_missions(), list_assignable_users() (+6 more)

### Community 15 - "answer_with_full_context"
Cohesion: 0.10
Nodes (10): answer_question(), answer_with_full_context(), _expand_hebrew_abbrevs(), _expand_query(), _extract_manager_name_from_question(), _extract_stage_filter_from_question(), _extract_wbs_and_projects_from_chunks(), _is_bare_name_query() (+2 more)

### Community 16 - "Gold Answers & Feedback"
Cohesion: 0.12
Nodes (16): EvalGoldAnswer, record_thumbs_down(), seed_from_production(), delete_gold(), _detect_field(), _detect_project(), _fact_based_check(), _format_field_answer() (+8 more)

### Community 17 - "Decision"
Cohesion: 0.06
Nodes (20): Decision, LessonLearned, _can_toggle_relevance(), learning_batch_status(), learning_page(), DecisionService, embed(), get_similar_decisions() (+12 more)

### Community 18 - "War Room Router"
Cohesion: 0.11
Nodes (19): MissionStatusEnum, add_note(), assign_owner(), _blank_to_none(), bulk_action(), change_due(), change_status(), create_mission_web() (+11 more)

### Community 19 - "Project Risk Learning"
Cohesion: 0.13
Nodes (11): _buffer_pts(), compute_risk_score(), _current_stage_run_start(), get_stage_duration_stats(), is_presumed_completed(), _keyword_pts(), _overdue_pts(), _stage_intervals() (+3 more)

### Community 21 - "Project Query Tools"
Cohesion: 0.09
Nodes (20): _ai_detect_intent(), answer_project_query(), _compute_delay(), _confirmed_aliases_for(), count_by_type(), find_projects_by_identifier(), _format_project_card(), get_project_details() (+12 more)

### Community 22 - "Answer Agent & Routing"
Cohesion: 0.14
Nodes (12): bare_name_plan(), build_prompt(), _clean(), engine_applies(), engine_mode(), engine_on(), make_plan(), parse_plan() (+4 more)

### Community 23 - "War Room Motto"
Cohesion: 0.06
Nodes (31): stats(), _band(), build(), cache_key(), _context(), _cycle_order(), _db_get(), _db_put() (+23 more)

### Community 24 - "Missions API & KPIs"
Cohesion: 0.16
Nodes (8): _due(), _latest_note(), _loaded_attr(), _mission_row(), missions(), _missions_query(), _owner_name(), _status()

### Community 25 - "Eval Loop Pages"
Cohesion: 0.05
Nodes (36): EvalRun, ask_page(), get_feedbacks(), get_raci(), backfill_status(), _emit_log(), eval_abort(), eval_curate_page() (+28 more)

### Community 26 - "Feedback Optimization"
Cohesion: 0.17
Nodes (9): sse_body(), clean_instructions(), is_actionable_instruction(), _merge_and_store_instructions(), reorganize_knowledge(), _call_instructions(), _call_synonyms(), run_optimization() (+1 more)

### Community 27 - "Project Analysis Pages"
Cohesion: 0.09
Nodes (32): project_blockers.html (Blockers cinematic film), Blocker AI recommendations (POST /dashboard/projects/blockers/ai), project_contracts.html (Contractor engagements & tenders), Runway to ground-breaking (diverging chart), Procurement process funnel (reached vs here), project_delays.html (Delay analysis), detail() per-project story, Slip months per project-year metric (+24 more)

### Community 28 - "Knowledge File Management"
Cohesion: 0.09
Nodes (22): KnowledgeFile, delete_file(), edit_file(), _ext(), file_details(), files_page(), open_file(), replace_file() (+14 more)

### Community 29 - "Projects Telegram Menu"
Cohesion: 0.12
Nodes (16): build_custom_results_keyboard(), build_detail_back_keyboard(), build_filter_date_keyboard(), build_filter_field_keyboard(), build_filter_value_keyboard(), build_results_keyboard(), build_th_results_keyboard(), build_th_sub_keyboard() (+8 more)

### Community 31 - "Logs & Knowledge Inspector"
Cohesion: 0.12
Nodes (21): QueryLog, QuerySynonym, add_instruction(), add_knowledge_entry(), clear_all_logs(), delete_instruction_by_index(), delete_knowledge_entry(), FeedbackRequest (+13 more)

### Community 32 - "Project Report Service"
Cohesion: 0.12
Nodes (12): auto_send_project_report(), generate_pdf(), generate_report_html(), _methodology_html(), _project_row(), _rag_badge(), _render_html(), _finishing_section() (+4 more)

### Community 33 - "Judge Backfill Service"
Cohesion: 0.20
Nodes (10): _classify_failure(), get_progress(), get_rejudge_progress(), _is_no_info(), judge_one(), parse_failure_type(), rejudge_distinct(), rejudge_gold_covered() (+2 more)

### Community 34 - "Lessons Project Link"
Cohesion: 0.13
Nodes (17): all_clear(), completion_items(), completion_state(), _dismissed(), _dup_holds(), _last_sync(), _lessons_projects(), link_issues() (+9 more)

### Community 35 - "app_database"
Cohesion: 0.14
Nodes (11): _send(), _compute_delta(), _decisions_summary(), _gather_raw_data(), generate_report_for_user(), _pending_approvals(), report_failed(), _sanitize_json_string() (+3 more)

### Community 36 - "Pending Queue Service"
Cohesion: 0.10
Nodes (10): is_overload_error(), enqueue(), _fail(), _process_one(), process_pending_queue(), queue_depth(), _build_preview_text(), _decision_preview_keyboard() (+2 more)

### Community 37 - "Missions Report Service"
Cohesion: 0.12
Nodes (12): MissionReportCache, build_report_bytes(), _cache_put(), day_cache_get(), day_cache_put(), _db_cache_get(), _db_cache_put(), get_ai_insights() (+4 more)

### Community 38 - "Extraction Service"
Cohesion: 0.13
Nodes (11): SystemFlag, _adjudicate(), approve_pending(), build_weekly_digest(), expire_stale_pending(), _extract_facts(), _flag_value(), _notify_admins_contradiction() (+3 more)

### Community 39 - "Learning Rules"
Cohesion: 0.18
Nodes (15): IntentOverride, ProjectAlias, AliasCreate, approve_pending(), create_alias(), create_intent_override(), delete_alias(), delete_correction_pin() (+7 more)

### Community 40 - "Lessons Import"
Cohesion: 0.14
Nodes (11): clean_rows(), drift(), fetch_all(), fetch_source(), ImportAbort, insert_columns(), local_only_projects(), name_words() (+3 more)

### Community 41 - "Per Question Loop Service"
Cohesion: 0.12
Nodes (16): RepairProposal, _answer(), _apply_patch(), approve_pin(), _clear_kill_switch(), _kill_switch_on(), _patch_to_shadow(), propose_targeted_fix() (+8 more)

### Community 42 - "Contracts Sheet"
Cohesion: 0.10
Nodes (22): analyse(), _as_date(), _clean(), _contractor(), _dated_steps(), _dotted(), events(), _ground() (+14 more)

### Community 43 - "War Room"
Cohesion: 0.26
Nodes (13): _war_room_actions.html (shared war-room write actions), openChild(ds) follow-up mission, openCreate(), wrOwnerFilter(inp), _war_room_owner_field.html (owner search + select), _war_room_project_field.html (optional project on new mission), _war_room_style_switcher.html (per-user layout picker), war_room.html (War Room 2x2 Eisenhower board) (+5 more)

### Community 44 - "Project Links"
Cohesion: 0.16
Nodes (12): decision_text(), get_label(), label(), labels_for(), manager_names_for(), mission_text(), _norm_id(), options() (+4 more)

### Community 45 - "user_ai_analysis"
Cohesion: 0.20
Nodes (6): _cached_ai_panel(), _build(), decision_ai_analysis(), _recent_decision_lines(), _recent_feedback_notes(), user_ai_analysis()

### Community 46 - "Logs"
Cohesion: 0.11
Nodes (21): _navbar.html (shared SHAN-AI / DSS navbar), Pending approvals badge, Shared motion layer (static/css/shan_ui.css + static/js/shan_ui.js), ask.html (Ask the AI chat page), dashboard.html (live substation control center), decision_review.html (AI decision review & publish), logs.html (Logs & Learning page), Log failure type (STRUCTURE / TERMINOLOGY) (+13 more)

### Community 47 - "Telegram Polling"
Cohesion: 0.12
Nodes (8): _cause_keyboard(), _comment_keyboard(), _feedback_keyboard(), _keyboard_for_user(), _main_reply_keyboard(), _mgr_approval_keyboard(), _process_master_and_notify(), _viewer_reply_keyboard()

### Community 48 - "telegram.py"
Cohesion: 0.08
Nodes (7): get_db_session(), telegram_webhook(), webhook_repair(), webhook_status(), gold_keyboard(), is_manager(), next_candidate()

### Community 49 - "Projects #2"
Cohesion: 0.21
Nodes (5): backfill_history(), _ext(), insights_ai_refresh(), regenerate_briefs(), upload_project_file()

### Community 50 - "projects.py"
Cohesion: 0.09
Nodes (20): _blockers_data(), _blockers_scope_key(), insights_access_page(), insights_access_save(), _kpi_rows(), project_ai(), project_blockers_ai(), project_blockers_page() (+12 more)

### Community 51 - "Dashboard Domains"
Cohesion: 0.17
Nodes (10): all_domains(), create_rights(), _days(), _exec_summary(), _fmt(), lessons_stats(), missions_stats(), projects_stats() (+2 more)

### Community 52 - "Insight Access"
Cohesion: 0.13
Nodes (12): ai_kinds_for(), drill_for(), owns_blocker(), preview_options(), preview_scope(), Scope, scope_for(), sees_all() (+4 more)

### Community 53 - "_masters"
Cohesion: 0.21
Nodes (7): complete(), create_missing(), dismiss(), dismiss_problem(), _dismissed_ids(), _levels(), _masters()

### Community 54 - "Llm Health"
Cohesion: 0.15
Nodes (9): available_models(), check_providers(), format_models_he(), format_report_he(), _probe_gemma(), _probe_groq(), _probe_models(), _status_icon() (+1 more)

### Community 55 - " War Room Actions"
Cohesion: 0.19
Nodes (13): act(id, action), addNote(id), assign(id), closeMission(id, withNote, thenChild), move(id), post(url, data), saveDue(), toggleClose(id, action) (+5 more)

### Community 56 - "War Room Chain"
Cohesion: 0.18
Nodes (6): _closed_at(), _il_date(), load_chains(), load_child_counts(), _range(), _step()

### Community 57 - "Dossier Service"
Cohesion: 0.17
Nodes (11): ProjectDossier, build_source(), extract_dossier_request(), _fmt(), _generation_enabled(), get_dossier_text(), get_dossier_text_by_identifier(), mark_dirty() (+3 more)

### Community 58 - " Project Visual"
Cohesion: 0.31
Nodes (19): arc(), Substation digital twin (three.js), drawIn(), el(), glowFilter(), reportBlock(), sceneDelay(), sceneFlags() (+11 more)

### Community 59 - "Video Report Service"
Cohesion: 0.18
Nodes (10): authenticator_password_sql(), ensure_schema(), _draw_text_center(), _draw_text_ra(), generate_report_video(), _get_font(), _make_audio(), _make_slide() (+2 more)

### Community 60 - "war_room_kpis.py"
Cohesion: 0.15
Nodes (6): apply_filter(), build_cards(), is_known(), owns_status(), resolve(), week_ago()

### Community 61 - "Decisions Menu Service"
Cohesion: 0.16
Nodes (12): build_custom_filter_keyboard(), build_custom_filter_message(), build_custom_results_keyboard(), build_decision_card_keyboard(), build_feedback_results_keyboard(), build_results_keyboard(), format_decision_card(), format_result_line() (+4 more)

### Community 62 - "Memory Service"
Cohesion: 0.17
Nodes (6): build_change_fact(), extract_recall_topic(), _fmt_value(), format_memory_context(), record_project_changes(), save_memory()

### Community 63 - "Ask"
Cohesion: 0.13
Nodes (15): appendAIMsg(), appendDecisionPreview(), escapeHtml(), openCorrectionBox(logId), sendFeedback(logId, value), sendQuestion(), submitCorrection(logId) -> /dashboard/ask/correct, submitDecisionFromAsk() (+7 more)

### Community 64 - "Lessons Project Link #3"
Cohesion: 0.15
Nodes (11): _insert_created(), keep_local(), link(), link_problem(), _log(), manager_check(), merge(), merge_problem() (+3 more)

### Community 65 - "Dashboard #2"
Cohesion: 0.13
Nodes (14): Four-station zoom engine (#decisions/#missions/#projects/#lessons), sbCount() counter tween, sbDonut(), sbLiveValues(d), sbPoll() /dashboard/live polling, sbSpark() 7-day sparkline, sbSyncGrid(d), Scope switch (שלי / כל האגף) (+6 more)

### Community 66 - "Lessons Link Sheet"
Cohesion: 0.18
Nodes (6): build_workbook(), parse_value(), parse_workbook(), SheetError, user_option(), validate()

### Community 67 - "Memory Service #2"
Cohesion: 0.15
Nodes (9): MemoryNote, _active_clause(), describe_notes(), forget_memory(), get_relevant_memories(), is_memory_enabled(), _keyword_terms(), link_project() (+1 more)

### Community 68 - "Project Reports"
Cohesion: 0.26
Nodes (10): ProjectReport, ProjectReportSchedule, _check_admin(), project_report_delete(), project_report_detail(), project_reports_list(), report_schedule_page(), report_schedule_save() (+2 more)

### Community 69 - "Project Chart"
Cohesion: 0.18
Nodes (8): _clip(), _d(), delay_story(), _months_between(), _norm(), risk_column_changes(), risk_matrix(), _risk_text()

### Community 70 - "Claude Service"
Cohesion: 0.14
Nodes (4): ClaudeService, _extract_json(), _repair_unescaped_quotes(), _strip_md_fences()

### Community 71 - "Schemas"
Cohesion: 0.16
Nodes (7): RoleAssignmentRequest, RoleAssignmentResponse, Config, RoleEnum, UserRegisterRequest, UserResponse, Config

### Community 72 - "Ask #2"
Cohesion: 0.21
Nodes (6): ask_correct(), ask_query(), AskRequest, CorrectionRequest, _schedule_repair_for_gold(), _try_classify_as_decision()

### Community 73 - "Gemma Client"
Cohesion: 0.17
Nodes (7): gemma_chat(), _healthy_first(), list_models(), redact(), _strip_json_fences(), _strip_thinking(), _to_google_format()

### Community 74 - "_ensure_eval_caches"
Cohesion: 0.33
Nodes (3): CorrectionPin, PromptOverride, _ensure_eval_caches()

### Community 76 - "Per Question Loop Service #2"
Cohesion: 0.24
Nodes (6): compare_to_gold(), _extract_dates(), _rule_check(), normalize_hebrew(), _candidate_projects(), _score()

### Community 77 - "extract_text"
Cohesion: 0.32
Nodes (4): extract_text(), _extract_text_smart(), _find_context_columns(), _greedy_row_parser()

### Community 78 - "Missions Report Service #2"
Cohesion: 0.21
Nodes (9): _age_days(), _at_risk_context(), _compute_stats(), _owner_row(), _days_since_update(), due_bucket(), _format_at_risk_plain(), _last_update_at() (+1 more)

### Community 79 - "Ask Router"
Cohesion: 0.19
Nodes (6): AnswerResult, _log_query(), _normalize_q_hash(), route(), _finish(), _write_trace()

### Community 80 - "Target Reading"
Cohesion: 0.24
Nodes (7): compare(), _date(), effective_date(), _energized_positions(), find_dates(), _month_end(), read_text()

### Community 81 - "Telegram State"
Cohesion: 0.10
Nodes (8): _process_and_notify(), apply_action_edit(), expect_action_edit(), expect_answer_comment(), get_context(), take_action_edit(), take_answer_comment(), take_new_decision()

### Community 82 - "Session Summary Service"
Cohesion: 0.21
Nodes (6): ConversationSummary, build_exchange_log(), get_summary(), run_daily_summaries(), _summaries_enabled(), summarize_user()

### Community 83 - "Knowledge Service #2"
Cohesion: 0.11
Nodes (7): answer_decisions_question(), _find_header_row(), _find_latest_weekly_detail_col(), _find_manager_col(), _find_status_col(), _find_weekly_report_sheet(), _normalize_date()

### Community 84 - "Lessons Project Link #4"
Cohesion: 0.19
Nodes (8): creation_plan(), fields_for(), manager_profile_for(), missing_fields(), project_type_for(), risk_for(), stage_indexes_for(), sync_plan()

### Community 85 - "Lessons Project Link #5"
Cohesion: 0.17
Nodes (10): idf_weights(), manager_hint(), norm_name(), same_manager(), _token_hit(), _tokens(), _variants(), weighted_score() (+2 more)

### Community 86 - "missions_menu_service.py"
Cohesion: 0.09
Nodes (19): build_cancel_keyboard(), build_close_prompt_keyboard(), build_confirm_keyboard(), build_digest_keyboard(), build_due_pick_keyboard(), build_mission_card_keyboard(), build_owner_pick_keyboard(), build_quadrant_pick_keyboard() (+11 more)

### Community 88 - "Main"
Cohesion: 0.12
Nodes (7): api_status(), health_check(), _retry_telegram_start(), _scheduler_running(), _start_telegram_bot(), _webhook_watchdog(), scheduler_running()

### Community 89 - "Config"
Cohesion: 0.15
Nodes (3): _read_build_commit(), Settings, Config

### Community 90 - "Project Link Telegram"
Cohesion: 0.18
Nodes (8): button_title(), back_cb(), back_keyboard(), _button(), done_text(), picker_keyboard(), picker_text(), search_text()

### Community 91 - "_get_learned_instructions"
Cohesion: 0.29
Nodes (5): _cosine(), _embed_rules(), _batch(), _get_learned_instructions(), pick_relevant_rules()

### Community 92 - "build_mission_card"
Cohesion: 0.11
Nodes (17): build_mission_card(), create_mission(), format_create_progress(), format_digest(), format_due(), format_due_change(), format_mission_line(), format_results_message() (+9 more)

### Community 93 - "Eval Curate"
Cohesion: 0.29
Nodes (14): eval.html (Diagnostic eval cycle page), applyEvent(), eval_curate.html (Gold answer curation page), badge(), escapeHtml(), Gold answers (eval ground truth), load(), loadCandidates() (+6 more)

### Community 94 - " Project Charts"
Cohesion: 0.35
Nodes (13): drawAll(), drawDelay(), select(), drawMatrix(), el(), fillDelayTip(), fmtDate(), fmtM() (+5 more)

### Community 95 - "Groq Client"
Cohesion: 0.21
Nodes (5): _cooling_error(), get_client(), groq_chat(), list_models(), retry_after_seconds()

### Community 96 - "auth.py"
Cohesion: 0.15
Nodes (5): generate_password(), get_default_password_hash(), hash_password(), is_default_password(), verify_password()

### Community 97 - "Lessons Process Metrics"
Cohesion: 0.27
Nodes (6): compute(), _days(), load_rows(), _pct(), snapshot(), _spread()

### Community 98 - "Missions Report Service #3"
Cohesion: 0.18
Nodes (5): build_workbook(), cache_status(), _close_update(), _closed_at(), compute_insights()

### Community 99 - "Missions Report Service #4"
Cohesion: 0.15
Nodes (6): build_focus_summary(), _fallback(), collect_at_risk(), _decorate_summary(), _priority_weight(), SummaryNotBuilt

### Community 100 - "Project Lessons"
Cohesion: 0.22
Nodes (5): answer_label(), for_project(), shape(), station_type(), _visible()

### Community 101 - "Logs #2"
Cohesion: 0.20
Nodes (7): escapeHtml() (logs), openInspector(), reorganizeKnowledge(), runOptimize(), Self-optimization from feedback (synonyms + learned instructions), escapeHtml() (projects), renderBackfill()

### Community 102 - "Project Visual"
Cohesion: 0.22
Nodes (7): il_dates(), build(), stage_journey(), station_model(), twin_state(), twin_steps(), _weeks_between()

### Community 103 - "Insight Triage"
Cohesion: 0.20
Nodes (4): board(), fc_move_counts(), triage_all(), triage_project()

### Community 104 - "Main #2"
Cohesion: 0.13
Nodes (7): shutdown(), startup(), _backfill_password_flags(), _tidy_learned_rules(), _warm_embeddings(), backfill_password_is_default(), migrate_user_passwords()

### Community 105 - "Lessons Project Link #6"
Cohesion: 0.22
Nodes (5): drop_excluded(), excluded(), remove_excluded(), search_file(), search_reason()

### Community 106 - "Project Kpis"
Cohesion: 0.27
Nodes (6): board(), _file_kpis(), _ordered(), rows_for(), stage_group(), stage_key()

### Community 107 - "Files"
Cohesion: 0.24
Nodes (10): file_detail.html (Knowledge file details/edit page), Extracted file chunks, file_open.html (In-browser file viewer: PDF/DOCX/XLSX), XLSX sheet tab switcher script, files.html (Knowledge base file list & upload), handleUploadClick(), Master XLSX project sync (WBS, status, manager, weekly update), pollSyncStatus() (+2 more)

### Community 108 - "Quality"
Cohesion: 0.18
Nodes (7): Gold answer set (LLM-judge eval), load() quality data + charts, pollBf(), pollRejudge(), pollRejudgeDistinct(), seedGold(), refresh() in-place body swap

### Community 109 - "Main #3"
Cohesion: 0.18
Nodes (4): _analysis_refusal_as_page(), root(), _security_headers(), site_icon()

### Community 110 - "Knowledge Service #4"
Cohesion: 0.17
Nodes (6): _extract_keywords(), _extract_query_phrases(), _has_proper_nouns(), _inject_domain_keywords(), _rerank_by_query_keywords(), search_knowledge()

### Community 111 - "Lessons Spa"
Cohesion: 0.23
Nodes (4): _has_session(), lessons_app(), lessons_root(), spa_file()

### Community 112 - "Distinct Eval Service"
Cohesion: 0.27
Nodes (4): distinct_question_eval(), distinct_summary(), _representatives(), summarize()

### Community 113 - "File Preview"
Cohesion: 0.20
Nodes (4): _cell(), _col_letter(), preview_docx(), preview_xlsx()

### Community 114 - "Knowledge Service #5"
Cohesion: 0.20
Nodes (5): _dedup_fragment_lines(), _extract_project_name(), _format_compact_index(), format_knowledge_context(), _trim_chunk_for_specific()

### Community 115 - "Missions Report Service #5"
Cohesion: 0.18
Nodes (6): collect_report_data(), _days_until(), _description_with_updates(), _fmt_dt(), _last_update(), _update_text()

### Community 116 - "asyncio"
Cohesion: 0.14
Nodes (4): _spawn_background(), cleanup_notifications(), run_nightly(), seconds_until()

### Community 117 - "Lessons Project Link #7"
Cohesion: 0.25
Nodes (4): change_note(), sync_after_upload(), sync_from_file(), sync_report()

### Community 118 - "Login"
Cohesion: 0.27
Nodes (9): _intro_splash.html (5s opening splash), login.html (Login page), bus(), draw(), First-login Telegram verification code, layout(), pulse(), Live substation single-line diagram background (canvas) (+1 more)

### Community 119 - "Learning Rules #2"
Cohesion: 0.24
Nodes (8): Eval cycle SSE stream (/dashboard/eval/stream), Learning loop effectiveness (7d pass-rate, rules applied, corrections), loadLearnEff(), learning_rules.html (Learning rules CRUD), approve(), createAlias(), Learned rules: aliases, intent overrides, correction pins, synonyms, Pending rule proposals awaiting approval (patch + rationale)

### Community 120 - "Contracts Sheet #3"
Cohesion: 0.22
Nodes (5): ProjectContractSheet, latest(), redact(), redact_payload(), save()

### Community 121 - "Analysis Cache"
Cohesion: 0.33
Nodes (3): _evict(), invalidate(), put()

### Community 122 - "il_format.py"
Cohesion: 0.15
Nodes (9): ProjectExecSummary, latest(), reread_if_unresolved(), save(), unresolved(), il_date(), il_datetime(), register() (+1 more)

### Community 123 - "Stage Sectors"
Cohesion: 0.31
Nodes (4): is_closed(), normalize_stage(), sectors_for(), stage_sort_key()

### Community 124 - "llm_router.py"
Cohesion: 0.15
Nodes (5): LLMConfig, llm_config_page(), llm_config_save(), _get_config(), invalidate_cache()

### Community 125 - "ManagerAlias"
Cohesion: 0.20
Nodes (6): ManagerAlias, apply_completion(), apply_manager(), confirm_alias(), ensure_pm_profile(), set_manager()

### Community 126 - "blockers.py"
Cohesion: 0.20
Nodes (8): ai_context(), ai_kind(), episodes(), close(), owner_of(), recommend(), _text(), _weeks()

### Community 127 - "learning.html (Learning & calibration center)"
Cohesion: 0.18
Nodes (15): Decision outcome feedback (1-5 stars), submitFeedback(), learning.html (Learning & calibration center), AI confidence calibration vs feedback, initProjectsTab(), Lesson extraction & knowledge summaries, loadOverview(), loadRiskTable() (+7 more)

### Community 128 - "ProjectSnapshot"
Cohesion: 0.20
Nodes (7): ProjectSnapshot, get_overview_stats(), get_project_detail(), get_risk_table(), predict_next_score(), _raw_risk_rows(), save_snapshot()

### Community 129 - "Project Sync"
Cohesion: 0.25
Nodes (4): _ai_then_briefs(), _clean_brief(), generate_all_briefs(), _generate_weekly_brief()

### Community 130 - "Projects Menu Service"
Cohesion: 0.19
Nodes (6): Project, build_project_card(), _date_clause(), get_filter_options(), get_total_active(), query_projects()

### Community 131 - "War Room Styles"
Cohesion: 0.29
Nodes (3): is_known(), resolve(), resolve_for_create()

### Community 132 - " Intro Splash"
Cohesion: 0.29
Nodes (5): bolt() lightning arc, drawFlow(t) feeder/energise canvas, play(), render(t), tick()

### Community 133 - " Project Picker"
Cohesion: 0.33
Nodes (6): pjxDone(id, label), pjxLoad(), pjxOpen(opts), pjxSaveSearch(), pickCreateProject(form), Project link picker handler (pjxOpen → /decisions/{id}/project)

### Community 134 - "Project Delays"
Cohesion: 0.24
Nodes (10): blockerCard(), openSheet() blocker drill sheet, BUILD.cuts (by stage/sector/type/manager/horizon), BUILD.pipe path-to-energization leak canvas, BUILD.timeline postponements per report, BUILD.years target-year sankey, drawSheet(), drillStage() (+2 more)

### Community 135 - "name_projects"
Cohesion: 0.18
Nodes (8): name_projects(), one(), _quote_norm(), short_name(), validate(), fix_numbers(), sub(), _walk_strings()

### Community 136 - "Missions Report Service #6"
Cohesion: 0.33
Nodes (3): _recent_updates_text(), _sanitize(), _stats_block()

### Community 137 - "Project Tools"
Cohesion: 0.33
Nodes (3): _detect_intent(), _extract_type_from_count_query(), _extract_year()

### Community 138 - "feedback_service.py"
Cohesion: 0.24
Nodes (3): run_feedback_scheduler(), save_feedback_score(), send_feedback_requests()

### Community 140 - "inventory.py"
Cohesion: 0.24
Nodes (6): _bearer_token(), _configured_key(), inventory(), _iso(), _project_row(), _require_inventory_key()

### Community 141 - "Llm Config #2"
Cohesion: 0.60
Nodes (4): llm_config.html (LLM provider configuration), checkHealth(), Per-usage LLM provider routing (Groq / Gemma 4 / Auto) with fallback, selectAll()

### Community 143 - "generate_areas"
Cohesion: 0.18
Nodes (6): PatternAIAnalysis, decorate(), generate_areas(), load(), sector_kind(), weekly_by_identifier()

### Community 145 - "telegram_routing.py"
Cohesion: 0.13
Nodes (7): _ai_route_message(), _is_data_question(), _is_project_query(), looks_like_bare_name(), _maybe_summarize(), menu_shortcut(), _parse_routing_response()

### Community 146 - "llm_chat"
Cohesion: 0.22
Nodes (6): _build(), _parse_ai_json(), _build(), AIUnavailable, ask(), llm_chat()

### Community 147 - "Dashboard #4"
Cohesion: 0.67
Nodes (4): aiFooter() cache age + refresh, openDashboardAI(refresh), openDecisionAI(id, refresh), renderAISections()

### Community 148 - "Project Blockers"
Cohesion: 0.67
Nodes (4): Scene formations F (particle layout per scene), go() scene cut director, layoutMap() topic-clustered collision layout, render() canvas frame loop

### Community 150 - "analyse"
Cohesion: 0.36
Nodes (5): analyse(), owner_rows(), topic_rows(), by_hierarchy(), _spread_weeks()

### Community 151 - "generate_project"
Cohesion: 0.29
Nodes (4): generate_project(), pm_aliases(), project_kind(), _store()

### Community 152 - "AsyncSession"
Cohesion: 0.29
Nodes (3): archive_old_masters(), get_decisions_context(), _get_master_file_id()

### Community 155 - "War Room Wall (every() interval registry)"
Cohesion: 0.67
Nodes (3): every() interval registry, show() page rotation, tick() clock

### Community 180 - "_worth_extracting"
Cohesion: 0.33
Nodes (3): _worth_extracting(), extract_remember_content(), is_recall_query()

### Community 181 - "_apply_learned_synonyms"
Cohesion: 0.33
Nodes (3): _apply_learned_synonyms(), _question_word_forms(), _word_forms()

### Community 183 - "_project_picker.html (optional project link dialog)"
Cohesion: 0.33
Nodes (5): _project_picker.html (optional project link dialog), Due-date postponement dialog (dueDlg), openDueDlg(), setDue(id), --wrs-bg / --wrs-fg theme variables

### Community 184 - "war_room_wall.html (TV wall display)"
Cohesion: 0.33
Nodes (3): war_room_wall.html (TV wall display), Hourly motto band (quote + AI line, capped in Python), Silent missions (no status report N days)

### Community 186 - "_ask"
Cohesion: 0.40
Nodes (3): _ask(), _ask_retrying(), get_last_llm_meta()

### Community 187 - "Keyboard shortcuts (J/K/Space/E/D/Esc//)"
Cohesion: 0.50
Nodes (4): clearSel(), focusRow(), Keyboard shortcuts (J/K/Space/E/D/Esc//), syncSel()

## Ambiguous Edges - Review These
- `shorten()` → `Hourly motto band (quote + AI line, capped in Python)`  [AMBIGUOUS]
  templates/war_room_wall.html · relation: references
- `checkAutoOpenRaci()` → `decision_review.html (AI decision review & publish)`  [AMBIGUOUS]
  templates/decisions.html · relation: references
- `reprocessFile() (unused: no rp- button rendered)` → `Log failure type (STRUCTURE / TERMINOLOGY)`  [AMBIGUOUS]
  templates/logs.html · relation: conceptually_related_to

## Knowledge Gaps
- **59 isolated node(s):** `XLSX sheet tab switcher script`, `refresh() in-place body swap`, `Gold answer set (LLM-judge eval)`, `_intro_splash.html (5s opening splash)`, `render() user search filter` (+54 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1305 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **40 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `User` connect `Dashboard Router & Report Models` to `Lessons Gateway & Access`, `Core Decision Data Models`, `Login & Auth`, `RACI Suggestion Service`, `Eval Cron & Weekly Reports`, `feedback_service.py`, `Lessons Admin`, `Mission`, `Decision`, `War Room Router`, `Telegram Bot Callbacks`, `Project Query Tools`, `Eval Loop Pages`, `Knowledge File Management`, `_weekly_eval_summary`, `Logs & Knowledge Inspector`, `Project Report Service`, `app_database`, `Pending Queue Service`, `Missions Report Service`, `Extraction Service`, `Learning Rules`, `user_ai_analysis`, `Projects #2`, `projects.py`, `Dashboard Domains`, `Insight Access`, `gather_report_data`, `Memory Service #2`, `Project Reports`, `Ask #2`, `Missions Report Service #2`, `missions_menu_service.py`, `Telegram Service`, `Main #2`, `llm_router.py`?**
  _High betweenness centrality (0.153) - this node is a cross-community bridge._
- **Are the 252 inferred relationships involving `User` (e.g. with `ask_correct()` and `ask_page()`) actually correct?**
  _`User` has 252 INFERRED edges - model-reasoned connections that need verification._
- **What connects `XLSX sheet tab switcher script`, `refresh() in-place body swap`, `Gold answer set (LLM-judge eval)` to the rest of the system?**
  _59 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Dashboard Router & Report Models` be split into smaller, more focused modules?**
  _Cohesion score 0.04283746556473829 - nodes in this community are weakly interconnected._
- **Why does `shorten()` connect `War Room Motto` to `war_room_wall.html (TV wall display)`?**
  _High betweenness centrality (0.128) - this node is a cross-community bridge._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `shorten()` and `Hourly motto band (quote + AI line, capped in Python)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `checkAutoOpenRaci()` and `decision_review.html (AI decision review & publish)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._