"""
================================================================================
CHANCE DISCORD BOT
================================================================================
A comprehensive Discord bot for the Chance lottery platform on Base L2.

COMMANDS (25 total):
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
    
    Fun:
        /lucky        - Generate lucky numbers
        /faq          - Interactive FAQ browser
    
    Giveaways:
        /giveaway     - [ADMIN] Start a giveaway
        /endgiveaway  - [ADMIN] End giveaway early
        /reroll       - [ADMIN] Reroll winners
    
    Admin:
        /forceleaderboard - Force post leaderboards
        /forcestats       - Force post daily stats
        /posthelp         - Post help guide to channel
        /postfaq          - Post FAQ guide to channel
        /testwinner       - Test winner announcements
        /testendingsoon   - Test ending soon alerts
        /testmilestone    - Test milestone announcements
    
    Info:
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
from datetime import datetime, timezone, timedelta
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
    'winners': int(os.getenv('CHANNEL_WINNERS', '0')),           # All winners
    'big_wins': int(os.getenv('CHANNEL_BIG_WINS', '0')),         # $50K+ winners
    'daily_stats': int(os.getenv('CHANNEL_DAILY_STATS', '0')),   # Daily statistics
    'ending_soon': int(os.getenv('CHANNEL_ENDING_SOON', '0')),   # Lotteries ending soon
    'milestones': int(os.getenv('CHANNEL_MILESTONES', '0')),     # User milestones
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
# DAILY STATS AUTO-POSTER
# =============================================================================

class DailyStatsPoster:
    """Auto-posts daily statistics to Discord"""
    
    def __init__(self, bot, api_url: str):
        self.bot = bot
        self.api_url = api_url
        self.channel_id = None
        self.post_hour = 0  # Default: midnight UTC
        self.last_post_date = None
    
    def configure(self, channel_id: int, post_hour: int = 0):
        """Configure channel and posting time"""
        self.channel_id = channel_id
        self.post_hour = post_hour
    
    async def start(self, check_interval: int = 300):
        """Start the daily stats poster (checks every 5 minutes by default)"""
        print(f"📊 Daily stats poster started (posts at {self.post_hour}:00 UTC)")
        
        while True:
            try:
                await self.check_and_post()
            except Exception as e:
                print(f"❌ Daily stats error: {e}")
            
            await asyncio.sleep(check_interval)
    
    async def check_and_post(self):
        """Check if it's time to post daily stats"""
        now = datetime.now(timezone.utc)
        today = now.date()
        
        # Only post once per day at the configured hour
        if now.hour == self.post_hour and self.last_post_date != today:
            await self.post_daily_stats()
            self.last_post_date = today
    
    async def fetch_stats_data(self):
        """Fetch lottery data from the last 24 hours"""
        # Get timestamp for 24 hours ago
        now = datetime.now(timezone.utc)
        yesterday_timestamp = int((now.timestamp()) - 86400)
        
        query = """
        query GetDailyStats($since: BigInt!) {
          # Today's lotteries
          todayLotteries: lotteries(
            first: 1000
            where: { createdAt_gte: $since }
            orderBy: createdAt
            orderDirection: desc
          ) {
            id
            prizeAmount
            ticketPrice
            pickRange
            ticketsSold
            hasWinner
            winner
            createdAt
            status
            grossRevenue
          }
          
          # All completed lotteries with winners (for all-time stats)
          allWinners: lotteries(
            first: 1000
            where: { hasWinner: true }
          ) {
            id
            prizeAmount
            winner
          }
          
          # All lotteries for total volume
          allLotteries: lotteries(first: 1000) {
            id
            grossRevenue
            ticketsSold
          }
        }
        """
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_url,
                json={"query": query, "variables": {"since": str(yesterday_timestamp)}},
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != 200:
                    return None
                
                try:
                    data = await response.json()
                except:
                    return None
                
                if 'errors' in data:
                    return None
                
                return data.get('data', {})
    
    def calculate_stats(self, data):
        """Calculate all statistics from raw data"""
        today_lotteries = data.get('todayLotteries', [])
        all_winners = data.get('allWinners', [])
        all_lotteries = data.get('allLotteries', [])
        
        # Today's stats
        today_volume = 0
        today_tickets = 0
        today_winners = []
        today_new = len(today_lotteries)
        today_completed = 0
        today_active = 0
        ticket_prices = []
        most_tickets_lottery = None
        max_tickets = 0
        best_rtp_lottery = None
        best_rtp = 0
        biggest_buyer = {}  # wallet -> tickets bought
        
        for lottery in today_lotteries:
            prize_wei = int(lottery.get('prizeAmount', 0))
            prize = prize_wei / 1_000_000
            ticket_price_wei = int(lottery.get('ticketPrice', 0))
            ticket_price = ticket_price_wei / 1_000_000
            tickets_sold = int(lottery.get('ticketsSold', 0))
            pick_range = int(lottery.get('pickRange', 100))
            gross_wei = int(lottery.get('grossRevenue', 0))
            gross = gross_wei / 1_000_000
            
            today_volume += gross
            today_tickets += tickets_sold
            
            if ticket_price > 0:
                ticket_prices.append(ticket_price)
            
            # Check for winners
            if lottery.get('hasWinner'):
                today_completed += 1
                winner = lottery.get('winner', '')
                today_winners.append({
                    'prize': prize,
                    'winner': winner,
                    'lottery_id': lottery.get('id')
                })
            elif lottery.get('status') == 'ACTIVE':
                today_active += 1
            
            # Track most popular lottery
            if tickets_sold > max_tickets:
                max_tickets = tickets_sold
                most_tickets_lottery = {
                    'id': lottery.get('id'),
                    'tickets': tickets_sold
                }
            
            # Calculate RTP
            if ticket_price > 0 and pick_range > 0:
                rtp = (prize / pick_range / ticket_price) * 100
                if rtp > best_rtp and rtp <= 100:
                    best_rtp = rtp
                    best_rtp_lottery = {
                        'id': lottery.get('id'),
                        'rtp': rtp
                    }
        
        # Find biggest win today
        biggest_win = None
        if today_winners:
            biggest_win = max(today_winners, key=lambda x: x['prize'])
        
        # Lowest odds win (against all odds)
        against_all_odds = None
        for lottery in today_lotteries:
            if lottery.get('hasWinner'):
                pick_range = int(lottery.get('pickRange', 0))
                if against_all_odds is None or pick_range > against_all_odds.get('odds', 0):
                    prize_wei = int(lottery.get('prizeAmount', 0))
                    against_all_odds = {
                        'odds': pick_range,
                        'prize': prize_wei / 1_000_000,
                        'winner': lottery.get('winner'),
                        'lottery_id': lottery.get('id')
                    }
        
        # All-time stats
        total_volume = 0
        total_tickets = 0
        for lottery in all_lotteries:
            gross_wei = int(lottery.get('grossRevenue', 0))
            total_volume += gross_wei / 1_000_000
            total_tickets += int(lottery.get('ticketsSold', 0))
        
        total_winners = len(all_winners)
        total_paid_out = sum(int(l.get('prizeAmount', 0)) / 1_000_000 for l in all_winners)
        
        # Luckiest wallet today (most wins)
        winner_counts = {}
        for w in today_winners:
            addr = w.get('winner', '')
            if addr:
                winner_counts[addr] = winner_counts.get(addr, 0) + 1
        
        luckiest_wallet = None
        if winner_counts:
            luckiest_addr = max(winner_counts, key=winner_counts.get)
            luckiest_wallet = {
                'address': luckiest_addr,
                'wins': winner_counts[luckiest_addr]
            }
        
        # Average ticket price
        avg_ticket = sum(ticket_prices) / len(ticket_prices) if ticket_prices else 0
        
        return {
            'today_volume': today_volume,
            'today_tickets': today_tickets,
            'today_new': today_new,
            'today_completed': today_completed,
            'today_active': today_active,
            'today_winners_count': len(today_winners),
            'avg_ticket': avg_ticket,
            'biggest_win': biggest_win,
            'most_popular': most_tickets_lottery,
            'best_rtp': best_rtp_lottery,
            'against_all_odds': against_all_odds,
            'luckiest_wallet': luckiest_wallet,
            'total_volume': total_volume,
            'total_winners': total_winners,
            'total_paid_out': total_paid_out,
            'total_tickets': total_tickets,
        }
    
    async def post_daily_stats(self):
        """Post the daily stats embed"""
        if not self.channel_id:
            return
        
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            print(f"❌ Could not find daily stats channel: {self.channel_id}")
            return
        
        # Fetch data
        data = await self.fetch_stats_data()
        if not data:
            print("❌ Failed to fetch daily stats data")
            return
        
        # Calculate stats
        stats = self.calculate_stats(data)
        
        # Helper functions
        def fmt(val):
            if val >= 1_000_000:
                return f"${val/1_000_000:.2f}M"
            elif val >= 1_000:
                return f"${val/1_000:.1f}K"
            return f"${val:,.2f}"
        
        def short_addr(addr):
            if not addr or len(addr) < 10:
                return addr or "Unknown"
            return f"{addr[:6]}...{addr[-4:]}"
        
        # Get today's date
        today = datetime.now(timezone.utc).strftime("%B %d, %Y")
        
        # Create main embed
        embed = discord.Embed(
            title=f"📊 CHANCE DAILY STATS",
            description=f"**{today}**",
            color=discord.Color.blue()
        )
        
        # Volume section
        embed.add_field(
            name="💰 TODAY'S VOLUME",
            value=(
                f"📈 Volume: **{fmt(stats['today_volume'])}**\n"
                f"🎟️ Tickets Sold: **{stats['today_tickets']:,}**\n"
                f"💵 Avg Ticket: **{fmt(stats['avg_ticket'])}**"
            ),
            inline=True
        )
        
        # Lotteries section
        embed.add_field(
            name="🎰 LOTTERIES",
            value=(
                f"🆕 New: **{stats['today_new']}**\n"
                f"✅ Completed: **{stats['today_completed']}**\n"
                f"🔴 Active: **{stats['today_active']}**\n"
                f"🏆 Winners: **{stats['today_winners_count']}**"
            ),
            inline=True
        )
        
        # Highlights section
        highlights = []
        
        if stats['biggest_win']:
            highlights.append(f"💎 **Biggest Win:** {fmt(stats['biggest_win']['prize'])} ({short_addr(stats['biggest_win']['winner'])})")
        
        if stats['most_popular']:
            highlights.append(f"🔥 **Most Popular:** {stats['most_popular']['tickets']} tickets")
        
        if stats['best_rtp']:
            highlights.append(f"🎯 **Best RTP:** {stats['best_rtp']['rtp']:.1f}%")
        
        if stats['against_all_odds']:
            highlights.append(f"🍀 **Against All Odds:** 1 in {stats['against_all_odds']['odds']:,} ({short_addr(stats['against_all_odds']['winner'])})")
        
        if stats['luckiest_wallet'] and stats['luckiest_wallet']['wins'] > 1:
            highlights.append(f"🐴 **Luckiest Wallet:** {short_addr(stats['luckiest_wallet']['address'])} ({stats['luckiest_wallet']['wins']} wins!)")
        
        if highlights:
            embed.add_field(
                name="🏆 TODAY'S HIGHLIGHTS",
                value="\n".join(highlights) if highlights else "No highlights yet!",
                inline=False
            )
        
        # All-time stats
        embed.add_field(
            name="📈 PLATFORM TOTALS (All-Time)",
            value=(
                f"💰 Total Volume: **{fmt(stats['total_volume'])}**\n"
                f"🏆 Total Winners: **{stats['total_winners']:,}**\n"
                f"💸 Total Paid Out: **{fmt(stats['total_paid_out'])}**"
            ),
            inline=False
        )
        
        embed.set_footer(text="🍀 Play now at chance.fun • Stats reset daily at 00:00 UTC")
        
        await channel.send(embed=embed)
        print(f"📊 Daily stats posted for {today}")


# Initialize daily stats poster
daily_stats_poster = DailyStatsPoster(bot=bot, api_url=API_BASE_URL)


# =============================================================================
# ENDING SOON AUTO-POSTER
# =============================================================================

