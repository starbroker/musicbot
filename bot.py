import os
import asyncio
import threading
from collections import deque
import subprocess
import sys

# Install dependencies if missing
try:
    import discord
    from discord import app_commands
    from discord.ext import commands
    print("✅ discord.py imported successfully")
except ImportError:
    print("📦 Installing discord.py...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "discord.py[voice]==2.3.2"])
    import discord
    from discord import app_commands
    from discord.ext import commands

try:
    import yt_dlp
    print("✅ yt-dlp imported successfully")
except ImportError:
    print("📦 Installing yt-dlp...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp==2023.11.16"])
    import yt_dlp

# Flask keep-alive
try:
    from flask import Flask
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "🎵 Music Bot Online"
    
    def run_flask():
        app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)
        
except ImportError:
    def run_flask():
        pass

# YouTube DL configuration
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class MusicSource:
    def __init__(self, source, data):
        self.source = source
        self.data = data
        self.title = data.get('title', 'Unknown Title')
        self.url = data.get('webpage_url', '')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail', '')
        self.uploader = data.get('uploader', 'Unknown')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        
        def extract_info():
            return ytdl.extract_info(url, download=False)
        
        data = await loop.run_in_executor(None, extract_info)
        
        if 'entries' in data:
            data = data['entries'][0]
        
        audio_url = data['url']
        source = discord.FFmpegPCMAudio(audio_url, **ffmpeg_options)
        source = discord.PCMVolumeTransformer(source, volume=0.7)
        
        return cls(source, data)

class MusicQueue:
    def __init__(self):
        self._queue = deque()
        self.history = deque(maxlen=10)
        self.loop = False
        self.loop_queue = False
        self.now_playing = None
    
    def add(self, item):
        self._queue.append(item)
    
    def get(self):
        if not self._queue:
            return None
            
        item = self._queue.popleft()
        self.now_playing = item
        
        if not self.loop_queue:
            self.history.append(item)
            
        return item
    
    def clear(self):
        self._queue.clear()
        self.now_playing = None
    
    def __len__(self):
        return len(self._queue)
    
    def __getitem__(self, index):
        if 0 <= index < len(self._queue):
            return self._queue[index]
        return None

class MusicBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}
        self.autoplay_enabled = True
    
    def get_queue(self, guild_id):
        if guild_id not in self.queues:
            self.queues[guild_id] = MusicQueue()
        return self.queues[guild_id]
    
    async def play_next(self, interaction):
        guild_id = interaction.guild.id
        queue = self.get_queue(guild_id)
        voice_client = interaction.guild.voice_client
        
        if not voice_client:
            return
        
        # Handle loop modes
        if queue.loop and queue.now_playing:
            next_song = queue.now_playing
        else:
            next_song = queue.get()
        
        if next_song:
            try:
                def after_playing(error):
                    if error:
                        print(f'Playback error: {error}')
                    asyncio.run_coroutine_threadsafe(self.play_next(interaction), self.bot.loop)
                
                voice_client.play(next_song.source, after=after_playing)
                
                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{next_song.title}**",
                    color=0x00ff00
                )
                embed.add_field(name="Uploader", value=next_song.uploader, inline=True)
                
                if next_song.duration:
                    minutes = next_song.duration // 60
                    seconds = next_song.duration % 60
                    embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
                
                if next_song.thumbnail:
                    embed.set_thumbnail(url=next_song.thumbnail)
                
                await interaction.followup.send(embed=embed)
                
            except Exception as e:
                await interaction.followup.send(f"❌ Error playing song: {e}")
        elif self.autoplay_enabled and queue.history:
            await self.autoplay_next(interaction)
        else:
            await interaction.followup.send("🎶 Queue finished! Use `/play` to add more songs")
    
    async def autoplay_next(self, interaction):
        try:
            queue = self.get_queue(interaction.guild.id)
            if not queue.history:
                return
            
            last_song = queue.history[-1]
            
            ytdl_search = yt_dlp.YoutubeDL({
                **ytdl_format_options,
                'extract_flat': True,
                'quiet': True
            })
            
            def search_related():
                return ytdl_search.extract_info(f"related:{last_song.url}", download=False)
            
            search_data = await asyncio.get_event_loop().run_in_executor(None, search_related)
            
            if 'entries' in search_data and search_data['entries']:
                recent_urls = {song.url for song in list(queue.history)[-3:]}
                for related in search_data['entries'][:3]:
                    if related.get('url') not in recent_urls:
                        new_song = await MusicSource.from_url(related['url'])
                        queue.add(new_song)
                        
                        await interaction.followup.send(f"🔮 Autoplay added: **{new_song.title}**")
                        
                        voice_client = interaction.guild.voice_client
                        if voice_client and not voice_client.is_playing():
                            await self.play_next(interaction)
                        break
                        
        except Exception as e:
            print(f"Autoplay error: {e}")

    @app_commands.command(name="play", description="Play a song from YouTube")
    @app_commands.describe(query="Song name or YouTube URL")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        
        if not interaction.user.voice:
            await interaction.followup.send("❌ You need to be in a voice channel!")
            return
        
        voice_channel = interaction.user.voice.channel
        
        # Connect to voice channel
        if interaction.guild.voice_client is None:
            await voice_channel.connect()
        elif interaction.guild.voice_client.channel != voice_channel:
            await interaction.guild.voice_client.move_to(voice_channel)
        
        try:
            # Get the song
            song = await MusicSource.from_url(query)
            
            # Add to queue
            queue = self.get_queue(interaction.guild.id)
            queue.add(song)
            
            voice_client = interaction.guild.voice_client
            
            if not voice_client.is_playing():
                await self.play_next(interaction)
            else:
                await interaction.followup.send(f"✅ Added to queue: **{song.title}** (#{len(queue)})")
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ No music is playing!")
            return
        
        voice_client.stop()
        await interaction.response.send_message("⏭️ Skipped!")

    @app_commands.command(name="stop", description="Stop music and clear queue")
    async def stop(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        queue = self.get_queue(interaction.guild.id)
        
        if voice_client:
            voice_client.stop()
        queue.clear()
        
        await interaction.response.send_message("🛑 Stopped and cleared queue!")

    @app_commands.command(name="queue", description="Show current queue")
    async def show_queue(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        
        if len(queue) == 0 and not queue.now_playing:
            await interaction.response.send_message("📭 Queue is empty!")
            return
        
        embed = discord.Embed(title="🎵 Music Queue", color=0x3498db)
        
        if queue.now_playing:
            embed.add_field(
                name="Now Playing",
                value=f"▶️ **{queue.now_playing.title}**",
                inline=False
            )
        
        if len(queue) > 0:
            queue_text = ""
            for i, song in enumerate(queue[:5], 1):
                duration = f" ({song.duration//60}:{song.duration%60:02d})" if song.duration else ""
                queue_text += f"`{i}.` {song.title}{duration}\n"
            
            if len(queue) > 5:
                queue_text += f"\n...and {len(queue) - 5} more songs"
            
            embed.add_field(name="Upcoming", value=queue_text, inline=False)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pause", description="Pause current song")
    async def pause(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ No music is playing!")
            return
        
        voice_client.pause()
        await interaction.response.send_message("⏸️ Paused!")

    @app_commands.command(name="resume", description="Resume paused song")
    async def resume(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_paused():
            await interaction.response.send_message("❌ No music is paused!")
            return
        
        voice_client.resume()
        await interaction.response.send_message("▶️ Resumed!")

    @app_commands.command(name="disconnect", description="Disconnect bot from voice")
    async def disconnect(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client:
            await interaction.response.send_message("❌ I'm not in a voice channel!")
            return
        
        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        
        await voice_client.disconnect()
        await interaction.response.send_message("👋 Disconnected!")

    @app_commands.command(name="autoplay", description="Toggle autoplay")
    async def autoplay_cmd(self, interaction: discord.Interaction):
        self.autoplay_enabled = not self.autoplay_enabled
        status = "enabled" if self.autoplay_enabled else "disabled"
        await interaction.response.send_message(f"🔮 Autoplay **{status}**!")

    @app_commands.command(name="nowplaying", description="Show current song")
    async def nowplaying(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        voice_client = interaction.guild.voice_client
        
        if not queue.now_playing or not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ No music is playing!")
            return
        
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{queue.now_playing.title}**",
            color=0x00ff00
        )
        embed.add_field(name="Uploader", value=queue.now_playing.uploader, inline=True)
        
        if queue.now_playing.duration:
            minutes = queue.now_playing.duration // 60
            seconds = queue.now_playing.duration % 60
            embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
        
        await interaction.response.send_message(embed=embed)

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
    
    async def setup_hook(self):
        await self.add_cog(MusicBot(self))
        try:
            synced = await self.tree.sync()
            print(f"✅ Synced {len(synced)} commands")
        except Exception as e:
            print(f"❌ Command sync error: {e}")
    
    async def on_ready(self):
        print(f'✅ Logged in as {self.user.name}')
        print(f'📍 Connected to {len(self.guilds)} guilds')
        print('🎵 Music Bot Ready with Voice Features!')

def main():
    # Start keep-alive server
    try:
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print("🌐 Keep-alive server started")
    except:
        print("⚠️  No keep-alive server")
    
    # Get bot token
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        print("❌ ERROR: No bot token found!")
        print("💡 Set DISCORD_BOT_TOKEN environment variable")
        sys.exit(1)
    
    # Run bot
    bot = Bot()
    try:
        bot.run(token)
    except Exception as e:
        print(f"❌ Bot error: {e}")

if __name__ == "__main__":
    main()
