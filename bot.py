import discord
from discord.ext import commands
import anthropic
import json
import os
import asyncio

# ─── Konfiguration ────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "DEIN_DISCORD_TOKEN_HIER")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "DEIN_ANTHROPIC_KEY_HIER")
TASK_PREFIX = "!aufgabe"          # Befehl für Aufgaben
ADMIN_USER_IDS = []               # Optional: Nur bestimmte User dürfen Aufgaben geben
                                  # Beispiel: [123456789, 987654321]

# ─── Bot Setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Discord Tools für Claude ─────────────────────────────────────────────────
TOOLS = [
    {
        "name": "send_message",
        "description": "Sendet eine Nachricht in einen bestimmten Discord-Channel",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {"type": "string", "description": "Name des Channels (z.B. 'general')"},
                "message": {"type": "string", "description": "Der Nachrichtentext"}
            },
            "required": ["channel_name", "message"]
        }
    },
    {
        "name": "send_dm",
        "description": "Sendet eine Direktnachricht an einen User",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Discord-Username des Empfängers"},
                "message": {"type": "string", "description": "Der Nachrichtentext"}
            },
            "required": ["username", "message"]
        }
    },
    {
        "name": "create_channel",
        "description": "Erstellt einen neuen Text-Channel auf dem Server",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {"type": "string", "description": "Name des neuen Channels"},
                "category": {"type": "string", "description": "Optionale Kategorie, in der der Channel erstellt wird"},
                "topic": {"type": "string", "description": "Optionales Thema/Beschreibung des Channels"}
            },
            "required": ["channel_name"]
        }
    },
    {
        "name": "delete_channel",
        "description": "Löscht einen Channel vom Server",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {"type": "string", "description": "Name des zu löschenden Channels"}
            },
            "required": ["channel_name"]
        }
    },
    {
        "name": "create_role",
        "description": "Erstellt eine neue Rolle auf dem Server",
        "input_schema": {
            "type": "object",
            "properties": {
                "role_name": {"type": "string", "description": "Name der neuen Rolle"},
                "color": {"type": "string", "description": "Farbe der Rolle als Hex-Code (z.B. '#FF0000' für Rot)"},
                "mentionable": {"type": "boolean", "description": "Ob die Rolle erwähnt werden kann"}
            },
            "required": ["role_name"]
        }
    },
    {
        "name": "assign_role",
        "description": "Weist einem User eine Rolle zu",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Discord-Username"},
                "role_name": {"type": "string", "description": "Name der Rolle"}
            },
            "required": ["username", "role_name"]
        }
    },
    {
        "name": "remove_role",
        "description": "Entfernt eine Rolle von einem User",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Discord-Username"},
                "role_name": {"type": "string", "description": "Name der Rolle"}
            },
            "required": ["username", "role_name"]
        }
    },
    {
        "name": "kick_member",
        "description": "Kickt einen User vom Server",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Discord-Username des zu kickenden Users"},
                "reason": {"type": "string", "description": "Grund für den Kick"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "ban_member",
        "description": "Bannt einen User vom Server",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Discord-Username des zu bannenden Users"},
                "reason": {"type": "string", "description": "Grund für den Ban"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "pin_message",
        "description": "Pinnt eine Nachricht in einem Channel an",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {"type": "string", "description": "Name des Channels"},
                "message_content": {"type": "string", "description": "Inhalt der zu pinnenden Nachricht (zur Identifizierung)"}
            },
            "required": ["channel_name", "message_content"]
        }
    },
    {
        "name": "list_members",
        "description": "Listet alle Mitglieder des Servers auf",
        "input_schema": {
            "type": "object",
            "properties": {
                "role_filter": {"type": "string", "description": "Optional: Nur Mitglieder mit dieser Rolle anzeigen"}
            }
        }
    },
    {
        "name": "list_channels",
        "description": "Listet alle Channels des Servers auf",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
]

