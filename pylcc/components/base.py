"""
Base component model for pyLCC layout devices.
Provides unified lifecycle, event registration, and CDI binding.
"""

from ..event_id import EventID


class BaseComponent:
    """Base class for all layout components (Turnout, Sensor, Signal, Light, etc.)."""
    __slots__ = ("name", "on_change", "node")
    def __init__(self, name: str = "", on_change=None):
        self.name = name
        self.on_change = on_change
        self.node = None

    def bind(self, node):
        """Bind component to a Node and register its events."""
        self.node = node
        self.setup_events()

    def setup_events(self):
        """Override to register consumers and producers with the node's EventManager."""
        pass

    async def _notify_change(self, state):
        """Invoke on_change callback if provided."""
        if self.on_change:
            try:
                res = self.on_change(state)
                if hasattr(res, "__await__"):
                    await res
            except Exception as e:
                # Debug logging / resilient error handling
                pass
