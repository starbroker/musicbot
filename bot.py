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
    subprocess.check_call([sys.executable, "-m", "pip", "install", "discord.py==2.3.2"])
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

# Simple Flask keep-alive
try:
    from flask import Flask
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "🎵 Music Bot Online"
    
    @app.route('/health')
    def health():
        return "OK"
    
    def run_flask():
        app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)
        
except ImportError:
    print("❌ Flask not available, running without web server")

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
    def __init__(self, data, source):
        self.data = data
        self.source = source
        self.title = data.get('title', 'Unknown Title')
        self.url = data.get('url', '')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail', '')
        self.uploader = data.get('uploader', 'Unknown')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        
        def extract_info():
            return ytdl.extract_info(url, download=False)
        
        try:
            data = await loop.run_in_executor(None, extract_info)
            
            if 'entries' in data:
                data = data['entries'][0]
            
            # Get the audio URL
            audio_url = data['url']
            
            # Create FFmpeg audio source directly
            source = discord.FFmpegPCMAudio(
                audio_url,
                **ffmpeg_options
            )
            
            # Apply volume
            source = discord.PCMVolumeTransformer(source, volume=0.7)
            
            return cls(data, source)
        except Exception as e:
            print(f"Error creating music source: {e}")
            raise e

class MusicQueue:
    def __init__(self):
        self._queue = deque()
        self.history = deque(maxlen=10)
        self.loop = False
        self.loop_queue = False
    
    def add(self, item):
        self._queue.append(item)
    
    def get(self):
        if not self._queue:
            return None
            
        item = self._queue.popleft()
        if not self.loop_queue:
            self.history.append(item)
        return item
    
    def clear(self):
        self._queue.clear()
    
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
        if queue.loop and queue.history:
            next_song = queue.history[-1]
        elif queue.loop_queue and queue.history:
            queue._queue.extend(queue.history)
            next_song = queue.get()
        else:
            next_song = queue.get()
        
        if next_song:
            try:
                def after_playing(error):
                    if error:
                        print(f'Playback error: {error}')
                    # Schedule next song
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
                
                asyncio.run_coroutine_threadsafe(
                    interaction.followup.send(embed=embed),
                    self.bot.loop
                )
                
            except Exception as e:
                print(f"Error playing song: {e}")
                asyncio.run_coroutine_threadsafe(
                    interaction.followup.send(f"❌ Error playing song: {e}"),
                    self.bot.loop
                )
        elif self.autoplay_enabled and queue.history:
            await self.autoplay_next(interaction)
        else:
            await interaction.followup.send("🎶 Queue finished! Add more songs with `/play`")
    
    async def autoplay_next(self, interaction):
        try:
            queue = self.get_queue(interaction.guild.id)
            if not queue.history:
                return
            
            last_song = queue.history[-1]
            search_query = f"related:{last_song.url}"
            
            ytdl_search = yt_dlp.YoutubeDL({
                **ytdl_format_options,
                'extract_flat': True,
                'quiet': True
            })
            
            def search_related():
                return ytdl_search.extract_info(search_query, download=False)
            
            search_data = await asyncio.get_event_loop().run_in_executor(None, search_related)
            
            if 'entries' in search_data and search_data['entries']:
                related_song = search_data['entries'][0]
                if related_song:
                    song = await MusicSource.from_url(related_song['url'])
                    queue.add(song)
                    
                    embed = discord.Embed(
                        title="🔮 Autoplay",
                        description=f"Added **{song.title}** to queue",
                        color=0x9370DB
                    )
                    await interaction.followup.send(embed=embed)
                    
                    voice_client = interaction.guild.voice_client
                    if voice_client and not voice_client.is_playing():
                        await self.play_next(interaction)
                        
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
                embed = discord.Embed(
                    title="✅ Added to Queue",
                    description=f"**{song.title}**",
                    color=0x00ff00
                )
                embed.add_field(name="Position", value=f"#{len(queue)}", inline=True)
                await interaction.followup.send(embed=embed)
                
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
        
        if len(queue) == 0:
            await interaction.response.send_message("📭 Queue is empty!")
            return
        
        embed = discord.Embed(title="🎵 Music Queue", color=0x0099ff)
        
        # Show next 10 songs
        queue_text = ""
        for i, song in enumerate(queue[:10], 1):
            duration = ""
            if song.duration:
                min = song.duration // 60
                sec = song.duration % 60
                duration = f" ({min}:{sec:02d})"
            queue_text += f"`{i}.` {song.title}{duration}\n"
        
        if len(queue) > 10:
            queue_text += f"\n...and {len(queue) - 10} more songs"
        
        embed.add_field(name="Upcoming", value=queue_text, inline=False)
        embed.set_footer(text=f"Total: {len(queue)} songs | Autoplay: {'On' if self.autoplay_enabled else 'Off'}")
        
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
        await interaction.response.send_message(f"🔮 Autoplay {status}!")

    @app_commands.command(name="nowplaying", description="Show current song")
    async def nowplaying(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        queue = self.get_queue(interaction.guild.id)
        
        if not voice_client or not voice_client.is_playing() or not queue.history:
            await interaction.response.send_message("❌ No music is playing!")
            return
        
        current_song = queue.history[-1]
        
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{current_song.title}**",
            color=0x00ff00
        )
        embed.add_field(name="Uploader", value=current_song.uploader, inline=True)
        
        if current_song.duration:
            minutes = current_song.duration // 60
            seconds = current_song.duration % 60
            embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
        
        if current_song.thumbnail:
            embed.set_thumbnail(url=current_song.thumbnail)
        
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
            print(f"✅ Synced {len(synced)} command(s)")
        except Exception as e:
            print(f"❌ Error syncing commands: {e}")
    
    async def on_ready(self):
        print(f'✅ Logged in as {self.user.name}')
        print(f'📍 Connected to {len(self.guilds)} guild(s)')
        print('🎵 Music Bot is ready!')
        print('Available commands: /play, /skip, /stop, /pause, /resume, /queue, /disconnect, /autoplay, /nowplaying')

def main():
    # Start Flask keep-alive if available
    try:
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print("🌐 Flask keep-alive server started")
    except:
        print("⚠️  Running without Flask keep-alive")
    
    # Get bot token
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        print("❌ ERROR: DISCORD_BOT_TOKEN environment variable not set!")
        print("💡 Set it in Render.com environment variables")
        sys.exit(1)
    
    # Create and run bot
    bot = Bot()
    
    try:
        bot.run(token)
    except discord.LoginFailure:
        print("❌ Invalid bot token!")
    except Exception as e:
        print(f"❌ Bot error: {e}")

if __name__ == "__main__":
    main()
