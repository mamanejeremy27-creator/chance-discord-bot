"""
================================================================================
CHANCE DISCORD BOT
================================================================================
A comprehensive Discord bot for the Chance lottery platform on Base L2.

COMMANDS (15 total):
    Analysis:
        /rtp          - Calculate RTP and validate tiers
        /breakeven    - Calculate profit scenarios  
        /optimize     - Get optimized lottery parameters
        /suggest      - Reverse calculator (Prize + RTP → Parameters)
        /simulate     - Run Monte Carlo simulations
        /compare      - Compare two lottery setups
    
    Platform:
        /stats        - View live platform statistics
        /leaderboard  - See top creators, winners & volume
        /preview      - Preview lottery before launching
    
    Alerts:
        /alert        - Create custom lottery alerts
        /myalerts     - View your active alerts
        /deletealert  - Remove an alert
    
    Admin:
        /forceleaderboard - Force post leaderboards
        /posthelp         - Post help guide to channel
    
    Help:
        /help         - Show all commands

AUTO-FEATURES:
    - Lottery Monitor: Posts new lotteries every 30 seconds
    - Leaderboard Poster: Posts daily rankings at 12:00 UTC
    - Alert System: DMs users when matching lotteries appear

================================================================================
"""

import discord
from discord import app_commands
from discord.ext import commands
import os
import random
import asyncio
import aiohttp
from datetime import datetime, timezone
from dotenv import load_dotenv
from lottery_monitor import LotteryMonitor
from flask import Flask
from threading import Thread


# =============================================================================
# CONFIGURATION
# =============================================================================

load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
API_BASE_URL = os.getenv('CHANCE_API_URL', 'https://api.goldsky.com/api/public/project_cmjboofbdidyj01x8bi8t0xia/subgraphs/chance-lottery-testnet/2.0.0/gn')

CHANNEL_IDS = {
    'new_lotteries': int(os.getenv('CHANNEL_NEW_LOTTERIES', '0')),
    'high_value': int(os.getenv('CHANNEL_HIGH_VALUE', '0')),
    'budget_plays': int(os.getenv('CHANNEL_BUDGET_PLAYS', '0')),
    'moonshots': int(os.getenv('CHANNEL_MOONSHOTS', '0')),
    'leaderboard': int(os.getenv('CHANNEL_LEADERBOARD', '0')),
}


# =============================================================================
# FLASK WEB SERVER (Railway Health Checks)
# =============================================================================

flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Chance Discord Bot is running! ✅"

@flask_app.route('/health')
def health():
    return {"status": "healthy", "bot": "online"}

def run_flask():
    flask_app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("🌐 Web server started on port 8080")


# =============================================================================
# BOT SETUP
# =============================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)
lottery_monitor = LotteryMonitor(bot=bot, api_base_url=API_BASE_URL)


# =============================================================================
# RTP CALCULATOR CLASS
# =============================================================================

class RTPCalculator:
    """Calculator for lottery RTP and tier validation"""
    
    @staticmethod
    def calculate_rtp(prize: float, ticket_price: float, odds: int) -> float:
        """
        Calculate Return to Player percentage
        Formula: RTP = (Prize × Probability) / Ticket Price
        
        Args:
            prize: Prize amount in USDC
            ticket_price: Ticket price in USDC
            odds: Odds as pick range (e.g., 250 for 1-in-250)
        
        Returns:
            RTP as percentage (e.g., 80.0 for 80%)
        """
        probability = 1 / odds
        rtp = (prize * probability) / ticket_price
        return rtp * 100
    
    @staticmethod
    def get_minimum_rtp(prize: float) -> tuple[float, str]:
        """
        Get minimum RTP requirement based on prize tier
        
        Args:
            prize: Prize amount in USDC
        
        Returns:
            Tuple of (minimum_rtp_percentage, tier_name)
        """
        if prize < 100:
            return 0, "Below minimum ($100+)"
        elif prize < 10000:
            return 70, "$100-$10K tier"
        elif prize < 100000:
            return 60, "$10K-$100K tier"
        else:
            return 50, "$100K+ tier"
    
    @staticmethod
    def format_currency(amount: float) -> str:
        """Format currency with commas and 2 decimal places"""
        return f"${amount:,.2f}"
    
    @staticmethod
    def passes_minimum(rtp: float, minimum: float) -> bool:
        """Check if RTP meets minimum requirement"""
        return rtp >= minimum


# =============================================================================
# UTILITY FUNCTIONS (Used across multiple commands)
# =============================================================================

def format_currency(amount: float, short: bool = False) -> str:
    """
    Format currency with appropriate suffix
    
    Args:
        amount: Amount to format
        short: If True, use K/M suffixes for large numbers
    """
    if short:
        if amount >= 1_000_000:
            return f"${amount/1_000_000:.2f}M"
        elif amount >= 1_000:
            return f"${amount/1_000:.1f}K"
    return f"${amount:,.2f}"

