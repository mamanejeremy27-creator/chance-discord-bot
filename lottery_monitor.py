import discord
import asyncio
import aiohttp
from datetime import datetime, timezone
from typing import Dict, List, Optional
import json

class LotteryMonitor:
    """Monitors Chance API for new lotteries and posts to Discord channels"""
    
    def __init__(self, bot: discord.Client, api_base_url: str):
        self.bot = bot
        self.api_base_url = api_base_url
        self.posted_lotteries = set()  # Track which lotteries we've already posted
        self.posted_winners = set()    # Track which winners we've already announced
        self.is_running = False
        self.is_first_run = True  # Prevent posting old lotteries on startup
        self.alert_callback = None  # Callback for alert notifications
        
        # Health tracking
        self.last_successful_poll = None
        self.consecutive_failures = 0
        self.max_consecutive_failures = 10  # Alert after this many failures
        
    def configure_channels(self, channel_ids: Dict[str, int]):
        """
        Configure Discord channel IDs for posting
        
        Args:
            channel_ids: Dict with keys:
                - 'new_lotteries': Channel for all new lotteries
                - 'high_value': Channel for $10K+ prizes
                - 'budget_plays': Channel for <$10 tickets
                - 'moonshots': Channel for $50K+ prizes
                - 'winners': Channel for winner announcements
        """
        self.channels = channel_ids
    
    def set_alert_callback(self, callback):
        """Set callback function for alert notifications"""
        self.alert_callback = callback
        
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
                await self.check_for_winners()  # Also check for winners
            except Exception as e:
                print(f"❌ Error checking lotteries: {e}")
            
            await asyncio.sleep(check_interval)
    
    def stop(self):
        """Stop the lottery monitor"""
        self.is_running = False
        print("🛑 Lottery monitor stopped")
    
    async def _fetch_with_retry(self, query: str, max_retries: int = 3, retry_delay: int = 5) -> Optional[Dict]:
        """
        Fetch data from subgraph with retry logic
        
        Args:
            query: GraphQL query string
            max_retries: Maximum number of retry attempts
            retry_delay: Seconds to wait between retries
            
        Returns:
            Parsed JSON data or None on failure
        """
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.api_base_url,
                        json={"query": query},
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        if response.status != 200:
                            print(f"⚠️ Subgraph returned status {response.status} (attempt {attempt + 1}/{max_retries})")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                                continue
                            return None
                        
                        # Try to parse JSON, handle HTML error pages
                        try:
                            data = await response.json()
                        except Exception as json_error:
                            text = await response.text()
                            if '<html' in text.lower():
                                print(f"⚠️ API returned HTML instead of JSON (Cloudflare error, attempt {attempt + 1}/{max_retries})")
                            else:
                                print(f"⚠️ Failed to parse JSON: {json_error}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delay * (attempt + 1))
                                continue
                            return None
                        
                        # Handle GraphQL errors
                        if 'errors' in data:
                            print(f"⚠️ GraphQL errors: {data['errors']}")
                            return None
                        
                        # Success!
                        self.consecutive_failures = 0
                        self.last_successful_poll = datetime.now(timezone.utc)
                        return data
                        
            except asyncio.TimeoutError:
                print(f"⚠️ Request timeout (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))
                    continue
            except Exception as e:
                print(f"⚠️ Request error: {e} (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))
                    continue
        
        # All retries failed
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            print(f"🚨 ALERT: {self.consecutive_failures} consecutive API failures!")
        
        return None
    
    async def check_for_new_lotteries(self):
        """Poll Goldsky subgraph for new lotteries and post them"""
        try:
            # GraphQL query for recent lotteries - INCREASED from 20 to 50
            query = """
            query GetRecentLotteries {
              lotteries(
                first: 50
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
            
            data = await self._fetch_with_retry(query)
            if not data:
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
                
                # NEW LOTTERY FOUND!
                print(f"🆕 New lottery detected: {lottery_id}")
                
                # Transform subgraph data to expected format
                formatted_lottery = self._format_subgraph_data(lottery)
                
                # Post to Discord with rate limiting
                try:
                    await self.post_lottery(formatted_lottery)
                    new_count += 1
                    
                    # Send alert notifications
                    if self.alert_callback:
                        try:
                            lottery_url = f"https://chance.fun/lottery/{lottery_id}"
                            await self.alert_callback(self.bot, lottery, lottery_url)
                        except Exception as alert_error:
                            print(f"⚠️ Alert notification error: {alert_error}")
                    
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
            
            # Log status (only if we checked successfully)
            if new_count > 0:
                print(f"✅ Posted {new_count} new lottery/lotteries")
    
        except Exception as e:
            print(f"❌ Error in check_for_new_lotteries: {e}")
    
    async def check_for_winners(self):
        """Poll Goldsky subgraph for lotteries with winners and announce them"""
        try:
            # Query for lotteries with winners
            query = """
            query GetWinners {
              lotteries(
                first: 50
                orderBy: createdAt
                orderDirection: desc
                where: { hasWinner: true }
              ) {
                id
                prizeProvider
                prizeAmount
                ticketPrice
                pickRange
                winner
                ticketsSold
                createdAt
              }
            }
            """
            
            data = await self._fetch_with_retry(query, max_retries=2)  # Fewer retries for winners
            if not data:
                return
            
            lotteries = data.get('data', {}).get('lotteries', [])
            
            # On first run, just mark winners as seen
            if len(self.posted_winners) == 0 and len(lotteries) > 0:
                for lottery in lotteries:
                    lottery_id = lottery.get('id')
                    if lottery_id:
                        self.posted_winners.add(lottery_id)
                print(f"📝 Marked {len(lotteries)} existing winners as seen")
                return
            
            # Check for new winners
            for lottery in lotteries:
                lottery_id = lottery.get('id')
                
                if lottery_id in self.posted_winners:
                    continue
                
                # Post winner announcement
                try:
                    await self.post_winner(lottery)
                except Exception as e:
                    print(f"❌ Error posting winner {lottery_id}: {e}")
                    continue
                
                # Mark as posted
                self.posted_winners.add(lottery_id)
                
                # Rate limiting
                await asyncio.sleep(1)
            
            # Limit set size
            if len(self.posted_winners) > 10000:
                self.posted_winners = set(list(self.posted_winners)[-5000:])
    
        except Exception as e:
            print(f"❌ Error in check_for_winners: {e}")
    
    async def post_winner(self, lottery: Dict):
        """Post winner announcement to winners channels"""
        
        # Extract data
        lottery_id = lottery.get('id', '')
        winner = lottery.get('winner', 'Unknown')
        prize_wei = int(lottery.get('prizeAmount', 0))
        prize = prize_wei / 1_000_000
        ticket_price_wei = int(lottery.get('ticketPrice', 0))
        ticket_price = ticket_price_wei / 1_000_000
        tickets_sold = int(lottery.get('ticketsSold', 0))
        pick_range = int(lottery.get('pickRange', 0))
        
        # Shorten winner address
        if winner and len(winner) > 10:
            short_winner = f"{winner[:6]}...{winner[-4:]}"
        else:
            short_winner = winner or "Unknown"
        
        # Calculate total pot
        total_pot = tickets_sold * ticket_price
        
        # Lottery URL
        lottery_url = f"https://chance.fun/lottery/{lottery_id}"
        
        # ===== POST TO #RECENT-WINNERS (ALL WINNERS) =====
        winners_channel_id = self.channels.get('winners')
        if winners_channel_id:
            channel = self.bot.get_channel(winners_channel_id)
            if channel:
                # Create standard winner embed
                embed = discord.Embed(
                    title="🎉 WE HAVE A WINNER! 🎉",
                    description=f"Congratulations to our lucky winner!",
                    color=discord.Color.gold()
                )
                
                embed.add_field(
                    name="🏆 Winner",
                    value=f"`{short_winner}`",
                    inline=True
                )
                
                embed.add_field(
                    name="💰 Prize Won",
                    value=f"**${prize:,.2f}** USDC",
                    inline=True
                )
                
                embed.add_field(
                    name="🎫 Winning Odds",
                    value=f"1 in {pick_range:,}",
                    inline=True
                )
                
                embed.add_field(
                    name="📊 Lottery Stats",
                    value=f"🎟️ Tickets Sold: **{tickets_sold:,}**\n💵 Total Pot: **${total_pot:,.2f}**",
                    inline=False
                )
                
                embed.add_field(
                    name="🔗 View Lottery",
                    value=f"[Click Here]({lottery_url})",
                    inline=False
                )
                
                embed.set_footer(text="🍀 Could you be next? Play now at chance.fun!")
                
                await channel.send(embed=embed)
                print(f"🎉 Winner announced: {short_winner} won ${prize:,.2f}")
        
        # ===== POST TO #BIG-WINS ($50K+ ONLY) =====
        if prize >= 50000:
            big_wins_channel_id = self.channels.get('big_wins')
            if big_wins_channel_id:
                big_channel = self.bot.get_channel(big_wins_channel_id)
                if big_channel:
                    # Create special BIG WIN embed
                    big_embed = discord.Embed(
                        title="🚀💰 MASSIVE WIN! 💰🚀",
                        description=f"# ${prize:,.0f} JACKPOT! 🎰",
                        color=discord.Color.from_rgb(255, 215, 0)  # Gold color
                    )
                    
                    big_embed.add_field(
                        name="🏆 Lucky Winner",
                        value=f"`{short_winner}`",
                        inline=True
                    )
                    
                    big_embed.add_field(
                        name="💎 Prize Won",
                        value=f"**${prize:,.2f}** USDC",
                        inline=True
                    )
                    
                    big_embed.add_field(
                        name="🎯 Odds Beaten",
                        value=f"**1 in {pick_range:,}**",
                        inline=True
                    )
                    
                    big_embed.add_field(
                        name="📊 Lottery Stats",
                        value=f"🎟️ Tickets Sold: **{tickets_sold:,}**\n💵 Total Pot: **${total_pot:,.2f}**\n🎫 Ticket Price: **${ticket_price:,.2f}**",
                        inline=False
                    )
                    
                    big_embed.add_field(
                        name="🔗 View Winning Lottery",
                        value=f"[Click Here]({lottery_url})",
                        inline=False
                    )
                    
                    big_embed.set_footer(text="🔥 Big wins happen here! Play now at chance.fun 🔥")
                    
                    await big_channel.send("@everyone 🚨 **HUGE WIN ALERT!** 🚨", embed=big_embed)
                    print(f"🚀 BIG WIN announced: {short_winner} won ${prize:,.2f}!")

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
        lottery_url = f"https://chance.fun/lottery/{lottery_id}" if lottery_id else "https://chance.fun"
        
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
        
        data = await self._fetch_with_retry(query)
        if not data:
            return []
        
        return data.get('data', {}).get('lotteries', [])
    
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
        
        data = await self._fetch_with_retry(query)
        if not data:
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
            timestamp=datetime.now(timezone.utc)
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
            hours = duration
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
    
    async def debug_check_lottery(self, lottery_id: str) -> Optional[Dict]:
        """
        Debug helper: Check if a specific lottery exists in the subgraph
        
        Args:
            lottery_id: The lottery ID to check
            
        Returns:
            Lottery data if found, None otherwise
        """
        query = f"""
        query GetLottery {{
          lottery(id: "{lottery_id}") {{
            id
            status
            prizeAmount
            ticketPrice
            pickRange
            createdAt
            hasWinner
          }}
        }}
        """
        
        data = await self._fetch_with_retry(query)
        if not data:
            return None
        
        lottery = data.get('data', {}).get('lottery')
        if lottery:
            print(f"🔍 Debug - Lottery {lottery_id}:")
            print(f"   Status: {lottery.get('status')}")
            print(f"   Prize: ${int(lottery.get('prizeAmount', 0)) / 1_000_000:.2f}")
            print(f"   In posted set: {lottery_id in self.posted_lotteries}")
        else:
            print(f"🔍 Debug - Lottery {lottery_id} NOT FOUND in subgraph")
        
        return lottery
    
    def get_health_status(self) -> Dict:
        """Get monitor health status"""
        return {
            'is_running': self.is_running,
            'last_successful_poll': self.last_successful_poll.isoformat() if self.last_successful_poll else None,
            'consecutive_failures': self.consecutive_failures,
            'posted_lotteries_count': len(self.posted_lotteries),
            'posted_winners_count': len(self.posted_winners),
        }