class EndingSoonPoster:
    """Auto-posts lotteries that are ending soon"""
    
    def __init__(self, bot, api_url: str):
        self.bot = bot
        self.api_url = api_url
        self.channel_id = None
        self.posted_alerts = {}  # Track posted alerts: {lottery_id: [posted_intervals]}
        # Alert intervals in minutes
        self.alert_intervals = [60, 30, 15, 5]  # 1 hour, 30 min, 15 min, 5 min
    
    def configure(self, channel_id: int):
        """Configure channel for posting"""
        self.channel_id = channel_id
    
    async def start(self, check_interval: int = 60):
        """Start the ending soon poster (checks every minute by default)"""
        print(f"⏰ Ending soon poster started (checking every {check_interval}s)")
        
        while True:
            try:
                await self.check_ending_soon()
            except Exception as e:
                print(f"❌ Ending soon error: {e}")
            
            await asyncio.sleep(check_interval)
    
    async def check_ending_soon(self):
        """Check for lotteries ending soon and post alerts"""
        if not self.channel_id:
            return
        
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            return
        
        # Get current timestamp
        now = datetime.now(timezone.utc)
        now_ts = int(now.timestamp())
        
        # Query for active lotteries
        query = """
        query GetActiveLotteries {
          lotteries(
            first: 100
            where: { status: ACTIVE }
            orderBy: endTime
            orderDirection: asc
          ) {
            id
            prizeAmount
            ticketPrice
            pickRange
            endTime
            maxTickets
            ticketsSold
            createdAt
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
                    return
                
                try:
                    data = await response.json()
                except:
                    return
                
                if 'errors' in data:
                    return
                
                lotteries = data.get('data', {}).get('lotteries', [])
        
        # Check each lottery
        for lottery in lotteries:
            lottery_id = lottery.get('id')
            end_time = int(lottery.get('endTime', 0))
            
            if end_time == 0:
                continue
            
            # Calculate minutes until end
            minutes_left = (end_time - now_ts) / 60
            
            # Skip if already ended or too far away
            if minutes_left <= 0 or minutes_left > 65:
                continue
            
            # Initialize tracking for this lottery
            if lottery_id not in self.posted_alerts:
                self.posted_alerts[lottery_id] = []
            
            # Check each alert interval
            for interval in self.alert_intervals:
                # Check if we should alert for this interval
                # Alert when minutes_left is within 2 minutes of the interval
                if interval - 2 <= minutes_left <= interval + 2:
                    # Haven't posted this interval yet
                    if interval not in self.posted_alerts[lottery_id]:
                        await self.post_ending_soon(channel, lottery, minutes_left, interval)
                        self.posted_alerts[lottery_id].append(interval)
        
        # Clean up old entries (lotteries that have ended)
        to_remove = []
        for lottery_id in self.posted_alerts:
            # Keep for 2 hours then clean up
            if len(self.posted_alerts[lottery_id]) >= len(self.alert_intervals):
                to_remove.append(lottery_id)
        
        for lottery_id in to_remove:
            if len(self.posted_alerts) > 1000:  # Only clean if getting large
                del self.posted_alerts[lottery_id]
    
    async def post_ending_soon(self, channel, lottery: dict, minutes_left: float, interval: int):
        """Post an ending soon alert"""
        
        # Extract data
        lottery_id = lottery.get('id', '')
        prize_wei = int(lottery.get('prizeAmount', 0))
        prize = prize_wei / 1_000_000
        ticket_price_wei = int(lottery.get('ticketPrice', 0))
        ticket_price = ticket_price_wei / 1_000_000
        tickets_sold = int(lottery.get('ticketsSold', 0))
        max_tickets = int(lottery.get('maxTickets', 0))
        pick_range = int(lottery.get('pickRange', 0))
        
        # Calculate RTP
        rtp = (prize / pick_range / ticket_price * 100) if pick_range > 0 and ticket_price > 0 else 0
        
        # Tickets remaining
        tickets_left = max_tickets - tickets_sold if max_tickets > 0 else "∞"
        
        # Format time
        if minutes_left >= 60:
            time_str = f"{int(minutes_left // 60)}h {int(minutes_left % 60)}m"
        else:
            time_str = f"{int(minutes_left)}m"
        
        # Urgency level
        if interval <= 5:
            color = discord.Color.red()
            urgency = "🚨 FINAL CALL!"
            title = f"⏰ ENDING IN {time_str}! 🚨"
        elif interval <= 15:
            color = discord.Color.orange()
            urgency = "⚠️ HURRY!"
            title = f"⏰ ENDING IN {time_str}!"
        elif interval <= 30:
            color = discord.Color.gold()
            urgency = "⏳ Don't miss out!"
            title = f"⏰ Ending in {time_str}"
        else:
            color = discord.Color.blue()
            urgency = "🔔 Last chance coming up!"
            title = f"⏰ Ending in {time_str}"
        
        # Create embed
        embed = discord.Embed(
            title=title,
            description=f"**{urgency}**",
            color=color
        )
        
        embed.add_field(
            name="🏆 Prize",
            value=f"**${prize:,.2f}**",
            inline=True
        )
        
        embed.add_field(
            name="🎫 Ticket",
            value=f"**${ticket_price:,.2f}**",
            inline=True
        )
        
        embed.add_field(
            name="🎲 Odds",
            value=f"**1 in {pick_range:,}**",
            inline=True
        )
        
        embed.add_field(
            name="📊 Stats",
            value=f"🎟️ Sold: **{tickets_sold:,}**\n🎯 RTP: **{rtp:.1f}%**",
            inline=True
        )
        
        embed.add_field(
            name="⏱️ Time Left",
            value=f"**{time_str}**",
            inline=True
        )
        
        if isinstance(tickets_left, int):
            embed.add_field(
                name="🎫 Spots Left",
                value=f"**{tickets_left:,}**",
                inline=True
            )
        
        # Add lottery link
        lottery_url = f"https://chance.fun/lottery/{lottery_id}"
        embed.add_field(
            name="🎮 Play Now",
            value=f"**[Click to Enter]({lottery_url})**",
            inline=False
        )
        
        embed.set_footer(text="⏰ Don't miss your chance! • chance.fun")
        
        # Send with ping for urgent ones
        if interval <= 5:
            await channel.send("🚨 **LAST CALL!** 🚨", embed=embed)
        else:
            await channel.send(embed=embed)
        
        print(f"⏰ Posted ending soon alert: Lottery {lottery_id[:8]}... ({int(minutes_left)}min left)")


# Initialize ending soon poster
ending_soon_poster = EndingSoonPoster(bot=bot, api_url=API_BASE_URL)


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
    lottery_channels = {k: v for k, v in CHANNEL_IDS.items() if k not in ['leaderboard', 'winners', 'big_wins', 'daily_stats', 'ending_soon', 'milestones']}
    if all(v for v in lottery_channels.values()):
        lottery_monitor.configure_channels(CHANNEL_IDS)
        lottery_monitor.set_alert_callback(send_alert_notifications)  # Set alert callback
        bot.loop.create_task(lottery_monitor.start(check_interval=30))
        print("✅ Lottery monitor enabled")
        print("✅ Alert notifications enabled")
        if CHANNEL_IDS.get('winners'):
            print("✅ Winner announcements enabled (Recent Winners)")
        if CHANNEL_IDS.get('big_wins'):
            print("✅ Big Wins announcements enabled ($50K+)")
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
    
    # Configure and start daily stats poster
    if CHANNEL_IDS.get('daily_stats'):
        daily_stats_poster.configure(
            channel_id=CHANNEL_IDS['daily_stats'],
            post_hour=0  # Post at 00:00 UTC daily (midnight)
        )
        bot.loop.create_task(daily_stats_poster.start(check_interval=300))
        print("✅ Daily stats auto-poster enabled (daily at 00:00 UTC)")
    else:
        print("⚠️ Daily stats poster disabled - set CHANNEL_DAILY_STATS in .env")
    
    # Configure and start ending soon poster
    if CHANNEL_IDS.get('ending_soon'):
        ending_soon_poster.configure(
            channel_id=CHANNEL_IDS['ending_soon']
        )
        bot.loop.create_task(ending_soon_poster.start(check_interval=60))
        print("✅ Ending soon poster enabled (alerts at 1h, 30m, 15m, 5m)")
    else:
        print("⚠️ Ending soon poster disabled - set CHANNEL_ENDING_SOON in .env")
    
    # Configure milestone tracker
    if CHANNEL_IDS.get('milestones'):
        milestone_tracker.configure(
            channel_id=CHANNEL_IDS['milestones']
        )
        print("✅ Milestone announcements enabled")
    else:
        print("⚠️ Milestones disabled - set CHANNEL_MILESTONES in .env")


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
# ADMIN COMMAND - Force Post Daily Stats
# =============================================================================

@bot.tree.command(name="forcestats", description="[ADMIN] Force post daily stats now")
@app_commands.default_permissions(administrator=True)
async def forcestats_command(interaction: discord.Interaction):
    """Force post daily stats to the daily stats channel (admin only)"""
    
    if not CHANNEL_IDS.get('daily_stats'):
        await interaction.response.send_message(
            "❌ **Error:** Daily stats channel not configured! Set `CHANNEL_DAILY_STATS` in environment variables.",
            ephemeral=True
        )
        return
    
    await interaction.response.send_message(
        "📊 **Posting daily stats now...** Check the daily-stats channel!",
        ephemeral=True
    )
    
    try:
        await daily_stats_poster.post_daily_stats()
        print("✅ Daily stats force-posted by admin")
    except Exception as e:
        print(f"❌ Error force-posting daily stats: {e}")


# =============================================================================
# ADMIN COMMAND - Test Winner Announcement
# =============================================================================

@bot.tree.command(name="testwinner", description="[ADMIN] Test winner announcement with fake data")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    prize="Prize amount to test (default: 5000)",
    big_win="Test as big win $50K+ (default: False)"
)
async def testwinner_command(
    interaction: discord.Interaction,
    prize: float = 5000.0,
    big_win: bool = False
):
    """Test winner announcement system (admin only)"""
    
    # If big_win is True, set prize to 50K+
    if big_win and prize < 50000:
        prize = 75000.0
    
    # Debug info
    winners_id = CHANNEL_IDS.get('winners')
    big_wins_id = CHANNEL_IDS.get('big_wins')
    
    await interaction.response.send_message(
        f"🧪 **Testing winner announcement...**\n"
        f"Prize: ${prize:,.2f}\n"
        f"Big Win: {big_win}\n\n"
        f"**Debug Info:**\n"
        f"CHANNEL_WINNERS ID: `{winners_id}`\n"
        f"CHANNEL_BIG_WINS ID: `{big_wins_id}`",
        ephemeral=True
    )
    
    # Create fake lottery data
    fake_lottery = {
        'id': 'TEST-' + str(int(datetime.now().timestamp())),
        'winner': '0x1234567890abcdef1234567890abcdef12345678',
        'prizeAmount': int(prize * 1_000_000),  # Convert to Wei
        'ticketPrice': int(25 * 1_000_000),     # $25 ticket
        'ticketsSold': int(prize / 20),          # Simulate ~20x tickets
        'pickRange': 250
    }
    
    # Post to #recent-winners
    winners_channel_id = CHANNEL_IDS.get('winners')
    if winners_channel_id and winners_channel_id != 0:
        channel = bot.get_channel(winners_channel_id)
        if channel:
            # Create standard winner embed
            short_winner = "0x1234...5678"
            tickets_sold = fake_lottery['ticketsSold']
            total_pot = tickets_sold * 25
            
            embed = discord.Embed(
                title="🧪 TEST: 🎉 WE HAVE A WINNER! 🎉",
                description=f"Congratulations to our lucky winner!",
                color=discord.Color.gold()
            )
            
            embed.add_field(name="🏆 Winner", value=f"`{short_winner}`", inline=True)
            embed.add_field(name="💰 Prize Won", value=f"**${prize:,.2f}** USDC", inline=True)
            embed.add_field(name="🎫 Winning Odds", value=f"1 in 250", inline=True)
            embed.add_field(
                name="📊 Lottery Stats",
                value=f"🎟️ Tickets Sold: **{tickets_sold:,}**\n💵 Total Pot: **${total_pot:,.2f}**",
                inline=False
            )
            embed.set_footer(text="🧪 THIS IS A TEST - Not a real winner")
            
            await channel.send(embed=embed)
            print(f"🧪 Test winner posted to #recent-winners")
        else:
            print(f"❌ Could not find channel with ID: {winners_channel_id}")
    else:
        print(f"⚠️ CHANNEL_WINNERS not set or is 0: {winners_channel_id}")
    
    # Post to #big-wins if prize >= $50K
    if prize >= 50000:
        big_wins_channel_id = CHANNEL_IDS.get('big_wins')
        if big_wins_channel_id:
            big_channel = bot.get_channel(big_wins_channel_id)
            if big_channel:
                short_winner = "0x1234...5678"
                tickets_sold = fake_lottery['ticketsSold']
                total_pot = tickets_sold * 25
                
                big_embed = discord.Embed(
                    title="🧪 TEST: 🚀💰 MASSIVE WIN! 💰🚀",
                    description=f"# ${prize:,.0f} JACKPOT! 🎰",
                    color=discord.Color.from_rgb(255, 215, 0)
                )
                
                big_embed.add_field(name="🏆 Lucky Winner", value=f"`{short_winner}`", inline=True)
                big_embed.add_field(name="💎 Prize Won", value=f"**${prize:,.2f}** USDC", inline=True)
                big_embed.add_field(name="🎯 Odds Beaten", value=f"**1 in 250**", inline=True)
                big_embed.add_field(
                    name="📊 Lottery Stats",
                    value=f"🎟️ Tickets Sold: **{tickets_sold:,}**\n💵 Total Pot: **${total_pot:,.2f}**\n🎫 Ticket Price: **$25.00**",
                    inline=False
                )
                big_embed.set_footer(text="🧪 THIS IS A TEST - Not a real winner")
                
                await big_channel.send("🧪 **TEST** - @everyone 🚨 **HUGE WIN ALERT!** 🚨", embed=big_embed)
                print(f"🧪 Test BIG WIN posted to #big-wins")


# =============================================================================
# ADMIN COMMAND - Test Ending Soon Alert
# =============================================================================

@bot.tree.command(name="testendingsoon", description="[ADMIN] Test ending soon alert with fake data")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    minutes="Minutes until end (5, 15, 30, or 60)",
    prize="Prize amount to test (default: 5000)"
)
async def testendingsoon_command(
    interaction: discord.Interaction,
    minutes: int = 15,
    prize: float = 5000.0
):
    """Test ending soon alert system (admin only)"""
    
    ending_soon_id = CHANNEL_IDS.get('ending_soon')
    
    await interaction.response.send_message(
        f"🧪 **Testing ending soon alert...**\n"
        f"Minutes Left: {minutes}\n"
        f"Prize: ${prize:,.2f}\n\n"
        f"**Debug Info:**\n"
        f"CHANNEL_ENDING_SOON ID: `{ending_soon_id}`",
        ephemeral=True
    )
    
    if not ending_soon_id or ending_soon_id == 0:
        print(f"⚠️ CHANNEL_ENDING_SOON not set")
        return
    
    channel = bot.get_channel(ending_soon_id)
    if not channel:
        print(f"❌ Could not find channel with ID: {ending_soon_id}")
        return
    
    # Determine urgency level based on minutes
    if minutes <= 5:
        color = discord.Color.red()
        urgency = "🚨 FINAL CALL!"
        title = f"🧪 TEST: ⏰ ENDING IN {minutes}m! 🚨"
    elif minutes <= 15:
        color = discord.Color.orange()
        urgency = "⚠️ HURRY!"
        title = f"🧪 TEST: ⏰ ENDING IN {minutes}m!"
    elif minutes <= 30:
        color = discord.Color.gold()
        urgency = "⏳ Don't miss out!"
        title = f"🧪 TEST: ⏰ Ending in {minutes}m"
    else:
        color = discord.Color.blue()
        urgency = "🔔 Last chance coming up!"
        title = f"🧪 TEST: ⏰ Ending in {minutes}m"
    
    # Calculate fake stats
    ticket_price = 25.0
    pick_range = 250
    tickets_sold = int(prize / 20)
    rtp = (prize / pick_range / ticket_price) * 100
    
    # Create embed
    embed = discord.Embed(
        title=title,
        description=f"**{urgency}**",
        color=color
    )
    
    embed.add_field(name="🏆 Prize", value=f"**${prize:,.2f}**", inline=True)
    embed.add_field(name="🎫 Ticket", value=f"**${ticket_price:,.2f}**", inline=True)
    embed.add_field(name="🎲 Odds", value=f"**1 in {pick_range:,}**", inline=True)
    embed.add_field(
        name="📊 Stats",
        value=f"🎟️ Sold: **{tickets_sold:,}**\n🎯 RTP: **{rtp:.1f}%**",
        inline=True
    )
    embed.add_field(name="⏱️ Time Left", value=f"**{minutes}m**", inline=True)
    embed.add_field(name="🎫 Spots Left", value=f"**{pick_range - tickets_sold:,}**", inline=True)
    
    embed.add_field(
        name="🎮 Play Now",
        value=f"**[Click to Enter](https://chance.fun)**",
        inline=False
    )
    
    embed.set_footer(text="🧪 THIS IS A TEST - Not a real lottery")
    
    # Send with appropriate message
    if minutes <= 5:
        await channel.send("🧪 **TEST** - 🚨 **LAST CALL!** 🚨", embed=embed)
    else:
        await channel.send(embed=embed)
    
    print(f"🧪 Test ending soon alert posted ({minutes}min)")


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
# ADMIN COMMAND - Post FAQ to Channel
# =============================================================================

@bot.tree.command(name="postfaq", description="[ADMIN] Post all FAQ categories to this channel")
@app_commands.default_permissions(administrator=True)
async def postfaq_command(interaction: discord.Interaction):
    """Post all FAQ categories to the current channel (admin only)"""
    
    await interaction.response.send_message(
        "📋 **Posting FAQ guide...**",
        ephemeral=True
    )
    
    channel = interaction.channel
    
    # Header embed
    header = discord.Embed(
        title="❓ CHANCE FAQ",
        description="Everything you need to know about Chance.fun!\n\n**Use `/faq` to browse interactively or read below 👇**",
        color=discord.Color.blue()
    )
    header.set_footer(text="chance.fun • Provably fair lotteries on Base")
    await channel.send(embed=header)
    
    # Define all FAQs
    faqs = [
        {
            "title": "🚀 GETTING STARTED",
            "color": discord.Color.green(),
            "questions": [
                ("What is Chance?", "Chance is a provably fair lottery platform on Base where players buy tickets to win prizes, and anyone can create their own lotteries to earn revenue."),
                ("How do I connect my wallet?", "Click 'Connect Wallet' on chance.fun. We support MetaMask, Coinbase Wallet, and other EOA wallets. You can also use a Smart Wallet for gasless transactions."),
                ("Do I need to pay gas fees?", "**No gas fees!** Chance uses Account Abstraction (ERC-4337) so all transactions are gasless. You only pay the ticket price in USDC."),
                ("What currency does Chance use?", "All prizes and tickets are in **USDC** on Base L2."),
            ]
        },
        {
            "title": "🎰 PLAYING LOTTERIES",
            "color": discord.Color.blue(),
            "questions": [
                ("How do I buy a ticket?", "Browse lotteries → Select one → Pick your number(s) → Buy ticket → Watch the instant draw animation → See if you won!"),
                ("How are winners selected?", "Winners are selected using **Pyth Entropy (VRF)** - a verifiable random function. Every draw is provably random and you can verify it on-chain."),
                ("How fast do I get paid if I win?", "**Instantly!** Results and payouts happen immediately after purchase. The prize is auto-sent to your wallet."),
                ("What do the odds mean?", "Odds like '1 in 250' mean if you pick correctly out of 250 numbers, you win. Higher odds = bigger potential prizes but lower chance of winning."),
                ("What is RTP?", "**Return to Player** - the percentage of ticket sales returned as prizes. 70% RTP means for every $100 in tickets, $70 goes to winners on average."),
            ]
        },
        {
            "title": "👑 CREATING LOTTERIES",
            "color": discord.Color.purple(),
            "questions": [
                ("How do I create a lottery?", "Click 'Create Lottery' → Set your prize, ticket price, max tickets, duration, and pick range → Upload an image → Publish! Your prize is escrowed on-chain."),
                ("What parameters can I set?", "**Prize Amount** (total pool), **Ticket Price**, **Max Tickets**, **Duration**, **Pick Range** (odds), and **Referral Commission Rate**."),
                ("What are the RTP requirements?", "• $100-$10K prizes: **70% minimum RTP**\n• $10K-$100K prizes: **60% minimum RTP**\n• $100K+ prizes: **50% minimum RTP**"),
                ("How do I earn as a creator?", "You earn from ticket sales minus the prize, platform fee (5%), and any referral commissions. Use `/breakeven` to calculate your profits!"),
                ("When can I claim my revenue?", "After your lottery completes (winner drawn or expired), claim your revenue from the Creator Dashboard."),
            ]
        },
        {
            "title": "🤝 REFERRALS",
            "color": discord.Color.orange(),
            "questions": [
                ("How do referrals work?", "Generate a referral link for any lottery → Share it → When someone buys through your link, you earn a commission set by the creator."),
                ("How do I get my referral link?", "On any lottery page, click 'Share' or 'Referral Link'. The link is signed with your wallet to track your referrals."),
                ("How much can I earn?", "Commission rates are set by lottery creators (typically 0-20% of ticket price). Check each lottery for its referral rate."),
                ("When do I get paid?", "Referral earnings accrue as your referees buy tickets. Claim your commissions from the Referral Dashboard after lotteries settle."),
            ]
        },
        {
            "title": "🔐 TRUST & FAIRNESS",
            "color": discord.Color.gold(),
            "questions": [
                ("Is Chance provably fair?", "**Yes!** Every draw uses Pyth Entropy (VRF) for verifiable randomness. You can check the proof on-chain yourself."),
                ("Can creators rig their lotteries?", "**No.** Winners are determined by on-chain VRF, not by creators. Smart contracts hold all funds - no human can manipulate results."),
                ("Where are the funds held?", "All funds (prizes, ticket sales) are held in smart contracts on Base, not by any person or company."),
                ("How can I verify a draw?", "Every lottery shows a 'View on Chain' link. Click it to see the transaction proof on Basescan."),
            ]
        },
        {
            "title": "💰 FEES & PAYOUTS",
            "color": discord.Color.red(),
            "questions": [
                ("What fees does Chance charge?", "**5% platform fee** on ticket sales. No gas fees for users (gasless transactions)."),
                ("How fast are payouts?", "**Instant!** Winners receive prizes immediately after the draw. Creator revenue can be claimed once the lottery completes."),
                ("Is there a minimum withdrawal?", "No minimum! Claim any amount from your dashboard."),
                ("What if a lottery doesn't fill?", "If a lottery expires without a winner, the creator can reclaim their prize and any ticket revenue is still distributed."),
            ]
        },
    ]
    
    # Post each category
    for faq in faqs:
        embed = discord.Embed(
            title=faq["title"],
            color=faq["color"]
        )
        
        for q, a in faq["questions"]:
            embed.add_field(
                name=f"Q: {q}",
                value=a,
                inline=False
            )
        
        await channel.send(embed=embed)
        await asyncio.sleep(0.5)  # Small delay to avoid rate limits
    
    # Footer embed
    footer = discord.Embed(
        title="🎮 Ready to Play?",
        description="**🌐 Website:** https://chance.fun\n**💬 Support:** Open a ticket in #support\n**🤖 Bot Help:** Use `/help` for bot commands",
        color=discord.Color.blue()
    )
    footer.set_footer(text="Good luck! 🍀")
    await channel.send(embed=footer)
    
    print(f"✅ FAQ posted to #{channel.name} by {interaction.user}")


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
    
    embed.add_field(
        name="❓ Need More Info?",
        value="**`/faq`** - Browse FAQ categories\n**`/faq category:play`** - Jump to a topic",
        inline=False
    )
    
    embed.set_footer(text="Need more help? Ask in #creator-support")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# =============================================================================
# FAQ COMMAND WITH INTERACTIVE BUTTONS
# =============================================================================

# FAQ Data (shared between command and buttons)
FAQ_DATA = {
    "start": {
        "title": "🚀 Getting Started",
        "color": discord.Color.green(),
        "questions": [
            ("What is Chance?", "Chance is a provably fair lottery platform on Base where players buy tickets to win prizes, and anyone can create their own lotteries to earn revenue."),
            ("How do I connect my wallet?", "Click 'Connect Wallet' on chance.fun. We support MetaMask, Coinbase Wallet, and other EOA wallets. You can also use a Smart Wallet for gasless transactions."),
            ("Do I need to pay gas fees?", "**No gas fees!** Chance uses Account Abstraction (ERC-4337) so all transactions are gasless. You only pay the ticket price in USDC."),
            ("What currency does Chance use?", "All prizes and tickets are in **USDC** on Base L2."),
        ]
    },
    "play": {
        "title": "🎰 Playing Lotteries",
        "color": discord.Color.blue(),
        "questions": [
            ("How do I buy a ticket?", "Browse lotteries → Select one → Pick your number(s) → Buy ticket → Watch the instant draw animation → See if you won!"),
            ("How are winners selected?", "Winners are selected using **Pyth Entropy (VRF)** - a verifiable random function. Every draw is provably random and you can verify it on-chain."),
            ("How fast do I get paid if I win?", "**Instantly!** Results and payouts happen immediately after purchase. The prize is auto-sent to your wallet."),
            ("What do the odds mean?", "Odds like '1 in 250' mean if you pick correctly out of 250 numbers, you win. Higher odds = bigger potential prizes but lower chance of winning."),
            ("What is RTP?", "**Return to Player** - the percentage of ticket sales returned as prizes. 70% RTP means for every $100 in tickets, $70 goes to winners on average."),
        ]
    },
    "create": {
        "title": "👑 Creating Lotteries",
        "color": discord.Color.purple(),
        "questions": [
            ("How do I create a lottery?", "Click 'Create Lottery' → Set your prize, ticket price, max tickets, duration, and pick range → Upload an image → Publish! Your prize is escrowed on-chain."),
            ("What parameters can I set?", "**Prize Amount** (total pool), **Ticket Price**, **Max Tickets**, **Duration**, **Pick Range** (odds), and **Referral Commission Rate**."),
            ("What are the RTP requirements?", "• $100-$10K prizes: **70% minimum RTP**\n• $10K-$100K prizes: **60% minimum RTP**\n• $100K+ prizes: **50% minimum RTP**"),
            ("How do I earn as a creator?", "You earn from ticket sales minus the prize, platform fee (5%), and any referral commissions. Use `/breakeven` to calculate your profits!"),
            ("When can I claim my revenue?", "After your lottery completes (winner drawn or expired), claim your revenue from the Creator Dashboard."),
        ]
    },
    "referral": {
        "title": "🤝 Referrals",
        "color": discord.Color.orange(),
        "questions": [
            ("How do referrals work?", "Generate a referral link for any lottery → Share it → When someone buys through your link, you earn a commission set by the creator."),
            ("How do I get my referral link?", "On any lottery page, click 'Share' or 'Referral Link'. The link is signed with your wallet to track your referrals."),
            ("How much can I earn?", "Commission rates are set by lottery creators (typically 0-20% of ticket price). Check each lottery for its referral rate."),
            ("When do I get paid?", "Referral earnings accrue as your referees buy tickets. Claim your commissions from the Referral Dashboard after lotteries settle."),
        ]
    },
    "trust": {
        "title": "🔐 Trust & Fairness",
        "color": discord.Color.gold(),
        "questions": [
            ("Is Chance provably fair?", "**Yes!** Every draw uses Pyth Entropy (VRF) for verifiable randomness. You can check the proof on-chain yourself."),
            ("Can creators rig their lotteries?", "**No.** Winners are determined by on-chain VRF, not by creators. Smart contracts hold all funds - no human can manipulate results."),
            ("Where are the funds held?", "All funds (prizes, ticket sales) are held in smart contracts on Base, not by any person or company."),
            ("How can I verify a draw?", "Every lottery shows a 'View on Chain' link. Click it to see the transaction proof on Basescan."),
        ]
    },
    "fees": {
        "title": "💰 Fees & Payouts",
        "color": discord.Color.red(),
        "questions": [
            ("What fees does Chance charge?", "**5% platform fee** on ticket sales. No gas fees for users (gasless transactions)."),
            ("How fast are payouts?", "**Instant!** Winners receive prizes immediately after the draw. Creator revenue can be claimed once the lottery completes."),
            ("Is there a minimum withdrawal?", "No minimum! Claim any amount from your dashboard."),
            ("What if a lottery doesn't fill?", "If a lottery expires without a winner, the creator can reclaim their prize and any ticket revenue is still distributed."),
        ]
    }
}


class FAQView(discord.ui.View):
    """Interactive FAQ view with category buttons"""
    
    def __init__(self):
        super().__init__(timeout=300)  # 5 minute timeout
    
    def get_main_embed(self):
        """Create the main FAQ menu embed"""
        embed = discord.Embed(
            title="❓ Chance FAQ",
            description="**Click a button below to browse FAQ categories!**",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="📚 Categories",
            value=(
                "🚀 **Getting Started** — Wallet, gas fees, basics\n"
                "🎰 **Playing** — Buying tickets, winning, odds\n"
                "👑 **Creating** — Launch your own lottery\n"
                "🤝 **Referrals** — Earn commissions\n"
                "🔐 **Trust** — Provably fair, verification\n"
                "💰 **Fees** — Platform fees, payouts"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🔗 More Help",
            value="**Website:** chance.fun\n**Support:** Open a ticket in #support",
            inline=False
        )
        
        embed.set_footer(text="Buttons expire after 5 minutes • Use /faq to restart")
        return embed
    
    def get_category_embed(self, category: str):
        """Create embed for a specific category"""
        data = FAQ_DATA.get(category)
        if not data:
            return None
        
        embed = discord.Embed(
            title=f"❓ FAQ — {data['title']}",
            color=data["color"]
        )
        
        for q, a in data["questions"]:
            embed.add_field(
                name=f"Q: {q}",
                value=a,
                inline=False
            )
        
        embed.set_footer(text="Click another category or 🏠 to go back")
        return embed
    
    @discord.ui.button(label="Getting Started", emoji="🚀", style=discord.ButtonStyle.green, row=0)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.get_category_embed("start")
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="Playing", emoji="🎰", style=discord.ButtonStyle.blurple, row=0)
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.get_category_embed("play")
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="Creating", emoji="👑", style=discord.ButtonStyle.blurple, row=0)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.get_category_embed("create")
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="Referrals", emoji="🤝", style=discord.ButtonStyle.gray, row=1)
    async def referral_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.get_category_embed("referral")
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="Trust", emoji="🔐", style=discord.ButtonStyle.gray, row=1)
    async def trust_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.get_category_embed("trust")
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="Fees", emoji="💰", style=discord.ButtonStyle.gray, row=1)
    async def fees_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.get_category_embed("fees")
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.red, row=1)
    async def home_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.get_main_embed()
        await interaction.response.edit_message(embed=embed, view=self)


@bot.tree.command(name="faq", description="Frequently asked questions about Chance")
@app_commands.describe(
    category="Jump directly to a category (optional)"
)
@app_commands.choices(category=[
    app_commands.Choice(name="🚀 Getting Started", value="start"),
    app_commands.Choice(name="🎰 Playing Lotteries", value="play"),
    app_commands.Choice(name="👑 Creating Lotteries", value="create"),
    app_commands.Choice(name="🤝 Referrals", value="referral"),
    app_commands.Choice(name="🔐 Trust & Fairness", value="trust"),
    app_commands.Choice(name="💰 Fees & Payouts", value="fees"),
])
async def faq_command(
    interaction: discord.Interaction,
    category: str = None
):
    """Interactive FAQ command with buttons"""
    
    view = FAQView()
    
    if category and category in FAQ_DATA:
        # Jump directly to category
        embed = view.get_category_embed(category)
    else:
        # Show main menu
        embed = view.get_main_embed()
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# =============================================================================
# /LUCKY COMMAND - Random Lucky Numbers
# =============================================================================

@bot.tree.command(name="lucky", description="Generate your lucky numbers for today! 🍀")
@app_commands.describe(
    count="How many numbers to generate (1-10, default 5)",
    max_range="Maximum number in range (10-1000, default 100)"
)
async def lucky_command(
    interaction: discord.Interaction,
    count: int = 5,
    max_range: int = 100
):
    """Generate random lucky numbers"""
    
    # Validate inputs
    if count < 1 or count > 10:
        await interaction.response.send_message(
            "❌ Count must be between 1 and 10!",
            ephemeral=True
        )
        return
    
    if max_range < 10 or max_range > 1000:
        await interaction.response.send_message(
            "❌ Range must be between 10 and 1000!",
            ephemeral=True
        )
        return
    
    if count > max_range:
        await interaction.response.send_message(
            "❌ Count can't be higher than the range!",
            ephemeral=True
        )
        return
    
    # Generate unique random numbers
    lucky_numbers = random.sample(range(1, max_range + 1), count)
    lucky_numbers.sort()
    
    # Format numbers nicely
    numbers_str = "  ".join([f"**`{n:>3}`**" for n in lucky_numbers])
    
    # Fun messages
    fortunes = [
        "✨ The stars align in your favor!",
        "🔮 Fortune smiles upon these numbers!",
        "🍀 Luck is on your side today!",
        "⭐ These numbers carry good energy!",
        "🌟 The universe has spoken!",
        "🎯 Trust in these lucky picks!",
        "💫 May fortune favor the bold!",
        "🌈 Good vibes with these numbers!",
    ]
    
    embed = discord.Embed(
        title="🍀 Your Lucky Numbers 🍀",
        description=f"{random.choice(fortunes)}",
        color=discord.Color.green()
    )
    
    embed.add_field(
        name=f"🎲 Numbers (1-{max_range})",
        value=numbers_str,
        inline=False
    )
    
    # Add a lucky tip
    tips = [
        "💡 **Tip:** Use these for your next lottery pick!",
        "💡 **Tip:** Feeling lucky? Play now at chance.fun!",
        "💡 **Tip:** Remember, every ticket is a chance to win!",
        "💡 **Tip:** The best odds come to those who play!",
    ]
    
    embed.add_field(
        name="",
        value=random.choice(tips),
        inline=False
    )
    
    embed.set_footer(text=f"🎰 Generated for {interaction.user.display_name} • chance.fun")
    
    await interaction.response.send_message(embed=embed)


# =============================================================================
# GIVEAWAY SYSTEM
# =============================================================================

# Store active giveaways
active_giveaways = {}  # {message_id: giveaway_data}


class GiveawayView(discord.ui.View):
    """Interactive giveaway view with enter button"""
    
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)  # No timeout - we handle it manually
        self.giveaway_id = giveaway_id
    
    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.green, custom_id="giveaway_enter")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle giveaway entry"""
        giveaway = active_giveaways.get(self.giveaway_id)
        
        if not giveaway:
            await interaction.response.send_message(
                "❌ This giveaway has ended!",
                ephemeral=True
            )
            return
        
        user_id = interaction.user.id
        
        if user_id in giveaway['entries']:
            await interaction.response.send_message(
                "✅ You're already entered! Good luck! 🍀",
                ephemeral=True
            )
            return
        
        # Add entry
        giveaway['entries'].append(user_id)
        
        await interaction.response.send_message(
            f"🎉 **You're in!** Good luck!\n\nEntrants: **{len(giveaway['entries'])}**",
            ephemeral=True
        )
        
        # Update the giveaway message with new count
        try:
            message = await interaction.channel.fetch_message(int(self.giveaway_id))
            embed = message.embeds[0]
            
            # Update entries count in footer
            embed.set_footer(text=f"🎫 {len(giveaway['entries'])} entries • Click to enter!")
            
            await message.edit(embed=embed, view=self)
        except:
            pass


