"""
NFT Portfolio Watcher — Discord bot
------------------------------------
Shows your top-N highest-floor-price NFT collections, pulled live from OpenSea.

Setup:
  1. pip install -r requirements.txt
  2. Copy .env.example to .env and fill in your values
  3. python bot.py

Slash command:
  /portfolio [wallet] [top] [chains]
"""

import os
import asyncio
from collections import Counter
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENSEA_API_KEY = os.getenv("OPENSEA_API_KEY")
DEFAULT_WALLET = os.getenv("WALLET_ADDRESS")  # optional fallback so users can just run /portfolio

# OpenSea has no single "all chains" endpoint — each chain must be queried
# separately and merged. CHAINS is a comma-separated list in .env, e.g.:
#   CHAINS=ethereum,polygon,base,arbitrum,optimism,robinhood
# Full list of supported chain slugs: GET https://api.opensea.io/api/v2/chains
DEFAULT_CHAINS = [c.strip() for c in os.getenv("CHAINS", "ethereum").split(",") if c.strip()]

OPENSEA_BASE = "https://api.opensea.io/api/v2"
HEADERS = {"accept": "application/json", "x-api-key": OPENSEA_API_KEY}

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
# Maps the currency symbol OpenSea returns for a collection's floor price to
# a CoinGecko coin id, so we can look up a USD rate for it. Extend this if
# you hold NFTs priced in a token not listed here.
SYMBOL_TO_COINGECKO_ID = {
    "ETH": "ethereum",
    "WETH": "weth",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "SOL": "solana",
    "BNB": "binancecoin",
    "AVAX": "avalanche-2",
}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------------------
# OpenSea helpers
# ---------------------------------------------------------------------------

async def fetch_wallet_collections(session: aiohttp.ClientSession, wallet: str, chain: str) -> Counter:
    """Return a Counter of {collection slug: number of NFTs owned in it} for one chain."""
    counts = Counter()
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
                counts[slug] += 1

        cursor = data.get("next")
        if cursor:
            params = {"limit": 200, "next": cursor}
        else:
            url = None  # done paginating

    return counts


async def fetch_usd_rates(session: aiohttp.ClientSession, symbols: set[str]) -> dict[str, float]:
    """Return {symbol: usd_rate} for the given currency symbols, via CoinGecko.
    Symbols with no known CoinGecko id, or that fail to fetch, are simply omitted —
    callers should treat a missing symbol as 'USD conversion unavailable'."""
    coingecko_ids = {SYMBOL_TO_COINGECKO_ID[s] for s in symbols if s in SYMBOL_TO_COINGECKO_ID}
    if not coingecko_ids:
        return {}

    params = {"ids": ",".join(coingecko_ids), "vs_currencies": "usd"}
    try:
        async with session.get(COINGECKO_URL, params=params) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
    except aiohttp.ClientError:
        return {}

    rates = {}
    for symbol, cg_id in SYMBOL_TO_COINGECKO_ID.items():
        price = data.get(cg_id, {}).get("usd")
        if price is not None:
            rates[symbol] = float(price)
    return rates


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


async def build_portfolio(wallet: str, chains: list[str], top_n: int) -> tuple[list[dict], float]:
    """Fetch a wallet's collections across one or more chains.

    Returns (top_n collections by floor price descending, total_usd_worth).
    total_usd_worth sums floor_price * quantity_owned across ALL held
    collections (not just the top N shown), converted to USD where a rate
    is available. Collections in a currency with no known USD rate are
    excluded from the total and flagged per-collection instead.
    """
    async with aiohttp.ClientSession() as session:
        # One request set per chain — OpenSea has no combined "all chains" call
        chain_results = await asyncio.gather(
            *(fetch_wallet_collections(session, wallet, c) for c in chains),
            return_exceptions=True,
        )

        counts = Counter()
        for chain, result in zip(chains, chain_results):
            if isinstance(result, Exception):
                print(f"Skipping chain '{chain}': {result}")
                continue
            counts.update(result)

        # Fetch floor prices concurrently, but capped so we don't hammer the API
        semaphore = asyncio.Semaphore(5)

        async def bounded_fetch(slug):
            async with semaphore:
                return await fetch_collection_floor(session, slug)

        floor_results = await asyncio.gather(*(bounded_fetch(s) for s in counts))
        valid = [r for r in floor_results if r is not None]
        for r in valid:
            r["quantity"] = counts[r["slug"]]

        # Look up USD rates for every currency symbol we actually need
        symbols_needed = {r["symbol"] for r in valid}
        usd_rates = await fetch_usd_rates(session, symbols_needed)

    total_usd = 0.0
    for r in valid:
        rate = usd_rates.get(r["symbol"])
        if rate is not None:
            r["usd_value"] = r["floor_price"] * r["quantity"] * rate
            total_usd += r["usd_value"]
        else:
            r["usd_value"] = None  # no conversion available for this currency

    valid.sort(key=lambda r: r["floor_price"], reverse=True)
    return valid[:top_n], total_usd


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
    chains="Comma-separated chain slugs, e.g. ethereum,polygon,base,robinhood (default: CHAINS in .env)",
)
async def portfolio(
    interaction: discord.Interaction,
    wallet: str = None,
    top: int = 5,
    chains: str = None,
):
    wallet = wallet or DEFAULT_WALLET
    chain_list = [c.strip() for c in chains.split(",")] if chains else DEFAULT_CHAINS

    if not wallet:
        await interaction.response.send_message(
            "No wallet provided and no default WALLET_ADDRESS set in .env.", ephemeral=True
        )
        return

    await interaction.response.defer()  # OpenSea calls can take a few seconds, more so across chains

    try:
        top_collections, total_usd = await build_portfolio(wallet, chain_list, top)
    except Exception as e:
        await interaction.followup.send(f"Something went wrong: {e}")
        return

    if not top_collections:
        await interaction.followup.send("No collections with floor price data found for that wallet.")
        return

    embed = discord.Embed(
        title="🖼️ NFT Portfolio — Top Holdings by Floor Price",
        description=(
            f"Wallet: `{wallet}`\n"
            f"Chains: `{', '.join(chain_list)}`\n"
            f"**Estimated total worth: ${total_usd:,.2f}**"
        ),
        color=discord.Color.blurple(),
    )

    for i, c in enumerate(top_collections, start=1):
        usd_str = f" (${c['usd_value']:,.2f})" if c["usd_value"] is not None else " (USD rate unavailable)"
        qty_str = f" × {c['quantity']}" if c["quantity"] > 1 else ""
        embed.add_field(
            name=f"{i}. {c['slug']}",
            value=f"Floor: **{c['floor_price']} {c['symbol']}**{usd_str}{qty_str}",
            inline=False,
        )

    embed.set_footer(text="Totals are an estimate: floor price × quantity held, not guaranteed sale value.")

    await interaction.followup.send(embed=embed)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN missing — set it in your .env file.")
    if not OPENSEA_API_KEY:
        raise SystemExit("OPENSEA_API_KEY missing — set it in your .env file.")
    bot.run(DISCORD_TOKEN)
