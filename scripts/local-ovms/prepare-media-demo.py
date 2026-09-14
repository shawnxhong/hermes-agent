#!/usr/bin/env python3
"""Encode an existing local narration WAV as fixed demo MP3 and MP4 assets."""
import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--wav', type=Path, required=True)
parser.add_argument('--output', type=Path, default=Path.home() / 'Desktop')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
                '-i', str(args.wav), '-c:a', 'libmp3lame', '-b:a', '160k',
                str(args.output / 'demo.mp3')], check=True)
font = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
graph = (
    '[1:a]asplit[a][vis];'
    '[vis]showwaves=s=1040x160:mode=cline:colors=0x00C7FD:rate=25[w];'
    '[0:v]drawbox=x=0:y=0:w=1280:h=12:color=0x00C7FD:t=fill,'
    f'drawtext=fontfile={font}:text=INTEL AI BOX:fontsize=60:fontcolor=white:x=120:y=100,'
    f'drawtext=fontfile={font}:text=LOCAL MEDIA DEMO:fontsize=30:fontcolor=0x00C7FD:x=120:y=190,'
    f'drawtext=fontfile={font}:text=Hermes + local Qwen on Ubuntu:fontsize=30:fontcolor=white:x=120:y=280,'
    f'drawtext=fontfile={font}:text=MP3 audio  /  MP4 video  /  Offline playback:fontsize=24:fontcolor=0xBCC7D9:x=120:y=340[b];'
    '[b][w]overlay=x=120:y=425:shortest=1[v]')
subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
                '-f', 'lavfi', '-i', 'color=c=0x101B30:s=1280x720:r=25',
                '-i', str(args.wav), '-filter_complex', graph,
                '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'veryfast',
                '-crf', '22', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k',
                '-shortest', '-movflags', '+faststart', str(args.output / 'demo.mp4')], check=True)
print('Created demo.mp3 and demo.mp4 in', args.output)