@bot.tree.command(name="giveaway", description="[ADMIN] Start a giveaway")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    prize="What's the prize? (e.g., '$100 USDC', '10 Free Tickets')",
    duration="Duration in minutes (default: 60)",
    winners="Number of winners (default: 1)"
)
async def giveaway_command(
    interaction: discord.Interaction,
    prize: str,
    duration: int = 60,
    winners: int = 1
):
    """Start a giveaway (admin only)"""
    
    if duration < 1 or duration > 10080:  # Max 1 week
        await interaction.response.send_message(
            "❌ Duration must be between 1 minute and 7 days (10080 minutes)!",
            ephemeral=True
        )
        return
    
    if winners < 1 or winners > 10:
        await interaction.response.send_message(
            "❌ Winners must be between 1 and 10!",
            ephemeral=True
        )
        return
    
    # Calculate end time
    end_time = datetime.now(timezone.utc) + timedelta(minutes=duration)
    end_timestamp = int(end_time.timestamp())
    
    # Create giveaway embed
    embed = discord.Embed(
        title="🎉 GIVEAWAY 🎉",
        description=f"**Prize:** {prize}",
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="⏰ Ends",
        value=f"<t:{end_timestamp}:R>",
        inline=True
    )
    
    embed.add_field(
        name="👑 Winners",
        value=f"**{winners}**",
        inline=True
    )
    
    embed.add_field(
        name="📋 How to Enter",
        value="Click the **🎉 Enter Giveaway** button below!",
        inline=False
    )
    
    embed.set_footer(text="🎫 0 entries • Click to enter!")
    
    # Send initial response
    await interaction.response.send_message("🎉 **Starting giveaway...**", ephemeral=True)
    
    # Create placeholder view (we'll update with real ID after sending)
    temp_view = discord.ui.View()
    temp_button = discord.ui.Button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.green)
    temp_view.add_item(temp_button)
    
    # Send giveaway message
    giveaway_msg = await interaction.channel.send(embed=embed, view=temp_view)
    
    # Store giveaway data
    giveaway_id = str(giveaway_msg.id)
    active_giveaways[giveaway_id] = {
        'prize': prize,
        'end_time': end_timestamp,
        'winners_count': winners,
        'entries': [],
        'channel_id': interaction.channel.id,
        'host_id': interaction.user.id,
        'ended': False
    }
    
    # Update message with real view
    real_view = GiveawayView(giveaway_id)
    await giveaway_msg.edit(view=real_view)
    
    # Schedule end
    bot.loop.create_task(end_giveaway_after(giveaway_id, duration * 60))
    
    print(f"🎉 Giveaway started: {prize} ({duration}min, {winners} winners)")