def format_number(num: int) -> str:
    """Format number with commas"""
    return f"{num:,}"

def short_address(addr: str) -> str:
    """Shorten wallet address for display"""
    if not addr or len(addr) < 10:
        return addr or "Unknown"
    return f"{addr[:6]}...{addr[-4:]}"

def calculate_rtp(prize: float, ticket: float, odds: int) -> float:
    """Calculate RTP percentage"""
    if odds <= 0 or ticket <= 0:
        return 0
    return (prize / odds / ticket) * 100

def calculate_roi(prize: float, ticket: float, odds: int, affiliate: float = 0) -> float:
    """Calculate creator ROI percentage"""
    platform_fee = 0.05
    affiliate_rate = affiliate / 100
    gross = odds * ticket
    net = gross * (1 - platform_fee - affiliate_rate)
    profit = net - prize
    return (profit / prize) * 100 if prize > 0 else 0


# =============================================================================
# LEADERBOARD AUTO-POSTER
# =============================================================================


class LeaderboardPoster:
    """Auto-posts leaderboards to Discord on a schedule"""
    
    def __init__(self, bot, api_url: str):
        self.bot = bot
        self.api_url = api_url
        self.channel_id = None
        self.post_hour = 12  # Post at 12:00 UTC daily
        self.last_post_date = None
    
    def configure(self, channel_id: int, post_hour: int = 12):
        """Configure the leaderboard channel and posting time"""
        self.channel_id = channel_id
        self.post_hour = post_hour
    
    async def start(self, check_interval: int = 300):
        """Start the leaderboard posting loop (checks every 5 min by default)"""
        print(f"🏆 Leaderboard poster started (posts daily at {self.post_hour}:00 UTC)")
        
        while True:
            try:
                await self.check_and_post()
            except Exception as e:
                print(f"❌ Leaderboard poster error: {e}")
            
            await asyncio.sleep(check_interval)
    
    async def check_and_post(self):
        """Check if it's time to post and do so"""
        now = datetime.now(timezone.utc)
        today = now.date()
        
        # Only post once per day at the specified hour
        if self.last_post_date == today:
            return
        
        if now.hour != self.post_hour:
            return
        
        # It's time to post!
        print(f"📊 Posting daily leaderboards...")
        await self.post_all_leaderboards()
        self.last_post_date = today
    
    async def fetch_lottery_data(self):
        """Fetch lottery data from Goldsky"""
        query = """
        query GetLeaderboardData {
          lotteries(first: 1000, orderBy: createdAt, orderDirection: desc) {
            id
            prizeProvider
            prizeAmount
            ticketPrice
            ticketsSold
            grossRevenue
            status
            hasWinner
            winner
          }
        }
        """
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_url,
                json={"query": query},
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != 200:
                    return None
                
                try:
                    data = await response.json()
                except:
                    print("⚠️ Leaderboard API returned invalid JSON")
                    return None
                    
                if 'errors' in data:
                    return None
                
                return data.get('data', {}).get('lotteries', [])
    
    async def post_all_leaderboards(self):
        """Post all 3 leaderboards to the channel"""
        if not self.channel_id:
            return
        
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            print(f"❌ Leaderboard channel {self.channel_id} not found")
            return
        
        lotteries = await self.fetch_lottery_data()
        if not lotteries:
            print("❌ Could not fetch lottery data for leaderboards")
            return
        
        # Post header
        today = datetime.now(timezone.utc).strftime("%B %d, %Y")
        await channel.send(f"# 🏆 Daily Leaderboards - {today}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # Post each leaderboard with a small delay
        await self.post_creators_leaderboard(channel, lotteries)
        await asyncio.sleep(1)
        
        await self.post_winners_leaderboard(channel, lotteries)
        await asyncio.sleep(1)
        
        await self.post_volume_leaderboard(channel, lotteries)
        
        print("✅ Daily leaderboards posted!")
    
    def fmt(self, val):
        """Format currency"""
        if val >= 1_000_000:
            return f"${val/1_000_000:.2f}M"
        elif val >= 1_000:
            return f"${val/1_000:.1f}K"
        else:
            return f"${val:,.0f}"
    
    def short_addr(self, addr):
        """Shorten wallet address"""
        if not addr:
            return "Unknown"
        return f"`{addr[:6]}...{addr[-4:]}`"
    
    async def post_creators_leaderboard(self, channel, lotteries):
        """Post top creators leaderboard"""
        creator_stats = {}
        for lottery in lotteries:
            creator = lottery.get('prizeProvider', '').lower()
            if not creator:
                continue
            
            if creator not in creator_stats:
                creator_stats[creator] = {'count': 0, 'volume': 0, 'winners': 0}
            
            creator_stats[creator]['count'] += 1
            
            revenue_raw = lottery.get('grossRevenue', '0')
            try:
                revenue = int(revenue_raw) / 1_000_000 if revenue_raw else 0
            except:
                revenue = 0
            creator_stats[creator]['volume'] += revenue
            
            if lottery.get('hasWinner'):
                creator_stats[creator]['winners'] += 1
        
        sorted_creators = sorted(creator_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
        
        embed = discord.Embed(
            title="🎨 Top Creators",
            description="Ranked by lotteries created",
            color=discord.Color.gold()
        )
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        text = ""
        for i, (creator, stats) in enumerate(sorted_creators):
            medal = medals[i] if i < len(medals) else f"{i+1}."
            text += f"{medal} {self.short_addr(creator)} — **{stats['count']}** lotteries • {self.fmt(stats['volume'])} vol\n"
        
        if text:
            embed.add_field(name="Rankings", value=text, inline=False)
        else:
            embed.add_field(name="Rankings", value="No creators yet!", inline=False)
        
        await channel.send(embed=embed)
    
    async def post_winners_leaderboard(self, channel, lotteries):
        """Post top winners leaderboard"""
        winner_stats = {}
        for lottery in lotteries:
            if not lottery.get('hasWinner'):
                continue
            
            winner = lottery.get('winner', '').lower()
            if not winner:
                continue
            
            if winner not in winner_stats:
                winner_stats[winner] = {'wins': 0, 'total_won': 0}
            
            winner_stats[winner]['wins'] += 1
            
            prize_raw = lottery.get('prizeAmount', '0')
            try:
                prize = int(prize_raw) / 1_000_000 if prize_raw else 0
            except:
                prize = 0
            winner_stats[winner]['total_won'] += prize
        
        sorted_winners = sorted(winner_stats.items(), key=lambda x: x[1]['total_won'], reverse=True)[:10]
        
        embed = discord.Embed(
            title="💰 Top Winners",
            description="Ranked by total prizes won",
            color=discord.Color.green()
        )
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        text = ""
        for i, (winner, stats) in enumerate(sorted_winners):
            medal = medals[i] if i < len(medals) else f"{i+1}."
            text += f"{medal} {self.short_addr(winner)} — **{self.fmt(stats['total_won'])}** • {stats['wins']} wins\n"
        
        if text:
            embed.add_field(name="Rankings", value=text, inline=False)
        else:
            embed.add_field(name="Rankings", value="No winners yet!", inline=False)
        
        await channel.send(embed=embed)
    
    async def post_volume_leaderboard(self, channel, lotteries):
        """Post top volume leaderboard"""
        creator_volume = {}
        for lottery in lotteries:
            creator = lottery.get('prizeProvider', '').lower()
            if not creator:
                continue
            
            if creator not in creator_volume:
                creator_volume[creator] = {'volume': 0, 'tickets': 0}
            
            revenue_raw = lottery.get('grossRevenue', '0')
            try:
                revenue = int(revenue_raw) / 1_000_000 if revenue_raw else 0
            except:
                revenue = 0
            creator_volume[creator]['volume'] += revenue
            
            tickets_raw = lottery.get('ticketsSold', '0')
            try:
                tickets = int(tickets_raw) if tickets_raw else 0
            except:
                tickets = 0
            creator_volume[creator]['tickets'] += tickets
        
        sorted_volume = sorted(creator_volume.items(), key=lambda x: x[1]['volume'], reverse=True)[:10]
        
        embed = discord.Embed(
            title="📊 Top Volume",
            description="Ranked by total volume generated",
            color=discord.Color.blue()
        )
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        text = ""
        for i, (creator, stats) in enumerate(sorted_volume):
            medal = medals[i] if i < len(medals) else f"{i+1}."
            text += f"{medal} {self.short_addr(creator)} — **{self.fmt(stats['volume'])}** • {stats['tickets']:,} tickets\n"
        
        if text:
            embed.add_field(name="Rankings", value=text, inline=False)
        else:
            embed.add_field(name="Rankings", value="No volume yet!", inline=False)
        
        await channel.send(embed=embed)


# Initialize leaderboard poster
leaderboard_poster = LeaderboardPoster(bot=bot, api_url=API_BASE_URL)


# =============================================================================
# ALERT NOTIFICATION FUNCTION (must be defined before on_ready)
# =============================================================================

# Store user alerts in memory (resets on bot restart)
# Format: {user_id: [alert1, alert2, ...]}
user_alerts = {}

class AlertManager:
    """Manages user alerts for lottery notifications"""
    
    MAX_ALERTS_PER_USER = 5
    
    @staticmethod
    def add_alert(user_id: int, alert: dict) -> tuple:
        """Add an alert for a user. Returns (success, message)"""
        if user_id not in user_alerts:
            user_alerts[user_id] = []
        
        if len(user_alerts[user_id]) >= AlertManager.MAX_ALERTS_PER_USER:
            return False, f"You've reached the maximum of {AlertManager.MAX_ALERTS_PER_USER} alerts. Delete one first!"
        
        # Generate alert ID
        alert['id'] = len(user_alerts[user_id]) + 1
        user_alerts[user_id].append(alert)
        
        return True, f"Alert #{alert['id']} created!"
    
    @staticmethod
    def get_alerts(user_id: int) -> list:
        """Get all alerts for a user"""
        return user_alerts.get(user_id, [])
    
    @staticmethod
    def delete_alert(user_id: int, alert_id: int) -> tuple:
        """Delete an alert by ID. Returns (success, message)"""
        if user_id not in user_alerts:
            return False, "You don't have any alerts!"
        
        alerts = user_alerts[user_id]
        for i, alert in enumerate(alerts):
            if alert['id'] == alert_id:
                alerts.pop(i)
                # Renumber remaining alerts
                for j, a in enumerate(alerts):
                    a['id'] = j + 1
                return True, f"Alert #{alert_id} deleted!"
        
        return False, f"Alert #{alert_id} not found!"
    
    @staticmethod
    def check_lottery_matches(lottery: dict) -> list:
        """Check if a lottery matches any user alerts. Returns list of (user_id, alert)"""
        matches = []
        
        # Extract lottery values
        try:
            prize = int(lottery.get('prizeAmount', '0')) / 1_000_000
            ticket = int(lottery.get('ticketPrice', '0')) / 1_000_000
            
            # Calculate RTP if possible
            pick_range = lottery.get('pickRange', '0')
            try:
                odds = int(pick_range) if pick_range else 0
                rtp = (prize / odds / ticket * 100) if odds > 0 and ticket > 0 else 0
            except:
                rtp = 0
        except:
            return matches
        
        # Check each user's alerts
        for user_id, alerts in user_alerts.items():
            for alert in alerts:
                if AlertManager._lottery_matches_alert(prize, ticket, rtp, alert):
                    matches.append((user_id, alert))
        
        return matches
    
    @staticmethod
    def _lottery_matches_alert(prize: float, ticket: float, rtp: float, alert: dict) -> bool:
        """Check if lottery values match alert criteria"""
        # Check min prize
        if alert.get('min_prize') and prize < alert['min_prize']:
            return False
        
        # Check max prize
        if alert.get('max_prize') and prize > alert['max_prize']:
            return False
        
        # Check max ticket
        if alert.get('max_ticket') and ticket > alert['max_ticket']:
            return False
        
        # Check min RTP
        if alert.get('min_rtp') and rtp < alert['min_rtp']:
            return False
        
        return True


async def send_alert_notifications(bot_instance, lottery: dict, lottery_url: str):
    """Send DM notifications to users whose alerts match this lottery"""
    
    try:
        matches = AlertManager.check_lottery_matches(lottery)
        
        if not matches:
            return
        
        # Extract lottery info for the message
        try:
            prize = int(lottery.get('prizeAmount', '0')) / 1_000_000
            ticket = int(lottery.get('ticketPrice', '0')) / 1_000_000
            pick_range = lottery.get('pickRange', '0')
            odds = int(pick_range) if pick_range else 0
            rtp = (prize / odds / ticket * 100) if odds > 0 and ticket > 0 else 0
        except:
            return
        
        def fmt(val):
            return f"${val:,.2f}"
        
        # Create alert embed
        embed = discord.Embed(
            title="🔔 Lottery Alert!",
            description="A new lottery matches your criteria!",
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name="🏆 Prize",
            value=f"**{fmt(prize)}** USDC",
            inline=True
        )
        embed.add_field(
            name="🎫 Ticket",
            value=f"**{fmt(ticket)}** USDC",
            inline=True
        )
        embed.add_field(
            name="🎲 Odds",
            value=f"**1 in {odds:,}**" if odds > 0 else "N/A",
            inline=True
        )
        
        if rtp > 0:
            embed.add_field(
                name="📊 RTP",
                value=f"**{rtp:.1f}%**",
                inline=True
            )
        
        embed.add_field(
            name="🎮 Play Now",
            value=f"[Click to Play]({lottery_url})",
            inline=False
        )
        
        embed.set_footer(text="Manage alerts with /myalerts and /deletealert")
        
        # Send DM to each matching user
        sent_users = set()  # Avoid sending duplicate DMs
        
        for user_id, alert in matches:
            if user_id in sent_users:
                continue
            
            try:
                user = await bot_instance.fetch_user(user_id)
                if user:
                    await user.send(embed=embed)
                    sent_users.add(user_id)
                    print(f"🔔 Alert sent to user {user_id}")
            except discord.Forbidden:
                print(f"⚠️ Could not DM user {user_id} (DMs disabled)")
            except Exception as e:
                print(f"❌ Error sending alert to {user_id}: {e}")
    
    except Exception as e:
        print(f"❌ Error in send_alert_notifications: {e}")


@bot.event
async def on_ready():
    """Bot startup event"""
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guilds')
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Failed to sync commands: {e}')
    
    # Configure and start lottery monitor
    lottery_channels = {k: v for k, v in CHANNEL_IDS.items() if k != 'leaderboard'}
    if all(v for v in lottery_channels.values()):
        lottery_monitor.configure_channels(CHANNEL_IDS)
        lottery_monitor.set_alert_callback(send_alert_notifications)  # Set alert callback
        bot.loop.create_task(lottery_monitor.start(check_interval=30))
        print("✅ Lottery monitor enabled")
        print("✅ Alert notifications enabled")
    else:
        print("⚠️ Lottery monitor disabled - configure channel IDs in .env")
    
    # Configure and start leaderboard poster
    if CHANNEL_IDS.get('leaderboard'):
        leaderboard_poster.configure(
            channel_id=CHANNEL_IDS['leaderboard'],
            post_hour=12  # Post at 12:00 UTC daily
        )
        bot.loop.create_task(leaderboard_poster.start(check_interval=300))
        print("✅ Leaderboard auto-poster enabled (daily at 12:00 UTC)")
    else:
        print("⚠️ Leaderboard poster disabled - set CHANNEL_LEADERBOARD in .env")


# =============================================================================
# ADMIN COMMAND - Force Post Leaderboard
# =============================================================================

@bot.tree.command(name="forceleaderboard", description="[ADMIN] Force post leaderboards now")
@app_commands.default_permissions(administrator=True)
async def forceleaderboard_command(interaction: discord.Interaction):
    """Force post all leaderboards to the leaderboard channel (admin only)"""
    
    if not CHANNEL_IDS.get('leaderboard'):
        await interaction.response.send_message(
            "❌ **Error:** Leaderboard channel not configured! Set `CHANNEL_LEADERBOARD` in environment variables.",
            ephemeral=True
        )
        return
    
    await interaction.response.send_message(
        "📊 **Posting leaderboards now...** Check the leaderboard channel!",
        ephemeral=True
    )
    
    try:
        await leaderboard_poster.post_all_leaderboards()
        print("✅ Leaderboards force-posted by admin")
    except Exception as e:
        print(f"❌ Error force-posting leaderboards: {e}")


# =============================================================================
# ADMIN COMMAND - Post Help/Commands to Channel
# =============================================================================

@bot.tree.command(name="posthelp", description="[ADMIN] Post all bot commands to this channel")
@app_commands.default_permissions(administrator=True)
async def posthelp_command(interaction: discord.Interaction):
    """Post a full help guide to the current channel (admin only)"""
    
    await interaction.response.send_message(
        "📋 **Posting help guide...**",
        ephemeral=True
    )
    
    # Create main embed
    embed1 = discord.Embed(
        title="🎰 CHANCE BOT COMMANDS",
        description="Your complete toolkit for creating and analyzing lotteries!",
        color=discord.Color.gold()
    )
    
    embed1.add_field(
        name="📊 ANALYSIS COMMANDS",
        value=(
            "`/rtp` — Calculate RTP and validate tiers\n"
            "`/breakeven` — Calculate profit scenarios\n"
            "`/optimize` — Get optimized lottery parameters\n"
            "`/suggest` — Reverse calculator (Prize + RTP → Parameters)\n"
            "`/simulate` — Run 1000 Monte Carlo simulations\n"
            "`/compare` — Compare two lottery setups side-by-side"
        ),
        inline=False
    )
    
    embed1.add_field(
        name="📈 PLATFORM COMMANDS",
        value=(
            "`/stats` — View live platform statistics\n"
            "`/leaderboard` — See top creators, winners & volume\n"
            "`/preview` — Preview your lottery before launching"
        ),
        inline=False
    )
    
    embed1.add_field(
        name="🔔 ALERT COMMANDS",
        value=(
            "`/alert` — Create custom lottery alerts (get DM'd!)\n"
            "`/myalerts` — View your active alerts\n"
            "`/deletealert` — Remove an alert"
        ),
        inline=False
    )
    
    # Create examples embed
    embed2 = discord.Embed(
        title="🎯 EXAMPLES",
        color=discord.Color.blue()
    )
    
    embed2.add_field(
        name="Calculate RTP",
        value="`/rtp prize:5000 ticket:25 odds:250`",
        inline=False
    )
    
    embed2.add_field(
        name="Get Suggested Parameters",
        value="`/suggest prize:5000 target_rtp:75`",
        inline=False
    )
    
    embed2.add_field(
        name="Simulate Outcomes",
        value="`/simulate prize:5000 ticket:25 odds:250`",
        inline=False
    )
    
    embed2.add_field(
        name="Set an Alert",
        value="`/alert min_prize:10000 max_ticket:25`",
        inline=False
    )
    
    # Create RTP tiers embed
    embed3 = discord.Embed(
        title="📈 RTP TIERS",
        description=(
            "💰 **$100 - $10K** → Minimum 70% RTP\n"
            "💎 **$10K - $100K** → Minimum 60% RTP\n"
            "👑 **$100K+** → Minimum 50% RTP"
        ),
        color=discord.Color.green()
    )
    
    embed3.add_field(
        name="🎮 Ready to play?",
        value="**https://chance.fun**",
        inline=False
    )
    
    embed3.set_footer(text="Questions? Open a ticket in #support!")
    
    # Send all embeds to the channel (not ephemeral - visible to everyone)
    channel = interaction.channel
    await channel.send(embed=embed1)
    await channel.send(embed=embed2)
    await channel.send(embed=embed3)
    
    print(f"✅ Help guide posted to #{channel.name} by {interaction.user}")


# =============================================================================
# /SUGGEST COMMAND - Reverse Calculator (Prize + RTP → Parameters)
# =============================================================================

@bot.tree.command(name="suggest", description="Get 3 optimized setups for your prize and target RTP")
@app_commands.describe(
    prize="Prize amount in USDC (e.g., 5000)",
    target_rtp="Target RTP percentage for players (e.g., 75)",
    affiliate="Affiliate percentage (0-20, default 0)"
)
async def suggest_command(
    interaction: discord.Interaction,
    prize: float,
    target_rtp: float,
    affiliate: float = 0.0
):
    """
    Reverse calculator: Given prize and target RTP, suggest 3 parameter options
    """
    
    # Input validation
    if prize < 100:
        await interaction.response.send_message(
            "❌ **Error:** Minimum prize is $100 USDC",
            ephemeral=True
        )
        return
    
    if target_rtp <= 0 or target_rtp > 100:
        await interaction.response.send_message(
            "❌ **Error:** Target RTP must be between 1 and 100%",
            ephemeral=True
        )
        return
    
    if affiliate < 0 or affiliate > 20:
        await interaction.response.send_message(
            "❌ **Error:** Affiliate must be between 0 and 20%",
            ephemeral=True
        )
        return
    
    # Check RTP tier requirements
    calc = RTPCalculator()
    min_rtp, tier_name = calc.get_minimum_rtp(prize)
    
    if target_rtp < min_rtp:
        await interaction.response.send_message(
            f"❌ **Error:** Target RTP {target_rtp}% is below the minimum {min_rtp}% for {tier_name}!\n\n"
            f"For a ${prize:,.0f} prize, you need at least **{min_rtp}% RTP**.",
            ephemeral=True
        )
        return
    
    # Calculate max profitable RTP
    platform_fee = 0.05
    affiliate_rate = affiliate / 100
    net_rate = 1 - platform_fee - affiliate_rate
    max_profitable_rtp = net_rate * 100
    
    if target_rtp > max_profitable_rtp:
        await interaction.response.send_message(
            f"❌ **Error:** Target RTP {target_rtp}% is too high to be profitable!\n\n"
            f"With {affiliate}% affiliate + 5% platform fee, max profitable RTP is **{max_profitable_rtp:.1f}%**\n\n"
            f"💡 Lower your target RTP or reduce affiliate percentage.",
            ephemeral=True
        )
        return
    
    # RTP Formula: RTP = (Prize / Odds / Ticket) * 100
    # So: Ticket * Odds = Prize * 100 / RTP
    product = prize * 100 / target_rtp
    
    # Generate 3 options: Budget, Standard, Premium
    # Budget: Lower ticket price, higher odds (more accessible)
    # Standard: Medium ticket, medium odds
    # Premium: Higher ticket, lower odds (fewer players needed)
    
    options = []
    
    # Option 1: Budget (ticket ~0.5-1% of prize)
    budget_ticket = max(1, round(prize * 0.005, 2))  # 0.5% of prize, min $1
    if budget_ticket < 1:
        budget_ticket = 1
    budget_odds = int(product / budget_ticket)
    if budget_odds >= 10:
        options.append({
            'name': '💚 Budget Play',
            'desc': 'Low entry, high odds - accessible to everyone',
            'ticket': budget_ticket,
            'odds': budget_odds
        })
    
    # Option 2: Standard (ticket ~1-2% of prize)
    standard_ticket = max(5, round(prize * 0.01, 2))  # 1% of prize, min $5
    standard_odds = int(product / standard_ticket)
    if standard_odds >= 10:
        options.append({
            'name': '💛 Standard',
            'desc': 'Balanced entry and odds',
            'ticket': standard_ticket,
            'odds': standard_odds
        })
    
    # Option 3: Premium (ticket ~2-5% of prize)
    premium_ticket = max(10, round(prize * 0.025, 2))  # 2.5% of prize, min $10
    premium_odds = int(product / premium_ticket)
    if premium_odds >= 10:
        options.append({
            'name': '💎 Premium',
            'desc': 'Higher entry, better odds per ticket',
            'ticket': premium_ticket,
            'odds': premium_odds
        })
    
    # If we don't have 3 options, try to add more variations
    if len(options) < 3:
        # Try a micro option
        micro_ticket = 1
        micro_odds = int(product / micro_ticket)
        if micro_odds >= 10 and not any(o['ticket'] == 1 for o in options):
            options.insert(0, {
                'name': '🪙 Micro',
                'desc': '$1 entry - maximum accessibility',
                'ticket': micro_ticket,
                'odds': micro_odds
            })
    
    if len(options) < 3:
        # Try a whale option
        whale_ticket = max(50, round(prize * 0.05, 2))  # 5% of prize
        whale_odds = int(product / whale_ticket)
        if whale_odds >= 10:
            options.append({
                'name': '🐋 Whale',
                'desc': 'High entry, best odds',
                'ticket': whale_ticket,
                'odds': whale_odds
            })
    
    if not options:
        await interaction.response.send_message(
            "❌ **Error:** Could not generate valid options for these parameters.\n"
            "Try a different target RTP or prize amount.",
            ephemeral=True
        )
        return
    
    # Take first 3 options
    options = options[:3]
    
    # Create embed
    embed = discord.Embed(
        title="🎯 Suggested Lottery Parameters",
        description=f"**Prize:** ${prize:,.2f} USDC\n**Target RTP:** {target_rtp}%\n**Affiliate:** {affiliate}%",
        color=discord.Color.green()
    )
    
    # Calculate and add each option
    for opt in options:
        ticket = opt['ticket']
        odds = opt['odds']
        
        # Verify RTP
        actual_rtp = (prize / odds / ticket) * 100
        
        # Calculate ROI
        expected_gross = odds * ticket
        platform_cost = expected_gross * platform_fee
        affiliate_cost = expected_gross * affiliate_rate
        net_revenue = expected_gross - platform_cost - affiliate_cost
        profit = net_revenue - prize
        roi = (profit / prize) * 100
        
        # Calculate break-even
        net_per_ticket = ticket * net_rate
        breakeven = int(prize / net_per_ticket) + 1 if net_per_ticket > 0 else 0
        
        embed.add_field(
            name=opt['name'],
            value=(
                f"*{opt['desc']}*\n"
                f"🎫 **Ticket:** ${ticket:,.2f}\n"
                f"🎲 **Odds:** 1 in {odds:,}\n"
                f"📊 **RTP:** {actual_rtp:.1f}%\n"
                f"💰 **Your ROI:** {roi:.1f}%\n"
                f"⚖️ **Break-even:** {breakeven:,} tickets\n"
                f"💵 **Expected Profit:** ${profit:,.2f}"
            ),
            inline=True
        )
    
    # Add summary
    embed.add_field(
        name="📋 Summary",
        value=(
            f"All options achieve ~**{target_rtp}% RTP** for players\n"
            f"Min RTP required: {min_rtp}% ({tier_name}) ✅\n"
            f"Max profitable RTP: {max_profitable_rtp:.1f}%"
        ),
        inline=False
    )
    
    # Tips
    embed.add_field(
        name="💡 Tips",
        value=(
            "• **Budget** = More players, longer to fill\n"
            "• **Premium** = Fewer players needed, faster fill\n"
            "• Use `/preview` to see how it looks before launch"
        ),
        inline=False
    )
    
    embed.set_footer(text="Use /simulate to test any of these setups!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="rtp", description="Calculate RTP for a lottery and check if it meets tier minimums")
@app_commands.describe(
    prize="Prize amount in USDC (e.g., 5000)",
    ticket="Ticket price in USDC (e.g., 25)",
    odds="Odds as pick range - 1 in X (e.g., 250 for 1-in-250 odds)"
)
async def rtp_command(
    interaction: discord.Interaction,
    prize: float,
    ticket: float,
    odds: int
):
    """
    Calculate RTP and validate against tier minimums
    Responds ephemerally (only visible to user) and sends DM
    """
    
    # Input validation
    if prize <= 0 or ticket <= 0 or odds <= 0:
        await interaction.response.send_message(
            "❌ **Error:** All values must be positive numbers!",
            ephemeral=True
        )
        return
    
    if prize < 100:
        await interaction.response.send_message(
            "❌ **Error:** Minimum prize is $100 USDC",
            ephemeral=True
        )
        return
    
    if ticket > prize:
        await interaction.response.send_message(
            "❌ **Error:** Ticket price cannot exceed prize amount!",
            ephemeral=True
        )
        return
    
    # Calculate RTP
    calc = RTPCalculator()
    rtp = calc.calculate_rtp(prize, ticket, odds)
    min_rtp, tier_name = calc.get_minimum_rtp(prize)
    passes = calc.passes_minimum(rtp, min_rtp)
    
    # Format values
    prize_formatted = calc.format_currency(prize)
    ticket_formatted = calc.format_currency(ticket)
    
    # Determine status emoji and message
    if passes:
        status_emoji = "✅"
        status_msg = f"Meets {min_rtp}% minimum for {tier_name}"
    else:
        status_emoji = "❌"
        status_msg = f"Below {min_rtp}% minimum for {tier_name}"
        
    # Create embed for the response
    embed = discord.Embed(
        title="🎰 RTP Calculator Results",
        color=discord.Color.green() if passes else discord.Color.red(),
        description=f"Calculation for your lottery parameters"
    )
    
    embed.add_field(
        name="📊 Input Parameters",
        value=f"**Prize:** {prize_formatted} USDC\n**Ticket Price:** {ticket_formatted} USDC\n**Odds:** 1 in {odds:,}",
        inline=False
    )
    
    embed.add_field(
        name="📈 RTP Result",
        value=f"**{rtp:.2f}%** {status_emoji}",
        inline=True
    )
    
    embed.add_field(
        name="🎯 Tier Requirement",
        value=f"**{min_rtp}%** minimum\n({tier_name})",
        inline=True
    )
    
    embed.add_field(
        name="✨ Status",
        value=status_msg,
        inline=False
    )
    
    # Add helpful context
    if not passes:
        difference = min_rtp - rtp
        embed.add_field(
            name="💡 How to Fix",
            value=f"Your RTP is **{difference:.2f}%** too low.\n\n**Options:**\n• Increase prize amount\n• Decrease ticket price\n• Improve odds (lower pick range)",
            inline=False
        )
    else:
        # Calculate how competitive the RTP is
        if rtp >= 85:
            competitive_msg = "🔥 **Very competitive!** This is player-friendly RTP."
        elif rtp >= 75:
            competitive_msg = "✅ **Competitive.** Good balance of value and profit."
        elif rtp >= min_rtp + 5:
            competitive_msg = "⚠️ **Meets minimum** but competitors may offer better."
        else:
            competitive_msg = "⚠️ **Barely passes.** Consider increasing RTP to compete."
        
        embed.add_field(
            name="💡 Market Position",
            value=competitive_msg,
            inline=False
        )
    
    embed.set_footer(text="Chance RTP Calculator • Use /breakeven for profit calculations")
    
    # Send ephemeral response (only visible to user)
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # Try to send DM
    try:
        dm_embed = embed.copy()
        dm_embed.set_footer(text="This is your private RTP calculation from Chance Discord")
        
        await interaction.user.send(embed=dm_embed)
        
        # Send a follow-up letting them know they got a DM
        await interaction.followup.send(
            "📬 Check your DMs for a copy of your calculation!",
            ephemeral=True
        )
    except discord.Forbidden:
        # User has DMs disabled
        await interaction.followup.send(
            "⚠️ Couldn't send you a DM. Make sure your DMs are open to receive calculations!",
            ephemeral=True
        )
    except Exception as e:
        print(f"Error sending DM: {e}")

@bot.tree.command(name="help", description="Learn how to use the Chance RTP Calculator")
async def help_command(interaction: discord.Interaction):
    """Help command explaining how to use the bot"""
    
    embed = discord.Embed(
        title="🎰 Chance Discord Bot - Help",
        color=discord.Color.blue(),
        description="Your complete toolkit for creating and analyzing lotteries!"
    )
    
    embed.add_field(
        name="📊 Analysis Commands",
        value=(
            "**`/rtp`** - Calculate RTP\n"
            "**`/breakeven`** - Profit scenarios\n"
            "**`/optimize`** - Best parameters\n"
            "**`/suggest`** - 🆕 Reverse calculator\n"
            "**`/simulate`** - Monte Carlo sim\n"
            "**`/compare`** - Compare setups"
        ),
        inline=True
    )
    
    embed.add_field(
        name="📈 Platform Commands",
        value=(
            "**`/stats`** - Platform stats\n"
            "**`/leaderboard`** - Top users\n"
            "**`/preview`** - Preview lottery"
        ),
        inline=True
    )
    
    embed.add_field(
        name="🔔 Alert Commands",
        value=(
            "**`/alert`** - Create alert\n"
            "**`/myalerts`** - View alerts\n"
            "**`/deletealert`** - Remove alert"
        ),
        inline=True
    )
    
    embed.add_field(
        name="🎯 /suggest - Reverse Calculator",
        value=(
            "Tell us your prize & target RTP, get 3 optimized setups!\n"
            "`/suggest prize:5000 target_rtp:75`"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📈 RTP Tiers",
        value="**$100-$10K:** 70% • **$10K-$100K:** 60% • **$100K+:** 50%",
        inline=False
    )
    
    embed.set_footer(text="Need more help? Ask in #creator-support")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="breakeven", description="Calculate break-even and profit scenarios for a lottery")
@app_commands.describe(
    prize="Prize amount in USDC (e.g., 5000)",
    ticket="Ticket price in USDC (e.g., 25)",
    odds="Odds as pick range - 1 in X (e.g., 250 for 1-in-250 odds)",
    affiliate="Affiliate percentage (0-20, optional, default 0)"
)
async def breakeven_command(
    interaction: discord.Interaction,
    prize: float,
    ticket: float,
    odds: int,
    affiliate: float = 0.0
):
    """
    Calculate break-even point and profit scenarios for lottery creators
    """
    
    # Input validation
    if prize <= 0 or ticket <= 0 or od
