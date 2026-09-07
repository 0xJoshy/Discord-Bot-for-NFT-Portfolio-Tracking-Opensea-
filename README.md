# NFT Portfolio Watcher (Discord Bot)

A Discord bot that shows your top NFT collections ranked by OpenSea floor price.

## What it does

1. Looks up every NFT a wallet owns (via OpenSea's account-NFTs endpoint).
2. Groups them into unique collections.
3. Fetches the current floor price for each collection.
4. Posts a Discord embed with the top N collections, sorted highest floor first.

## 1. Get your credentials

**Discord bot token**
1. Go to https://discord.com/developers/applications → New Application.
2. Bot tab → Reset Token → copy it (this is `DISCORD_TOKEN`).
3. Under "Privileged Gateway Intents" you don't need any extra intents for this bot (it only uses slash commands).
4. OAuth2 → URL Generator → check `bot` and `applications.commands` scopes, then under Bot Permissions check `Send Messages` and `Embed Links`. Open the generated URL to invite the bot to your server.

**OpenSea API key**
1. Apply at https://docs.opensea.io/reference/api-keys (free tier is fine for personal use).
2. Approval can take anywhere from minutes to a couple of days.

## 2. Install dependencies

```bash
pip install -r requirements.txt
```

## 3. Configure

```bash
cp .env.example .env
```

Then edit `.env`:
- `DISCORD_TOKEN` — from step 1
- `OPENSEA_API_KEY` — from step 1
- `WALLET_ADDRESS` — (optional) your wallet, so people can run `/portfolio` with no arguments
- `CHAIN` — defaults to `ethereum`. Use `matic` for Polygon, `base` for Base, etc.

## 4. Run it

```bash
python bot.py
```

You should see `Logged in as YourBot#1234 — synced 1 command(s).`

## 5. Use it in Discord

```
/portfolio
/portfolio wallet:0xabc123... top:10
/portfolio wallet:0xabc123... chain:matic top:3
```

## Notes & things to customize next

- **Rate limits**: the bot fetches floor prices with up to 5 concurrent requests to stay polite to OpenSea's API. If you have a wallet with many collections, `/portfolio` may take a few seconds — that's why the bot "defers" its reply.
- **Auto-refresh**: right now this is on-demand (slash command). If you want it to auto-post every hour, add a `discord.ext.tasks.loop(hours=1)` job that calls `build_portfolio()` and edits/sends a message in a fixed channel — happy to add that if you want it.
- **Multiple wallets**: if you hold NFTs across several wallets, you'd extend `fetch_wallet_collections` to accept a list and merge the results before ranking.
- **NFT images**: OpenSea's NFT objects include an `image_url` — you could set `embed.set_thumbnail()` per top collection if you want visuals instead of just text.
- **Persistence**: nothing is stored — every call hits OpenSea live. For a "watcher" that alerts you on floor changes, you'd want to cache last-seen floor prices (e.g. in a small SQLite file or JSON) and diff against new values on each poll.
