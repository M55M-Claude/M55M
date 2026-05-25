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
EFT_CODES_CHECK_INTERVAL    = 30
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
    "https://www.escapefromtarkov.com/news/rss",      # Offizielle BSG Website
]
BSG_OFFICIAL_DOMAINS = ["escapefromtarkov.com", "battlestategames.com"]

# Offizielle Twitter/X Accounts (via Nitter RSS - kein API Key nötig)
BSG_TWITTER_RSS_FEEDS = [
    "https://nitter.privacydev.net/bstategames/rss",   # BSG offiziell
    "https://nitter.privacydev.net/tarkov_game/rss",   # EFT offiziell
    "https://nitter.poast.org/bstategames/rss",         # BSG Backup
    "https://nitter.poast.org/tarkov_game/rss",         # EFT Backup
]

EFT_RELEASE_SOURCES = [
    "https://www.escapefromtarkov.com/news/rss",  # Nur offizielle BSG Website
    # Twitter via Nitter RSS (wenn verfügbar)
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

async def post_patchnote(item: dict, source_label: str, source_icon: str = ""):
    """Patchnote im Original posten (keine Übersetzung)"""
    title = item.get("title", "")
    link = item.get("link", "")
    description = item.get("description", "")

    # Für BSG Website: vollständigen Text laden
    if link and "escapefromtarkov.com" in link:
        full_text = await fetch_url(link)
        content_text = clean_html(full_text, 2000) if full_text else description
    else:
        content_text = description[:1000]

    embed = discord.Embed(
        title=f"🔧 {title[:200]}",
        url=link,
        description=content_text or description[:800],
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.set_author(name=source_label)
    embed.set_footer(text=f"Quelle: {source_label} • Original")

    for guild in bot.guilds:
        await post_to_channel(guild, EFT_PATCHNOTES_CHANNEL, embed)

@tasks.loop(minutes=EFT_PATCH_INTERVAL)
async def check_eft_patchnotes():
    global posted_patch_ids
    print(f"🔧 BSG Patchnotes-Check ({datetime.now().strftime('%H:%M')})")
    count = 0
    try:
        # ── 1. Offizielle BSG Website ──────────────────────────────────────
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
                await post_patchnote(item, "Offizielle BSG Website – escapefromtarkov.com")
                posted_patch_ids.add(item["link"])
                count += 1
                await asyncio.sleep(3)

        # ── 2. Offizielle Twitter/X Accounts von BSG & EFT (via Nitter) ──
        for feed_url in BSG_TWITTER_RSS_FEEDS:
            text = await fetch_url(feed_url)
            if not text:
                continue
            for item in parse_rss(text, 15):
                tweet_id = item["link"]
                if tweet_id in posted_patch_ids:
                    continue
                if not is_patchnote(item["title"], item["description"]):
                    continue
                label = "Twitter/X – @bstategames" if "bstategames" in feed_url else "Twitter/X – @tarkov_game"
                await post_patchnote(item, label)
                posted_patch_ids.add(tweet_id)
                count += 1
                await asyncio.sleep(3)

        save_json(POSTED_PATCHES_FILE, list(posted_patch_ids))
        print(f"  → {count} neue Patchnotes/Tweets")
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

        # ── 1. Offizielle BSG Website ──────────────────────────────────────
        for url in EFT_RELEASE_SOURCES:
            text = await fetch_url(url)
            if not text:
                continue
            for item in parse_rss(text, 10):
                if is_release_info(item["title"], item["description"]) and is_from_bsg(item["link"]):
                    item["id"] = item["link"]
                    releases.append(item)

        # ── 2. Offizielle Twitter/X Accounts ──────────────────────────────
        for feed_url in BSG_TWITTER_RSS_FEEDS:
            text = await fetch_url(feed_url)
            if not text:
                continue
            for item in parse_rss(text, 15):
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
                    "Text: " + c