async def end_giveaway_after(giveaway_id: str, seconds: int):
    """End giveaway after specified seconds"""
    await asyncio.sleep(seconds)
    await end_giveaway(giveaway_id)


async def end_giveaway(giveaway_id: str):
    """End a giveaway and pick winners"""
    giveaway = active_giveaways.get(giveaway_id)
    
    if not giveaway or giveaway.get('ended'):
        return
    
    giveaway['ended'] = True
    
    channel = bot.get_channel(giveaway['channel_id'])
    if not channel:
        return
    
    try:
        message = await channel.fetch_message(int(giveaway_id))
    except:
        return
    
    entries = giveaway['entries']
    winners_count = giveaway['winners_count']
    prize = giveaway['prize']
    
    # Create results embed
    if len(entries) == 0:
        # No entries
        embed = discord.Embed(
            title="🎉 GIVEAWAY ENDED 🎉",
            description=f"**Prize:** {prize}\n\n😢 No one entered!",
            color=discord.Color.red()
        )
    else:
        # Pick winners
        actual_winners = min(winners_count, len(entries))
        winner_ids = random.sample(entries, actual_winners)
        
        winners_mentions = "\n".join([f"🏆 <@{uid}>" for uid in winner_ids])
        
        embed = discord.Embed(
            title="🎉 GIVEAWAY ENDED 🎉",
            description=f"**Prize:** {prize}",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name=f"🏆 Winner{'s' if actual_winners > 1 else ''}!",
            value=winners_mentions,
            inline=False
        )
        
        embed.add_field(
            name="📊 Stats",
            value=f"Total entries: **{len(entries)}**",
            inline=False
        )
        
        # Congratulations message
        winner_pings = " ".join([f"<@{uid}>" for uid in winner_ids])
        await channel.send(f"🎊 **Congratulations** {winner_pings}! You won **{prize}**! 🎊")
    
    embed.set_footer(text="Thanks for participating! • chance.fun")
    
    # Update original message (remove button)
    await message.edit(embed=embed, view=None)
    
    # Clean up
    del active_giveaways[giveaway_id]
    
    print(f"🎉 Giveaway ended: {prize}")


@bot.tree.command(name="endgiveaway", description="[ADMIN] End a giveaway early")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    message_id="The message ID of the giveaway to end"
)
async def endgiveaway_command(
    interaction: discord.Interaction,
    message_id: str
):
    """End a giveaway early (admin only)"""
    
    if message_id not in active_giveaways:
        await interaction.response.send_message(
            "❌ Giveaway not found! Make sure you're using the correct message ID.",
            ephemeral=True
        )
        return
    
    await interaction.response.send_message(
        "🎉 **Ending giveaway and picking winners...**",
        ephemeral=True
    )
    
    await end_giveaway(message_id)


@bot.tree.command(name="reroll", description="[ADMIN] Reroll giveaway winner(s)")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    message_id="The message ID of the ended giveaway",
    winners="Number of new winners to pick (default: 1)"
)
async def reroll_command(
    interaction: discord.Interaction,
    message_id: str,
    winners: int = 1
):
    """Reroll winners for an ended giveaway (admin only)"""
    
    try:
        message = await interaction.channel.fetch_message(int(message_id))
    except:
        await interaction.response.send_message(
            "❌ Could not find that message!",
            ephemeral=True
        )
        return
    
    # This is a simple reroll - picks from the channel
    # In a production bot, you'd store entries permanently
    await interaction.response.send_message(
        f"🎲 **Rerolling is not available for ended giveaways.**\n"
        f"Start a new giveaway with `/giveaway`!",
        ephemeral=True
    )


# =============================================================================
# MILESTONES SYSTEM
# =============================================================================

# Milestone thresholds
MILESTONE_THRESHOLDS = {
    'tickets': [1, 10, 50, 100, 250, 500, 1000],
    'wins': [1, 5, 10, 25, 50, 100],
    'spent': [100, 500, 1000, 5000, 10000, 50000],  # In USD
    'won': [100, 500, 1000, 5000, 10000, 50000, 100000],  # In USD
}

# Track user milestones (in memory - resets on restart)
user_milestones = {}  # {wallet: {'tickets': 0, 'wins': 0, 'spent': 0, 'won': 0, 'achieved': set()}}


class MilestoneTracker:
    """Tracks and announces user milestones"""
    
    def __init__(self, bot, channel_id: int = None):
        self.bot = bot
        self.channel_id = channel_id
    
    def configure(self, channel_id: int):
        """Set the milestones announcement channel"""
        self.channel_id = channel_id
    
    async def check_milestones(self, wallet: str, category: str, new_value: float):
        """Check if user hit a milestone and announce it"""
        if not self.channel_id:
            return
        
        # Initialize user if needed
        if wallet not in user_milestones:
            user_milestones[wallet] = {
                'tickets': 0,
                'wins': 0,
                'spent': 0,
                'won': 0,
                'achieved': set()
            }
        
        user_data = user_milestones[wallet]
        user_data[category] = new_value
        
        # Check thresholds
        thresholds = MILESTONE_THRESHOLDS.get(category, [])
        
        for threshold in thresholds:
            milestone_key = f"{category}_{threshold}"
            
            if new_value >= threshold and milestone_key not in user_data['achieved']:
                user_data['achieved'].add(milestone_key)
                await self.announce_milestone(wallet, category, threshold)
    
    async def announce_milestone(self, wallet: str, category: str, threshold: int):
        """Announce a milestone achievement"""
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            return
        
        # Shorten wallet
        short_wallet = f"{wallet[:6]}...{wallet[-4:]}" if len(wallet) > 10 else wallet
        
        # Milestone messages
        messages = {
            'tickets': {
                1: ("🎫 First Ticket!", f"**{short_wallet}** just bought their first ticket! Welcome to Chance! 🍀"),
                10: ("🎫 Getting Started!", f"**{short_wallet}** has bought **10 tickets**! They're warming up! 🔥"),
                50: ("🎫 Regular Player!", f"**{short_wallet}** hit **50 tickets**! A true believer! 💪"),
                100: ("🎫 Century Club!", f"**{short_wallet}** reached **100 tickets**! Centurion status! 💯"),
                250: ("🎫 High Roller!", f"**{short_wallet}** hit **250 tickets**! They're on fire! 🔥🔥"),
                500: ("🎫 Legend!", f"**{short_wallet}** reached **500 tickets**! Legendary! 👑"),
                1000: ("🎫 GOAT!", f"**{short_wallet}** hit **1,000 TICKETS**! The GOAT! 🐐"),
            },
            'wins': {
                1: ("🏆 First Win!", f"**{short_wallet}** just won their first lottery! Congrats! 🎉"),
                5: ("🏆 Lucky Streak!", f"**{short_wallet}** has **5 wins**! Lady Luck loves them! 🍀"),
                10: ("🏆 Winner Winner!", f"**{short_wallet}** hit **10 wins**! They know the secret! 🎯"),
                25: ("🏆 Pro Winner!", f"**{short_wallet}** reached **25 wins**! Professional luck! ⭐"),
                50: ("🏆 Master Winner!", f"**{short_wallet}** hit **50 wins**! A true master! 👑"),
                100: ("🏆 LEGENDARY!", f"**{short_wallet}** reached **100 WINS**! LEGENDARY! 🏆🏆🏆"),
            },
            'spent': {
                100: ("💸 First $100!", f"**{short_wallet}** spent their first **$100**! Let's go! 🚀"),
                500: ("💸 Big Spender!", f"**{short_wallet}** has spent **$500**! Committed! 💪"),
                1000: ("💸 $1K Club!", f"**{short_wallet}** reached **$1,000 spent**! High roller! 🎰"),
                5000: ("💸 Whale Alert!", f"**{short_wallet}** hit **$5,000 spent**! 🐋 in the house!"),
                10000: ("💸 VIP Status!", f"**{short_wallet}** reached **$10,000 spent**! VIP! 💎"),
                50000: ("💸 MEGA WHALE!", f"**{short_wallet}** hit **$50,000 SPENT**! MEGA WHALE! 🐋🐋🐋"),
            },
            'won': {
                100: ("💰 First $100 Won!", f"**{short_wallet}** won their first **$100**! Nice! 🎉"),
                500: ("💰 $500 Winner!", f"**{short_wallet}** has won **$500 total**! Keep it up! 📈"),
                1000: ("💰 $1K Winner!", f"**{short_wallet}** reached **$1,000 in winnings**! 🤑"),
                5000: ("💰 $5K Winner!", f"**{short_wallet}** hit **$5,000 won**! Big money! 💵"),
                10000: ("💰 $10K Winner!", f"**{short_wallet}** reached **$10,000 in winnings**! 💎"),
                50000: ("💰 $50K Winner!", f"**{short_wallet}** hit **$50,000 WON**! MASSIVE! 💰💰"),
                100000: ("💰 $100K LEGEND!", f"**{short_wallet}** reached **$100,000 IN WINNINGS**! LEGEND! 👑👑👑"),
            },
        }
        
        category_messages = messages.get(category, {})
        milestone_data = category_messages.get(threshold)
        
        if not milestone_data:
            return
        
        title, description = milestone_data
        
        # Create embed
        embed = discord.Embed(
            title=f"🎊 MILESTONE: {title}",
            description=description,
            color=discord.Color.gold()
        )
        
        embed.set_footer(text="🍀 Milestones tracked by Chance Bot • chance.fun")
        
        await channel.send(embed=embed)
        print(f"🎊 Milestone announced: {short_wallet} - {category} {threshold}")


# Initialize milestone tracker
milestone_tracker = MilestoneTracker(bot=bot)


@bot.tree.command(name="testmilestone", description="[ADMIN] Test milestone announcement")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    category="Milestone category",
    value="Value to test (triggers appropriate milestone)"
)
@app_commands.choices(category=[
    app_commands.Choice(name="🎫 Tickets Bought", value="tickets"),
    app_commands.Choice(name="🏆 Wins", value="wins"),
    app_commands.Choice(name="💸 Amount Spent ($)", value="spent"),
    app_commands.Choice(name="💰 Amount Won ($)", value="won"),
])
async def testmilestone_command(
    interaction: discord.Interaction,
    category: str,
    value: int
):
    """Test milestone announcement (admin only)"""
    
    milestones_channel = CHANNEL_IDS.get('milestones')
    
    await interaction.response.send_message(
        f"🧪 **Testing milestone...**\n"
        f"Category: {category}\n"
        f"Value: {value}\n\n"
        f"**Debug Info:**\n"
        f"CHANNEL_MILESTONES ID: `{milestones_channel}`",
        ephemeral=True
    )
    
    if not milestones_channel or milestones_channel == 0:
        print("⚠️ CHANNEL_MILESTONES not set")
        return
    
    # Configure and test
    milestone_tracker.configure(milestones_channel)
    
    # Use a test wallet
    test_wallet = "0xTEST1234567890abcdef1234567890abcdef"
    
    # Reset test user milestones
    if test_wallet in user_milestones:
        del user_milestones[test_wallet]
    
    await milestone_tracker.check_milestones(test_wallet, category, value)


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
    
    if affiliate < 0 or affiliate > 20:
        await interaction.response.send_message(
            "❌ **Error:** Affiliate percentage must be between 0-20%",
            ephemeral=True
        )
        return
    
    # Calculate RTP first to check tier requirements
    calc = RTPCalculator()
    rtp = calc.calculate_rtp(prize, ticket, odds)
    min_rtp, tier_name = calc.get_minimum_rtp(prize)
    passes_rtp = calc.passes_minimum(rtp, min_rtp)
    
    # Platform takes 5%, creator keeps 95%
    PLATFORM_FEE = 0.05
    creator_rate = 1 - PLATFORM_FEE
    
    # Affiliate cuts into creator's share
    affiliate_rate = affiliate / 100
    net_creator_rate = creator_rate - affiliate_rate
    
    # Calculate break-even point
    # Break-even = Prize / (Ticket Price × Net Creator Rate)
    breakeven_tickets = prize / (ticket * net_creator_rate)
    
    # Expected payout point (based on odds)
    expected_payout = odds
    
    # Scenario calculations
    # Worst case: Winner at 50% of expected
    worst_case_tickets = int(expected_payout * 0.5)
    worst_revenue = worst_case_tickets * ticket
    worst_platform_fee = worst_revenue * PLATFORM_FEE
    worst_affiliate_cost = worst_revenue * affiliate_rate
    worst_net = worst_revenue - prize - worst_platform_fee - worst_affiliate_cost
    
    # Expected case: Winner at expected odds
    expected_revenue = expected_payout * ticket
    expected_platform_fee = expected_revenue * PLATFORM_FEE
    expected_affiliate_cost = expected_revenue * affiliate_rate
    expected_net = expected_revenue - prize - expected_platform_fee - expected_affiliate_cost
    
    # Best case: Winner at 150% of expected
    best_case_tickets = int(expected_payout * 1.5)
    best_revenue = best_case_tickets * ticket
    best_platform_fee = best_revenue * PLATFORM_FEE
    best_affiliate_cost = best_revenue * affiliate_rate
    best_net = best_revenue - prize - best_platform_fee - best_affiliate_cost
    
    # ROI calculations
    expected_roi = (expected_net / prize) * 100 if prize > 0 else 0
    
    # Format currency
    def fmt(amount):
        return f"${amount:,.2f}"
    
    # Create embed
    embed = discord.Embed(
        title="💰 Break-Even Calculator Results",
        color=discord.Color.blue() if passes_rtp else discord.Color.red(),
        description=f"Profit analysis for your lottery parameters"
    )
    
    # Input summary
    embed.add_field(
        name="📊 Lottery Parameters",
        value=(
            f"**Prize:** {fmt(prize)} USDC\n"
            f"**Ticket Price:** {fmt(ticket)} USDC\n"
            f"**Odds:** 1 in {odds:,}\n"
            f"**Affiliate:** {affiliate}%\n"
            f"**RTP:** {rtp:.2f}% {'✅' if passes_rtp else '❌'}"
        ),
        inline=False
    )
    
    # Break-even analysis
    status_emoji = "✅" if expected_payout > breakeven_tickets else "⚠️"
    margin = expected_payout - breakeven_tickets
    
    embed.add_field(
        name="🎯 Break-Even Analysis",
        value=(
            f"**Break-even point:** {breakeven_tickets:.0f} tickets\n"
            f"**Expected payout:** {expected_payout:,} tickets\n"
            f"**Margin:** {margin:.0f} tickets {status_emoji}"
        ),
        inline=False
    )
    
    # Profit scenarios
    embed.add_field(
        name="📉 Worst Case (Winner at ticket {})".format(worst_case_tickets),
        value=(
            f"Revenue: {fmt(worst_revenue)}\n"
            f"Costs: {fmt(prize + worst_platform_fee + worst_affiliate_cost)}\n"
            f"**Net: {fmt(worst_net)}** {'📉' if worst_net < 0 else '✅'}"
        ),
        inline=True
    )
    
    embed.add_field(
        name="📊 Expected Case (Winner at ticket {})".format(expected_payout),
        value=(
            f"Revenue: {fmt(expected_revenue)}\n"
            f"Costs: {fmt(prize + expected_platform_fee + expected_affiliate_cost)}\n"
            f"**Net: {fmt(expected_net)}** {'📉' if expected_net < 0 else '✅'}"
        ),
        inline=True
    )
    
    embed.add_field(
        name="📈 Best Case (Winner at ticket {})".format(best_case_tickets),
        value=(
            f"Revenue: {fmt(best_revenue)}\n"
            f"Costs: {fmt(prize + best_platform_fee + best_affiliate_cost)}\n"
            f"**Net: {fmt(best_net)}** {'📈' if best_net > 0 else '⚠️'}"
        ),
        inline=True
    )
    
    # ROI Summary
    roi_emoji = "🎯" if expected_roi > 20 else "⚠️" if expected_roi > 0 else "📉"
    embed.add_field(
        name="💼 Expected ROI",
        value=f"**{expected_roi:.1f}%** {roi_emoji}",
        inline=False
    )
    
    # Recommendations
    if not passes_rtp:
        recommendation = f"⚠️ **Warning:** RTP is below {min_rtp}% minimum for {tier_name}. Adjust parameters before deploying."
    elif margin < 0:
        recommendation = "⚠️ **High Risk:** Expected payout is before break-even. Consider adjusting odds or ticket price."
    elif expected_roi < 10:
        recommendation = "💡 **Low Margin:** Profit margin is tight. Consider increasing odds or decreasing prize."
    elif expected_roi > 50:
        recommendation = "🔥 **Strong Setup:** Good profit potential with acceptable risk."
    else:
        recommendation = "✅ **Balanced:** Reasonable profit potential with managed risk."
    
    embed.add_field(
        name="💡 Assessment",
        value=recommendation,
        inline=False
    )
    
    embed.set_footer(text="Chance Break-Even Calculator • Use /rtp to check RTP requirements")
    
    # Send ephemeral response
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # Try to send DM
    try:
        dm_embed = embed.copy()
        dm_embed.set_footer(text="This is your private break-even calculation from Chance Discord")
        
        await interaction.user.send(embed=dm_embed)
        
        await interaction.followup.send(
            "📬 Check your DMs for a copy of your calculation!",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ Couldn't send you a DM. Make sure your DMs are open!",
            ephemeral=True
        )
    except Exception as e:
        print(f"Error sending DM: {e}")

