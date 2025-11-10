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

try:
    import nacl
    print("✅ PyNaCl imported successfully")
except ImportError:
    print("📦 Installing PyNaCl...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "PyNaCl==1.5.0"])
    import nacl

# Flask keep-alive for Render
try:
    from flask import Flask
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "🎵 Music Bot Online - Using PyNaCl/Opus"
    
    @app.route('/health')
    def health():
        return "OK"
    
    def run_flask():
        app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)
        
except ImportError:
    print("❌ Flask not available")

# YouTube DL configuration with optimized audio formats
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

# Optimized FFmpeg options for Opus
ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -fflags +genpts',
    'options': '-vn -acodec pcm_s16le -ar 48000 -ac 2 -f s16le'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class OpusAudioSource(discord.AudioSource):
    def __init__(self, source, *, volume=0.5):
        self.source = source
        self.volume = volume
    
    def read(self):
        data = self.source.read()
        if data:
            # Apply volume and return raw PCM data for Opus encoding
            return self._apply_volume(data)
        return b''
    
    def _apply_volume(self, data):
        # Simple volume adjustment for PCM data
        import array
        audio_data = array.array('h', data)
        for i in range(len(audio_data)):
            audio_data[i] = int(audio_data[i] * self.volume)
        return audio_data.tobytes()
    
    def cleanup(self):
        if hasattr(self.source, 'cleanup'):
            self.source.cleanup()

