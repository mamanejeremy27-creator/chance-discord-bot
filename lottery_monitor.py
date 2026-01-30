import discord
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional
import json

class LotteryMonitor:
    """Monitors Chance API for new lotteries and posts to Discord channels"""
    
    def __init__(self, bot: discord.Client, api_base_url: str):
        self.bot = bot
        self.api_base_url = api_base_url
        self.posted_lotteries = set()  # Track which lotteries we've already posted
        self.is_running = False
        
    def configure_channels(self, channel_ids: Dict[str, int]):
        """
        Configure Discord channel IDs for posting
        
        Args:
            channel_ids: Dict with keys:
                - 'new_lotteries': Channel for all new lotteries
                - 'high_value': Channel for $10K+ prizes
                - 'budget_plays': Channel for <$10 tickets
                - 'moonshots': Channel for $50K+ prizes
        """
        self.channels = channel_ids
        
    async def start(self, check_interval: int = 30):
        """
        Start monitoring for new lotteries
        
        Args:
            check_interval: Seconds between API checks (default 30)
        """
        self.is_running = True
        print(f"🔍 Lottery monitor started (checking every {check_interval}s)")
        
        while self.is_running:
            try:
                await self.check_for_new_lotteries()
            except Exception as e:
                print(f"❌ Error checking lotteries: {e}")
            
            await asyncio.sleep(check_interval)
    
    def stop(self):
        """Stop the lottery monitor"""
        self.is_running = False
        print("🛑 Lottery monitor stopped")
    
    async def check_for_new_lotteries(self):
        """Poll API for new lotteries and post them"""
        try:
            async with aiohttp.ClientSession() as session:
                # TODO: Replace with actual API endpoint when provided
                url = f"{self.api_base_url}/lotteries/recent"
                
                async with session.get(url) as response:
                    if response.status != 200:
                        print(f"⚠️ API returned status {response.status}")
                        return
                    
                    data = await response.json()
                    lotteries = data.get('lotteries', [])
                    
                    for lottery in lotteries:
                        lottery_id = lottery.get('id') or lottery.get('contract_address')
                        
                        # Skip if we've already posted this lottery
                        if lottery_id in self.posted_lotteries:
                            continue
                        
                        # Post to Discord
                        await self.post_lottery(lottery)
                        
                        # Mark as posted
                        self.posted_lotteries.add(lottery_id)
                        
                        # Limit set size to prevent memory issues
                        if len(self.posted_lotteries) > 10000:
                            # Remove oldest entries (keep last 5000)
                            self.posted_lotteries = set(list(self.posted_lotteries)[-5000:])
        
        except Exception as e:
            print(f"❌ Error in check_for_new_lotteries: {e}")
    
    async def post_lottery(self, lottery_data: Dict):
        """
        Post a lottery to appropriate Discord channels
        
        Expected lottery_data format:
        {
            'id': str,
            'contract_address': str,
            'prize': float (in USDC),
            'ticket_price': float (in USDC),
            'odds': int (pick range),
            'duration': int (seconds) or None,
            'max_tickets': int or None,
            'affiliate_percentage': float (0-20),
            'creator': str (address),
            'created_at': str (ISO timestamp),
            'url': str (link to lottery on chance.fun)
        }
        """
        try:
            # Extract data
            prize = lottery_data.get('prize', 0)
            ticket_price = lottery_data.get('ticket_price', 0)
            odds = lottery_data.get('odds', 1)
            duration = lottery_data.get('duration')
            max_tickets = lottery_data.get('max_tickets')
            affiliate = lottery_data.get('affiliate_percentage', 0)
            url = lottery_data.get('url', 'https://chance.fun')
            
            # Calculate RTP
            rtp = self.calculate_rtp(prize, ticket_price, odds)
            
            # Get minimum RTP for tier
            min_rtp, tier_name = self.get_minimum_rtp(prize)
            passes = rtp >= min_rtp
            
            # Create embed
            embed = self.create_lottery_embed(lottery_data, rtp, min_rtp, passes)
            
            # Determine which channels to post to
            channels_to_post = [self.channels.get('new_lotteries')]
            
            # Add high-value channel if prize >= $10K
            if prize >= 10000:
                channels_to_post.append(self.channels.get('high_value'))
            
            # Add budget-plays channel if ticket < $10
            if ticket_price < 10:
                channels_to_post.append(self.channels.get('budget_plays'))
            
            # Add moonshots channel if prize >= $50K
            if prize >= 50000:
                channels_to_post.append(self.channels.get('moonshots'))
            
            # Post to all relevant channels
            for channel_id in channels_to_post:
                if channel_id:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        await channel.send(embed=embed)
                        print(f"✅ Posted lottery to #{channel.name}")
                    else:
                        print(f"⚠️ Channel {channel_id} not found")
        
        except Exception as e:
            print(f"❌ Error posting lottery: {e}")
    
    def create_lottery_embed(self, lottery_data: Dict, rtp: float, min_rtp: float, passes: bool) -> discord.Embed:
        """Create a Discord embed for a lottery announcement"""
        
        prize = lottery_data.get('prize', 0)
        ticket_price = lottery_data.get('ticket_price', 0)
        odds = lottery_data.get('odds', 1)
        duration = lottery_data.get('duration')
        max_tickets = lottery_data.get('max_tickets')
        affiliate = lottery_data.get('affiliate_percentage', 0)
        url = lottery_data.get('url', 'https://chance.fun')
        contract = lottery_data.get('contract_address', '')
        
        # Determine embed color based on RTP
        if rtp >= 85:
            color = discord.Color.green()  # Very competitive
        elif rtp >= 75:
            color = discord.Color.blue()   # Competitive
        elif passes:
            color = discord.Color.orange() # Barely passes
        else:
            color = discord.Color.red()    # Fails minimum
        
        # Create embed
        embed = discord.Embed(
            title="🎰 NEW LOTTERY LIVE",
            color=color,
            url=url,
            timestamp=datetime.utcnow()
        )
        
        # Prize and ticket info
        embed.add_field(
            name="💰 Prize",
            value=f"**${prize:,.2f}** USDC",
            inline=True
        )
        
        embed.add_field(
            name="🎫 Ticket Price",
            value=f"**${ticket_price:.2f}** USDC",
            inline=True
        )
        
        embed.add_field(
            name="📊 Odds",
            value=f"**1 in {odds:,}**",
            inline=True
        )
        
        # RTP with status emoji
        rtp_emoji = "✅" if passes else "❌"
        embed.add_field(
            name="📈 RTP",
            value=f"**{rtp:.2f}%** {rtp_emoji}",
            inline=True
        )
        
        # Duration
        if duration:
            hours = duration // 3600
            if hours < 24:
                duration_str = f"{hours} hours"
            else:
                days = hours // 24
                duration_str = f"{days} days"
        else:
            duration_str = "Unlimited"
        
        embed.add_field(
            name="⏰ Duration",
            value=f"**{duration_str}**",
            inline=True
        )
        
        # Max tickets
        tickets_str = f"**{max_tickets:,}**" if max_tickets else "**Unlimited**"
        embed.add_field(
            name="🎟️ Max Tickets",
            value=tickets_str,
            inline=True
        )
        
        # Affiliate percentage
        if affiliate > 0:
            embed.add_field(
                name="💸 Affiliate Rewards",
                value=f"**{affiliate}%**",
                inline=True
            )
        
        # Add market position assessment
        if rtp >= 85:
            market_msg = "🔥 Very competitive! Player-friendly RTP."
        elif rtp >= 75:
            market_msg = "✅ Competitive RTP. Good value."
        elif passes:
            market_msg = "⚠️ Meets minimum but not highly competitive."
        else:
            market_msg = f"❌ Below {min_rtp}% minimum for this tier."
        
        embed.add_field(
            name="💡 Market Position",
            value=market_msg,
            inline=False
        )
        
        # Add play button
        embed.add_field(
            name="🎮 Play Now",
            value=f"[Click to Play]({url})",
            inline=False
        )
        
        # Footer with contract address
        if contract:
            embed.set_footer(text=f"Contract: {contract[:8]}...{contract[-6:]}")
        
        return embed
    
    @staticmethod
    def calculate_rtp(prize: float, ticket_price: float, odds: int) -> float:
        """Calculate RTP percentage"""
        if ticket_price <= 0 or odds <= 0:
            return 0
        
        probability = 1 / odds
        rtp = (prize * probability) / ticket_price
        return rtp * 100
    
    @staticmethod
    def get_minimum_rtp(prize: float) -> tuple[float, str]:
        """Get minimum RTP requirement based on prize tier"""
        if prize < 100:
            return 0, "Below minimum ($100+)"
        elif prize < 10000:
            return 70, "$100-$10K tier"
        elif prize < 100000:
            return 60, "$10K-$100K tier"
        else:
            return 50, "$100K+ tier"


# Example usage in bot.py:
"""
# In bot.py, after bot initialization:

lottery_monitor = LotteryMonitor(
    bot=bot,
    api_base_url="https://api.chance.fun"  # Replace with actual API
)

# Configure channel IDs (get these from Discord - right click channel > Copy ID)
lottery_monitor.configure_channels({
    'new_lotteries': 1234567890123456789,  # Replace with actual channel ID
    'high_value': 1234567890123456789,     # Replace with actual channel ID
    'budget_plays': 1234567890123456789,   # Replace with actual channel ID
    'moonshots': 1234567890123456789,      # Replace with actual channel ID
})

# Start monitoring when bot is ready
@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Failed to sync commands: {e}')
    
    # Start lottery monitor
    bot.loop.create_task(lottery_monitor.start(check_interval=30))
"""
