from coach import prompts


def _activities():
    return [
        {"start_date": "2026-04-18T10:00:00Z", "name": "Easy", "type": "Run",
         "distance_km": 6.0, "duration_min": 40, "avg_hr": 138},
        {"start_date": "2026-04-16T10:00:00Z", "name": "Long", "type": "Run",
         "distance_km": 18.0, "duration_min": 100, "avg_hr": 150},
    ]


def test_build_morning_prompt_contains_sections():
    p = prompts.build_morning_prompt(
        system_prompt="SYS",
        today_label="Saturday, April 18",
        recent=_activities(),
    )
    assert "SYS" in p
    assert "Saturday, April 18" in p
    assert "Easy" in p
    assert "Long" in p


def test_build_post_run_prompt_includes_current_activity():
    activity = {"id": 1, "name": "Tempo", "type": "Run",
                "distance_km": 8.0, "duration_min": 45, "avg_hr": 162,
                "start_date": "2026-04-18T10:00:00Z"}
    p = prompts.build_post_run_prompt(
        system_prompt="SYS",
        activity=activity,
        recent=_activities(),
    )
    assert "Tempo" in p
    assert "8.0 km" in p
    assert "45 min" in p
    assert "162 bpm" in p


def test_assemble_system_prompt_joins_with_headers():
    s = prompts.assemble_system_prompt(
        coach_voice="voice", training_plan="plan", athlete_context="ctx")
    assert "# Coach Voice\nvoice" in s
    assert "# Training Plan\nplan" in s
    assert "# Athlete Context\nctx" in s
