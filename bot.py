import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
from lottery_monitor import LotteryMonitor

# Flask web server to keep Railway happy
from flask import Flask
from threading import Thread

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
API_BASE_URL = os.getenv('CHANCE_API_URL', 'https://api.chance.fun')

# Flask app for health checks
flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Chance Discord Bot is running! ✅"

@flask_app.route('/health')
def health():
    return {"status": "healthy", "bot": "online"}

def run_flask():
    """Run Flask app in a separate thread"""
    flask_app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """Start Flask server to satisfy Railway's health check"""
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("🌐 Web server started on port 8080")

# Channel IDs (replace these with your actual channel IDs)
# To get channel ID: Right-click channel in Discord > Copy ID
# (You need Developer Mode enabled: Settings > Advanced > Developer Mode)
CHANNEL_IDS = {
    'new_lotteries': int(os.getenv('CHANNEL_NEW_LOTTERIES', '0')),
    'high_value': int(os.getenv('CHANNEL_HIGH_VALUE', '0')),
    'budget_plays': int(os.getenv('CHANNEL_BUDGET_PLAYS', '0')),
    'moonshots': int(os.getenv('CHANNEL_MOONSHOTS', '0')),
}

# Bot setup with intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Initialize lottery monitor
lottery_monitor = LotteryMonitor(bot=bot, api_base_url=API_BASE_URL)

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
    if all(CHANNEL_IDS.values()):
        lottery_monitor.configure_channels(CHANNEL_IDS)
        bot.loop.create_task(lottery_monitor.start(check_interval=30))
        print("✅ Lottery monitor enabled")
    else:
        print("⚠️ Lottery monitor disabled - configure channel IDs in .env")
        print("   See .env.example for instructions")

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
        name="📝 Commands",
        value=(
            "**`/rtp`** - Calculate RTP and validate tiers\n"
            "**`/breakeven`** - Calculate profit scenarios\n"
            "**`/optimize`** - Get optimized parameters for your goals\n"
            "**`/preview`** - Preview your lottery before deploying\n"
            "**`/compare`** - Compare two lottery setups side-by-side\n"
            "**`/help`** - Show this message"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎯 /optimize - Parameter Optimizer",
        value=(
            "Get the best settings for your lottery!\n"
            "**`/optimize prize:5000 target:profit`**\n"
            "**`/optimize prize:5000 target:volume`**\n"
            "**`/optimize prize:5000 target:balanced`**"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⚖️ /compare - Side-by-Side Comparison",
        value=(
            "Compare two setups to find the best one:\n"
            "**`/compare prize1:5000 ticket1:25 odds1:250 prize2:5000 ticket2:50 odds2:150`**"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📊 RTP Tiers",
        value=(
            "**$100 - $10,000:** 70% minimum\n"
            "**$10,000 - $100,000:** 60% minimum\n"
            "**$100,000+:** 50% minimum"
        ),
        inline=False
    )
    
    embed.add_field(
        name="💡 Quick Examples",
        value=(
            "`/rtp prize:5000 ticket:25 odds:250`\n"
            "`/breakeven prize:5000 ticket:25 odds:250 affiliate:10`\n"
            "`/optimize prize:5000 target:balanced`\n"
            "`/compare prize1:5000 ticket1:25 odds1:250 prize2:5000 ticket2:50 odds2:150`"
        ),
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
