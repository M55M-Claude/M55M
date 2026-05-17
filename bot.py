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
ADMIN_USER_IDS = []

# Channel Namen
EFT_NEWS_CHANNEL        = "eft-news"
EFT_CODES_CHANNEL       = "eft-codes"
EFT_PATCHNOTES_CHANNEL  = "eft-patchnotes"
EFT_RELEASE_CHANNEL     = "eft-release"
ARMA_KOTH_CODES_CHANNEL = "arma-reforger-koth-codes"

# Intervalle (Minuten)
EFT_NEWS_INTERVAL           = 30
EFT_CODES_CHECK_INTERVAL    = 60
EFT_CODES_VERIFY_INTERVAL   = 120
EFT_PATCH_INTERVAL          = 15
EFT_RELEASE_INTERVAL        = 60
ARMA_CODES_CHECK_INTERVAL   = 60
ARMA_CODES_VERIFY_INTERVAL  = 60

# Speicherdateien
POSTED_NEWS_FILE       = "/tmp/posted_eft_news.json"
POSTED_CODES_FILE      = "/tmp/posted_eft_codes.json"
POSTED_PATCHES_FILE    = "/tmp/posted_eft_patches.json"
POSTED_RELEASE_FILE    = "/tmp/posted_eft_release.json"
POSTED_ARMA_CODES_FILE = "/tmp/posted_arma_codes.json"

# ─── Quellen ──────────────────────────────────────────────────────────────────
EFT_RSS_FEEDS = [
    "https://www.escapefromtarkov.com/news/rss",  # Nur offizielle BSG Quelle
]
EFT_PATCH_SOURCES = [
    "https://www.escapefromtarkov.com/news/rss",
]
BSG_OFFICIAL_DOMAINS = ["escapefromtarkov.com", "battlestategames.com"]

EFT_RELEASE_SOURCES = [
    "https://www.escapefromtarkov.com/news/rss",
    "https://www.reddit.com/r/EscapeFromTarkov/search.json?q=wipe+date+release&sort=new&limit=10",
]
EFT_CODES_URLS = [
    "https://progameguides.com/escape-from-tarkov/escape-from-tarkov-promo-codes/",
    "https://www.pcgamesn.com/escape-from-tarkov/promo-codes",
]
ARMA_KOTH_CODES_URLS = [
    "https://www.reddit.com/r/armareforger/search.json?q=king+of+the+hill+code&sort=new&limit=10",
    "https://www.reddit.com/r/ArmaReforger/search.json?q=KOTH+code&sort=new&limit=10",
    "https://www.reddit.com/r/armaReforger/search.json?q=promo+code&sort=new&limit=10",
]

# ─── Bot Setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Datenspeicherung ─────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return default

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Speicherfehler: {e}")

posted_news_ids    = set(load_json(POSTED_NEWS_FILE, []))
posted_codes       = load_json(POSTED_CODES_FILE, {})
posted_patch_ids   = set(load_json(POSTED_PATCHES_FILE, []))
posted_release_ids = set(load_json(POSTED_RELEASE_FILE, []))
posted_arma_codes  = load_json(POSTED_ARMA_CODES_FILE, {})

# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────
def ask_claude(prompt: str, max_tokens: int = 800) -> str:
    try:
        response = claude_client.messages.create(
            model="claude-opus-4-5", max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"Claude-Fehler: {e}")
        return ""

async def fetch_url(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.text()
    except Exception as e:
        print(f"Fetch-Fehler ({url}): {e}")
    return ""

def clean_html(text: str, max_len: int = 3000) -> str:
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:max_len]

