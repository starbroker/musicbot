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
    def run_flask():
        pass  # No Flask available

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

class MusicTrack:
    def __init__(self, data, audio_url):
        self.data = data
        self.audio_url = audio_url
        self.title = data.get('title', 'Unknown Title')
        self.url = data.get('webpage_url', audio_url)
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail', '')
        self.uploader = data.get('uploader', 'Unknown')
    
    @classmethod
    async def from_query(cls, query, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        
        def extract_info():
            return ytdl.extract_info(query, download=False)
        
        data = await loop.run_in_executor(None, extract_info)
        
        if 'entries' in data:
            data = data['entries'][0]
        
        audio_url = data['url']
        return cls(data, audio_url)
    
    def create_audio_source(self, volume=0.5):
        source = discord.FFmpegPCMAudio(
            self.audio_url,
            **ffmpeg_options
        )
        return discord.PCMVolumeTransformer(source, volume=volume)

class MusicQueue:
    def __init__(self):
        self._queue = deque()
        self.history = deque(maxlen=10)
        self.loop_mode = 0
        self.now_playing = None
    
    def add(self, track):
        self._queue.append(track)
    
    def get_next(self):
        if not self._queue:
            return None
            
        track = self._queue.popleft()
        
        if self.loop_mode == 1 and self.now_playing:
            self._queue.appendleft(track)
            return self.now_playing
        elif self.loop_mode == 2 and track:
            self._queue.append(track)
        
        self.now_playing = track
        if track and self.loop_mode != 1:
            self.history.append(track)
        return track
    
    def clear(self):
        self._queue.clear()
        self.now_playing = None
    
    def set_loop(self, mode):
        self.loop_mode = mode
    
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
        self.default_volume = 0.7
    
    def get_queue(self, guild_id):
        if guild_id not in self.queues:
            self.queues[guild_id] = MusicQueue()
        return self.queues[guild_id]
    
    async def ensure_voice(self, interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ Join a voice channel first!", ephemeral=True)
            return False
        
        voice_client = interaction.guild.voice_client
        
        if not voice_client:
            await interaction.user.voice.channel.connect()
        elif voice_client.channel != interaction.user.voice.channel:
            await voice_client.move_to(interaction.user.voice.channel)
        
        return True
    
    async def play_next(self, interaction):
        guild_id = interaction.guild.id
        queue = self.get_queue(guild_id)
        voice_client = interaction.guild.voice_client
        
        if not voice_client:
            return
        
        next_track = queue.get_next()
        
        if next_track:
            try:
                audio_source = next_track.create_audio_source(volume=self.default_volume)
                
                def after_playing(error):
                    if error:
                        print(f'Playback error: {error}')
                    asyncio.run_coroutine_threadsafe(self.play_next(interaction), self.bot.loop)
                
                voice_client.play(audio_source, after=after_playing)
                
                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{next_track.title}**",
                    color=0x00ff00
                )
                embed.add_field(name="Uploader", value=next_track.uploader, inline=True)
                
                if next_track.duration:
                    minutes = next_track.duration // 60
                    seconds = next_track.duration % 60
                    embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
                
                if next_track.thumbnail:
                    embed.set_thumbnail(url=next_track.thumbnail)
                
                await interaction.followup.send(embed=embed)
                
            except Exception as e:
                await interaction.followup.send(f"❌ Error playing: {e}")
        elif self.autoplay_enabled and queue.history:
            await self.autoplay_next(interaction)
        else:
            await interaction.followup.send("🎶 Queue finished! Use `/play` to add more songs")
    
    async def autoplay_next(self, interaction):
        try:
            queue = self.get_queue(interaction.guild.id)
            if not queue.history:
                return
            
            last_track = queue.history[-1]
            
            ytdl_search = yt_dlp.YoutubeDL({
                **ytdl_format_options,
                'extract_flat': True,
                'quiet': True
            })
            
            def search_related():
                return ytdl_search.extract_info(f"related:{last_track.url}", download=False)
            
            search_data = await asyncio.get_event_loop().run_in_executor(None, search_related)
            
            if 'entries' in search_data and search_data['entries']:
                recent_urls = {track.url for track in list(queue.history)[-3:]}
                for related in search_data['entries'][:3]:
                    if related.get('url') not in recent_urls:
                        new_track = await MusicTrack.from_query(related['url'])
                        queue.add(new_track)
                        
                        await interaction.followup.send(f"🔮 Autoplay added: **{new_track.title}**")
                        
                        voice_client = interaction.guild.voice_client
                        if voice_client and not voice_client.is_playing():
                            await self.play_next(interaction)
                        break
                        
        except Exception as e:
            print(f"Autoplay error: {e}")

    @app_commands.command(name="play", description="Play a song from YouTube")
    @app_commands.describe(query="Song name or YouTube URL")
    async def play(self, interaction: discord.Interaction, query: str):
        if not await self.ensure_voice(interaction):
            return
        
        await interaction.response.defer()
        
        try:
            track = await MusicTrack.from_query(query)
            queue = self.get_queue(interaction.guild.id)
            queue.add(track)
            
            voice_client = interaction.guild.voice_client
            
            if not voice_client.is_playing():
                await self.play_next(interaction)
            else:
                await interaction.followup.send(f"✅ Added to queue: **{track.title}** (#{len(queue)})")
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="skip", description="Skip current song")
    async def skip(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ No music playing!", ephemeral=True)
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
            await interaction.response.send_message("📭 Queue is empty!", ephemeral=True)
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
            for i, track in enumerate(queue[:5], 1):
                duration = f" ({track.duration//60}:{track.duration%60:02d})" if track.duration else ""
                queue_text += f"`{i}.` {track.title}{duration}\n"
            
            if len(queue) > 5:
                queue_text += f"\n...and {len(queue) - 5} more"
            
            embed.add_field(name="Upcoming", value=queue_text, inline=False)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pause", description="Pause current song")
    async def pause(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ No music playing!", ephemeral=True)
            return
        
        voice_client.pause()
        await interaction.response.send_message("⏸️ Paused!")

    @app_commands.command(name="resume", description="Resume paused song")
    async def resume(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_paused():
            await interaction.response.send_message("❌ No music paused!", ephemeral=True)
            return
        
        voice_client.resume()
        await interaction.response.send_message("▶️ Resumed!")

    @app_commands.command(name="volume", description="Adjust volume (1-100)")
    @app_commands.describe(level="Volume level from 1 to 100")
    async def volume(self, interaction: discord.Interaction, level: int):
        if not 1 <= level <= 100:
            await interaction.response.send_message("❌ Volume must be 1-100!", ephemeral=True)
            return
        
        self.default_volume = level / 100
        
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_playing() and hasattr(voice_client.source, 'volume'):
            voice_client.source.volume = self.default_volume
        
        await interaction.response.send_message(f"🔊 Volume set to {level}%")

    @app_commands.command(name="loop", description="Set loop mode")
    @app_commands.describe(mode="Loop mode")
    @app_commands.choices(mode=[
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="track", value="track"),
        app_commands.Choice(name="queue", value="queue")
    ])
    async def loop(self, interaction: discord.Interaction, mode: str):
        queue = self.get_queue(interaction.guild.id)
        mode_map = {"off": 0, "track": 1, "queue": 2}
        queue.set_loop(mode_map[mode])
        await interaction.response.send_message(f"🔁 Loop: **{mode}**")

    @app_commands.command(name="autoplay", description="Toggle autoplay")
    async def autoplay_cmd(self, interaction: discord.Interaction):
        self.autoplay_enabled = not self.autoplay_enabled
        status = "enabled" if self.autoplay_enabled else "disabled"
        await interaction.response.send_message(f"🔮 Autoplay **{status}**!")

    @app_commands.command(name="disconnect", description="Disconnect bot")
    async def disconnect(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client:
            await interaction.response.send_message("❌ Not in voice channel!", ephemeral=True)
            return
        
        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        await voice_client.disconnect()
        await interaction.response.send_message("👋 Disconnected!")

    @app_commands.command(name="nowplaying", description="Show current song")
    async def nowplaying(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        voice_client = interaction.guild.voice_client
        
        if not queue.now_playing or not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ No music playing!", ephemeral=True)
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
        print('🎵 Music Bot Ready!')

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