# =============================================================================
# /OPTIMIZE COMMAND - Parameter Optimizer for Creators
# =============================================================================

class LotteryOptimizer:
    """Optimizer for lottery parameters based on creator goals"""
    
    # Platform fee (5%)
    PLATFORM_FEE = 0.05
    
    # Tier minimums
    TIER_MINIMUMS = {
        'small': {'max': 10000, 'min_rtp': 70},
        'medium': {'max': 100000, 'min_rtp': 60},
        'large': {'max': float('inf'), 'min_rtp': 50}
    }
    
    @classmethod
    def get_tier_info(cls, prize: float) -> tuple:
        """Get tier minimum RTP and name"""
        if prize < 10000:
            return 70, "$100-$10K tier"
        elif prize < 100000:
            return 60, "$10K-$100K tier"
        else:
            return 50, "$100K+ tier"
    
    @classmethod
    def calculate_rtp(cls, prize: float, ticket_price: float, odds: int) -> float:
        """Calculate RTP percentage"""
        probability = 1 / odds
        return (prize * probability / ticket_price) * 100
    
    @classmethod
    def calculate_roi(cls, prize: float, ticket_price: float, odds: int, affiliate: float = 0) -> float:
        """Calculate expected ROI for creator"""
        expected_tickets = odds  # On average, this many tickets to get a winner
        gross_revenue = expected_tickets * ticket_price
        platform_fee = gross_revenue * cls.PLATFORM_FEE
        affiliate_cost = gross_revenue * (affiliate / 100)
        net_revenue = gross_revenue - platform_fee - affiliate_cost - prize
        roi = (net_revenue / prize) * 100
        return roi
    
    @classmethod
    def calculate_breakeven(cls, prize: float, ticket_price: float, affiliate: float = 0) -> int:
        """Calculate break-even ticket count"""
        platform_fee_per_ticket = ticket_price * cls.PLATFORM_FEE
        affiliate_per_ticket = ticket_price * (affiliate / 100)
        net_per_ticket = ticket_price - platform_fee_per_ticket - affiliate_per_ticket
        if net_per_ticket <= 0:
            return float('inf')
        return int(prize / net_per_ticket) + 1
    
    @classmethod
    def get_min_odds_for_profit(cls, prize: float, ticket_price: float, affiliate: float, target_roi: float = 10) -> int:
        """
        Calculate minimum odds needed to achieve target ROI
        
        Formula derivation:
        ROI = (Odds × Ticket × (1 - 0.05 - Affiliate%) - Prize) / Prize × 100
        Target ROI = (Odds × Ticket × NetRate - Prize) / Prize × 100
        
        Solving for Odds:
        Odds = Prize × (1 + Target ROI/100) / (Ticket × NetRate)
        """
        net_rate = 1 - cls.PLATFORM_FEE - (affiliate / 100)
        if net_rate <= 0:
            return float('inf')
        
        # Odds needed for target ROI
        min_odds = int((prize * (1 + target_roi / 100)) / (ticket_price * net_rate)) + 1
        return max(10, min_odds)
    
    @classmethod
    def get_max_odds_for_rtp(cls, prize: float, ticket_price: float, min_rtp: float) -> int:
        """
        Calculate maximum odds allowed to meet minimum RTP
        
        Formula: RTP = (Prize / Odds) / Ticket × 100
        Solving for Odds: Odds = Prize × 100 / (RTP × Ticket)
        """
        max_odds = int((prize * 100) / (min_rtp * ticket_price))
        return max_odds
    
    @classmethod
    def find_optimal_ticket_price(cls, prize: float, affiliate: float, strategy: str) -> float:
        """
        Find a ticket price that allows profitable setup within RTP constraints
        """
        min_rtp, _ = cls.get_tier_info(prize)
        net_rate = 1 - cls.PLATFORM_FEE - (affiliate / 100)
        
        if net_rate <= 0:
            return None  # Impossible to profit with these fees
        
        # Strategy-based ticket price targets
        if strategy == 'profit':
            # Higher ticket prices for more margin
            if prize < 1000:
                base_ticket = max(5, prize * 0.02)
            elif prize < 10000:
                base_ticket = max(10, prize * 0.01)
            elif prize < 50000:
                base_ticket = max(25, prize * 0.005)
            else:
                base_ticket = max(50, prize * 0.003)
        elif strategy == 'volume':
            # Lower ticket prices for accessibility
            if prize < 1000:
                base_ticket = max(1, prize * 0.005)
            elif prize < 10000:
                base_ticket = max(2, prize * 0.002)
            elif prize < 50000:
                base_ticket = max(5, prize * 0.001)
            else:
                base_ticket = max(10, prize * 0.0008)
        else:  # balanced
            if prize < 1000:
                base_ticket = max(2, prize * 0.01)
            elif prize < 10000:
                base_ticket = max(5, prize * 0.005)
            elif prize < 50000:
                base_ticket = max(15, prize * 0.003)
            else:
                base_ticket = max(25, prize * 0.002)
        
        # Round to nice number
        if base_ticket < 5:
            ticket_price = round(base_ticket)
            ticket_price = max(1, ticket_price)
        else:
            ticket_price = round(base_ticket / 5) * 5
            ticket_price = max(5, ticket_price)
        
        # Verify this ticket price allows a profitable setup
        # Min odds for 10% profit
        min_odds_profit = cls.get_min_odds_for_profit(prize, ticket_price, affiliate, target_roi=10)
        # Max odds for RTP compliance
        max_odds_rtp = cls.get_max_odds_for_rtp(prize, ticket_price, min_rtp)
        
        # If no valid range, increase ticket price until we find one
        attempts = 0
        while min_odds_profit > max_odds_rtp and attempts < 20:
            ticket_price = ticket_price * 1.2  # Increase by 20%
            if ticket_price < 10:
                ticket_price = round(ticket_price)
            else:
                ticket_price = round(ticket_price / 5) * 5
            
            min_odds_profit = cls.get_min_odds_for_profit(prize, ticket_price, affiliate, target_roi=10)
            max_odds_rtp = cls.get_max_odds_for_rtp(prize, ticket_price, min_rtp)
            attempts += 1
        
        return ticket_price
    
    @classmethod
    def optimize_for_profit(cls, prize: float, affiliate: float = 0) -> dict:
        """
        Optimize for maximum creator profit
        - Higher ticket prices
        - Odds tuned for strong margin (25%+ ROI target)
        """
        min_rtp, tier = cls.get_tier_info(prize)
        
        # Higher ticket prices for profit strategy
        if prize < 1000:
            ticket_price = max(10, prize * 0.02)
        elif prize < 10000:
            ticket_price = max(25, prize * 0.01)
        elif prize < 50000:
            ticket_price = max(40, prize * 0.006)
        else:
            ticket_price = max(75, prize * 0.004)
        
        # Round to nice number
        ticket_price = round(ticket_price / 5) * 5
        ticket_price = max(5, ticket_price)
        
        # Calculate odds range - target 25% ROI for profit mode
        target_roi = 25
        min_odds_profit = cls.get_min_odds_for_profit(prize, ticket_price, affiliate, target_roi)
        max_odds_rtp = cls.get_max_odds_for_rtp(prize, ticket_price, min_rtp)
        
        # If no valid range, increase ticket price until profitable
        while min_odds_profit > max_odds_rtp and ticket_price < prize * 0.15:
            ticket_price += 5
            min_odds_profit = cls.get_min_odds_for_profit(prize, ticket_price, affiliate, target_roi)
            max_odds_rtp = cls.get_max_odds_for_rtp(prize, ticket_price, min_rtp)
        
        # Use minimum odds needed for target profit
        odds = min_odds_profit
        odds = max(10, odds)
        
        actual_rtp = cls.calculate_rtp(prize, ticket_price, odds)
        roi = cls.calculate_roi(prize, ticket_price, odds, affiliate)
        breakeven = cls.calculate_breakeven(prize, ticket_price, affiliate)
        
        return {
            'ticket_price': ticket_price,
            'odds': odds,
            'rtp': actual_rtp,
            'roi': roi,
            'breakeven': breakeven,
            'min_rtp': min_rtp,
            'tier': tier,
            'strategy': 'profit'
        }
    
    @classmethod
    def optimize_for_volume(cls, prize: float, affiliate: float = 0) -> dict:
        """
        Optimize for maximum ticket sales/player engagement
        - Lower ticket prices (but still profitable!)
        - Better odds for players while maintaining margin
        """
        min_rtp, tier = cls.get_tier_info(prize)
        
        # Start with lower ticket price for volume
        if prize < 1000:
            ticket_price = max(5, prize * 0.01)
        elif prize < 10000:
            ticket_price = max(10, prize * 0.005)
        elif prize < 50000:
            ticket_price = max(15, prize * 0.003)
        else:
            ticket_price = max(25, prize * 0.002)
        
        # Round to nice number
        ticket_price = round(ticket_price / 5) * 5
        ticket_price = max(5, ticket_price)
        
        # Calculate odds range - use lower ROI target for volume (but still profitable!)
        target_roi = 10  # 10% minimum profit for volume
        min_odds_profit = cls.get_min_odds_for_profit(prize, ticket_price, affiliate, target_roi)
        max_odds_rtp = cls.get_max_odds_for_rtp(prize, ticket_price, min_rtp)
        
        # If no valid range, increase ticket price until profitable
        while min_odds_profit > max_odds_rtp and ticket_price < prize * 0.1:
            ticket_price += 5
            min_odds_profit = cls.get_min_odds_for_profit(prize, ticket_price, affiliate, target_roi)
            max_odds_rtp = cls.get_max_odds_for_rtp(prize, ticket_price, min_rtp)
        
        # Use minimum odds that achieves target profit (better RTP for players)
        odds = min_odds_profit
        odds = max(10, odds)
        
        actual_rtp = cls.calculate_rtp(prize, ticket_price, odds)
        roi = cls.calculate_roi(prize, ticket_price, odds, affiliate)
        breakeven = cls.calculate_breakeven(prize, ticket_price, affiliate)
        
        return {
            'ticket_price': ticket_price,
            'odds': odds,
            'rtp': actual_rtp,
            'roi': roi,
            'breakeven': breakeven,
            'min_rtp': min_rtp,
            'tier': tier,
            'strategy': 'volume'
        }
    
    @classmethod
    def optimize_balanced(cls, prize: float, affiliate: float = 0) -> dict:
        """
        Balanced optimization - ensures profit while being fair to players
        Target: 15% ROI with reasonable RTP
        """
        min_rtp, tier = cls.get_tier_info(prize)
        
        # Start with moderate ticket price
        if prize < 1000:
            ticket_price = max(5, prize * 0.012)
        elif prize < 10000:
            ticket_price = max(15, prize * 0.006)
        elif prize < 50000:
            ticket_price = max(25, prize * 0.004)
        else:
            ticket_price = max(40, prize * 0.003)
        
        # Round to nice number
        ticket_price = round(ticket_price / 5) * 5
        ticket_price = max(5, ticket_price)
        
        # Calculate odds for target profit
        target_roi = 15  # 15% profit target for balanced
        min_odds_profit = cls.get_min_odds_for_profit(prize, ticket_price, affiliate, target_roi)
        max_odds_rtp = cls.get_max_odds_for_rtp(prize, ticket_price, min_rtp)
        
        # If no valid range, increase ticket price until profitable
        while min_odds_profit > max_odds_rtp and ticket_price < prize * 0.1:
            ticket_price += 5
            min_odds_profit = cls.get_min_odds_for_profit(prize, ticket_price, affiliate, target_roi)
            max_odds_rtp = cls.get_max_odds_for_rtp(prize, ticket_price, min_rtp)
        
        # Use minimum odds for target profit
        odds = min_odds_profit
        odds = max(10, odds)
        
        actual_rtp = cls.calculate_rtp(prize, ticket_price, odds)
        roi = cls.calculate_roi(prize, ticket_price, odds, affiliate)
        breakeven = cls.calculate_breakeven(prize, ticket_price, affiliate)
        
        return {
            'ticket_price': ticket_price,
            'odds': odds,
            'rtp': actual_rtp,
            'roi': roi,
            'breakeven': breakeven,
            'min_rtp': min_rtp,
            'tier': tier,
            'strategy': 'balanced'
        }


