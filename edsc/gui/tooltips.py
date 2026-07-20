"""Rich tooltip formatting for station and colonization search results."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape

from ..station_planning import StationResult
from ..systems import CLAIM_RANGE_LY, SystemResult
from ..time_utils import parse_timestamp
from . import icons, theme

_UNKNOWN = "?"


def _quantity(value: int, unit: str) -> str:
    return f"{value} {unit}{'' if value == 1 else 's'}"


def elapsed(duration: timedelta) -> str:
    for threshold, divisor, unit in (
        (365, 365, "year"),
        (30, 30, "month"),
        (7, 7, "week"),
        (1, 1, "day"),
    ):
        if duration.days >= threshold:
            return _quantity(duration.days // divisor, unit)

    hours, remainder = divmod(duration.seconds, 3600)
    if hours:
        return _quantity(hours, "hour")
    minutes = remainder // 60
    return _quantity(minutes, "minute") if minutes else "just now"


def freshness_text(updated: str) -> str:
    updated_at = parse_timestamp(updated)
    if updated_at is None:
        return "unknown"
    return f"{elapsed(datetime.now(timezone.utc) - updated_at)} ago"


def station_tooltip(station: StationResult, copy_hint: bool) -> str:
    kind = (
        "Planetary"
        if station.is_planetary
        else "Carrier"
        if station.is_carrier
        else "Orbital"
    )

    def stock_entry(name: str) -> tuple[str, str, bool]:
        demand = station.demand_by_name.get(name, 0)
        if demand <= 0:
            return escape(name), "", True
        stocked = min(
            100,
            round(100 * station.supply_by_name.get(name, 0) / demand),
        )
        return escape(name), f"{stocked}%", stocked >= 100

    stock = [stock_entry(name) for name in sorted(station.matched)]
    missing = [escape(name) for name in sorted(station.missing)]
    title, owner = escape(station.name), escape(station.owner)
    if station.is_carrier and owner:
        title, owner = f"{title} · {owner}", ""
    header = theme.tooltip_station_header(
        icons.station_icon_html(station, theme.tooltip_icon_px()),
        title,
        f"Market data from: {escape(freshness_text(station.market_updated_at))}",
        escape(station.system),
        f"{kind} · {escape(station.station_type or 'Station')}",
        owner=owner,
    )
    hint = "<br>Click to copy the system name" if copy_hint else ""
    return f"{header}{theme.tooltip_stock_table(stock, missing)}{hint}"


def format_ls(distance: float) -> str:
    return f"{distance / 1000:,.1f}k" if distance >= 1000 else f"{distance:,.0f}"


def body_breakdown(system: SystemResult) -> str:
    if not system.bodies:
        return "<br>" + theme.tooltip_note(
            "No body data on Spansh - system may be unscanned.",
            theme.TEXT_DIM,
        )

    def entries(body_type: str) -> list[str]:
        counts = Counter(
            body.subtype or body.type or "Unknown"
            for body in system.bodies
            if body.type == body_type
        )
        return [
            f"{escape(subtype)} ×{count}" if count > 1 else escape(subtype)
            for subtype, count in counts.most_common()
        ]

    content = theme.tooltip_pair_table(
        "Stars",
        entries("Star"),
        "Planets",
        entries("Planet"),
    )
    if system.terraformable_count:
        count = system.terraformable_count
        content += theme.tooltip_note(
            f"{count} terraformable bod{'y' if count == 1 else 'ies'}",
            theme.DONE,
        )
    if system.body_count is not None and system.body_count > len(system.bodies):
        content += "<br>" + theme.tooltip_note(
            f"{len(system.bodies)} of {system.body_count} bodies scanned",
            theme.TEXT_DIM,
        )
    return content


def system_tooltip(system: SystemResult, copy_hint: bool) -> str:
    stars = str(system.star_count) if system.star_count is not None else _UNKNOWN
    bodies = (
        f"{system.known_body_count:,}"
        if system.known_body_count is not None
        else _UNKNOWN
    )
    furthest = (
        f"{format_ls(system.furthest_ls)} Ls"
        if system.furthest_ls is not None
        else f"{_UNKNOWN} Ls"
    )
    summary = f"{stars} stars · {bodies} bodies · furthest {furthest}"
    if system.steps == 1:
        steps = "Claimable now: populated space is within claim range"
    elif system.steps is not None:
        bridges = system.steps - 1
        steps = (
            f"Needs {bridges} bridge colon{'y' if bridges == 1 else 'ies'} "
            f"({CLAIM_RANGE_LY:.0f} Ly each)"
        )
    else:
        steps = "Beyond colonization reach"

    raven_icon = (
        icons.powered_logo_html(
            "raven",
            theme.tooltip_icon_px(),
            theme.tooltip_icon_px(),
            alt="Confirmed by Raven Colonial",
        )
        if system.verified is True
        else ""
    )
    header = theme.tooltip_station_header(
        "",
        escape(system.name),
        f"Body data from: {escape(freshness_text(system.updated_at))}",
        escape(summary),
        escape(steps),
        corner_icon=raven_icon,
    )
    if system.agent is not None:
        location = f" ({escape(system.agent.system)})" if system.agent.system else ""
        verdict = (
            "in claim range"
            if system.agent.distance_ly <= CLAIM_RANGE_LY
            else f"beyond the {CLAIM_RANGE_LY:.0f} Ly claim limit"
        )
        colour = theme.DONE if system.claimable else theme.ORANGE
        agent = (
            f"Agent: {escape(system.agent.name)}{location} at "
            f"{system.agent.distance_ly:,.1f} Ly - {verdict}"
        )
    elif system.agent_error:
        colour = theme.TEXT_DIM
        agent = "Agent lookup failed - refresh to retry"
    else:
        colour = theme.TEXT_DIM
        agent = "No colonisation contact found nearby"

    hint = "<br>Click to copy the system name" if copy_hint else ""
    return (
        f"{header}<br>{theme.tooltip_note(agent, colour)}"
        f"{body_breakdown(system)}{hint}"
    )
