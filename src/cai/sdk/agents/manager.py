"""
Base AgentManager utilities shared across agent managers.

This module provides the recursive `sync_models` implementation so that
concrete manager implementations can inherit and reuse it.
"""

from typing import Any, Optional


class AgentManager:
    """Base AgentManager mixin providing shared utilities."""

    def sync_models(self, new_model: str, target_agent: Optional[Any] = None):
        """Synchronize model settings across managed agents.

        This updates the `model` attribute of agent instances managed by
        the manager (the active agent and any parallel agents), and
        recursively updates any handoff agents referenced by those
        agents. If `target_agent` is provided, only that agent and its
        handoffs are updated.
        """
        visited = set()

        def _update(agent):
            if agent is None:
                return
            # Avoid revisiting the same agent object
            aid = id(agent)
            if aid in visited:
                return
            visited.add(aid)

            try:
                if hasattr(agent, "model") and hasattr(agent.model, "model"):
                    # Apply the model string
                    agent.model.model = new_model
                    if hasattr(agent.model, "agent_name"):
                        try:
                            agent.model.agent_name = getattr(agent, "name", "")
                        except Exception:
                            pass

                    # Reset client to force recreation on next use
                    if hasattr(agent.model, "_client"):
                        try:
                            agent.model._client = None
                        except Exception:
                            pass

                    # Reset converter caches if present
                    if hasattr(agent.model, "_converter"):
                        try:
                            conv = agent.model._converter
                            if hasattr(conv, "recent_tool_calls"):
                                try:
                                    conv.recent_tool_calls.clear()
                                except Exception:
                                    pass
                            if hasattr(conv, "tool_outputs"):
                                try:
                                    conv.tool_outputs.clear()
                                except Exception:
                                    pass
                        except Exception:
                            pass
            except Exception:
                # Best-effort: don't let model sync raise
                pass

            # Recurse into handoffs if available
            try:
                if hasattr(agent, "handoffs"):
                    for handoff_item in agent.handoffs:
                        # Handoff objects created via handoff() often expose
                        # an `on_invoke_handoff` closure which may contain
                        # the actual agent instance in its closure cells.
                        if hasattr(handoff_item, "on_invoke_handoff"):
                            try:
                                fn = handoff_item.on_invoke_handoff
                                if hasattr(fn, "__closure__") and fn.__closure__:
                                    for cell in fn.__closure__:
                                        cell_contents = getattr(cell, "cell_contents", None)
                                        if (
                                            cell_contents
                                            and hasattr(cell_contents, "model")
                                            and hasattr(cell_contents, "name")
                                        ):
                                            _update(cell_contents)
                                            break
                            except Exception:
                                pass
                        elif hasattr(handoff_item, "model"):
                            # Direct agent reference stored in handoff
                            try:
                                _update(handoff_item)
                            except Exception:
                                pass
            except Exception:
                pass

        # If a specific agent was requested, update only it
        if target_agent is not None:
            _update(target_agent)
            return

        # Otherwise update the active agent and any parallel agents we manage
        try:
            if getattr(self, "_active_agent", None):
                active_ref = self._active_agent
                # active_ref may be a weakref
                try:
                    active = active_ref() if callable(active_ref) else active_ref
                except Exception:
                    active = active_ref
                _update(active)
        except Exception:
            pass

        # Update parallel agents
        for aid, agent_ref in list(getattr(self, "_parallel_agents", {}).items()):
            try:
                if agent_ref:
                    a = agent_ref() if callable(agent_ref) else agent_ref
                    _update(a)
            except Exception:
                pass
