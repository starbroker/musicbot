# bot.py  –  1-file Discord music bot (yt-dlp)  –  Render-ready
import os, asyncio, discord
from discord.ext import commands
import yt_dlp as youtube_dl

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN not found in env")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ---------- yt-dlp ----------
ytdl_opts = {
    "format": "bestaudio/best",
    "outtmpl": "%(id)s.%(ext)s",
    "quiet": True,
    "noplaylist": True,
    "source_address": "0.0.0.0"
}
ffmpeg_opts = {"options": "-vn"}
ytdl = youtube_dl.YoutubeDL(ytdl_opts)

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

def play_next(ctx):
    if queue:
        next_url = queue.pop(0)
        coro = _play_song(ctx, next_url)
        fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"play_next error: {e}")
    else:
        # nothing left – idle until user adds more
        pass

async def _play_song(ctx, url):
    async with ctx.typing():
        player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
        ctx.voice_client.play(player, after=lambda _: play_next(ctx))
    await ctx.send(f"▶️ Now playing: **{player.title}**")

# ---------- commands ----------
@bot.command(name="join", aliases=["j"])
async def join(ctx):
    if ctx.author.voice:
        await ctx.author.voice.channel.connect()
    else:
        await ctx.send("You're not in a voice channel.")

@bot.command(name="leave", aliases=["l"])
async def leave(ctx):
    if ctx.voice_client:
        queue.clear()
        await ctx.voice_client.disconnect()
    else:
        await ctx.send("Not in a voice channel.")

@bot.command(name="play", aliases=["p"])
async def play(ctx, *, url):
    if not ctx.voice_client:
        await ctx.invoke(join)
    queue.append(url)
    if not ctx.voice_client.is_playing():
        play_next(ctx)
    else:
        await ctx.send("➕ Added to queue.")

@bot.command(name="skip", aliases=["s"])
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()  # triggers after= play_next → next song
        await ctx.send("⏭️ Skipped.")
    else:
        await ctx.send("Nothing playing.")

@bot.command(name="pause")
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused.")

@bot.command(name="resume", aliases=["r"])
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed.")

@bot.command(name="queue", aliases=["q"])
async def show_queue(ctx):
    if queue:
        lines = [f"{i+1}. {url}" for i, url in enumerate(queue)]
        await ctx.send("**Queue:**\n" + "\n".join(lines))
    else:
        await ctx.send("Queue is empty.")

# ---------- run ----------
bot.run(TOKEN)
