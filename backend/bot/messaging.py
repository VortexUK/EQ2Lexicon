"""The Discord I/O half of backend/bot/render.py's SendPlan."""

from __future__ import annotations

import io

import discord

from backend.bot.render import SendPlan


async def send_plan(followup: discord.Webhook, plan: SendPlan) -> None:
    """Send a SendPlan: fenced content directly, or the file attachment with
    the plan's header line (if any) as the message body."""
    if plan.file_text is None:
        # plan_code_block always sets content on the fenced branch.
        await followup.send(plan.content or "")
        return
    file = discord.File(io.BytesIO(plan.file_text.encode("utf-8")), filename=plan.filename)
    if plan.content is None:
        await followup.send(file=file)
    else:
        await followup.send(content=plan.content, file=file)
