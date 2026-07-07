"""Platform-specific window integration: foreground detection + click-through.

These are best-effort and degrade gracefully: on sessions where they are not
available (e.g. a pure-Wayland Qt window with no X access), the overlay simply
stays interactive and the user can toggle behaviour manually.
"""