# ─── Tool-Ausführung ──────────────────────────────────────────────────────────
async def execute_tool(tool_name: str, tool_input: dict, guild: discord.Guild, ctx_channel) -> str:
    try:
        if tool_name == "send_message":
            channel = discord.utils.get(guild.text_channels, name=tool_input["channel_name"])
            if not channel:
                return f"❌ Channel '{tool_input['channel_name']}' nicht gefunden."
            await channel.send(tool_input["message"])
            return f"✅ Nachricht in #{tool_input['channel_name']} gesendet."

        elif tool_name == "send_dm":
            member = discord.utils.find(
                lambda m: m.name.lower() == tool_input["username"].lower() or
                          str(m).lower() == tool_input["username"].lower(),
                guild.members
            )
            if not member:
                return f"❌ User '{tool_input['username']}' nicht gefunden."
            await member.send(tool_input["message"])
            return f"✅ DM an {member.name} gesendet."

        elif tool_name == "create_channel":
            existing = discord.utils.get(guild.text_channels, name=tool_input["channel_name"])
            if existing:
                return f"⚠️ Channel '{tool_input['channel_name']}' existiert bereits."
            category = None
            if tool_input.get("category"):
                category = discord.utils.get(guild.categories, name=tool_input["category"])
            channel = await guild.create_text_channel(
                tool_input["channel_name"],
                category=category,
                topic=tool_input.get("topic", "")
            )
            return f"✅ Channel #{channel.name} erstellt."

        elif tool_name == "delete_channel":
            channel = discord.utils.get(guild.text_channels, name=tool_input["channel_name"])
            if not channel:
                return f"❌ Channel '{tool_input['channel_name']}' nicht gefunden."
            await channel.delete()
            return f"✅ Channel '{tool_input['channel_name']}' gelöscht."

        elif tool_name == "create_role":
            color = discord.Color.default()
            if tool_input.get("color"):
                hex_color = tool_input["color"].lstrip("#")
                color = discord.Color(int(hex_color, 16))
            role = await guild.create_role(
                name=tool_input["role_name"],
                color=color,
                mentionable=tool_input.get("mentionable", False)
            )
            return f"✅ Rolle '{role.name}' erstellt."

        elif tool_name == "assign_role":
            member = discord.utils.find(
                lambda m: m.name.lower() == tool_input["username"].lower(),
                guild.members
            )
            role = discord.utils.get(guild.roles, name=tool_input["role_name"])
            if not member:
                return f"❌ User '{tool_input['username']}' nicht gefunden."
            if not role:
                return f"❌ Rolle '{tool_input['role_name']}' nicht gefunden."
            await member.add_roles(role)
            return f"✅ Rolle '{role.name}' an {member.name} vergeben."

        elif tool_name == "remove_role":
            member = discord.utils.find(
                lambda m: m.name.lower() == tool_input["username"].lower(),
                guild.members
            )
            role = discord.utils.get(guild.roles, name=tool_input["role_name"])
            if not member:
                return f"❌ User '{tool_input['username']}' nicht gefunden."
            if not role:
                return f"❌ Rolle '{tool_input['role_name']}' nicht gefunden."
            await member.remove_roles(role)
            return f"✅ Rolle '{role.name}' von {member.name} entfernt."

        elif tool_name == "kick_member":
            member = discord.utils.find(
                lambda m: m.name.lower() == tool_input["username"].lower(),
                guild.members
            )
            if not member:
                return f"❌ User '{tool_input['username']}' nicht gefunden."
            await member.kick(reason=tool_input.get("reason", "Kein Grund angegeben"))
            return f"✅ {member.name} wurde gekickt."

        elif tool_name == "ban_member":
            member = discord.utils.find(
                lambda m: m.name.lower() == tool_input["username"].lower(),
                guild.members
            )
            if not member:
                return f"❌ User '{tool_input['username']}' nicht gefunden."
            await member.ban(reason=tool_input.get("reason", "Kein Grund angegeben"))
            return f"✅ {member.name} wurde gebannt."

        elif tool_name == "pin_message":
            channel = discord.utils.get(guild.text_channels, name=tool_input["channel_name"])
            if not channel:
                return f"❌ Channel '{tool_input['channel_name']}' nicht gefunden."
            async for msg in channel.history(limit=50):
                if tool_input["message_content"].lower() in msg.content.lower():
                    await msg.pin()
                    return f"✅ Nachricht in #{channel.name} angepinnt."
            return f"❌ Nachricht nicht gefunden in #{channel.name}."

        elif tool_name == "list_members":
            members = guild.members
            if tool_input.get("role_filter"):
                role = discord.utils.get(guild.roles, name=tool_input["role_filter"])
                if role:
                    members = [m for m in members if role in m.roles]
            names = [f"{m.name}#{m.discriminator}" if m.discriminator != "0" else m.name for m in members]
            return f"👥 Mitglieder ({len(names)}): {', '.join(names)}"

        elif tool_name == "list_channels":
            channels = [f"#{c.name}" for c in guild.text_channels]
            return f"📋 Channels ({len(channels)}): {', '.join(channels)}"

        else:
            return f"❌ Unbekanntes Tool: {tool_name}"

    except discord.Forbidden:
        return f"❌ Keine Berechtigung für '{tool_name}'. Bot benötigt mehr Rechte."
    except Exception as e:
        return f"❌ Fehler bei '{tool_name}': {str(e)}"

