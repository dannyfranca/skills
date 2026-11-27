#!/usr/bin/env python3
"""Put the video inside the report: embed-video.py <report.html> [<video.mp4>]

A remote viewer (a phone, the file viewer of a harness) gets only the HTML
file, so a link to a file beside the report shows no video there.
"""
import base64
import pathlib
import re
import subprocess
import sys
import tempfile

VIDEO_TAG = re.compile(r"<video\b[^>]*>")
SRC_ATTR = re.compile(r'\ssrc="[^"]*"')
PAYLOAD = re.compile(r'<script type="application/octet-stream" id="video-data">.*?</script>\s*<script id="video-load">.*?</script>\n?', re.S)
# A blob URL, not a data: URL: mobile Safari can refuse a data: video.
LOADER = (
    '<script id="video-load">(()=>{const s=atob(document.getElementById("video-data").textContent),'
    "a=new Uint8Array(s.length);for(let i=0;i<s.length;i++)a[i]=s.charCodeAt(i);"
    'document.querySelector("video").src=URL.createObjectURL(new Blob([a],{type:"video/mp4"}))})()</script>'
)


def encode_small(video: pathlib.Path, small: pathlib.Path) -> None:
    # Each megabyte is wait time on a phone. 720p at 24 fps keeps slide text sharp.
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(video),
            "-vf", "scale=-2:'min(720,ih)',fps=24",
            "-c:v", "libx264", "-crf", "30", "-preset", "medium",
            "-c:a", "aac", "-b:a", "48k", "-ac", "1",
            "-movflags", "+faststart", str(small),
        ],
        check=True,
    )


def main() -> None:
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    report = pathlib.Path(sys.argv[1])
    video = pathlib.Path(sys.argv[2]) if len(sys.argv) == 3 else report.with_name("video.mp4")
    html = PAYLOAD.sub("", report.read_text())
    if len(VIDEO_TAG.findall(html)) != 1 or "</body>" not in html:
        sys.exit(f"{report}: expected one <video> and a </body>")

    with tempfile.TemporaryDirectory() as tmp:
        small = pathlib.Path(tmp, "small.mp4")
        encode_small(video, small)
        data = base64.encodebytes(small.read_bytes()).decode()

    html = VIDEO_TAG.sub(lambda tag: SRC_ATTR.sub("", tag[0]), html)
    # The payload is last, in short lines: the page shows before the video
    # arrives, and a text tool that reads the report does not meet one 10 MB line.
    payload = f'<script type="application/octet-stream" id="video-data">\n{data}</script>\n{LOADER}\n'
    head, body_end, tail = html.rpartition("</body>")
    report.write_text(head + payload + body_end + tail)
    print(f"{report}: video inside, {len(data) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
