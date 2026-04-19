from __future__ import annotations


def assemble_system_prompt(*, coach_voice: str, training_plan: str,
                           athlete_context: str) -> str:
    return "\n\n---\n\n".join([
        f"# Coach Voice\n{coach_voice}",
        f"# Training Plan\n{training_plan}",
        f"# Athlete Context\n{athlete_context}",
    ])


def _format_activity_line(a: dict) -> str:
    date = str(a.get("start_date", ""))[:10]
    hr = f", {a['avg_hr']} bpm" if a.get("avg_hr") else ""
    return (f"- {date}: {a.get('name', 'Activity')} — "
            f"{a.get('distance_km', '?')} km, "
            f"{a.get('duration_min', '?')} min{hr}")


def _format_recent_section(recent: list[dict], exclude_id: int | None = None) -> str:
    if not recent:
        return ""
    lines = ["## Recent Activity (Last 3 Weeks)"]
    for a in recent:
        if exclude_id is not None and a.get("id") == exclude_id:
            continue
        lines.append(_format_activity_line(a))
    return "\n".join(lines)


def build_morning_prompt(*, system_prompt: str, today_label: str,
                          recent: list[dict]) -> str:
    body = [f"Today is {today_label}. Write the morning check-in.", ""]
    recent_md = _format_recent_section(recent)
    if recent_md:
        body.append(recent_md)
    return system_prompt + "\n\n---\n\n" + "\n".join(body)


def build_post_run_prompt(*, system_prompt: str, activity: dict,
                          recent: list[dict]) -> str:
    hr_note = f", avg HR {activity['avg_hr']} bpm" if activity.get("avg_hr") else ""
    header = (
        f"{activity['name']} just finished — "
        f"{activity['distance_km']} km, {activity['duration_min']} min{hr_note}. "
        f"Write the post-run coaching message now."
    )
    lines = [header, "", "## This Run",
             f"- {activity['name']} ({activity['type']})",
             f"- Distance: {activity['distance_km']} km, "
             f"Duration: {activity['duration_min']} min"]
    if activity.get("avg_hr"):
        lines.append(f"- Avg HR: {activity['avg_hr']} bpm")
    recent_md = _format_recent_section(recent, exclude_id=activity.get("id"))
    if recent_md:
        lines += ["", recent_md]
    return system_prompt + "\n\n---\n\n" + "\n".join(lines)