@bot.tree.command(name="optimize", description="Get optimized lottery parameters based on your goals")
@app_commands.describe(
    prize="Prize amount in USDC (e.g., 5000)",
    target="Optimization target: profit, volume, or balanced",
    affiliate="Affiliate percentage you plan to offer (0-20, optional, default 0)"
)
@app_commands.choices(target=[
    app_commands.Choice(name="💰 Profit - Maximize your earnings", value="profit"),
    app_commands.Choice(name="📈 Volume - Maximize ticket sales", value="volume"),
    app_commands.Choice(name="⚖️ Balanced - Best of both worlds", value="balanced"),
])
async def optimize_command(
    interaction: discord.Interaction,
    prize: float,
    target: str,
    affiliate: float = 0.0
):
    """
    Optimize lottery parameters based on creator goals
    """
    
    # Input validation
    if prize < 100:
        await interaction.response.send_message(
            "❌ **Error:** Minimum prize is $100 USDC",
            ephemeral=True
        )
        return
    
    if affiliate < 0 or affiliate > 20:
        await interaction.response.send_message(
            "❌ **Error:** Affiliate percentage must be between 0 and 20",
            ephemeral=True
        )
        return
    
    # Get optimizations for all strategies
    optimizer = LotteryOptimizer()
    
    if target == "profit":
        result = optimizer.optimize_for_profit(prize, affiliate)
        title = "💰 Profit-Optimized Setup"
        description = "Maximizes your earnings while meeting RTP requirements"
        color = discord.Color.gold()
    elif target == "volume":
        result = optimizer.optimize_for_volume(prize, affiliate)
        title = "📈 Volume-Optimized Setup"
        description = "Attracts more players with competitive odds"
        color = discord.Color.blue()
    else:  # balanced
        result = optimizer.optimize_balanced(prize, affiliate)
        title = "⚖️ Balanced Setup"
        description = "Good balance of profit and player appeal"
        color = discord.Color.green()
    
    # Check if RTP passes minimum
    passes_rtp = result['rtp'] >= result['min_rtp']
    
    # Format values
    def fmt(val):
        return f"${val:,.2f}"
    
    # Create embed
    embed = discord.Embed(
        title=title,
        description=description,
        color=color
    )
    
    # Prize info
    embed.add_field(
        name="🎁 Prize",
        value=f"**{fmt(prize)}** USDC\n{result['tier']}",
        inline=True
    )
    
    # Affiliate info
    if affiliate > 0:
        embed.add_field(
            name="🤝 Affiliate",
            value=f"**{affiliate}%**",
            inline=True
        )
    
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # Spacer
    
    # Recommended Parameters
    embed.add_field(
        name="🎯 Recommended Parameters",
        value=(
            f"**Ticket Price:** {fmt(result['ticket_price'])} USDC\n"
            f"**Odds:** 1 in {result['odds']:,}\n"
            f"**RTP:** {result['rtp']:.1f}% {'✅' if passes_rtp else '❌'}\n"
            f"*(Min: {result['min_rtp']}% for this tier)*"
        ),
        inline=False
    )
    
    # Expected Performance
    roi_emoji = "🔥" if result['roi'] > 30 else "✅" if result['roi'] > 10 else "⚠️" if result['roi'] > 0 else "📉"
    embed.add_field(
        name="📊 Expected Performance",
        value=(
            f"**Break-Even:** {result['breakeven']:,} tickets\n"
            f"**Expected ROI:** {result['roi']:.1f}% {roi_emoji}\n"
            f"**Expected Payout:** ~{result['odds']:,} tickets"
        ),
        inline=False
    )
    
    # Revenue Breakdown
    expected_tickets = result['odds']
    gross_revenue = expected_tickets * result['ticket_price']
    platform_fee = gross_revenue * 0.05
    affiliate_cost = gross_revenue * (affiliate / 100)
    net_profit = gross_revenue - platform_fee - affiliate_cost - prize
    
    embed.add_field(
        name="💵 Revenue Breakdown (Expected)",
        value=(
            f"**Gross Revenue:** {fmt(gross_revenue)}\n"
            f"**Platform Fee (5%):** -{fmt(platform_fee)}\n"
            f"{'**Affiliate Cost:** -' + fmt(affiliate_cost) + chr(10) if affiliate > 0 else ''}"
            f"**Prize Payout:** -{fmt(prize)}\n"
            f"**Net Profit:** {fmt(net_profit)} {'📈' if net_profit > 0 else '📉'}"
        ),
        inline=False
    )
    
    # Strategy Tips
    if target == "profit":
        tips = (
            "💡 **Tips for Profit Strategy:**\n"
            "• Higher ticket prices = fewer buyers needed\n"
            "• Tighter odds = more margin per lottery\n"
            "• Best for established creators with loyal following"
        )
    elif target == "volume":
        tips = (
            "💡 **Tips for Volume Strategy:**\n"
            "• Lower prices attract more players\n"
            "• Better odds = happier players = more shares\n"
            "• Great for building audience and reputation"
        )
    else:
        tips = (
            "💡 **Tips for Balanced Strategy:**\n"
            "• Good starting point for new creators\n"
            "• Reasonable profit with competitive odds\n"
            "• Adjust based on results over time"
        )
    
    embed.add_field(
        name="\u200b",
        value=tips,
        inline=False
    )
    
    # Warning if RTP doesn't pass
    if not passes_rtp:
        embed.add_field(
            name="⚠️ Warning",
            value=f"This setup has {result['rtp']:.1f}% RTP but requires {result['min_rtp']}% minimum. Adjust parameters!",
            inline=False
        )
    
    embed.set_footer(text="Chance Parameter Optimizer • Use /preview to see your lottery post")
    
    # Send response
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # Send DM copy
    try:
        dm_embed = embed.copy()
        dm_embed.set_footer(text="This is your private optimization from Chance Discord")
        await interaction.user.send(embed=dm_embed)
        await interaction.followup.send(
            "📬 Check your DMs for a copy of your optimization!",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ Couldn't send DM. Enable DMs from server members to receive copies!",
            ephemeral=True
        )
    except Exception as e:
        print(f"Error sending DM: {e}")


# =============================================================================
# /PREVIEW COMMAND - Preview Lottery Post
# =============================================================================

@bot.tree.command(name="preview", description="Preview what your lottery will look like when posted")
@app_commands.describe(
    prize="Prize amount in USDC (e.g., 5000)",
    ticket="Ticket price in USDC (e.g., 25)",
    odds="Odds as pick range - 1 in X (e.g., 250)",
    duration="Duration in hours (optional, e.g., 24)",
    max_tickets="Maximum tickets (optional, 0 = unlimited)",
    affiliate="Affiliate percentage (0-20, optional)"
)
async def preview_command(
    interaction: discord.Interaction,
    prize: float,
    ticket: float,
    odds: int,
    duration: int = 0,
    max_tickets: int = 0,
    affiliate: float = 0.0
):
    """
    Preview what the lottery post will look like
    """
    
    # Input validation
    if prize < 100:
        await interaction.response.send_message(
            "❌ **Error:** Minimum prize is $100 USDC",
            ephemeral=True
        )
        return
    
    if ticket <= 0 or odds <= 0:
        await interaction.response.send_message(
            "❌ **Error:** Ticket price and odds must be positive!",
            ephemeral=True
        )
        return
    
    if affiliate < 0 or affiliate > 20:
        await interaction.response.send_message(
            "❌ **Error:** Affiliate percentage must be between 0 and 20",
            ephemeral=True
        )
        return
    
    # Calculate RTP
    calc = RTPCalculator()
    rtp = calc.calculate_rtp(prize, ticket, odds)
    min_rtp, tier_name = calc.get_minimum_rtp(prize)
    passes_rtp = rtp >= min_rtp
    
    # Format values
    def fmt(val):
        return f"${val:,.2f}"
    
    # Create preview embed (matching lottery monitor format)
    embed = discord.Embed(
        title="🎰 LOTTERY PREVIEW",
        description="*This is how your lottery will appear to players*",
        color=discord.Color.green() if passes_rtp else discord.Color.red()
    )
    
    # Main stats row
    embed.add_field(
        name="🏆 Prize",
        value=f"**{fmt(prize)}** USDC",
        inline=True
    )
    embed.add_field(
        name="🎫 Ticket Price",
        value=f"**{fmt(ticket)}** USDC",
        inline=True
    )
    embed.add_field(
        name="🎲 Odds",
        value=f"**1 in {odds:,}**",
        inline=True
    )
    
    # Second row
    rtp_display = f"**{rtp:.2f}%** {'✅' if passes_rtp else '❌'}"
    embed.add_field(
        name="📊 RTP",
        value=rtp_display,
        inline=True
    )
    
    if duration > 0:
        embed.add_field(
            name="⏱️ Duration",
            value=f"**{duration}** hours",
            inline=True
        )
    else:
        embed.add_field(
            name="⏱️ Duration",
            value="**Not set**",
            inline=True
        )
    
    embed.add_field(
        name="🎫 Max Tickets",
        value=f"**{max_tickets:,}**" if max_tickets > 0 else "**Unlimited**",
        inline=True
    )
    
    # Affiliate
    if affiliate > 0:
        embed.add_field(
            name="🤝 Affiliate Rewards",
            value=f"**{affiliate}%**",
            inline=True
        )
    
    # Market Position
    if not passes_rtp:
        market_msg = f"❌ Below {min_rtp}% minimum for {tier_name}."
    elif rtp >= 90:
        market_msg = "🔥 Excellent! Very competitive RTP."
    elif rtp >= 80:
        market_msg = "✅ Great! Above average RTP."
    elif rtp >= min_rtp + 5:
        market_msg = "👍 Good. Meets requirements with buffer."
    else:
        market_msg = f"⚠️ Tight. Just above {min_rtp}% minimum."
    
    embed.add_field(
        name="💡 Market Position",
        value=market_msg,
        inline=False
    )
    
    # Play button simulation
    embed.add_field(
        name="🎮 Play Now",
        value="[Click to Play](https://chance.fun)",
        inline=False
    )
    
    # Analysis section
    optimizer = LotteryOptimizer()
    breakeven = optimizer.calculate_breakeven(prize, ticket, affiliate)
    roi = optimizer.calculate_roi(prize, ticket, odds, affiliate)
    
    analysis = (
        f"**Break-Even:** {breakeven:,} tickets\n"
        f"**Expected ROI:** {roi:.1f}%\n"
        f"**RTP Status:** {'✅ Passes' if passes_rtp else '❌ FAILS'} {tier_name}"
    )
    
    embed.add_field(
        name="📈 Creator Analysis",
        value=analysis,
        inline=False
    )
    
    # Warnings
    warnings = []
    if not passes_rtp:
        warnings.append(f"⚠️ RTP {rtp:.1f}% is below {min_rtp}% minimum!")
    if roi < 0:
        warnings.append("⚠️ Negative expected ROI - you may lose money!")
    if roi < 10 and roi >= 0:
        warnings.append("💡 Low margin - consider adjusting parameters")
    if ticket > prize * 0.1:
        warnings.append("💡 High ticket price relative to prize - may limit sales")
    
    if warnings:
        embed.add_field(
            name="⚠️ Warnings",
            value="\n".join(warnings),
            inline=False
        )
    
    embed.set_footer(text="Chance Lottery Preview • Use /optimize for suggestions")
    
    # Send preview
    await interaction.response.send_message(
        content="**📋 Here's how your lottery will look:**",
        embed=embed,
        ephemeral=True
    )


# =============================================================================
# /COMPARE COMMAND - Compare Two Lottery Setups
# =============================================================================

@bot.tree.command(name="compare", description="Compare two lottery setups side-by-side")
@app_commands.describe(
    prize1="Setup A: Prize amount in USDC",
    ticket1="Setup A: Ticket price in USDC",
    odds1="Setup A: Odds (1 in X)",
    prize2="Setup B: Prize amount in USDC",
    ticket2="Setup B: Ticket price in USDC",
    odds2="Setup B: Odds (1 in X)",
    affiliate="Affiliate percentage for both (0-20, optional)"
)
async def compare_command(
    interaction: discord.Interaction,
    prize1: float,
    ticket1: float,
    odds1: int,
    prize2: float,
    ticket2: float,
    odds2: int,
    affiliate: float = 0.0
):
    """
    Compare two lottery setups side-by-side
    """
    
    # Input validation
    if prize1 < 100 or prize2 < 100:
        await interaction.response.send_message(
            "❌ **Error:** Minimum prize is $100 USDC for both setups",
            ephemeral=True
        )
        return
    
    if ticket1 <= 0 or ticket2 <= 0 or odds1 <= 0 or odds2 <= 0:
        await interaction.response.send_message(
            "❌ **Error:** All values must be positive!",
            ephemeral=True
        )
        return
    
    if affiliate < 0 or affiliate > 20:
        await interaction.response.send_message(
            "❌ **Error:** Affiliate percentage must be between 0 and 20",
            ephemeral=True
        )
        return
    
    # Calculate metrics for both setups
    calc = RTPCalculator()
    optimizer = LotteryOptimizer()
    
    # Setup A
    rtp1 = calc.calculate_rtp(prize1, ticket1, odds1)
    min_rtp1, tier1 = calc.get_minimum_rtp(prize1)
    passes1 = rtp1 >= min_rtp1
    roi1 = optimizer.calculate_roi(prize1, ticket1, odds1, affiliate)
    breakeven1 = optimizer.calculate_breakeven(prize1, ticket1, affiliate)
    
    # Expected profit calculation
    expected_tickets1 = odds1
    gross1 = expected_tickets1 * ticket1
    platform_fee1 = gross1 * 0.05
    affiliate_cost1 = gross1 * (affiliate / 100)
    net_profit1 = gross1 - platform_fee1 - affiliate_cost1 - prize1
    
    # Setup B
    rtp2 = calc.calculate_rtp(prize2, ticket2, odds2)
    min_rtp2, tier2 = calc.get_minimum_rtp(prize2)
    passes2 = rtp2 >= min_rtp2
    roi2 = optimizer.calculate_roi(prize2, ticket2, odds2, affiliate)
    breakeven2 = optimizer.calculate_breakeven(prize2, ticket2, affiliate)
    
    # Expected profit calculation
    expected_tickets2 = odds2
    gross2 = expected_tickets2 * ticket2
    platform_fee2 = gross2 * 0.05
    affiliate_cost2 = gross2 * (affiliate / 100)
    net_profit2 = gross2 - platform_fee2 - affiliate_cost2 - prize2
    
    # Format currency
    def fmt(val):
        return f"${val:,.2f}"
    
    # Winner indicators
    def winner(a, b, higher_is_better=True):
        if higher_is_better:
            return ("🏆", "") if a > b else ("", "🏆") if b > a else ("", "")
        else:
            return ("🏆", "") if a < b else ("", "🏆") if b < a else ("", "")
    
    # Create comparison embed
    embed = discord.Embed(
        title="⚖️ Lottery Comparison",
        description="Side-by-side analysis of two setups",
        color=discord.Color.blue()
    )
    
    # Setup A Summary
    status1 = "✅" if passes1 else "❌"
    roi_emoji1 = "📈" if roi1 > 0 else "📉"
    w_rtp1, _ = winner(rtp1, rtp2, True)
    w_roi1, _ = winner(roi1, roi2, True)
    w_profit1, _ = winner(net_profit1, net_profit2, True)
    w_be1, _ = winner(breakeven1, breakeven2, False)
    
    embed.add_field(
        name="🅰️ Setup A",
        value=(
            f"**Prize:** {fmt(prize1)}\n"
            f"**Ticket:** {fmt(ticket1)}\n"
            f"**Odds:** 1 in {odds1:,}\n"
            f"**RTP:** {rtp1:.1f}% {status1} {w_rtp1}\n"
            f"**ROI:** {roi1:.1f}% {roi_emoji1} {w_roi1}\n"
            f"**Break-Even:** {breakeven1:,} {w_be1}\n"
            f"**Net Profit:** {fmt(net_profit1)} {w_profit1}"
        ),
        inline=True
    )
    
    # Setup B Summary
    status2 = "✅" if passes2 else "❌"
    roi_emoji2 = "📈" if roi2 > 0 else "📉"
    _, w_rtp2 = winner(rtp1, rtp2, True)
    _, w_roi2 = winner(roi1, roi2, True)
    _, w_profit2 = winner(net_profit1, net_profit2, True)
    _, w_be2 = winner(breakeven1, breakeven2, False)
    
    embed.add_field(
        name="🅱️ Setup B",
        value=(
            f"**Prize:** {fmt(prize2)}\n"
            f"**Ticket:** {fmt(ticket2)}\n"
            f"**Odds:** 1 in {odds2:,}\n"
            f"**RTP:** {rtp2:.1f}% {status2} {w_rtp2}\n"
            f"**ROI:** {roi2:.1f}% {roi_emoji2} {w_roi2}\n"
            f"**Break-Even:** {breakeven2:,} {w_be2}\n"
            f"**Net Profit:** {fmt(net_profit2)} {w_profit2}"
        ),
        inline=True
    )
    
    # Overall Recommendation
    score1 = 0
    score2 = 0
    
    # Score based on key metrics (weighted)
    if passes1 and not passes2:
        score1 += 3
    elif passes2 and not passes1:
        score2 += 3
    
    if roi1 > roi2:
        score1 += 2
    elif roi2 > roi1:
        score2 += 2
    
    if net_profit1 > net_profit2:
        score1 += 2
    elif net_profit2 > net_profit1:
        score2 += 2
    
    if breakeven1 < breakeven2:
        score1 += 1
    elif breakeven2 < breakeven1:
        score2 += 1
    
    # Determine recommendation
    if not passes1 and not passes2:
        recommendation = "⚠️ **Neither setup meets RTP requirements!** Use `/optimize` to find valid parameters."
    elif not passes1:
        recommendation = "🅱️ **Setup B wins!** Setup A fails RTP requirements."
    elif not passes2:
        recommendation = "🅰️ **Setup A wins!** Setup B fails RTP requirements."
    elif score1 > score2:
        recommendation = f"🅰️ **Setup A wins!** Better overall metrics (ROI: {roi1:.1f}% vs {roi2:.1f}%)"
    elif score2 > score1:
        recommendation = f"🅱️ **Setup B wins!** Better overall metrics (ROI: {roi2:.1f}% vs {roi1:.1f}%)"
    else:
        recommendation = "🤝 **It's a tie!** Both setups are comparable."
    
    embed.add_field(
        name="🏆 Recommendation",
        value=recommendation,
        inline=False
    )
    
    # Warnings (if any)
    warnings = []
    if not passes1:
        warnings.append(f"• Setup A: RTP {rtp1:.1f}% below {min_rtp1}% minimum")
    if not passes2:
        warnings.append(f"• Setup B: RTP {rtp2:.1f}% below {min_rtp2}% minimum")
    if roi1 < 0:
        warnings.append("• Setup A: Negative ROI - will lose money")
    if roi2 < 0:
        warnings.append("• Setup B: Negative ROI - will lose money")
    
    if warnings:
        embed.add_field(
            name="⚠️ Warnings",
            value="\n".join(warnings),
            inline=False
        )
    
    if affiliate > 0:
        embed.set_footer(text=f"Includes {affiliate}% affiliate fee • 🏆 = Winner")
    else:
        embed.set_footer(text="🏆 = Winner for that metric • Use /optimize for suggestions")
    
    # Send response
    await interaction.response.send_message(embed=embed, ephemeral=True)


