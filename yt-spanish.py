#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
A zero-config, single-file tool to download YouTube videos with a preference
for Spanish audio or subtitles.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import urllib.request
import json
import re
import tarfile
import zipfile
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

# --- Dependency Management ---

def ensure_yt_dlp():
    """
    Check if yt-dlp is installed via pip, and if not, install it.
    """
    if importlib.util.find_spec("yt_dlp"):
        print("yt-dlp is already installed.")
        return
    print("yt-dlp not found, attempting to install...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "yt-dlp", "requests"],
            check=True, capture_output=True, text=True)
        print("yt-dlp installed successfully.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error installing yt-dlp: {getattr(e, 'stderr', e)}", file=sys.stderr)
        sys.exit(1)

def _download_progress_hook(count, block_size, total_size):
    """A hook for urlretrieve to show download progress for dependencies."""
    percent = int(count * block_size * 100 / total_size)
    sys.stdout.write(f"\rDownloading dependency... {percent}%")
    sys.stdout.flush()

def get_ffmpeg_path() -> str:
    """
    Finds a usable ffmpeg executable. Checks PATH first, then a local cache.
    If not found, downloads a static build for the current OS and caches it.
    Returns the path to the ffmpeg executable.
    """
    ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    ffmpeg_in_path = shutil.which(ffmpeg_name)
    if ffmpeg_in_path:
        print(f"ffmpeg found in PATH: {ffmpeg_in_path}")
        return ffmpeg_in_path

    cache_dir = Path.home() / ".yt-spanish"
    ffmpeg_exe_path = cache_dir / "ffmpeg" / ffmpeg_name
    if ffmpeg_exe_path.is_file():
        print(f"ffmpeg found in cache: {ffmpeg_exe_path}")
        return str(ffmpeg_exe_path)

    print("ffmpeg not found. Attempting to download...")
    cache_dir.mkdir(exist_ok=True)
    ffmpeg_exe_path.parent.mkdir(exist_ok=True)

    download_path = None
    try:
        if sys.platform == "win32":
            url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
            download_path = cache_dir / "ffmpeg.zip"
            archive_path_segment = "bin/ffmpeg.exe"
            urllib.request.urlretrieve(url, download_path, _download_progress_hook)
            with zipfile.ZipFile(download_path, "r") as zip_ref:
                member_path = next((m.filename for m in zip_ref.infolist() if m.filename.endswith(archive_path_segment)), None)
                if not member_path: raise FileNotFoundError("ffmpeg.exe not in archive")
                zip_ref.extract(member_path, path=cache_dir)
                shutil.move(cache_dir / member_path, ffmpeg_exe_path)
                shutil.rmtree(cache_dir / member_path.split('/')[0])
        else: # Assume Linux
            url = "https://johnvansickle.com/ffmpeg/builds/ffmpeg-git-amd64-static.tar.xz"
            download_path = cache_dir / "ffmpeg.tar.xz"
            archive_path_segment = "ffmpeg"
            urllib.request.urlretrieve(url, download_path, _download_progress_hook)
            with tarfile.open(download_path, "r:xz") as tar_ref:
                member = next((m for m in tar_ref.getmembers() if m.name.endswith(archive_path_segment) and m.isfile()), None)
                if not member: raise FileNotFoundError("ffmpeg not in archive")
                tar_ref.extract(member, path=cache_dir)
                shutil.move(cache_dir / member.name, ffmpeg_exe_path)
                shutil.rmtree(cache_dir / member.name.split('/')[0])

        os.chmod(ffmpeg_exe_path, 0o755)
        print(f"\nffmpeg successfully installed to {ffmpeg_exe_path}")
        return str(ffmpeg_exe_path)
    except Exception as e:
        print(f"\nError downloading or extracting ffmpeg: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if download_path and download_path.exists():
            download_path.unlink()

# --- Core Logic ---

def interactive_select(streams: List[Dict[str, Any]], stream_type: str) -> Optional[str]:
    """
    Presenta una lista de pistas al usuario y solicita una selección interactiva.

    Args:
        streams: Una lista de diccionarios de pistas (formatos o subtítulos).
        stream_type: El tipo de pista ('audio' o 'subtítulo').

    Returns:
        El 'format_id' de la pista de audio elegida, el código de idioma del
        subtítulo elegido, o None si el usuario omite la selección.
    """
    if not streams:
        print(f"No se encontraron pistas de {stream_type}.")
        return None

    print(f"\n--- Pistas de {stream_type} disponibles ---")
    if stream_type == "audio":
        print(f"{'#':>2} | {'ID':<6} | {'Idioma':<8} | {'Codec':<15} | {'Bitrate':<12} | {'Nota'}")
        print("-" * 80)
        for i, stream in enumerate(streams, 1):
            lang = stream.get('language', 'n/a')
            acodec = stream.get('acodec', 'n/a').replace('mp4a.40.2', 'aac')
            abr = stream.get('abr', 0)
            note = stream.get('format_note', '')
            print(f"{i:>2} | {stream['format_id']:<6} | {lang:<8} | {acodec:<15} | {f'{abr}k':<12} | {note}")

    elif stream_type == "subtítulo":
        print(f"{'#':>2} | {'Idioma':<8} | {'Formato':<7} | {'Nombre'}")
        print("-" * 50)
        for i, stream in enumerate(streams, 1):
            lang = stream.get('lang_code', 'n/a')
            ext = stream.get('ext', 'n/a')
            name = stream.get('name', 'Auto-generado')
            print(f"{i:>2} | {lang:<8} | {ext:<7} | {name}")

    print("-" * (80 if stream_type == 'audio' else 50))

    while True:
        try:
            prompt = f"Elige el número de la pista de {stream_type} (0 para omitir): "
            choice_str = input(prompt)

            if not choice_str.strip():
                print("Selección inválida. Por favor, introduce un número.")
                continue

            choice = int(choice_str)

            if choice == 0:
                print(f"Se omitirá la descarga de {stream_type}.")
                return None

            if not (1 <= choice <= len(streams)):
                raise IndexError

            selected_stream = streams[choice - 1]

            if stream_type == "audio":
                return selected_stream['format_id']
            elif stream_type == "subtítulo":
                return selected_stream['lang_code']

        except ValueError:
            print("Entrada inválida. Por favor, introduce un número.")
        except IndexError:
            print("Número fuera de rango. Por favor, elige un número de la lista.")


def get_video_info_cli(url: str, browsers: List[str], verbose: bool) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Fetches video info by calling the yt-dlp CLI.
    Tries a list of browsers to load cookies for authentication.
    Returns the info dict and the name of the successful browser, if any.
    """
    if verbose:
        print("Attempting to fetch video info using browser cookies...")

    for browser in browsers:
        try:
            command = [sys.executable, "-m", "yt_dlp", "--dump-json", "--cookies-from-browser", browser, url]
            result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8')
            if verbose:
                print(f"Successfully fetched info using '{browser}' cookies.")
            return json.loads(result.stdout), browser
        except Exception as e:
            if verbose:
                error_output = e.stderr if hasattr(e, 'stderr') and e.stderr else str(e)
                if "Unable to find a suitable cookie file" in error_output:
                    print(f"Info: No cookie file found for '{browser}'.")
                elif "PermissionError" in error_output:
                    print(f"Warning: Permission denied for '{browser}' cookies.", file=sys.stderr)

    if verbose:
        print("Warning: All browser cookie attempts failed. Trying without cookies...")

    try:
        command = [sys.executable, "-m", "yt_dlp", "--dump-json", url]
        result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8')
        return json.loads(result.stdout), None
    except Exception as e:
        print("Fatal: Could not fetch video info. Error from yt-dlp below:", file=sys.stderr)
        if hasattr(e, 'stderr'):
            print(e.stderr, file=sys.stderr)
        sys.exit(1)

def select_streams(video_info: Dict[str, Any], quality: Optional[str] = None) -> Dict[str, Any]:
    """
    Permite al usuario seleccionar interactivamente las pistas de audio y subtítulos,
    mientras selecciona automáticamente la mejor pista de vídeo.
    """
    formats = video_info.get('formats', [])

    # 1. Selección de vídeo (automática)
    video_streams = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
    if not video_streams: # Fallback a formatos con audio si no hay solo vídeo
        video_streams = [f for f in formats if f.get('vcodec') != 'none']

    if quality:
        try:
            quality_val = int(re.sub(r'\D', '', quality))
            quality_streams = [f for f in video_streams if f.get('height') == quality_val]
            if quality_streams:
                video_streams = quality_streams
            else:
                print(f"Aviso: Calidad '{quality}' no encontrada. Usando la mejor disponible.", file=sys.stderr)
        except (ValueError, TypeError):
            print(f"Aviso: Formato de calidad '{quality}' inválido. Ignorando.", file=sys.stderr)

    video_streams.sort(key=lambda f: (f.get('height', 0), f.get('fps', 0), f.get('tbr', 0)), reverse=True)
    selected_video = video_streams[0] if video_streams else None

    if not selected_video:
        print("Error fatal: No se encontró ninguna pista de vídeo compatible.", file=sys.stderr)
        sys.exit(1)

    # 2. Selección de audio (interactiva)
    audio_streams = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
    if not audio_streams: # Fallback a formatos con vídeo si no hay solo audio
        audio_streams = [f for f in formats if f.get('acodec') != 'none']

    audio_streams.sort(key=lambda f: (f.get('language', 'zz'), -f.get('abr', 0)))
    selected_audio_id = interactive_select(audio_streams, "audio")

    # 3. Selección de subtítulos (interactiva)
    subtitles_data = video_info.get('subtitles', {})
    auto_captions_data = video_info.get('automatic_captions', {})

    subtitle_list = []
    all_sub_langs = set(subtitles_data.keys()) | set(auto_captions_data.keys())

    for lang in sorted(list(all_sub_langs)):
        if len(lang) > 3 and '-' not in lang: continue

        if lang in subtitles_data:
            sub = subtitles_data[lang][0]
            sub['lang_code'] = lang
            sub['name'] = sub.get('name', lang) + " (manual)"
            subtitle_list.append(sub)
        elif lang in auto_captions_data:
            sub = auto_captions_data[lang][0]
            sub['lang_code'] = lang
            sub['name'] = sub.get('name', lang) + " (auto)"
            subtitle_list.append(sub)

    selected_subtitle_lang = interactive_select(subtitle_list, "subtítulo")

    return {
        "video": selected_video,
        "audio_id": selected_audio_id,
        "subtitle_lang": selected_subtitle_lang
    }

def sanitize_filename(name: str) -> str:
    """Removes invalid characters from a string to make it a valid filename."""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return re.sub(r'\s+', ' ', name).strip()

def download_and_process(url: str, video_info: Dict[str, Any], selection: Dict[str, Any], ffmpeg_path: str, browser: Optional[str], output_override: Optional[str], verbose: bool):
    """
    Constructs and runs the final yt-dlp command to download and process the video.
    Streams the output of the subprocess in real-time.
    """
    if output_override:
        output_path = Path(output_override).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        title = sanitize_filename(video_info.get('title', 'video'))
        output_path = Path.home() / "Desktop" / f"{title}.mp4"

    video = selection.get('video')
    audio_id = selection.get('audio_id')
    subtitle_lang = selection.get('subtitle_lang')

    if not video:
        print("Error: No suitable video stream found.", file=sys.stderr)
        return

    command = [sys.executable, "-m", "yt_dlp", url, "--ffmpeg-location", str(Path(ffmpeg_path).parent)]
    if browser:
        command.extend(["--cookies-from-browser", browser])
    if verbose:
        command.append("--verbose")

    # Format selection
    video_id = video['format_id']
    if audio_id:
        command.extend(["-f", f"{video_id}+{audio_id}"])
    else:
        # Fallback to best audio if user skips selection, ensures audio is present
        command.extend(["-f", f"{video_id}+bestaudio"])

    # Subtitle selection
    if subtitle_lang:
        command.extend(["--write-sub", "--sub-lang", subtitle_lang, "--embed-subs"])

    # Always merge to mp4 to prevent extensionless files
    command.extend(["--merge-output-format", "mp4"])
    command.extend(["-o", str(output_path)])

    print("\nExecuting download command...")
    if verbose:
        print(f"Command: {' '.join(command)}")

    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in iter(process.stdout.readline, ''):
            print(line, end='')
        process.wait()
        if process.returncode == 0:
            print(f"\nSuccess! Video saved to: {output_path}")
        else:
            print(f"\nError: yt-dlp exited with code {process.returncode}", file=sys.stderr)
    except Exception as e:
        print(f"\nAn unexpected error occurred during download: {e}", file=sys.stderr)

def interactive_prompt() -> Tuple[str, Optional[str], Optional[str], bool]:
    """
    Prompts the user for download details in an interactive session.
    """
    youtube_regex = re.compile(r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)[\w-]+')
    url = ""
    while not url:
        url_input = input("Enter the YouTube URL: ").strip()
        if youtube_regex.match(url_input):
            url = url_input
        else:
            print("Invalid YouTube URL. Please try again.")

    quality = input("Enter preferred quality (e.g., 720p, 1080p) [default: 720p]: ").strip() or "720p"
    output = input("Enter output folder [default: ./downloads]: ").strip() or "./downloads"
    verbose_input = input("Enable verbose mode? [Y/n]: ").strip().lower()
    verbose = verbose_input not in ['n', 'no']

    return url, quality, output, verbose

def run_download(url: str, quality: Optional[str], output: Optional[str], verbose: bool):
    """
    Core logic to download a video with specified options.
    """
    # Simple regex to catch obviously invalid URLs before calling the API
    youtube_regex = re.compile(r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)[\w-]+')
    if not youtube_regex.match(url):
        print(f"Error: The provided URL '{url}' does not look like a valid YouTube URL.", file=sys.stderr)
        sys.exit(1)

    print("--- Verifying Dependencies ---")
    ensure_yt_dlp()
    ffmpeg_path = get_ffmpeg_path()
    print("----------------------------\n")

    browsers_to_try = ["chrome", "firefox", "brave", "edge", "opera", "vivaldi", "safari"]
    video_info, successful_browser = get_video_info_cli(url, browsers_to_try, verbose)

    if not video_info:
        sys.exit(1)

    selection = select_streams(video_info, quality)

    print("\n--- Download Plan ---")
    if selection.get("video"):
        video_note = selection['video'].get('format_note', 'best')
        video_id = selection['video']['format_id']
        print(f"Video: {video_note} ({video_id})")

    if selection.get("audio_id"):
        print(f"Audio: Pista seleccionada por el usuario (ID: {selection['audio_id']})")
    else:
        print("Audio: No se seleccionó pista. Se usará la mejor disponible.")

    if selection.get("subtitle_lang"):
        print(f"Subtítulos: Idioma seleccionado por el usuario ('{selection['subtitle_lang']}')")
    else:
        print("Subtítulos: No se seleccionaron subtítulos.")
    print("---------------------\n")

    download_and_process(url, video_info, selection, ffmpeg_path, successful_browser, output, verbose)

def main():
    """Main entry point for the script."""
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description="Download YouTube videos with a preference for Spanish audio or subtitles.",
            formatter_class=argparse.RawTextHelpFormatter)
        parser.add_argument("url", help="The URL of the YouTube video to download.")
        parser.add_argument("-q", "--quality", help="Optional: Desired video quality (e.g., '1080p', '720p').\nDefaults to best available.", default=None)
        parser.add_argument("-o", "--output", help="Optional: The output path and filename (e.g., '~/videos/my_video.mp4').\nDefaults to your Desktop.", default=None)
        parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output for debugging.")
        args = parser.parse_args()
        run_download(args.url, args.quality, args.output, args.verbose)
    else:
        print("--- Interactive Mode ---")
        url, quality, output, verbose = interactive_prompt()
        run_download(url, quality, output, verbose)


if __name__ == "__main__":
    main()
