"""Agent info helpers extracted from common.py.

Provides `_get_agent_token_info()` which returns a dict of lightweight
agent display and accounting information. Kept as a thin compatibility
helper so callers in `common` can import it.
"""

from typing import Dict


def _get_agent_token_info() -> Dict:
    """Get current agent's token information from the active model instance.

    Returns a dict with keys matching the previous implementation in
    `common.py` so existing call sites need no changes.
    """
    try:
        from cai.sdk.agents.models.openai_chatcompletions import get_current_active_model

        # First try to get the current active model (set during execution)
        model = get_current_active_model()

        if model:
            # Get display name with ID (e.g., "Red Team Agent [P1]")
            if hasattr(model, "get_full_display_name"):
                display_name = model.get_full_display_name()
            elif hasattr(model, "agent_name"):
                # Include [P1] only if we have a valid agent_id
                if hasattr(model, "agent_id") and model.agent_id:
                    display_name = f"{model.agent_name} [{model.agent_id}]"
                else:
                    display_name = model.agent_name
            else:
                display_name = "Agent"

            return {
                "agent_name": display_name,
                "agent_id": getattr(model, "agent_id", None),
                "interaction_counter": getattr(model, "interaction_counter", 0),
                "total_input_tokens": getattr(model, "total_input_tokens", 0),
                "total_output_tokens": getattr(model, "total_output_tokens", 0),
                "total_reasoning_tokens": getattr(model, "total_reasoning_tokens", 0),
                "total_cost": getattr(model, "total_cost", 0.0),
            }

        # Fallback: Try to get from the most recent instance in the registry
        from cai.sdk.agents.models.openai_chatcompletions import ACTIVE_MODEL_INSTANCES

        if ACTIVE_MODEL_INSTANCES:
            latest_key = max(ACTIVE_MODEL_INSTANCES.keys(), key=lambda x: x[1])
            model_ref = ACTIVE_MODEL_INSTANCES[latest_key]
            model = model_ref() if model_ref else None

            if model:
                if hasattr(model, "get_full_display_name"):
                    display_name = model.get_full_display_name()
                elif hasattr(model, "agent_name"):
                    if hasattr(model, "agent_id") and model.agent_id:
                        display_name = f"{model.agent_name} [{model.agent_id}]"
                    else:
                        display_name = model.agent_name
                else:
                    display_name = "Agent"

                return {
                    "agent_name": display_name,
                    "agent_id": getattr(model, "agent_id", None),
                    "interaction_counter": getattr(model, "interaction_counter", 0),
                    "total_input_tokens": getattr(model, "total_input_tokens", 0),
                    "total_output_tokens": getattr(model, "total_output_tokens", 0),
                    "total_reasoning_tokens": getattr(model, "total_reasoning_tokens", 0),
                    "total_cost": getattr(model, "total_cost", 0.0),
                }
    except Exception:
        # Swallow errors and return default stub
        pass

    return {
        "agent_name": "Agent",
        "agent_id": None,
        "interaction_counter": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_reasoning_tokens": 0,
        "total_cost": 0.0,
    }
