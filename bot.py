import os, asyncio, discord
from discord.ext import commands
from discord import app_commands
import yt_dlp as youtube_dl

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN not found in env")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# ---------- yt-dlp ----------
ytdl_format = {
    "format": "bestaudio/best",
    "outtmpl": "%(id)s.%(ext)s",
    "quiet": True,
    "noplaylist": True,
    "source_address": "0.0.0.0",
}
ffmpeg_opts = {"options": "-vn"}
ytdl = youtube_dl.YoutubeDL(ytdl_format)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.title = data.get("title")
        self.web_url = data.get("webpage_url")

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if "entries" in data:
            data = data["entries"][0]
        filename = data["url"] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_opts), data=data)

# ---------- queue ----------
queue = []

async def _play_next(inter: discord.Interaction):
    if queue:
        url = queue.pop(0)
        player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
        inter.guild.voice_client.play(
            player,
            after=lambda _: bot.loop.create_task(_play_next(inter))
        )
        await inter.followup.send(f"▶️ Now playing: **{player.title}**")
    else:
        await inter.followup.send("⏹️ Queue finished.")

# ---------- sync tree ----------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} connected & tree synced")

# ---------- slash commands ----------
@bot.tree.command(name="join", description="Join your voice channel")
async def join(inter: discord.Interaction):
    if not inter.user.voice:
        return await inter.response.send_message("You are not in a voice channel.", ephemeral=True)
    await inter.user.voice.channel.connect()
    await inter.response.send_message("👋 Joined.")

@bot.tree.command(name="leave", description="Leave voice and clear queue")
async def leave(inter: discord.Interaction):
    if inter.guild.voice_client:
        queue.clear()
        await inter.guild.voice_client.disconnect()
        await inter.response.send_message("👋 Left.")
    else:
        await inter.response.send_message("Not in a voice channel.", ephemeral=True)

@bot.tree.command(name="play", description="Add a song/keyword to the queue and start playing")
async def play(inter: discord.Interaction, query: str):
    await inter.response.defer()
    if not inter.guild.voice_client:
        if inter.user.voice:
            await inter.user.voice.channel.connect()
        else:
            return await inter.followup.send("Join a voice channel first.", ephemeral=True)

    queue.append(query)
    if not inter.guild.voice_client.is_playing():
        await _play_next(inter)
    else:
        await inter.followup.send("➕ Added to queue.")

@bot.tree.command(name="skip", description="Skip current track")
async def skip(inter: discord.Interaction):
    vc = inter.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()  # after= will auto-play next
        await inter.response.send_message("⏭️ Skipped.")
    else:
        await inter.response.send_message("Nothing playing.", ephemeral=True)

@bot.tree.command(name="pause", description="Pause playback")
async def pause(inter: discord.Interaction):
    vc = inter.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await inter.response.send_message("⏸️ Paused.")
    else:
        await inter.response.send_message("Nothing playing.", ephemeral=True)

@bot.tree.command(name="resume", description="Resume playback")
async def resume(inter: discord.Interaction):
    vc = inter.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await inter.response.send_message("▶️ Resumed.")
    else:
        await inter.response.send_message("Not paused.", ephemeral=True)

@bot.tree.command(name="queue", description="Show current queue")
async def show_queue(inter: discord.Interaction):
    if queue:
        lines = [f"{i+1}. {url}" for i, url in enumerate(queue)]
        await inter.response.send_message("**Queue:**\n" + "\n".join(lines))
    else:
        await inter.response.send_message("Queue is empty.", ephemeral=True)

# ---------- run ----------
bot.run(TOKEN)
