import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
import os
from collections import deque

# Configure yt-dlp options
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
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            data = data['entries'][0]
        
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class MusicQueue:
    def __init__(self):
        self._queue = deque()
        self.history = deque(maxlen=50)
        self.loop = False
        self.loop_queue = False
    
    def add(self, item):
        self._queue.append(item)
    
    def add_next(self, item):
        self._queue.appendleft(item)
    
    def get(self):
        if self._queue:
            item = self._queue.popleft()
            if not self.loop_queue:
                self.history.append(item)
            return item
        return None
    
    def clear(self):
        self._queue.clear()
    
    def remove(self, index):
        if 0 <= index < len(self._queue):
            return self._queue.remove(self._queue[index])
        raise IndexError("Queue index out of range")
    
    def __len__(self):
        return len(self._queue)
    
    def __getitem__(self, index):
        return self._queue[index]

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
        
        if voice_client is None:
            return
        
        if queue.loop and queue.history:
            next_song = queue.history[-1]
        elif queue.loop_queue and queue.history:
            queue._queue.extend(queue.history)
            next_song = queue.get()
        else:
            next_song = queue.get()
        
        if next_song:
            try:
                voice_client.play(next_song, after=lambda e: asyncio.run_coroutine_threadsafe(self.play_next(interaction), self.bot.loop))
                
                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**[{next_song.title}]({next_song.url})**",
                    color=discord.Color.blue()
                )
                if next_song.thumbnail:
                    embed.set_thumbnail(url=next_song.thumbnail)
                if next_song.duration:
                    minutes, seconds = divmod(next_song.duration, 60)
                    embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
                
                await interaction.followup.send(embed=embed)
                
            except Exception as e:
                await interaction.followup.send(f"Error playing song: {e}")
        elif self.autoplay_enabled and queue.history:
            await self.autoplay_next(interaction)
        else:
            await interaction.followup.send("Queue is empty! Use `/play` to add more songs.")
    
    async def autoplay_next(self, interaction):
        guild_id = interaction.guild.id
        queue = self.get_queue(guild_id)
        
        if not queue.history:
            return
        
        last_song = queue.history[-1]
        
        try:
            search_query = f"related:{last_song.url}"
            
            ytdl_search = yt_dlp.YoutubeDL({
                **ytdl_format_options,
                'extract_flat': True,
                'quiet': True
            })
            
            loop = asyncio.get_event_loop()
            search_data = await loop.run_in_executor(
                None, 
                lambda: ytdl_search.extract_info(search_query, download=False)
            )
            
            if 'entries' in search_data and search_data['entries']:
                available_songs = [entry for entry in search_data['entries'][:5] 
                                 if entry.get('url') not in [s.url for s in queue.history]]
                
                if available_songs:
                    next_url = available_songs[0]['url']
                    player = await YTDLSource.from_url(next_url, loop=self.bot.loop, stream=True)
                    queue.add(player)
                    
                    embed = discord.Embed(
                        title="🔮 Autoplay",
                        description=f"Added **[{player.title}]({player.url})** to queue",
                        color=discord.Color.purple()
                    )
                    await interaction.followup.send(embed=embed)
                    
                    voice_client = interaction.guild.voice_client
                    if voice_client and not voice_client.is_playing():
                        await self.play_next(interaction)
        
        except Exception as e:
            print(f"Autoplay error: {e}")

    @app_commands.command(name="play", description="Play a song from YouTube")
    @app_commands.describe(query="The song name or YouTube URL to play")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        
        if not interaction.user.voice:
            await interaction.followup.send("You need to be in a voice channel to play music!")
            return
        
        voice_channel = interaction.user.voice.channel
        
        if interaction.guild.voice_client is None:
            await voice_channel.connect()
        elif interaction.guild.voice_client.channel != voice_channel:
            await interaction.guild.voice_client.move_to(voice_channel)
        
        try:
            player = await YTDLSource.from_url(query, loop=self.bot.loop, stream=True)
            
            queue = self.get_queue(interaction.guild.id)
            queue.add(player)
            
            voice_client = interaction.guild.voice_client
            if not voice_client.is_playing():
                await self.play_next(interaction)
            else:
                embed = discord.Embed(
                    title="✅ Added to Queue",
                    description=f"**[{player.title}]({player.url})**",
                    color=discord.Color.green()
                )
                if player.thumbnail:
                    embed.set_thumbnail(url=player.thumbnail)
                embed.add_field(name="Position in queue", value=f"#{len(queue)}", inline=True)
                await interaction.followup.send(embed=embed)
                
        except Exception as e:
            await interaction.followup.send(f"Error: {str(e)}")

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            await interaction.followup.send("No music is currently playing!")
            return
        
        voice_client.stop()
        await interaction.followup.send("⏭️ Skipped current song")

    @app_commands.command(name="queue", description="Show the current queue")
    async def show_queue(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        
        if len(queue) == 0 and not queue.history:
            await interaction.response.send_message("Queue is empty!")
            return
        
        embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.blue())
        
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_playing() and queue.history:
            current_song = queue.history[-1]
            embed.add_field(
                name="Now Playing",
                value=f"[{current_song.title}]({current_song.url})",
                inline=False
            )
        
        if len(queue) > 0:
            queue_text = ""
            for i, song in enumerate(queue[:10], 1):
                queue_text += f"{i}. [{song.title}]({song.url})\n"
            
            if len(queue) > 10:
                queue_text += f"\n...and {len(queue) - 10} more songs"
            
            embed.add_field(name="Upcoming", value=queue_text, inline=False)
        
        embed.set_footer(text=f"Total songs in queue: {len(queue)} | Autoplay: {'On' if self.autoplay_enabled else 'Off'}")
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("No music is currently playing!")
            return
        
        voice_client.pause()
        await interaction.response.send_message("⏸️ Paused")

    @app_commands.command(name="resume", description="Resume the current song")
    async def resume(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_paused():
            await interaction.response.send_message("No music is paused!")
            return
        
        voice_client.resume()
        await interaction.response.send_message("▶️ Resumed")

    @app_commands.command(name="stop", description="Stop the music and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        queue = self.get_queue(interaction.guild.id)
        
        if voice_client:
            voice_client.stop()
        
        queue.clear()
        await interaction.response.send_message("🛑 Stopped music and cleared queue")

    @app_commands.command(name="loop", description="Toggle looping for current song or queue")
    @app_commands.describe(mode="What to loop: song, queue, or off")
    @app_commands.choices(mode=[
        app_commands.Choice(name="song", value="song"),
        app_commands.Choice(name="queue", value="queue"),
        app_commands.Choice(name="off", value="off")
    ])
    async def loop(self, interaction: discord.Interaction, mode: str):
        queue = self.get_queue(interaction.guild.id)
        
        if mode == "song":
            queue.loop = True
            queue.loop_queue = False
            await interaction.response.send_message("🔂 Looping current song")
        elif mode == "queue":
            queue.loop = False
            queue.loop_queue = True
            await interaction.response.send_message("🔁 Looping queue")
        else:
            queue.loop = False
            queue.loop_queue = False
            await interaction.response.send_message("➡️ Loop disabled")

    @app_commands.command(name="autoplay", description="Toggle autoplay feature")
    async def autoplay(self, interaction: discord.Interaction):
        self.autoplay_enabled = not self.autoplay_enabled
        status = "enabled" if self.autoplay_enabled else "disabled"
        await interaction.response.send_message(f"🔮 Autoplay {status}")

    @app_commands.command(name="volume", description="Adjust the volume (0-100)")
    @app_commands.describe(level="Volume level (0-100)")
    async def volume(self, interaction: discord.Interaction, level: int):
        voice_client = interaction.guild.voice_client
        
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("No music is currently playing!")
            return
        
        if not 0 <= level <= 100:
            await interaction.response.send_message("Volume must be between 0 and 100!")
            return
        
        volume = level / 100
        if hasattr(voice_client.source, 'volume'):
            voice_client.source.volume = volume
        
        await interaction.response.send_message(f"🔊 Volume set to {level}%")

    @app_commands.command(name="disconnect", description="Disconnect the bot from voice channel")
    async def disconnect(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if not voice_client:
            await interaction.response.send_message("I'm not connected to a voice channel!")
            return
        
        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        
        await voice_client.disconnect()
        await interaction.response.send_message("👋 Disconnected from voice channel")

    @app_commands.command(name="nowplaying", description="Show currently playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        queue = self.get_queue(interaction.guild.id)
        
        if not voice_client or not voice_client.is_playing() or not queue.history:
            await interaction.response.send_message("No music is currently playing!")
            return
        
        current_song = queue.history[-1]
        
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**[{current_song.title}]({current_song.url})**",
            color=discord.Color.blue()
        )
        
        if current_song.thumbnail:
            embed.set_thumbnail(url=current_song.thumbnail)
        
        if current_song.duration:
            minutes, seconds = divmod(current_song.duration, 60)
            embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
        
        embed.add_field(name="Autoplay", value="On" if self.autoplay_enabled else "Off", inline=True)
        
        await interaction.response.send_message(embed=embed)

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
    
    async def setup_hook(self):
        await self.add_cog(MusicBot(self))
        await self.tree.sync()
        print(f"Slash commands synced for {self.user}")
    
    async def on_ready(self):
        print(f'{self.user} has logged in!')
        print(f'Bot is in {len(self.guilds)} guilds')
        print('Bot is ready to play music!')

# Keep-alive for Render (important!)
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "Discord Music Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

if __name__ == "__main__":
    import threading
    # Start Flask server in a separate thread for keep-alive
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        print("Error: DISCORD_BOT_TOKEN environment variable not set!")
        print("Please set your bot token in Render environment variables")
        exit(1)
    
    bot = Bot()
    bot.run(token)