import discord
from discord.ext import commands, tasks
import anthropic
import json
import os
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import re
from datetime import datetime

# ─── Konfiguration ────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "DEIN_DISCORD_TOKEN_HIER")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "DEIN_ANTHROPIC_KEY_HIER")
ADMIN_USER_IDS = []  # Optional: Nur bestimmte User dürfen Aufgaben geben

# EFT News Konfiguration
EFT_NEWS_CHANNEL = "eft-news"       # Name des Discord Channels
EFT_CHECK_INTERVAL = 30             # Minuten zwischen den Checks
POSTED_NEWS_FILE = "/tmp/posted_eft_news.json"

# EFT News Quellen (RSS Feeds)
EFT_RSS_FEEDS = [
    "https://www.reddit.com/r/EscapeFromTarkov/new/.rss",
    "https://www.escapefromtarkov.com/news/rss",
]

# ─── Bot Setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── News Tracking ────────────────────────────────────────────────────────────
def load_posted_news():
    try:
        with open(POSTED_NEWS_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_posted_news(posted: set):
    try:
        with open(POSTED_NEWS_FILE, "w") as f:
            json.dump(list(posted), f)
    except:
        pass

posted_news_ids = load_posted_news()

# ─── EFT News abrufen ─────────────────────────────────────────────────────────
async def fetch_eft_news():
    news_items = []
    headers = {"User-Agent": "Mozilla/5.0 EFT-Discord-Bot/1.0"}

    async with aiohttp.ClientSession(headers=headers) as session:
        for feed_url in EFT_RSS_FEEDS:
            try:
                async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        root = ET.fromstring(text)

                        ns = {"atom": "http://www.w3.org/2005/Atom"}
                        items = root.findall(".//item") or root.findall(".//atom:entry", ns)

                        for item in items[:5]:
                            title = (
                                item.findtext("title") or
                                item.findtext("atom:title", namespaces=ns) or ""
                            ).strip()

                            link_el = item.find("atom:link", ns)
                            link = (
                                item.findtext("link") or
                                (link_el.get("href") if link_el is not None else "") or ""
                            ).strip()

                            description = (
                                item.findtext("description") or
                                item.findtext("atom:summary", namespaces=ns) or
                                item.findtext("atom:content", namespaces=ns) or ""
                            ).strip()
                            description = re.sub(r'<[^>]+>', '', description)[:500]

                            if title and link:
                                news_items.append({
                                    "id": link,
                                    "title": title,
                                    "link": link,
                                    "description": description,
                                    "source": feed_url
                                })
            except Exception as e:
                print(f"Fehler beim Abrufen von {feed_url}: {e}")

    return news_items

# ─── News übersetzen ──────────────────────────────────────────────────────────
async def translate_news(title: str, description: str) -> tuple:
    try:
        response = claude.messages.create(
            model="claude-opus-4-5",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""Übersetze diese EFT News ins Deutsche.
Antworte NUR mit JSON: {{"title": "...", "summary": "..."}}

Titel: {title}
Beschreibung: {description}"""
            }]
        )
        text = response.content[0].text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("title", title), data.get("summary", description)
    except Exception as e:
        print(f"Übersetzungsfehler: {e}")
    return title, description

# ─── News posten ──────────────────────────────────────────────────────────────
async def post_news_to_discord(news: dict):
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=EFT_NEWS_CHANNEL)
        if not channel:
            continue

        title_de, summary_de = await translate_news(news["title"], news["description"])

        embed = discord.Embed(
            title=f"🎯 {title_de}",
            url=news["link"],
            description=summary_de or "Klicke auf den Titel für mehr Details.",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        source_name = "Reddit EFT" if "reddit" in news["source"] else "Escape from Tarkov"
        embed.set_footer(text=f"Quelle: {source_name} • Automatische Übersetzung")

        try:
            await channel.send(embed=embed)
            print(f"✅ News gepostet: {title_de}")
        except Exception as e:
            print(f"❌ Fehler beim Posten: {e}")

# ─── Automatischer News-Check ─────────────────────────────────────────────────
@tasks.loop(minutes=EFT_CHECK_INTERVAL)
async def check_eft_news():
    global posted_news_ids
    print(f"🔍 Prüfe EFT News... ({datetime.now().strftime('%H:%M')})")

    try:
        news_items = await fetch_eft_news()
        new_count = 0

        for item in news_items:
            if item["id"] not in posted_news_ids:
                await post_news_to_discord(item)
                posted_news_ids.add(item["id"])
                new_count += 1
                await asyncio.sleep(2)

        save_posted_news(posted_news_ids)
        print(f"📰 {new_count} neue News gepostet!" if new_count > 0 else "ℹ️ Keine neuen News.")
    except Exception as e:
        print(f"❌ Fehler beim News-Check: {e}")

# ─── Discord Tools für Claude ─────────────────────────────────────────────────
TOOLS = [
    {
        "name": "send_message",
        "description": "Sendet eine Nachricht in einen Discord-Channel",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {"type": "string"},
                "message": {"type": "string"}
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
                "username": {"type": "string"},
                "message": {"type": "string"}
            },
            "required": ["username", "message"]
        }
    },
    {
        "name": "create_channel",
        "description": "Erstellt einen neuen Text-Channel",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {"type": "string"},
                "category": {"type": "string"},
                "topic": {"type": "string"}
            },
            "required": ["channel_name"]
        }
    },
    {
        "name": "delete_channel",
        "description": "Löscht einen Channel",
        "input_schema": {
            "type": "object",
            "properties": {"channel_name": {"type": "string"}},
            "required": ["channel_name"]
        }
    },
    {
        "name": "create_role",
        "description": "Erstellt eine neue Rolle",
        "input_schema": {
            "type": "object",
            "properties": {
                "role_name": {"type": "string"},
                "color": {"type": "string"},
                "mentionable": {"type": "boolean"}
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
                "username": {"type": "string"},
                "role_name": {"type": "string"}
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
                "username": {"type": "string"},
                "role_name": {"type": "string"}
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
                "username": {"type": "string"},
                "reason": {"type": "string"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "ban_member",
        "description": "Bannt einen User",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "reason": {"type": "string"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "list_members",
        "description": "Listet alle Mitglieder auf",
        "input_schema": {
            "type": "object",
            "properties": {"role_filter": {"type": "string"}}
        }
    },
    {
        "name": "list_channels",
        "description": "Listet alle Channels auf",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "check_news_now",
        "description": "Prüft sofort auf neue EFT News",
        "input_schema": {"type": "object", "properties": {}}
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
            member = discord.utils.find(lambda m: m.name.lower() == tool_input["username"].lower(), guild.members)
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
                color = discord.Color(int(tool_input["color"].lstrip("#"), 16))
            role = await guild.create_role(name=tool_input["role_name"], color=color, mentionable=tool_input.get("mentionable", False))
            return f"✅ Rolle '{role.name}' erstellt."

        elif tool_name == "assign_role":
            member = discord.utils.find(lambda m: m.name.lower() == tool_input["username"].lower(), guild.members)
            role = discord.utils.get(guild.roles, name=tool_input["role_name"])
            if not member: return f"❌ User nicht gefunden."
            if not role: return f"❌ Rolle nicht gefunden."
            await member.add_roles(role)
            return f"✅ Rolle '{role.name}' an {member.name} vergeben."

        elif tool_name == "remove_role":
            member = discord.utils.find(lambda m: m.name.lower() == tool_input["username"].lower(), guild.members)
            role = discord.utils.get(guild.roles, name=tool_input["role_name"])
            if not member: return f"❌ User nicht gefunden."
            if not role: return f"❌ Rolle nicht gefunden."
            await member.remove_roles(role)
            return f"✅ Rolle '{role.name}' von {member.name} entfernt."

        elif tool_name == "kick_member":
            member = discord.utils.find(lambda m: m.name.lower() == tool_input["username"].lower(), guild.members)
            if not member: return f"❌ User nicht gefunden."
            await member.kick(reason=tool_input.get("reason", "Kein Grund"))
            return f"✅ {member.name} wurde gekickt."

        elif tool_name == "ban_member":
            member = discord.utils.find(lambda m: m.name.lower() == tool_input["username"].lower(), guild.members)
            if not member: return f"❌ User nicht gefunden."
            await member.ban(reason=tool_input.get("reason", "Kein Grund"))
            return f"✅ {member.name} wurde gebannt."

        elif tool_name == "list_members":
            members = guild.members
            if tool_input.get("role_filter"):
                role = discord.utils.get(guild.roles, name=tool_input["role_filter"])
                if role:
                    members = [m for m in members if role in m.roles]
            return f"👥 Mitglieder ({len(members)}): {', '.join(m.name for m in members)}"

        elif tool_name == "list_channels":
            channels = [f"#{c.name}" for c in guild.text_channels]
            return f"📋 Channels: {', '.join(channels)}"

        elif tool_name == "check_news_now":
            await check_eft_news()
            return "✅ News-Check ausgeführt!"

        else:
            return f"❌ Unbekanntes Tool: {tool_name}"

    except discord.Forbidden:
        return f"❌ Keine Berechtigung für '{tool_name}'."
    except Exception as e:
        return f"❌ Fehler: {str(e)}"

# ─── Claude Aufgaben-Verarbeitung ─────────────────────────────────────────────
async def process_task_with_claude(task: str, guild: discord.Guild, ctx) -> tuple:
    server_info = f"Server: {guild.name}, Channels: {', '.join(c.name for c in guild.text_channels[:15])}"
    messages = [{"role": "user", "content": task}]
    results = []

    response = claude.messages.create(
        model="claude-opus-4-5",
        max_tokens=2048,
        system=f"Du bist ein Discord-Bot-Assistent. {server_info}. Antworte auf Deutsch.",
        tools=TOOLS,
        messages=messages
    )

    while response.stop_reason == "tool_use":
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await execute_tool(block.name, block.input, guild, ctx.channel)
                results.append(f"**{block.name}**: {result}")
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        response = claude.messages.create(
            model="claude-opus-4-5",
            max_tokens=2048,
            system=f"Discord-Bot-Assistent. Server: {guild.name}. Antworte auf Deutsch.",
            tools=TOOLS,
            messages=messages
        )

    final_text = next((b.text for b in response.content if hasattr(b, "text")), "")
    return final_text, results

# ─── Bot Events & Commands ────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    if not check_eft_news.is_running():
        check_eft_news.start()
    print(f"📰 EFT News-Check gestartet (alle {EFT_CHECK_INTERVAL} Min.)")

@bot.command(name="aufgabe", aliases=["task", "do"])
async def aufgabe(ctx, *, aufgabe_text: str):
    if ADMIN_USER_IDS and ctx.author.id not in ADMIN_USER_IDS:
        await ctx.reply("❌ Keine Berechtigung.")
        return
    if not ctx.guild:
        await ctx.reply("❌ Nur in einem Server nutzbar.")
        return

    loading_msg = await ctx.reply("🤖 Verarbeite Aufgabe...")
    try:
        final_text, actions = await process_task_with_claude(aufgabe_text, ctx.guild, ctx)
        embed = discord.Embed(title="📋 Aufgabe ausgeführt", description=f"**Aufgabe:** {aufgabe_text}", color=discord.Color.green())
        if actions:
            embed.add_field(name="🔧 Aktionen", value="\n".join(actions[:10]), inline=False)
        if final_text:
            embed.add_field(name="💬 Claude sagt", value=final_text[:1024], inline=False)
        embed.set_footer(text=f"Angefragt von {ctx.author.name}")
        await loading_msg.edit(content=None, embed=embed)
    except Exception as e:
        await loading_msg.edit(content=f"❌ Fehler: {str(e)}")

@bot.command(name="eft_news")
async def eft_news_cmd(ctx):
    """Sofortiger EFT News Check"""
    msg = await ctx.reply("🔍 Prüfe EFT News...")
    await check_eft_news()
    await msg.edit(content="✅ News-Check abgeschlossen!")

@bot.command(name="hilfe_bot")
async def hilfe_bot(ctx):
    embed = discord.Embed(title="🤖 Bot Hilfe", color=discord.Color.blue())
    embed.add_field(name="📝 Aufgaben", value="`!aufgabe [Beschreibung]`", inline=False)
    embed.add_field(name="📰 EFT News", value=f"`!eft_news` - Sofort prüfen\nAutomatisch alle {EFT_CHECK_INTERVAL} Min.", inline=False)
    await ctx.send(embed=embed)

# ─── Start ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
