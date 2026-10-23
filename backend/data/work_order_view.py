"""Expose approved facts as a versioned view while preserving the legacy text."""
import json

from core.harness import check_output_forbidden_words


def build(values: dict) -> dict | None:
    components = values.get("involved_components")
    if not values.get("work_order") or not components:
        return None  # Old checkpoints retain their original text; never invent missing facts.
    blocks = []
    for component in components:
        part = values.get("parts_status", {}).get(component, {})
        procurement = None if not part or "error" in part else {
            key: part.get(key) for key in ("part_no", "on_hand", "min_stock", "lead_time_days", "needs_procurement", "eol_status", "eol_date")
        }
        blocks.append({"component": component,
                       "evidence": values.get("component_evidence", {}).get(component, ""),
                       "manual": values.get("component_manuals", {}).get(component, ""),
                       "action": values.get("component_actions", {}).get(component, ""),
                       "steps": values.get("component_steps", {}).get(component, []), "procurement": procurement})
    result = {"version": 1, "machine_id": values.get("machine_id"), "severity": values.get("severity"),
              "priority": values.get("priority"), "recommend_shutdown": values.get("recommend_shutdown"),
              "priority_reasons": values.get("priority_reasons", []), "components": blocks}
    check_output_forbidden_words(json.dumps(result, ensure_ascii=False))
    return result
