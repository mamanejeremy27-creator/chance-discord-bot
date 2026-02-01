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
        title="🎰 Chance RTP Calculator - Help",
        color=discord.Color.blue(),
        description="Calculate Return to Player (RTP) for your lottery and validate against tier minimums."
    )
    
    embed.add_field(
        name="📝 Commands",
        value=(
            "**`/rtp`** - Calculate RTP and validate tiers\n"
            "**`/breakeven`** - Calculate profit scenarios\n"
            "**`/help`** - Show this message"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🔢 /rtp Parameters",
        value=(
            "**prize** - Prize amount in USDC (e.g., 5000)\n"
            "**ticket** - Ticket price in USDC (e.g., 25)\n"
            "**odds** - Pick range for 1-in-X odds (e.g., 250)"
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
        name="💡 Example",
        value=(
            "`/rtp prize:5000 ticket:25 odds:250`\n"
            "`/breakeven prize:5000 ticket:25 odds:250`"
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
