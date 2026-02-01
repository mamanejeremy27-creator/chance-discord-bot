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
        self.is_first_run = True  # Prevent posting old lotteries on startup
        
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
        """Poll Goldsky subgraph for new lotteries and post them"""
        try:
            # GraphQL query for recent lotteries
            query = """
            query GetRecentLotteries {
              lotteries(
                first: 20
                orderBy: createdAt
                orderDirection: desc
                where: { status: ACTIVE }
              ) {
                id
                prizeProvider
                prizeToken
                prizeAmount
                ticketPrice
                pickRange
                endTime
                maxTickets
                affiliateFeeBps
                rtpValue
                createdAt
                status
                hasWinner
                winner
                ticketsSold
                grossRevenue
                netRevenueCollected
              }
            }
            """
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_base_url,
                    json={"query": query},
                    headers={"Content-Type": "application/json"}
                ) as response:
                    if response.status != 200:
                        print(f"⚠️ Subgraph returned status {response.status}")
                        return
                    
                    data = await response.json()
                    
                    # Handle GraphQL errors
                    if 'errors' in data:
                        print(f"⚠️ GraphQL errors: {data['errors']}")
                        return
                    
                    lotteries = data.get('data', {}).get('lotteries', [])
                    
                    # On first run, just mark lotteries as seen without posting
                    if self.is_first_run:
                        print(f"📝 First run: marking {len(lotteries)} existing lotteries as seen")
                        for lottery in lotteries:
                            lottery_id = lottery.get('id')
                            if lottery_id:
                                self.posted_lotteries.add(lottery_id)
                        self.is_first_run = False
                        print("✅ Bot will now post new lotteries only")
                        return
                    
                    # Process new lotteries
                    new_count = 0
                    for lottery in lotteries:
                        lottery_id = lottery.get('id') or lottery.get('contractAddress')
                        
                        # Skip if we've already posted this lottery
                        if lottery_id in self.posted_lotteries:
                            continue
                        
                        # Transform subgraph data to expected format
                        formatted_lottery = self._format_subgraph_data(lottery)
                        
                        # Post to Discord with rate limiting
                        try:
                            await self.post_lottery(formatted_lottery)
                            new_count += 1
                            
                            # Add small delay between posts to avoid rate limits
                            if new_count > 1:
                                await asyncio.sleep(2)  # 2 second delay between posts
                        except Exception as e:
                            print(f"❌ Error posting lottery {lottery_id}: {e}")
                            continue
                        
                        # Mark as posted
                        self.posted_lotteries.add(lottery_id)
                        
                        # Limit set size to prevent memory issues
                        if len(self.posted_lotteries) > 10000:
                            # Remove oldest entries (keep last 5000)
                            self.posted_lotteries = set(list(self.posted_lotteries)[-5000:])
        
        except Exception as e:
            print(f"❌ Error in check_for_new_lotteries: {e}")
    
    def _format_subgraph_data(self, lottery_data: Dict) -> Dict:
        """
        Transform Goldsky subgraph data into the format expected by post_lottery
        
        Args:
            lottery_data: Raw data from subgraph (matching Chance v4 schema)
            
        Returns:
            Formatted lottery data
        """
        # Get pickRange (this is the odds - e.g., 250 means 1-in-250)
        pick_range = int(lottery_data.get('pickRange', 100))
        
        # Convert Wei to USDC (6 decimals for USDC)
        # prizeAmount and ticketPrice are in Wei
        prize_wei = int(lottery_data.get('prizeAmount', 0))
        ticket_price_wei = int(lottery_data.get('ticketPrice', 0))
        
        prize = prize_wei / 1_000_000  # USDC has 6 decimals
        ticket_price = ticket_price_wei / 1_000_000
        
        # Get affiliate fee (in basis points - 1000 = 10%)
        # Convert from basis points to percentage
        affiliate_bps = lottery_data.get('affiliateFeeBps', '0')
        affiliate_pct = float(affiliate_bps) / 100 if affiliate_bps else 0
        
        # Get maxTickets (it's a String in the schema)
        max_tickets_str = lottery_data.get('maxTickets')
        max_tickets = int(max_tickets_str) if max_tickets_str and max_tickets_str != '0' else None
        
        # Calculate duration from endTime (Unix timestamp)
        end_time = lottery_data.get('endTime')
        created_at = lottery_data.get('createdAt')
        duration = None
        if end_time and created_at:
            duration_seconds = int(end_time) - int(created_at)
            duration = duration_seconds // 3600  # Convert to hours
        
        # Build lottery URL using lottery ID
        lottery_id = lottery_data.get('id', '')
        # Use the actual Chance web app URL (Vercel deployment)
        lottery_url = f"https://chance-web-nikita-3888-chancedotfun.vercel.app/lottery/details/{lottery_id}" if lottery_id else "https://chance-web-nikita-3888-chancedotfun.vercel.app"
        
        # Get creator address (prizeProvider in schema)
        creator = lottery_data.get('prizeProvider', '').lower()  # Ensure lowercase
        
        return {
            'id': lottery_id,
            'contract_address': lottery_id,  # Using ID as identifier
            'creator': creator,
            'prize': prize,
            'ticket_price': ticket_price,
            'odds': pick_range,  # Using pickRange as odds
            'duration': duration,
            'max_tickets': max_tickets,
            'affiliate_percentage': affiliate_pct,
            'created_at': lottery_data.get('createdAt', ''),
            'url': lottery_url,
            'tickets_sold': int(lottery_data.get('ticketsSold', 0)),
            'status': lottery_data.get('status', 'ACTIVE'),
            'rtp': lottery_data.get('rtpValue')  # RTP might be pre-calculated in subgraph
        }
    
    async def get_recent_winners(self, limit: int = 10):
        """
        Query subgraph for recent winners
        
        Args:
            limit: Number of winners to fetch
            
        Returns:
            List of winner data
        """
        query = f"""
        query GetRecentWinners {{
          lotteries(
            first: {limit}
            orderBy: createdAt
            orderDirection: desc
            where: {{ status: COMPLETED, hasWinner: true }}
          ) {{
            id
            prizeProvider
            prizeAmount
            ticketPrice
            winner
            ticketsSold
            winningNumber
            createdAt
          }}
        }}
        """
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_base_url,
                    json={"query": query},
                    headers={"Content-Type": "application/json"}
                ) as response:
                    if response.status != 200:
                        return []
                    
                    data = await response.json()
                    
                    if 'errors' in data:
                        print(f"⚠️ GraphQL errors: {data['errors']}")
                        return []
                    
                    return data.get('data', {}).get('lotteries', [])
        except Exception as e:
            print(f"❌ Error fetching winners: {e}")
            return []
    
    async def get_global_stats(self):
        """
        Query subgraph for global statistics
        
        Returns:
            Dict with global stats
        """
        query = """
        query GetGlobalStats {
          lotteries(first: 1000) {
            id
            prizeAmount
            ticketsSold
            ticketPrice
            grossRevenue
            status
          }
        }
        """
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_base_url,
                    json={"query": query},
                    headers={"Content-Type": "application/json"}
                ) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    
                    if 'errors' in data:
                        print(f"⚠️ GraphQL errors: {data['errors']}")
                        return None
                    
                    lotteries = data.get('data', {}).get('lotteries', [])
                    
                    # Calculate stats
                    total_volume = 0
                    total_tickets = 0
                    completed_count = 0
                    active_count = 0
                    
                    for lottery in lotteries:
                        tickets_sold = int(lottery.get('ticketsSold', 0))
                        
                        # Use grossRevenue if available (already calculated on-chain)
                        gross_revenue = lottery.get('grossRevenue')
                        if gross_revenue:
                            total_volume += int(gross_revenue) / 1_000_000
                        else:
                            # Fallback: calculate from ticketPrice * ticketsSold
                            ticket_price_wei = int(lottery.get('ticketPrice', 0))
                            total_volume += (tickets_sold * ticket_price_wei) / 1_000_000
                        
                        total_tickets += tickets_sold
                        
                        status = lottery.get('status', '')
                        if status == 'COMPLETED':
                            completed_count += 1
                        elif status == 'ACTIVE':
                            active_count += 1
                    
                    return {
                        'total_volume': total_volume,
                        'total_tickets': total_tickets,
                        'total_winners': completed_count,
                        'active_lotteries': active_count,
                        'total_lotteries': len(lotteries)
                    }
        except Exception as e:
            print(f"❌ Error fetching stats: {e}")
            return None
    
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
