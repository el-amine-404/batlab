"""Code shared by batlab's own services (songdrop, musicguard, videodrop,
clipseerr, subfeed): which song a title names, whether two recordings are
one, the stores' 30-second previews, notifications through Apprise, the apps'
people and API keys (accounts, web), and the egress proxy.

Standard library only (web.py aside: FastAPI, for the web servers), so it
runs in any Python 3.11+ environment, spotDL's included. It needs ffmpeg on the PATH for fingerprints (fpcalc, Chromaprint's
own tool, is used instead when installed: same result, four times faster).
"""

__version__ = "1.1.0"