class MusicTrack:
    def __init__(self, data, audio_url):
        self.data = data
        self.audio_url = audio_url
        self.title = data.get('title', 'Unknown Title')
        self.url = data.get('webpage_url', audio_url)
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail', '')
        self.uploader = data.get('uploader', 'Unknown')
        self.views = data.get('view_count', 0)
        self.likes = data.get('like_count', 0)
    
    @classmethod
    async def from_query(cls, query, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        
        def extract_info():
            return ytdl.extract_info(query, download=False)
        
        try:
            data = await loop.run_in_executor(None, extract_info)
            
            if 'entries' in data:
                data = data['entries'][0]
            
            # Get the best audio URL
            audio_url = data['url']
            
            return cls(data, audio_url)
        except Exception as e:
            print(f"Error creating music track: {e}")
            raise e
    
    def create_audio_source(self, volume=0.5):
        """Create an optimized audio source for Discord"""
        source = discord.FFmpegPCMAudio(
            self.audio_url,
            **ffmpeg_options
        )
        
        # Use PyNaCl-powered Opus encoder
        return discord.PCMVolumeTransformer(source, volume=volume)

class AdvancedMusicQueue:
    def __init__(self):
        self._queue = deque()
        self.history = deque(maxlen=20)
        self.loop_mode = 0  # 0: off, 1: track, 2: queue
        self.now_playing = None
    
    def add(self, track):
        self._queue.append(track)
    
    def add_next(self, track):
        self._queue.appendleft(track)
    
    def get_next(self):
        if not self._queue:
            return None
            
        track = self._queue.popleft()
        
        # Handle loop modes
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
    
    def remove(self, index):
        if 0 <= index < len(self._queue):
            return self._queue[index]
        return None
    
    def set_loop(self, mode):
        """Set loop mode: 0=off, 1=track, 2=queue"""
        self.loop_mode = mode
    
    def __len__(self):
        return len(self._queue)
    
    def __getitem__(self, index):
        if 0 <= index < len(self._queue):
            return self._queue[index]
        return None

class EnhancedMusicBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}
        self.autoplay_enabled = True
        self.default_volume = 0.7
    
    def get_queue(self, guild_id):
        if guild_id not in self.queues:
            self.queues[guild_id] = AdvancedMusicQueue()
        return self.queues[guild_id]
    
    async def ensure_voice(self, interaction):
        """Ensure bot is in voice channel"""
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You need to be in a voice channel!", ephemeral=True)
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
                    # Schedule next song
                    asyncio.run_coroutine_threadsafe(self.play_next(interaction), self.bot.loop)
                
                voice_client.play(audio_source, after=after_playing)
                
                # Send now playing embed
                embed = self.create_track_embed(next_track, "🎵 Now Playing")
                asyncio.run_coroutine_threadsafe(
                    interaction.followup.send(embed=embed),
                    self.bot.loop
                )
                
            except Exception as e:
                print(f"Error playing track: {e}")
                asyncio.run_coroutine_threadsafe(
                    interaction.followup.send(f"❌ Error playing track: {e}"),
                    self.bot.loop
                )
        elif self.autoplay_enabled and queue.history:
            await self.autoplay_next(interaction)
        else:
            embed = discord.Embed(
                title="🎶 Queue Finished",
                description="The queue has ended. Add more songs with `/play`",
                color=0x95a5a6
            )
            await interaction.followup.send(embed=embed)
    
    def create_track_embed(self, track, title):
        """Create a beautiful embed for track info"""
        embed = discord.Embed(
            title=title,
            description=f"**[{track.title}]({track.url})**",
            color=0x1abc9c
        )
        
        embed.add_field(name="Uploader", value=track.uploader, inline=True)
        
        if track.duration:
            minutes = track.duration // 60
            seconds = track.duration % 60
            embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
        
        if track.views:
            embed.add_field(name="Views", value=f"{track.views:,}", inline=True)
        
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
        
        embed.set_footer(text=f"Volume: {int(self.default_volume * 100)}% | Autoplay: {'On' if self.autoplay_enabled else 'Off'}")
        
        return embed
    
    async def autoplay_next(self, interaction):
        try:
            queue = self.get_queue(interaction.guild.id)
            if not queue.history:
                return
            
            last_track = queue.history[-1]
            
            # Search for related tracks
            ytdl_search = yt_dlp.YoutubeDL({
                **ytdl_format_options,
                'extract_flat': True,
                'quiet': True
            })
            
            def search_related():
                return ytdl_search.extract_info(f"related:{last_track.url}", download=False)
            
            search_data = await asyncio.get_event_loop().run_in_executor(None, search_related)
            
            if 'entries' in search_data and search_data['entries']:
                # Find a track that's not in recent history
                recent_urls = {track.url for track in list(queue.history)[-5:]}
                for related in search_data['entries'][:10]:
                    if related.get('url') not in recent_urls:
                        try:
                            new_track = await MusicTrack.from_query(related['url'])
                            queue.add(new_track)
                            
                            embed = discord.Embed(
                                title="🔮 Autoplay",
                                description=f"Added **{new_track.title}** to queue",
                                color=0x9b59b6
                            )
                            await interaction.followup.send(embed=embed)
                            
                            # Start playing if not already
                            voice_client = interaction.guild.voice_client
                            if voice_client and not voice_client.is_playing():
                                await self.play_next(interaction)
                            break
                        except Exception:
                            continue
                        
        except Exception as e:
            print(f"Autoplay error: {e}")

    @app_commands.command(name="play", description="Play a song from YouTube")
    @app_commands.describe(query="Song name, artist, or YouTube URL")
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
                embed = self.create_track_embed(track, "✅ Added to Queue")
                embed.add_field(name="Position", value=f"#{len(queue)}", inline=True)
                await interaction.followup.send(embed=embed)
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ No music is playing!", ephemeral=True)
            return
        
        voice_client.stop()
        await interaction.response.send_message("⏭️ Skipped to next track!")

    @app_commands.command(name="stop", description="Stop music and clear queue")
    async def stop(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        queue = self.get_queue(interaction.guild.id)
        
        if voice_client:
            voice_client.stop()
        queue.clear()
        
        await interaction.response.send_message("🛑 Stopped playback and cleared queue!")

    @app_commands.command(name="queue", description="Show current music queue")
    async def show_queue(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        
        if len(queue) == 0 and not queue.now_playing:
            await interaction.response.send_message("📭 Queue is empty!", ephemeral=True)
            return
        
        embed = discord.Embed(title="🎵 Music Queue", color=0x3498db)
        
        # Current track
        if queue.now_playing:
            embed.add_field(
                name="Now Playing",
                value=f"▶️ **{queue.now_playing.title}**",
                inline=False
            )
        
        # Upcoming tracks
        if len(queue) > 0:
            queue_text = ""
            for i, track in enumerate(queue[:10], 1):
                duration = f" ({track.duration//60}:{track.duration%60:02d})" if track.duration else ""
                queue_text += f"`{i}.` {track.title}{duration}\n"
            
            if len(queue) > 10:
                queue_text += f"\n...and {len(queue) - 10} more tracks"
            
            embed.add_field(name="Upcoming", value=queue_text, inline=False)
        
        # Queue info
        loop_modes = ["Off", "Track", "Queue"]
        embed.add_field(
            name="Queue Info",
            value=f"• Tracks: {len(queue)}\n• Loop: {loop_modes[queue.loop_mode]}\n• Autoplay: {'On' if self.autoplay_enabled else 'Off'}",
            inline=True
        )
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pause", description="Pause current song")
    async def pause(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ No music is playing!", ephemeral=True)
            return
        
        voice_client.pause()
        await interaction.response.send_message("⏸️ Playback paused!")

    @app_commands.command(name="resume", description="Resume paused song")
    async def resume(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_paused():
            await interaction.response.send_message("❌ No music is paused!", ephemeral=True)
            return
        
        voice_client.resume()
        await interaction.response.send_message("▶️ Playback resumed!")

    @app_commands.command(name="volume", description="Adjust bot volume (1-100)")
    @app_commands.describe(level="Volume level from 1 to 100")
    async def volume(self, interaction: discord.Interaction, level: int):
        if not 1 <= level <= 100:
            await interaction.response.send_message("❌ Volume must be between 1 and 100!", ephemeral=True)
            return
        
        self.default_volume = level / 100
        
        # Adjust current playback volume if any
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_playing() and hasattr(voice_client.source, 'volume'):
            voice_client.source.volume = self.default_volume
        
        await interaction.response.send_message(f"🔊 Volume set to {level}%")

    @app_commands.command(name="loop", description="Set loop mode")
    @app_commands.describe(mode="Loop mode: off, track, or queue")
    @app_commands.choices(mode=[
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="track", value="track"),
        app_commands.Choice(name="queue", value="queue")
    ])
    async def loop(self, interaction: discord.Interaction, mode: str):
        queue = self.get_queue(interaction.guild.id)
        
        mode_map = {"off": 0, "track": 1, "queue": 2}
        queue.set_loop(mode_map[mode])
        
        mode_names = {"off": "Off", "track": "Track", "queue": "Queue"}
        await interaction.response.send_message(f"🔁 Loop mode set to: **{mode_names[mode]}**")

    @app_commands.command(name="autoplay", description="Toggle autoplay feature")
    async def autoplay_cmd(self, interaction: discord.Interaction):
        self.autoplay_enabled = not self.autoplay_enabled
        status = "enabled" if self.autoplay_enabled else "disabled"
        await interaction.response.send_message(f"🔮 Autoplay **{status}**!")

    @app_commands.command(name="disconnect", description="Disconnect bot from voice")
    async def disconnect(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client:
            await interaction.response.send_message("❌ I'm not in a voice channel!", ephemeral=True)
            return
        
        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        
        await voice_client.disconnect()
        await interaction.response.send_message("👋 Disconnected from voice channel!")

    @app_commands.command(name="nowplaying", description="Show currently playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        voice_client = interaction.guild.voice_client
        
        if not queue.now_playing or not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ No music is playing!", ephemeral=True)
            return
        
        embed = self.create_track_embed(queue.now_playing, "🎵 Now Playing")
        
        # Add playback status
        if voice_client.is_paused():
            status = "⏸️ Paused"
        else:
            status = "▶️ Playing"
        
        embed.add_field(name="Status", value=status, inline=True)
        
        await interaction.response.send_message(embed=embed)

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
    
    async def setup_hook(self):
        await self.add_cog(EnhancedMusicBot(self))
        try:
            synced = await self.tree.sync()
            print(f"✅ Synced {len(synced)} command(s)")
        except Exception as e:
            print(f"❌ Error syncing commands: {e}")
    
    async def on_ready(self):
        print(f'✅ Logged in as {self.user.name} ({self.user.id})')
        print(f'📍 Connected to {len(self.guilds)} guild(s)')
        print('🎵 Enhanced Music Bot is ready!')
        print('💎 Using PyNaCl/Opus for superior audio quality')

def main():
    # Start Flask keep-alive if available
    try:
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print("🌐 Flask keep-alive server started on port 8080")
    except Exception as e:
        print(f"⚠️  Running without Flask: {e}")
    
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
