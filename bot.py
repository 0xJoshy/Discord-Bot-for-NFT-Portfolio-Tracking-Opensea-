"""
NFT Portfolio Watcher — Discord bot
------------------------------------
Shows your top-N highest-floor-price NFT collections, pulled live from OpenSea.

Setup:
  1. pip install -r requirements.txt
  2. Copy .env.example to .env and fill in your values
  3. python bot.py

Slash command:
  /portfolio [wallet] [top] [chain]
"""

import os
import asyncio
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENSEA_API_KEY = os.getenv("OPENSEA_API_KEY")
DEFAULT_WALLET = os.getenv("WALLET_ADDRESS")  # optional fallback so users can just run /portfolio
DEFAULT_CHAIN = os.getenv("CHAIN", "ethereum")

OPENSEA_BASE = "https://api.opensea.io/api/v2"
HEADERS = {"accept": "application/json", "x-api-key": OPENSEA_API_KEY}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------------------
# OpenSea helpers
# ---------------------------------------------------------------------------

async def fetch_wallet_collections(session: aiohttp.ClientSession, wallet: str, chain: str) -> set[str]:
    """Return the set of distinct collection slugs a wallet holds NFTs in."""
    slugs = set()
    url = f"{OPENSEA_BASE}/chain/{chain}/account/{wallet}/nfts"
    params = {"limit": 200}

    while url:
        async with session.get(url, headers=HEADERS, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"OpenSea NFTs API error {resp.status}: {text}")
            data = await resp.json()

        for nft in data.get("nfts", []):
            slug = nft.get("collection")
            if slug:
                slugs.add(slug)

        cursor = data.get("next")
        if cursor:
            params = {"limit": 200, "next": cursor}
        else:
            url = None  # done paginating

    return slugs


async def fetch_collection_floor(session: aiohttp.ClientSession, slug: str) -> dict | None:
    """Return {'slug', 'floor_price', 'symbol'} for a collection, or None on failure."""
    url = f"{OPENSEA_BASE}/collections/{slug}/stats"
    async with session.get(url, headers=HEADERS) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()

    total = data.get("total", {})
    floor = total.get("floor_price")
    if floor is None:
        return None

    return {
        "slug": slug,
        "floor_price": float(floor),
        "symbol": total.get("floor_price_symbol", "ETH"),
    }


async def build_portfolio(wallet: str, chain: str, top_n: int) -> list[dict]:
    """Fetch a wallet's collections and return the top_n by floor price, descending."""
    async with aiohttp.ClientSession() as session:
        slugs = await fetch_wallet_collections(session, wallet, chain)

        # Fetch floor prices concurrently, but capped so we don't hammer the API
        semaphore = asyncio.Semaphore(5)

        async def bounded_fetch(slug):
            async with semaphore:
                return await fetch_collection_floor(session, slug)

        results = await asyncio.gather(*(bounded_fetch(s) for s in slugs))

    valid = [r for r in results if r is not None]
    valid.sort(key=lambda r: r["floor_price"], reverse=True)
    return valid[:top_n]


# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user} — synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Sync failed: {e}")


@bot.tree.command(name="portfolio", description="Show your top NFT collections by floor price")
@app_commands.describe(
    wallet="Wallet address to check (defaults to WALLET_ADDRESS in .env)",
    top="How many collections to show (default 5)",
    chain="Chain slug, e.g. ethereum, matic, base (default ethereum)",
)
async def portfolio(
    interaction: discord.Interaction,
    wallet: str = None,
    top: int = 5,
    chain: str = None,
):
    wallet = wallet or DEFAULT_WALLET
    chain = chain or DEFAULT_CHAIN

    if not wallet:
        await interaction.response.send_message(
            "No wallet provided and no default WALLET_ADDRESS set in .env.", ephemeral=True
        )
        return

    await interaction.response.defer()  # OpenSea calls can take a few seconds

    try:
        top_collections = await build_portfolio(wallet, chain, top)
    except Exception as e:
        await interaction.followup.send(f"Something went wrong: {e}")
        return

    if not top_collections:
        await interaction.followup.send("No collections with floor price data found for that wallet.")
        return

    embed = discord.Embed(
        title="🖼️ NFT Portfolio — Top Holdings by Floor Price",
        description=f"Wallet: `{wallet}`\nChain: `{chain}`",
        color=discord.Color.blurple(),
    )

    for i, c in enumerate(top_collections, start=1):
        embed.add_field(
            name=f"{i}. {c['slug']}",
            value=f"Floor: **{c['floor_price']} {c['symbol']}**",
            inline=False,
        )

    await interaction.followup.send(embed=embed)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN missing — set it in your .env file.")
    if not OPENSEA_API_KEY:
        raise SystemExit("OPENSEA_API_KEY missing — set it in your .env file.")
    bot.run(DISCORD_TOKEN)