# =============================================================================
# /SIMULATE COMMAND - Monte Carlo Simulation
# =============================================================================

@bot.tree.command(name="simulate", description="Run 1000 simulated lottery outcomes to see realistic profit ranges")
@app_commands.describe(
    prize="Prize amount in USDC (e.g., 5000)",
    ticket="Ticket price in USDC (e.g., 25)",
    odds="Odds as pick range - 1 in X (e.g., 250)",
    affiliate="Affiliate percentage (0-20, optional)",
    simulations="Number of simulations (100-5000, default 1000)"
)
async def simulate_command(
    interaction: discord.Interaction,
    prize: float,
    ticket: float,
    odds: int,
    affiliate: float = 0.0,
    simulations: int = 1000
):
    """
    Run Monte Carlo simulation to show realistic profit outcomes
    """
    
    # Input validation
    if prize < 100:
        await interaction.response.send_message(
            "❌ **Error:** Minimum prize is $100 USDC",
            ephemeral=True
        )
        return
    
    if ticket <= 0 or odds <= 0:
        await interaction.response.send_message(
            "❌ **Error:** Ticket price and odds must be positive!",
            ephemeral=True
        )
        return
    
    if affiliate < 0 or affiliate > 20:
        await interaction.response.send_message(
            "❌ **Error:** Affiliate percentage must be between 0 and 20",
            ephemeral=True
        )
        return
    
    if simulations < 100 or simulations > 5000:
        await interaction.response.send_message(
            "❌ **Error:** Simulations must be between 100 and 5000",
            ephemeral=True
        )
        return
    
    # Defer response since simulation might take a moment
    await interaction.response.defer(ephemeral=True)
    
    # Calculate constants
    platform_fee_rate = 0.05
    affiliate_rate = affiliate / 100
    net_rate = 1 - platform_fee_rate - affiliate_rate
    net_per_ticket = ticket * net_rate
    win_probability = 1 / odds
    
    # Run Monte Carlo simulation
    results = []
    wins_before_breakeven = 0
    total_tickets_sold = 0
    winner_counts = []
    
    breakeven_tickets = int(prize / net_per_ticket) + 1 if net_per_ticket > 0 else float('inf')
    
    for _ in range(simulations):
        # Simulate selling tickets until someone wins
        tickets_sold = 0
        winner_found = False
        
        while not winner_found:
            tickets_sold += 1
            # Each ticket has 1/odds chance of winning
            if random.random() < win_probability:
                winner_found = True
        
        # Calculate profit for this simulation
        gross_revenue = tickets_sold * ticket
        platform_fee = gross_revenue * platform_fee_rate
        affiliate_cost = gross_revenue * affiliate_rate
        net_profit = gross_revenue - platform_fee - affiliate_cost - prize
        
        results.append(net_profit)
        winner_counts.append(tickets_sold)
        total_tickets_sold += tickets_sold
        
        if tickets_sold < breakeven_tickets:
            wins_before_breakeven += 1
    
    # Calculate statistics
    results.sort()
    winner_counts.sort()
    
    avg_profit = sum(results) / len(results)
    median_profit = results[len(results) // 2]
    best_case = max(results)
    worst_case = min(results)
    
    avg_tickets = total_tickets_sold / simulations
    median_tickets = winner_counts[len(winner_counts) // 2]
    min_tickets = min(winner_counts)
    max_tickets = max(winner_counts)
    
    # Calculate percentiles
    p10 = results[int(len(results) * 0.10)]
    p25 = results[int(len(results) * 0.25)]
    p75 = results[int(len(results) * 0.75)]
    p90 = results[int(len(results) * 0.90)]
    
    # Win rate (profitable simulations)
    profitable_count = sum(1 for r in results if r > 0)
    profit_rate = (profitable_count / simulations) * 100
    
    # Early loss rate
    early_loss_rate = (wins_before_breakeven / simulations) * 100
    
    # Calculate RTP and expected ROI
    calc = RTPCalculator()
    rtp = calc.calculate_rtp(prize, ticket, odds)
    min_rtp, tier_name = calc.get_minimum_rtp(prize)
    passes_rtp = rtp >= min_rtp
    expected_roi = ((avg_profit) / prize) * 100
    
    # Format currency
    def fmt(val):
        return f"${val:,.2f}"
    
    # Create visual distribution bar
    def create_distribution_bar(results):
        # Count how many in each bucket
        total = len(results)
        very_negative = sum(1 for r in results if r < -prize * 0.5) / total * 10
        negative = sum(1 for r in results if -prize * 0.5 <= r < 0) / total * 10
        small_profit = sum(1 for r in results if 0 <= r < prize * 0.25) / total * 10
        medium_profit = sum(1 for r in results if prize * 0.25 <= r < prize * 0.5) / total * 10
        large_profit = sum(1 for r in results if r >= prize * 0.5) / total * 10
        
        bar = ""
        bar += "🔴" * int(very_negative)
        bar += "🟠" * int(negative)
        bar += "🟡" * int(small_profit)
        bar += "🟢" * int(medium_profit)
        bar += "💚" * int(large_profit)
        
        return bar if bar else "🟡"
    
    distribution_bar = create_distribution_bar(results)
    
    # Determine risk level
    if profit_rate >= 80 and avg_profit > 0:
        risk_level = "🟢 Low Risk"
        risk_desc = "High probability of profit"
    elif profit_rate >= 60 and avg_profit > 0:
        risk_level = "🟡 Moderate Risk"
        risk_desc = "Good odds, some variance"
    elif profit_rate >= 40:
        risk_level = "🟠 Higher Risk"
        risk_desc = "Significant variance expected"
    else:
        risk_level = "🔴 High Risk"
        risk_desc = "More likely to lose money"
    
    # Create embed
    embed = discord.Embed(
        title="🎲 Monte Carlo Simulation",
        description=f"Ran **{simulations:,}** simulated lottery outcomes",
        color=discord.Color.green() if profit_rate >= 60 else discord.Color.gold() if profit_rate >= 40 else discord.Color.red()
    )
    
    # Setup Info
    status = "✅" if passes_rtp else "❌"
    embed.add_field(
        name="📋 Setup",
        value=(
            f"**Prize:** {fmt(prize)}\n"
            f"**Ticket:** {fmt(ticket)}\n"
            f"**Odds:** 1 in {odds:,}\n"
            f"**RTP:** {rtp:.1f}% {status}"
        ),
        inline=True
    )
    
    # Profit Statistics
    embed.add_field(
        name="💰 Profit Statistics",
        value=(
            f"**Average:** {fmt(avg_profit)}\n"
            f"**Median:** {fmt(median_profit)}\n"
            f"**Best:** {fmt(best_case)}\n"
            f"**Worst:** {fmt(worst_case)}"
        ),
        inline=True
    )
    
    # Ticket Statistics
    embed.add_field(
        name="🎫 Tickets to Winner",
        value=(
            f"**Average:** {avg_tickets:.0f}\n"
            f"**Median:** {median_tickets}\n"
            f"**Fastest:** {min_tickets}\n"
            f"**Longest:** {max_tickets:,}"
        ),
        inline=True
    )
    
    # Percentile Breakdown
    embed.add_field(
        name="📊 Outcome Distribution",
        value=(
            f"**10th %ile:** {fmt(p10)}\n"
            f"**25th %ile:** {fmt(p25)}\n"
            f"**75th %ile:** {fmt(p75)}\n"
            f"**90th %ile:** {fmt(p90)}"
        ),
        inline=True
    )
    
    # Risk Analysis
    embed.add_field(
        name="⚠️ Risk Analysis",
        value=(
            f"**Profit Rate:** {profit_rate:.1f}%\n"
            f"**Early Loss Rate:** {early_loss_rate:.1f}%\n"
            f"**Risk Level:** {risk_level}\n"
            f"*{risk_desc}*"
        ),
        inline=True
    )
    
    # Visual Distribution
    embed.add_field(
        name="📈 Distribution",
        value=(
            f"{distribution_bar}\n"
            f"🔴 Big Loss → 💚 Big Profit"
        ),
        inline=True
    )
    
    # Interpretation
    if profit_rate >= 70 and avg_profit > prize * 0.1:
        interpretation = "🎯 **Strong Setup!** High probability of profit with good average returns."
    elif profit_rate >= 50 and avg_profit > 0:
        interpretation = "✅ **Decent Setup.** More likely to profit than lose, but expect variance."
    elif avg_profit > 0:
        interpretation = "⚠️ **Risky Setup.** Average is positive, but many simulations lost money."
    else:
        interpretation = "❌ **Poor Setup.** Average profit is negative - consider adjusting parameters."
    
    embed.add_field(
        name="💡 Interpretation",
        value=interpretation,
        inline=False
    )
    
    # Warnings
    warnings = []
    if not passes_rtp:
        warnings.append(f"⚠️ RTP {rtp:.1f}% is below {min_rtp}% minimum!")
    if early_loss_rate > 40:
        warnings.append(f"⚠️ {early_loss_rate:.0f}% chance of winner before break-even")
    if worst_case < -prize:
        warnings.append(f"⚠️ Worst case loses more than prize amount")
    
    if warnings:
        embed.add_field(
            name="⚠️ Warnings",
            value="\n".join(warnings),
            inline=False
        )
    
    embed.set_footer(text=f"Based on {simulations:,} simulations • Results vary in reality")
    
    # Send response
    await interaction.followup.send(embed=embed, ephemeral=True)


# =============================================================================
# /STATS COMMAND - Live Platform Statistics from Goldsky
# =============================================================================

@bot.tree.command(name="stats", description="View live Chance platform statistics")
async def stats_command(interaction: discord.Interaction):
    """
    Fetch and display live platform statistics from Goldsky subgraph
    """
    
    # Defer response since we're making API calls
    await interaction.response.defer(ephemeral=True)
    
    try:
        # GraphQL query for platform statistics
        query = """
        query GetPlatformStats {
          lotteries(first: 1000, orderBy: createdAt, orderDirection: desc) {
            id
            prizeAmount
            ticketPrice
            ticketsSold
            grossRevenue
            status
            hasWinner
            winner
            createdAt
            prizeProvider
          }
        }
        """
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                API_BASE_URL,
                json={"query": query},
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != 200:
                    await interaction.followup.send(
                        "❌ **Error:** Could not fetch platform statistics. Try again later.",
                        ephemeral=True
                    )
                    return
                
                try:
                    data = await response.json()
                except:
                    await interaction.followup.send(
                        "❌ **Error:** API returned invalid response. Try again later.",
                        ephemeral=True
                    )
                    return
                
                if 'errors' in data:
                    await interaction.followup.send(
                        "❌ **Error:** Subgraph returned an error. Try again later.",
                        ephemeral=True
                    )
                    return
                
                lotteries = data.get('data', {}).get('lotteries', [])
        
        if not lotteries:
            await interaction.followup.send(
                "📊 **No lotteries found!** The platform appears to be empty.",
                ephemeral=True
            )
            return
        
        # Calculate statistics
        total_lotteries = len(lotteries)
        
        # Count by status
        active_count = sum(1 for l in lotteries if l.get('status') == 'ACTIVE')
        completed_count = sum(1 for l in lotteries if l.get('status') == 'COMPLETED')
        expired_count = sum(1 for l in lotteries if l.get('status') == 'EXPIRED')
        
        # Calculate totals (convert from Wei - 6 decimals for USDC)
        total_prize_pool = 0
        total_volume = 0
        total_tickets = 0
        biggest_prize = 0
        biggest_prize_id = None
        winners_count = 0
        
        unique_creators = set()
        unique_winners = set()
        
        for lottery in lotteries:
            # Prize amount (Wei to USDC)
            prize_raw = lottery.get('prizeAmount', '0')
            try:
                prize = int(prize_raw) / 1_000_000 if prize_raw else 0
            except:
                prize = 0
            
            total_prize_pool += prize
            
            if prize > biggest_prize:
                biggest_prize = prize
                biggest_prize_id = lottery.get('id')
            
            # Gross revenue
            revenue_raw = lottery.get('grossRevenue', '0')
            try:
                revenue = int(revenue_raw) / 1_000_000 if revenue_raw else 0
            except:
                revenue = 0
            total_volume += revenue
            
            # Tickets sold
            tickets_raw = lottery.get('ticketsSold', '0')
            try:
                tickets = int(tickets_raw) if tickets_raw else 0
            except:
                tickets = 0
            total_tickets += tickets
            
            # Unique creators
            creator = lottery.get('prizeProvider')
            if creator:
                unique_creators.add(creator.lower())
            
            # Winners
            if lottery.get('hasWinner'):
                winners_count += 1
                winner = lottery.get('winner')
                if winner:
                    unique_winners.add(winner.lower())
        
        # Calculate averages
        avg_prize = total_prize_pool / total_lotteries if total_lotteries > 0 else 0
        avg_tickets_per_lottery = total_tickets / total_lotteries if total_lotteries > 0 else 0
        
        # Format currency
        def fmt(val):
            if val >= 1_000_000:
                return f"${val/1_000_000:.2f}M"
            elif val >= 1_000:
                return f"${val/1_000:.1f}K"
            else:
                return f"${val:,.2f}"
        
        # Create embed
        embed = discord.Embed(
            title="📊 Chance Platform Statistics",
            description="Live data from the Chance subgraph",
            color=discord.Color.blue()
        )
        
        # Overview
        embed.add_field(
            name="🎰 Lotteries",
            value=(
                f"**Total:** {total_lotteries:,}\n"
                f"**Active:** {active_count:,} 🟢\n"
                f"**Completed:** {completed_count:,} ✅\n"
                f"**Expired:** {expired_count:,} ⏰"
            ),
            inline=True
        )
        
        # Volume Stats
        embed.add_field(
            name="💰 Volume",
            value=(
                f"**Total Volume:** {fmt(total_volume)}\n"
                f"**Prize Pool:** {fmt(total_prize_pool)}\n"
                f"**Avg Prize:** {fmt(avg_prize)}\n"
                f"**Tickets Sold:** {total_tickets:,}"
            ),
            inline=True
        )
        
        # Records
        embed.add_field(
            name="🏆 Records",
            value=(
                f"**Biggest Prize:** {fmt(biggest_prize)}\n"
                f"**Total Winners:** {winners_count:,}\n"
                f"**Unique Creators:** {len(unique_creators):,}\n"
                f"**Unique Winners:** {len(unique_winners):,}"
            ),
            inline=True
        )
        
        # Activity indicator
        if active_count > 10:
            activity = "🔥 **Very Active** - Lots of live lotteries!"
        elif active_count > 5:
            activity = "✅ **Active** - Good selection available"
        elif active_count > 0:
            activity = "🟡 **Moderate** - A few lotteries live"
        else:
            activity = "😴 **Quiet** - No active lotteries right now"
        
        embed.add_field(
            name="📈 Platform Activity",
            value=activity,
            inline=False
        )
        
        # Quick Stats Bar
        completion_rate = (completed_count / total_lotteries * 100) if total_lotteries > 0 else 0
        win_rate = (winners_count / total_lotteries * 100) if total_lotteries > 0 else 0
        
        embed.add_field(
            name="📉 Quick Stats",
            value=(
                f"**Completion Rate:** {completion_rate:.1f}%\n"
                f"**Win Rate:** {win_rate:.1f}%\n"
                f"**Avg Tickets/Lottery:** {avg_tickets_per_lottery:.0f}"
            ),
            inline=False
        )
        
        embed.set_footer(text="Data from Goldsky Subgraph • Updates every 30 seconds")
        
        # Send response
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        print(f"Error in /stats command: {e}")
        await interaction.followup.send(
            f"❌ **Error:** Could not fetch statistics. Please try again later.",
            ephemeral=True
        )


# =============================================================================
# /LEADERBOARD COMMAND - Top Creators and Winners
# =============================================================================

@bot.tree.command(name="leaderboard", description="View top creators and winners on Chance")
@app_commands.describe(
    category="Choose leaderboard type"
)
@app_commands.choices(category=[
    app_commands.Choice(name="🎨 Top Creators - By lotteries created", value="creators"),
    app_commands.Choice(name="💰 Top Winners - By prizes won", value="winners"),
    app_commands.Choice(name="📊 Top Volume - By total volume generated", value="volume"),
])
async def leaderboard_command(
    interaction: discord.Interaction,
    category: str
):
    """
    Display leaderboards for creators or winners
    """
    
    # Defer response since we're making API calls
    await interaction.response.defer(ephemeral=True)
    
    try:
        # GraphQL query for leaderboard data
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
                API_BASE_URL,
                json={"query": query},
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != 200:
                    await interaction.followup.send(
                        "❌ **Error:** Could not fetch leaderboard data. Try again later.",
                        ephemeral=True
                    )
                    return
                
                try:
                    data = await response.json()
                except:
                    await interaction.followup.send(
                        "❌ **Error:** API returned invalid response. Try again later.",
                        ephemeral=True
                    )
                    return
                
                if 'errors' in data:
                    await interaction.followup.send(
                        "❌ **Error:** Subgraph returned an error. Try again later.",
                        ephemeral=True
                    )
                    return
                
                lotteries = data.get('data', {}).get('lotteries', [])
        
        if not lotteries:
            await interaction.followup.send(
                "📊 **No data found!** The platform appears to be empty.",
                ephemeral=True
            )
            return
        
        # Format currency
        def fmt(val):
            if val >= 1_000_000:
                return f"${val/1_000_000:.2f}M"
            elif val >= 1_000:
                return f"${val/1_000:.1f}K"
            else:
                return f"${val:,.0f}"
        
        # Shorten address
        def short_addr(addr):
            if not addr:
                return "Unknown"
            return f"{addr[:6]}...{addr[-4:]}"
        
        # Medal emojis
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        if category == "creators":
            # Aggregate by creator
            creator_stats = {}
            for lottery in lotteries:
                creator = lottery.get('prizeProvider', '').lower()
                if not creator:
                    continue
                
                if creator not in creator_stats:
                    creator_stats[creator] = {
                        'lotteries': 0,
                        'total_prize': 0,
                        'total_volume': 0,
                        'completed': 0
                    }
                
                creator_stats[creator]['lotteries'] += 1
                
                # Prize amount
                prize_raw = lottery.get('prizeAmount', '0')
                try:
                    prize = int(prize_raw) / 1_000_000 if prize_raw else 0
                except:
                    prize = 0
                creator_stats[creator]['total_prize'] += prize
                
                # Volume
                volume_raw = lottery.get('grossRevenue', '0')
                try:
                    volume = int(volume_raw) / 1_000_000 if volume_raw else 0
                except:
                    volume = 0
                creator_stats[creator]['total_volume'] += volume
                
                if lottery.get('status') == 'COMPLETED':
                    creator_stats[creator]['completed'] += 1
            
            # Sort by number of lotteries
            sorted_creators = sorted(
                creator_stats.items(),
                key=lambda x: x[1]['lotteries'],
                reverse=True
            )[:10]
            
            # Create embed
            embed = discord.Embed(
                title="🎨 Top Creators Leaderboard",
                description="Ranked by number of lotteries created",
                color=discord.Color.gold()
            )
            
            leaderboard_text = ""
            for i, (creator, stats) in enumerate(sorted_creators):
                medal = medals[i] if i < len(medals) else f"{i+1}."
                leaderboard_text += (
                    f"{medal} **{short_addr(creator)}**\n"
                    f"   📊 {stats['lotteries']} lotteries • {fmt(stats['total_prize'])} prizes\n"
                )
            
            if leaderboard_text:
                embed.add_field(
                    name="🏆 Rankings",
                    value=leaderboard_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name="🏆 Rankings",
                    value="No creators found!",
                    inline=False
                )
        
        elif category == "winners":
            # Aggregate by winner
            winner_stats = {}
            for lottery in lotteries:
                if not lottery.get('hasWinner'):
                    continue
                
                winner = lottery.get('winner', '').lower()
                if not winner:
                    continue
                
                if winner not in winner_stats:
                    winner_stats[winner] = {
                        'wins': 0,
                        'total_won': 0
                    }
                
                winner_stats[winner]['wins'] += 1
                
                # Prize amount won
                prize_raw = lottery.get('prizeAmount', '0')
                try:
                    prize = int(prize_raw) / 1_000_000 if prize_raw else 0
                except:
                    prize = 0
                winner_stats[winner]['total_won'] += prize
            
            # Sort by total won
            sorted_winners = sorted(
                winner_stats.items(),
                key=lambda x: x[1]['total_won'],
                reverse=True
            )[:10]
            
            # Create embed
            embed = discord.Embed(
                title="💰 Top Winners Leaderboard",
                description="Ranked by total prizes won",
                color=discord.Color.green()
            )
            
            leaderboard_text = ""
            for i, (winner, stats) in enumerate(sorted_winners):
                medal = medals[i] if i < len(medals) else f"{i+1}."
                leaderboard_text += (
                    f"{medal} **{short_addr(winner)}**\n"
                    f"   💵 {fmt(stats['total_won'])} won • {stats['wins']} wins\n"
                )
            
            if leaderboard_text:
                embed.add_field(
                    name="🏆 Rankings",
                    value=leaderboard_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name="🏆 Rankings",
                    value="No winners found yet!",
                    inline=False
                )
        
        else:  # volume
            # Aggregate by creator volume
            creator_volume = {}
            for lottery in lotteries:
                creator = lottery.get('prizeProvider', '').lower()
                if not creator:
                    continue
                
                if creator not in creator_volume:
                    creator_volume[creator] = {
                        'volume': 0,
                        'lotteries': 0,
                        'tickets': 0
                    }
                
                creator_volume[creator]['lotteries'] += 1
                
                # Volume
                volume_raw = lottery.get('grossRevenue', '0')
                try:
                    volume = int(volume_raw) / 1_000_000 if volume_raw else 0
                except:
                    volume = 0
                creator_volume[creator]['volume'] += volume
                
                # Tickets
                tickets_raw = lottery.get('ticketsSold', '0')
                try:
                    tickets = int(tickets_raw) if tickets_raw else 0
                except:
                    tickets = 0
                creator_volume[creator]['tickets'] += tickets
            
            # Sort by volume
            sorted_volume = sorted(
                creator_volume.items(),
                key=lambda x: x[1]['volume'],
                reverse=True
            )[:10]
            
            # Create embed
            embed = discord.Embed(
                title="📊 Top Volume Leaderboard",
                description="Ranked by total volume generated",
                color=discord.Color.blue()
            )
            
            leaderboard_text = ""
            for i, (creator, stats) in enumerate(sorted_volume):
                medal = medals[i] if i < len(medals) else f"{i+1}."
                leaderboard_text += (
                    f"{medal} **{short_addr(creator)}**\n"
                    f"   💰 {fmt(stats['volume'])} volume • {stats['tickets']:,} tickets\n"
                )
            
            if leaderboard_text:
                embed.add_field(
                    name="🏆 Rankings",
                    value=leaderboard_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name="🏆 Rankings",
                    value="No volume data found!",
                    inline=False
                )
        
        embed.set_footer(text="Data from Goldsky Subgraph • Updates in real-time")
        
        # Send response
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        print(f"Error in /leaderboard command: {e}")
        await interaction.followup.send(
            f"❌ **Error:** Could not fetch leaderboard. Please try again later.",
            ephemeral=True
        )


@bot.tree.command(name="alert", description="Create an alert for lotteries matching your criteria")
@app_commands.describe(
    min_prize="Minimum prize amount in USDC (optional)",
    max_prize="Maximum prize amount in USDC (optional)",
    max_ticket="Maximum ticket price in USDC (optional)",
    min_rtp="Minimum RTP percentage (optional)"
)
async def alert_command(
    interaction: discord.Interaction,
    min_prize: float = None,
    max_prize: float = None,
    max_ticket: float = None,
    min_rtp: float = None
):
    """Create an alert for new lotteries matching criteria"""
    
    # Validate at least one criteria is set
    if min_prize is None and max_prize is None and max_ticket is None and min_rtp is None:
        await interaction.response.send_message(
            "❌ **Error:** Please set at least one criteria!\n\n"
            "**Examples:**\n"
            "`/alert min_prize:10000` - Alert for prizes $10K+\n"
            "`/alert max_ticket:10` - Alert for tickets under $10\n"
            "`/alert min_prize:5000 max_ticket:25` - Combined criteria",
            ephemeral=True
        )
        return
    
    # Validate values
    if (min_prize is not None and min_prize < 0) or \
       (max_prize is not None and max_prize < 0) or \
       (max_ticket is not None and max_ticket < 0) or \
       (min_rtp is not None and min_rtp < 0):
        await interaction.response.send_message(
            "❌ **Error:** All values must be positive!",
            ephemeral=True
        )
        return
    
    if max_prize is not None and min_prize is not None and min_prize > max_prize:
        await interaction.response.send_message(
            "❌ **Error:** min_prize cannot be greater than max_prize!",
            ephemeral=True
        )
        return
    
    if min_rtp is not None and min_rtp > 100:
        await interaction.response.send_message(
            "❌ **Error:** min_rtp cannot exceed 100%!",
            ephemeral=True
        )
        return
    
    # Create alert
    alert = {
        'min_prize': min_prize,
        'max_prize': max_prize,
        'max_ticket': max_ticket,
        'min_rtp': min_rtp,
    }
    
    success, message = AlertManager.add_alert(interaction.user.id, alert)
    
    if not success:
        await interaction.response.send_message(f"❌ {message}", ephemeral=True)
        return
    
    # Format criteria for display
    def fmt(val):
        return f"${val:,.0f}" if val else "Any"
    
    criteria_parts = []
    if alert['min_prize']:
        criteria_parts.append(f"Prize ≥ {fmt(alert['min_prize'])}")
    if alert['max_prize']:
        criteria_parts.append(f"Prize ≤ {fmt(alert['max_prize'])}")
    if alert['max_ticket']:
        criteria_parts.append(f"Ticket ≤ {fmt(alert['max_ticket'])}")
    if alert['min_rtp']:
        criteria_parts.append(f"RTP ≥ {alert['min_rtp']}%")
    
    criteria_text = " • ".join(criteria_parts)
    
    embed = discord.Embed(
        title="🔔 Alert Created!",
        description=f"You'll be DMed when a matching lottery appears.",
        color=discord.Color.green()
    )
    
    embed.add_field(
        name="📋 Your Criteria",
        value=criteria_text,
        inline=False
    )
    
    embed.add_field(
        name="💡 Tips",
        value=(
            f"• Use `/myalerts` to view your alerts\n"
            f"• Use `/deletealert` to remove an alert\n"
            f"• Max {AlertManager.MAX_ALERTS_PER_USER} alerts per user"
        ),
        inline=False
    )
    
    embed.set_footer(text="Alerts reset when bot restarts")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="myalerts", description="View your active lottery alerts")
async def myalerts_command(interaction: discord.Interaction):
    """View all alerts for the user"""
    
    alerts = AlertManager.get_alerts(interaction.user.id)
    
    if not alerts:
        await interaction.response.send_message(
            "📭 **You don't have any alerts!**\n\n"
            "Create one with `/alert`\n"
            "Example: `/alert min_prize:10000 max_ticket:25`",
            ephemeral=True
        )
        return
    
    def fmt(val):
        return f"${val:,.0f}" if val else "Any"
    
    embed = discord.Embed(
        title="🔔 Your Alerts",
        description=f"You have **{len(alerts)}/{AlertManager.MAX_ALERTS_PER_USER}** alerts",
        color=discord.Color.blue()
    )
    
    for alert in alerts:
        criteria_parts = []
        if alert.get('min_prize'):
            criteria_parts.append(f"Prize ≥ {fmt(alert['min_prize'])}")
        if alert.get('max_prize'):
            criteria_parts.append(f"Prize ≤ {fmt(alert['max_prize'])}")
        if alert.get('max_ticket'):
            criteria_parts.append(f"Ticket ≤ {fmt(alert['max_ticket'])}")
        if alert.get('min_rtp'):
            criteria_parts.append(f"RTP ≥ {alert['min_rtp']}%")
        
        criteria_text = "\n".join(criteria_parts) if criteria_parts else "No criteria"
        
        embed.add_field(
            name=f"Alert #{alert['id']}",
            value=criteria_text,
            inline=True
        )
    
    embed.set_footer(text="Use /deletealert id:<number> to remove an alert")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="deletealert", description="Delete one of your alerts")
@app_commands.describe(
    id="Alert ID to delete (use /myalerts to see IDs)"
)
async def deletealert_command(
    interaction: discord.Interaction,
    id: int
):
    """Delete an alert by ID"""
    
    success, message = AlertManager.delete_alert(interaction.user.id, id)
    
    if success:
        await interaction.response.send_message(
            f"✅ {message}",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ {message}",
            ephemeral=True
        )


# Error handling
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle slash command errors"""
    if isinstance(error, app_commands.CommandInvokeError):
        await interaction.response.send_message(
            f"❌ An error occurred: {str(error)}",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "❌ An unexpected error occurred. Please try again.",
            ephemeral=True
        )
    
    print(f"Error in command: {error}")

# Run the bot
if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN not found in .env file")
    else:
        # Start Flask web server for Railway health checks
        keep_alive()
        
        # Start Discord bot
        bot.run(TOKEN)
