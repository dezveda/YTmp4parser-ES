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
    Selects the best video, audio, and subtitle streams from the video info dict.
    Returns a dictionary with the selected stream objects.
    """
    formats = video_info.get('formats', [])
    subtitles = video_info.get('automatic_captions', {})
    title = video_info.get('title', '')

    video_streams = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
    if not video_streams: # Fallback to merged formats if no video-only
        video_streams = [f for f in formats if f.get('vcodec') != 'none']

    if quality:
        try:
            quality_val = int(re.sub(r'\D', '', quality))
            quality_streams = [f for f in video_streams if f.get('height') == quality_val]
            if quality_streams:
                video_streams = quality_streams
            else:
                print(f"Warning: Quality '{quality}' not found. Falling back to best available.", file=sys.stderr)
        except (ValueError, TypeError):
            print(f"Warning: Invalid quality format '{quality}'. Ignoring.", file=sys.stderr)

    video_streams.sort(key=lambda f: (f.get('height', 0), f.get('fps', 0), f.get('tbr', 0)), reverse=True)
    selected_video = video_streams[0] if video_streams else None

    es_audio = [f for f in formats if f.get('acodec') != 'none' and (f.get('language') or '').startswith('es')]
    es_audio.sort(key=lambda f: f.get('tbr', 0), reverse=True)
    selected_audio = es_audio[0] if es_audio else None

    audio_source_msg = "Found explicitly tagged Spanish audio."
    if not selected_audio:
        title_lower = title.lower()
        if 'español' in title_lower or 'castellano' in title_lower:
            all_audio = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
            if all_audio:
                all_audio.sort(key=lambda f: f.get('tbr', 0), reverse=True)
                selected_audio = all_audio[0]
                audio_source_msg = "Inferred Spanish from title, selected best available audio."
        else:
            audio_source_msg = "No Spanish audio found."

    selected_subtitle = None
    if not selected_audio:
        es_subs = subtitles.get('es', []) or subtitles.get('es-419', [])
        if es_subs:
            es_subs.sort(key=lambda s: {'srt': 0, 'vtt': 1}.get(s.get('ext'), 99))
            selected_subtitle = es_subs[0]

    return {
        "video": selected_video, "audio": selected_audio,
        "subtitle": selected_subtitle, "audio_source_msg": audio_source_msg
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
    audio = selection.get('audio')
    subtitle = selection.get('subtitle')

    if not video:
        print("Error: No suitable video stream found.", file=sys.stderr)
        return

    command = [sys.executable, "-m", "yt_dlp", url, "--ffmpeg-location", str(Path(ffmpeg_path).parent)]
    if browser:
        command.extend(["--cookies-from-browser", browser])
    if verbose:
        command.append("--verbose")

    if audio:
        print(f"Plan: Muxing video and audio to '{output_path}'")
        command.extend(["-f", f"{video['format_id']}+{audio['format_id']}"])
        command.extend(["--merge-output-format", "mp4"])
    elif subtitle:
        print(f"Plan: Embedding Spanish subtitles in '{output_path}'")
        command.extend(["-f", f"{video['format_id']}+bestaudio"])
        command.extend(["--embed-subs", "--sub-lang", "es,es-419"])
        command.extend(["--merge-output-format", "mp4"])
    else:
        print(f"Warning: No Spanish audio or subtitles. Downloading best available video and audio.", file=sys.stderr)
        command.extend(["-f", f"{video['format_id']}+bestaudio"])
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

def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Download YouTube videos with a preference for Spanish audio or subtitles.",
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("url", help="The URL of the YouTube video to download.")
    parser.add_argument("-q", "--quality", help="Optional: Desired video quality (e.g., '1080p', '720p').\nDefaults to best available.", default=None)
    parser.add_argument("-o", "--output", help="Optional: The output path and filename (e.g., '~/videos/my_video.mp4').\nDefaults to your Desktop.", default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output for debugging.")
    args = parser.parse_args()

    # Simple regex to catch obviously invalid URLs before calling the API
    youtube_regex = re.compile(r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)[\w-]+')
    if not youtube_regex.match(args.url):
        print(f"Error: The provided URL '{args.url}' does not look like a valid YouTube URL.", file=sys.stderr)
        sys.exit(1)

    print("--- Verifying Dependencies ---")
    ensure_yt_dlp()
    ffmpeg_path = get_ffmpeg_path()
    print("----------------------------\n")

    browsers_to_try = ["chrome", "firefox", "brave", "edge", "opera", "vivaldi", "safari"]
    video_info, successful_browser = get_video_info_cli(args.url, browsers_to_try, args.verbose)

    if not video_info:
        sys.exit(1)

    selection = select_streams(video_info, args.quality)

    print("\n--- Download Plan ---")
    if selection["video"]:
        print(f"Video: {selection['video']['format_note']} ({selection['video']['format_id']})")
    if selection["audio"]:
        print(f"Audio: {selection['audio'].get('language', 'n/a')} ({selection['audio']['format_id']}) - Source: {selection['audio_source_msg']}")
    if selection["subtitle"]:
        lang_code = selection['subtitle'].get('language_code', 'es')
        print(f"Subtitle: Spanish ({lang_code}) ({selection['subtitle']['ext']})")
    if not selection["audio"] and not selection["subtitle"]:
        print("Audio: No Spanish audio found.")
        print("Subtitle: No Spanish subtitles found.")
    print("---------------------\n")

    download_and_process(args.url, video_info, selection, ffmpeg_path, successful_browser, args.output, args.verbose)


if __name__ == "__main__":
    main()
