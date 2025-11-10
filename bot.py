import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
from collections import deque
import asyncio

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

SONG_QUEUES = {}
AUTOPLAY_ENABLED = {}
LAST_VIDEO_ID = {}
YTDLP_SEMAPHORE = asyncio.Semaphore(1)

async def search_ytdlp_async(query, ydl_opts):
    async with YTDLP_SEMAPHORE:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

async def get_related_song(video_id):
    try:
        ydl_options = {
            "format": "bestaudio[abr<=96]/bestaudio",
            "noplaylist": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False,
            "extract_flat": False,
        }
        
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        results = await search_ytdlp_async(video_url, ydl_options)
        
        if results and "entries" not in results:
            related = results.get("related_videos", [])
            if related:
                related_id = related[0].get("id")
                if related_id:
                    related_url = f"https://www.youtube.com/watch?v={related_id}"
                    related_info = await search_ytdlp_async(related_url, ydl_options)
                    if related_info and "entries" not in related_info:
                        return (
                            related_info["url"],
                            related_info.get("title", "Untitled"),
                            related_info.get("id", related_id)
                        )
        
        query = "ytsearch1:popular music"
        fallback = await search_ytdlp_async(query, ydl_options)
        tracks = fallback.get("entries", [])
        if tracks:
            track = tracks[0]
            return (
                track["url"],
                track.get("title", "Untitled"),
                track.get("id", "")
            )
    except Exception as e:
        print(f"Error getting related song: {e}")
    
    return None


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} is online!")


@bot.tree.command(name="skip", description="Skips the current playing song")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("Skipped the current song.")
    else:
        await interaction.response.send_message("Not playing anything to skip.")


@bot.tree.command(name="pause", description="Pause the currently playing song.")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        return await interaction.response.send_message("I'm not in a voice channel.")

    if not voice_client.is_playing():
        return await interaction.response.send_message("Nothing is currently playing.")
    
    voice_client.pause()
    await interaction.response.send_message("Playback paused!")


@bot.tree.command(name="resume", description="Resume the currently paused song.")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        return await interaction.response.send_message("I'm not in a voice channel.")

    if not voice_client.is_paused():
        return await interaction.response.send_message("I'm not paused right now.")
    
    voice_client.resume()
    await interaction.response.send_message("Playback resumed!")


@bot.tree.command(name="stop", description="Stop playback and clear the queue.")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        return await interaction.response.send_message("I'm not connected to any voice channel.")

    guild_id_str = str(interaction.guild_id)
    if guild_id_str in SONG_QUEUES:
        SONG_QUEUES[guild_id_str].clear()

    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    await voice_client.disconnect()

    await interaction.response.send_message("Stopped playback and disconnected!")


@bot.tree.command(name="autoplay", description="Toggle autoplay mode to automatically play related songs.")
async def autoplay(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    
    current_status = AUTOPLAY_ENABLED.get(guild_id, False)
    AUTOPLAY_ENABLED[guild_id] = not current_status
    
    if AUTOPLAY_ENABLED[guild_id]:
        await interaction.response.send_message("🎵 Autoplay is now **enabled**! Related songs will play automatically when the queue is empty.")
    else:
        await interaction.response.send_message("🎵 Autoplay is now **disabled**.")


@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(song_query="Search query")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer()

    if interaction.user.voice is None:
        await interaction.followup.send("You must be in a voice channel.")
        return

    voice_channel = interaction.user.voice.channel

    voice_client = interaction.guild.voice_client

    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_channel != voice_client.channel:
        await voice_client.move_to(voice_channel)

    ydl_options = {
        "format": "bestaudio[abr<=96]/bestaudio",
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
    }

    query = "ytsearch1: " + song_query
    results = await search_ytdlp_async(query, ydl_options)
    tracks = results.get("entries", [])

    if not tracks:
        await interaction.followup.send("No results found.")
        return

    first_track = tracks[0]
    audio_url = first_track["url"]
    title = first_track.get("title", "Untitled")
    video_id = first_track.get("id", "")

    guild_id = str(interaction.guild_id)
    if SONG_QUEUES.get(guild_id) is None:
        SONG_QUEUES[guild_id] = deque()

    SONG_QUEUES[guild_id].append((audio_url, title, video_id))

    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(f"Added to queue: **{title}**")
    else:
        await interaction.followup.send(f"Now playing: **{title}**")
        await play_next_song(voice_client, guild_id, interaction.channel)


async def play_next_song(voice_client, guild_id, channel):
    if SONG_QUEUES[guild_id]:
        audio_url, title, video_id = SONG_QUEUES[guild_id].popleft()
        LAST_VIDEO_ID[guild_id] = video_id

        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn -c:a libopus -b:a 96k",
        }

        source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options)

        def after_play(error):
            if error:
                print(f"Error playing {title}: {error}")
            asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)

        voice_client.play(source, after=after_play)
        asyncio.create_task(channel.send(f"Now playing: **{title}**"))
    elif AUTOPLAY_ENABLED.get(guild_id, False) and LAST_VIDEO_ID.get(guild_id):
        last_video_id = LAST_VIDEO_ID[guild_id]
        related_song = await get_related_song(last_video_id)
        
        if related_song:
            audio_url, title, video_id = related_song
            LAST_VIDEO_ID[guild_id] = video_id
            
            ffmpeg_options = {
                "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                "options": "-vn -c:a libopus -b:a 96k",
            }
            
            source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options)
            
            def after_play(error):
                if error:
                    print(f"Error playing {title}: {error}")
                asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)
            
            voice_client.play(source, after=after_play)
            asyncio.create_task(channel.send(f"🎵 Autoplay: **{title}**"))
        else:
            await voice_client.disconnect()
            SONG_QUEUES[guild_id] = deque()
    else:
        await voice_client.disconnect()
        SONG_QUEUES[guild_id] = deque()


if __name__ == "__main__":
    bot.run(TOKEN)