# ─── Claude Aufgaben-Verarbeitung ─────────────────────────────────────────────
async def process_task_with_claude(task: str, guild: discord.Guild, ctx) -> str:
    server_info = f"""
Server: {guild.name}
Channels: {', '.join([c.name for c in guild.text_channels[:20]])}
Rollen: {', '.join([r.name for r in guild.roles if r.name != '@everyone'][:15])}
Mitglieder: {guild.member_count}
"""

    messages = [{"role": "user", "content": task}]
    results = []

    response = claude.messages.create(
        model="claude-opus-4-5",
        max_tokens=2048,
        system=f"""Du bist ein Discord-Bot-Assistent. Du führst Aufgaben auf einem Discord-Server aus.

Server-Informationen:
{server_info}

Wichtige Regeln:
- Führe NUR die explizit angeforderten Aufgaben aus
- Sei präzise und effizient
- Frage nach, wenn eine Aufgabe unklar ist
- Nutze die verfügbaren Tools um Aufgaben auszuführen
- Antworte auf Deutsch""",
        tools=TOOLS,
        messages=messages
    )

    while response.stop_reason == "tool_use":
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await execute_tool(block.name, block.input, guild, ctx.channel)
                results.append(f"**{block.name}**: {result}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        response = claude.messages.create(
            model="claude-opus-4-5",
            max_tokens=2048,
            system=f"""Du bist ein Discord-Bot-Assistent. Du führst Aufgaben auf einem Discord-Server aus.
Server: {guild.name} | Antworte auf Deutsch""",
            tools=TOOLS,
            messages=messages
        )

    final_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            final_text = block.text
            break

    return final_text, results

# ─── Bot Events & Commands ────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot ist online: {bot.user}")
    print(f"📡 Verbunden mit {len(bot.guilds)} Server(n)")

@bot.command(name="aufgabe", aliases=["task", "do"])
async def aufgabe(ctx, *, aufgabe_text: str):
    """Gibt dem Bot eine Aufgabe auf Discord auszuführen"""

    # Admin-Check (optional)
    if ADMIN_USER_IDS and ctx.author.id not in ADMIN_USER_IDS:
        await ctx.reply("❌ Du hast keine Berechtigung, Aufgaben zu erteilen.")
        return

    if not ctx.guild:
        await ctx.reply("❌ Dieser Befehl funktioniert nur in einem Server.")
        return

    # Lade-Nachricht
    loading_msg = await ctx.reply("🤖 Verarbeite Aufgabe...")

    try:
        final_text, actions = await process_task_with_claude(aufgabe_text, ctx.guild, ctx)

        # Antwort aufbauen
        embed = discord.Embed(
            title="📋 Aufgabe ausgeführt",
            description=f"**Aufgabe:** {aufgabe_text}",
            color=discord.Color.green()
        )

        if actions:
            embed.add_field(
                name="🔧 Ausgeführte Aktionen",
                value="\n".join(actions[:10]),
                inline=False
            )

        if final_text:
            embed.add_field(
                name="💬 Claude sagt",
                value=final_text[:1024],
                inline=False
            )

        embed.set_footer(text=f"Angefragt von {ctx.author.name}")
        await loading_msg.edit(content=None, embed=embed)

    except Exception as e:
        await loading_msg.edit(content=f"❌ Fehler: {str(e)}")

@bot.command(name="hilfe_bot")
async def hilfe_bot(ctx):
    """Zeigt alle verfügbaren Aufgaben"""
    embed = discord.Embed(
        title="🤖 Discord Bot - Hilfe",
        description="Schreibe `!aufgabe [Aufgabenbeschreibung]`",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="📝 Beispiele",
        value="""
`!aufgabe Sende 'Hallo!' in den general Channel`
`!aufgabe Erstelle einen Channel namens 'ankündigungen'`
`!aufgabe Gib user123 die Rolle 'Moderator'`
`!aufgabe Schreibe eine DM an max mit 'Willkommen!'`
`!aufgabe Liste alle Mitglieder auf`
`!aufgabe Erstelle eine Rolle namens 'VIP' in blau`
        """,
        inline=False
    )
    embed.add_field(
        name="⚡ Verfügbare Aktionen",
        value="Nachricht senden, DM senden, Channel erstellen/löschen, Rolle erstellen/vergeben/entfernen, Mitglieder listen, Nachrichten pinnen, Kick/Ban",
        inline=False
    )
    await ctx.send(embed=embed)

# ─── Start ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