def parse_rss(text: str, max_items: int = 10) -> list:
    items = []
    try:
        root = ET.fromstring(text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        rss_items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        for item in rss_items[:max_items]:
            title = (item.findtext("title") or item.findtext("atom:title", namespaces=ns) or "").strip()
            link_el = item.find("atom:link", ns)
            link = (item.findtext("link") or (link_el.get("href") if link_el is not None else "") or "").strip()
            description = (item.findtext("description") or item.findtext("atom:summary", namespaces=ns) or "").strip()
            description = re.sub(r'<[^>]+>', '', description)[:1000]
            if title and link:
                items.append({"title": title, "link": link, "description": description})
    except Exception as e:
        print(f"RSS-Parse-Fehler: {e}")
    return items

def is_from_bsg(link: str) -> bool:
    return any(domain in link.lower() for domain in BSG_OFFICIAL_DOMAINS)

async def post_to_channel(guild, channel_name: str, embed: discord.Embed):
    channel = discord.utils.get(guild.text_channels, name=channel_name)
    if not channel:
        try:
            channel = await guild.create_text_channel(channel_name)
        except:
            return None
    try:
        return await channel.send(embed=embed)
    except Exception as e:
        print(f"Post-Fehler #{channel_name}: {e}")
    return None

async def delete_posted_message(data: dict, code: str, label: str):
    try:
        guild = bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None
        if channel:
            msg = await channel.fetch_message(data["message_id"])
            embed = discord.Embed(
                title=f"❌ Abgelaufen: ~~`{code}`~~",
                description="Dieser Code ist nicht mehr gültig.",
                color=discord.Color.red(), timestamp=datetime.now()
            )
            await msg.edit(embed=embed)
            await asyncio.sleep(5)
            await msg.delete()
            print(f"  🗑️ {label} gelöscht: {code}")
    except Exception as e:
        print(f"Lösch-Fehler {code}: {e}")

async def translate_text(title: str, description: str) -> tuple:
    result = ask_claude(
        f'Übersetze ins Deutsche. NUR JSON: {{"title":"...","summary":"..."}}\nTitel: {title}\nText: {description}', 500
    )
    match = re.search(r'\{.*\}', result, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return data.get("title", title), data.get("summary", description)
        except:
            pass
    return title, description

# ══════════════════════════════════════════════════════════════════════════════
# EFT NEWS (Nur BSG)
# ══════════════════════════════════════════════════════════════════════════════
@tasks.loop(minutes=EFT_NEWS_INTERVAL)
async def check_eft_news():
    global posted_news_ids
    print(f"📰 News-Check ({datetime.now().strftime('%H:%M')})")
    try:
        count = 0
        for feed_url in EFT_RSS_FEEDS:
            text = await fetch_url(feed_url)
            if not text:
                continue
            for item in parse_rss(text, 5):
                if item["link"] not in posted_news_ids:
                    title_de, summary_de = await translate_text(item["title"], item["description"])
                    embed = discord.Embed(
                        title=f"🎯 {title_de}", url=item["link"],
                        description=summary_de or "Klicke für mehr Details.",
                        color=discord.Color.orange(), timestamp=datetime.now()
                    )
                    embed.set_author(name="Escape from Tarkov – Offizielle News (BSG)")
                    embed.set_footer(text="Quelle: escapefromtarkov.com • Übersetzt")
                    for guild in bot.guilds:
                        await post_to_channel(guild, EFT_NEWS_CHANNEL, embed)
                    posted_news_ids.add(item["link"])
                    count += 1
                    await asyncio.sleep(2)
        save_json(POSTED_NEWS_FILE, list(posted_news_ids))
        print(f"  → {count} neue News")
    except Exception as e:
        print(f"News-Fehler: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# EFT PATCHNOTES (Nur offizielle BSG)
# ══════════════════════════════════════════════════════════════════════════════
def is_patchnote(title: str, description: str) -> bool:
    keywords = ["patch", "update", "hotfix", "fix", "changelog", "version", "patchnotes"]
    return any(kw in (title + " " + description).lower() for kw in keywords)

@tasks.loop(minutes=EFT_PATCH_INTERVAL)
async def check_eft_patchnotes():
    global posted_patch_ids
    print(f"🔧 BSG Patchnotes-Check ({datetime.now().strftime('%H:%M')})")
    try:
        count = 0
        for url in EFT_PATCH_SOURCES:
            text = await fetch_url(url)
            if not text:
                continue
            for item in parse_rss(text, 10):
                if not is_from_bsg(item["link"]):
                    continue
                if not is_patchnote(item["title"], item["description"]):
                    continue
                if item["link"] in posted_patch_ids:
                    continue
                full_text = await fetch_url(item["link"])
                content = clean_html(full_text, 4000) if full_text else item["description"]
                summary = ask_claude(
                    f"""Offizielle EFT Patchnotes von BSG. Erstelle eine deutsche Zusammenfassung mit:
**🐛 Bugfixes** | **⚙️ Änderungen** | **✨ Neue Inhalte** | **⚖️ Balance**
Max 1500 Zeichen. Titel: {item['title']}\nInhalt: {content}""", 800
                )
                embed = discord.Embed(
                    title=f"🔧 {item['title']}", url=item["link"],
                    description=summary or item["description"][:800],
                    color=discord.Color.blue(), timestamp=datetime.now()
                )
                embed.set_author(name="Offizielle BSG Patchnotes")
                embed.set_footer(text="Quelle: escapefromtarkov.com (Battlestate Games) • Übersetzt")
                for guild in bot.guilds:
                    await post_to_channel(guild, EFT_PATCHNOTES_CHANNEL, embed)
                posted_patch_ids.add(item["link"])
                count += 1
                await asyncio.sleep(3)
        save_json(POSTED_PATCHES_FILE, list(posted_patch_ids))
        print(f"  → {count} neue Patchnotes")
    except Exception as e:
        print(f"Patchnotes-Fehler: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# EFT RELEASE / WIPE DATES
# ══════════════════════════════════════════════════════════════════════════════
def is_release_info(title: str, description: str) -> bool:
    keywords = ["wipe", "release", "launch", "goes live", "patch release", "update date", "release date", "coming soon", "scheduled"]
    return any(kw in (title + " " + description).lower() for kw in keywords)

@tasks.loop(minutes=EFT_RELEASE_INTERVAL)
async def check_eft_release():
    global posted_release_ids
    print(f"🚀 Release-Check ({datetime.now().strftime('%H:%M')})")
    try:
        releases = []
        for url in EFT_RELEASE_SOURCES:
            text = await fetch_url(url)
            if not text:
                continue
            if "search.json" in url:
                try:
                    data = json.loads(text)
                    for post in data.get("data", {}).get("children", [])[:5]:
                        p = post.get("data", {})
                        title = p.get("title", "")
                        link = "https://reddit.com" + p.get("permalink", "")
                        desc = p.get("selftext", "")[:500]
                        if is_release_info(title, desc):
                            releases.append({"id": link, "title": title, "link": link, "description": desc})
                except:
                    pass
            else:
                for item in parse_rss(text, 10):
                    if is_release_info(item["title"], item["description"]):
                        item["id"] = item["link"]
                        releases.append(item)

        count = 0
        for release in releases:
            if release["id"] not in posted_release_ids:
                analysis = ask_claude(
                    f"""EFT Release/Wipe Info analysieren. NUR JSON:
{{"titel":"...","datum":"...","uhrzeit":"...","beschreibung":"...","typ":"wipe/patch/update"}}
Titel: {release['title']}\nText: {release['description']}""", 500
                )
                datum = "Noch nicht bekannt"
                uhrzeit = "Noch nicht bekannt"
                beschreibung = release["description"][:500]
                typ = "update"
                titel = release["title"]
                match = re.search(r'\{.*\}', analysis, re.DOTALL)
                if match:
                    try:
                        d = json.loads(match.group())
                        datum = d.get("datum", datum)
                        uhrzeit = d.get("uhrzeit", uhrzeit)
                        beschreibung = d.get("beschreibung", beschreibung)
                        typ = d.get("typ", typ)
                        titel = d.get("titel", titel)
                    except:
                        pass
                color = {"wipe": discord.Color.red(), "patch": discord.Color.blue(), "update": discord.Color.green()}.get(typ, discord.Color.gold())
                emoji = {"wipe": "💥", "patch": "🔧", "update": "⬆️"}.get(typ, "🚀")
                embed = discord.Embed(title=f"{emoji} {titel}", url=release["link"], description=beschreibung, color=color, timestamp=datetime.now())
                embed.add_field(name="📅 Datum", value=datum, inline=True)
                embed.add_field(name="🕐 Uhrzeit", value=uhrzeit, inline=True)
                embed.add_field(name="📌 Typ", value=typ.upper(), inline=True)
                embed.set_footer(text="Automatisch erkannt & übersetzt")
                for guild in bot.guilds:
                    await post_to_channel(guild, EFT_RELEASE_CHANNEL, embed)
                posted_release_ids.add(release["id"])
                count += 1
                await asyncio.sleep(3)
        save_json(POSTED_RELEASE_FILE, list(posted_release_ids))
        print(f"  → {count} neue Release-Infos")
    except Exception as e:
        print(f"Release-Fehler: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# EFT CODES
# ══════════════════════════════════════════════════════════════════════════════
@tasks.loop(minutes=EFT_CODES_CHECK_INTERVAL)
async def check_eft_codes():
    global posted_codes
    print(f"🎁 EFT Code-Check ({datetime.now().strftime('%H:%M')})")
    try:
        all_codes = []
        for url in EFT_CODES_URLS:
            text = await fetch_url(url)
            if not text:
                continue
            result = ask_claude(
                f'Finde alle EFT Promo-Codes. NUR JSON: [{{"code":"...","description":"...(Deutsch)"}}]\n\nText: {clean_html(text)}', 600
            )
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if match:
                try:
                    all_codes.extend(json.loads(match.group()))
                except:
                    pass
        seen = set()
        count = 0
        for c in all_codes:
            code = c.get("code", "").upper().strip()
            if not code or code in seen or code in posted_codes:
                continue
            seen.add(code)
            embed = discord.Embed(
                title=f"🎁 EFT Code: `{code}`",
                description=c.get("description", "EFT Promo-Code"),
                color=discord.Color.gold(), timestamp=datetime.now()
            )
            embed.add_field(name="📋 Code", value=f"```{code}```", inline=False)
            embed.set_footer(text="Abgelaufene Codes werden automatisch gelöscht")
            for guild in bot.guilds:
                msg = await post_to_channel(guild, EFT_CODES_CHANNEL, embed)
                if msg:
                    posted_codes[code] = {"message_id": msg.id, "channel_id": msg.channel.id, "guild_id": guild.id}
            count += 1
            await asyncio.sleep(2)
        save_json(POSTED_CODES_FILE, posted_codes)
        print(f"  → {count} neue EFT Codes")
    except Exception as e:
        print(f"EFT Code-Fehler: {e}")

@tasks.loop(minutes=EFT_CODES_VERIFY_INTERVAL)
async def verify_eft_codes():
    global posted_codes
    to_delete = []
    for code, data in posted_codes.items():
        result = ask_claude(f'Ist EFT Code "{code}" noch gültig? NUR JSON: {{"valid": true/false}}', 100)
        match = re.search(r'\{.*\}', result)
        valid = True
        if match:
            try:
                valid = json.loads(match.group()).get("valid", True)
            except:
                pass
        if not valid:
            await delete_posted_message(data, code, "EFT Code")
            to_delete.append(code)
        await asyncio.sleep(3)
    for code in to_delete:
        del posted_codes[code]
    if to_delete:
        save_json(POSTED_CODES_FILE, posted_codes)

# ══════════════════════════════════════════════════════════════════════════════
# ARMA REFORGER KOTH CODES
# ══════════════════════════════════════════════════════════════════════════════
async def scrape_arma_koth_codes() -> list:
    all_codes = []
    for url in ARMA_KOTH_CODES_URLS:
        text = await fetch_url(url)
        if not text:
            continue
        if "search.json" in url:
            try:
                data = json.loads(text)
                posts = data.get("data", {}).get("children", [])
                combined = ""
                for post in posts[:8]:
                    p = post.get("data", {})
                    combined += "Titel: " + p.get("title", "") + " | Text: " + p.get("selftext", "")[:300] + "\n"
                result = ask_claude(
                    "Finde alle Arma Reforger King of the Hill (KOTH) Promo-Codes/Keys in diesem Text.\n"
                    "Nur aktuelle, funktionierende Codes.\n"
                    'Antworte NUR mit JSON-Array ([] wenn keine): [{"code": "CODE", "description": "Was der Code gibt (Deutsch)"}]\n\n'
                    "Text: " + combined, 600
                )
            except Exception as e:
                print(f"Arma Reddit Fehler: {e}")
                result = "[]"
        else:
            result = ask_claude(
                "Finde alle Arma Reforger KOTH Promo-Codes in diesem Text.\n"
                "Nur aktuelle funktionierende Codes.\n"
                'Antworte NUR mit JSON-Array: [{"code": "CODE", "description": "Beschreibung (Deutsch)"}]\n\n'
                "Text: " + clean_html(text), 600
            )
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if match:
            try:
                all_codes.extend(json.loads(match.group()))
            except:
                pass
        await asyncio.sleep(1)

    seen = set()
    unique = []
    for c in all_codes:
        code = c.get("code", "").upper().strip()
        if code and len(code) >= 3 and code not in seen:
            seen.add(code)
            c["code"] = code
            unique.append(c)
    return unique

async def verify_arma_code(code: str) -> bool:
    for url in ARMA_KOTH_CODES_URLS[:2]:
        text = await fetch_url(url)
        if text and code.upper() in text.upper():
            return True
    result = ask_claude(
        f'Ist der Arma Reforger KOTH Code "{code}" noch gültig? Codes die generisch oder alt wirken sind meist abgelaufen. NUR JSON: {{"valid": true/false}}', 100
    )
    match = re.search(r'\{.*\}', result)
    if match:
        try:
            return json.loads(match.group()).get("valid", False)
        except:
            pass
    return False  # Im Zweifel löschen für maximale Aktualität

@tasks.loop(minutes=ARMA_CODES_CHECK_INTERVAL)
async def check_arma_koth_codes():
    global posted_arma_codes
    print(f"🎮 Arma KOTH Code-Check ({datetime.now().strftime('%H:%M')})")
    try:
        codes = await scrape_arma_koth_codes()
        count = 0
        for code_info in codes:
            code = code_info.get("code", "")
            if not code or code in posted_arma_codes:
                continue
            embed = discord.Embed(
                title=f"🎮 KOTH Code: `{code}`",
                description=code_info.get("description", "Arma Reforger King of the Hill Code"),
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="📋 Code kopieren", value=f"```{code}```", inline=False)
            embed.add_field(name="✅ Status", value="Aktiv & verifiziert", inline=True)
            embed.add_field(name="🎯 Spiel", value="Arma Reforger – King of the Hill", inline=True)
            embed.set_footer(text="Stündlich geprüft • Abgelaufene Codes werden automatisch gelöscht")
            for guild in bot.guilds:
                msg = await post_to_channel(guild, ARMA_KOTH_CODES_CHANNEL, embed)
                if msg:
                    posted_arma_codes[code] = {
                        "message_id": msg.id,
                        "channel_id": msg.channel.id,
                        "guild_id": guild.id,
                        "found_at": datetime.now().isoformat()
                    }
            count += 1
            await asyncio.sleep(2)
        save_json(POSTED_ARMA_CODES_FILE, posted_arma_codes)
        print(f"  → {count} neue KOTH Codes")
    except Exception as e:
        print(f"Arma Code-Fehler: {e}")

@tasks.loop(minutes=ARMA_CODES_VERIFY_INTERVAL)
async def verify_arma_koth_codes():
    global posted_arma_codes
    print(f"🔍 Arma KOTH Verifizierung ({datetime.now().strftime('%H:%M')})")
    to_delete = []
    for code, data in posted_arma_codes.items():
        if not await verify_arma_code(code):
            await delete_posted_message(data, code, "KOTH Code")
            to_delete.append(code)
        await asyncio.sleep(3)
    for code in to_delete:
        del posted_arma_codes[code]
    if to_delete:
        save_json(POSTED_ARMA_CODES_FILE, posted_arma_codes)
        print(f"  → {len(to_delete)} KOTH Codes gelöscht")

# ══════════════════════════════════════════════════════════════════════════════
# CLAUDE AUFGABEN TOOLS
# ══════════════════════════════════════════════════════════════════════════════
TOOLS = [
    {"name": "send_message", "description": "Sendet eine Nachricht in einen Channel",
     "input_schema": {"type": "object", "properties": {"channel_name": {"type": "string"}, "message": {"type": "string"}}, "required": ["channel_name", "message"]}},
    {"name": "send_dm", "description": "Direktnachricht an User",
     "input_schema": {"type": "object", "properties": {"username": {"type": "string"}, "message": {"type": "string"}}, "required": ["username", "message"]}},
    {"name": "create_channel", "description": "Erstellt einen Text-Channel",
     "input_schema": {"type": "object", "properties": {"channel_name": {"type": "string"}, "category": {"type": "string"}, "topic": {"type": "string"}}, "required": ["channel_name"]}},
    {"name": "delete_channel", "description": "Löscht einen Channel",
     "input_schema": {"type": "object", "properties": {"channel_name": {"type": "string"}}, "required": ["channel_name"]}},
    {"name": "create_role", "description": "Erstellt eine Rolle",
     "input_schema": {"type": "object", "properties": {"role_name": {"type": "string"}, "color": {"type": "string"}, "mentionable": {"type": "boolean"}}, "required": ["role_name"]}},
    {"name": "assign_role", "description": "Rolle an User vergeben",
     "input_schema": {"type": "object", "properties": {"username": {"type": "string"}, "role_name": {"type": "string"}}, "required": ["username", "role_name"]}},
    {"name": "remove_role", "description": "Rolle von User entfernen",
     "input_schema": {"type": "object", "properties": {"username": {"type": "string"}, "role_name": {"type": "string"}}, "required": ["username", "role_name"]}},
    {"name": "kick_member", "description": "User kicken",
     "input_schema": {"type": "object", "properties": {"username": {"type": "string"}, "reason": {"type": "string"}}, "required": ["username"]}},
    {"name": "ban_member", "description": "User bannen",
     "input_schema": {"type": "object", "properties": {"username": {"type": "string"}, "reason": {"type": "string"}}, "required": ["username"]}},
    {"name": "list_members", "description": "Mitglieder auflisten",
     "input_schema": {"type": "object", "properties": {"role_filter": {"type": "string"}}}},
    {"name": "list_channels", "description": "Channels auflisten",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_news_now", "description": "EFT News sofort prüfen", "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_codes_now", "description": "EFT Codes sofort suchen", "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_patchnotes_now", "description": "EFT Patchnotes sofort prüfen", "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_release_now", "description": "EFT Release-Info sofort prüfen", "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_arma_codes_now", "description": "Arma KOTH Codes sofort suchen", "input_schema": {"type": "object", "properties": {}}},
]

async def execute_tool(tool_name: str, tool_input: dict, guild: discord.Guild, ctx_channel) -> str:
    try:
        if tool_name == "send_message":
            ch = discord.utils.get(guild.text_channels, name=tool_input["channel_name"])
            if not ch: return "❌ Channel nicht gefunden."
            await ch.send(tool_input["message"])
            return f"✅ Nachricht in #{tool_input['channel_name']} gesendet."
        elif tool_name == "send_dm":
            m = discord.utils.find(lambda x: x.name.lower() == tool_input["username"].lower(), guild.members)
            if not m: return "❌ User nicht gefunden."
            await m.send(tool_input["message"])
            return f"✅ DM an {m.name} gesendet."
        elif tool_name == "create_channel":
            if discord.utils.get(guild.text_channels, name=tool_input["channel_name"]): return "⚠️ Existiert bereits."
            cat = discord.utils.get(guild.categories, name=tool_input.get("category", "")) if tool_input.get("category") else None
            ch = await guild.create_text_channel(tool_input["channel_name"], category=cat, topic=tool_input.get("topic", ""))
            return f"✅ Channel #{ch.name} erstellt."
        elif tool_name == "delete_channel":
            ch = discord.utils.get(guild.text_channels, name=tool_input["channel_name"])
            if not ch: return "❌ Channel nicht gefunden."
            await ch.delete()
            return "✅ Channel gelöscht."
        elif tool_name == "create_role":
            color = discord.Color(int(tool_input["color"].lstrip("#"), 16)) if tool_input.get("color") else discord.Color.default()
            r = await guild.create_role(name=tool_input["role_name"], color=color, mentionable=tool_input.get("mentionable", False))
            return f"✅ Rolle '{r.name}' erstellt."
        elif tool_name == "assign_role":
            m = discord.utils.find(lambda x: x.name.lower() == tool_input["username"].lower(), guild.members)
            r = discord.utils.get(guild.roles, name=tool_input["role_name"])
            if not m: return "❌ User nicht gefunden."
            if not r: return "❌ Rolle nicht gefunden."
            await m.add_roles(r)
            return "✅ Rolle vergeben."
        elif tool_name == "remove_role":
            m = discord.utils.find(lambda x: x.name.lower() == tool_input["username"].lower(), guild.members)
            r = discord.utils.get(guild.roles, name=tool_input["role_name"])
            if not m: return "❌ User nicht gefunden."
            if not r: return "❌ Rolle nicht gefunden."
            await m.remove_roles(r)
            return "✅ Rolle entfernt."
        elif tool_name == "kick_member":
            m = discord.utils.find(lambda x: x.name.lower() == tool_input["username"].lower(), guild.members)
            if not m: return "❌ User nicht gefunden."
            await m.kick(reason=tool_input.get("reason", "Kein Grund"))
            return f"✅ {m.name} gekickt."
        elif tool_name == "ban_member":
            m = discord.utils.find(lambda x: x.name.lower() == tool_input["username"].lower(), guild.members)
            if not m: return "❌ User nicht gefunden."
            await m.ban(reason=tool_input.get("reason", "Kein Grund"))
            return f"✅ {m.name} gebannt."
        elif tool_name == "list_members":
            members = guild.members
            if tool_input.get("role_filter"):
                r = discord.utils.get(guild.roles, name=tool_input["role_filter"])
                if r: members = [x for x in members if r in x.roles]
            return f"👥 {len(members)} Mitglieder: {', '.join(x.name for x in members)}"
        elif tool_name == "list_channels":
            return f"📋 {', '.join(f'#{c.name}' for c in guild.text_channels)}"
        elif tool_name == "check_news_now":
            await check_eft_news(); return "✅ News-Check ausgeführt."
        elif tool_name == "check_codes_now":
            await check_eft_codes(); return "✅ EFT Code-Check ausgeführt."
        elif tool_name == "check_patchnotes_now":
            await check_eft_patchnotes(); return "✅ Patchnotes-Check ausgeführt."
        elif tool_name == "check_release_now":
            await check_eft_release(); return "✅ Release-Check ausgeführt."
        elif tool_name == "check_arma_codes_now":
            await check_arma_koth_codes(); return "✅ Arma KOTH Code-Check ausgeführt."
        else:
            return f"❌ Unbekanntes Tool: {tool_name}"
    except discord.Forbidden:
        return "❌ Keine Berechtigung."
    except Exception as e:
        return f"❌ Fehler: {e}"

async def process_task(task: str, guild: discord.Guild, ctx) -> tuple:
    info = f"Server: {guild.name}, Channels: {', '.join(c.name for c in guild.text_channels[:15])}"
    messages = [{"role": "user", "content": task}]
    results = []
    response = claude_client.messages.create(
        model="claude-opus-4-5", max_tokens=2048,
        system=f"Du bist ein Discord-Bot-Assistent. {info}. Antworte auf Deutsch.",
        tools=TOOLS, messages=messages
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
        response = claude_client.messages.create(
            model="claude-opus-4-5", max_tokens=2048,
            system=f"Discord-Bot. Server: {guild.name}. Antworte auf Deutsch.",
            tools=TOOLS, messages=messages
        )
    return next((b.text for b in response.content if hasattr(b, "text")), ""), results

# ══════════════════════════════════════════════════════════════════════════════
# BOT COMMANDS
# ══════════════════════════════════════════════════════════════════════════════
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    for fn in [check_eft_news, check_eft_patchnotes, check_eft_release,
               check_eft_codes, verify_eft_codes,
               check_arma_koth_codes, verify_arma_koth_codes]:
        if not fn.is_running():
            fn.start()
    print("🚀 Alle Tasks gestartet!")

@bot.command(name="aufgabe", aliases=["task", "do"])
async def aufgabe(ctx, *, text: str):
    if ADMIN_USER_IDS and ctx.author.id not in ADMIN_USER_IDS:
        await ctx.reply("❌ Keine Berechtigung.")
        return
    if not ctx.guild:
        await ctx.reply("❌ Nur in einem Server.")
        return
    loading = await ctx.reply("🤖 Verarbeite Aufgabe...")
    try:
        final, actions = await process_task(text, ctx.guild, ctx)
        embed = discord.Embed(title="📋 Aufgabe ausgeführt", description=f"**Aufgabe:** {text}", color=discord.Color.green())
        if actions:
            embed.add_field(name="🔧 Aktionen", value="\n".join(actions[:10]), inline=False)
        if final:
            embed.add_field(name="💬 Claude sagt", value=final[:1024], inline=False)
        embed.set_footer(text=f"Von {ctx.author.name}")
        await loading.edit(content=None, embed=embed)
    except Exception as e:
        await loading.edit(content=f"❌ Fehler: {e}")

@bot.command(name="eft_news")
async def cmd_news(ctx):
    msg = await ctx.reply("🔍 Prüfe BSG News...")
    await check_eft_news()
    await msg.edit(content="✅ Erledigt!")

@bot.command(name="eft_codes")
async def cmd_codes(ctx):
    msg = await ctx.reply("🎁 Suche EFT Codes...")
    await check_eft_codes()
    await msg.edit(content="✅ Erledigt!")

@bot.command(name="eft_patchnotes")
async def cmd_patches(ctx):
    msg = await ctx.reply("🔧 Suche BSG Patchnotes...")
    await check_eft_patchnotes()
    await msg.edit(content="✅ Erledigt!")

@bot.command(name="eft_release")
async def cmd_release(ctx):
    msg = await ctx.reply("🚀 Suche Release-Info...")
    await check_eft_release()
    await msg.edit(content="✅ Erledigt!")

@bot.command(name="arma_codes")
async def cmd_arma_codes(ctx):
    msg = await ctx.reply("🎮 Suche Arma KOTH Codes...")
    await check_arma_koth_codes()
    await msg.edit(content="✅ Erledigt!")

@bot.command(name="hilfe_bot")
async def cmd_hilfe(ctx):
    embed = discord.Embed(title="🤖 Bot Übersicht", color=discord.Color.blue())
    embed.add_field(name="📝 Aufgaben", value="`!aufgabe [Beschreibung]`", inline=False)
    embed.add_field(name="📰 EFT News", value=f"`!eft_news` – Alle {EFT_NEWS_INTERVAL} Min. • Nur BSG", inline=False)
    embed.add_field(name="🔧 Patchnotes", value=f"`!eft_patchnotes` – Alle {EFT_PATCH_INTERVAL} Min. • Nur BSG", inline=False)
    embed.add_field(name="🚀 Release", value=f"`!eft_release` – Alle {EFT_RELEASE_INTERVAL} Min.", inline=False)
    embed.add_field(name="🎁 EFT Codes", value=f"`!eft_codes` – Alle {EFT_CODES_CHECK_INTERVAL} Min.", inline=False)
    embed.add_field(name="🎮 Arma KOTH Codes", value=f"`!arma_codes` – Alle {ARMA_CODES_CHECK_INTERVAL} Min. • Stündlich verifiziert", inline=False)
    await ctx.send(embed=embed)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
